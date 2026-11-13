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

    def test_real_telegram_style_initdata_including_signature(self):
        # Recent Telegram apps include an extra Ed25519 "signature".
        # Bot-token hash MUST cover that field; other fields unchanged.
        raw = signed(101, signature="TelegramEd25519SampleBase64url-_",
                     query_id="AA123456", chat_type="supergroup")
        validated = validate_init_data(raw, TOKEN)
        self.assertEqual(validated["user_id"], 101)

        # Do not accept removing/replacing a field that was HMAC signed.
        with self.assertRaises(Unauthorized):
            validate_init_data(raw.replace("signature=", "signature=modified"), TOKEN)
        with self.assertRaises(Unauthorized):
            validate_init_data(raw.replace("&signature=TelegramEd25519SampleBase64url-_", ""), TOKEN)

        # Both old (no signature) and new (with signature) Telegram
        # client versions must work. The unsigned tokens stay invalid.
        self.assertEqual(validate_init_data(signed(101), TOKEN)["user_id"], 101)

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
        # Actual Telegram clients may provide both hash and signature.
        self.headers = {
            "X-Telegram-Init-Data": signed(101, signature="ProductionTelegramEd25519_signature")
        }

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

    async def test_live_cooldowns_trip_limit_and_level_progress(self):
        r = await self.client.get("/api/pet/1", headers=self.headers)
        self.assertEqual(r.status, 200)
        pet = await r.json()
        self.assertEqual(pet["level_start_xp"], 0)
        self.assertEqual(pet["next_xp"], 50)
        self.assertEqual(pet["trips_remaining"], 3)
        self.assertTrue(pet["mood"])
        self.assertEqual(pet["care_state"]["feed"]["ready_at"], 0)

        # A completed care action generates a server-backed cooldown.
        r = await self.client.post("/api/action", headers=self.headers,
            json={"pet_id": 1, "kind": "care", "code": "feed"})
        self.assertEqual(r.status, 200)
        after = (await r.json())["pet"]
        self.assertGreater(after["care_state"]["feed"]["ready_at"], after["server_time"])
        self.assertLessEqual(after["care_state"]["feed"]["ready_at"] - after["server_time"], 7201)

        # Existing server-side validation still rejects immediate repeats.
        r = await self.client.post("/api/action", headers=self.headers,
            json={"pet_id": 1, "kind": "care", "code": "feed"})
        self.assertEqual(r.status, 200)
        repeated = (await r.json())["pet"]
        self.assertEqual(repeated["care_state"]["feed"]["ready_at"],
                         after["care_state"]["feed"]["ready_at"])
        self.assertEqual(repeated["balance"], after["balance"])

        r = await self.client.post("/api/action", headers=self.headers,
            json={"pet_id": 1, "kind": "start", "code": "park"})
        self.assertEqual(r.status, 200)
        started = (await r.json())["pet"]
        self.assertEqual(started["trips_remaining"], 2)
        self.assertIsNotNone(started["trip"])

    async def test_static_api_and_no_web_auth_bypass(self):
        r = await self.client.get("/assets/pets/dragon_baby.jpg")
        self.assertEqual(r.status, 200)
        r = await self.client.get("/api/pet/1",
                                  headers={"X-Telegram-Init-Data": signed(201)})
        self.assertEqual(r.status, 403)
        r = await self.client.post("/api/action", headers=self.headers,
                                   json={"pet_id": 1, "kind": "admin", "code": "credits"})
        self.assertEqual(r.status, 400)



class MiniAppFullscreenLayoutTests(unittest.TestCase):
    """Protect against Telegram native header overlapping the PETS logo."""

    def test_safe_area_insets_and_cache_versions(self):
        from pathlib import Path

        static = Path(__file__).resolve().parent / "miniapp" / "static"
        css = (static / "style.css").read_text(encoding="utf-8")
        js = (static / "app.js").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        self.assertIn("--tg-safe-area-inset-top", css)
        self.assertIn("--tg-content-safe-area-inset-top", css)
        self.assertIn("--pets-top-offset", css)
        self.assertIn("padding:calc(var(--pets-top-offset)", css)
        self.assertIn("contentSafeAreaChanged", js)
        self.assertIn("safeAreaChanged", js)
        self.assertIn("syncSafeArea()", js)
        self.assertIn('TG?.BackButton?.onClick(goBack)', js)
        self.assertIn('TG?.onEvent?.("activated"', js)
        self.assertIn('++ui.syncSerial', js)
        self.assertIn('data-trip-countdown', js)
        self.assertIn('care_state', js)
        self.assertIn("trips_remaining", js)
        self.assertIn("syncClock(snapshot)", js)
        self.assertIn("/static/style.css?v=4", html)
        self.assertIn("/static/app.js?v=4", html)


class MiniAppFeedbackTests(unittest.TestCase):
    """Visual feedback must be client-only and preserve the fullscreen layout."""

    def test_character_interaction_is_not_a_game_action(self):
        from pathlib import Path
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        js = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "style.css").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-click="pet-greet"', js)
        self.assertIn('if (target.dataset.click==="pet-greet") {petGreeting();return;}', js)
        self.assertIn('class="effects-layer"', html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('function showActionEffect(kind, code, before, after, message)', js)
        self.assertIn('setPet(result.pet);', js)
        self.assertIn('showActionEffect(kind, code, before, result.pet, result.message)', js)
        self.assertIn('if (success) {', js)
        self.assertIn('before.trip && !after.trip', js)
        self.assertIn('after.daily.filter(t => t.claimed)', js)
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('effect-card', css)
        self.assertIn('task-progress', css)
        self.assertIn('safeAreaInset', js)
        self.assertIn('contentSafeAreaInset', js)

    def test_frontend_javascript_has_valid_syntax(self):
        from pathlib import Path
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node runtime not available (GitHub Actions checks parsing)")
        js = Path(__file__).resolve().parent / "miniapp" / "static" / "app.js"
        cp = subprocess.run(["node", "--check", str(js)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
