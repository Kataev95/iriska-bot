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

    async def test_inventory_craft_auth_and_forged_browser_resources(self):
        bootstrap = await (
            await self.client.get("/api/bootstrap", headers=self.headers)
        ).json()
        self.assertEqual(len(bootstrap["materials"]), 4)
        self.assertEqual(len(bootstrap["recipes"]), 3)
        self.assertEqual(bootstrap["routes"][0]["loot"]["leaf"], 2)
        r = await self.client.get("/api/pet/1", headers=self.headers)
        pet = await r.json()
        self.assertEqual(pet["materials"]["leaf"], 0)
        self.assertEqual(pet["crafts"], [])

        payload = {"pet_id": 1, "kind": "craft", "code": "leaf_crown",
                   "user_id": 201, "materials": {"leaf": 500, "wood": 500}}
        r = await self.client.post("/api/action", headers=self.headers, json=payload)
        self.assertEqual(r.status, 200)
        self.assertIn("Не хватает", (await r.json())["message"])

        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(1,"leaf",4), (1,"wood",2)]
        )
        await self.db._require().commit()
        denied = await self.client.post(
            "/api/action",
            headers={"X-Telegram-Init-Data": signed(201)},
            json=payload,
        )
        self.assertEqual(denied.status, 403)

        r = await self.client.post("/api/action", headers=self.headers, json=payload)
        self.assertEqual(r.status, 200)
        data = await r.json()
        self.assertTrue(data["message"].startswith("✅"))
        self.assertIn("leaf_crown", data["pet"]["crafts"])
        self.assertEqual(data["pet"]["materials"]["leaf"], 0)
        self.assertEqual(data["pet"]["materials"]["wood"], 0)
        self.assertEqual(data["pet"]["balance"], 20)

        partner = await self.client.get(
            "/api/pet/1", headers={"X-Telegram-Init-Data": signed(102)}
        )
        self.assertIn("leaf_crown", (await partner.json())["crafts"])
        again = await self.client.post("/api/action", headers=self.headers, json=payload)
        self.assertEqual(again.status, 200)
        self.assertIn("уже изготовлена", (await again.json())["message"])
        invalid = await self.client.post("/api/action", headers=self.headers,
             json={"pet_id":1,"kind":"craft","code":"__admin"})
        self.assertEqual(invalid.status, 400)

    async def test_trip_claim_grants_real_materials_via_api_once(self):
        r = await self.client.post("/api/action", headers=self.headers,
              json={"pet_id":1,"kind":"start","code":"park"})
        self.assertEqual(r.status, 200)
        trip = (await r.json())["pet"]["trip"]
        self.assertIsNotNone(trip)
        # Make this specific test expedition claimable without sleeping 15min.
        await self.db._require().execute(
            "UPDATE pet_trips SET finish_at=? WHERE id=?",
            (time.time()-1,trip["id"]),
        )
        await self.db._require().commit()
        claim = {"pet_id":1,"kind":"claim","trip_id":trip["id"]}
        r = await self.client.post("/api/action",headers=self.headers,json=claim)
        self.assertEqual(r.status,200)
        data = await r.json()
        self.assertIn("🎒",data["message"])
        self.assertEqual(data["pet"]["materials"]["leaf"],2)
        self.assertEqual(data["pet"]["materials"]["wood"],1)
        self.assertEqual(data["pet"]["balance"],22)
        self.assertEqual(data["pet"]["xp"],35)
        r = await self.client.post("/api/action",headers=self.headers,json=claim)
        self.assertEqual(r.status,200)
        data = await r.json()
        self.assertIn("уже получена",data["message"])
        self.assertEqual(data["pet"]["materials"]["leaf"],2)
        self.assertEqual(data["pet"]["xp"],35)

    async def test_home_purchase_shared_rooms_daily_bonus_and_privacy(self):
        # Existing public API always authenticates using Telegram initData.
        await self.db.adjust_balance(CHAT_ID,101,10,"test",None)
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(1,"wood",10),(1,"leaf",8),(1,"stone",3)],
        )
        await self.db._require().commit()
        payload={"pet_id":1,"kind":"build_room","code":"bedroom","expected_level":0,
                 "materials":{"wood":10000},"user_id":201,"balance":99999}
        denied=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data": signed(201)},json=payload)
        self.assertEqual(denied.status,403)
        absent=await self.client.post("/api/action",headers=self.headers,json=payload)
        self.assertIn("Сначала купите домик",(await absent.json())["message"])

        buy=await self.client.post("/api/action",headers=self.headers,
                                   json={"pet_id":1,"kind":"buy","code":"house"})
        bought=await buy.json()
        self.assertTrue(bought["pet"]["house"])
        self.assertEqual(bought["pet"]["balance"],5)
        self.assertEqual(bought["pet"]["house_rooms"]["bedroom"],0)

        incorrect=await self.client.post("/api/action",headers=self.headers,
             json={"pet_id":1,"kind":"build_room","code":"bedroom"})
        self.assertEqual(incorrect.status,400)
        upgrade=await self.client.post("/api/action",headers=self.headers,json=payload)
        result=await upgrade.json()
        self.assertTrue(result["message"].startswith("✅"))
        self.assertEqual(result["pet"]["house_rooms"]["bedroom"],1)
        self.assertEqual(result["pet"]["materials"]["wood"],6)
        self.assertEqual(result["pet"]["materials"]["leaf"],6)
        replay=await self.client.post("/api/action",headers=self.headers,json=payload)
        self.assertIn("изменился",(await replay.json())["message"])
        self.assertEqual((await self.db.inventory_state(1))[0]["wood"],6)
        used=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data":signed(102)},
            json={"pet_id":1,"kind":"room_use","code":"bedroom"})
        outcome=await used.json()
        self.assertEqual(used.status,200)
        self.assertTrue(outcome["message"].startswith("✅"))
        self.assertIn("bedroom",outcome["pet"]["house_used"])
        repeated=await self.client.post("/api/action",headers=self.headers,
                                  json={"pet_id":1,"kind":"room_use","code":"bedroom"})
        self.assertIn("сегодня",(await repeated.json())["message"])

        workshop=await self.client.post("/api/action",headers=self.headers,
                json={"pet_id":1,"kind":"build_room","code":"workshop","expected_level":0})
        upgraded=await workshop.json()
        self.assertTrue(upgraded["message"].startswith("✅"))
        self.assertEqual(upgraded["pet"]["house_route_loot"]["forest"]["wood"],3)
        self.assertEqual(upgraded["pet"]["house_route_loot"]["park"]["leaf"],2)
        partner=await self.client.get("/api/pet/1",
                    headers={"X-Telegram-Init-Data":signed(102)})
        self.assertEqual((await partner.json())["house_rooms"]["workshop"],1)

    async def test_house_api_rejects_unknown_rooms_and_other_users(self):
        invalid=await self.client.post("/api/action",headers=self.headers,
             json={"pet_id":1,"kind":"build_room","code":"__all","expected_level":0})
        self.assertEqual(invalid.status,400)
        unknown=await self.client.post("/api/action",headers=self.headers,
             json={"pet_id":1,"kind":"room_use","code":"workshop"})
        self.assertEqual(unknown.status,400)
        no_auth=await self.client.post("/api/action",json={"pet_id":1,
                "kind":"room_use","code":"bedroom"})
        self.assertEqual(no_auth.status,401)

    async def test_business_authorization_production_and_sale(self):
        bootstrap=await self.client.get("/api/bootstrap",headers=self.headers)
        self.assertEqual(bootstrap.status,200)
        defs=await bootstrap.json()
        self.assertEqual(len(defs["businesses"]),2)
        self.assertEqual(defs["business_limits"]["sales_coins"],12)
        self.assertEqual(defs["business_limits"]["production_starts"],4)
        self.assertEqual((await self.client.get("/api/pet/1",
                headers=self.headers)).status,200)
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(1,"wood",30),(1,"leaf",30),(1,"stone",10)]
        )
        await self.db._require().commit()
        request={"pet_id":1,"kind":"business_build","code":"florist",
                 "expected_level":0,"user_id":201,
                 "materials":{"leaf":100000},"price":0}
        denied=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data":signed(201)},json=request)
        self.assertEqual(denied.status,403)
        r=await self.client.post("/api/action",headers=self.headers,json=request)
        self.assertEqual(r.status,200)
        self.assertEqual((await r.json())["pet"]["businesses"]["units"]["florist"]["level"],1)
        r=await self.client.post("/api/action",headers=self.headers,json=request)
        self.assertIn("изменился",(await r.json())["message"])
        self.assertEqual((await self.db.inventory_state(1))[0]["leaf"],24)
        started=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_start","code":"florist",
                  "production_seconds":0, "quantity":100000})
        startdata=await started.json()
        self.assertEqual(started.status,200)
        self.assertEqual(startdata["pet"]["businesses"]["units"]["florist"]["job"]["quantity"],2)
        batch=startdata["pet"]["businesses"]["units"]["florist"]["job"]
        too_early=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_collect","code":"florist",
                  "job_id":batch["id"]})
        self.assertIn("ещё",(await too_early.json())["message"])
        await self.db._require().execute(
            "UPDATE pet_business_jobs SET finish_at=? WHERE id=?",
            (time.time()-1,batch["id"]))
        await self.db._require().commit()
        claimed=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_collect","code":"florist",
                  "job_id":batch["id"],"quantity":9000})
        claimdata=await claimed.json()
        self.assertEqual(claimdata["pet"]["businesses"]["units"]["florist"]["stock"],2)
        repeated=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_collect","code":"florist",
                  "job_id":batch["id"]})
        self.assertIn("уже",(await repeated.json())["message"])
        sold=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_sell","code":"florist",
                  "quantity":123456, "coins":900000})
        sale=await sold.json()
        self.assertEqual(sale["pet"]["balance"],24)
        self.assertEqual(sale["pet"]["businesses"]["units"]["florist"]["stock"],0)
        self.assertEqual(sale["pet"]["businesses"]["sales_coins_remaining"],8)
        another=await self.client.get("/api/pet/1",
                    headers={"X-Telegram-Init-Data":signed(102)})
        self.assertEqual((await another.json())["businesses"]["units"]["florist"]["stock"],0)
        invalid=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"business_sell","code":"__internal__"})
        self.assertEqual(invalid.status,400)
        unauth=await self.client.post("/api/action",
            json={"pet_id":1,"kind":"business_sell","code":"florist"})
        self.assertEqual(unauth.status,401)

    async def test_market_auth_escrow_and_balance_transfer(self):
        await self.db._require().execute(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            (1,"leaf",5),
        )
        await self.db._require().commit()
        await self.db.adjust_balance(CHAT_ID,201,20,"test",None)
        no_auth=await self.client.get("/api/market/1")
        self.assertEqual(no_auth.status,401)
        alien=await self.client.get("/api/market/1",
            headers={"X-Telegram-Init-Data":signed(201)})
        self.assertEqual(alien.status,403)

        payload={"kind":"market_create","pet_id":1,"item_kind":"material",
                 "code":"leaf","quantity":3,"unit_price":2,
                 "balance":9999999,"user_id":201,"chat_id":999}
        attack=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data":signed(201)},json=payload)
        self.assertEqual(attack.status,403)
        created=await self.client.post("/api/action",headers=self.headers,json=payload)
        self.assertEqual(created.status,200)
        data=await created.json()
        self.assertTrue(data["message"].startswith("✅"))
        self.assertEqual(data["pet"]["materials"]["leaf"],2)

        browse=await self.client.get("/api/market/2",
            headers={"X-Telegram-Init-Data":signed(201)})
        self.assertEqual(browse.status,200)
        market=await browse.json()
        self.assertEqual(market["remaining_buys"],8)
        self.assertEqual(len(market["listings"]),1)
        listing=market["listings"][0]
        self.assertTrue(listing["can_buy"])
        self.assertEqual(listing["total_price"],6)
        ident=listing["id"]
        own=await self.client.get("/api/market/1",headers=self.headers)
        self.assertFalse((await own.json())["listings"][0]["can_buy"])

        bought=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data":signed(201)},
            json={"kind":"market_buy","pet_id":2,"listing_id":ident,
                  "quantity":100000,"price":0,"chat_id":CHAT_ID*2})
        self.assertEqual(bought.status,200)
        purchased=await bought.json()
        self.assertTrue(purchased["message"].startswith("✅"))
        self.assertEqual(purchased["pet"]["materials"]["leaf"],3)
        self.assertEqual(purchased["pet"]["balance"],14)
        self.assertEqual((await self.db.get_user(CHAT_ID,101))["balance"],26)
        self.assertEqual((await self.db.inventory_state(1))[0]["leaf"],2)
        again=await self.client.post("/api/action",
            headers={"X-Telegram-Init-Data":signed(202)},
            json={"kind":"market_buy","pet_id":2,"listing_id":ident})
        self.assertIn("уже",(await again.json())["message"])
        self.assertEqual((await self.db.inventory_state(2))[0]["leaf"],3)
        remaining=await self.client.get("/api/market/2",
            headers={"X-Telegram-Init-Data":signed(201)})
        self.assertEqual((await remaining.json())["listings"],[])

    async def test_market_rejects_invalid_items_and_forged_numbers(self):
        await self.db._require().execute(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            (1,"wood",10),
        )
        await self.db._require().commit()
        for value in (0,6,-1,True,"3"):
            invalid=await self.client.post("/api/action",headers=self.headers,
                json={"pet_id":1,"kind":"market_create","item_kind":"material",
                      "code":"wood","quantity":value,"unit_price":2})
            data=await invalid.json()
            self.assertIn("Неверное",data["message"])
        unknown=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"market_create","item_kind":"custom",
                  "code":"__grant_iriska","quantity":1,"unit_price":1})
        self.assertIn("нельзя",(await unknown.json())["message"])
        self.assertEqual((await self.db.inventory_state(1))[0]["wood"],10)
        forged=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"market_buy","listing_id":999999})
        self.assertIn("закрыто",(await forged.json())["message"])

    async def test_league_auth_read_only(self):
        missing=await self.client.get("/api/league/1")
        self.assertEqual(missing.status,401)
        alien=await self.client.get("/api/league/1",
            headers={"X-Telegram-Init-Data":signed(201)})
        self.assertEqual(alien.status,403)
        before=await self.db.get_user(CHAT_ID,101)
        response=await self.client.get("/api/league/1",headers=self.headers)
        self.assertEqual(response.status,200)
        info=await response.json()
        self.assertIsInstance(info,dict)
        after=await self.db.get_user(CHAT_ID,101)
        self.assertEqual(before["balance"],after["balance"])
        self.assertEqual(before["earned_total"],after["earned_total"])

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
        self.assertIn("/static/style.css?v=5", html)
        self.assertIn("/static/house.css?v=1", html)
        self.assertIn("/static/house.js?v=1", html)
        self.assertIn("/static/app.js?v=9", html)
        self.assertIn("/static/league.js?v=1", html)
        self.assertIn("/static/league.css?v=1", html)
        self.assertIn("/static/market.css?v=1", html)
        self.assertIn("/static/market.js?v=1", html)
        self.assertIn("/static/business.css?v=1", html)
        self.assertIn("/static/business.js?v=1", html)


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
        self.assertIn('window.PetsHouse.renderHouse(p,ui.config)', js)
        self.assertIn('data-click="house"', js)
        self.assertIn('data-build-room=', (static/"house.js").read_text(encoding="utf-8"))
        self.assertIn('house-scene', (static/"house.css").read_text(encoding="utf-8"))
        self.assertIn('window.PetsBusiness.renderBusiness', js)
        self.assertIn('data-business-build=', (static/"business.js").read_text(encoding="utf-8"))
        self.assertIn('business-hero', (static/"business.css").read_text(encoding="utf-8"))
        self.assertIn('window.PetsMarket.renderMarket', js)
        self.assertIn('data-market-buy=', (static/"market.js").read_text(encoding="utf-8"))
        self.assertIn('data-market-post=', (static/"market.js").read_text(encoding="utf-8"))
        self.assertIn('market-scene', (static/"market.css").read_text(encoding="utf-8"))
        self.assertIn('window.PetsLeague.renderLeague', js)
        self.assertIn('league-scene', (static/"league.css").read_text(encoding="utf-8"))
        self.assertIn('league-rank-row', (static/"league.js").read_text(encoding="utf-8"))

    def test_frontend_javascript_has_valid_syntax(self):
        from pathlib import Path
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node runtime not available (GitHub Actions checks parsing)")
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        for name in ("app.js", "house.js", "business.js", "market.js", "league.js"):
            js = static / name
            cp = subprocess.run(["node", "--check", str(js)], capture_output=True,
                                text=True, timeout=15, check=False)
            self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
