"""PETS WORLD 1.7 — read-only achievements and fair weekly challenge rules.

No new iriski are minted, no inventories are modified and no rewards require
background processing. Milestones are derived from permanent server records.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, time as time_of_day

from pet_game import xp_for_level


# Weekly points are granted ONLY for already durable, server-verified events.
# A user cannot submit arbitrary scores or a timestamp through the Mini App.
WEEKLY_POINTS = {
    "trip": 10,       # A completed, claimed expedition
    "daily": 4,       # A completed, claimed daily quest (max 3/day)
    "batch": 5,       # Goods actually collected (max 4 starts/day)
    "craft": 8,       # One-time souvenir
    "room": 8,        # One-time house room upgrade
    "business": 8,    # One-time business level-up
}


@dataclass(frozen=True)
class Badge:
    title: str
    icon: str
    description: str
    metric: str
    goal: int


BADGES = {
    "first_trip": Badge(
        "Первое приключение", "🧭", "Забрать награду первого похода", "trips", 1
    ),
    "explorer": Badge(
        "Бывалый путешественник", "🗺️", "Завершить 10 походов", "trips", 10
    ),
    "streak": Badge(
        "Неделя вместе", "🔥", "Поддерживать серию 7 дней", "best_streak", 7
    ),
    "streak_30": Badge(
        "Верные друзья", "🤝", "Достичь серии в 30 дней", "best_streak", 30
    ),
    "level_5": Badge(
        "Пятое небо", "⭐", "Достичь 5 уровня", "xp", xp_for_level(5)
    ),
    "maker": Badge(
        "Юный мастер", "🛠️", "Изготовить первый сувенир", "crafts", 1
    ),
    "builder": Badge(
        "Архитектор", "🏡", "Завершить 2 улучшения комнат", "rooms", 2
    ),
    "producer": Badge(
        "Предприниматель", "⚙️", "Забрать 5 готовых партий товаров", "batches", 5
    ),
    "merchant": Badge(
        "Торговец", "🛒", "Продать первый лот на рынке", "sales", 1
    ),
    "daily_star": Badge(
        "Звезда заданий", "🎯", "Забрать 30 ежедневных наград", "daily", 30
    ),
}


def week_bounds(now: float, tz) -> tuple[float, float, str, str]:
    """Week: Monday 00:00 to next Monday 00:00 in the bot timezone.

    Convert local dates independently: correctly handles DST transitions.
    """
    current = datetime.fromtimestamp(now, tz).date()
    monday = current - timedelta(days=current.weekday())
    next_monday = monday + timedelta(days=7)
    begin = datetime.combine(monday, time_of_day.min, tzinfo=tz).timestamp()
    end = datetime.combine(next_monday, time_of_day.min, tzinfo=tz).timestamp()
    return begin, end, monday.isoformat(), next_monday.isoformat()


def badge_entries(metrics: dict[str, int]) -> list[dict]:
    """Fixed catalogue order; no client-provided badge IDs or unlocks."""
    result = []
    for code, definition in BADGES.items():
        progress = max(0, int(metrics.get(definition.metric, 0)))
        result.append({
            "code": code,
            "title": definition.title,
            "icon": definition.icon,
            "description": definition.description,
            "progress": min(definition.goal, progress),
            "goal": definition.goal,
            "unlocked": progress >= definition.goal,
        })
    return result
