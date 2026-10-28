"""🐾 Питомцы 2.0: уход, настроение, магазин, аксессуары, события.

Чистая логика без Telegram и базы — чтобы легко тестировать.

Состояние питомца — три шкалы 0..100:
  🍖 сытость, 😊 настроение, ⚡ энергия.
Они плавно меняются со временем (считаются «лениво» при каждом обращении):
днём питомец голодает и скучает, ночью (00–08 по BOT_TZ) спит —
энергия восстанавливается, остальное почти не падает.
Переписка пары тоже кормит: каждое сообщение друг другу +сытость, +настроение.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, tzinfo

from pets import species_by_code, stage_for

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "pets")

STAT_MAX = 100.0
NIGHT_END_HOUR = 8  # с 00:00 до 08:00 питомец спит

# Изменение шкал за час: (сытость, настроение, энергия)
DAY_RATE = (-4.0, -3.0, -2.0)
NIGHT_RATE = (-1.0, -0.5, 6.0)
HOUSE_FACTOR = 0.65  # 🏠 домик: голод и скука копятся медленнее
MAX_DECAY_HOURS = 72

# Сколько даёт одно сообщение пары друг другу
CHAT_FOOD = 4.0
CHAT_JOY = 2.0


def clamp(v: float) -> float:
    return max(0.0, min(STAT_MAX, float(v)))


def is_night(hour: int) -> bool:
    return 0 <= hour < NIGHT_END_HOUR


def decay(
    food: float, joy: float, energy: float,
    last_ts: float, now_ts: float, tz: tzinfo, house: bool = False,
) -> tuple[float, float, float]:
    """Шкалы после прошедшего времени (по часовым отрезкам, с учётом ночи)."""
    if last_ts <= 0 or now_ts <= last_ts:
        return clamp(food), clamp(joy), clamp(energy)
    start = max(last_ts, now_ts - MAX_DECAY_HOURS * 3600)
    t = start
    while t < now_ts:
        step = min(3600.0, now_ts - t)
        hour = datetime.fromtimestamp(t + step / 2, tz).hour
        df, dj, de = NIGHT_RATE if is_night(hour) else DAY_RATE
        k = step / 3600.0
        if house:
            df, dj = df * HOUSE_FACTOR, dj * HOUSE_FACTOR
        food = clamp(food + df * k)
        joy = clamp(joy + dj * k)
        energy = clamp(energy + de * k)
        t += step
    return food, joy, energy


def bar(value: float, width: int = 10) -> str:
    filled = int(round(clamp(value) / STAT_MAX * width))
    return "▰" * filled + "▱" * (width - filled)


def stats_lines(food: float, joy: float, energy: float) -> list[str]:
    return [
        f"🍖 {bar(food)} {int(round(food))}%",
        f"😊 {bar(joy)} {int(round(joy))}%",
        f"⚡ {bar(energy)} {int(round(energy))}%",
    ]


def mood_text(food: float, joy: float, energy: float, hour: int) -> str:
    if is_night(hour):
        return "💤 Сладко спит"
    if energy < 15:
        return "🥱 Валится с лап от усталости"
    if food < 30:
        return "🥺 Голодный, смотрит на пустую миску"
    if joy < 30:
        return "😿 Скучает — поиграйте с ним"
    if joy >= 85 and food >= 50:
        return "🥰 Счастлив до небес"
    return "🙂 В хорошем настроении"


def image_key(
    species: str, streak: int, best_streak: int,
    food: float, joy: float, energy: float, hour: int,
    force: str | None = None,
) -> str:
    """Имя картинки (без .jpg) для текущего состояния питомца."""
    if best_streak <= 0:
        return "egg"
    if streak <= 0:
        return "runaway"
    sp = species_by_code(species).code
    if force in ("happy", "sleep", "hungry"):
        return f"{sp}_{force}"
    if is_night(hour) or energy < 15:
        return f"{sp}_sleep"
    if food < 30:
        return f"{sp}_hungry"
    if joy >= 85 and food >= 50:
        return f"{sp}_happy"
    stage = stage_for(streak)
    return f"{sp}_{stage.code if stage else 'baby'}"


def image_path(key: str) -> str | None:
    path = os.path.join(ASSETS_DIR, f"{key}.jpg")
    return path if os.path.isfile(path) else None


# ---------- уход ----------

@dataclass(frozen=True)
class CareAction:
    code: str
    button: str
    cooldown: float  # секунд, у каждого из пары свой
    food: float
    joy: float
    energy: float
    done: str


ACTIONS: dict[str, CareAction] = {
    "feed": CareAction("feed", "🍖 Покормить", 2 * 3600, 30, 5, 0,
                       "уплетает обед за обе щёки 🍖"),
    "play": CareAction("play", "🎾 Поиграть", 3600, -6, 25, -12,
                       "носится с мячиком и визжит от восторга 🎾"),
    "sleep": CareAction("sleep", "🛏 Уложить", 3 * 3600, 0, 3, 45,
                        "свернулся клубочком и сопит 💤"),
}
TOGETHER_WINDOW = 3600  # если партнёр сделал то же самое за последний час
TOGETHER_JOY = 15


def check_action(
    code: str, food: float, joy: float, energy: float, hour: int,
) -> str | None:
    """Текст отказа, если действие сейчас не к месту, иначе None."""
    if code == "feed" and food >= 95:
        return "Он уже наелся — пузико круглое 🙂"
    if code == "play":
        if is_night(hour):
            return "Ночь, он спит — не будите 💤"
        if energy < 15:
            return "Нет сил играть — сначала уложите поспать 🥱"
        if food < 10:
            return "Слишком голодный для игр — покормите 🍖"
    if code == "sleep" and energy >= 90:
        return "Он полон сил и спать не хочет ⚡"
    return None


# ---------- магазин ----------

@dataclass(frozen=True)
class ShopItem:
    code: str
    emoji: str
    title: str
    price: int
    kind: str  # food | wear | freeze | house
    note: str
    food: float = 0
    joy: float = 0


SHOP: tuple[ShopItem, ...] = (
    ShopItem("cookie", "🍪", "Печенье", 1, "food", "+40 сытости", food=40),
    ShopItem("cake", "🍰", "Тортик", 3, "food", "до отвала и +30 настроения",
             food=100, joy=30),
    ShopItem("bow", "🎀", "Бантик", 5, "wear", "аксессуар"),
    ShopItem("glasses", "🕶", "Очки", 8, "wear", "аксессуар"),
    ShopItem("flowers", "🌸", "Венок", 10, "wear", "аксессуар"),
    ShopItem("scarf", "🧣", "Шарф", 12, "wear", "аксессуар"),
    ShopItem("tophat", "🎩", "Цилиндр", 15, "wear", "аксессуар"),
    ShopItem("freeze", "🧊", "Заморозка серии", 15, "freeze",
             "спасёт серию, если пропустите день"),
    ShopItem("house", "🏠", "Домик", 25, "house",
             "голод и скука копятся в 1,5 раза медленнее"),
)
SHOP_BY_CODE = {i.code: i for i in SHOP}
WEARABLES = tuple(i for i in SHOP if i.kind == "wear")
FREEZE_MAX = 2


def parse_set(raw: str | None) -> list[str]:
    return [x for x in (raw or "").split(",") if x in SHOP_BY_CODE]


def join_set(items: list[str]) -> str:
    seen: list[str] = []
    for x in items:
        if x in SHOP_BY_CODE and x not in seen:
            seen.append(x)
    return ",".join(seen)


def worn_line(worn_raw: str | None) -> str:
    worn = parse_set(worn_raw)
    return " ".join(SHOP_BY_CODE[c].emoji for c in worn)


# ---------- события в течение дня ----------

@dataclass(frozen=True)
class EventKind:
    code: str
    emoji: str
    text: str
    buttons: tuple[tuple[str, str], ...]  # (choice, подпись)
    together: bool = False  # нужно, чтобы нажали ОБА


EVENTS: dict[str, EventKind] = {
    "coin": EventKind("coin", "🪙", "нашёл блестящую монетку! Кто первый заберёт?",
                      (("take", "🪙 Забрать"),)),
    "butterfly": EventKind("butterfly", "🦋",
                           "гоняется за бабочкой и зовёт вас обоих! Ловите вместе:",
                           (("catch", "🦋 Ловлю!"),), together=True),
    "parcel": EventKind("parcel", "🎁", "нашёл на пороге загадочную посылку…",
                        (("open", "🎁 Открыть"),)),
    "cold": EventKind("cold", "🤧", "простудился и жалобно чихает…",
                      (("cure", "💊 Вылечить — 2 🍬"),)),
    "snack": EventKind("snack", "🍩", "стащил со стола пончик и смотрит на вас виновато.",
                       (("allow", "😋 Пусть ест"), ("take", "🙅 Отобрать"))),
}
CURE_PRICE = 2


def random_event() -> EventKind:
    return EVENTS[secrets.choice(list(EVENTS))]


def first_event_ts(day_start: datetime) -> float:
    """Первое событие дня: случайно между 10:00 и 13:00."""
    base = day_start.replace(hour=10, minute=0, second=0, microsecond=0)
    return base.timestamp() + secrets.randbelow(3 * 3600)


def next_event_delay() -> float:
    """Пауза до следующего события: 2–5 часов."""
    return 2 * 3600 + secrets.randbelow(3 * 3600)


EVENT_HOURS = (10, 22)  # события приходят только днём
