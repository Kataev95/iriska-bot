"""PETS WORLD 1.8: real SQLite, four-week season and cosmetic reward tests."""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_season import CHALLENGES, season_bounds

TZ=ZoneInfo("Europe/Moscow")
NOW=datetime(2026,11,13,13,tzinfo=TZ).timestamp()
CHAT=-10099181
OTHER_CHAT=-10099182


class SeasonRulesTests(unittest.TestCase):
    def test_28_day_calendar_boundaries(self):
        begin,end,start,stop,number=season_bounds(NOW,TZ)
        self.assertEqual((start,stop,number),("2026-11-09","2026-12-07",1))
        self.assertLess(begin,NOW)
        self.assertGreater(end,NOW)
        next_season=season_bounds(end,TZ)
        self.assertEqual((next_season[2],next_season[3],next_season[4]),
                         ("2026-12-07","2027-01-04",2))
        self.assertEqual(len(CHALLENGES),5)

    def test_dst_uses_local_calendar_not_28_fixed_utc_days(self):
        ny=ZoneInfo("America/New_York")
        begin,end,_,_,_=season_bounds(NOW,ny)
        # DST changes in other seasons; timestamps must use local midnights.
        self.assertEqual(datetime.fromtimestamp(begin,ny).hour,0)
        self.assertEqual(datetime.fromtimestamp(end,ny).hour,0)


class SeasonDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"save.sqlite3")
        self.db=Database(self.path)
        await self.db.connect()
        for chat,users in [(CHAT,(101,102)),(CHAT,(201,202)),(OTHER_CHAT,(301,302))]:
            for uid in users:
                await self.db.ensure_user(chat,uid,None,f"Player{uid}")
            await self.db._require().execute(
                "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
                "streak,best_streak,created_ts,care_ts,food,joy,energy) "
                "VALUES(?,?,?,?,'active','dragon',2,2,?,?,80,80,80)",
                (chat,users[0],users[1],users[0],NOW,NOW),
            )
        await self.db._require().commit()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def seed_trips(self,pet_id=1,quantity=3,claimed_at=NOW):
        for idx in range(quantity):
            await self.db._require().execute(
                "INSERT INTO pet_trips"
                "(pet_id,user_id,day,route,started_at,finish_at,xp,coins,claimed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (pet_id,101 if pet_id==1 else 201,
                 datetime.fromtimestamp(claimed_at,TZ).date().isoformat(),
                 "park",claimed_at-1000-idx,claimed_at-10,35,2,claimed_at),
            )
        await self.db._require().commit()

    async def wallets(self):
        user=await self.db.get_user(CHAT,101)
        rows=await (await self.db._require().execute(
            "SELECT COUNT(*) FROM ledger WHERE chat_id=?",(CHAT,)
        )).fetchone()
        return (user["balance"],user["earned_total"],rows[0])

    async def test_initial_state_is_private_without_fake_results(self):
        state=await self.db.season_state(1,101,NOW,TZ)
        self.assertEqual(state["season"]["number"],1)
        self.assertEqual(state["my_score"],0)
        self.assertIsNone(state["my_rank"])
        self.assertEqual(state["top"],[])
        self.assertEqual(state["titles"],[])
        self.assertEqual(len(state["challenges"]),5)
        self.assertTrue(all(not c["ready"] and not c["claimed"]
                            for c in state["challenges"]))
        self.assertIsNone(await self.db.season_state(1,201,NOW,TZ))
        self.assertIsNone(await self.db.season_state(3,101,NOW,TZ))
        self.assertEqual((await self.db.season_state(3,301,NOW,TZ))["top"],[])

    async def test_real_trips_unlock_only_server_verified_reward(self):
        before=await self.wallets()
        self.assertIn("🔒",await self.db.claim_season_challenge(
            1,101,"explorer",NOW,TZ))
        self.assertIn("⚠️",await self.db.claim_season_challenge(
            1,101,"../../bad",NOW,TZ))
        await self.seed_trips()
        state=await self.db.season_state(1,102,NOW,TZ)
        self.assertEqual(state["my_score"],30)
        self.assertEqual(state["top"][0]["pet_id"],1)
        challenge=next(c for c in state["challenges"] if c["code"]=="explorer")
        self.assertTrue(challenge["ready"])
        self.assertFalse(challenge["claimed"])
        self.assertIn("✅",await self.db.claim_season_challenge(
            1,102,"explorer",NOW,TZ))
        self.assertIn("уже получена",await self.db.claim_season_challenge(
            1,101,"explorer",NOW,TZ))
        self.assertEqual((await self.db.season_state(1,101,NOW,TZ))["claimed_count"],1)
        self.assertEqual(await self.wallets(),before)
        self.assertIn("✅",await self.db.equip_season_title(
            1,101,"explorer","2026-11-09"))
        self.assertIn("Следопыт",await self.db.season_equipped_title(1))
        self.assertIn("✅",await self.db.equip_season_title(1,102,"",""))
        self.assertEqual(await self.db.season_equipped_title(1),"")

    async def test_concurrent_claims_are_exactly_once(self):
        await self.seed_trips()
        results=await asyncio.gather(
            self.db.claim_season_challenge(1,101,"explorer",NOW,TZ),
            self.db.claim_season_challenge(1,102,"explorer",NOW,TZ),
        )
        self.assertEqual(sum("Получен титул" in result for result in results),1)
        row=await (await self.db._require().execute(
            "SELECT COUNT(*) FROM pet_season_claims "
            "WHERE pet_id=1 AND challenge='explorer'"
        )).fetchone()
        self.assertEqual(row[0],1)

    async def test_old_claims_remain_permanent_across_rollover_and_restart(self):
        await self.seed_trips()
        await self.db.claim_season_challenge(1,101,"explorer",NOW,TZ)
        await self.db.equip_season_title(1,102,"explorer","2026-11-09")
        begin,end,*_=season_bounds(NOW,TZ)
        future=await self.db.season_state(1,101,end+60,TZ)
        self.assertEqual(future["season"]["number"],2)
        self.assertEqual(future["my_score"],0)
        self.assertEqual(future["claimed_count"],0)
        self.assertEqual(len(future["titles"]),1)
        self.assertTrue(future["titles"][0]["equipped"])
        self.assertIn("🔒",await self.db.claim_season_challenge(
            1,101,"explorer",end+60,TZ))
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        state=await self.db.season_state(1,102,end+60,TZ)
        self.assertEqual(len(state["titles"]),1)
        self.assertIn("Следопыт",await self.db.season_equipped_title(1))

    async def test_other_chat_and_forged_claims_cannot_equip(self):
        await self.seed_trips()
        self.assertIn("🔒",await self.db.equip_season_title(
            1,101,"explorer","2026-11-09"))
        self.assertIn("🔒",await self.db.equip_season_title(
            2,201,"explorer","2026-11-09"))
        self.assertIn("⚠️",await self.db.claim_season_challenge(
            3,101,"explorer",NOW,TZ))
        self.assertIn("⚠️",await self.db.equip_season_title(
            3,101,"",""))
        await self.db.claim_season_challenge(1,101,"explorer",NOW,TZ)
        self.assertIn("🔒",await self.db.equip_season_title(
            2,201,"explorer","2026-11-09"))
        self.assertIn("🔒",await self.db.equip_season_title(
            1,101,"explorer","9999-99-99"))
        self.assertEqual(await self.db.season_equipped_title(2),"")

    async def test_dailies_crafts_and_business_are_derived_from_completed_records(self):
        db=self.db._require()
        for day,task in [
            ("2026-11-09","care"),("2026-11-09","adventure"),
            ("2026-11-10","care"),("2026-11-10","adventure"),
            ("2026-11-11","care"),("2026-11-11","adventure"),
            ("2026-11-12","care"),("2026-11-12","adventure"),
        ]:
            await db.execute("INSERT INTO pet_daily_claims(pet_id,day,task)"
                             " VALUES(1,?,?)",(day,task))
        await db.execute("INSERT INTO pet_crafts(pet_id,recipe,crafted_at)"
                         " VALUES(1,'medal',?)",(NOW,))
        for idx in range(3):
            await db.execute(
                "INSERT INTO pet_business_jobs"
                "(pet_id,business,user_id,day,started_at,finish_at,output_qty,collected_at)"
                " VALUES(1,'florist',101,'2026-11-13',?,?,1,?)",
                (NOW-300-idx,NOW-100,NOW),
            )
        await db.commit()
        state=await self.db.season_state(1,101,NOW,TZ)
        ready={c["code"] for c in state["challenges"] if c["ready"]}
        self.assertTrue({"helper","maker","producer"}.issubset(ready))
        self.assertNotIn("explorer",ready)
        self.assertGreaterEqual(state["my_score"],8*4+8+3*5)
        self.assertIn("✅",await self.db.claim_season_challenge(1,101,"maker",NOW,TZ))


if __name__=="__main__":
    unittest.main()
