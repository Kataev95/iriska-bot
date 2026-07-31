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

    await db.close()
    print("✅ Все тесты пройдены")


if __name__ == "__main__":
    asyncio.run(run())
