"""🐾 Питомцы 2.0: карточка с фото и кнопками, уход, магазин, гардероб, события.

- «питомец» / «мои питомцы» / /pets — карточка питомца: фото по состоянию
  (малыш/подросток/взрослый/легенда, голодный, спит, счастлив), шкалы
  сытости, настроения и энергии, серия, надетые аксессуары;
- кнопки под карточкой меняют то же сообщение — чат не засоряется;
- нажимать могут только хозяева питомца;
- 2 раза в день (10:00–22:00) у питомца случается событие с кнопками.

Картинки лежат в assets/pets/{ключ}.jpg; после первой отправки бот
запоминает file_id Telegram и дальше не загружает файл заново.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime

from rich_ui import reply_rich, send_rich
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyParameters,
)

from config import Config
from db import Database
from handlers.common import GroupF, trig, yesterday_day
from handlers.pets import LIST_TRIGGERS, _plain, _who, rules_text
from pet_care import (
    ACTIONS,
    CHAT_FOOD,
    CHAT_JOY,
    CURE_PRICE,
    EVENT_HOURS,
    EVENTS,
    FREEZE_MAX,
    SHOP,
    SHOP_BY_CODE,
    TOGETHER_JOY,
    TOGETHER_WINDOW,
    WEARABLES,
    check_action,
    clamp,
    decay,
    first_event_ts,
    image_key,
    image_path,
    join_set,
    mood_text,
    next_event_delay,
    parse_set,
    random_event,
    stats_lines,
    worn_line,
)
from pet_game import growth_days, level_for, next_level_xp
from pets import next_stage, pet_name, pet_title, species_by_code, stage_line
from texts import days, fmt, iriski

logger = logging.getLogger(__name__)

router = Router(name="pet_menu")


# ---------- время и уход ----------

def local_now(config: Config, now_ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), config.tz)


async def refresh_care(db: Database, pet, config: Config, now_ts: float | None = None):
    """Пересчитать шкалы на текущий момент и сохранить."""
    now_ts = now_ts if now_ts is not None else time.time()
    return await db.game_refresh(int(pet["id"]), now_ts, config.tz)


async def add_care(
    db: Database, pet, config: Config,
    food: float = 0, joy: float = 0, energy: float = 0,
):
    return await db.game_refresh(int(pet["id"]), time.time(), config.tz, food, joy, energy)


async def chat_feeds_pet(db: Database, pet, config: Config) -> None:
    """Каждое сообщение пары друг другу немного кормит и радует питомца."""
    if pet is None or pet["status"] != "active" or int(pet["best_streak"]) <= 0:
        return
    await add_care(db, pet, config, food=CHAT_FOOD, joy=CHAT_JOY)


def is_owner(pet, user_id: int) -> bool:
    return pet is not None and user_id in (pet["user_a"], pet["user_b"])


def wait_text(seconds: float) -> str:
    minutes = max(int(seconds // 60) + 1, 1)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


# ---------- картинки ----------

async def _media(db: Database, key: str):
    """(медиа для отправки, это файл с диска?) или (None, False)."""
    file_id = await db.get_pet_media(key)
    if file_id:
        return file_id, False
    path = image_path(key)
    if path:
        return FSInputFile(path), True
    return None, False


async def _remember(db: Database, key: str, msg) -> None:
    if isinstance(msg, Message) and msg.photo:
        await db.set_pet_media(key, msg.photo[-1].file_id)


async def send_pet_photo(
    bot: Bot, db: Database, chat_id: int, key: str, caption: str,
    reply_to: int | None = None, markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Фото питомца с подписью; если картинки нет или подпись длинная — текстом."""
    reply = (
        ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
        if reply_to else None
    )
    media, from_disk = await _media(db, key)
    if media is not None and len(caption) <= 1024:
        for _ in range(2):
            try:
                msg = await bot.send_photo(
                    chat_id, media, caption=caption,
                    reply_markup=markup, reply_parameters=reply,
                )
                if from_disk:
                    await _remember(db, key, msg)
                return msg
            except TelegramBadRequest as e:
                if from_disk:
                    logger.warning("Фото %s не ушло: %s", key, e)
                    break
                # file_id протух (например, сменили бота) — грузим файл заново
                await db.drop_pet_media(key)
                media, from_disk = await _media(db, key)
                if media is None:
                    break
            except Exception as e:
                logger.warning("Фото %s не ушло: %s", key, e)
                break
    try:
        return await send_rich(bot, 
            chat_id, caption, reply_markup=markup, reply_parameters=reply
        )
    except Exception as e:
        logger.warning("Сообщение о питомце в чат %s не ушло: %s", chat_id, e)
        return None


