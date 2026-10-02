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


_USERNAME_RE = r"[A-Za-z][A-Za-z0-9_]{3,31}"
_CHANNEL_LINK_RE = re.compile(
    rf"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/({_USERNAME_RE})/?(?:\?.*)?$",
    re.IGNORECASE,
)


def parse_channel_ref(raw: str) -> int | str | None:
    """Разбор CHANNEL_ID: числовой ID, @username или ссылка t.me/username.

    - пусто -> None (проверка подписки выключена);
    - «-1001234567890» -> int; положительное число считаем ID без префикса -100
      (так выглядит ID в ссылках вида t.me/c/1234567890/5);
    - «@channel», «channel», «https://t.me/channel» -> «@channel»;
    - всё остальное (например, инвайт-ссылка t.me/+abc) -> ValueError:
      по такой ссылке Telegram не даёт проверить участника, нужен ID.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"-?\d+", raw):
        value = int(raw)
        return int(f"-100{value}") if value > 0 else value
    m = _CHANNEL_LINK_RE.match(raw)
    if m:
        return "@" + m.group(1)
    m = re.fullmatch(rf"@?({_USERNAME_RE})", raw)
    if m:
        return "@" + m.group(1)
    raise ValueError(raw)


def normalize_channel_url(raw: str) -> str:
    """Приводит CHANNEL_URL к виду https://t.me/... (пусто -> пустая строка)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.match(r"^https?://", raw, re.IGNORECASE):
        return raw
    if re.match(r"^(?:www\.)?(?:t|telegram)\.me/", raw, re.IGNORECASE):
        return "https://" + raw
    m = re.fullmatch(rf"@?({_USERNAME_RE})", raw)
    if m:
        return f"https://t.me/{m.group(1)}"
    return raw


def _channel_env(name: str) -> int | str | None:
    raw = (os.getenv(name) or "").strip()
    try:
        return parse_channel_ref(raw)
    except ValueError:
        raise RuntimeError(
            f"Не удалось разобрать {name}={raw!r}. Укажи числовой ID канала "
            "(вида -1001234567890) или @username публичного канала. "
            "Инвайт-ссылка вида t.me/+... не подходит: по ней нельзя проверить "
            "подписку — её можно положить в CHANNEL_URL, а ID взять через @getidsbot."
        ) from None


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
    # Канал, подписка на который нужна для бонуса. None — проверка выключена.
    channel_id: int | str | None = None
    # Ссылка на канал для подсказки «подпишись». Если не задана: для @username
    # строится из него, для числового ID бот пробует узнать её при старте.
    channel_url: str = ""
    # Магазин за ириски.
    shop_enabled: bool = True
    # Кому писать после покупки. Пусто — берётся ADMIN_CONTACT.
    shop_contact: str = ""
    # Переопределение цен: «vpn=350,nuds=12000» (цена <= 0 убирает товар).
    shop_prices: str = ""
    # Присылать ли админам заказы в личку (нужен ADMIN_IDS).
    shop_notify_admins: bool = True


def load_config() -> Config:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Получи токен у @BotFather и пропиши его "
            "в переменную окружения BOT_TOKEN (или в файл .env)."
        )
    admin_contact = (os.getenv("ADMIN_CONTACT") or "@PabloSvytoy").strip()
    channel_id = _channel_env("CHANNEL_ID")
    channel_url = normalize_channel_url(os.getenv("CHANNEL_URL") or "")
    if not channel_url and isinstance(channel_id, str):
        # публичный канал: ссылка однозначно строится из @username, без запросов
        channel_url = f"https://t.me/{channel_id.lstrip('@')}"
    return Config(
        bot_token=token,
        admin_ids=_ids_env("ADMIN_IDS"),
        admin_usernames=_names_env("ADMIN_USERNAMES", "PabloSvytoy"),
        admin_contact=admin_contact,
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
        channel_id=channel_id,
        channel_url=channel_url,
        shop_enabled=_bool_env("SHOP_ENABLED", True),
        shop_contact=(os.getenv("SHOP_CONTACT") or admin_contact).strip(),
        shop_prices=(os.getenv("SHOP_PRICES") or "").strip(),
        shop_notify_admins=_bool_env("SHOP_NOTIFY_ADMINS", True),
    )
