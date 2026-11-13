"""Mini App authorization and real SQLite integration tests without Telegram token."""

import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer
from db import Database
from miniapp.api import create_app
from miniapp.auth import Unauthorized, main_app_link, validate_init_data

TOKEN = "123456789:ABC_TEST_BOT_TOKEN"
CHAT_ID = -1009876


def signed(user_id: int, auth_date=None, **extras):
    parts = {
        "user": json.dumps({"id": user_id, "first_name": "Tester"},
                           separators=(",", ":")),
        "auth_date": str(auth_date or int(time.time())),
        **extras,
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(parts.items()))
    parts["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(parts)


class InitDataTests(unittest.TestCase):
    def test_auth_and_tamper(self):
        raw = signed(101, start_param="p_4")
        self.assertEqual(validate_init_data(raw, TOKEN)["user_id"], 101)
        self.assertEqual(validate_init_data(raw, TOKEN)["start_param"], "p_4")
        with self.assertRaises(Unauthorized):
            validate_init_data(raw.replace("p_4", "p_5"), TOKEN)
        with self.assertRaises(Unauthorized):
            validate_init_data(raw.replace("101", "102"), TOKEN)
        with self.assertRaises(Unauthorized):
            validate_init_data(signed(101, auth_date=int(time.time())-90001), TOKEN)
        with self.assertRaises(Unauthorized):
            validate_init_data("", TOKEN)
        with self.assertRaises(Unauthorized):
            validate_init_data(raw + "&auth_date=1", TOKEN)

    def test_links(self):
        self.assertEqual(main_app_link("IriskaBot", 25),
                         "https://t.me/IriskaBot?startapp=p_25")
        with self.assertRaises(ValueError):
            main_app_link("bad/name", 1)


class GameHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        now = time.time()
        for user_id in (101, 102, 201, 202):
            await self.db.ensure_user(CHAT_ID, user_id, None, f"Player{user_id}")
        for users in ((101, 102), (201, 202)):
            await self.db._require().execute(
                "INSERT INTO pets(chat_id,user_a,user_b,invited_by,status,species,"
                "streak,best_streak,created_ts,care_ts,food,joy,energy) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (CHAT_ID, users[0], users[1], users[0], "active", "dragon",
                 2, 2, now, now, 45, 50, 80),
            )
        await self.db._require().commit()
        await self.db.adjust_balance(CHAT_ID, 101, 20, "test", None)
        self.config = SimpleNamespace(bot_token=TOKEN, tz=ZoneInfo("Europe/Moscow"))
        self.client = TestClient(TestServer(create_app(self.db, self.config)))
        await self.client.start_server()
        self.headers = {"X-Telegram-Init-Data": signed(101)}

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()

    async def test_auth_bootstrap_and_private_pet(self):
        response = await self.client.get("/api/bootstrap")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/api/bootstrap", headers=self.headers)
        self.assertEqual(response.status, 200)
        pets = (await response.json())["pets"]
        self.assertEqual(len(pets), 1)
        self.assertEqual(pets[0]["id"], 1)

        response = await self.client.get("/api/pet/2", headers=self.headers)
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/pet/1", headers=self.headers)
        pet = await response.json()
        self.assertEqual(pet["partner"], "Player102")
        self.assertEqual(pet["balance"], 20)
        self.assertEqual(pet["level"], 1)
        self.assertIn("/assets/pets/dragon_", pet["image"])

    async def test_actions_are_authorized_and_existing_db_is_authoritative(self):
        # An attacker cannot act on another player's shared pet.
        r = await self.client.post(
            "/api/action", headers=self.headers,
            json={"kind": "buy", "pet_id": 2, "code": "bow"},
        )
        self.assertEqual(r.status, 403)
        r = await self.client.post(
            "/api/action", headers=self.headers,
            json={"kind": "buy", "pet_id": 1, "code": "bow", "user_id": 201,
                  "balance": 9999},
        )
        self.assertEqual(r.status, 200)
        result = await r.json()
        self.assertIn("bow", result["pet"]["items"])
        self.assertEqual(result["pet"]["balance"], 15)
        self.assertEqual((await self.db.get_user(CHAT_ID, 201))["balance"], 0)

        r = await self.client.post(
            "/api/action", headers=self.headers,
            json={"kind": "buy", "pet_id": 1, "code": "bow"},
        )
        self.assertEqual(r.status, 200)
        self.assertEqual((await r.json())["pet"]["balance"], 15)

    async def test_static_api_and_no_web_auth_bypass(self):
        r = await self.client.get("/assets/pets/dragon_baby.jpg")
        self.assertEqual(r.status, 200)
        r = await self.client.get("/api/pet/1",
                                  headers={"X-Telegram-Init-Data": signed(201)})
        self.assertEqual(r.status, 403)
        r = await self.client.post("/api/action", headers=self.headers,
                                   json={"pet_id": 1, "kind": "admin", "code": "credits"})
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