async def _edit_card(
    message: Message, db: Database, key: str, caption: str,
    markup: InlineKeyboardMarkup | None,
) -> None:
    """Обновить карточку: фото + подпись + кнопки (или текст, если карточка текстовая)."""
    if message.photo:
        media, from_disk = await _media(db, key)
        if media is not None and len(caption) <= 1024:
            try:
                msg = await message.edit_media(
                    InputMediaPhoto(media=media, caption=caption), reply_markup=markup
                )
                if from_disk:
                    await _remember(db, key, msg)
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                logger.warning("Не смог обновить фото карточки: %s", e)
        try:
            await message.edit_caption(caption=caption[:1024], reply_markup=markup)
        except TelegramBadRequest:
            pass
        return
    try:
        await message.edit_text(caption, reply_markup=markup)
    except TelegramBadRequest:
        pass


# ---------- карточка ----------

def card_caption(pet, names: dict, config: Config, viewer: int | None = None,
                 note: str | None = None) -> str:
    now = local_now(config)
    today = now.strftime("%Y-%m-%d")
    streak, best = int(pet["streak"]), int(pet["best_streak"])
    worn = worn_line(pet["worn"])
    head = f"<b>{pet_title(pet)}</b>" + (f" {worn}" if worn and best > 0 else "")
    pair = f"👫 {_plain(names, pet['user_a'])} и {_plain(names, pet['user_b'])}"
    lines = [head, pair, ""]
    if best <= 0:
        lines.append("🥚 Яйцо ждёт первого дня переписки.")
        lines.append("Напишите сегодня друг другу (ответом или @упоминанием) — и он вылупится!")
    else:
        lines.append(
            f"{stage_line(growth_days(pet))} • 🔥 Серия <b>{streak}</b> {days(streak)}"
            + (f" (рекорд {best})" if best > streak else "")
        )
        xp = int(pet["xp"])
        level = level_for(xp)
        target = next_level_xp(xp)
        lines.append(f"⭐ Уровень {level}/20 • {xp} XP")
        lines.append(f"До уровня {level + 1}: {target - xp} XP" if target else "🏆 Максимальный уровень")
        lines.append("🧭 Приключения дают опыт и ириски. Прогресс не сгорает.")
        lines.append("")
        lines.extend(stats_lines(float(pet["food"]), float(pet["joy"]), float(pet["energy"])))
        lines.append(mood_text(float(pet["food"]), float(pet["joy"]),
                               float(pet["energy"]), now.hour))
        lines.append("")
        if pet["last_fed_day"] == today:
            lines.append("✅ Сегодня день засчитан")
        else:
            waiting = [
                _plain(names, uid)
                for uid, side in ((pet["user_a"], "a_day"), (pet["user_b"], "b_day"))
                if pet[side] != today
            ]
            lines.append("⏳ Сегодня ещё должны написать: " + ", ".join(waiting))
    extras = []
    if int(pet["freezes"]):
        extras.append(f"🧊 Заморозок: {int(pet['freezes'])}")
    if int(pet["house"]):
        extras.append("🏠 Домик")
    if extras:
        lines.append(" • ".join(extras))
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


