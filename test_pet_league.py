"""PETS WORLD 1.7 — private, read-only, chat-scoped league regressions."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import Database

TZ=ZoneInfo("Europe/Moscow")
NOW=datetime(2026,11,13,12,tzinfo=TZ).timestamp()
CHAT=-10017903
OTHER_CHAT=-10017904


class LeagueStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.database=Database(str(Path(self.tmp.name)/"pets.sqlite3"))
        await self.database.connect()
        for chat,users in [
            (CHAT,(101,102)),(CHAT,(201,202)),(OTHER_CHAT,(301,302))
        ]:
            for user in users:
                await self.database.ensure_user(chat,user,None,"Player")
            await self.database._require().execute(
                "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
                "streak,best_streak,created_ts,care_ts,food,joy,energy) "
                "VALUES(?,?,?,?,'active','dragon',2,2,?,?,80,80,80)",
                (chat,users[0],users[1],users[0],NOW,NOW),
            )
        await self.database._require().commit()

    async def asyncTearDown(self):
        await self.database.close()
        self.tmp.cleanup()

    async def test_auth_and_read_only_balances(self):
        before=(await self.database.get_user(CHAT,101))["balance"]
        result=await self.database.league_state(1,101,NOW,TZ)
        self.assertIsInstance(result,dict)
        self.assertEqual((await self.database.get_user(CHAT,101))["balance"],before)
        outsider=await self.database.league_state(1,201,NOW,TZ)
        self.assertIsNone(outsider)

    async def test_week_switch_preserves_old_game_progress(self):
        before=await self.database.get_pet(1)
        current=await self.database.league_state(1,101,NOW,TZ)
        future=await self.database.league_state(1,101,NOW+7*86400,TZ)
        self.assertIsInstance(current,dict)
        self.assertIsInstance(future,dict)
        after=await self.database.get_pet(1)
        self.assertEqual(before["xp"],after["xp"])
        self.assertEqual(before["best_streak"],after["best_streak"])
        self.assertEqual(before["house"],after["house"])

    async def test_no_cross_chat_leak(self):
        mine=await self.database.league_state(1,101,NOW,TZ)
        foreign=await self.database.league_state(3,301,NOW,TZ)
        self.assertIsInstance(mine,dict)
        self.assertIsInstance(foreign,dict)
        self.assertIsNone(await self.database.league_state(3,101,NOW,TZ))

    async def test_restart_no_mutation(self):
        first=await self.database.league_state(2,201,NOW,TZ)
        path=self.database.path
        await self.database.close()
        self.database=Database(path)
        await self.database.connect()
        second=await self.database.league_state(2,201,NOW,TZ)
        self.assertEqual(first,second)


if __name__=="__main__":
    unittest.main()
