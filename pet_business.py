"""PETS WORLD 1.5 — bounded shared production and internal-only iriski sales.

All prices, costs and durations are server-owned. Nothing runs in the background:
on a later visit the player can collect a completed batch using a transaction.
Trading never transfers resources or currency outside this bot.
"""
from dataclasses import dataclass

# Shared for both owners of one pet. Prevents inflation from repeated clicks.
DAILY_PRODUCTION_STARTS = 4
DAILY_SALE_COIN_LIMIT = 12
MAX_BUSINESS_LEVEL = 2


@dataclass(frozen=True)
class Business:
    title: str
    icon: str
    good: str
    good_icon: str
    good_title: str
    sell_price: int
    description: str
    # Material costs of the initial building and then the level-2 upgrade.
    upgrades: tuple[tuple[tuple[str, int], ...], ...]
    # Each production batch consumes these input materials.
    input_materials: tuple[tuple[str, int], ...]
    production_seconds: int
    # Goods produced at level 1 and level 2.
    outputs: tuple[int, int]


BUSINESSES = {
    "florist": Business(
        "Цветочная лавка", "💐", "bouquet", "💐", "Букет", 2,
        "Делайте букеты из листьев и древесины, продавайте их за ириски.",
        (
            (("leaf", 6), ("wood", 4)),
            (("leaf", 10), ("wood", 6), ("stone", 2)),
        ),
        (("leaf", 3), ("wood", 1)), 15 * 60, (2, 3),
    ),
    "carpenter": Business(
        "Столярная лавка", "🪚", "wooden_toy", "🧸", "Игрушка", 3,
        "Изготавливайте деревянные игрушки и торгуйте ими.",
        (
            (("wood", 6), ("stone", 3)),
            (("wood", 10), ("stone", 6), ("crystal", 1)),
        ),
        (("wood", 4), ("stone", 1)), 30 * 60, (2, 3),
    ),
}