def card_key(pet, config: Config, force: str | None = None) -> str:
    if int(pet["best_streak"]) <= 0:
        today = local_now(config).strftime("%Y-%m-%d")
        if today in (pet["a_day"], pet["b_day"]):
            return "egg_hatching"  # один уже написал — яйцо трескается
    return image_key(
        pet["species"], growth_days(pet), int(pet["best_streak"]),
        float(pet["food"]), float(pet["joy"]), float(pet["energy"]),
        local_now(config).hour, force,
    )


def _cb(action: str, pet_id: int, arg: str = "") -> str:
    return f"pm:{action}:{pet_id}" + (f":{arg}" if arg else "")


def main_keyboard(pet, config: Config, many: bool) -> InlineKeyboardMarkup:
    pid = int(pet["id"])
    rows: list[list[InlineKeyboardButton]] = []
    if int(pet["best_streak"]) > 0:
        rows.append([
            InlineKeyboardButton(text=a.button, callback_data=_cb(a.code, pid))
            for a in ACTIONS.values()
        ])
        rows.append([
            InlineKeyboardButton(text="🧭 Приключения", callback_data=f"pg:menu:{pid}"),
            InlineKeyboardButton(text="📋 Задания", callback_data=f"pg:tasks:{pid}"),
        ])
        rows.append([
            InlineKeyboardButton(text="🛒 Магазин", callback_data=_cb("shop", pid)),
            InlineKeyboardButton(text="🎀 Гардероб", callback_data=_cb("wardrobe", pid)),
        ])
    nav = []
    if many:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=_cb("nav", pid, "-1")))
    nav.append(InlineKeyboardButton(text="🔄 Обновить", callback_data=_cb("main", pid)))
    if many:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=_cb("nav", pid, "1")))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_row(pid: int) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("main", pid))]


def shop_caption(pet, balance: int, note: str | None = None) -> str:
    owned = set(parse_set(pet["items"]))
    lines = [f"🛒 <b>Магазин для {pet_title(pet)}</b>",
             f"Твой баланс: <b>{fmt(balance)}</b> 🍬", ""]
    for item in SHOP:
        mark = ""
        if item.kind == "wear" and item.code in owned:
            mark = " ✅"
        elif item.kind == "house" and int(pet["house"]):
            mark = " ✅"
        elif item.kind == "freeze":
            mark = f" ({int(pet['freezes'])}/{FREEZE_MAX})"
        lines.append(f"{item.emoji} {item.title} — {item.price} 🍬: {item.note}{mark}")
    lines.extend(["", "Покупки общие: аксессуары и улучшения достаются питомцу, "
                  "платит тот, кто нажал."])
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


