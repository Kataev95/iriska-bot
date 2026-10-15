"""Тесты общего питомца 🐣 — без Telegram.

Запуск: python3 test_pets.py

Отдельный файл, потому что роутер aiogram нельзя подключить к двум
диспетчерам, а здесь собирается своя настоящая связка роутеров.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import tempfile
from dataclasses import replace
from unittest import mock

from db import Database
from pets import parse_rewards, stage_for

CHAT = -1009999999999
A, B, C = 101, 202, 303
REWARDS = {3: 2, 7: 5}


async def _db() -> Database:
    db = Database(os.path.join(tempfile.mkdtemp(), "pets.db"))
    await db.connect()
    return db


async def check_logic() -> None:
    assert parse_rewards("3=2, 7=5;bad,0=4,30=0") == {3: 2, 7: 5}
    assert stage_for(0) is None
    assert stage_for(1).title == "Малыш" and stage_for(6).title == "Малыш"
    assert stage_for(7).title == "Подросток" and stage_for(100).title == "Легенда"

    db = await _db()
    try:
        for uid in (A, B, C):
            await db.ensure_user(CHAT, uid, f"u{uid}", f"Юзер{uid}")
        now = 1_000_000.0

        # Приглашение: пара хранится упорядоченно, повтор — "exists"
        st, pet = await db.create_pet_invite(CHAT, B, A, "fox", now, 5)
        assert st == "ok" and (pet["user_a"], pet["user_b"]) == (A, B)
        st, _ = await db.create_pet_invite(CHAT, A, B, "cat", now, 5)
        assert st == "exists"
        pid = int(pet["id"])
        # Принять может только приглашённый
        st, _ = await db.answer_pet_invite(pid, B, True, now, 600, 5)
        assert st == "not_yours", "пригласивший не принимает сам"
        st, _ = await db.answer_pet_invite(pid, C, True, now, 600, 5)
        assert st == "not_yours"
        st, pet = await db.answer_pet_invite(pid, A, True, now + 5, 600, 5)
        assert st == "accepted" and pet["status"] == "active"

        # Писать надо обоим: одной стороны мало
        ev = await db.record_pet_message(CHAT, A, B, "2026-10-01", "2026-09-30", REWARDS)
        assert ev and not ev["fed"]
        ev = await db.record_pet_message(CHAT, A, B, "2026-10-01", "2026-09-30", REWARDS)
        assert not ev["fed"], "повтор той же стороны ничего не меняет"
        ev = await db.record_pet_message(CHAT, B, A, "2026-10-01", "2026-09-30", REWARDS)
        assert ev["fed"] and ev["streak"] == 1 and ev["prev_best"] == 0, "вылупился"
        ev = await db.record_pet_message(CHAT, B, A, "2026-10-01", "2026-09-30", REWARDS)
        assert not ev["fed"], "второй раз за день не кормим"
        # Чужая пара не трогает питомца
        assert await db.record_pet_message(CHAT, A, C, "2026-10-01", "2026-09-30") is None

        # Дни подряд: серия растёт, на 3-й день награда обоим
        bal_a = (await db.get_user(CHAT, A))["balance"]
        for day, prev in (("2026-10-02", "2026-10-01"), ("2026-10-03", "2026-10-02")):
            await db.record_pet_message(CHAT, A, B, day, prev, REWARDS)
            ev = await db.record_pet_message(CHAT, B, A, day, prev, REWARDS)
        assert ev["fed"] and ev["streak"] == 3 and ev["reward"] == 2
        assert (await db.get_user(CHAT, A))["balance"] == bal_a + 2
        assert (await db.get_user(CHAT, B))["balance"] == 2

        # Вчера кормили — не сбегает
        assert await db.sweep_lost_pets("2026-10-03", now) == []
        # Пропустили 04.10 -> 05.10 питомец сбегает, серия в lost_streak
        lost = await db.sweep_lost_pets("2026-10-04", now + 100)
        assert len(lost) == 1 and lost[0]["streak"] == 0 and lost[0]["lost_streak"] == 3
        assert await db.sweep_lost_pets("2026-10-04", now + 200) == [], "один раз"

        # Вернуть: не хватает ирисок -> бесплатно не отдаём
        await db.adjust_balance(CHAT, C, 0, "тест", None)
        st, bal, _ = await db.restore_pet(pid, B, 50, now + 300, 86400, "2026-10-04")
        assert st == "insufficient" and bal == 2
        st, bal, pet = await db.restore_pet(pid, A, 1, now + 300, 86400, "2026-10-04")
        assert st == "ok" and pet["streak"] == 3 and pet["last_fed_day"] == "2026-10-04"
        st, _, _ = await db.restore_pet(pid, A, 1, now + 400, 86400, "2026-10-04")
        assert st == "nothing", "второй раз возвращать нечего"
        # После возвращения серия продолжается
        await db.record_pet_message(CHAT, A, B, "2026-10-05", "2026-10-04", REWARDS)
        ev = await db.record_pet_message(CHAT, B, A, "2026-10-05", "2026-10-04", REWARDS)
        assert ev["streak"] == 4

        # Окно возвращения истекло
        lost = await db.sweep_lost_pets("2026-10-06", now + 1000)
        assert lost and lost[0]["lost_streak"] == 4
        st, _, _ = await db.restore_pet(pid, A, 0, now + 1000 + 90000, 86400, "2026-10-07")
        assert st == "expired"
        # Новая переписка — снова с 1, лучший результат сохраняется
        await db.record_pet_message(CHAT, A, B, "2026-10-08", "2026-10-07", REWARDS)
        ev = await db.record_pet_message(CHAT, B, A, "2026-10-08", "2026-10-07", REWARDS)
        assert ev["streak"] == 1 and ev["pet"]["best_streak"] == 4

        # Напоминание: раз в день и только тем, кого кормили вчера
        rows = await db.pets_to_remind("2026-10-09", "2026-10-08")
        assert [int(r["id"]) for r in rows] == [pid]
        assert await db.pets_to_remind("2026-10-09", "2026-10-08") == []

        # Лимит питомцев на участника
        st, inv = await db.create_pet_invite(CHAT, A, C, "cat", now, 1)
        assert st == "limit_inviter"
        # Приглашение истекает
        st, inv = await db.create_pet_invite(CHAT, B, C, "cat", now, 0)
        assert st == "ok"
        st, _ = await db.answer_pet_invite(int(inv["id"]), C, True, now + 601, 600, 0)
        assert st == "expired"
        # Отказ
        st, inv = await db.create_pet_invite(CHAT, B, C, "cat", now, 0)
        st, _ = await db.answer_pet_invite(int(inv["id"]), C, False, now, 600, 0)
        assert st == "declined"

        # Месячный сброс питомцев не трогает
        await db.ensure_stats_period("2026-10")
        await db.close_stats_period("2026-10", "2026-11", now)
        pet = await db.get_pet(pid)
        assert pet["status"] == "active" and pet["streak"] == 1 and pet["best_streak"] == 4

        # Отпустить может только участник пары
        assert not await db.abandon_pet(pid, C, now)
        assert await db.abandon_pet(pid, A, now)
        assert await db.pet_for_pair(CHAT, A, B) is None
        st, _ = await db.create_pet_invite(CHAT, A, B, "dog", now, 5)
        assert st == "ok", "после расставания можно завести нового"
    finally:
        await db.close()


async def check_handlers() -> None:
    """Настоящий Dispatcher со всеми роутерами и фейковой сетью."""
    from aiogram import Bot, Dispatcher
    from aiogram.client.session.base import BaseSession
    from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
    from aiogram.types import (
        CallbackQuery, Chat, Message, MessageEntity, Update, User,
    )

    from config import load_config
    from handlers import (
        admin_router, counting_router, games_router, pets_router, quiz_router,
        shop_router, user_router,
    )
    from handlers.pets import process_pet_day

    class FakeSession(BaseSession):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list = []
            self.edits: list = []
            self.answers: list = []
            self._mid = 0

        async def close(self) -> None:
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, SendMessage):
                self.sent.append(method)
                self._mid += 1
                return Message(message_id=self._mid, date=datetime.datetime.now(),
                               chat=Chat(id=method.chat_id, type="supergroup"),
                               text=method.text)
            if isinstance(method, EditMessageText):
                self.edits.append(method)
                return Message(message_id=method.message_id or 1,
                               date=datetime.datetime.now(),
                               chat=Chat(id=method.chat_id or CHAT, type="supergroup"),
                               text=method.text)
            if isinstance(method, AnswerCallbackQuery):
                self.answers.append(method)
                return True
            raise AssertionError(f"неожиданный запрос {type(method).__name__}")

    with mock.patch.dict(os.environ, {"BOT_TOKEN": "123456:test", "PET_REWARDS": "2=3"}):
        cfg = load_config()
    cfg = replace(cfg, bonus_hours=(), pet_remind_hour=20)
    db = await _db()
    session = FakeSession()
    bot = Bot(token="123456:test", session=session)
    dp = Dispatcher(db=db, config=cfg)
    for r in (admin_router, quiz_router, user_router, shop_router, games_router,
              pets_router, counting_router):
        dp.include_router(r)

    def user(uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=f"Юзер{uid}", username=f"user{uid}")

    chat = Chat(id=CHAT, type="supergroup", title="Чат")
    counter = 0
    msgs: dict[int, Message] = {}

    async def say(uid, text=None, reply_to=None, sticker=False, entities=None):
        nonlocal counter
        counter += 1
        kw = {}
        if sticker:
            from aiogram.types import Sticker
            kw["sticker"] = Sticker(file_id="s", file_unique_id="s", type="regular",
                                    width=1, height=1, is_animated=False,
                                    is_video=False)
        else:
            kw["text"] = text
        msg = Message(message_id=counter, date=datetime.datetime.now(), chat=chat,
                      from_user=user(uid),
                      reply_to_message=msgs.get(reply_to) if reply_to else None,
                      entities=entities, **kw)
        msgs[counter] = msg
        before = len(session.sent)
        await dp.feed_update(bot, Update(update_id=counter, message=msg))
        return counter, session.sent[before:]

    async def press(uid, data):
        nonlocal counter
        counter += 1
        bot_msg = Message(message_id=500, date=datetime.datetime.now(), chat=chat,
                          from_user=User(id=999, is_bot=True, first_name="Ириска"))
        q = CallbackQuery(id=f"q{counter}", from_user=user(uid), chat_instance="ci",
                          message=bot_msg, data=data)
        before = (len(session.edits), len(session.answers))
        await dp.feed_update(bot, Update(update_id=counter, callback_query=q))
        return session.edits[before[0]:], session.answers[before[1]:]

    try:
        a1, _ = await say(A, "привет всем")
        b1, _ = await say(B, "и тебе")
        # Без ответа «питомец» показывает правила
        _, out = await say(A, "питомец")
        assert "Общий питомец" in out[0].text
        # Ответом — приглашение с кнопками
        _, out = await say(A, "Питомец!", reply_to=b1)
        assert len(out) == 1 and out[0].reply_markup is not None
        data_yes = out[0].reply_markup.inline_keyboard[0][0].callback_data
        assert data_yes.startswith("pet:yes:")
        edits, answers = await press(A, data_yes)
        assert answers[0].show_alert and not edits, "свою же кнопку не принять"
        edits, _ = await press(B, data_yes)
        assert "общее яйцо" in edits[0].text
        # «питомец» в статистику не попал
        assert (await db.get_user(CHAT, A))["total_counted"] == 1

        # Кормление: ответ текстом + ответ стикером
        _, out = await say(A, "как дела?", reply_to=b1)
        assert out == [], "одной стороны мало"
        _, out = await say(B, sticker=True, reply_to=a1)
        assert len(out) == 1 and "вылупился" in out[0].text, [m.text for m in out]
        # Сообщение-ответ засчитано и в статистику
        assert (await db.get_user(CHAT, A))["total_counted"] == 2

        # Имя
        _, out = await say(A, "назвать питомца Барсик")
        assert "Барсик" in out[0].text
        # Список
        _, out = await say(B, "мои питомцы")
        assert "Барсик" in out[0].text and "Серия: <b>1</b>" in out[0].text

        # Следующий день: упоминание вместо ответа тоже считается
        pet = await db.pet_for_pair(CHAT, A, B)
        yesterday = (datetime.datetime.now(cfg.tz) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        before = (datetime.datetime.now(cfg.tz) - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        db_ = db._require()
        await db_.execute("UPDATE pets SET last_fed_day=?, a_day=?, b_day=? WHERE id=?",
                          (yesterday, yesterday, yesterday, pet["id"]))
        await db_.commit()
        text = "эй @user202 привет"
        ent = [MessageEntity(type="mention", offset=3, length=8)]
        _, out = await say(A, text, entities=ent)
        assert out == []
        _, out = await say(B, "ага", reply_to=a1)
        assert "серия <b>2</b>" in out[0].text and "+3 🍬" in out[0].text, out[0].text

        # Топ
        _, out = await say(C, "топ питомцев")
        assert "Барсик" in out[0].text

        # Смотритель: побег и уведомление
        await db_.execute("UPDATE pets SET last_fed_day=? WHERE id=?", (before, pet["id"]))
        await db_.commit()
        sent_before = len(session.sent)
        lost, _ = await process_pet_day(bot, db, cfg, now=datetime.datetime.now(cfg.tz).replace(hour=10))
        assert lost == 1 and "сбежали" in session.sent[sent_before].text
        # Вернуть за ириски (у A 3 🍬 награды, цена 20 — не хватает)
        _, out = await say(A, "вернуть питомца")
        assert "Не хватает" in out[0].text
        await db.adjust_balance(CHAT, A, 50, "тест", None)
        _, out = await say(A, "вернуть питомца")
        assert "вернулся" in out[0].text and "<b>2</b>" in out[0].text

        # Напоминание вечером
        sent_before = len(session.sent)
        _, reminded = await process_pet_day(
            bot, db, cfg, now=datetime.datetime.now(cfg.tz).replace(hour=21))
        assert reminded == 1 and "проголодались" in session.sent[sent_before].text
        _, reminded = await process_pet_day(
            bot, db, cfg, now=datetime.datetime.now(cfg.tz).replace(hour=22))
        assert reminded == 0, "раз в день"

        # Отпустить — с подтверждением
        _, out = await say(B, "бросить питомца")
        kb = out[0].reply_markup.inline_keyboard[0]
        edits, answers = await press(C, kb[0].callback_data)
        assert answers[0].show_alert, "чужой не может отпустить"
        edits, _ = await press(B, kb[0].callback_data)
        assert "отпущен" in edits[0].text
        assert await db.pet_for_pair(CHAT, A, B) is None

        # Выключатель
        off = replace(cfg, pets_enabled=False)
        assert (await process_pet_day(bot, db, off)) == (0, 0)
    finally:
        await db.close()


async def main() -> None:
    await check_logic()
    await check_handlers()
    print("✅ Тесты питомца пройдены")


if __name__ == "__main__":
    asyncio.run(main())
