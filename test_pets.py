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
    from aiogram.methods import (
        AnswerCallbackQuery, EditMessageCaption, EditMessageMedia, EditMessageText,
        SendMessage, SendPhoto,
    )
    from aiogram.types import (
        CallbackQuery, Chat, Message, MessageEntity, PhotoSize, Update, User,
    )

    from config import load_config
    from handlers import (
        admin_router, counting_router, games_router, pet_menu_router, pets_router,
        quiz_router, shop_router, user_router,
    )
    from handlers.pet_menu import process_pet_events
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
            if isinstance(method, SendPhoto):
                self.sent.append(method)
                self._mid += 1
                return Message(message_id=self._mid, date=datetime.datetime.now(),
                               chat=Chat(id=method.chat_id, type="supergroup"),
                               caption=method.caption,
                               photo=[PhotoSize(file_id=f"ph{self._mid}",
                                                file_unique_id=f"u{self._mid}",
                                                width=800, height=800)])
            if isinstance(method, (EditMessageMedia, EditMessageCaption)):
                self.edits.append(method)
                return Message(message_id=method.message_id or 1,
                               date=datetime.datetime.now(),
                               chat=Chat(id=method.chat_id or CHAT, type="supergroup"),
                               caption=getattr(method, "caption", None)
                               or getattr(getattr(method, "media", None), "caption", None),
                               photo=[PhotoSize(file_id="phE", file_unique_id="uE",
                                                width=800, height=800)])
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
              pets_router, pet_menu_router, counting_router):
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

    def body(m) -> str:
        """Текст исходящего сообщения: text, подпись фото или подпись в медиа."""
        if getattr(m, "text", None):
            return m.text
        if getattr(m, "caption", None):
            return m.caption
        media = getattr(m, "media", None)
        return getattr(media, "caption", "") or ""

    async def press(uid, data, photo=False):
        nonlocal counter
        counter += 1
        extra = {}
        if photo:
            extra["photo"] = [PhotoSize(file_id="p", file_unique_id="p", width=1, height=1)]
            extra["caption"] = "карточка"
        bot_msg = Message(message_id=500, date=datetime.datetime.now(), chat=chat,
                          from_user=User(id=999, is_bot=True, first_name="Ириска"), **extra)
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
        assert len(out) == 1 and isinstance(out[0], SendPhoto), out
        assert "вылупился" in out[0].caption and out[0].photo is not None
        # Сообщение-ответ засчитано и в статистику
        assert (await db.get_user(CHAT, A))["total_counted"] == 2

        # Имя
        _, out = await say(A, "назвать питомца Барсик")
        assert "Барсик" in out[0].text
        # Карточка с фото и кнопками
        _, out = await say(B, "мои питомцы")
        assert isinstance(out[0], SendPhoto), out
        assert "Барсик" in out[0].caption and "Серия <b>1</b>" in out[0].caption
        cbs = [b.callback_data for row in out[0].reply_markup.inline_keyboard for b in row]
        assert any(c.startswith("pm:feed:") for c in cbs) and any(
            c.startswith("pm:shop:") for c in cbs)
        # file_id запомнился — второй раз файл с диска не грузится
        sp = (await db.pet_for_pair(CHAT, A, B))["species"]
        assert await db.get_pet_media(f"{sp}_baby"), "file_id не сохранён"

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
        assert lost == 1 and "сбежали" in body(session.sent[sent_before])
        assert isinstance(session.sent[sent_before], SendPhoto), "фото «сбежал»"
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
        assert reminded == 1 and "проголодались" in body(session.sent[sent_before])
        _, reminded = await process_pet_day(
            bot, db, cfg, now=datetime.datetime.now(cfg.tz).replace(hour=22))
        assert reminded == 0, "раз в день"

        await check_menu(db, cfg, pet["id"], say, press, body, session,
                         process_pet_day, process_pet_events, bot)

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


