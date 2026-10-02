"""Магазин ирисок: каталог товаров и услуг и работа с ним.

Цены по умолчанию заданы здесь, а менять их можно переменной окружения
SHOP_PRICES вида «vpn=350,nuds=12000» (код=цена, через запятую или точку
с запятой). Товар с ценой 0 или меньше пропадает из магазина — так удобно
выключать отдельные лоты, не трогая код.

Модуль не зависит от aiogram: чистые данные и парсинг, которые удобно
проверять тестами.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache


@dataclass(frozen=True)
class ShopItem:
    """Товар или услуга магазина."""

    code: str
    title: str
    emoji: str
    price: int
    summary: str
    aliases: tuple[str, ...] = ()

    def label(self) -> str:
        """Подпись для кнопки и списка: «📸 ИИ-фотосессия»."""
        return f"{self.emoji} {self.title}"


# Каталог по умолчанию. Порядок = порядок в магазине.
DEFAULT_ITEMS: tuple[ShopItem, ...] = (
    ShopItem(
        "vpn", "VPN на месяц", "🛡", 300,
        "Личный VPN-доступ на 30 дней",
        ("впн", "vpn"),
    ),
    ShopItem(
        "brak", "Купить брак", "💍", 200,
        "Оформить брак с участником чата — админ всё устроит",
        ("брак",),
    ),
    ShopItem(
        "aiphoto", "ИИ-фотосессия", "📸", 150,
        "Персональная ИИ-фотосессия по твоим фото",
        ("фото", "фотосессия", "ии фото", "ии-фотосессия", "photo"),
    ),
    ShopItem(
        "compliment", "Комплимент", "🎀", 20,
        "Красивый комплимент от админа в чате",
        ("комплимент", "получить комплимент"),
    ),
    ShopItem(
        "nuds", "Нюдсы админов", "🍑", 10_000,
        "Легендарный лот. Дорого, потому что бесценно 😎",
        ("нюдсы", "нюдс", "nudes", "nuds"),
    ),
    ShopItem(
        "psycho", "Психологический разбор", "🧠", 200,
        "Разбор личности и поведения от админа",
        ("разбор", "психолог", "психологический разбор", "психоразбор"),
    ),
    ShopItem(
        "pleasant", "Сделать приятное", "🤗", 10,
        "Админ сделает тебе приятно — в хорошем смысле!",
        ("приятное", "сделать приятное"),
    ),
)


def norm(value: str | None) -> str:
    """Нормализация кода/алиаса: нижний регистр, ё→е, без пробелов по краям."""
    return (value or "").strip().lower().replace("ё", "е")


def find_item(items: tuple[ShopItem, ...], key: str | None) -> ShopItem | None:
    """Ищет товар по коду или алиасу: «vpn», «VPN», «впн», «/buy брак»."""
    needle = norm(key).lstrip("/").lstrip("@")
    if not needle:
        return None
    for item in items:
        if needle == norm(item.code) or needle in {norm(a) for a in item.aliases}:
            return item
    return None


def parse_price_overrides(raw: str | None) -> dict[str, int]:
    """Разбор SHOP_PRICES: «vpn=350, nuds:12000» -> {'vpn': 350, 'nuds': 12000}.

    Некорректные куски молча пропускаются: магазин важнее строгости.
    """
    overrides: dict[str, int] = {}
    for part in re.split(r"[,;]", raw or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([A-Za-zА-Яа-я0-9_+-]+)\s*[=:]\s*(-?\d+)$", part)
        if not m:
            continue
        overrides[norm(m.group(1))] = int(m.group(2))
    return overrides


def apply_overrides(
    items: tuple[ShopItem, ...], raw: str | None,
) -> tuple[ShopItem, ...]:
    """Применяет цены из SHOP_PRICES и выкидывает лоты с ценой <= 0."""
    overrides = parse_price_overrides(raw)
    result: list[ShopItem] = []
    for item in items:
        price = overrides.get(norm(item.code), item.price)
        if price <= 0:
            continue
        if price != item.price:
            item = replace(item, price=price)
        result.append(item)
    return tuple(result)


@lru_cache(maxsize=8)
def catalog(raw_overrides: str = "") -> tuple[ShopItem, ...]:
    """Каталог с учётом настроек (кэшируется по строке SHOP_PRICES)."""
    return apply_overrides(DEFAULT_ITEMS, raw_overrides)
