"""PETS WORLD 1.9 — purely cosmetic pet studio.

Style unlocks derive from durable server-side gameplay records; the client
cannot supply progress, purchase prices, rewards, or arbitrary CSS classes.
"""
from dataclasses import dataclass

from pet_game import xp_for_level


@dataclass(frozen=True)
class Style:
    icon: str
    title: str
    description: str
    metric: str
    goal: int


FRAMES = {
    "classic": Style("🤍", "Классика", "Уютная рамка для каждого питомца.", "", 0),
    "grove": Style("🌲", "Лесная тропа", "Завершите 3 экспедиции.", "trips", 3),
    "neon": Style("🌈", "Сияние", "Достигните 5-го уровня.", "xp", xp_for_level(5)),
    "atelier": Style("🎨", "Мастерская", "Создайте первый сувенир.", "crafts", 1),
    "champion": Style("🏆", "Чемпион", "Получите один сезонный титул.", "titles", 1),
    "cosmos": Style("🌌", "Космос", "Завершите 10 экспедиций.", "trips", 10),
}

STICKERS = {
    "paw": Style("🐾", "Лапка", "Первый значок доступен всем.", "", 0),
    "compass": Style("🧭", "Компас", "Завершите первый поход.", "trips", 1),
    "heart": Style("💞", "Вместе", "Оба хозяина заботились в 3 разных дня.", "days_both", 3),
    "medal": Style("🎯", "Медаль", "Заберите 8 ежедневных наград.", "daily", 8),
    "crystal": Style("💎", "Сокровище", "Найдите 3 кристалла в походах.", "crystals", 3),
    "crown": Style("👑", "Корона", "Получите сезонный титул.", "titles", 1),
}


def style_entries(catalog: dict[str, Style], metrics: dict[str, int],
                  equipped: str) -> list[dict]:
    result = []
    for code, rule in catalog.items():
        progress = max(0, int(metrics.get(rule.metric, 0)))
        unlocked = progress >= rule.goal
        result.append({
            "code": code, "icon": rule.icon, "title": rule.title,
            "description": rule.description, "progress": min(progress, rule.goal),
            "goal": rule.goal, "unlocked": unlocked, "equipped": equipped == code,
        })
    return result