def shop_keyboard(pet) -> InlineKeyboardMarkup:
    pid = int(pet["id"])
    buttons = [
        InlineKeyboardButton(
            text=f"{i.emoji} {i.title} · {i.price} 🍬", callback_data=_cb("buy", pid, i.code)
        )
        for i in SHOP
    ]
    rows = [buttons[k:k + 2] for k in range(0, len(buttons), 2)]
    rows.append(back_row(pid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wardrobe_caption(pet, note: str | None = None) -> str:
    owned = parse_set(pet["items"])
    worn = worn_line(pet["worn"])
    lines = [f"🎀 <b>Гардероб {pet_title(pet)}</b>", ""]
    if not owned:
        lines.append("Пока пусто — аксессуары продаются в магазине и иногда "
                     "приходят в посылках 🎁")
    else:
        lines.append("Надето: " + (worn or "ничего"))
        lines.append("Нажми на вещь, чтобы надеть или снять.")
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


def wardrobe_keyboard(pet) -> InlineKeyboardMarkup:
    pid = int(pet["id"])
    owned = parse_set(pet["items"])
    worn = set(parse_set(pet["worn"]))
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if c in worn else "") + f"{SHOP_BY_CODE[c].emoji} {SHOP_BY_CODE[c].title}",
            callback_data=_cb("wear", pid, c),
        )
        for c in owned
    ]
    rows = [buttons[k:k + 2] for k in range(0, len(buttons), 2)]
    if not owned:
        rows.append([InlineKeyboardButton(text="🛒 В магазин", callback_data=_cb("shop", pid))])
    rows.append(back_row(pid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _names(db: Database, pet) -> dict:
    return await db.user_names(pet["chat_id"], [pet["user_a"], pet["user_b"]])


async def send_card(message: Message, db: Database, config: Config, pet=None) -> None:
    """Отправить карточку питомца в ответ на сообщение."""
    user = message.from_user
    pets = await db.user_pets(message.chat.id, user.id)
    if pet is None:
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None and not reply.from_user.is_bot:
            pet = await db.pet_for_pair(message.chat.id, user.id, reply.from_user.id)
            if pet is not None and pet["status"] != "active":
                pet = None
        if pet is None and pets:
            pet = pets[0]
    if pet is None:
        await reply_rich(message, rules_text(config))
        return
    pet = await refresh_care(db, pet, config, local_now(config).timestamp())
    names = await _names(db, pet)
    await send_pet_photo(
        message.bot, db, message.chat.id, card_key(pet, config),
        card_caption(pet, names, config, user.id),
        reply_to=message.message_id,
        markup=main_keyboard(pet, config, len(pets) > 1),
    )


@router.message(GroupF, Command("pets"))
@router.message(GroupF, trig(LIST_TRIGGERS))
async def cmd_my_pets(message: Message, db: Database, config: Config) -> None:
    if message.from_user is None or not config.pets_enabled:
        return
    await send_card(message, db, config)


# ---------- кнопки карточки ----------

@router.callback_query(F.data.startswith("pm:"))
async def on_menu(callback: CallbackQuery, db: Database, config: Config) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit() or not config.pets_enabled:
        await callback.answer()
        return
    action, pet_id = parts[1], int(parts[2])
    arg = parts[3] if len(parts) > 3 else ""
    user = callback.from_user
    message = callback.message
    pet = await db.get_pet(pet_id)
    if not is_owner(pet, user.id):
        await callback.answer("Это не твой питомец 🙂 Заведи своего: ответь другу «питомец»",
                              show_alert=True)
        return
    if pet["status"] != "active":
        await callback.answer("Этого питомца больше нет 💔", show_alert=True)
        return
    if not isinstance(message, Message):
        await callback.answer()
        return

    pets = await db.user_pets(pet["chat_id"], user.id)
    many = len(pets) > 1

    async def show_main(pet_row, note: str | None = None, force: str | None = None):
        names = await _names(db, pet_row)
        await _edit_card(
            message, db, card_key(pet_row, config, force),
            card_caption(pet_row, names, config, user.id, note),
            main_keyboard(pet_row, config, many),
        )

    async def show_shop(pet_row, note: str | None = None):
        urow = await db.get_user(pet_row["chat_id"], user.id)
        balance = int(urow["balance"]) if urow else 0
        await _edit_card(message, db, card_key(pet_row, config),
                         shop_caption(pet_row, balance, note), shop_keyboard(pet_row))

    async def show_wardrobe(pet_row, note: str | None = None):
        await _edit_card(message, db, card_key(pet_row, config),
                         wardrobe_caption(pet_row, note), wardrobe_keyboard(pet_row))

    pet = await refresh_care(db, pet, config, local_now(config).timestamp())
    alive = int(pet["best_streak"]) > 0

    if action == "main":
        await show_main(pet)
        await callback.answer()
        return

    if action == "nav":
        ids = [int(p["id"]) for p in pets]
        if pet_id not in ids or not ids:
            await callback.answer()
            return
        step = -1 if arg == "-1" else 1
        target = await db.get_pet(ids[(ids.index(pet_id) + step) % len(ids)])
        target = await refresh_care(db, target, config)
        await show_main(target)
        await callback.answer()
        return

    if action in ACTIONS:
        note = await db.game_care(pet_id, user.id, action, local_now(config).timestamp(), config.tz)
        if not note.startswith(("✅", "💕")):
            await callback.answer(note, show_alert=True)
            return
        pet = await db.get_pet(pet_id)
        await show_main(pet, note)
        await callback.answer()
        return

    if action == "shop":
        await show_shop(pet)
        await callback.answer()
        return

    if action == "wardrobe":
        await show_wardrobe(pet)
        await callback.answer()
        return

    if action == "buy":
        note = await db.game_buy(pet_id, user.id, arg, time.time(), config.tz)
        if not note.startswith("✅"):
            await callback.answer(note, show_alert=True)
            return
        await show_shop(await db.get_pet(pet_id), note)
        await callback.answer()
        return

    if action == "wear":
        await db.game_wear(pet_id,user.id,arg)
        await show_wardrobe(await db.get_pet(pet_id))
        await callback.answer()
        return

    if action == "restore":
        await show_main(pet, "🏡 Питомец дома. Уровень и вещи больше не теряются из-за пропуска.")
        await callback.answer()
        return

    await callback.answer()


# ---------- события в течение дня ----------

def event_caption(pet, names: dict, kind) -> str:
    title = f"<b>{pet_title(pet)}</b>"
    lines = [
        f"{kind.emoji} {title} {kind.text}",
        f"{_who(names, pet['user_a'])} и {_who(names, pet['user_b'])}",
    ]
    if kind.together:
        lines.append("Нужно, чтобы нажали <b>оба</b>.")
    return "\n".join(lines)


def event_keyboard(event_id: int, kind) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"pe:{event_id}:{choice}")
        for choice, label in kind.buttons
    ]])


