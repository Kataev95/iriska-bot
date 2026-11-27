"""Server-owned countryside economy. All goods and progress are shared by a pet."""
from dataclasses import dataclass
from secrets import randbelow

FARM_DAILY_PLANTS = 6
FISH_DAILY_CASTS = 3
COUNTRY_DAILY_SALES = 8
PLOT_COUNTS = (2, 3, 4)
WATER_PERCENT = (25, 35, 45)
BITE_WINDOWS = (12, 15, 18)
UPGRADES = {
    "land": ((("wood", 5), ("stone", 2)), (("wood", 8), ("stone", 4))),
    "tool": ((("wood", 4), ("stone", 2)), (("wood", 6), ("stone", 4), ("crystal", 1))),
    "rod": ((("wood", 5), ("leaf", 3)), (("wood", 7), ("stone", 3), ("crystal", 1))),
}

@dataclass(frozen=True)
class Crop:
    name: str
    icon: str
    price: int
    seconds: int
    quantity: int
    sell: int

CROPS = {
    "wheat": Crop("Пшеница", "🌾", 1, 5*60, 2, 1),
    "carrot": Crop("Морковь", "🥕", 1, 15*60, 3, 1),
    "berry": Crop("Клубника", "🍓", 2, 30*60, 3, 2),
}

@dataclass(frozen=True)
class Fish:
    name: str
    icon: str
    rarity: str
    sell: int
    weights: tuple[int, int, int]

FISH = {
    "crucian": Fish("Карась", "🐟", "common", 1, (65, 50, 40)),
    "perch": Fish("Окунь", "🐠", "rare", 2, (25, 32, 35)),
    "pike": Fish("Щука", "🐟", "epic", 3, (8, 14, 18)),
    "goldfish": Fish("Золотая рыбка", "🐠", "legendary", 4, (2, 3, 6)),
    "moonfish": Fish("Лунная рыба", "🌙", "mythic", 5, (0, 1, 1)),
}

def roll_fish(rod: int) -> str:
    weights = [(code, fish.weights[rod-1]) for code, fish in FISH.items()]
    number = randbelow(sum(weight for _, weight in weights))
    for code, weight in weights:
        if number < weight:
            return code
        number -= weight
    raise RuntimeError("Invalid fish weights")

def countryside_config():
    return {
        "crops": [{"code": code, **vars(crop)} for code, crop in CROPS.items()],
        "fish": [{"code": code, "name": fish.name, "icon": fish.icon,
                  "rarity": fish.rarity, "sell": fish.sell, "weights": list(fish.weights)}
                 for code, fish in FISH.items()],
        "plants_daily": FARM_DAILY_PLANTS, "casts_daily": FISH_DAILY_CASTS,
        "sales_daily": COUNTRY_DAILY_SALES, "plot_counts": list(PLOT_COUNTS),
        "water_percent": list(WATER_PERCENT), "bite_windows": list(BITE_WINDOWS),
        "upgrades": {code: [{"costs": [{"code": m, "quantity": q} for m, q in tier]}
                           for tier in tiers] for code, tiers in UPGRADES.items()},
    }
