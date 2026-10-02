"""Локальные тесты логики начислений — без Telegram.

Запуск: python3 test_logic.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime

from db import Database
from monthly import monthly_summary_text, stats_period_for

CHAT = -1001234567890
USER1 = 111
USER2 = 222
PER = 100


async def run() -> None:
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "test.db"))
    await db.connect()

    ts = 1_000_000.0
    accrued_total = 0

    # 250 засчитанных сообщений: 100 «вчера», 150 «сегодня»
    for i in range(250):
        counted, accrued = await db.try_count_message(
            chat_id=CHAT, user_id=USER1, username="tester", first_name="Тестер",
            day="2026-07-30" if i < 100 else "2026-07-31",
            msg_hash=f"hash-{i}", now_ts=ts, cooldown=5, per_iriska=PER,
        )
        assert counted, f"сообщение {i} не засчиталось"
        accrued_total += accrued
        ts += 10
    last_ts = ts - 10

    row = await db.get_user(CHAT, USER1)
    assert row["total_counted"] == 250, row["total_counted"]
    assert row["balance"] == 2 and accrued_total == 2, "250 сообщений => 2 ириски"
    assert row["progress"] == 50, row["progress"]
    assert row["earned_total"] == 2

    # Кулдаун: сообщение через 2 сек не засчитывается
    counted, _ = await db.try_count_message(
        chat_id=CHAT, user_id=USER1, username="tester", first_name="Тестер",
        day="2026-07-31", msg_hash="hash-cooldown", now_ts=last_ts + 2,
        cooldown=5, per_iriska=PER,
    )
    assert not counted, "кулдаун не сработал"

    # Повтор того же сообщения подряд не засчитывается
    counted, _ = await db.try_count_message(
        chat_id=CHAT, user_id=USER1, username="tester", first_name="Тестер",
        day="2026-07-31", msg_hash="hash-249", now_ts=last_ts + 100,
        cooldown=5, per_iriska=PER,
    )
    assert not counted, "дубликат засчитался"

    # Новое сообщение после кулдауна — засчитывается
    counted, _ = await db.try_count_message(
        chat_id=CHAT, user_id=USER1, username="tester", first_name="Тестер",
        day="2026-07-31", msg_hash="hash-new", now_ts=last_ts + 200,
        cooldown=5, per_iriska=PER,
    )
    assert counted

    # Дневная/недельная статистика
    assert await db.user_count_on(CHAT, USER1, "2026-07-31") == 151
    assert await db.user_count_since(CHAT, USER1, "2026-07-30") == 251
    assert await db.user_count_since(CHAT, USER1, "2026-07-31") == 151

    # Второй участник
    ts2 = 2_000_000.0
    for i in range(5):
        counted, _ = await db.try_count_message(
            chat_id=CHAT, user_id=USER2, username="second", first_name="Второй",
            day="2026-07-31", msg_hash=f"u2-{i}", now_ts=ts2, cooldown=5, per_iriska=PER,
        )
        assert counted
        ts2 += 10

    # Топы и ранги
    top = await db.top_alltime(CHAT, 10)
    assert [r["user_id"] for r in top] == [USER1, USER2]
    assert top[0]["total_counted"] == 251

    week = await db.top_since(CHAT, "2026-07-25", 10)
    assert [r["user_id"] for r in week] == [USER1, USER2]
    assert week[0]["cnt"] == 251 and week[1]["cnt"] == 5

    assert await db.rank(CHAT, 251) == 1
    assert await db.rank(CHAT, 5) == 2

    # Админские операции с балансом
    status, bal = await db.adjust_balance(CHAT, USER1, 298, "начислено админом", 999)
    assert status == "ok" and bal == 300

    status, bal = await db.adjust_balance(CHAT, USER1, -301, "списано админом", 999)
    assert status == "insufficient" and bal == 300, "ушли в минус"

    status, bal = await db.adjust_balance(CHAT, USER1, -300, "списано админом (вывод)", 999)
    assert status == "ok" and bal == 0

    status, _ = await db.adjust_balance(CHAT, 555, 10, "тест", 999)
    assert status == "not_found"

    await db.ensure_user(CHAT, 555, "someone", "Некто")
    status, bal = await db.adjust_balance(CHAT, 555, 10, "начислено админом", 999)
    assert status == "ok" and bal == 10

    # Итоги чата
    totals = await db.chat_totals(CHAT)
    assert totals["users"] == 2
    assert totals["msgs"] == 256
    assert totals["earned"] == 300  # 2 за активность + 298 бонусом

    # --- Ежедневный бонус со стриком ---
    status, bal, amt, streak = await db.claim_bonus(
        CHAT, USER1, "tester", "Тестер", "2026-08-01", "2026-07-31", 2, 3)
    assert status == "ok" and bal == 2 and amt == 2 and streak == 1
    status, bal2, amt, streak = await db.claim_bonus(
        CHAT, USER1, "tester", "Тестер", "2026-08-01", "2026-07-31", 2, 3)
    assert status == "already" and bal2 == 2 and streak == 1, "бонус выдался дважды за день"
    status, bal3, amt, streak = await db.claim_bonus(
        CHAT, USER1, "tester", "Тестер", "2026-08-02", "2026-08-01", 1, 3)
    assert status == "ok" and streak == 2 and amt == 2, "стрик не вырос"  # 1 базовый +1 стрик
    assert bal3 == 4

    # --- Дуэли ---
    # USER1: 4, USER2: 0 -> выравниваем балансы
    await db.adjust_balance(CHAT, USER1, 46, "тест", None)   # 50
    await db.adjust_balance(CHAT, USER2, 50, "тест", None)   # 50

    now = 3_000_000.0
    duel_id = await db.create_duel(CHAT, USER1, USER2, 20, now)
    assert await db.has_pending_duel(CHAT, USER1)
    assert await db.has_pending_duel(CHAT, USER2)
    duel = await db.pending_duel_for_target(CHAT, USER2)
    assert duel is not None and duel["id"] == duel_id and duel["amount"] == 20

    # Расчёт: USER2 проиграл — 20 переходят USER1
    status, wb, lb = await db.settle_duel(duel_id, CHAT, USER1, USER2, 20, now + 10)
    assert status == "ok" and wb == 70 and lb == 30
    assert not await db.has_pending_duel(CHAT, USER1)

    # Проигравший без денег: дуэль отменяется, балансы не трогаются
    duel_id2 = await db.create_duel(CHAT, USER1, USER2, 20, now + 20)
    await db.adjust_balance(CHAT, USER2, -25, "тест: обнуляем", None)  # у USER2 осталось 5
    status, _, lb = await db.settle_duel(duel_id2, CHAT, USER1, USER2, 20, now + 30)
    assert status == "loser_broke" and lb == 5
    r1 = await db.get_user(CHAT, USER1)
    assert r1["balance"] == 70, "баланс победителя изменился при отменённой дуэли"

    # Протухание вызова
    duel_id3 = await db.create_duel(CHAT, USER1, USER2, 5, now + 40)
    await db.expire_old_duels(CHAT, now + 40 + 400, ttl=300)
    assert not await db.has_pending_duel(CHAT, USER1), "дуэль не протухла"

    # --- Слоты: раскладка выплат ---
    from handlers.games import slot_multiplier, slot_reels

    assert slot_reels(64) == (3, 3, 3)
    assert slot_multiplier(64)[0] == 10          # джекпот 777
    for v in (1, 22, 43):
        assert slot_multiplier(v)[0] == 5, v     # три одинаковых
    assert slot_multiplier(16)[0] == 1           # (3,3,0) — две семёрки
    assert slot_multiplier(52)[0] == 1           # (3,0,3) — две семёрки
    assert slot_multiplier(37)[0] == 0           # (0,1,2) — мимо
    total_ev = sum(slot_multiplier(v)[0] for v in range(1, 65))
    assert total_ev == 10 + 5 * 3 + 1 * 9, total_ev  # дом в плюсе: 34/64

    # --- Антиспам выключен: короткие, подряд и с повторами — считаются ---
    USER4 = 444
    for i in range(3):
        counted, _ = await db.try_count_message(
            chat_id=CHAT, user_id=USER4, username="fast", first_name="Быстрый",
            day="2026-08-01", msg_hash="same-hash",  # один хеш, одно время
            now_ts=5_000_000.0, cooldown=0, per_iriska=100, dedupe=False,
        )
        assert counted, "без антиспама сообщение обязано засчитаться"
    r4 = await db.get_user(CHAT, USER4)
    assert r4["total_counted"] == 3, "повторы подряд должны считаться при dedupe=False"

    # --- Бонусные часы ---
    from config import _parse_windows
    from handlers.common import is_bonus_hour

    assert _parse_windows("7-9,13-15,20-21") == ((7, 9), (13, 15), (20, 21))
    assert _parse_windows("") == ()
    assert _parse_windows("25-30, 5-4, 8-10") == ((8, 10),)

    ws = ((7, 9), (13, 15), (20, 21))
    assert is_bonus_hour(7, ws) and is_bonus_hour(8, ws)
    assert not is_bonus_hour(9, ws) and not is_bonus_hour(6, ws)
    assert is_bonus_hour(13, ws) and is_bonus_hour(14, ws) and not is_bonus_hour(15, ws)
    assert is_bonus_hour(20, ws) and not is_bonus_hour(21, ws) and not is_bonus_hour(12, ws)

    from handlers.common import current_window, next_window_start

    assert current_window(8, ws) == (7, 9)
    assert current_window(14, ws) == (13, 15)
    assert current_window(12, ws) is None
    assert next_window_start(9, ws) == 13
    assert next_window_start(16, ws) == 20
    assert next_window_start(22, ws) == 7   # переход на завтра
    assert next_window_start(3, ws) == 7
    assert next_window_start(10, ()) is None

    # Список чатов для анонсов
    chats = await db.known_chats()
    assert CHAT in chats

    # Вес х2: 50 сообщений с weight=2 => 1 ириска, счётчик сообщений честный (50)
    USER3 = 333
    ts3 = 4_000_000.0
    for i in range(50):
        counted, _ = await db.try_count_message(
            chat_id=CHAT, user_id=USER3, username="w2", first_name="Двойной",
            day="2026-08-01", msg_hash=f"w2-{i}", now_ts=ts3,
            cooldown=5, per_iriska=100, weight=2,
        )
        assert counted
        ts3 += 10
    r3 = await db.get_user(CHAT, USER3)
    assert r3["balance"] == 1, "50 сообщений х2 должны дать 1 ириску"
    assert r3["progress"] == 0
    assert r3["total_counted"] == 50, "счётчик сообщений должен остаться честным"
    assert await db.user_count_on(CHAT, USER3, "2026-08-01") == 50

    # --- Стрик: рост, потолок и сброс ---
    USER5 = 555_001
    expected = [
        ("2026-08-10", "2026-08-09", 2, 1),   # день 1: базовый 2
        ("2026-08-11", "2026-08-10", 3, 2),   # +1
        ("2026-08-12", "2026-08-11", 4, 3),   # +2
        ("2026-08-13", "2026-08-12", 5, 4),   # +3 (потолок)
        ("2026-08-14", "2026-08-13", 5, 5),   # прибавка не растёт выше потолка
    ]
    bal_run = 0
    for day, yest, want_amt, want_streak in expected:
        status, bal_run, amt, streak = await db.claim_bonus(
            CHAT, USER5, "st", "Стрикер", day, yest, 2, 3)
        assert status == "ok" and amt == want_amt and streak == want_streak, (day, amt, streak)
    assert bal_run == 2 + 3 + 4 + 5 + 5

    # Пропустил день — серия сгорела
    status, bal_run, amt, streak = await db.claim_bonus(
        CHAT, USER5, "st", "Стрикер", "2026-08-20", "2026-08-19", 2, 3)
    assert status == "ok" and amt == 2 and streak == 1, "стрик не сбросился после пропуска"

    # --- Викторина ---
    from handlers.quiz import parse_quiz_args

    p = parse_quiz_args("Столица Франции? | Париж")
    assert p == (1, "Столица Франции?", ["париж"], "Париж"), p
    p = parse_quiz_args("5 | 2+2? | 4; четыре")
    assert p == (5, "2+2?", ["4", "четыре"], "4"), p
    p = parse_quiz_args("10 плюс 10? | 20")  # приз только в формате N | вопрос | ответ
    assert p is not None and p[0] == 1 and p[1] == "10 плюс 10?"
    assert parse_quiz_args("только вопрос без ответа") is None
    assert parse_quiz_args("") is None

    quiz_id = await db.create_quiz(CHAT, "Вопрос?", ["ответ"], "Ответ", 3, 999, 6_000_000.0)
    q = await db.active_quiz(CHAT)
    assert q is not None and q["id"] == quiz_id and q["prize"] == 3
    assert CHAT in await db.active_quiz_chat_ids()

    r2_before = (await db.get_user(CHAT, USER2))["balance"]
    status, prize, bal = await db.try_win_quiz(quiz_id, CHAT, USER2, "second", "Второй", 6_000_100.0)
    assert status == "ok" and prize == 3 and bal == r2_before + 3
    status2, _, _ = await db.try_win_quiz(quiz_id, CHAT, USER1, "tester", "Тестер", 6_000_101.0)
    assert status2 == "late", "второй ответивший не должен получить приз"
    assert await db.active_quiz(CHAT) is None

    quiz_id2 = await db.create_quiz(CHAT, "Ещё вопрос?", ["икс"], "икс", 1, 999, 6_000_200.0)
    actives, queued = await db.cancel_active_quizzes(6_000_300.0)
    assert len(actives) == 1 and actives[0]["id"] == quiz_id2 and queued == 0
    assert await db.active_quiz(CHAT) is None
    assert await db.active_quiz_chat_ids() == []

    # --- Пачка вопросов (очередь) ---
    from handlers.quiz import parse_quiz_pack

    items, bad = parse_quiz_pack("В1? | о1\n\n5 | В2? | о2; вар\nплохая строка")
    assert len(items) == 2 and bad == [4], (items, bad)
    assert items[1][0] == 5 and items[1][2] == ["о2", "вар"]

    items, bad = parse_quiz_pack("В1? | а\nВ2? | б\nВ3? | в")
    assert bad == [] and len(items) == 3
    await db.enqueue_quizzes(CHAT, items, 999, 7_000_000.0)
    assert await db.queued_count(CHAT) == 3
    assert await db.active_quiz(CHAT) is None
    assert CHAT in await db.chats_with_queued()

    row1 = await db.activate_next_quiz(CHAT, 7_000_010.0)
    assert row1 is not None and row1["seq"] == 1 and row1["total"] == 3
    assert await db.activate_next_quiz(CHAT, 7_000_011.0) is None  # уже есть активный
    assert await db.queued_count(CHAT) == 2

    status, _, _ = await db.try_win_quiz(int(row1["id"]), CHAT, USER1, "tester", "Тестер", 7_000_020.0)
    assert status == "ok"
    row2 = await db.activate_next_quiz(CHAT, 7_000_021.0)
    assert row2 is not None and row2["seq"] == 2

    # Пропуск вопроса: отмена текущего + следующий из очереди
    await db.cancel_quiz(int(row2["id"]), 7_000_030.0)
    row3 = await db.activate_next_quiz(CHAT, 7_000_031.0)
    assert row3 is not None and row3["seq"] == 3

    actives, queued = await db.cancel_active_quizzes(7_000_040.0)
    assert len(actives) == 1 and queued == 0
    assert await db.chats_with_queued() == []

    # Остановка с непустой очередью
    await db.enqueue_quizzes(CHAT, items, 999, 7_000_100.0)
    await db.activate_next_quiz(CHAT, 7_000_101.0)
    actives, queued = await db.cancel_active_quizzes(7_000_102.0)
    assert len(actives) == 1 and queued == 2
    assert await db.queued_count(CHAT) == 0

    # --- Месячный сброс в 23:59 последнего дня ---
    assert stats_period_for(datetime(2026, 8, 31, 23, 58)) == "2026-08"
    assert stats_period_for(datetime(2026, 8, 31, 23, 59)) == "2026-09"
    assert stats_period_for(datetime(2026, 2, 28, 23, 59)) == "2026-03"
    assert stats_period_for(datetime(2026, 12, 31, 23, 59)) == "2027-01"

    month_path = os.path.join(tmp, "month.db")
    month_db = Database(month_path)
    await month_db.connect()
    for i in range(5):
        await month_db.try_count_message(
            chat_id=CHAT, user_id=USER1, username="first", first_name="Первый",
            day="2026-08-31", msg_hash=f"m1-{i}", now_ts=8_000_000 + i,
            cooldown=0, per_iriska=100, dedupe=False,
        )
    for i in range(3):
        await month_db.try_count_message(
            chat_id=CHAT, user_id=USER2, username="second", first_name="Второй",
            day="2026-08-31", msg_hash=f"m2-{i}", now_ts=8_000_100 + i,
            cooldown=0, per_iriska=100, dedupe=False,
        )
    await month_db.adjust_balance(CHAT, USER1, 12, "тест", None)
    await month_db.adjust_balance(CHAT, USER2, 7, "тест", None)
    duel_id = await month_db.create_duel(CHAT, USER1, USER2, 5, 8_000_200)
    assert duel_id > 0
    assert await month_db.ensure_stats_period("2026-08") == "2026-08"

    snapshots = await month_db.close_stats_period(
        "2026-08", "2026-09", 8_000_300,
    )
    assert snapshots is not None and len(snapshots) == 1
    assert [r["user_id"] for r in snapshots[0]["top"]] == [USER1, USER2]
    assert snapshots[0]["totals"]["msgs"] == 8
    summary = monthly_summary_text("2026-08", snapshots[0])
    assert "Август 2026 подошёл к концу" in summary
    assert "итоги августа 2026" in summary
    assert "все накопленные ириски сгорели" in summary

    for user_id in (USER1, USER2):
        row = await month_db.get_user(CHAT, user_id)
        assert row["total_counted"] == 0 and row["balance"] == 0
        assert row["earned_total"] == 0 and row["progress"] == 0
        assert row["last_bonus_day"] is None and row["bonus_streak"] == 0
    assert await month_db.user_count_since(CHAT, USER1, "2000-01-01") == 0
    assert not await month_db.has_pending_duel(CHAT, USER1)
    assert await month_db.close_stats_period(
        "2026-08", "2026-09", 8_000_301,
    ) is None, "месяц нельзя закрыть дважды"
    assert await month_db.ensure_stats_period("2026-10") == "2026-09"
    await month_db.close()

    # --- Миграция старой базы (без колонки last_bonus_day) ---
    import sqlite3

    old_path = os.path.join(tmp, "old.db")
    conn = sqlite3.connect(old_path)
    conn.execute(
        "CREATE TABLE users (chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, "
        "username TEXT, first_name TEXT, total_counted INTEGER NOT NULL DEFAULT 0, "
        "balance INTEGER NOT NULL DEFAULT 0, earned_total INTEGER NOT NULL DEFAULT 0, "
        "progress INTEGER NOT NULL DEFAULT 0, last_counted_ts REAL NOT NULL DEFAULT 0, "
        "last_msg_hash TEXT, PRIMARY KEY (chat_id, user_id))"
    )
    conn.execute(
        "INSERT INTO users (chat_id, user_id, username, first_name, balance) "
        "VALUES (?, ?, 'old', 'Старый', 42)",
        (CHAT, 777),
    )
    conn.commit()
    conn.close()

    old_db = Database(old_path)
    await old_db.connect()  # должна пройти миграция
    status, bal, amt, streak = await old_db.claim_bonus(
        CHAT, 777, "old", "Старый", "2026-08-01", "2026-07-31", 2, 3)
    assert status == "ok" and bal == 44 and streak == 1, "миграция/бонус на старой базе не сработали"
    row = await old_db.get_user(CHAT, 777)
    assert row["balance"] == 44 and row["bonus_streak"] == 1
    await old_db.close()

    await db.close()

    await run_subscription()
    await run_shop()
    print("✅ Все тесты пройдены")


# ---------------------------------------------------------------------------
# Бонус только для подписчиков канала
# ---------------------------------------------------------------------------

async def run_subscription() -> None:
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "subscription.db"))
    await db.connect()
    try:
        await _check_subscription(db)
    finally:
        await db.close()  # иначе при падении проверки процесс не завершится


async def _check_subscription(db: Database) -> None:
    """Проверка подписки по ID: разбор настроек, ответы бота, сбои, самопроверка.

    Берётся настоящий Dispatcher с настоящими роутерами и объектами aiogram,
    а сеть подменена: фейковая сессия отвечает на getChatMember по сценарию
    и запоминает всё, что бот отправил в чат.
    """
    import datetime
    import logging
    from dataclasses import replace
    from unittest import mock

    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.base import BaseSession
    from aiogram.enums import ParseMode
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
    from aiogram.methods import (
        AnswerCallbackQuery, EditMessageText, GetChat, GetChatMember, GetMe,
        SendMessage,
    )
    from aiogram.types import (
        Chat, ChatFullInfo, ChatMemberAdministrator, ChatMemberBanned,
        ChatMemberLeft, ChatMemberMember, ChatMemberOwner, ChatMemberRestricted,
        InlineKeyboardMarkup, Message, Update, User,
    )

    from config import load_config, normalize_channel_url, parse_channel_ref
    from handlers import (
        admin_router, counting_router, games_router, quiz_router, shop_router,
        user_router,
    )
    from subscription import setup_channel

    # --- Разбор CHANNEL_ID / CHANNEL_URL ---
    assert parse_channel_ref("") is None
    assert parse_channel_ref("-1001234567890") == -1001234567890
    assert parse_channel_ref("1234567890") == -1001234567890, "ID из ссылки t.me/c/... без -100"
    for raw in ("@mychannel", "mychannel", "https://t.me/mychannel", "t.me/mychannel/"):
        assert parse_channel_ref(raw) == "@mychannel", raw
    for raw in ("https://t.me/+AbCdEf", "два слова", "ab"):
        try:
            parse_channel_ref(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{raw!r} должно считаться ошибкой")
    assert normalize_channel_url("") == ""
    assert normalize_channel_url("t.me/mychannel") == "https://t.me/mychannel"
    assert normalize_channel_url("@mychannel") == "https://t.me/mychannel"
    assert normalize_channel_url("https://t.me/+AbCd") == "https://t.me/+AbCd"

    env = {"BOT_TOKEN": "123456:test", "CHANNEL_ID": "", "CHANNEL_URL": ""}
    with mock.patch.dict(os.environ, env):
        base_cfg = load_config()
    assert base_cfg.channel_id is None and base_cfg.channel_url == "", \
        "по умолчанию проверка подписки выключена"
    with mock.patch.dict(os.environ, {**env, "CHANNEL_ID": "@Iriska_Channel",
                                      "CHANNEL_URL": "t.me/Iriska_Channel"}):
        cfg = load_config()
    assert cfg.channel_id == "@Iriska_Channel"
    assert cfg.channel_url == "https://t.me/Iriska_Channel"
    with mock.patch.dict(os.environ, {**env, "CHANNEL_ID": "@Iriska_Channel"}):
        assert load_config().channel_url == "https://t.me/Iriska_Channel", \
            "для @username ссылка строится сама, без CHANNEL_URL"
    with mock.patch.dict(os.environ, {**env, "CHANNEL_ID": "-1001234567890"}):
        assert load_config().channel_url == "", "для числового ID ссылку без сети не узнать"
    with mock.patch.dict(os.environ, {**env, "CHANNEL_ID": "@Iriska_Channel",
                                      "CHANNEL_URL": "https://t.me/+secret"}):
        assert load_config().channel_url == "https://t.me/+secret", \
            "явный CHANNEL_URL важнее ссылки из @username"
    with mock.patch.dict(os.environ, {**env, "CHANNEL_ID": "https://t.me/+AbCdEf"}):
        try:
            load_config()
        except RuntimeError as e:
            assert "CHANNEL_ID" in str(e)
        else:
            raise AssertionError("инвайт-ссылка в CHANNEL_ID должна давать понятную ошибку")

    # --- Фейковый Telegram ---
    group, channel, url, bot_id = -1001111111111, "@iriska_channel", "https://t.me/iriska_channel", 999

    def user_obj(uid: int) -> User:
        return User(id=uid, is_bot=False, first_name=f"Юзер{uid}", username=f"user{uid}")

    def member_obj(kind: str, uid: int):
        # model_construct: в разных версиях aiogram набор обязательных полей разный
        u = user_obj(uid)
        if kind == "member":
            return ChatMemberMember.model_construct(user=u)
        if kind == "left":
            return ChatMemberLeft.model_construct(user=u)
        if kind == "kicked":
            return ChatMemberBanned.model_construct(user=u)
        if kind == "creator":
            return ChatMemberOwner.model_construct(user=u)
        if kind == "admin":
            return ChatMemberAdministrator.model_construct(user=u)
        if kind == "restricted_in":
            return ChatMemberRestricted.model_construct(user=u, is_member=True)
        if kind == "restricted_out":
            return ChatMemberRestricted.model_construct(user=u, is_member=False)
        raise ValueError(kind)

    def bad_request(text: str) -> TelegramBadRequest:
        return TelegramBadRequest(
            method=GetChatMember(chat_id=channel, user_id=1), message=text
        )

    class FakeSession(BaseSession):
        def __init__(self) -> None:
            super().__init__()
            self.sent: list[SendMessage] = []
            self.member_calls: list[GetChatMember] = []
            self.edits: list[EditMessageText] = []
            self.answers: list[AnswerCallbackQuery] = []
            self.scenario: dict = {}      # user_id -> тип участника | исключение
            self.chat_info: dict | Exception = {}
            self._mid = 0

        async def close(self) -> None:
            pass

        async def stream_content(self, *args, **kwargs):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            if isinstance(method, GetChatMember):
                self.member_calls.append(method)
                what = self.scenario.get(method.user_id, "left")
                if isinstance(what, Exception):
                    raise what
                return member_obj(what, method.user_id)
            if isinstance(method, SendMessage):
                self.sent.append(method)
                self._mid += 1
                return Message(
                    message_id=self._mid, date=datetime.datetime.now(),
                    chat=Chat(id=method.chat_id, type="supergroup"), text=method.text,
                )
            if isinstance(method, EditMessageText):
                self.edits.append(method)
                self._mid += 1
                return Message(
                    message_id=method.message_id, date=datetime.datetime.now(),
                    chat=Chat(id=method.chat_id, type="supergroup"), text=method.text,
                )
            if isinstance(method, AnswerCallbackQuery):
                self.answers.append(method)
                return True
            if isinstance(method, GetMe):
                return User(id=bot_id, is_bot=True, first_name="Ириска", username="iriska_bot")
            if isinstance(method, GetChat):
                if isinstance(self.chat_info, Exception):
                    raise self.chat_info
                return ChatFullInfo.model_construct(
                    id=-1002222222222, type="channel", **self.chat_info
                )
            raise AssertionError(f"неожиданный запрос к Telegram: {method}")

    class LogCatcher(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[tuple[str, str]] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append((record.levelname, record.getMessage()))

    session = FakeSession()
    bot = Bot("123456:test", session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(db=db, config=base_cfg)
    for router in (admin_router, quiz_router, user_router, shop_router,
                   games_router, counting_router):
        dp.include_router(router)  # в том же порядке, что и в bot.py

    cfg_on = replace(base_cfg, channel_id=channel, channel_url=url,
                     bonus_min=2, bonus_max=2)
    cfg_off = replace(cfg_on, channel_id=None, channel_url="")
    counter = 1000

    async def say(uid: int, text: str, cfg=cfg_on) -> list[SendMessage]:
        """Участник пишет в группу; возвращает ответы бота."""
        nonlocal counter
        counter += 1
        before = len(session.sent)
        msg = Message(
            message_id=counter, date=datetime.datetime.now(),
            chat=Chat(id=group, type="supergroup", title="Чат"),
            from_user=user_obj(uid), text=text,
        )
        await dp.feed_update(bot, Update(update_id=counter, message=msg), config=cfg)
        return session.sent[before:]

    async def bonus_state(uid: int):
        row = await db.get_user(group, uid)
        return (row["balance"], row["last_bonus_day"]) if row else (0, None)

    # --- Не подписан: просьба подписаться со ссылкой, бонус не тратится ---
    session.scenario[101] = "left"
    replies = await say(101, "бонус")
    assert len(replies) == 1
    reply = replies[0]
    assert "только подписчикам канала" in reply.text, reply.text
    assert url in reply.text, "в тексте должна быть ссылка на канал"
    assert isinstance(reply.reply_markup, InlineKeyboardMarkup)
    assert reply.reply_markup.inline_keyboard[0][0].url == url, "нет кнопки со ссылкой"
    assert reply.link_preview_options and reply.link_preview_options.is_disabled
    assert len(session.member_calls) == 1
    assert session.member_calls[0].user_id == 101 and session.member_calls[0].chat_id == channel, \
        "проверка должна идти по ID пользователя в канал из CHANNEL_ID"
    assert await bonus_state(101) == (0, None), "неподписанному бонус не выдаётся"

    # Подписался -> пишет «бонус» снова в тот же день и получает его
    session.scenario[101] = "member"
    replies = await say(101, "бонус")
    assert "Ежедневный бонус: <b>+2</b>" in replies[0].text, replies[0].text
    bal, day = await bonus_state(101)
    assert bal == 2 and day is not None, "после подписки бонус дня не должен был сгореть"

    replies = await say(101, "бонус")
    assert "уже забирал бонус сегодня" in replies[0].text
    assert (await bonus_state(101))[0] == 2, "повторный бонус за день"

    # Все статусы участника канала
    expected = {
        "member": True, "admin": True, "creator": True, "restricted_in": True,
        "left": False, "kicked": False, "restricted_out": False,
    }
    for i, (kind, allowed) in enumerate(expected.items()):
        uid = 200 + i
        session.scenario[uid] = kind
        replies = await say(uid, "Бонус!")
        got = "Ежедневный бонус" in replies[0].text
        assert got == allowed, f"статус {kind}: бонус {'должен' if allowed else 'не должен'} выдаваться"
        assert (await bonus_state(uid))[0] == (2 if allowed else 0), kind
        if not allowed:
            assert "только подписчикам канала" in replies[0].text, kind

    # «user not found» от Telegram — человека нет в канале
    logging.disable(logging.CRITICAL)  # ожидаемые ошибки не засоряют вывод тестов
    session.scenario[300] = bad_request("Bad Request: user not found")
    replies = await say(300, "бонус")
    assert "только подписчикам канала" in replies[0].text
    assert await bonus_state(300) == (0, None)

    # Сбой проверки (неверный CHANNEL_ID, сеть, неожиданная ошибка): бонус НЕ
    # выдаётся, а человеку не врут «ты не подписан»
    failures = {
        301: bad_request("Bad Request: chat not found"),
        302: TelegramNetworkError(
            method=GetChatMember(chat_id=channel, user_id=1), message="timeout"),
        303: RuntimeError("неожиданный сбой"),
    }
    for uid, exc in failures.items():
        session.scenario[uid] = exc
        replies = await say(uid, "бонус")
        assert "Не получилось проверить подписку" in replies[0].text, type(exc).__name__
        assert "только подписчикам" not in replies[0].text
        assert await bonus_state(uid) == (0, None), \
            f"при сбое проверки ({type(exc).__name__}) бонус выдаваться не должен"
    logging.disable(logging.NOTSET)

    # Команда и длинная форма триггера защищены так же
    session.scenario[400] = "left"
    assert "только подписчикам канала" in (await say(400, "/bonus"))[0].text
    assert "только подписчикам канала" in (await say(400, "ежедневный бонус"))[0].text
    assert await bonus_state(400) == (0, None)

    # Много «бонус» подряд от подписчика: выдан ровно один (сеть перед записью
    # не должна открывать гонку)
    session.scenario[500] = "member"
    before = len(session.sent)
    await asyncio.gather(*(say(500, "бонус") for _ in range(5)))
    texts_sent = [m.text for m in session.sent[before:]]
    assert sum("Ежедневный бонус" in t for t in texts_sent) == 1, texts_sent
    assert sum("уже забирал" in t for t in texts_sent) == 4, texts_sent
    assert (await bonus_state(500))[0] == 2

    # Справка упоминает подписку (и только когда она включена)
    games_on = (await say(101, "/games"))[0].text
    assert "Только для подписчиков" in games_on and url in games_on, games_on
    assert "ежедневный бонус для подписчиков" in (await say(101, "/help"))[0].text

    # --- Канал не настроен: всё как раньше, Telegram о подписке не спрашиваем ---
    calls = len(session.member_calls)
    replies = await say(600, "бонус", cfg=cfg_off)
    assert "Ежедневный бонус: <b>+2</b>" in replies[0].text
    assert len(session.member_calls) == calls, "без CHANNEL_ID проверка подписки не нужна"
    assert "подписчиков" not in (await say(600, "/games", cfg=cfg_off))[0].text
    help_off = (await say(600, "/help", cfg=cfg_off))[0].text
    assert "/bonus — ежедневный бонус («бонус»)" in help_off

    # --- Самопроверка при старте ---
    catcher = LogCatcher()
    logging.getLogger("subscription").addHandler(catcher)
    cfg_nourl = replace(cfg_on, channel_url="")

    session.scenario[bot_id] = "admin"
    session.chat_info = {"title": "Ириска", "username": "iriska_channel"}
    assert (await setup_channel(bot, cfg_nourl)).channel_url == url, \
        "ссылка на публичный канал строится по username"
    keep = replace(cfg_on, channel_url="https://t.me/+secret")
    assert (await setup_channel(bot, keep)).channel_url == "https://t.me/+secret", \
        "заданный CHANNEL_URL перезаписываться не должен"
    session.chat_info = {"title": "Закрытый", "invite_link": "https://t.me/+AbCdEf"}
    assert (await setup_channel(bot, cfg_nourl)).channel_url == "https://t.me/+AbCdEf"

    catcher.records.clear()
    session.scenario[bot_id] = "member"
    await setup_channel(bot, cfg_on)
    assert any(lvl == "ERROR" and "не администратор" in m for lvl, m in catcher.records), \
        "если бот не админ канала — об этом должно быть громко сказано в логе"

    catcher.records.clear()
    session.chat_info = bad_request("Bad Request: chat not found")
    assert await setup_channel(bot, cfg_on) is cfg_on, "ошибка канала не должна ронять старт"
    assert any(lvl == "ERROR" and "Не могу получить канал" in m for lvl, m in catcher.records)

    catcher.records.clear()
    assert await setup_channel(bot, cfg_off) is cfg_off
    assert any(lvl == "WARNING" and "БЕЗ проверки" in m for lvl, m in catcher.records), \
        "без CHANNEL_ID бот предупреждает, что бонус открыт всем"
    logging.getLogger("subscription").removeHandler(catcher)

    # --- Магазин за ириски: кнопки, заказы, уведомления ---
    await _check_shop_handlers(
        db, bot=bot, dp=dp, session=session, group=group, user_obj=user_obj,
    )


# ---------------------------------------------------------------------------
# Магазин за ириски
# ---------------------------------------------------------------------------

BUYER = 333
SHOP_ADMIN = 4242


def _check_shop_catalog() -> None:
    """Каталог: цены из ТЗ, поиск по коду и алиасам, переопределение цен."""
    from shop import (
        DEFAULT_ITEMS, apply_overrides, catalog, find_item,
        parse_price_overrides,
    )

    items = catalog("")
    assert {i.code: i.price for i in items} == {
        "vpn": 300, "brak": 200, "aiphoto": 150, "compliment": 20,
        "nuds": 10_000, "psycho": 200, "pleasant": 10,
    }, [i.price for i in items]

    assert find_item(items, "VPN").code == "vpn"
    assert find_item(items, "  впн  ").code == "vpn"
    assert find_item(items, "брак").code == "brak"
    assert find_item(items, "NUDES").code == "nuds"
    assert find_item(items, "приятное").code == "pleasant"
    assert find_item(items, "/buy") is None
    assert find_item(items, "чего-то такого") is None
    assert find_item(items, None) is None

    assert parse_price_overrides("vpn=350, nuds:12000;мусор;x=") == {
        "vpn": 350, "nuds": 12000,
    }
    assert parse_price_overrides("") == {} and parse_price_overrides(None) == {}
    assert apply_overrides(DEFAULT_ITEMS, "") == DEFAULT_ITEMS

    over = apply_overrides(DEFAULT_ITEMS, "vpn=350, pleasant=0, compliment=-5")
    assert {i.code: i.price for i in over} == {
        "vpn": 350, "brak": 200, "aiphoto": 150, "nuds": 10000, "psycho": 200,
    }, "лоты с ценой <= 0 должны исчезать из магазина"


async def _check_shop_db(db: Database) -> None:
    """Покупки на уровне базы: списание, заказы, закрытие и возврат."""
    import sqlite3

    from shop import catalog

    items = {i.code: i for i in catalog("")}
    compliment = items["compliment"]

    def ledger_rows() -> list[tuple[int, str]]:
        conn = sqlite3.connect(db.path)
        try:
            return conn.execute(
                "SELECT amount, reason FROM ledger "
                "WHERE chat_id = ? AND user_id = ? ORDER BY id",
                (CHAT, BUYER),
            ).fetchall()
        finally:
            conn.close()

    # Незнакомого участника магазин не обслуживает
    status, balance, order_id = await db.create_order(
        CHAT, BUYER, "buyer", "Покупатель",
        compliment.code, compliment.title, compliment.price)
    assert (status, balance, order_id) == ("not_found", 0, None)

    # Пустой баланс: покупки нет, заказа нет, журнал пуст
    await db.ensure_user(CHAT, BUYER, "buyer", "Покупатель")
    status, balance, order_id = await db.create_order(
        CHAT, BUYER, "buyer", "Покупатель",
        compliment.code, compliment.title, compliment.price)
    assert (status, balance, order_id) == ("insufficient", 0, None)
    assert await db.list_orders(CHAT) == []
    assert ledger_rows() == []

    await db.adjust_balance(CHAT, BUYER, 100, "тест", None)
    status, balance, order_id = await db.create_order(
        CHAT, BUYER, "buyer", "Покупатель",
        compliment.code, compliment.title, compliment.price)
    assert (status, balance) == ("ok", 80) and order_id is not None
    orders = await db.list_orders(CHAT)
    assert len(orders) == 1
    assert orders[0]["status"] == "new" and orders[0]["item_code"] == "compliment"
    assert orders[0]["price"] == 20 and orders[0]["buyer_name"] == "Покупатель"
    assert orders[0]["buyer_username"] == "buyer"
    assert ledger_rows()[-1] == (-20, "магазин: Комплимент"), ledger_rows()

    # Закрытие заказа идемпотентно, закрытый не возвращают
    assert await db.complete_order(order_id, CHAT, SHOP_ADMIN) is True
    assert await db.complete_order(order_id, CHAT, SHOP_ADMIN) is False
    assert (await db.list_orders(CHAT)) == []
    assert [o["id"] for o in await db.list_orders(CHAT, "done")] == [order_id]
    assert await db.refund_order(order_id, CHAT, SHOP_ADMIN) == ("not_found", 0)

    # Возврат за новый заказ: ириски возвращаются, заказ получает статус refunded
    status, balance, order2 = await db.create_order(
        CHAT, BUYER, "buyer", "Покупатель",
        items["pleasant"].code, items["pleasant"].title, items["pleasant"].price)
    assert (status, balance) == ("ok", 70)
    assert await db.refund_order(order2, CHAT, SHOP_ADMIN) == ("ok", 80)
    assert await db.refund_order(order2, CHAT, SHOP_ADMIN) == ("not_found", 0)
    row = await db.get_user(CHAT, BUYER)
    assert row["balance"] == 80 and row["earned_total"] == 100, \
        "покупки и возвраты не должны менять «всего заработано»"
    assert ledger_rows()[-1] == (10, f"магазин: возврат за заказ №{order2}")
    assert [o["id"] for o in await db.list_orders(CHAT, "refunded")] == [order2]
    all_orders = await db.list_orders(CHAT, "")
    assert len(all_orders) == 2, "пустой статус = все заказы"
    assert len(await db.list_orders(CHAT, "", limit=1)) == 1


async def run_shop() -> None:
    """Магазин: каталог и покупки на уровне базы.

    Кнопки, заказы и уведомления проверяются в _check_shop_handlers — там
    нужен диспетчер с роутерами, который собирает _check_subscription.
    """
    _check_shop_catalog()
    tmp = tempfile.mkdtemp()
    db = Database(os.path.join(tmp, "shop.db"))
    await db.connect()
    try:
        await _check_shop_db(db)
    finally:
        await db.close()


async def _check_shop_handlers(
    db: Database, *, bot, dp, session, group: int, user_obj,
) -> None:
    """Кнопки магазина: каталог, подтверждение, списание, заказы, уведомления.

    Работает на диспетчере и фейковой сессии из _check_subscription: у aiogram
    роутер нельзя подключить к двум диспетчерам, а проверить хочется настоящую
    связку роутеров, а не вызовы функций напрямую.
    """
    import datetime
    from dataclasses import replace
    from unittest import mock

    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from config import load_config
    from handlers import shop as shop_handlers
    from shop import catalog, find_item

    # --- Настройки магазина ---
    env = {"BOT_TOKEN": "123456:test", "ADMIN_IDS": str(SHOP_ADMIN),
           "SHOP_CONTACT": ""}
    with mock.patch.dict(os.environ, env):
        cfg = load_config()
    assert cfg.shop_enabled is True
    assert cfg.shop_contact == "@PabloSvytoy", "пустой SHOP_CONTACT -> ADMIN_CONTACT"
    assert cfg.shop_notify_admins is True
    with mock.patch.dict(os.environ, {**env, "SHOP_CONTACT": "@Curator"}):
        assert load_config().shop_contact == "@Curator"
    with mock.patch.dict(os.environ, {**env, "SHOP_ENABLED": "0"}):
        assert load_config().shop_enabled is False
    with mock.patch.dict(os.environ, {**env, "SHOP_PRICES": "aiphoto=160, pleasant=0"}):
        items = catalog(load_config().shop_prices)
    assert find_item(items, "aiphoto").price == 160
    assert find_item(items, "pleasant") is None, "цена 0 должна убирать товар"

    bot_user = User(id=999, is_bot=True, first_name="Ириска", username="iriska_bot")
    counter = 9000

    async def say(uid: int, text: str, config=cfg):
        nonlocal counter
        counter += 1
        before = len(session.sent)
        msg = Message(
            message_id=counter, date=datetime.datetime.now(),
            chat=Chat(id=group, type="supergroup", title="Чат"),
            from_user=user_obj(uid), text=text,
        )
        await dp.feed_update(
            bot, Update(update_id=counter, message=msg), config=config
        )
        return session.sent[before:]

    async def press(uid: int, data: str, config=cfg):
        nonlocal counter
        counter += 1
        before_edits, before_answers = len(session.edits), len(session.answers)
        msg = Message(
            message_id=700, date=datetime.datetime.now(),
            chat=Chat(id=group, type="supergroup", title="Чат"),
            from_user=bot_user,
        )
        query = CallbackQuery(
            id=f"cb{counter}", from_user=user_obj(uid), chat_instance="chat-ci",
            message=msg, data=data,
        )
        await dp.feed_update(
            bot, Update(update_id=counter, callback_query=query), config=config
        )
        return session.edits[before_edits:], session.answers[before_answers:]

    # Баланс покупателя: 400 ирисок
    await db.ensure_user(group, BUYER, "buyer", "Покупатель")
    await db.adjust_balance(group, BUYER, 400, "тест", None)
    shop_handlers._last_purchase.clear()

    # Каталог: все семь лотов, кнопка на каждый и подсказка про админа
    replies = await say(BUYER, "магазин")
    assert len(replies) == 1 and replies[0].reply_markup is not None, [r.text for r in replies]
    catalog_reply = replies[0]
    assert "Магазин ирисок" in catalog_reply.text, catalog_reply.text
    assert "VPN на месяц" in catalog_reply.text and "10 000" in catalog_reply.text
    assert "@PabloSvytoy" in catalog_reply.text
    rows = catalog_reply.reply_markup.inline_keyboard
    assert len(rows) == 7 and rows[0][0].callback_data == "shop:item:vpn"

    # Нюдсы не по карману: списания нет, кнопки «Купить» нет
    edits, _ = await press(BUYER, "shop:item:nuds")
    assert "Не хватает" in edits[-1].text and "9 600" in edits[-1].text
    assert len(edits[-1].reply_markup.inline_keyboard) == 1, "нет кнопки покупки"
    assert (await db.get_user(group, BUYER))["balance"] == 400, "списали лишнее"

    # Покупка ИИ-фотосессии: списание, заказ, чек и уведомление админу в личку
    with mock.patch.object(shop_handlers, "PURCHASE_COOLDOWN", 0.0):
        edits, _ = await press(BUYER, "shop:item:aiphoto")
        assert "останется" in edits[-1].text and "250" in edits[-1].text
        edits, answers = await press(BUYER, "shop:buy:aiphoto")
        receipt = edits[-1].text
        assert "Покупка оформлена" in receipt and "150" in receipt
        assert "Админ свяжется с вами: @PabloSvytoy" in receipt
        assert "Юзер333" in receipt

        # Повторное «Купить» сразу защищено паузой и не списывает второй раз
        with mock.patch.object(shop_handlers, "PURCHASE_COOLDOWN", 60.0):
            _, answers = await press(BUYER, "shop:buy:compliment")
        assert "Уже оформляю" in (answers[0].text or ""), answers[0].text

    assert (await db.get_user(group, BUYER))["balance"] == 250, "двойное списание"
    orders = await db.list_orders(group)
    assert len(orders) == 1 and orders[0]["price"] == 150
    order_id = orders[0]["id"]
    dms = [m for m in session.sent if m.chat_id == SHOP_ADMIN]
    assert len(dms) == 1, "админу должно уйти уведомление о заказе"
    assert "Новый заказ" in dms[0].text and "ИИ-фотосессия" in dms[0].text
    assert "Юзер333" in dms[0].text and "user333" in dms[0].text

    # /buy: код, русское название и незнакомый товар
    replies = await say(BUYER, "/buy vpn")
    assert "Не хватает" in replies[-1].text and "50" in replies[-1].text
    replies = await say(BUYER, "/buy впн")
    assert "Подтверди покупку" in replies[-1].text
    replies = await say(BUYER, "/buy ерунда")
    assert "Не знаю такого товара" in replies[-1].text

    # Админ видит новые заказы чата и закрывает их
    replies = await say(SHOP_ADMIN, "/orders")
    assert f"№{order_id}" in replies[-1].text and "ИИ-фотосессия" in replies[-1].text
    assert "/order_done" in replies[-1].text
    replies = await say(SHOP_ADMIN, f"/order_done {order_id}")
    assert "отмечен выполненным" in replies[-1].text
    replies = await say(SHOP_ADMIN, f"/order_done {order_id}")
    assert "не найден среди новых" in replies[-1].text
    replies = await say(SHOP_ADMIN, "/orders")
    assert "Новых заказов нет" in replies[-1].text
    replies = await say(SHOP_ADMIN, "/orders all")
    assert f"№{order_id}" in replies[-1].text and "выполнен" in replies[-1].text

    # Возврат за невыполненный заказ: ириски возвращаются покупателю
    with mock.patch.object(shop_handlers, "PURCHASE_COOLDOWN", 0.0):
        await press(BUYER, "shop:buy:compliment")
    assert (await db.get_user(group, BUYER))["balance"] == 230
    refund_id = (await db.list_orders(group))[0]["id"]
    replies = await say(SHOP_ADMIN, f"/order_refund {refund_id}")
    assert "вернулись" in replies[-1].text and "250" in replies[-1].text
    assert (await db.get_user(group, BUYER))["balance"] == 250
    replies = await say(SHOP_ADMIN, f"/order_refund {refund_id}")
    assert "не найден среди новых" in replies[-1].text
    replies = await say(SHOP_ADMIN, "/order_done")
    assert "Укажи номер заказа" in replies[-1].text

    # Обычному участнику админские команды магазина недоступны
    before = len(session.sent)
    await say(BUYER, "/orders")
    assert len(session.sent) == before, "обычный участник увидел /orders"

    # Выключенный магазин отвечает отказом, кнопки не работают
    cfg_off = replace(cfg, shop_enabled=False)
    replies = await say(BUYER, "магазин", config=cfg_off)
    assert "закрыт" in replies[-1].text
    _, answers = await press(BUYER, "shop:item:vpn", config=cfg_off)
    assert answers and "закрыт" in (answers[-1].text or "")


if __name__ == "__main__":
    asyncio.run(run())