def event_photo_key(pet, kind_code: str) -> str:
    sp = species_by_code(pet["species"]).code
    return f"{sp}_hungry" if kind_code == "cold" else f"{sp}_happy"


async def spawn_event(bot: Bot, db: Database, config: Config, pet, now_ts: float) -> bool:
    kind = random_event()
    ttl = max(config.pet_event_ttl_hours, 0.1) * 3600
    event_id = await db.create_pet_event(int(pet["id"]), int(pet["chat_id"]),
                                         kind.code, now_ts, ttl)
    names = await _names(db, pet)
    msg = await send_pet_photo(
        bot, db, int(pet["chat_id"]), event_photo_key(pet, kind.code),
        event_caption(pet, names, kind), markup=event_keyboard(event_id, kind),
    )
    if msg is not None:
        await db.set_pet_event_message(event_id, msg.message_id)
    else:
        await db.fail_pet_event_delivery(event_id, now_ts + 3600)
    return msg is not None


async def _finish_event_message(bot: Bot, ev, caption: str) -> None:
    if not ev["message_id"]:
        return
    try:
        await bot.edit_message_caption(
            chat_id=ev["chat_id"], message_id=ev["message_id"],
            caption=caption[:1024], reply_markup=None,
        )
    except TelegramBadRequest:
        try:
            await bot.edit_message_text(
                caption, chat_id=ev["chat_id"], message_id=ev["message_id"],
                reply_markup=None,
            )
        except TelegramBadRequest:
            pass
    except Exception as e:
        logger.warning("Не смог закрыть событие питомца: %s", e)


EXPIRED_TEXT = {
    "coin": "🪙 Монетку утащила сорока…",
    "butterfly": "🦋 Бабочка улетела — в следующий раз ловите вдвоём!",
    "parcel": "📦 Курьер забрал посылку обратно…",
    "cold": "😷 Никто не вылечил — питомец загрустил и устал (−30 настроения).",
    "snack": "🍩 Пончик съеден без спроса. Ну и ладно, он доволен 😋",
}


async def expire_event(bot: Bot, db: Database, config: Config, ev) -> None:
    pet = await db.get_pet(int(ev["pet_id"]))
    kind = EVENTS.get(ev["kind"])
    if pet is None or kind is None:
        return
    # Effects were applied atomically when the event expired in the database.
    names = await _names(db, pet)
    await _finish_event_message(
        bot, ev, event_caption(pet, names, kind) + "\n\n" + EXPIRED_TEXT[ev["kind"]]
    )


