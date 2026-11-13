"""PETS WORLD 1.4: home rooms and bounded, server-authoritative bonuses.

The old paid house remains exactly the same (pets.house from the shop).
Room upgrades are optional material sinks for shared pets. Only two room
features give bounded daily care; the workshop adds guaranteed loot within
the existing trip frequency limit. No passive coins/XP or timers.
"""
from dataclasses import dataclass

from pet_inventory import loot_for


@dataclass(frozen=True)
class Room:
    title: str
    icon: str
    description: str
    upgrades: tuple[tuple[tuple[str, int], ...], ...]
    effects: tuple[str, ...]


ROOMS = {
    "bedroom": Room(
        "Спальня", "🛏️",
        "Тихое место для отдыха. Раз в день восстанавливает энергию питомца.",
        (
            (("wood", 4), ("leaf", 2)),
            (("wood", 8), ("leaf", 4), ("stone", 2)),
        ),
        ("🌙 Раз в день +12 энергии", "🌙 Раз в день +20 энергии"),
    ),
    "garden": Room(
        "Сад", "🌷",
        "Уютный уголок для игр. Раз в день поднимает настроение.",
        (
            (("leaf", 6), ("wood", 2)),
            (("leaf", 10), ("stone", 3), ("crystal", 1)),
        ),
        ("🌸 Раз в день +12 настроения", "🌸 Раз в день +20 настроения"),
    ),
    "workshop": Room(
        "Верстак", "🔨",
        "Улучшение добычи из будущих приключений.",
        (
            (("wood", 6), ("stone", 3)),
            (("wood", 10), ("stone", 5), ("crystal", 2)),
        ),
        ("🪵 Лес: +1 древесина", "🪵 Лес: +1 древесина · 🪨 Руины: +1 камень"),
    ),
}

MAX_ROOM_LEVEL = 2
# Shared, single daily use per room, not one claim per owner.
DAILY_ROOM_CARE = {
    "bedroom": ("energy", (12, 20)),
    "garden": ("joy", (12, 20)),
}


def room_effect(room_code: str, level: int) -> tuple[str, int] | None:
    definition = DAILY_ROOM_CARE.get(room_code)
    if definition is None or level not in (1, 2):
        return None
    return definition[0], definition[1][level - 1]


def extra_loot(route: str, workshop_level: int) -> dict[str, int]:
    """All workshop bonuses are deterministic and limited by the trip limit."""
    result = {}
    if workshop_level >= 1 and route == "forest":
        result["wood"] = 1
    if workshop_level >= 2 and route == "ruins":
        result["stone"] = 1
    return result

def route_loot(route: str, workshop_level: int) -> dict[str, int]:
    """One reward algorithm shared by claim handler and Mini App previews."""
    loot = loot_for(route)
    for material, amount in extra_loot(route, workshop_level).items():
        loot[material] = loot.get(material, 0) + amount
    return loot
