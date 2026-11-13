"""PETS WORLD 1.4: safe house expansion on real SQLite."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_home import ROOMS, extra_loot, room_effect, route_loot

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ).timestamp()
CHAT = -1005500


class HouseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "pets.db")
        self.db = Database(self.path)
        await self.db.connect()
        for uid in (101, 102, 201):
            await self.db.ensure_user(CHAT, uid, None, f"User{uid}")
        await self.db.adjust_balance(CHAT, 101, 100, "test", None)
        await self.db._require().execute(
            "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
            "streak,best_streak,created_ts,care_ts,food,joy,energy) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (CHAT,101,102,101,"active","dragon",2,2,NOW,NOW,80,20,25),
        )
        await self.db._require().commit()
        self.pid = 1

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def seed(self, **stocks):
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?) "
            "ON CONFLICT(pet_id,material) DO UPDATE SET quantity=excluded.quantity",
            [(self.pid,code,qty) for code,qty in stocks.items()],
        )
        await self.db._require().commit()

    async def buy_base(self):
        result = await self.db.game_buy(self.pid,101,"house",NOW,TZ)
        self.assertTrue(result.startswith("✅"), result)
        self.assertEqual((await self.db.get_pet(self.pid))["house"], 1)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"], 75)

    async def test_original_house_required_and_old_shop_price_preserved(self):
        await self.seed(wood=8, leaf=6, stone=3)
        r = await self.db.build_room(self.pid,101,"bedroom",NOW,0)
        self.assertIn("Сначала купите домик",r)
        self.assertEqual((await self.db.inventory_state(self.pid))[0]["wood"],8)
        await self.buy_base()
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0],
                         {"bedroom":0,"garden":0,"workshop":0})
        self.assertTrue((await self.db.build_room(
            self.pid,101,"bedroom",NOW,0)).startswith("✅"))
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0]["bedroom"],1)
        mat, _ = await self.db.inventory_state(self.pid)
        self.assertEqual((mat["wood"],mat["leaf"]),(4,4))
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],75)

    async def test_shared_daily_room_use_race_and_persistent_reset(self):
        await self.buy_base()
        await self.seed(wood=4,leaf=2)
        await self.db.build_room(self.pid,101,"bedroom",NOW,0)
        responses = await asyncio.gather(
            self.db.use_room(self.pid,101,"bedroom",NOW,TZ),
            self.db.use_room(self.pid,102,"bedroom",NOW,TZ),
        )
        self.assertEqual(sum(r.startswith("✅") for r in responses),1,responses)
        self.assertAlmostEqual((await self.db.get_pet(self.pid))["energy"],37)
        _,used=await self.db.home_state(self.pid,"2026-11-13")
        self.assertEqual(used,["bedroom"])
        self.assertIn("завтра",await self.db.use_room(
            self.pid,102,"bedroom",NOW+5,TZ))
        self.assertAlmostEqual((await self.db.get_pet(self.pid))["energy"],37)

        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.assertIn("bedroom",(await self.db.home_state(
            self.pid,"2026-11-13"))[1])
        self.assertTrue((await self.db.use_room(
            self.pid,102,"bedroom",NOW+86400,TZ)).startswith("✅"))
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-14"))[1],
                         ["bedroom"])

    async def test_room_stale_double_click_cannot_buy_next_tier(self):
        await self.buy_base()
        await self.seed(wood=20, leaf=10, stone=5)
        results = await asyncio.gather(
            self.db.build_room(self.pid,101,"bedroom",NOW,0),
            self.db.build_room(self.pid,102,"bedroom",NOW,0),
        )
        self.assertEqual(sum(r.startswith("✅") for r in results),1,results)
        self.assertTrue(any("изменился" in r for r in results))
        materials,_=await self.db.inventory_state(self.pid)
        self.assertEqual((materials["wood"],materials["leaf"],materials["stone"]),
                         (16,8,5))
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0]["bedroom"],1)
        upgraded=await self.db.build_room(self.pid,102,"bedroom",NOW+10,1)
        self.assertTrue(upgraded.startswith("✅"),upgraded)
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0]["bedroom"],2)
        materials,_=await self.db.inventory_state(self.pid)
        self.assertEqual((materials["wood"],materials["leaf"],materials["stone"]),
                         (8,4,3))
        self.assertIn("изменился",await self.db.build_room(
            self.pid,101,"bedroom",NOW+12,1))
        audit=await (
            await self.db._require().execute(
                "SELECT level,user_id FROM pet_house_upgrades WHERE pet_id=? ORDER BY level",
                (self.pid,),
            )
        ).fetchall()
        self.assertEqual([a["level"] for a in audit],[1,2])

    async def test_insufficient_materials_or_nonowner_never_spends(self):
        await self.buy_base()
        await self.seed(wood=999,leaf=0)
        self.assertIn("Не хватает",await self.db.build_room(
            self.pid,101,"garden",NOW,0))
        self.assertIn("недоступен",await self.db.build_room(
            self.pid,201,"bedroom",NOW,0))
        self.assertIn("недоступен",await self.db.use_room(
            self.pid,201,"garden",NOW,TZ))
        self.assertIn("Сначала постройте",await self.db.use_room(
            self.pid,101,"garden",NOW,TZ))
        self.assertEqual((await self.db.inventory_state(self.pid))[0]["wood"],999)
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0]["garden"],0)

    async def test_failure_mid_build_rolls_back_all_materials(self):
        await self.buy_base()
        await self.seed(wood=4,leaf=2)
        await self.db._require().execute(
            "CREATE TRIGGER fail_house BEFORE INSERT ON pet_house_upgrades "
            "BEGIN SELECT RAISE(ABORT,'oops'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.build_room(self.pid,101,"bedroom",NOW,0)
        materials,_=await self.db.inventory_state(self.pid)
        self.assertEqual((materials["wood"],materials["leaf"]),(4,2))
        self.assertEqual((await self.db.home_state(self.pid,"2026-11-13"))[0]["bedroom"],0)
        await self.db._require().execute("DROP TRIGGER fail_house")
        await self.db._require().commit()
        self.assertTrue((await self.db.build_room(
            self.pid,101,"bedroom",NOW,0)).startswith("✅"))

    async def test_workshop_bonus_is_deterministic_and_claimed_once(self):
        await self.buy_base()
        await self.seed(wood=25, stone=12, crystal=4)
        self.assertTrue((await self.db.build_room(
            self.pid,101,"workshop",NOW,0)).startswith("✅"))
        self.assertEqual(route_loot("forest",1),{"wood":3,"stone":1})
        self.assertEqual(route_loot("ruins",1),{"stone":2,"crystal":1})
        self.assertTrue((await self.db.start_trip(
            self.pid,101,"forest",NOW,TZ)).startswith("✅"))
        first=await self.db.active_trip(self.pid)
        result=await self.db.claim_trip(self.pid,102,first["id"],NOW+7300,TZ)
        self.assertIn("🎒",result)
        materials,_=await self.db.inventory_state(self.pid)
        self.assertEqual((materials["wood"],materials["stone"]),(22,10))
        self.assertIn("уже получена",await self.db.claim_trip(
            self.pid,101,first["id"],NOW+7400,TZ))
        self.assertEqual((await self.db.inventory_state(self.pid))[0]["wood"],22)
        self.assertTrue((await self.db.build_room(
            self.pid,102,"workshop",NOW+7400,1)).startswith("✅"))
        self.assertEqual(route_loot("ruins",2),{"stone":3,"crystal":1})
        # Refresh energy to ensure the second expedition is affordable.
        await self.db.game_refresh(self.pid,NOW+7401,TZ,energy=100,food=100)
        self.assertTrue((await self.db.start_trip(
            self.pid,101,"ruins",NOW+7402,TZ)).startswith("✅"))
        second=await self.db.active_trip(self.pid)
        self.assertIn("🎒",await self.db.claim_trip(
            self.pid,102,second["id"],NOW+36500,TZ))
        materials,_=await self.db.inventory_state(self.pid)
        self.assertEqual(materials["stone"],8)  # 10 - tier2(5) + ruins(3)
        self.assertEqual(materials["crystal"],3)  # 4 - tier2(2) + ruins(1)

    async def test_restart_migration_keeps_legacy_house(self):
        await self.buy_base()
        await self.db._require().executescript("""
            DROP TABLE pet_house_rooms;
            DROP TABLE pet_house_upgrades;
            DROP TABLE pet_room_uses;
        """)
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        self.assertTrue((await self.db.get_pet(self.pid))["house"])
        rooms,used=await self.db.home_state(self.pid,"2026-11-13")
        self.assertEqual(rooms,{"bedroom":0,"garden":0,"workshop":0})
        self.assertEqual(used,[])
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],75)


class HouseDefinitionsTests(unittest.TestCase):
    def test_costs_effects_and_deterministic_loot(self):
        from pet_inventory import MATERIALS
        self.assertEqual(len(ROOMS),3)
        for code,room in ROOMS.items():
            self.assertEqual(len(room.upgrades),2)
            self.assertEqual(len(room.effects),2)
            for tier in room.upgrades:
                self.assertTrue(tier)
                for resource,quantity in tier:
                    self.assertIn(resource,MATERIALS)
                    self.assertGreater(quantity,0)
            if code!="workshop":
                self.assertIsNotNone(room_effect(code,1))
        self.assertIsNone(room_effect("workshop",1))
        self.assertEqual(extra_loot("park",2),{})
        self.assertEqual(extra_loot("forest",2),{"wood":1})
        self.assertEqual(extra_loot("ruins",2),{"stone":1})
        self.assertEqual(route_loot("park",2),{"leaf":2,"wood":1})


if __name__ == "__main__":
    unittest.main()
