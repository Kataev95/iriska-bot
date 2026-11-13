"""Питомцы 3.0: правила развития без Telegram и SQL."""

from dataclasses import dataclass

MAX_LEVEL = 20
DAILY_START_LIMIT = 3
PROFESSIONS = {
    "explorer": ("Исследователь", "+20% опыта за поход"),
    "artisan": ("Ремесленник", "на 3 меньше энергии для похода"),
    "merchant": ("Торговец", "+1 ириска за поход"),
}
TASKS = {
    "care": ("Позаботиться о питомце", 15),
    "adventure": ("Забрать награду похода", 25),
    "together": ("Оба хозяина позаботились сегодня", 20),
}


@dataclass(frozen=True)
class Route:
    title: str
    seconds: int
    energy: int
    xp: int
    coins: int
    story: str


ROUTES = {
    "park": Route(
        "🌳 Парк",
        15 * 60,
        10,
        35,
        2,
        "Питомец помог садовнику найти потерянные семена.",
    ),
    "forest": Route(
        "🌲 Лес",
        2 * 3600,
        20,
        80,
        4,
        "Питомец прошёл лесную тропу и нашёл старую карту.",
    ),
    "ruins": Route(
        "🏛 Руины",
        8 * 3600,
        30,
        160,
        7,
        "Питомец исследовал мастерскую и разгадал тайну её хозяина.",
    ),
}


def xp_for_level(level: int) -> int:
    return 25 * level * (level - 1)


def level_for(xp: int) -> int:
    return max(level for level in range(1, MAX_LEVEL + 1) if xp >= xp_for_level(level))


def next_level_xp(xp: int) -> int | None:
    level = level_for(xp)
    return xp_for_level(level + 1) if level < MAX_LEVEL else None


def legacy_xp(best: int) -> int:
    level = 20 if best >= 100 else 10 if best >= 30 else 5 if best >= 7 else 1
    return xp_for_level(level)


def growth_days(pet) -> int:
    xp = int(pet["xp"]) if "xp" in pet.keys() else 0  # noqa: SIM118 — sqlite3.Row membership tests values
    level = level_for(xp)
    growth = 100 if level >= 20 else 30 if level >= 10 else 7 if level >= 5 else 1
    return max(int(pet["best_streak"]), growth) if int(pet["best_streak"]) > 0 else 0
