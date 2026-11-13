"""Validate Telegram Mini App initData, never trust initDataUnsafe or client IDs."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 86400


class Unauthorized(ValueError):
    pass


def validate_init_data(raw: str, token: str, *, now: float | None = None,
                       max_age: int = MAX_AGE_SECONDS) -> dict:
    if not raw or len(raw) > 12000:
        raise Unauthorized("Открой игру через Telegram")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(pairs) != len(data):
            raise Unauthorized("Повторяющиеся поля авторизации")
        given_hash = data.pop("hash")
        # For bot-token HMAC Telegram signs EVERY received field except hash,
        # including the optional "signature" field (Ed25519 third-party proof).
        # Excluding signature here caused "Неверная подпись Telegram" in
        # Telegram clients that send both hash and signature.
        # Only Ed25519 third-party validation excludes BOTH hash and signature.
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, given_hash):
            raise Unauthorized("Неверная подпись Telegram")
        auth_at = int(data["auth_date"])
        current = time.time() if now is None else now
        if auth_at > current + 60 or current - auth_at > max_age:
            raise Unauthorized("Сессия устарела. Открой игру заново")
        user = json.loads(data["user"])
        user_id = user["id"]
        if type(user_id) is not int or user_id < 1:
            raise Unauthorized("Некорректный пользователь")
        return {"user_id": user_id, "name": str(user.get("first_name", "")),
                "start_param": data.get("start_param", "")}
    except Unauthorized:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise Unauthorized("Неверные данные Telegram") from exc


def main_app_link(username: str, pet_id: int | None = None) -> str:
    """Telegram deep link: works in groups, unlike WebAppInfo inline buttons."""
    if not username or not username.replace("_", "").isalnum():
        raise ValueError("Неверное имя Telegram-бота")
    if pet_id is None:
        return f"https://t.me/{username}?startapp"
    if pet_id <= 0:
        raise ValueError("Неверный питомец")
    return f"https://t.me/{username}?startapp=p_{pet_id}"
