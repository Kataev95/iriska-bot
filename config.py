"""Конфигурация бота: всё настраивается через переменные окружения / файл .env."""

from __future__ import annotations

import os
import re
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


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _parse_windows(raw: str) -> tuple[tuple[int, int], ...]:
    """Разбор расписания вида "7-9,13-15,20-21" в окна часов.

    Конец окна не включается: 7-9 означает с 07:00 до 08:59.
    Некорректные куски молча пропускаются. Пустая строка — окон нет.
    """
    windows: list[tuple[int, int]] = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d{1,2})\s*[-–]\s*(\d{1,2})$", part)
        if not m:
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if 0 <= start < end <= 24:
            windows.append((start, end))
    return tuple(windows)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    admin_usernames: frozenset[str]
    admin_contact: str
    db_path: str
    min_msg_len: int
    cooldown_seconds: float
    dedupe_repeats: bool
    messages_per_iriska: int
    withdraw_threshold: int
    bonus_enabled: bool
    bonus_min: int
    bonus_max: int
    streak_max_extra: int
    games_enabled: bool
    casino_min_bet: int
    casino_max_bet: int
    casino_cooldown: float
    duel_min_bet: int
    duel_max_bet: int
    duel_ttl: float
    bonus_hours: tuple[tuple[int, int], ...]
    bonus_hours_mult: int
    announce_bonus_hours: bool
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
        min_msg_len=_int_env("MIN_MSG_LEN", 1),
        cooldown_seconds=_float_env("COOLDOWN_SECONDS", 0.0),
        dedupe_repeats=_bool_env("DEDUPE_REPEATS", False),
        messages_per_iriska=_int_env("MESSAGES_PER_IRISKA", 100),
        withdraw_threshold=_int_env("WITHDRAW_THRESHOLD", 300),
        bonus_enabled=_bool_env("BONUS_ENABLED", True),
        bonus_min=_int_env("BONUS_MIN", 1),
        bonus_max=_int_env("BONUS_MAX", 2),
        streak_max_extra=_int_env("STREAK_MAX_EXTRA", 3),
        games_enabled=_bool_env("GAMES_ENABLED", True),
        casino_min_bet=_int_env("CASINO_MIN_BET", 1),
        casino_max_bet=_int_env("CASINO_MAX_BET", 50),
        casino_cooldown=_float_env("CASINO_COOLDOWN", 30.0),
        duel_min_bet=_int_env("DUEL_MIN_BET", 1),
        duel_max_bet=_int_env("DUEL_MAX_BET", 100),
        duel_ttl=_float_env("DUEL_TTL", 300.0),
        bonus_hours=_parse_windows(
            os.getenv("BONUS_HOURS")
            if os.getenv("BONUS_HOURS") is not None
            else "7-9,13-15,20-21"
        ),
        bonus_hours_mult=_int_env("BONUS_HOURS_MULT", 2),
        announce_bonus_hours=_bool_env("ANNOUNCE_BONUS_HOURS", True),
        tz=ZoneInfo((os.getenv("BOT_TZ") or "Europe/Moscow").strip()),
    )
