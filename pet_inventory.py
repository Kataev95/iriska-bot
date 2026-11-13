"""PETS WORLD 1.3: shared materials and one-time cosmetic workshop trophies.

Values are server-side constants, not browser supplied. Each expedition
guarantees small, route-specific loot. Daily expedition limits already exist
in pet_game_db and apply to obtaining these materials.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Material:
    title: str
    icon: str
    rarity: str
    description: str


@dataclass(frozen=True)
class Recipe:
    title: str
    icon: str
    description: str
    ingredients: tuple[tuple[str, int], ...]


MATERIALS = {
    "leaf": Material("Листочки", "🍃", "common", "Находятся на прогулках в парке."),
    "wood": Material("Древесина", "🪵", "common", "Материал для небольших поделок."),
    "stone": Material("Камень", "🪨", "uncommon", "Прочный материал из леса и руин."),
    "crystal": Material("Кристалл", "💎", "rare", "Редкая находка из древних руин."),
}

# Guaranteed rewards; client cannot reroll or supply amounts.
ROUTE_MATERIALS = MappingProxyType({
    "park": MappingProxyType({"leaf": 2, "wood": 1}),
    "forest": MappingProxyType({"wood": 2, "stone": 1}),
    "ruins": MappingProxyType({"stone": 2, "crystal": 1}),
})

# Crafting is purely cosmetic in this phase; no hidden economic multipliers.
RECIPES = {
    "leaf_crown": Recipe(
        "Венок путешественника", "🌿",
        "Первый сувенир ваших совместных приключений.",
        (("leaf", 4), ("wood", 2)),
    ),
    "camp_lantern": Recipe(
        "Походный фонарь", "🏮",
        "Тёплый огонёк для будущего домика.",
        (("wood", 4), ("stone", 2)),
    ),
    "star_charm": Recipe(
        "Звёздный талисман", "🌟",
        "Редкая памятная вещь из глубин древних руин.",
        (("stone", 4), ("crystal", 2)),
    ),
}


def loot_for(route: str) -> dict[str, int]:
    """Return a fresh dict to avoid changing the server reward template."""
    return dict(ROUTE_MATERIALS.get(route, {}))


def loot_summary(loot: dict[str, int]) -> str:
    return ", ".join(
        f"{MATERIALS[code].icon} +{qty}" for code, qty in loot.items() if qty
    )
