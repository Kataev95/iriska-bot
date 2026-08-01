"""Локальные тесты логики начислений — без Telegram.

Запуск: python3 test_logic.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from db import Database

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

    # --- Ежедневный бонус ---
    status, bal = await db.claim_bonus(CHAT, USER1, "tester", "Тестер", "2026-08-01", 2)
    assert status == "ok" and bal == 2  # баланс был 0 после вывода
    status, bal2 = await db.claim_bonus(CHAT, USER1, "tester", "Тестер", "2026-08-01", 2)
    assert status == "already" and bal2 == 2, "бонус выдался дважды за день"
    status, bal3 = await db.claim_bonus(CHAT, USER1, "tester", "Тестер", "2026-08-02", 1)
    assert status == "ok" and bal3 == 3, "бонус не выдался на следующий день"

    # --- Дуэли ---
    # USER1: 3, USER2: 0 -> выравниваем балансы
    await db.adjust_balance(CHAT, USER1, 47, "тест", None)   # 50
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
    status, bal = await old_db.claim_bonus(CHAT, 777, "old", "Старый", "2026-08-01", 2)
    assert status == "ok" and bal == 44, "миграция/бонус на старой базе не сработали"
    row = await old_db.get_user(CHAT, 777)
    assert row["balance"] == 44
    await old_db.close()

    await db.close()
    print("✅ Все тесты пройдены")


if __name__ == "__main__":
    asyncio.run(run())
