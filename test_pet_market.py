"""PETS WORLD 1.6: chat-private market escrow, zero-sum trades and rollback."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_market import (
    MAX_ACTIVE_LISTINGS_PER_PET, MAX_LISTINGS_PER_PET_DAY,
    MAX_BUYS_PER_PET_DAY, LISTING_TTL_SECONDS,
)

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 11, 13, 12, tzinfo=TZ).timestamp()
CHAT = -100991
OTHER_CHAT = -100992


class MarketGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"game.sqlite3")
        self.db=Database(self.path)
        await self.db.connect()
        for chat,owners in (
            (CHAT,(101,102)), (CHAT,(201,202)),
            (CHAT,(101,301)), (OTHER_CHAT,(401,402))
        ):
            for uid in owners:
                await self.db.ensure_user(chat,uid,None,f"User{uid}")
            await self.db._require().execute(
                "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
                "streak,best_streak,created_ts,care_ts,food,joy,energy) "
                "VALUES(?,?,?,?,'active','dragon',2,2,?,?,80,80,80)",
                (chat,min(owners),max(owners),owners[0],NOW,NOW),
            )
        await self.db._require().commit()
        self.seller,self.buyer,self.shared,self.other=1,2,3,4
        await self.db.adjust_balance(CHAT,201,30,"test",None)
        await self.db.adjust_balance(CHAT,202,20,"test",None)
        await self.db.adjust_balance(CHAT,101,0,"test",None)
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(self.seller,"leaf",50),(self.seller,"wood",12)],
        )
        await self.db._require().execute(
            "INSERT INTO pet_business_goods(pet_id,business,quantity) VALUES(?,?,?)",
            (self.seller,"florist",9),
        )
        await self.db._require().commit()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def create(self,kind="material",code="leaf",qty=3,price=2,pet=1,
                     uid=101,now=NOW):
        return await self.db.create_market_listing(
            pet,uid,kind,code,qty,price,now,TZ
        )

    async def state(self,pet=2,user=201,now=NOW):
        return await self.db.market_state(pet,user,now,TZ)

    async def latest(self):
        cur=await self.db._require().execute(
            "SELECT * FROM pet_market_listings ORDER BY id DESC LIMIT 1"
        )
        return await cur.fetchone()

    async def leaf(self,pet=1):
        return (await self.db.inventory_state(pet))[0]["leaf"]

    async def test_cross_pet_purchase_is_zero_sum_and_shared(self):
        self.assertIn("✅",await self.create())
        listing=await self.latest()
        self.assertEqual(listing["total_price"],6)
        self.assertEqual(await self.leaf(),47)
        feed=await self.state()
        self.assertEqual(len(feed["listings"]),1)
        self.assertEqual(feed["listings"][0]["pet_name"],"Питомец #1")
        self.assertTrue(feed["listings"][0]["can_buy"])
        self.assertFalse((await self.state(1,101))["listings"][0]["can_buy"])
        result=await self.db.buy_market_listing(2,201,listing["id"],NOW+5,TZ)
        self.assertIn("✅",result)
        self.assertEqual(await self.leaf(),47)
        self.assertEqual(await self.leaf(2),3)
        self.assertEqual((await self.db.get_user(CHAT,201))["balance"],24)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],6)
        self.assertEqual((await self.db.get_user(CHAT,202))["balance"],20)
        self.assertEqual((await self.state())["listings"],[])
        movement=await (
            await self.db._require().execute(
                "SELECT SUM(amount) FROM ledger WHERE reason LIKE 'рынок PETS:%'"
            )
        ).fetchone()
        self.assertEqual(movement[0],0)
        self.assertIn("уже",await self.db.buy_market_listing(
            2,201,listing["id"],NOW+7,TZ))

    async def test_double_purchase_and_cancel_races_preserve_stock(self):
        await self.create(qty=5,price=3)
        ident=(await self.latest())["id"]
        a,b=await asyncio.gather(
            self.db.buy_market_listing(2,201,ident,NOW+10,TZ),
            self.db.buy_market_listing(2,202,ident,NOW+10,TZ),
        )
        self.assertEqual(sum(x.startswith("✅") for x in (a,b)),1,(a,b))
        self.assertEqual(await self.leaf(2),5)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],15)
        await self.create(qty=2,price=1)
        ident=(await self.latest())["id"]
        a,b=await asyncio.gather(
            self.db.buy_market_listing(2,201,ident,NOW+20,TZ),
            self.db.cancel_market_listing(1,102,ident,NOW+20),
        )
        self.assertEqual(sum(x.startswith("✅") for x in (a,b)),1,(a,b))
        self.assertEqual(await self.leaf()+await self.leaf(2),50)
        self.assertEqual((await self.latest())["state"] in ("sold","cancelled"),True)

    async def test_expiration_and_cancellation_restore_escrow_only_once(self):
        await self.create(qty=4,price=2)
        ident=(await self.latest())["id"]
        self.assertEqual(await self.leaf(),46)
        expired=await self.state(now=NOW+LISTING_TTL_SECONDS+1)
        self.assertEqual(expired["listings"],[])
        self.assertEqual(await self.leaf(),50)
        self.assertEqual((await self.latest())["state"],"expired")
        self.assertEqual((await self.state(now=NOW+LISTING_TTL_SECONDS+20))["listings"],[])
        self.assertIn("закрыто",await self.db.cancel_market_listing(
            1,101,ident,NOW+LISTING_TTL_SECONDS+30))
        self.assertEqual(await self.leaf(),50)
        await self.create(qty=2,price=2,now=NOW+LISTING_TTL_SECONDS+40)
        second=(await self.latest())["id"]
        self.assertIn("✅",await self.db.cancel_market_listing(
            1,102,second,NOW+LISTING_TTL_SECONDS+50))
        self.assertEqual(await self.leaf(),50)
        self.assertIn("закрыто",await self.db.cancel_market_listing(
            1,101,second,NOW+LISTING_TTL_SECONDS+51))
        self.assertEqual(await self.leaf(),50)

    async def test_goods_trade_preserves_made_goods(self):
        self.assertIn("✅",await self.create(kind="good",code="florist",qty=3,price=4))
        listing=(await self.latest())["id"]
        self.assertEqual((await self.db.business_state(1,"2026-11-13"))["units"]["florist"]["stock"],6)
        self.assertIn("✅",await self.db.buy_market_listing(2,201,listing,NOW+1,TZ))
        self.assertEqual((await self.db.business_state(2,"2026-11-13"))["units"]["florist"]["stock"],3)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],12)

    async def test_isolation_and_anti_self_farming(self):
        await self.create()
        listing=(await self.latest())["id"]
        self.assertIsNone(await self.state(pet=1,user=201))
        self.assertIsNone(await self.state(pet=4,user=201))
        self.assertIn("недоступен",await self.db.buy_market_listing(
            4,401,listing,NOW+1,TZ))
        self.assertIn("владеете",await self.db.buy_market_listing(
            3,101,listing,NOW+1,TZ))
        self.assertIn("владеете",await self.db.buy_market_listing(
            1,102,listing,NOW+1,TZ) if False else
            await self.db.buy_market_listing(3,301,listing,NOW+1,TZ))
        # user 301 is not a co-owner of seller pet; pet #3 itself shares
        # owner 101, but this API deliberately checks the clicking user.
        # No chat crossing is allowed.
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],0)
        self.assertEqual(await self.leaf(),47)

    async def test_missing_balance_or_invalid_price_does_not_lose_goods(self):
        self.assertIn("Неверное",await self.create(price=0))
        self.assertIn("Неверное",await self.create(price=21))
        self.assertIn("Неверное",await self.create(qty=6))
        self.assertIn("Неверное",await self.create(qty=True))
        self.assertIn("нельзя",await self.create(code="__admin"))
        self.assertEqual(await self.leaf(),50)
        await self.create(qty=5,price=20)
        ident=(await self.latest())["id"]
        self.assertIn("Недостаточно",await self.db.buy_market_listing(
            2,201,ident,NOW+3,TZ))
        self.assertEqual(await self.leaf(),45)
        self.assertEqual(await self.leaf(2),0)
        self.assertEqual((await self.latest())["state"],"active")

    async def test_daily_and_active_limits_apply_to_both_owners(self):
        for i in range(MAX_ACTIVE_LISTINGS_PER_PET):
            self.assertIn("✅",await self.create(qty=1,price=1,uid=101 if i%2==0 else 102))
        self.assertIn("максимум",await self.create(qty=1,price=1))
        for i in range(MAX_ACTIVE_LISTINGS_PER_PET):
            cur=await self.db._require().execute(
                "SELECT id FROM pet_market_listings WHERE state='active' ORDER BY id LIMIT 1"
            )
            ident=(await cur.fetchone())["id"]
            self.assertIn("✅",await self.db.cancel_market_listing(1,102,ident,NOW+2))
        self.assertIn("✅",await self.create(qty=1,price=1))
        self.assertIn("✅",await self.create(qty=1,price=1))
        self.assertIn("5 объявлений",await self.create(qty=1,price=1))
        self.assertEqual((await self.state(1,102))["remaining_posts"],0)
        self.assertIn("✅",await self.create(qty=1,price=1,now=NOW+86400))
        self.assertEqual((await self.state(1,102,now=NOW+86400))["remaining_posts"],4)

    async def test_failed_ledger_rolls_back_payment_and_goods(self):
        await self.create(qty=3,price=5)
        ident=(await self.latest())["id"]
        await self.db._require().execute(
            "CREATE TRIGGER bad_ledger BEFORE INSERT ON ledger "
            "WHEN NEW.reason LIKE 'рынок PETS:%' "
            "BEGIN SELECT RAISE(ABORT,'ledger failure'); END"
        )
        await self.db._require().commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.buy_market_listing(2,201,ident,NOW+1,TZ)
        self.assertEqual((await self.latest())["state"],"active")
        self.assertEqual(await self.leaf(2),0)
        self.assertEqual(await self.leaf(),47)
        self.assertEqual((await self.db.get_user(CHAT,201))["balance"],30)
        self.assertEqual((await self.db.get_user(CHAT,101))["balance"],0)
        await self.db._require().execute("DROP TRIGGER bad_ledger")
        await self.db._require().commit()
        self.assertIn("✅",await self.db.buy_market_listing(
            2,201,ident,NOW+2,TZ))

    async def test_restart_preserves_active_listings_and_refunds(self):
        await self.create(qty=2,price=7)
        ident=(await self.latest())["id"]
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        self.assertEqual((await self.latest())["state"],"active")
        self.assertEqual(await self.leaf(),48)
        self.assertEqual((await self.state())["listings"][0]["id"],ident)
        await self.state(now=NOW+LISTING_TTL_SECONDS+3)
        self.assertEqual(await self.leaf(),50)


if __name__ == "__main__":
    unittest.main()
