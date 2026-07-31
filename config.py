"""Конфигурация бота: всё настраивается через переменные окружения / файл .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _ids_env(name: str) -> frozenset[int]:
    raw = (os.getenv(name) or "").replace(";", ",")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part and part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _names_env(name: str, default: str) -> frozenset[str]:
    raw = (os.getenv(name) or default).replace(";", ",")
    return frozenset(
        p.strip().lstrip("@").lower() for p in raw.split(",") if p.strip()
    )


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    admin_usernames: frozenset[str]
    admin_contact: str
    db_path: str
    min_msg_len: int
    cooldown_seconds: float
    messages_per_iriska: int
    withdraw_threshold: int
    tz: ZoneInfo


def load_config() -> Config:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Получи токен у @BotFather и пропиши его "
            "в переменную окружения BOT_TOKEN (или в файл .env)."
        )
    return Config(
        bot_token=token,
        admin_ids=_ids_env("ADMIN_IDS"),
        admin_usernames=_names_env("ADMIN_USERNAMES", "PabloSvytoy"),
        admin_contact=(os.getenv("ADMIN_CONTACT") or "@PabloSvytoy").strip(),
        db_path=(os.getenv("DB_PATH") or "data/iriski.db").strip(),
        min_msg_len=_int_env("MIN_MSG_LEN", 3),
        cooldown_seconds=_float_env("COOLDOWN_SECONDS", 5.0),
        messages_per_iriska=_int_env("MESSAGES_PER_IRISKA", 100),
        withdraw_threshold=_int_env("WITHDRAW_THRESHOLD", 300),
        tz=ZoneInfo((os.getenv("BOT_TZ") or "Europe/Moscow").strip()),
    )
