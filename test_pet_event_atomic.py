"""Atomic random pet events + backup recovery safeguards."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backup_db import create_consistent_backup
from db import Database

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ).timestamp()


class AtomicEventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "play.db")
        self.db = Database(self.path)
        await self.db.connect()
        for uid in (1, 2):
            await self.db.ensure_user(-100, uid, None, str(uid))
            await self.db.adjust_balance(-100, uid, 100, "test", None)
        cur = await self.db._require().execute(
            "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
            "streak,best_streak,created_ts,care_ts,food,joy,energy) "
            "VALUES(-100,1,2,1,'active','cat',7,7,?, ?,80,50,100)",
            (NOW, NOW),
        )
        await self.db._require().commit()
        self.pet_id = cur.lastrowid

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def event(self, kind):
        return await self.db.create_pet_event(self.pet_id, -100, kind, NOW, 3600)

    async def test_double_coin_and_foreign_owner(self):
        event_id = await self.event("coin")
        result = await self.db.resolve_pet_event(event_id, 3, "take", NOW, TZ)
        self.assertEqual(result[0], "forbidden")
        results = await asyncio.gather(
            self.db.resolve_pet_event(event_id, 1, "take", NOW, TZ),
            self.db.resolve_pet_event(event_id, 2, "take", NOW, TZ),
        )
        self.assertEqual(sum(r[0] == "done" for r in results), 1)
        self.assertEqual((await self.db.get_user(-100, 1))["balance"]
                         + (await self.db.get_user(-100, 2))["balance"], 201)

    async def test_ledger_failure_rolls_back_event(self):
        event_id = await self.event("coin")
        await self.db._require().execute(
            "CREATE TRIGGER broken_ledger BEFORE INSERT ON ledger "
            "BEGIN SELECT RAISE(ABORT,'failed'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.resolve_pet_event(event_id, 1, "take", NOW, TZ)
        self.assertEqual((await self.db.get_pet_event(event_id))["status"], "open")
        self.assertEqual((await self.db.get_user(-100, 1))["balance"], 100)

    async def test_cure_insufficient_does_not_consume_event(self):
        event_id = await self.event("cold")
        await self.db.adjust_balance(-100, 1, -100, "test", None)
        result = await self.db.resolve_pet_event(event_id, 1, "cure", NOW, TZ)
        self.assertEqual(result[0], "insufficient")
        self.assertEqual((await self.db.get_pet_event(event_id))["status"], "open")

    async def test_butterfly_two_owners_once(self):
        event_id = await self.event("butterfly")
        self.assertEqual(
            (await self.db.resolve_pet_event(event_id, 1, "catch", NOW, TZ))[0],
            "wait",
        )
        self.assertEqual(
            (await self.db.resolve_pet_event(event_id, 2, "catch", NOW, TZ))[0],
            "done",
        )
        self.assertEqual(
            (await self.db.resolve_pet_event(event_id, 2, "catch", NOW, TZ))[0],
            "gone",
        )
        self.assertEqual((await self.db.get_user(-100, 1))["balance"], 101)
        self.assertEqual((await self.db.get_user(-100, 2))["balance"], 101)

    async def test_expired_cold_effect_only_once(self):
        event_id = await self.event("cold")
        await self.db.expire_pet_events(NOW + 3601, TZ)
        joy = (await self.db.get_pet(self.pet_id))["joy"]
        await self.db.expire_pet_events(NOW + 3601, TZ)
        self.assertEqual((await self.db.get_pet(self.pet_id))["joy"], joy)
        self.assertEqual((await self.db.get_pet_event(event_id))["status"], "expired")

    async def test_online_backup_has_wal_changes(self):
        backup = await create_consistent_backup(self.path, label="test")
        self.assertIsNotNone(backup)
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pets").fetchone()[0], 1)
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.assertTrue(list((Path(self.path).parent / "backups").glob("*.sqlite3")))


if __name__ == "__main__":
    unittest.main()
