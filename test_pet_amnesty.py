"""Разовые проверки амнистии питомцев.

Запуск после установки зависимостей: python test_pet_amnesty.py
"""

import asyncio
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from db import Database
from pet_amnesty import AMNESTY_KEY, apply, backup_database, preview


async def test_amnesty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "iriski.db"))
        await db.connect()
        try:
            c1, c2 = -100111, -100222
            old_day = (datetime.now(ZoneInfo("Europe/Moscow"))
                       - timedelta(days=5)).strftime("%Y-%m-%d")
            now_ts = time.time()
            conn = db._require()
            entries = [
                # chat, a, b, status, streak, best, lost, last_fed
                (c1, 101, 202, "active", 0, 7, 7, old_day),
                (c1, 202, 303, "active", 4, 4, 0, old_day),
                (c1, 303, 404, "active", 0, 12, 0, old_day),
                (c1, 404, 505, "abandoned", 10, 10, 0, old_day),
                (c1, 505, 606, "active", 0, 0, 0, None),  # яйцо
                (c2, 101, 303, "active", 2, 2, 0, old_day),
            ]
            for chat, a, b, status, streak, best, lost, fed in entries:
                await conn.execute(
                    """INSERT INTO pets
                       (chat_id, user_a, user_b, invited_by, status, species,
                        streak, best_streak, lost_streak, last_fed_day,
                        created_ts, food, joy, energy, care_ts)
                       VALUES (?, ?, ?, ?, ?, 'cat', ?, ?, ?, ?, ?, 10, 10, 10, ?)""",
                    (chat, a, b, a, status, streak, best, lost, fed,
                     now_ts - 100000, now_ts - 100000),
                )
            await conn.commit()
            before = await preview(db)
            assert before == {
                "applied": False,
                "marker": None,
                "pets": 5,
                "away": 2,
                "fallback": 1,
                "owners": 8,
                "total_bonus": 800,
            }, before

            backup_path = await backup_database(db)
            assert os.path.isfile(backup_path)
            with sqlite3.connect(backup_path) as old:
                assert old.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 6
                assert old.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0

            cfg = SimpleNamespace(tz=ZoneInfo("Europe/Moscow"))
            result = await apply(db, cfg, admin_id=999)
            assert result == {
                "status": "ok", "pets": 5, "restored": 2,
                "protected": 2, "fallback": 1,
                "owners": 8, "total_bonus": 800,
            }, result
            rows = await conn.execute(
                "SELECT user_a, user_b, status, streak, lost_streak, "
                "last_fed_day, food FROM pets WHERE chat_id = ? ORDER BY id",
                (c1,),
            )
            pets = list(await rows.fetchall())
            yesterday = (datetime.now(cfg.tz) - timedelta(days=1)).strftime("%Y-%m-%d")
            assert pets[0]["streak"] == 7 and pets[0]["lost_streak"] == 0
            assert pets[0]["last_fed_day"] == yesterday and pets[0]["food"] >= 80
            assert pets[1]["streak"] == 4 and pets[1]["last_fed_day"] == yesterday
            assert pets[2]["streak"] == 12 and pets[2]["last_fed_day"] == yesterday
            assert pets[3]["status"] == "abandoned" and pets[3]["streak"] == 10
            assert pets[4]["streak"] == 0, "яйцо остаётся яйцом"
            assert await db.sweep_lost_pets(yesterday, time.time()) == []

            for chat_id, uid in [(c1, 101), (c1, 202), (c1, 303),
                                 (c1, 404), (c1, 505), (c1, 606),
                                 (c2, 101), (c2, 303)]:
                assert (await db.get_user(chat_id, uid))["balance"] == 100
            assert (await db.get_user(c1, 505))["earned_total"] == 100
            cur = await conn.execute(
                "SELECT COUNT(*) AS n, SUM(amount) AS s FROM ledger "
                "WHERE reason LIKE 'компенсация%'"
            )
            row = await cur.fetchone()
            assert (row["n"], row["s"]) == (8, 800)
            assert (await preview(db))["applied"] is True
            assert (await apply(db, cfg, admin_id=999)) == {"status": "already"}
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM bot_meta WHERE key = ?", (AMNESTY_KEY,)
            )
            assert (await cur.fetchone())["n"] == 1
            cur = await conn.execute("SELECT COUNT(*) AS n FROM ledger")
            assert (await cur.fetchone())["n"] == 8, "повторный бонус запрещён"
        finally:
            await db.close()


if __name__ == "__main__":
    asyncio.run(test_amnesty())
    print("OK: pet outage amnesty")
