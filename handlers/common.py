"""Общие фильтры и построители текстов для хендлеров."""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F
from aiogram.enums import ChatType

from config import Config
from db import Database
from texts import days, display_name, fmt, iriski, msgs

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
GroupF = F.chat.type.in_(GROUP_TYPES)


def norm(t: str | None) -> str:
    """Нормализация текста для сравнения с триггерами."""
    return " ".join((t or "").lower().replace("ё", "е").split()).strip(" !?.,")


def trig(words: set[str]):
    """Фильтр: текст сообщения (без учёта регистра/пунктуации) — одно из слов."""
    return F.text.func(lambda t: norm(t) in words)


def is_bonus_hour(hour: int, windows: tuple[tuple[int, int], ...]) -> bool:
    """Попадает ли час в одно из бонусных окон (конец окна не включается)."""
    return any(start <= hour < end for start, end in windows)


def current_window(hour: int, windows: tuple[tuple[int, int], ...]) -> tuple[int, int] | None:
    """Окно, в которое попадает час, или None."""
    for start, end in windows:
        if start <= hour < end:
            return (start, end)
    return None


def next_window_start(hour: int, windows: tuple[tuple[int, int], ...]) -> int | None:
    """Час старта ближайшего окна после текущего часа (с переходом на завтра)."""
    starts = sorted(start for start, _ in windows)
    if not starts:
        return None
    for start in starts:
        if start > hour:
            return start
    return starts[0]


def windows_text(windows: tuple[tuple[int, int], ...]) -> str:
    return ", ".join(f"{start:02d}:00–{end:02d}:00" for start, end in windows)


def today_day(config: Config) -> str:
    return datetime.now(config.tz).strftime("%Y-%m-%d")


def yesterday_day(config: Config) -> str:
    return (datetime.now(config.tz) - timedelta(days=1)).strftime("%Y-%m-%d")


def week_ago_day(config: Config) -> str:
    return (datetime.now(config.tz) - timedelta(days=6)).strftime("%Y-%m-%d")


def current_streak(row, config: Config) -> int:
    """Живая серия бонусов: считается, пока последний бонус был сегодня или вчера."""
    if row["last_bonus_day"] in (today_day(config), yesterday_day(config)):
        return int(row["bonus_streak"] or 0)
    return 0


async def build_profile(db: Database, config: Config, chat_id: int, row) -> str:
    """Карточка статистики участника (используется в /me и админском /who)."""
    user_id = row["user_id"]
    cnt_today = await db.user_count_on(chat_id, user_id, today_day(config))
    cnt_week = await db.user_count_since(chat_id, user_id, week_ago_day(config))
    rank = await db.rank(chat_id, row["total_counted"])
    to_next = max(config.messages_per_iriska - row["progress"], 1)
    balance = row["balance"]

    lines = [
        f"📊 <b>{display_name(row['first_name'], row['username'])}</b>",
        "",
        f"✉️ Сообщений: <b>{fmt(row['total_counted'])}</b> "
        f"(сегодня: {fmt(cnt_today)}, за 7 дней: {fmt(cnt_week)})",
        f"🏆 Место в чате: <b>#{rank}</b>",
        "",
        f"🍬 Ириски: <b>{fmt(balance)}</b> (всего заработано: {fmt(row['earned_total'])})",
        f"⏳ До следующей ириски: {fmt(to_next)} {msgs(to_next)}",
    ]
    streak = current_streak(row, config)
    if streak:
        lines.append(f"🔥 Стрик бонуса: <b>{streak}</b> {days(streak)}")
    if balance >= config.withdraw_threshold:
        lines.append(f"✅ <b>Можно выводить!</b> Пиши {config.admin_contact}")
    else:
        need = config.withdraw_threshold - balance
        lines.append(
            f"📤 До вывода ({fmt(config.withdraw_threshold)}): "
            f"ещё {fmt(need)} {iriski(need)}"
        )
    return "\n".join(lines)