async def process_pet_events(
    bot: Bot, db: Database, config: Config, *, now: datetime | None = None,
) -> int:
    """Один проход: закрыть просроченные события и запустить новые. Возвращает,
    сколько событий запущено."""
    if not config.pets_enabled:
        return 0
    now = now or datetime.now(config.tz)
    now_ts = now.timestamp()
    for ev in await db.expire_pet_events(now_ts, config.tz):
        try:
            await expire_event(bot, db, config, ev)
        except Exception:
            logger.exception("Ошибка при закрытии события питомца")
    if config.pet_events_per_day <= 0:
        return 0
    if not (EVENT_HOURS[0] <= now.hour < EVENT_HOURS[1]):
        return 0
    today = now.strftime("%Y-%m-%d")
    ttl = max(config.pet_event_ttl_hours, 0.1) * 3600
    spawned = 0
    for pet in await db.pets_for_events():
        if pet["events_day"] != today:
            pet = await db.update_pet(
                int(pet["id"]), events_day=today, events_count=0,
                next_event_ts=first_event_ts(now),
            )
        if int(pet["events_count"]) >= config.pet_events_per_day:
            continue
        if now_ts < float(pet["next_event_ts"]):
            continue
        await db.update_pet(
            int(pet["id"]), events_count=int(pet["events_count"]) + 1,
            next_event_ts=now_ts + max(next_event_delay(), ttl),
        )
        if await spawn_event(bot, db, config, pet, now_ts):
            spawned += 1
    return spawned


async def pet_events_keeper(bot: Bot, db: Database, config: Config) -> None:
    """Раз в минуту: события питомцев."""
    if not config.pets_enabled:
        return
    while True:
        try:
            await process_pet_events(bot, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка событий питомцев")
        await asyncio.sleep(60)


@router.callback_query(F.data.startswith("pe:"))
async def on_event(callback: CallbackQuery, db: Database, config: Config) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer()
        return
    event_id, choice = int(parts[1]), parts[2]
    user = callback.from_user
    ev = await db.get_pet_event(event_id)
    pet = await db.get_pet(int(ev["pet_id"])) if ev else None
    kind = EVENTS.get(ev["kind"]) if ev else None
    if ev is None or pet is None or kind is None:
        await callback.answer()
        return
    if not is_owner(pet, user.id):
        await callback.answer("Это событие чужого питомца 🙂", show_alert=True)
        return
    if choice not in {c for c, _ in kind.buttons}:
        await callback.answer()
        return
    chat_id = int(pet["chat_id"])
    now = time.time()

    status, ev, pet, result = await db.resolve_pet_event(
        event_id, user.id, choice, now, config.tz,
    )
    if status == "forbidden":
        await callback.answer("Это чужой питомец", show_alert=True)
        return
    if status == "insufficient":
        row = await db.get_user(chat_id, user.id)
        balance = int(row["balance"]) if row else 0
        await callback.answer(
            f"Лекарство стоит {CURE_PRICE} 🍬, у тебя {fmt(balance)}",
            show_alert=True,
        )
        return
    if status in ("gone", "again", "invalid"):
        await callback.answer("Уже нажато 🙂" if status == "again" else "Событие закончено")
        return
    names = await _names(db, pet)
    who = _who(names, user.id)
    if status == "wait":
        await callback.answer("Ждём второго! 🦋")
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_caption(
                    caption=event_caption(pet, names, kind) + f"\n\n✅ {who} уже ловит!",
                    reply_markup=event_keyboard(event_id, kind),
                )
            except TelegramBadRequest:
                pass
        return
    await callback.answer("Готово!")
    await _finish_event_message(
        callback.bot, ev, event_caption(pet, names, kind) + "\n\n✅ " + who + ": " + result,
    )

