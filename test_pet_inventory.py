"""PETS WORLD 1.3: durable shared loot and atomic crafting (real SQLite)."""

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_inventory import MATERIALS, RECIPES, loot_for

TZ = ZoneInfo("Europe/Moscow")
CHAT = -10090001


class InventoryGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "pets.db")
        self.db = Database(self.db_path)
        await self.db.connect()
        self.now = time.time()
        for owner in (101, 102, 201):
            await self.db.ensure_user(CHAT, owner, None, f"User{owner}")
        await self.db._require().execute(
            "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,streak,"
            "best_streak,created_ts,care_ts,food,joy,energy) "
            "VALUES(?,?,?,?,'active','dragon',2,2,?,?,95,90,99)",
            (CHAT, 101, 102, 101, self.now, self.now),
        )
        await self.db._require().commit()
        self.pet_id = 1

    async def asyncTearDown(self):
        await self.db.close()
        self.temp.cleanup()

    async def finish_trip(self, route, *, starter=101):
        result = await self.db.start_trip(
            self.pet_id, starter, route, self.now, TZ,
        )
        self.assertTrue(result.startswith("✅"), result)
        trip = await self.db.active_trip(self.pet_id)
        self.assertIsNotNone(trip)
        return trip

    async def test_park_loot_exactly_once_and_survives_restarts(self):
        trip = await self.finish_trip("park")
        outcomes = await asyncio.gather(
            self.db.claim_trip(self.pet_id, 101, trip["id"], self.now+901, TZ),
            self.db.claim_trip(self.pet_id, 102, trip["id"], self.now+901, TZ),
        )
        self.assertEqual(sum("🎒" in msg for msg in outcomes), 1, outcomes)
        materials, crafts = await self.db.inventory_state(self.pet_id)
        self.assertEqual(materials["leaf"], 2)
        self.assertEqual(materials["wood"], 1)
        self.assertEqual(materials["stone"], 0)
        self.assertEqual(materials["crystal"], 0)
        self.assertFalse(crafts)
        records = await (
            await self.db._require().execute(
                "SELECT material,quantity FROM pet_material_drops WHERE trip_id=?",
                (trip["id"],),
            )
        ).fetchall()
        self.assertEqual({r["material"]: r["quantity"] for r in records}, loot_for("park"))
        self.assertEqual((await self.db.get_pet(self.pet_id))["xp"], 35)
        self.assertEqual((await self.db.get_user(CHAT, 101))["balance"], 2)
        self.assertIn("уже получена",
                      await self.db.claim_trip(self.pet_id, 101, trip["id"],
                                               self.now+1200, TZ))
        await self.db.close()
        self.db = Database(self.db_path)
        await self.db.connect()
        self.assertEqual((await self.db.inventory_state(self.pet_id))[0]["leaf"], 2)
        self.assertEqual((await self.db.get_pet(self.pet_id))["xp"], 35)

    async def test_material_failure_rolls_back_coins_xp_and_trip(self):
        trip = await self.finish_trip("forest")
        await self.db._require().execute(
            "CREATE TRIGGER fail_loot BEFORE INSERT ON pet_material_drops "
            "BEGIN SELECT RAISE(ABORT,'cannot grant material'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.claim_trip(self.pet_id, 101, trip["id"], self.now+7300, TZ)
        self.assertIsNotNone(await self.db.active_trip(self.pet_id))
        self.assertEqual((await self.db.get_pet(self.pet_id))["xp"], 0)
        self.assertEqual((await self.db.get_user(CHAT, 101))["balance"], 0)
        self.assertEqual((await self.db.inventory_state(self.pet_id))[0]["wood"], 0)
        await self.db._require().execute("DROP TRIGGER fail_loot")
        await self.db._require().commit()
        self.assertIn("🎒", await self.db.claim_trip(
            self.pet_id, 101, trip["id"], self.now+7300, TZ
        ))

    async def test_crafting_refusal_race_access_and_persistence(self):
        self.assertIn("Не хватает материалов", await self.db.craft_trophy(
            self.pet_id, 101, "leaf_crown", self.now
        ))
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(self.pet_id,"leaf",4),(self.pet_id,"wood",2)],
        )
        await self.db._require().commit()
        self.assertIn("недоступен", (await self.db.craft_trophy(
            self.pet_id, 201, "leaf_crown", self.now
        )).lower())
        self.assertIn("Неизвестный", await self.db.craft_trophy(
            self.pet_id, 101, "invalid", self.now
        ))
        results = await asyncio.gather(
            self.db.craft_trophy(self.pet_id, 101, "leaf_crown", self.now),
            self.db.craft_trophy(self.pet_id, 102, "leaf_crown", self.now),
        )
        self.assertEqual(sum(r.startswith("✅") for r in results), 1, results)
        materials, crafts = await self.db.inventory_state(self.pet_id)
        self.assertEqual(materials["leaf"], 0)
        self.assertEqual(materials["wood"], 0)
        self.assertEqual(crafts, ["leaf_crown"])
        self.assertEqual((await self.db.get_pet(self.pet_id))["xp"], 0)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"], 0)
        await self.db.close()
        self.db = Database(self.db_path)
        await self.db.connect()
        self.assertEqual((await self.db.inventory_state(self.pet_id))[1],
                         ["leaf_crown"])
        self.assertIn("уже изготовлена", await self.db.craft_trophy(
            self.pet_id, 101, "leaf_crown", self.now+1
        ))

    async def test_insufficient_second_material_never_spends_first(self):
        await self.db._require().execute(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            (self.pet_id, "wood", 99),
        )
        await self.db._require().commit()
        message = await self.db.craft_trophy(self.pet_id, 101, "camp_lantern", self.now)
        self.assertIn("Не хватает материалов", message)
        self.assertEqual((await self.db.inventory_state(self.pet_id))[0]["wood"], 99)

    async def test_old_game_without_inventory_tables_is_migrated(self):
        await self.db._require().executescript("""
            DROP TABLE pet_material_drops;
            DROP TABLE pet_materials;
            DROP TABLE pet_crafts;
        """)
        await self.db.close()
        self.db = Database(self.db_path)
        await self.db.connect()
        self.assertEqual((await self.db.get_pet(self.pet_id))["best_streak"], 2)
        self.assertEqual((await self.db.inventory_state(self.pet_id))[0]["leaf"], 0)
        self.assertEqual((await self.db.inventory_state(self.pet_id))[1], [])


class InventoryDefinitionsTests(unittest.TestCase):
    def test_materials_and_recipe_consistency(self):
        for route in ("park", "forest", "ruins"):
            loot = loot_for(route)
            self.assertTrue(loot)
            for code, amount in loot.items():
                self.assertIn(code, MATERIALS)
                self.assertGreater(amount, 0)
        for recipe in RECIPES.values():
            self.assertTrue(recipe.ingredients)
            for code, amount in recipe.ingredients:
                self.assertIn(code, MATERIALS)
                self.assertGreater(amount, 0)
        self.assertEqual(loot_for("unknown"), {})


if __name__ == "__main__":
    unittest.main()
