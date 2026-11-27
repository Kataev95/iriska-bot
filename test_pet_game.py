"""Питомцы 3.0: конкурентные операции, рестарт, миграция, права и экономика."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_game import growth_days, legacy_xp, level_for

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ).timestamp()


class GameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "game.db")
        self.db = Database(self.path)
        await self.db.connect()
        self.pid = await self.add_pet(-100, 1, 2)
        for uid in (1, 2):
            await self.db.ensure_user(-100, uid, None, str(uid))
            await self.db.adjust_balance(-100, uid, 100, "test", None)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def add_pet(self, chat, a, b, best=7):
        db = self.db._require()
        cur = await db.execute(
            "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,streak,best_streak,created_ts,care_ts,food,joy,energy) VALUES(?,?,?,?,'active','cat',?,?,?, ?,80,50,100)",
            (chat, a, b, a, best, best, NOW, NOW),
        )
        await db.commit()
        return cur.lastrowid

    async def test_extended_levels_and_passive_effects(self):
        from pet_game import MAX_LEVEL, xp_for_level, next_level_xp
        from pet_progression import effects, skill_points
        self.assertEqual(MAX_LEVEL,100)
        for level in range(1,21):
            self.assertEqual(xp_for_level(level),25*level*(level-1))
        for level in range(2,101):
            threshold=xp_for_level(level)
            self.assertEqual(level_for(threshold),level)
            self.assertEqual(level_for(threshold-1),level-1)
        self.assertEqual(level_for(legacy_xp(100)),20)
        self.assertEqual(level_for(9999999),100)
        self.assertIsNone(next_level_xp(xp_for_level(100)))
        self.assertEqual(skill_points(100),20)
        db=self.db._require()
        await db.execute("UPDATE pets SET xp=?,profession='explorer',food=40,energy=60 WHERE id=?",
                         (xp_for_level(35),self.pid))
        await db.commit()
        self.assertTrue((await self.db.learn_skill(self.pid,1,"trail",0)).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,2,"trail",1)).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,1,"wisdom",0)).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,2,"care",0)).startswith("✅"))
        self.assertTrue((await self.db.choose_specialization(self.pid,1,"wanderer")).startswith("✅"))
        self.assertTrue((await self.db.start_trip(self.pid,1,"park",NOW,TZ)).startswith("✅"))
        trip=await self.db.active_trip(self.pid)
        self.assertEqual(trip["xp"],35*128//100)
        self.assertEqual(trip["coins"],2)
        self.assertEqual((await self.db.get_pet(self.pid))["energy"],52)
        self.assertTrue((await self.db.game_care(self.pid,2,"feed",NOW,TZ)).startswith("✅"))
        self.assertEqual((await self.db.get_pet(self.pid))["food"],67)
        state=await self.db.progression_state(await self.db.get_pet(self.pid))
        self.assertEqual(state["points"],3)
        self.assertEqual(state["evolution"]["name"],"Герой долины")
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        self.assertEqual(await self.db.progression_state(await self.db.get_pet(self.pid)),state)
        self.assertEqual((await self.db.get_pet(self.pid))["profession"],"explorer")

    async def test_new_professions_real_effects_and_skill_budget(self):
        from pet_game import xp_for_level
        db=self.db._require()
        await db.execute("UPDATE pets SET xp=?,food=40 WHERE id=?",(xp_for_level(5),self.pid))
        await db.commit()
        self.assertTrue((await self.db.choose_profession(self.pid,1,"guardian")).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,1,"care",0)).startswith("✅"))
        self.assertFalse((await self.db.learn_skill(self.pid,2,"trail",0)).startswith("✅"))
        self.assertTrue((await self.db.game_care(self.pid,1,"feed",NOW,TZ)).startswith("✅"))
        self.assertEqual((await self.db.get_pet(self.pid))["food"],77)
        await db.execute("UPDATE pets SET profession='gardener',xp=? WHERE id=?",(xp_for_level(25),self.pid))
        await db.commit()
        self.assertTrue((await self.db.learn_skill(self.pid,1,"craft",0)).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,1,"craft",1)).startswith("✅"))
        self.assertTrue((await self.db.learn_skill(self.pid,1,"garden",0)).startswith("✅"))
        self.assertTrue((await self.db.farm_action(self.pid,1,"plant","wheat",1,0,NOW,TZ)).startswith("✅"))
        self.assertTrue((await self.db.farm_action(self.pid,2,"water","",1,1,NOW,TZ)).startswith("✅"))
        state=await self.db.countryside_state(self.pid,datetime.fromtimestamp(NOW,TZ).date().isoformat())
        self.assertEqual(state["plots"][0]["ready_at"],NOW+300*(1-.37))
        await db.execute("UPDATE pets SET profession='engineer' WHERE id=?",(self.pid,))
        await db.executemany("INSERT INTO pet_materials VALUES(?,?,?)",
                             [(self.pid,"wood",100),(self.pid,"leaf",100),(self.pid,"stone",100)])
        await db.commit()
        self.assertTrue((await self.db.build_business(self.pid,1,"florist",NOW,0)).startswith("✅"))
        self.assertTrue((await self.db.start_business_job(self.pid,1,"florist",NOW,TZ)).startswith("✅"))
        business=await self.db.business_state(self.pid,datetime.fromtimestamp(NOW,TZ).date().isoformat())
        self.assertEqual(business["units"]["florist"]["job"]["finish_at"],NOW+900*86//100)

    async def test_purchase_race_and_failure_rollback(self):
        results = await asyncio.gather(
            *(self.db.game_buy(self.pid, u, "house", NOW, TZ) for u in (1, 2))
        )
        self.assertEqual(sum(x.startswith("✅") for x in results), 1)
        balances = [(await self.db.get_user(-100, u))["balance"] for u in (1, 2)]
        self.assertEqual(sum(balances), 175)
        db = self.db._require()
        await db.execute(
            "CREATE TRIGGER fail_ledger BEFORE INSERT ON ledger BEGIN SELECT RAISE(ABORT,'fail'); END"
        )
        await db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.game_buy(self.pid, 1, "bow", NOW, TZ)
        self.assertNotIn("bow", (await self.db.get_pet(self.pid))["items"])
        self.assertEqual((await self.db.get_user(-100, 1))["balance"], balances[0])

    async def test_trip_race_restart_claim_and_stale_button(self):
        results = await asyncio.gather(
            *(self.db.start_trip(self.pid, u, "park", NOW, TZ) for u in (1, 2))
        )
        self.assertEqual(sum(x.startswith("✅") for x in results), 1)
        trip = await self.db.active_trip(self.pid)
        sender = trip["user_id"]
        self.assertIn(
            "не закончен", await self.db.claim_trip(self.pid, 1, trip["id"], NOW, TZ)
        )
        self.assertIn(
            "недоступен",
            await self.db.claim_trip(self.pid, 99, trip["id"], NOW + 900, TZ),
        )
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        results = await asyncio.gather(
            *(
                self.db.claim_trip(self.pid, u, trip["id"], NOW + 900, TZ)
                for u in (1, 2)
            )
        )
        self.assertEqual(sum("отправившему" in x for x in results), 1)
        self.assertEqual((await self.db.get_pet(self.pid))["xp"], 35)
        self.assertEqual((await self.db.get_user(-100, sender))["balance"], 102)
        self.assertEqual((await self.db.get_user(-100, 3 - sender))["balance"], 100)
        await self.db.start_trip(self.pid, 1, "park", NOW + 901, TZ)
        self.assertIn(
            "уже получена",
            await self.db.claim_trip(self.pid, 1, trip["id"], NOW + 2000, TZ),
        )
        self.assertIsNotNone(await self.db.active_trip(self.pid))

    async def test_claim_rollback_after_trip_status_write(self):
        await self.db.start_trip(self.pid, 1, "park", NOW, TZ)
        trip = await self.db.active_trip(self.pid)
        await self.db._require().execute(
            "CREATE TRIGGER fail_reward BEFORE INSERT ON ledger BEGIN SELECT RAISE(ABORT,'fail'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.claim_trip(self.pid, 2, trip["id"], NOW + 1000, TZ)
        self.assertIsNotNone(await self.db.active_trip(self.pid))
        self.assertEqual((await self.db.get_user(-100, 1))["balance"], 100)
        self.assertEqual((await self.db.get_pet(self.pid))["xp"], 0)

    async def test_daily_care_and_profession_race(self):
        results = await asyncio.gather(
            *(self.db.game_care(self.pid, 1, "play", NOW, TZ) for _ in range(2))
        )
        self.assertEqual(sum(x.startswith("✅") for x in results), 1)
        self.assertTrue(
            (await self.db.game_care(self.pid, 2, "play", NOW, TZ)).startswith("💕")
        )
        results = await asyncio.gather(
            *(self.db.claim_daily(self.pid, u, "2026-11-13") for u in (1, 2))
        )
        self.assertEqual(sum("+35 XP" in x for x in results), 1)
        self.assertEqual((await self.db.get_pet(self.pid))["xp"], 35)
        self.assertIn("пока нет", await self.db.claim_daily(self.pid, 1, "2026-11-14"))
        self.assertIn("5-м", await self.db.choose_profession(self.pid, 1, "explorer"))
        await self.db._require().execute(
            "UPDATE pets SET xp=500 WHERE id=?", (self.pid,)
        )
        await self.db._require().commit()
        results = await asyncio.gather(
            self.db.choose_profession(self.pid, 1, "explorer"),
            self.db.choose_profession(self.pid, 2, "merchant"),
        )
        self.assertEqual(sum(x.startswith("✅") for x in results), 1)
        await self.db.start_trip(self.pid, 1, "park", NOW, TZ)
        trip = await self.db.active_trip(self.pid)
        profession = (await self.db.get_pet(self.pid))["profession"]
        self.assertEqual(trip["xp"], 42 if profession == "explorer" else 35)
        self.assertEqual(trip["coins"], 3 if profession == "merchant" else 2)

    async def test_limits_shared_between_chats_and_days(self):
        ids = [self.pid] + [await self.add_pet(-101 - i, 1, 10 + i) for i in range(3)]
        for pid in ids[:3]:
            self.assertTrue(
                (await self.db.start_trip(pid, 1, "park", NOW, TZ)).startswith("✅")
            )
        self.assertIn(
            "уже начаты 3", await self.db.start_trip(ids[3], 1, "park", NOW, TZ)
        )
        await self.db.game_refresh(ids[3], NOW + 86400, TZ, food=100, energy=100)
        self.assertTrue(
            (await self.db.start_trip(ids[3], 1, "park", NOW + 86400, TZ)).startswith(
                "✅"
            )
        )
        self.assertIn(
            "неизвестный",
            (await self.db.start_trip(ids[3], 1, "invalid", NOW, TZ)).lower(),
        )
        self.assertIn(
            "недоступен", await self.db.game_buy(self.pid, 99, "bow", NOW, TZ)
        )

    async def test_legacy_migration_and_month_boundary(self):
        await self.db._require().execute(
            "UPDATE pets SET best_streak=100,streak=0,lost_streak=100,items='bow',house=1 WHERE id=?",
            (self.pid,),
        )
        await self.db._require().execute(
            "DELETE FROM bot_meta WHERE key='pets3_migrated'"
        )
        await self.db._require().execute("ALTER TABLE pets DROP COLUMN xp")
        await self.db._require().execute("ALTER TABLE pets DROP COLUMN profession")
        await self.db._require().commit()
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        pet = await self.db.get_pet(self.pid)
        self.assertEqual(pet["xp"], legacy_xp(100))
        self.assertEqual(level_for(pet["xp"]), 20)
        self.assertEqual(growth_days(pet), 100)
        self.assertEqual((pet["items"], pet["house"]), ("bow", 1))
        await self.db.ensure_stats_period("2026-11")
        await self.db.close_stats_period("2026-11", "2026-12", NOW)
        self.assertEqual((await self.db.get_user(-100, 1))["balance"], 100)
        self.assertEqual((await self.db.get_pet(self.pid))["xp"], 9500)
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.assertEqual((await self.db.get_pet(self.pid))["xp"], 9500)
        self.assertTrue(
            (await self.db.start_trip(self.pid, 1, "park", NOW, TZ)).startswith("✅")
        )


if __name__ == "__main__":
    unittest.main()
