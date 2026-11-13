"""PETS WORLD 1.9 — real SQLite studio, partner progress and album tests."""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database
from pet_studio import FRAMES, STICKERS

TZ=ZoneInfo("Europe/Moscow")
NOW=datetime(2026,11,13,15,tzinfo=TZ).timestamp()
CHAT=-10014201
OTHER_CHAT=-10014202


class StudioGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"pets.sqlite3")
        self.db=Database(self.path)
        await self.db.connect()
        for chat,users in [
            (CHAT,(101,102)),(CHAT,(201,202)),(OTHER_CHAT,(301,302))
        ]:
            for uid in users:
                await self.db.ensure_user(chat,uid,None,f"Player{uid}")
            await self.db._require().execute(
                "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
                "streak,best_streak,created_ts,care_ts,food,joy,energy) "
                "VALUES(?,?,?,?,'active','dragon',2,2,?,?,80,80,80)",
                (chat,users[0],users[1],users[0],NOW-86400,NOW),
            )
        await self.db._require().commit()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def trip(self, pet_id=1, number=1):
        for i in range(number):
            cur=await self.db._require().execute(
                "INSERT INTO pet_trips"
                "(pet_id,user_id,day,route,started_at,finish_at,xp,coins,claimed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (pet_id,101 if pet_id==1 else 201,
                 "2026-11-13","park",NOW-500-i,NOW-100,35,2,NOW-i),
            )
            # The saved drop table records claimed expedition rewards.
            await self.db._require().execute(
                "INSERT INTO pet_material_drops(trip_id,material,quantity)"
                " VALUES(?,?,?)",(cur.lastrowid,"crystal",1),
            )
        await self.db._require().commit()

    async def wallet(self):
        row=await self.db.get_user(CHAT,101)
        ledger=(await (await self.db._require().execute(
            "SELECT COUNT(*) FROM ledger WHERE chat_id=?",(CHAT,)
        )).fetchone())[0]
        return (row["balance"],row["earned_total"],ledger)

    async def test_default_private_state_and_no_fake_unlocks(self):
        state=await self.db.studio_state(1,101,TZ)
        self.assertEqual(state["equipped"]["frame"],"classic")
        self.assertEqual(state["equipped"]["sticker"],"paw")
        self.assertEqual(len(state["frames"]),len(FRAMES))
        self.assertEqual(len(state["stickers"]),len(STICKERS))
        self.assertEqual(sum(x["unlocked"] for x in state["frames"]),1)
        self.assertEqual(sum(x["unlocked"] for x in state["stickers"]),1)
        self.assertEqual(state["metrics"]["days_both"],0)
        self.assertEqual(state["album"][0]["kind"],"origin")
        self.assertEqual(state["album"][0]["day"],"12.11.2026")
        self.assertIsNone(await self.db.studio_state(1,201,TZ))
        self.assertIsNone(await self.db.studio_state(3,101,TZ))
        self.assertIsInstance(await self.db.studio_state(3,301,TZ),dict)

    async def test_studio_equip_fails_for_locked_and_forged_styles(self):
        before=await self.wallet()
        self.assertIn("🔒",await self.db.equip_studio_style(
            1,101,"frame","grove",NOW))
        self.assertIn("🔒",await self.db.equip_studio_style(
            1,101,"sticker","heart",NOW))
        self.assertIn("⚠️",await self.db.equip_studio_style(
            1,101,"frame","broken;cursor:pointer",NOW))
        self.assertIn("⚠️",await self.db.equip_studio_style(
            1,101,"pet_id","classic",NOW))
        self.assertIn("⚠️",await self.db.equip_studio_style(
            2,101,"frame","classic",NOW))
        self.assertIn("⚠️",await self.db.equip_studio_style(
            3,101,"frame","classic",NOW))
        self.assertEqual(await self.wallet(),before)
        self.assertEqual((await self.db.studio_equipped(1))["frame"],"classic")

    async def test_trips_unlock_frames_stickers_and_shared_album(self):
        before=await self.wallet()
        await self.trip(1,3)
        state=await self.db.studio_state(1,102,TZ)
        self.assertTrue(next(x for x in state["frames"] if x["code"]=="grove")["unlocked"])
        self.assertFalse(next(x for x in state["frames"] if x["code"]=="cosmos")["unlocked"])
        self.assertTrue(next(x for x in state["stickers"] if x["code"]=="compass")["unlocked"])
        self.assertEqual(state["metrics"]["crystals"],3)
        self.assertTrue(next(x for x in state["stickers"] if x["code"]=="crystal")["unlocked"])
        self.assertEqual(sum(e["kind"]=="trip" for e in state["album"]),3)
        self.assertEqual(state["album"][-1]["kind"],"origin")
        self.assertIn("✅",await self.db.equip_studio_style(
            1,102,"frame","grove",NOW))
        self.assertIn("✅",await self.db.equip_studio_style(
            1,101,"sticker","compass",NOW))
        self.assertEqual((await self.db.studio_equipped(1))["frame"],"grove")
        self.assertEqual((await self.db.studio_state(1,102,TZ))["equipped"]["sticker"],
                         "compass")
        self.assertEqual((await self.db.studio_equipped(2))["frame"],"classic")
        self.assertEqual(await self.wallet(),before)

    async def test_cooperation_requires_both_distinct_owners_same_day(self):
        db=self.db._require()
        for day in ("2026-11-10","2026-11-11","2026-11-12"):
            await db.execute(
                "INSERT INTO pet_daily_activity(pet_id,day,user_id,kind) "
                "VALUES(1,?,101,'care')",(day,))
        await db.commit()
        self.assertEqual((await self.db.studio_state(1,102,TZ))["metrics"]["days_both"],0)
        self.assertIn("🔒",await self.db.equip_studio_style(
            1,101,"sticker","heart",NOW))
        for day in ("2026-11-10","2026-11-11","2026-11-12"):
            await db.execute(
                "INSERT INTO pet_daily_activity(pet_id,day,user_id,kind) "
                "VALUES(1,?,102,'care')",(day,))
        await db.commit()
        state=await self.db.studio_state(1,102,TZ)
        self.assertEqual(state["metrics"]["days_both"],3)
        self.assertIn("✅",await self.db.equip_studio_style(
            1,101,"sticker","heart",NOW))
        self.assertEqual((await self.db.studio_equipped(1))["sticker"],"heart")

    async def test_season_claim_unlocks_champion_crown_and_album(self):
        await self.db._require().execute(
            "INSERT INTO pet_season_claims"
            "(pet_id,season_start,challenge,claimed_at,claimed_by)"
            " VALUES(1,'2026-11-09','explorer',?,101)",(NOW,)
        )
        await self.db._require().commit()
        state=await self.db.studio_state(1,102,TZ)
        self.assertTrue(next(x for x in state["frames"] if x["code"]=="champion")["unlocked"])
        self.assertTrue(next(x for x in state["stickers"] if x["code"]=="crown")["unlocked"])
        self.assertIn("title",{x["kind"] for x in state["album"]})
        self.assertIn("✅",await self.db.equip_studio_style(
            1,102,"frame","champion",NOW))
        self.assertIn("✅",await self.db.equip_studio_style(
            1,101,"sticker","crown",NOW))

    async def test_studio_persists_restart_resets_and_shared_owner(self):
        await self.trip(1,3)
        r=await asyncio.gather(
            self.db.equip_studio_style(1,101,"frame","grove",NOW),
            self.db.equip_studio_style(1,102,"sticker","crystal",NOW),
        )
        self.assertEqual(sum(text.startswith("✅") for text in r),2)
        old_album=await self.db.studio_state(1,101,TZ)
        await self.db.close()
        self.db=Database(self.path)
        await self.db.connect()
        second=await self.db.studio_state(1,102,TZ)
        self.assertEqual(second["equipped"]["frame"],"grove")
        self.assertEqual(second["equipped"]["sticker"],"crystal")
        self.assertEqual(second["album"],old_album["album"])
        self.assertIn("✅",await self.db.equip_studio_style(
            1,102,"frame","classic",NOW+1))
        self.assertIn("✅",await self.db.equip_studio_style(
            1,101,"sticker","paw",NOW+1))
        self.assertEqual((await self.db.studio_equipped(1))["frame"],"classic")
        self.assertEqual((await self.db.studio_equipped(1))["sticker"],"paw")

    async def test_album_is_bounded_and_does_not_cross_chat(self):
        await self.trip(1,30)
        await self.trip(2,1)
        state=await self.db.studio_state(1,101,TZ)
        self.assertEqual(len(state["album"]),24)
        self.assertTrue(all("101" not in item["title"] for item in state["album"]))
        self.assertEqual((await self.db.studio_state(2,201,TZ))["metrics"]["trips"],1)
        self.assertEqual((await self.db.studio_state(3,301,TZ))["metrics"]["trips"],0)


if __name__ == "__main__":
    unittest.main()
