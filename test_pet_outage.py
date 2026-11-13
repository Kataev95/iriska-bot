"""Защита питомцев от простоя сервера.

Запуск: python test_pet_outage.py
Проверяет SQLite без Telegram API / реального бота.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db import Database


TZ = ZoneInfo("Europe/Moscow")
CHAT = -100654


def ts(day: int, hour: int = 12) -> float:
    return datetime(2026, 11, day, hour, 0, tzinfo=TZ).timestamp()


async def add_pet(
    db: Database, a: int, b: int, streak: int, last_day: str | None,
    *, lost: int = 0, freezes: int = 0,
) -> int:
    cur = await db._require().execute(
        """INSERT INTO pets (
           chat_id, user_a, user_b, invited_by, status, species,
           streak, best_streak, last_fed_day, lost_streak,
           freezes, created_ts
        ) VALUES (?, ?, ?, ?, 'active', 'cat', ?, ?, ?, ?, ?, ?)""",
        (CHAT, a, b, a, streak, max(streak, lost), last_day, lost, freezes, ts(1)),
    )
    await db._require().commit()
    return int(cur.lastrowid)


async def check_long_downtime_and_no_freeze_spend() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "bot.db"))
        await db.connect()
        try:
            a = await add_pet(db, 101, 102, 14, "2026-11-10", freezes=2)
            b = await add_pet(db, 201, 202, 6, "2026-11-09")
            stale = await add_pet(db, 301, 302, 9, "2026-11-08")
            lost = await add_pet(db, 401, 402, 0, "2026-11-08", lost=8)

            # Первая установка создаёт только отметку: задним числом не лечит.
            n, gap = await db.shield_pet_streaks_after_outage(ts(10), TZ, 6)
            assert (n, gap) == (0, 0.0)

            # После 72 часов простоя бот впервые снова проверяет питомцев.
            n, gap = await db.shield_pet_streaks_after_outage(ts(13), TZ, 6)
            assert n == 2 and 71.9 < gap < 72.1, (n, gap)
            pa = await db.get_pet(a)
            pb = await db.get_pet(b)
            ps = await db.get_pet(stale)
            pl = await db.get_pet(lost)
            assert pa["streak"] == 14 and pa["last_fed_day"] == "2026-11-12"
            assert pa["freezes"] == 2, "за простой не тратим заморозку"
            assert pb["streak"] == 6 and pb["last_fed_day"] == "2026-11-12"
            assert ps["last_fed_day"] == "2026-11-08", "старый пропуск не лечим"
            assert pl["streak"] == 0 and pl["lost_streak"] == 8

            frozen = await db.use_pet_freezes("2026-11-12")
            assert not frozen, "заморозка не должна расходоваться за простой"
            gone = await db.sweep_lost_pets("2026-11-12", ts(13))
            assert [int(p["id"]) for p in gone] == [stale]
            assert (await db.get_pet(a))["streak"] == 14

            # После возобновления работы оба написали друг другу -> серия +1.
            await db.record_pet_message(
                CHAT, 101, 102, "2026-11-13", "2026-11-12"
            )
            fed = await db.record_pet_message(
                CHAT, 102, 101, "2026-11-13", "2026-11-12"
            )
            assert fed["fed"] and fed["streak"] == 15

            # Частые проверки и перезапуск в тот же день не дублируют защиту.
            assert (await db.shield_pet_streaks_after_outage(
                ts(13) + 20, TZ, 6
            )) == (0, 0.0)
            await db.shield_pet_streaks_after_outage(ts(13) + 65, TZ, 6)
            assert (await db.get_pet(a))["streak"] == 15

            # При обычной работе хранится heartbeat; за реальные пропуски
            # после возвращения бота серия снова сгорает.
            for i in range(1, 49):
                await db.shield_pet_streaks_after_outage(
                    ts(13) + 65 + i * 3600, TZ, 6
                )
            gone = await db.sweep_lost_pets("2026-11-14", ts(15))
            assert a in [int(p["id"]) for p in gone]
            assert (await db.get_pet(a))["streak"] == 0
        finally:
            await db.close()


async def check_short_outage_and_disabled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "bot.db"))
        await db.connect()
        try:
            pid = await add_pet(db, 501, 502, 4, "2026-11-09")
            await db.shield_pet_streaks_after_outage(ts(10, 23), TZ, 6)
            n, _ = await db.shield_pet_streaks_after_outage(ts(11, 1), TZ, 6)
            assert n == 0, "короткий рестарт не должен защищать серию"
            assert (await db.get_pet(pid))["last_fed_day"] == "2026-11-09"

            # Отключённый режим только запоминает время, не даёт амнистию.
            n, _ = await db.shield_pet_streaks_after_outage(ts(13), TZ, 0)
            assert n == 0 and (await db.get_pet(pid))["streak"] == 4
            assert (await db.get_pet(pid))["last_fed_day"] == "2026-11-09"
            gone = await db.sweep_lost_pets("2026-11-12", ts(13))
            assert len(gone) == 1 and gone[0]["id"] == pid
        finally:
            await db.close()


async def check_restart_heartbeat_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bot.db")
        db = Database(path)
        await db.connect()
        pid = await add_pet(db, 601, 602, 3, "2026-11-10")
        await db.shield_pet_streaks_after_outage(ts(10), TZ, 6)
        await db.close()

        reopened = Database(path)
        await reopened.connect()
        try:
            n, _ = await reopened.shield_pet_streaks_after_outage(ts(12), TZ, 6)
            assert n == 1 and (await reopened.get_pet(pid))["streak"] == 3
            assert (await reopened.get_pet(pid))["last_fed_day"] == "2026-11-11"
        finally:
            await reopened.close()


async def main() -> None:
    await check_long_downtime_and_no_freeze_spend()
    await check_short_outage_and_disabled()
    await check_restart_heartbeat_persists()


if __name__ == "__main__":
    asyncio.run(main())
    print("OK: pet outage protection")
