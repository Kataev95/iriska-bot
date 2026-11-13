"""PETS WORLD 1.5 — real SQLite regression tests for business economy."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_business import BUSINESSES, DAILY_PRODUCTION_STARTS, DAILY_SALE_COIN_LIMIT

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 10, tzinfo=TZ).timestamp()
CHAT = -1009701


class BusinessGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "save.sqlite3")
        self.db = Database(self.path)
        await self.db.connect()
        for uid in (101, 102, 201):
            await self.db.ensure_user(CHAT, uid, None, f"Player{uid}")
        await self.db._require().execute(
            "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
            "streak,best_streak,created_ts,care_ts,food,joy,energy) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (CHAT,101,102,101,"active","dragon",2,2,NOW,NOW,80,80,80),
        )
        await self.db._require().commit()
        self.pet_id = 1

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def seed(self, **amounts):
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?) "
            "ON CONFLICT(pet_id,material) DO UPDATE SET quantity=excluded.quantity",
            [(self.pet_id,code,num) for code,num in amounts.items()],
        )
        await self.db._require().commit()

    async def build(self, code, level=0, by=101, now=NOW):
        return await self.db.build_business(self.pet_id,by,code,now,level)

    async def materials(self):
        return (await self.db.inventory_state(self.pet_id))[0]

    async def day_state(self, day="2026-11-13"):
        return await self.db.business_state(self.pet_id,day)

    async def test_initial_state_migration_and_existing_pet_unchanged(self):
        p=await self.db.get_pet(self.pet_id)
        self.assertEqual(p["xp"],0)
        self.assertEqual(p["house"],0)
        self.assertEqual(p["best_streak"],2)
        state=await self.day_state()
        self.assertEqual(state["starts_remaining"],4)
        self.assertEqual(state["sales_coins_remaining"],12)
        self.assertEqual(state["units"]["florist"]["level"],0)
        self.assertIsNone(state["units"]["carpenter"]["job"])
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        self.assertEqual((await self.day_state())["starts_remaining"],4)
        self.assertEqual((await self.db.get_pet(self.pet_id))["best_streak"],2)

    async def test_build_consumes_exact_materials_and_stale_click_is_safe(self):
        await self.seed(leaf=40,wood=30,stone=20,crystal=3)
        results=await asyncio.gather(
            self.build("florist",0,101),
            self.build("florist",0,102),
        )
        self.assertEqual(sum(r.startswith("✅") for r in results),1,results)
        self.assertTrue(any("изменился" in r for r in results))
        self.assertEqual((await self.day_state())["units"]["florist"]["level"],1)
        self.assertEqual((await self.materials())["leaf"],34)
        self.assertEqual((await self.materials())["wood"],26)
        self.assertTrue((await self.build("florist",1,102)).startswith("✅"))
        self.assertEqual((await self.day_state())["units"]["florist"]["level"],2)
        self.assertEqual((await self.materials())["leaf"],24)
        self.assertEqual((await self.materials())["wood"],20)
        self.assertIn("изменился",await self.build("florist",1,101))
        history=await (
            await self.db._require().execute(
                "SELECT level FROM pet_business_upgrades WHERE pet_id=? ORDER BY level",
                (self.pet_id,),
            )
        ).fetchall()
        self.assertEqual([r["level"] for r in history],[1,2])
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],0)

    async def test_build_requires_all_materials_and_owner(self):
        await self.seed(wood=100,leaf=0)
        self.assertIn("Не хватает",await self.build("florist"))
        self.assertIn("недоступен",await self.build("florist",0,201))
        self.assertEqual((await self.materials())["wood"],100)
        self.assertEqual((await self.day_state())["units"]["florist"]["level"],0)

    async def test_start_collect_is_durable_and_claimed_only_once(self):
        await self.seed(wood=60,leaf=60,stone=12)
        self.assertTrue((await self.build("florist")).startswith("✅"))
        before=await self.materials()
        results=await asyncio.gather(
            self.db.start_business_job(self.pet_id,101,"florist",NOW,TZ),
            self.db.start_business_job(self.pet_id,102,"florist",NOW,TZ),
        )
        self.assertEqual(sum(x.startswith("✅") for x in results),1,results)
        self.assertIn("запущена",results[0]+results[1])
        self.assertEqual((await self.materials())["leaf"],before["leaf"]-3)
        self.assertEqual((await self.day_state())["starts_remaining"],3)
        job=(await self.day_state())["units"]["florist"]["job"]
        self.assertIsNotNone(job)
        self.assertEqual(job["quantity"],2)
        self.assertIn("ещё",await self.db.collect_business_job(
            self.pet_id,101,"florist",job["id"],NOW+10))
        collected=await asyncio.gather(
            self.db.collect_business_job(self.pet_id,101,"florist",job["id"],NOW+1000),
            self.db.collect_business_job(self.pet_id,102,"florist",job["id"],NOW+1000),
        )
        self.assertEqual(sum(r.startswith("✅") for r in collected),1,collected)
        self.assertEqual((await self.day_state())["units"]["florist"]["stock"],2)
        self.assertIsNone((await self.day_state())["units"]["florist"]["job"])
        self.assertIn("уже",await self.db.collect_business_job(
            self.pet_id,101,"florist",job["id"],NOW+1010))
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        self.assertEqual((await self.day_state())["units"]["florist"]["stock"],2)
        self.assertEqual((await self.day_state())["starts_remaining"],3)

    async def test_shared_daily_start_limit_across_businesses(self):
        await self.seed(wood=100,leaf=100,stone=30)
        await self.build("florist")
        await self.build("carpenter")
        for i in range(DAILY_PRODUCTION_STARTS):
            code="florist" if i%2==0 else "carpenter"
            at=NOW+i*3600
            self.assertTrue((await self.db.start_business_job(
                self.pet_id,101 if i%2==0 else 102,code,at,TZ)).startswith("✅"))
            job=(await self.day_state())["units"][code]["job"]
            self.assertTrue((await self.db.collect_business_job(
                self.pet_id,102,code,job["id"],at+3500)).startswith("✅"))
        self.assertEqual((await self.day_state())["starts_remaining"],0)
        self.assertIn("4 запуска",await self.db.start_business_job(
            self.pet_id,101,"florist",NOW+DAILY_PRODUCTION_STARTS*3600,TZ))
        self.assertEqual((await self.day_state("2026-11-14"))["starts_remaining"],4)
        self.assertTrue((await self.db.start_business_job(
            self.pet_id,102,"florist",NOW+86400,TZ)).startswith("✅"))

    async def test_sales_are_bounded_shared_and_credited_to_seller(self):
        await self.db._require().executemany(
            "INSERT INTO pet_business_goods(pet_id,business,quantity) VALUES(?,?,?)",
            [(self.pet_id,"florist",20),(self.pet_id,"carpenter",20)],
        )
        await self.db._require().commit()
        sold=await asyncio.gather(
            self.db.sell_business_goods(self.pet_id,101,"florist",NOW,TZ),
            self.db.sell_business_goods(self.pet_id,102,"carpenter",NOW,TZ),
        )
        self.assertEqual(sum(x.startswith("✅") for x in sold),1,sold)
        self.assertEqual((await self.day_state())["sales_coins_remaining"],0)
        total=(await self.db.get_user(CHAT,101))["balance"]+(
            await self.db.get_user(CHAT,102))["balance"]
        self.assertEqual(total,DAILY_SALE_COIN_LIMIT)
        self.assertIn("лимит", (await self.db.sell_business_goods(
            self.pet_id,101,"florist",NOW,TZ)).lower())
        ledger=await (
            await self.db._require().execute(
                "SELECT SUM(amount) FROM ledger "
                "WHERE chat_id=? AND reason LIKE 'питомец #1, бизнес%'",
                (CHAT,),
            )
        ).fetchone()
        self.assertEqual(ledger[0],DAILY_SALE_COIN_LIMIT)
        self.assertTrue((await self.db.sell_business_goods(
            self.pet_id,102,"florist",NOW+86400,TZ)).startswith("✅"))
        self.assertEqual((await self.day_state("2026-11-14"))["sales_coins_remaining"],0)

    async def test_sale_fail_mid_transaction_rolls_back_stock_and_balance(self):
        await self.db._require().execute(
            "INSERT INTO pet_business_goods(pet_id,business,quantity) VALUES(?,?,?)",
            (self.pet_id,"florist",5),
        )
        await self.db._require().execute(
            "CREATE TRIGGER fail_sale BEFORE INSERT ON pet_business_sales "
            "BEGIN SELECT RAISE(ABORT,'cannot record sale'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.sell_business_goods(self.pet_id,101,"florist",NOW,TZ)
        self.assertEqual((await self.day_state())["units"]["florist"]["stock"],5)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],0)
        self.assertEqual((await self.day_state())["sales_coins_remaining"],12)
        await self.db._require().execute("DROP TRIGGER fail_sale")
        await self.db._require().commit()
        self.assertTrue((await self.db.sell_business_goods(
            self.pet_id,101,"florist",NOW,TZ)).startswith("✅"))

    async def test_build_failure_rolls_back_materials(self):
        await self.seed(wood=4,leaf=6)
        await self.db._require().execute(
            "CREATE TRIGGER fail_build BEFORE INSERT ON pet_business_upgrades "
            "BEGIN SELECT RAISE(ABORT,'fail business'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.build("florist")
        self.assertEqual((await self.materials())["leaf"],6)
        self.assertEqual((await self.materials())["wood"],4)
        self.assertEqual((await self.day_state())["units"]["florist"]["level"],0)


class BusinessDefinitionsTests(unittest.TestCase):
    def test_prices_are_bounded_and_recipes_valid(self):
        from pet_inventory import MATERIALS
        self.assertEqual(DAILY_SALE_COIN_LIMIT,12)
        self.assertEqual(DAILY_PRODUCTION_STARTS,4)
        for code, business in BUSINESSES.items():
            self.assertEqual(len(business.upgrades),2)
            self.assertEqual(len(business.outputs),2)
            self.assertGreater(business.sell_price,0)
            self.assertLessEqual(business.sell_price,DAILY_SALE_COIN_LIMIT)
            for tier in (*business.upgrades,business.input_materials):
                self.assertTrue(tier)
                for resource, qty in tier:
                    self.assertIn(resource,MATERIALS)
                    self.assertGreater(qty,0)


if __name__=="__main__":
    unittest.main()
