"""Mini App authorization and real SQLite integration tests without Telegram token."""

import asyncio
import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime
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
        self.assertEqual(pet["world_hour"], datetime.fromtimestamp(pet["server_time"], self.config.tz).hour)
        self.assertFalse(pet["partner_wrote_today"])
        self.assertEqual(pet["portrait_reactions"], {"happy": "/assets/pets/dragon_happy.jpg", "sleep": "/assets/pets/dragon_sleep.jpg"})
        day = datetime.now(self.config.tz).date().isoformat()
        await self.db._require().execute("UPDATE pets SET b_day=? WHERE id=1", (day,))
        await self.db._require().commit()
        response = await self.client.get("/api/pet/1", headers=self.headers)
        self.assertTrue((await response.json())["partner_wrote_today"])

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

    async def test_tycoon_endpoint_is_authorized_and_server_timed(self):
        await self.db._require().executemany(
            "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?)",
            [(1,"wood",30),(1,"leaf",30),(1,"stone",10)],
        )
        await self.db._require().commit()
        config=await (await self.client.get("/api/bootstrap",headers=self.headers)).json()
        self.assertEqual(config["tycoon_rules"]["tap_seconds"],[60,90])
        self.assertEqual(config["tycoon_rules"]["max_taps"],30)
        for payload in (
            {"kind":"business_build","code":"florist","expected_level":0},
            {"kind":"tycoon_hire","code":"florist","expected_level":0},
            {"kind":"business_start","code":"florist"},
        ):
            r=await self.client.post("/api/action",headers=self.headers,json={"pet_id":1,**payload})
            data=await r.json()
            self.assertTrue(data["message"].startswith("✅"),data)
        job=data["pet"]["businesses"]["units"]["florist"]["job"]
        self.assertEqual(data["pet"]["businesses"]["units"]["florist"]["workers"],1)
        payload={"pet_id":1,"code":"florist","job_id":job["id"],"expected_taps":0,
                 "seconds":100000,"quantity":10000,"coins":100000,"user_id":201}
        self.assertEqual((await self.client.post("/api/tycoon/tap",json=payload)).status,401)
        self.assertEqual((await self.client.post("/api/tycoon/tap",json=payload,
            headers={"X-Telegram-Init-Data":signed(201)})).status,403)
        forged=await self.client.post("/api/tycoon/tap",headers=self.headers,
            json={**payload,"expected_taps":True})
        self.assertFalse((await forged.json())["accepted"])
        first=await (await self.client.post("/api/tycoon/tap",headers=self.headers,json=payload)).json()
        self.assertTrue(first["accepted"])
        self.assertEqual(first["job"]["finish_at"],job["finish_at"]-60)
        self.assertEqual(first["job"]["taps"],1)
        self.assertEqual(first["job"]["quantity"],2)
        again=await (await self.client.post("/api/tycoon/tap",headers=self.headers,json=payload)).json()
        self.assertFalse(again["accepted"])
        self.assertEqual(again["job"],first["job"])
        partner=await (await self.client.get("/api/pet/1",
            headers={"X-Telegram-Init-Data":signed(102)})).json()
        self.assertEqual(partner["businesses"]["units"]["florist"]["job"],first["job"])
        self.assertEqual(partner["businesses"]["units"]["florist"]["stock"],0)
        self.assertEqual((await self.db.get_user(CHAT_ID,101))["balance"],20)
        await self.db._require().execute("UPDATE pet_business_jobs SET finish_at=? WHERE id=?",
                                        (time.time()-1,job["id"]))
        await self.db._require().commit()
        r=await self.client.post("/api/action",headers=self.headers,
            json={"kind":"business_collect","pet_id":1,"code":"florist","job_id":job["id"]})
        self.assertEqual((await r.json())["pet"]["businesses"]["units"]["florist"]["stock"],2)
        r=await self.client.post("/api/action",headers=self.headers,
            json={"kind":"tycoon_order","pet_id":1,"code":"florist","expected_stock":2,"coins":999999})
        data=await r.json()
        self.assertEqual(data["pet"]["balance"],24)
        self.assertEqual(data["pet"]["businesses"]["sales_coins_remaining"],8)
        self.assertTrue(data["pet"]["businesses"]["units"]["florist"]["order_done"])
        self.assertEqual(data["pet"]["businesses"]["units"]["florist"]["stock"],0)

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

    async def test_private_season_claim_and_cosmetic_equip(self):
        from datetime import datetime
        from pet_season import season_bounds
        self.assertEqual((await self.client.get("/api/season/1")).status, 401)
        self.assertEqual((await self.client.get("/api/season/2",
            headers=self.headers)).status, 403)
        self.assertEqual((await self.client.get("/api/season/1",
            headers={"X-Telegram-Init-Data": signed(201)})).status, 403)
        result=await self.client.get("/api/season/1",headers=self.headers)
        self.assertEqual(result.status,200)
        initial=await result.json()
        self.assertEqual(len(initial["challenges"]),5)
        self.assertEqual(initial["titles"],[])
        self.assertIn("seconds_left",initial["season"])
        before=await self.db.get_user(CHAT_ID,101)
        # Forgeries cannot bypass the current server-side season goals.
        premature=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"season_claim","code":"star","score":999999})
        self.assertEqual(premature.status,200)
        self.assertIn("🔒",(await premature.json())["message"])
        outsider=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":2,"kind":"season_claim","code":"explorer"})
        self.assertEqual(outsider.status,403)
        now=time.time()
        _,_,start_day,_,_=season_bounds(now,self.config.tz)
        day=datetime.fromtimestamp(now,self.config.tz).date().isoformat()
        for i in range(3):
            await self.db._require().execute(
                "INSERT INTO pet_trips"
                "(pet_id,user_id,day,route,started_at,finish_at,xp,coins,claimed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (1,101,day,"park",now-500-i,now-5,35,2,now),
            )
        await self.db._require().commit()
        claim=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"season_claim","code":"explorer"})
        self.assertEqual(claim.status,200)
        self.assertIn("Получен титул",(await claim.json())["message"])
        repeated=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"season_claim","code":"explorer"})
        self.assertIn("уже получена",(await repeated.json())["message"])
        equip=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"season_equip","code":"explorer",
                  "season_start":start_day,"currency":9999})
        self.assertEqual(equip.status,200)
        equipped=await equip.json()
        self.assertIn("Следопыт",equipped["pet"]["season_title"])
        my_season=await self.client.get("/api/season/1",headers=self.headers)
        progress=await my_season.json()
        self.assertEqual(progress["claimed_count"],1)
        self.assertTrue(progress["titles"][0]["equipped"])
        after=await self.db.get_user(CHAT_ID,101)
        self.assertEqual((before["balance"],before["earned_total"]),
                         (after["balance"],after["earned_total"]))

    async def test_weekly_chapter_shared_progress_and_one_time_reward(self):
        async def play(kind, code, uid=101, **extra):
            response = await self.client.post(
                "/api/action", headers={"X-Telegram-Init-Data": signed(uid)},
                json={"pet_id": 1, "kind": kind, "code": code, **extra},
            )
            self.assertEqual(response.status, 200)
            return await response.json()

        boot = await self.client.get("/api/bootstrap", headers={
            "X-Telegram-Init-Data": signed(101, start_param=f"week_{-CHAT_ID}"),
        })
        self.assertEqual((await boot.json())["chapter_pet_id"], 1)
        foreign = await self.client.post(
            "/api/action", headers={"X-Telegram-Init-Data": signed(201)},
            json={"pet_id": 1, "kind": "chapter_finish", "code": "forest_trail"},
        )
        self.assertEqual(foreign.status, 403)
        locked = await play("studio_equip", "trailkeeper", slot="frame")
        self.assertIn("🔒", locked["message"])
        chapter = locked["pet"]["chapter"]["code"]
        invalid = await play("chapter_start", "forest", chapter=chapter)
        self.assertIsNone(invalid["pet"]["trip"])
        started = await play("chapter_start", "park", chapter=chapter)
        trip_id = started["pet"]["chapter"]["trip_id"]
        self.assertEqual(started["pet"]["trip"]["id"], trip_id)
        premature = await play("chapter_craft", chapter, uid=102)
        self.assertEqual(premature["pet"]["chapter"]["stage"], 0)
        await self.db._require().execute(
            "UPDATE pet_trips SET finish_at=? WHERE id=?", (time.time() - 1, trip_id),
        )
        await self.db._require().commit()
        claimed = await play("claim", "", uid=102, trip_id=trip_id)
        self.assertEqual(claimed["pet"]["chapter"]["stage"], 1)
        self.assertEqual(claimed["pet"]["materials"]["wood"], 1)
        results = await asyncio.gather(
            play("chapter_craft", chapter), play("chapter_craft", chapter, uid=102),
        )
        self.assertEqual(sum(r["message"].startswith("✅") for r in results), 1)
        self.assertTrue(all(r["pet"]["materials"]["wood"] == 0 for r in results))

        finished = await play("chapter_finish", chapter)
        self.assertEqual(finished["pet"]["chapter"]["stage"], 3)
        self.assertTrue(finished["pet"]["chapter"]["shared"])
        repeat = await play("chapter_finish", chapter)
        self.assertEqual(repeat["message"], "Глава уже завершена")
        equipped = await play("studio_equip", "trailkeeper", uid=102, slot="frame")
        self.assertEqual(equipped["pet"]["style"]["frame"], "trailkeeper")
        state = await self.db.studio_state(1, 101, self.config.tz)
        pages = [e for e in state["album"] if e["kind"] == "chapter"]
        self.assertEqual([e["icon"] for e in pages], ["💞"])

        self.assertTrue(await self.db.reserve_chapter_announcement(CHAT_ID))
        self.assertFalse(await self.db.reserve_chapter_announcement(CHAT_ID))
        day = "2099-01-01"
        stats = await self.db.chapter_stats(CHAT_ID, day)
        self.assertEqual((stats["started"], stats["first_step"], stats["finished"],
                          stats["shared"], stats["d1_total"], stats["d7_total"]),
                         (1, 1, 1, 1, 1, 1))
        self.assertEqual((stats["d1_returned"], stats["d7_returned"]), (0, 0))
        await self.db._require().execute(
            "INSERT INTO pet_daily_activity SELECT pet_id,DATE(day,'+1 day'),user_id,'care' "
            "FROM pet_trips WHERE id=?", (trip_id,),
        )
        stats = await self.db.chapter_stats(CHAT_ID, day)
        self.assertEqual((stats["d1_returned"], stats["d7_returned"]), (1, 0))

    async def test_private_studio_album_and_verified_equip(self):
        self.assertEqual((await self.client.get("/api/studio/1")).status,401)
        forbidden=await self.client.get("/api/studio/2",headers=self.headers)
        self.assertEqual(forbidden.status,403)
        forbidden=await self.client.get("/api/studio/1",
            headers={"X-Telegram-Init-Data":signed(201)})
        self.assertEqual(forbidden.status,403)
        state=await self.client.get("/api/studio/1",headers=self.headers)
        self.assertEqual(state.status,200)
        info=await state.json()
        self.assertEqual(info["equipped"]["frame"],"classic")
        self.assertEqual(info["equipped"]["sticker"],"paw")
        self.assertEqual(info["album"][0]["kind"],"origin")
        before=await self.db.get_user(CHAT_ID,101)
        forged=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"studio_equip","slot":"frame","code":"cosmos",
                  "trips":999999,"unlocked":True,"balance":9000})
        self.assertEqual(forged.status,200)
        self.assertIn("🔒",(await forged.json())["message"])
        invalid=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"studio_equip","slot":"frame",
                  "code":"classic; background:red"})
        self.assertIn("⚠️",(await invalid.json())["message"])
        forbidden=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":2,"kind":"studio_equip","slot":"frame","code":"classic"})
        self.assertEqual(forbidden.status,403)
        now=time.time()
        day=__import__("datetime").datetime.fromtimestamp(now,self.config.tz).date().isoformat()
        for i in range(3):
            await self.db._require().execute(
                "INSERT INTO pet_trips"
                "(pet_id,user_id,day,route,started_at,finish_at,xp,coins,claimed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (1,101,day,"park",now-500-i,now-100,35,2,now-i),
            )
        await self.db._require().commit()
        equipped=await self.client.post("/api/action",headers=self.headers,
            json={"pet_id":1,"kind":"studio_equip","slot":"frame",
                  "code":"grove"})
        self.assertEqual(equipped.status,200)
        data=await equipped.json()
        self.assertIn("✅",data["message"])
        self.assertEqual(data["pet"]["style"]["frame"],"grove")
        profile=await self.client.get("/api/pet/1",headers=self.headers)
        self.assertEqual((await profile.json())["style"]["frame"],"grove")
        album=await self.client.get("/api/studio/1",headers=self.headers)
        updated=await album.json()
        self.assertEqual(sum(x["kind"]=="trip" for x in updated["album"]),3)
        after=await self.db.get_user(CHAT_ID,101)
        self.assertEqual((before["balance"],before["earned_total"]),
                         (after["balance"],after["earned_total"]))

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
        self.assertIn("/static/style.css?v=6", html)
        self.assertIn("/static/house.css?v=1", html)
        self.assertIn("/static/house.js?v=2", html)
        self.assertIn("/static/app.js?v=17", html)
        self.assertIn("/static/world-map.js?v=1", html)
        self.assertIn("/static/world-map.css?v=1", html)
        self.assertIn("/static/pet-life.js?v=1", html)
        self.assertIn("/static/pet-life.css?v=1", html)
        self.assertIn("/static/world-ui.js?v=1", html)
        self.assertIn("/static/world.css?v=1", html)
        self.assertIn("/static/navigation.js?v=1", html)
        self.assertIn("/static/studio.js?v=3", html)
        self.assertIn("/static/studio.css?v=2", html)
        self.assertIn("/static/season.js?v=1", html)
        self.assertIn("/static/season.css?v=1", html)
        self.assertIn("/static/league.js?v=2", html)
        self.assertIn("/static/league.css?v=1", html)
        self.assertIn("/static/market.css?v=1", html)
        self.assertIn("/static/market.js?v=1", html)
        self.assertIn("/static/business.css?v=2", html)
        self.assertIn("/static/business.js?v=3", html)


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
        self.assertIn("worldMap.renderMap(p,serverNow(),ui.config", js)
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
        self.assertIn('window.PetsSeason.renderSeason', js)
        self.assertIn('season-hero', (static/"season.css").read_text(encoding="utf-8"))
        self.assertIn('data-season-claim=', (static/"season.js").read_text(encoding="utf-8"))
        self.assertIn('data-season-equip=', (static/"season.js").read_text(encoding="utf-8"))
        self.assertIn('window.PetsStudio.renderStudio', js)
        self.assertIn('studio-preview', (static/"studio.css").read_text(encoding="utf-8"))
        self.assertIn('data-studio-equip=', (static/"studio.js").read_text(encoding="utf-8"))
        self.assertIn('studio-memory', (static/"studio.js").read_text(encoding="utf-8"))
        self.assertIn('studio-hero-sticker', js)

    def test_navigation_history_and_root_ownership(self):
        from pathlib import Path
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node runtime not available")
        script = Path(__file__).resolve().parent / "miniapp" / "static" / "navigation.js"
        code = """
const assert = require("node:assert/strict");
const navigation = require(process.argv[1]);
assert.deepEqual(navigation.tabs.map(x => x[0]), ["home","world","games","trade","club"]);
const nav = navigation.create();
assert.equal(nav.current(), "home");
assert.equal(nav.open("profile"), "profile");
assert.equal(nav.open("studio"), "studio");
assert.equal(nav.activeRoot(), "home");
assert.equal(nav.back(), "profile");
assert.equal(nav.back(), "home");
assert.equal(nav.select("world"), "world");
assert.equal(nav.open("trips"), "trips");
assert.equal(nav.open("inventory"), "inventory");
assert.equal(nav.activeRoot(), "world");
assert.equal(nav.back(), "trips");
assert.equal(nav.open("studio"), "studio");
assert.equal(nav.activeRoot(), "home");
assert.equal(nav.back(), "trips");
assert.equal(nav.select("club"), "club");
assert.equal(nav.open("season"), "season");
assert.equal(nav.open("league"), "league");
assert.equal(nav.activeRoot(), "club");
assert.equal(nav.open("season"), "season");
assert.equal(nav.back(), "club");
assert.equal(nav.back(), "home");
assert.equal(nav.select("games"), "games");
assert.equal(nav.open("business"), "business");
assert.equal(nav.activeRoot(), "games");
assert.equal(nav.select("trade"), "trade");
assert.equal(nav.open("market"), "market");
assert.equal(nav.activeRoot(), "trade");
assert.equal(nav.open("unknown"), "market");
assert.equal(nav.back(), "trade");
assert.equal(nav.back(), "home");
"""
        cp = subprocess.run(["node", "-e", code, str(script)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_world_ui_visual_rules(self):
        from pathlib import Path
        import shutil
        import subprocess
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        css = (static / "world.css").read_text(encoding="utf-8")
        js = (static / "app.js").read_text(encoding="utf-8")
        self.assertIn("motion-paused", css)
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertIn("animation-play-state:paused", css)
        self.assertIn("if(motionHidden()) return;", js)
        self.assertIn("design.phase(p.world_hour)", js)
        self.assertIn("design.landscape()", js)
        self.assertIn("pet-portrait studio-frame", js)
        self.assertIn("partner_wrote_today", js)
        self.assertIn('max="100" value="', js)
        self.assertNotIn("backdrop-filter:blur", css)
        for name in ("app.js", "house.js", "business.js", "league.js", "studio.js"):
            self.assertNotIn('style="width:', (static / name).read_text(encoding="utf-8"))
        if not shutil.which("node"):
            self.skipTest("Node runtime not available")
        script = static / "world-ui.js"
        code = """
const assert = require("node:assert/strict");
const d = require(process.argv[1]);
assert.equal(d.phase(0), "night");
assert.equal(d.phase(7), "night");
assert.equal(d.phase(8), "morning");
assert.equal(d.phase(11), "day");
assert.equal(d.phase(18), "evening");
assert.equal(d.phase(22), "night");
assert.equal(d.phase(null), "day");
assert.equal(d.phase(24), "day");
assert.equal(d.percent(-5), 0);
assert.equal(d.percent(103), 100);
assert.equal(d.percent(NaN), 0);
assert.match(d.needs(21,"Energy","bolt"), /is-low/);
assert.match(d.needs(40,"Energy","bolt"), /is-medium/);
assert.match(d.needs(83,"Energy","bolt"), /value="83"/);
assert.doesNotMatch(d.needs(83,"Energy","bolt"), /style=/);
assert.match(d.landscape(), /scene-hill-far/);
assert.match(d.routeIcon("world"), /<svg/);
assert.doesNotMatch(d.routeIcon("<script>"), /<script>/);
"""
        cp = subprocess.run(["node", "-e", code, str(script)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_pet_life_is_cosmetic_and_cancels_stale_reactions(self):
        from pathlib import Path
        import shutil
        import subprocess
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        js = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "pet-life.css").read_text(encoding="utf-8")
        life = (static / "pet-life.js").read_text(encoding="utf-8")
        self.assertIn('if(reactPet(ui.pet,"pat"))', js)
        self.assertIn("careConfirmed&&reactPet(after,code,true)", js)
        self.assertIn("reactionGate.current(token,p.id)", js)
        self.assertIn("scene.dataset.markup!==sceneMarkup", js)
        self.assertIn('TG?.onEvent?.("deactivated"', js)
        self.assertNotIn("offsetWidth", js)
        self.assertNotIn("fetch(", life)
        self.assertIn("prefers-reduced-motion:reduce", css)
        for motion in ("pet-breathe", "egg-rock", "pet-snooze", "pet-feed", "pet-play", "pet-settle"):
            self.assertIn("@keyframes " + motion, css)
        if not shutil.which("node"):
            self.skipTest("Node runtime not available")
        script = static / "pet-life.js"
        code = """
const assert = require("node:assert/strict");
const d = require(process.argv[1]);
const p = {id:1,best_streak:2,joy:80,image:"/assets/pets/dragon_baby.jpg",
  portrait_reactions:{happy:"/assets/pets/dragon_happy.jpg",sleep:"/assets/pets/dragon_sleep.jpg"}};
assert.equal(d.pose(p),"idle");
assert.equal(d.pose({...p,best_streak:0}),"egg");
assert.equal(d.pose({...p,image:"/assets/pets/dragon_sleep.jpg"}),"sleep");
assert.equal(d.pose({...p,image:"/assets/pets/dragon_hungry.jpg"}),"hungry");
assert.equal(d.pose({...p,image:"/assets/pets/dragon_happy.jpg"}),"happy");
assert.equal(d.pose({...p,joy:15}),"bored");
assert.equal(d.reaction(p,"feed"),null);
assert.equal(d.reaction(p,"play",true).motion,"play");
assert.equal(d.reaction(p,"sleep",true).expression,"sleep");
assert.equal(d.reaction({...p,best_streak:0},"feed",true),null);
assert.equal(d.reaction({...p,best_streak:0},"pat").motion,"egg");
assert.equal(d.reaction({...p,image:"/assets/pets/dragon_sleep.jpg"},"pat").motion,"quiet");
assert.equal(d.reaction({...p,image:"/assets/pets/dragon_hungry.jpg"},"pat").expression,undefined);
assert.equal(d.reaction({...p,image:"/assets/pets/dragon_hungry.jpg"},"play",true).expression,undefined);
assert.equal(d.expressionPath(p,"happy"),"/assets/pets/dragon_happy.jpg");
assert.equal(d.expressionPath({portrait_reactions:{happy:"https://bad.example/x.jpg"}},"happy"),null);
assert.equal(d.expressionPath(p,"toString"),null);
const g=d.createGate();
const a=g.begin(1,100,1500);
assert.equal(g.begin(1,150,1500),null);
assert.equal(g.current(a,1),true);
assert.equal(g.current(a,2),false);
const b=g.begin(1,200,1500,true);
assert.notEqual(a,b);
assert.equal(g.finish(a),false);
assert.equal(g.current(b,1),true);
g.reset();
assert.equal(g.current(b,1),false);
assert.equal(g.finish(b),false);
const c=g.begin(2,300,1500);
assert.equal(g.finish(c),true);
assert.equal(g.begin(2,301,1500)>c,true);
"""
        cp = subprocess.run(["node", "-e", code, str(script)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_tycoon_frontend_countdown_and_worker_rules(self):
        from pathlib import Path
        import shutil
        import subprocess
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        js = (static / "app.js").read_text(encoding="utf-8")
        business = (static / "business.js").read_text(encoding="utf-8")
        self.assertIn('api("/api/tycoon/tap"', js)
        self.assertIn("expected_taps:job.taps||0", js)
        self.assertIn("expected_stock:expectedStock", js)
        self.assertIn('data-tycoon-hire=', business)
        self.assertIn('data-tycoon-order=', business)
        self.assertIn('data-tycoon-tap=', business)
        if not shutil.which("node"):
            self.skipTest("Node runtime not available")
        script = static / "business.js"
        code = """
const assert=require("node:assert/strict");
global.window={};
require(process.argv[1]);
const t=window.PetsBusiness, rules={max_taps:30,tap_seconds:[60,90],worker_duration_percent:[100,75,50]};
const job={id:1,taps:2,finish_at:100,tap_ready_at:50};
assert.equal(t.workState(job,rules,49).canTap,false);
assert.equal(t.workState(job,rules,50).canTap,true);
assert.equal(t.workState(job,rules,100).ready,true);
assert.equal(t.workState({...job,taps:30},rules,50).canTap,false);
assert.equal(t.workerDuration({production_seconds:900},{workers:2},rules),450);
assert.equal(t.duration(75),"1 мин 15 с");
const unit={code:"florist",name:"Лавка",icon:"💐",good_icon:"💐",good_title:"Букет",description:"Тест",sell_price:2,production_seconds:900,outputs:[2,3],inputs:[],tiers:[{costs:[]},{costs:[]}],worker_tiers:[{duration_percent:75,costs:[]}]};
const p={best_streak:2,businesses:{starts_remaining:3,sales_coins_remaining:12,units:{florist:{level:1,workers:0,stock:2,job}}}};
const c={businesses:[unit],materials:[],tycoon_rules:rules};
let html=t.renderBusiness(p,c,60,"florist");
assert.match(html,/data-tycoon-tap="florist"/);
assert.match(html,/data-tycoon-hire="florist"/);
assert.match(html,/data-tycoon-order="florist"/);
assert.match(html,/data-business-collect="florist"[^>]*disabled/);
html=t.renderBusiness(p,c,101,"florist");
assert.match(html,/data-tycoon-tap="florist"[^>]*disabled/);
assert.match(html,/Забрать партию/);
p.businesses.units.florist.order_done=true;
html=t.renderBusiness(p,c,101,"florist");
assert.match(html,/data-tycoon-order="florist"[^>]*disabled[^>]*>✓ Выполнен/);
"""
        cp = subprocess.run(["node", "-e", code, str(script)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_world_map_routes_and_snapshot_statuses(self):
        from pathlib import Path
        import shutil
        import subprocess
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        js = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "world-map.css").read_text(encoding="utf-8")
        self.assertIn("worldMap.valid(id)", js)
        self.assertIn("goToTab(place.route)", js)
        self.assertIn("ui.businessCode=status.businessCode", js)
        self.assertIn("ui.pet?.id!==snapshot.id", js)
        self.assertIn("syncWorldMap()", js)
        self.assertIn("aria-pressed", js)
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertIn("repeat(3,minmax(0,1fr))", css)
        if not shutil.which("node"):
            self.skipTest("Node runtime not available")
        script = static / "world-map.js"
        navigation = static / "navigation.js"
        code = """
const assert=require("node:assert/strict");
const m=require(process.argv[1]), n=require(process.argv[2]);
const p={id:1,best_streak:2,house:true,world_hour:12,chapter:{stage:3},
  businesses:{units:{florist:{level:1,stock:2,order_done:false},carpenter:{level:1,stock:0,job:{finish_at:120}}},sales_coins_remaining:12}};
const c={businesses:[{code:"florist",sell_price:2},{code:"carpenter",sell_price:3}],tycoon_rules:{order_quantity:2}};
assert.equal(m.places.length,9);
assert.equal(new Set(m.places.map(x=>x.id)).size,9);
assert.equal(m.valid("__proto__"),false);
assert.equal(m.valid("unknown"),false);
for(const place of m.places){
  const html=m.renderDetail(place.id,p,100,c);
  if(place.route){
    const nav=n.create(); nav.select("world");
    assert.equal(nav.open(place.route),place.route);
    assert.equal(nav.back(),"world");
    assert.match(html,/data-map-open=/);
  } else {
    assert.doesNotMatch(html,/data-map-open=/);
    assert.match(html,/В РАЗРАБОТКЕ/);
  }
}
assert.equal(m.status(m.get("house"),p,100,c).text,"Ваш дом");
assert.equal(m.status(m.get("house"),{...p,house:false},100,c).text,"Построить");
assert.equal(m.status(m.get("forest"),{...p,trip:{finish_at:101}},100,c).tone,"active");
assert.equal(m.status(m.get("forest"),{...p,trip:{finish_at:100}},100,c).tone,"reward");
assert.equal(m.status(m.get("business"),p,100,c).text,"В работе");
assert.equal(m.status(m.get("business"),p,120,c).businessCode,"carpenter");
assert.equal(m.recommended(p,120,c),"business");
assert.equal(m.recommended({...p,trip:{finish_at:99}},120,c),"forest");
const order={...p,businesses:{...p.businesses,units:{florist:{level:1,stock:2,order_done:false}}}};
assert.equal(m.status(m.get("business"),order,120,c).text,"Заказ готов");
assert.equal(m.status(m.get("business"),{...order,businesses:{...order.businesses,sales_coins_remaining:0}},120,c).tone,"open");
const html=m.renderMap(p,120,c,"night");
assert.equal((html.match(/data-map-location=/g)||[]).length,9);
assert.match(html,/map-phase-night/);
assert.match(html,/tone-reward/);
assert.doesNotMatch(html,/style=/);
assert.doesNotMatch(m.renderMap(p,100,c,'"><script>'),/<script>/);
"""
        cp = subprocess.run(["node", "-e", code, str(script), str(navigation)], capture_output=True,
                            text=True, timeout=15, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_frontend_javascript_has_valid_syntax(self):
        from pathlib import Path
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("Node runtime not available (GitHub Actions checks parsing)")
        static = Path(__file__).resolve().parent / "miniapp" / "static"
        for name in ("world-map.js", "pet-life.js", "world-ui.js", "navigation.js", "app.js", "house.js", "business.js", "market.js", "league.js", "season.js", "studio.js"):
            js = static / name
            cp = subprocess.run(["node", "--check", str(js)], capture_output=True,
                                text=True, timeout=15, check=False)
            self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