async def check_menu(db, cfg, pet_id, say, press, body, session,
                     process_pet_day, process_pet_events, bot) -> None:
    """🐾 Питомцы 2.0: кнопки ухода, магазин, гардероб, события, заморозка."""
    from handlers import pet_menu
    from pet_care import EVENTS

    noon = datetime.datetime.now(cfg.tz).replace(hour=12, minute=0, second=0, microsecond=0)
    db_ = db._require()
    with mock.patch.object(pet_menu, "local_now", lambda config, ts=None: noon):
        # Чужой не нажимает
        edits, answers = await press(C, f"pm:main:{pet_id}", photo=True)
        assert answers[0].show_alert and not edits
        # Поиграть: A, затем B — «вдвоём»
        await db_.execute("UPDATE pets SET food=50, joy=40, energy=80 WHERE id=?", (pet_id,))
        await db_.commit()
        edits, answers = await press(A, f"pm:play:{pet_id}", photo=True)
        assert edits and "мячиком" in body(edits[0]), body(edits[0]) if edits else answers
        joy_a = float((await db.get_pet(pet_id))["joy"])
        assert joy_a > 60, joy_a
        edits, answers = await press(A, f"pm:play:{pet_id}", photo=True)
        assert answers[0].show_alert and "через" in answers[0].text, "кулдаун"
        edits, answers = await press(B, f"pm:play:{pet_id}", photo=True)
        assert "вдвоём" in body(edits[0])
        # Покормить: пузико полное — отказ
        await db_.execute("UPDATE pets SET food=99 WHERE id=?", (pet_id,))
        await db_.commit()
        edits, answers = await press(A, f"pm:feed:{pet_id}", photo=True)
        assert answers[0].show_alert and not edits

        # Магазин: бантик и заморозка
        balance0 = (await db.get_user(CHAT, A))["balance"]
        edits, _ = await press(A, f"pm:shop:{pet_id}", photo=True)
        assert "Магазин" in body(edits[0])
        edits, answers = await press(A, f"pm:buy:{pet_id}:bow", photo=True)
        assert "куплен" in body(edits[0]), answers
        p = await db.get_pet(pet_id)
        assert p["items"] == "bow" and p["worn"] == "bow"
        assert (await db.get_user(CHAT, A))["balance"] == balance0 - 5
        edits, answers = await press(A, f"pm:buy:{pet_id}:bow", photo=True)
        assert answers[0].show_alert, "второй бантик не продаём"
        await press(B, f"pm:buy:{pet_id}:freeze", photo=True)
        assert int((await db.get_pet(pet_id))["freezes"]) in (0, 1)
        await db.adjust_balance(CHAT, B, 30, "тест", None)
        await press(B, f"pm:buy:{pet_id}:freeze", photo=True)
        assert int((await db.get_pet(pet_id))["freezes"]) >= 1
        # Гардероб: снять бантик
        edits, _ = await press(A, f"pm:wear:{pet_id}:bow", photo=True)
        assert (await db.get_pet(pet_id))["worn"] == ""
        assert "Гардероб" in body(edits[0])
        # Карточка показывает надетое
        await press(A, f"pm:wear:{pet_id}:bow", photo=True)
        edits, _ = await press(A, f"pm:main:{pet_id}", photo=True)
        assert "🎀" in body(edits[0]) and "Заморозок" in body(edits[0])

    # События: монетка — первый забирает
    await db_.execute("UPDATE pets SET events_day=NULL WHERE id=?", (pet_id,))
    await db_.commit()
    with mock.patch.object(pet_menu, "random_event", lambda: EVENTS["coin"]), \
            mock.patch.object(pet_menu, "first_event_ts", lambda now: 0):
        before = len(session.sent)
        assert await process_pet_events(bot, db, cfg, now=noon) == 1
        ev_msg = session.sent[before]
        assert "монетку" in body(ev_msg)
        data = ev_msg.reply_markup.inline_keyboard[0][0].callback_data
        assert await process_pet_events(bot, db, cfg, now=noon) == 0, "пауза между событиями"
    edits, answers = await press(C, data, photo=True)
    assert answers[0].show_alert, "чужое событие"
    bal = (await db.get_user(CHAT, A))["balance"]
    edits, _ = await press(A, data, photo=True)
    assert (await db.get_user(CHAT, A))["balance"] == bal + 1
    assert "+1 🍬" in body(edits[0])
    _, answers = await press(B, data, photo=True)
    assert "закончилось" in answers[0].text or "Уже" in answers[0].text

    # Бабочка — нужны оба
    ev_id = await db.create_pet_event(pet_id, CHAT, "butterfly", noon.timestamp(), 7200)
    await db.set_pet_event_message(ev_id, 777)
    edits, answers = await press(A, f"pe:{ev_id}:catch", photo=True)
    assert "второго" in answers[0].text
    bal_b = (await db.get_user(CHAT, B))["balance"]
    edits, _ = await press(B, f"pe:{ev_id}:catch", photo=True)
    assert "вдвоём" in body(edits[-1])
    assert (await db.get_user(CHAT, B))["balance"] == bal_b + 1

    # Простуда без лечения — истекает и портит настроение
    await db_.execute("UPDATE pets SET joy=80, care_ts=? WHERE id=?", (noon.timestamp(), pet_id))
    await db_.commit()
    ev_id = await db.create_pet_event(pet_id, CHAT, "cold", noon.timestamp(), 60)
    await db.set_pet_event_message(ev_id, 778)
    later = noon + datetime.timedelta(minutes=5)
    with mock.patch.object(time_module(), "time", lambda: noon.timestamp()):
        await process_pet_events(bot, db, replace(cfg, pet_events_per_day=0), now=later)
    assert (await db.get_pet_event(ev_id))["status"] == "expired"
    assert float((await db.get_pet(pet_id))["joy"]) <= 51

    # Заморозка спасает серию
    p = await db.get_pet(pet_id)
    streak = int(p["streak"])
    old = (datetime.datetime.now(cfg.tz) - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    await db_.execute("UPDATE pets SET last_fed_day=?, freezes=1 WHERE id=?", (old, pet_id))
    await db_.commit()
    before = len(session.sent)
    lost, _ = await process_pet_day(bot, db, cfg,
                                    now=datetime.datetime.now(cfg.tz).replace(hour=10))
    p = await db.get_pet(pet_id)
    assert lost == 0 and int(p["streak"]) == streak and int(p["freezes"]) == 0
    assert "Заморозка" in body(session.sent[before])


def time_module():
    import handlers.pet_menu as pm
    return pm.time


def check_care_logic() -> None:
    from zoneinfo import ZoneInfo

    from pet_care import check_action, decay, image_key, join_set, parse_set

    tz = ZoneInfo("Europe/Moscow")
    day = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=tz).timestamp()
    night = datetime.datetime(2026, 10, 1, 1, 0, tzinfo=tz).timestamp()
    f, j, e = decay(80, 80, 50, day, day + 3 * 3600, tz)
    assert (round(f), round(j), round(e)) == (68, 71, 44), (f, j, e)
    f, j, e = decay(80, 80, 50, night, night + 3 * 3600, tz)
    assert e > 60 and f > 75, "ночью спит: энергия растёт"
    fh, _, _ = decay(80, 80, 50, day, day + 3 * 3600, tz, house=True)
    assert fh > 68, "домик замедляет голод"
    assert decay(10, 10, 10, day, day + 99 * 3600, tz)[0] == 0
    assert image_key("fox", 0, 0, 50, 50, 50, 12) == "egg"
    assert image_key("fox", 0, 5, 50, 50, 50, 12) == "runaway"
    assert image_key("fox", 8, 8, 50, 50, 50, 12) == "fox_teen"
    assert image_key("fox", 120, 120, 50, 50, 50, 12) == "fox_legend"
    assert image_key("fox", 3, 3, 50, 50, 50, 3) == "fox_sleep"
    assert image_key("fox", 3, 3, 20, 50, 50, 12) == "fox_hungry"
    assert image_key("fox", 3, 3, 90, 90, 50, 12) == "fox_happy"
    assert check_action("play", 50, 50, 50, 3) is not None, "ночью не играем"
    assert check_action("play", 50, 50, 50, 12) is None
    assert check_action("sleep", 50, 50, 95, 12) is not None
    assert parse_set("bow,bad,,glasses") == ["bow", "glasses"]
    assert join_set(["bow", "bow", "x", "scarf"]) == "bow,scarf"
    import os as _os
    from pet_care import ASSETS_DIR
    from pets import SPECIES, STAGES
    need = {"egg", "egg_hatching", "runaway"}
    for sp in SPECIES:
        need |= {f"{sp.code}_{st.code}" for st in STAGES}
        need |= {f"{sp.code}_{m}" for m in ("sleep", "hungry", "happy")}
    missing = [k for k in need if not _os.path.isfile(_os.path.join(ASSETS_DIR, f"{k}.jpg"))]
    assert not missing, f"нет картинок: {missing}"


async def main() -> None:
    check_care_logic()
    await check_logic()
    await check_handlers()
    print("✅ Тесты питомца пройдены")


if __name__ == "__main__":
    asyncio.run(main())
