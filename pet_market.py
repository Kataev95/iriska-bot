"""PETS WORLD 1.6 — bounded, chat-local marketplace rules.

Listings put real shared-pet materials or manufactured goods into escrow.
Prices are transfers of existing internal iriski, NOT minting.
"""

from pet_inventory import MATERIALS
from pet_business import BUSINESSES

LISTING_TTL_SECONDS = 48 * 3600
MAX_ACTIVE_LISTINGS_PER_PET = 3
MAX_LISTINGS_PER_PET_DAY = 5
MAX_BUYS_PER_PET_DAY = 8
MAX_ITEM_QUANTITY = 5
MAX_UNIT_PRICE = 20

# Only already-produced goods and farmed materials. No crafted trophies,
# pets, XP, rare apparel or other account-bound items can be listed.
ITEM_TYPES = ("material", "good")


def item_details(kind: str, code: str) -> dict | None:
    if kind == "material" and code in MATERIALS:
        obj = MATERIALS[code]
        return {"kind": kind, "code": code, "icon": obj.icon,
                "name": obj.title}
    if kind == "good" and code in BUSINESSES:
        obj = BUSINESSES[code]
        return {"kind": kind, "code": code, "icon": obj.good_icon,
                "name": obj.good_title}
    return None
