"""Shared SQLite/Aiogram backend for the first playable PETS Mini App.

All writes reuse existing transaction-protected game_* Database methods.
Never accept user_id/chat_id/balance/rewards from the browser.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

from miniapp.auth import Unauthorized, validate_init_data
from pet_care import ACTIONS, SHOP, SHOP_BY_CODE, check_action, image_key, mood_text, parse_set
from pet_game import (DAILY_START_LIMIT, PROFESSIONS, ROUTES, TASKS,
                      growth_days, level_for, next_level_xp, xp_for_level)
from pets import species_by_code

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
ASSETS = ROOT.parent / "assets" / "pets"
PET_IMAGE_RE = re.compile(r"^[a-z_]+\.jpg$")
ACTIONS_ALLOWED = {"care", "start", "claim", "daily", "buy", "wear", "profession"}


@web.middleware
async def api_errors(request: web.Request, handler):
    try:
        response = await handler(request)
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Unauthorized as exc:
        return web.json_response({"error": str(exc)}, status=401,
                                 headers={"Cache-Control": "no-store"})
    except web.HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        return web.json_response({"error": "Некорректные данные"}, status=400,
                                 headers={"Cache-Control": "no-store"})


def current_user(request):
    cfg = request.app["config"]
    return validate_init_data(request.headers.get("X-Telegram-Init-Data", ""),
                              cfg.bot_token)


async def own_pet(db, user_id: int, pet_id: int):
    row = await db.get_pet(pet_id)
    if row is None or row["status"] != "active" or user_id not in (
        row["user_a"], row["user_b"]
    ):
        raise web.HTTPForbidden(text="Это не твой питомец")
    return row


async def user_pet_list(db, user_id: int):
    cur = await db._require().execute(
        "SELECT * FROM pets WHERE status='active' AND "
        "(user_a=? OR user_b=?) ORDER BY streak DESC, id DESC LIMIT 60",
        (user_id, user_id),
    )
    return list(await cur.fetchall())


def pet_icon(pet):
    if not pet["best_streak"]:
        return "egg"
    return image_key(
        pet["species"], growth_days(pet), int(pet["best_streak"]),
        float(pet["food"]), float(pet["joy"]), float(pet["energy"]),
        datetime.now().hour, None,
    )


async def snapshot(db, cfg, user_id: int, pet_id: int):
    pet = await own_pet(db, user_id, pet_id)
    pet = await db.game_refresh(pet_id, time.time(), cfg.tz)
    now = time.time()
    partner = pet["user_b"] if user_id == pet["user_a"] else pet["user_a"]
    names = await db.user_names(pet["chat_id"], [partner])
    profile = names.get(partner)
    partner_name = (
        (profile["first_name"] or profile["username"] or "Друг")
        if profile is not None else "Друг"
    )
    user = await db.get_user(pet["chat_id"], user_id)
    trip = await db.active_trip(pet_id)
    day = datetime.fromtimestamp(now, cfg.tz).date().isoformat()
    tasks, claimed = await db.daily_state(pet_id, day)
    current_xp = int(pet["xp"])
    next_xp = next_level_xp(current_xp)
    job = PROFESSIONS.get(pet["profession"])
    hour = datetime.fromtimestamp(now, cfg.tz).hour

    # Server-authoritative cooldowns and daily trip quota, not game rules
    # guessed from JavaScript. These are read-only; enforcement still happens
    # inside the existing atomic game_care / start_trip database methods.
    action_rows = await (
        await db._require().execute(
            "SELECT action, ts FROM pet_actions WHERE pet_id=? AND user_id=?",
            (pet_id, user_id),
        )
    ).fetchall()
    last_actions = {row["action"]: float(row["ts"]) for row in action_rows}
    care_state = {
        code: {
            "ready_at": max(0.0, last_actions.get(code, 0) + action.cooldown)
            if code in last_actions else 0.0,
            "blocked_reason": (
                "Сначала дождитесь вылупления питомца"
                if not int(pet["best_streak"]) else
                check_action(code, pet["food"], pet["joy"], pet["energy"], hour)
            ),
        }
        for code, action in ACTIONS.items()
    }
    trip_count = (
        await (
            await db._require().execute(
                "SELECT COUNT(*) FROM pet_trips WHERE user_id=? AND day=?",
                (user_id, day),
            )
        ).fetchone()
    )[0]
    current_image = image_key(
        pet["species"], growth_days(pet), int(pet["best_streak"]),
        float(pet["food"]), float(pet["joy"]), float(pet["energy"]),
        datetime.fromtimestamp(now, cfg.tz).hour, None,
    )
    return {
        "id": pet_id, "name": pet["name"] or species_by_code(pet["species"]).title,
        "species": pet["species"], "stage_days": growth_days(pet),
        "image": f"/assets/pets/{current_image}.jpg",
        "partner": partner_name,
        "xp": current_xp, "level": level_for(current_xp),
        "level_start_xp": xp_for_level(level_for(current_xp)),
        "next_xp": next_xp,
        "server_time": now,
        "mood": mood_text(pet["food"], pet["joy"], pet["energy"], hour),
        "care_state": care_state,
        "trips_remaining": max(0, DAILY_START_LIMIT - int(trip_count)),
        "profession": pet["profession"] or "", "profession_title": job[0] if job else "",
        "streak": int(pet["streak"]), "best_streak": int(pet["best_streak"]),
        "food": round(float(pet["food"])), "joy": round(float(pet["joy"])),
        "energy": round(float(pet["energy"])), "house": bool(pet["house"]),
        "freezes": int(pet["freezes"]), "balance": int(user["balance"]) if user else 0,
        "items": parse_set(pet["items"]), "worn": parse_set(pet["worn"]),
        "daily": [
            {"code": key, "name": val[0], "xp": val[1],
             "done": bool(tasks[key]), "claimed": key in claimed}
            for key, val in TASKS.items()
        ],
        "trip": {
            "id": int(trip["id"]), "route": trip["route"],
            "finish_at": float(trip["finish_at"]), "xp": int(trip["xp"]),
            "coins": int(trip["coins"]),
        } if trip else None,
        "today_fed": pet["last_fed_day"] == day,
        "user_cared": pet["a_day" if user_id == pet["user_a"] else "b_day"] == day,
    }


async def bootstrap(request: web.Request):
    user_id = current_user(request)["user_id"]
    rows = await user_pet_list(request.app["db"], user_id)
    return web.json_response({
        "pets": [
            {"id": int(p["id"]), "name": p["name"] or species_by_code(p["species"]).title,
             "species": p["species"], "streak": int(p["streak"])}
            for p in rows
        ],
        "routes": [
            {"code": key, "name": route.title, "seconds": route.seconds,
             "energy": route.energy, "xp": route.xp, "coins": route.coins}
            for key, route in ROUTES.items()
        ],
        "shop": [
            {"code": item.code, "name": f"{item.emoji} {item.title}",
             "kind": item.kind, "price": item.price, "info": item.note}
            for item in SHOP
        ],
        "professions": [
            {"code": key, "name": val[0], "effect": val[1]}
            for key, val in PROFESSIONS.items()
        ],
        "actions": [
            {"code": code, "name": action.button}
            for code, action in ACTIONS.items()
        ],
    })


async def get_pet(request: web.Request):
    uid = current_user(request)["user_id"]
    pet_id = int(request.match_info["pet_id"])
    return web.json_response(await snapshot(request.app["db"],
                                            request.app["config"], uid, pet_id))


async def action(request: web.Request):
    uid = current_user(request)["user_id"]
    if (request.content_length or 0) > 2048:
        raise web.HTTPRequestEntityTooLarge(max_size=2048, actual_size=request.content_length)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise ValueError("Payload")
    pet_id = int(payload["pet_id"])
    kind = str(payload["kind"])
    code = str(payload.get("code", ""))
    db, cfg = request.app["db"], request.app["config"]
    await own_pet(db, uid, pet_id)
    now = time.time()
    if kind == "care" and code in ACTIONS:
        result = await db.game_care(pet_id, uid, code, now, cfg.tz)
    elif kind == "start" and code in ROUTES:
        result = await db.start_trip(pet_id, uid, code, now, cfg.tz)
    elif kind == "claim":
        result = await db.claim_trip(pet_id, uid, int(payload["trip_id"]), now, cfg.tz)
    elif kind == "daily":
        result = await db.claim_daily(
            pet_id, uid, datetime.fromtimestamp(now, cfg.tz).date().isoformat()
        )
    elif kind == "buy" and code in SHOP_BY_CODE:
        result = await db.game_buy(pet_id, uid, code, now, cfg.tz)
    elif kind == "wear" and code in SHOP_BY_CODE:
        result = "✅ Вещь обновлена" if await db.game_wear(pet_id, uid, code) \
                 else "Вещи нет в гардеробе"
    elif kind == "profession" and code in PROFESSIONS:
        result = await db.choose_profession(pet_id, uid, code)
    else:
        raise ValueError("Unknown action")
    # Do not trust optimistic client state; return the durable DB snapshot.
    return web.json_response({
        "message": result,
        "pet": await snapshot(db, cfg, uid, pet_id),
    })


async def index(request):
    response = web.FileResponse(ROOT / "static" / "index.html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


async def pet_image(request):
    name = request.match_info["filename"]
    if not PET_IMAGE_RE.fullmatch(name):
        raise web.HTTPNotFound()
    path = ASSETS / name
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def create_app(db, config) -> web.Application:
    app = web.Application(middlewares=[api_errors], client_max_size=4096)
    app["db"], app["config"] = db, config
    app.router.add_get("/healthz", lambda request: web.json_response({"ok": True}))
    app.router.add_get("/", index)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get(r"/api/pet/{pet_id:\d+}", get_pet)
    app.router.add_post("/api/action", action)
    app.router.add_get("/assets/pets/{filename}", pet_image)
    app.router.add_static("/static/", ROOT / "static")
    return app
