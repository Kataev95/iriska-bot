"""Общий питомец: серия общения и постоянный прогресс Питомцев 3.0.

Пропуск прерывает серию, но не уровень, лучший облик и имущество.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from rich_ui import edit_rich, reply_rich, rich_table, send_rich
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from db import Database
from handlers.common import GroupF, mention, today_day, trig, yesterday_day
from pet_game import growth_days, level_for
from pets import (
    clean_name,
    next_stage,
    parse_rewards,
    pet_name,
    pet_title,
    random_species,
    species_by_code,
    stage_for,
    stage_line,
)
from texts import days, display_name, fmt, iriski, place

logger = logging.getLogger(__name__)

router = Router(name="pets")

INVITE_TRIGGERS = {"питомец", "завести питомца", "общий питомец"}
LIST_TRIGGERS = {"мой питомец", "мои питомцы", "питомцы", "наш питомец"}
TOP_TRIGGERS = {"топ питомцев", "питомцы топ"}
RESTORE_TRIGGERS = {"вернуть питомца", "вылечить питомца"}
ABANDON_TRIGGERS = {"бросить питомца", "отпустить питомца"}
HELP_TRIGGERS = {"как растить питомца", "питомец помощь", "правила питомца"}
RENAME_RE = re.compile(r"^\s*(?:назвать|имя)\s+питомца\s+(.+?)\s*$", re.IGNORECASE | re.S)


# ---------- тексты ----------

def rules_text(config: Config) -> str:
    return (
        "🐾 <b>Питомцы 3.0: развитие и приключения</b>\n\n"
        "Ответь другу «питомец» и дождись согласия. Первая взаимная переписка вылупит яйцо.\n"
        "Уровень, лучший облик и вещи постоянны. Пропуск обнуляет только серию общения.\n"
        "Открой «мой питомец»: уход, магазин, приключения и ежедневные задания.\n"
        "🧭 Парк — 15 минут, лес — 2 часа, руины — 8 часов. Один поход на питомца; "
        "до 3 стартов в день на хозяина во всех чатах. Награда ждёт возвращения.\n"
        "⭐ На 5-м уровне выбери профессию. XP общий, ириски похода получает отправитель.\n"
        "📋 Задания: уход, завершённый поход, участие обоих хозяев. Обновляются в полночь по времени бота.\n"
        "🍬 Ириски — валюта только этого бота, без вывода и месячного сгорания.\n"
        "Команды: «мой питомец», «топ питомцев», «назвать питомца Барсик»."
    )


def _who(rows: dict, uid: int) -> str:
    row = rows.get(uid)
    if row is None:
        return mention(uid, None, None)
    return mention(uid, row["first_name"], row["username"])


def _plain(rows: dict, uid: int) -> str:
    row = rows.get(uid)
    if row is None:
        return "боец"
    return display_name(row["first_name"], row["username"])


def pet_card(pet, me: int, names: dict, config: Config, today: str) -> str:
    """Строка про одного питомца в списке «мои питомцы»."""
    partner = pet["user_b"] if pet["user_a"] == me else pet["user_a"]
    streak = int(pet["streak"])
    head = f"{pet_title(pet)} — с {_plain(names, partner)}"
    lines = [f"<b>{head}</b>"]
    if streak > 0:
        lines.append(f"   🔥 Серия: <b>{streak}</b> {days(streak)} • {stage_line(streak)}")
    elif int(pet["lost_streak"]) > 0:
        lost = int(pet["lost_streak"])
        lines.append(f"   🏡 Серия {lost} {days(lost)} завершена. Питомец и вещи сохранены.")
    elif int(pet["best_streak"]) > 0:
        lines.append("   🏡 Дома. Уровень и вещи сохранены; начните новую серию общения")
    else:
        lines.append("   🥚 Ждёт первого дня переписки, чтобы вылупиться")
    if pet["last_fed_day"] == today:
        lines.append("   ✅ Сегодня накормлен")
    else:
        my_side = "a_day" if pet["user_a"] == me else "b_day"
        their_side = "b_day" if my_side == "a_day" else "a_day"
        waiting = []
        if pet[my_side] != today:
            waiting.append("ты")
        if pet[their_side] != today:
            waiting.append(_plain(names, partner))
        lines.append("   ⏳ Сегодня ещё должны написать: " + ", ".join(waiting))
    nxt = next_stage(streak)
    if streak > 0 and nxt is not None:
        left = nxt.min_streak - streak
        lines.append(f"   До «{nxt.title}» {nxt.badge}: {left} {days(left)}")
    return "\n".join(lines)


def invite_keyboard(pet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🥚 Растим вместе!", callback_data=f"pet:yes:{pet_id}"),
        InlineKeyboardButton(text="🙅 Нет", callback_data=f"pet:no:{pet_id}"),
    ]])


def abandon_keyboard(pet_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💔 Да, отпустить", callback_data=f"pet:del:{pet_id}"),
        InlineKeyboardButton(text="Оставить", callback_data=f"pet:keep:{pet_id}"),
    ]])


async def _resolve_pet(message: Message, db: Database):
    """Питомец, о котором речь: пара с тем, кому ответили, или единственный.

    Возвращает (питомец | None, текст ошибки | None).
    """
    user = message.from_user
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None and not reply.from_user.is_bot \
            and reply.from_user.id != user.id and reply.forum_topic_created is None:
        pet = await db.pet_for_pair(message.chat.id, user.id, reply.from_user.id)
        if pet is None or pet["status"] != "active":
            return None, "У вас с ним нет общего питомца. Завести: ответь ему «питомец» 🐣"
        return pet, None
    pets = await db.user_pets(message.chat.id, user.id)
    if not pets:
        return None, "У тебя пока нет питомца. Ответь другу «питомец» — и растите вместе 🐣"
    if len(pets) > 1:
        return None, "У тебя несколько питомцев — напиши это ответом на сообщение партнёра."
    return pets[0], None


# ---------- приглашение ----------

@router.message(GroupF, Command("pet"))
@router.message(GroupF, trig(INVITE_TRIGGERS))
async def cmd_pet(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot or not config.pets_enabled:
        return
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or reply.forum_topic_created is not None:
        await cmd_my_pets(message, db, config)
        return
    target = reply.from_user
    if target.is_bot:
        # «питомец» ответом на сообщение бота (например, на карточку) — показать карточку
        await cmd_my_pets(message, db, config)
        return
    if target.id == user.id:
        await reply_rich(message, "Питомца растят вдвоём — ответь «питомец» другу 😉")
        return

    now = time.time()
    await db.expire_pet_invites(now, config.pet_invite_ttl)
    await db.ensure_user(message.chat.id, user.id, user.username, user.first_name)
    await db.ensure_user(message.chat.id, target.id, target.username, target.first_name)
    status, pet = await db.create_pet_invite(
        message.chat.id, user.id, target.id, random_species().code,
        now, config.pet_max_per_user,
    )
    if status == "exists":
        if pet["status"] == "pending":
            await reply_rich(message, "Приглашение уже отправлено — ждём ответа 🥚")
        else:
            names = await db.user_names(message.chat.id, [pet["user_a"], pet["user_b"]])
            await reply_rich(message, pet_card(pet, user.id, names, config, today_day(config)))
        return
    if status == "limit_inviter":
        await reply_rich(message, 
            f"У тебя уже {config.pet_max_per_user} питомцев — больше не прокормить 🙈"
        )
        return
    if status == "limit_target":
        await reply_rich(message, "У него уже максимум питомцев 🙈")
        return
    minutes = max(int(config.pet_invite_ttl // 60), 1)
    await reply_rich(message, 
        f"🥚 {mention(user.id, user.first_name, user.username)} предлагает "
        f"{mention(target.id, target.first_name, target.username)} завести "
        "<b>общего питомца</b>!\n\n"
        "Пишите друг другу каждый день — он вылупится и будет расти. "
        "Пропуск прерывает только серию: питомец и вещи сохраняются.\n"
        f"Приглашение действует {minutes} мин.",
        reply_markup=invite_keyboard(int(pet["id"])),
    )


@router.callback_query(F.data.startswith("pet:"))
async def on_pet_callback(callback: CallbackQuery, db: Database, config: Config) -> None:
    user = callback.from_user
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or not config.pets_enabled:
        await callback.answer()
        return
    action, pet_id = parts[1], int(parts[2])
    now = time.time()
    message = callback.message

    async def edit(text: str) -> None:
        if message is None:
            return
        try:
            await edit_rich(message, text, reply_markup=None)
        except TelegramBadRequest:
            pass

    if action in ("yes", "no"):
        status, pet = await db.answer_pet_invite(
            pet_id, user.id, action == "yes", now,
            config.pet_invite_ttl, config.pet_max_per_user,
        )
        if status == "not_yours":
            await callback.answer("Это приглашение не тебе 🙂", show_alert=True)
            return
        if status == "gone":
            await callback.answer("Приглашение уже неактуально")
            return
        if status == "limit":
            await callback.answer("У кого-то из вас уже максимум питомцев", show_alert=True)
            return
        names = await db.user_names(pet["chat_id"], [pet["user_a"], pet["user_b"]])
        a, b = _who(names, pet["user_a"]), _who(names, pet["user_b"])
        if status == "expired":
            await edit("⌛ Приглашение завести питомца истекло — яйцо остыло.")
            await callback.answer()
            return
        if status in ("declined", "cancelled"):
            await edit("🙅 Питомца решили не заводить.")
            await callback.answer()
            return
        await edit(
            f"🥚 У {a} и {b} появилось <b>общее яйцо</b>!\n\n"
            "Сегодня напишите друг другу (ответом на сообщение или @упоминанием) — "
            "и из яйца кто-то вылупится. Дальше — каждый день, чтобы серия росла 🔥\n"
            "Свои питомцы: «мои питомцы»"
        )
        await callback.answer("Растите вместе! 🐣")
        return

    if action in ("del", "keep"):
        pet = await db.get_pet(pet_id)
        if pet is None or user.id not in (pet["user_a"], pet["user_b"]):
            await callback.answer("Это не твой питомец 🙂", show_alert=True)
            return
        if action == "keep":
            await edit(f"💛 {pet_title(pet)} остаётся с вами.")
            await callback.answer()
            return
        if await db.abandon_pet(pet_id, user.id, now):
            await edit(f"💔 {pet_title(pet)} отпущен на волю. Серия обнулена.")
        await callback.answer()
        return
    await callback.answer()


# ---------- список, топ, правила ----------

async def cmd_my_pets(message: Message, db: Database, config: Config) -> None:
    """Карточка питомца с фото и кнопками (сами триггеры — в handlers/pet_menu.py)."""
    if message.from_user is None or not config.pets_enabled:
        return
    from handlers.pet_menu import send_card  # без циклического импорта

    await send_card(message, db, config)


@router.message(GroupF, Command("pettop"))
@router.message(GroupF, trig(TOP_TRIGGERS))
async def cmd_pet_top(message: Message, db: Database, config: Config) -> None:
    if not config.pets_enabled:
        return
    top = await db.top_pets(message.chat.id, 10)
    if not top:
        await reply_rich(message, "Питомцев пока нет. Ответь другу «питомец» — будьте первыми 🐣")
        return
    ids = sorted({p["user_a"] for p in top} | {p["user_b"] for p in top})
    names = await db.user_names(message.chat.id, ids)
    lines = ["🏆 <b>Топ питомцев чата</b>", ""]
    for i, p in enumerate(top, 1):
        streak = int(p["streak"])
        tail = f"⭐ Уровень {level_for(p['xp'])} • {p['xp']} XP • 🔥 {streak} {days(streak)}"
        lines.append(
            f"{place(i)} {pet_title(p)} — {_plain(names, p['user_a'])} и "
            f"{_plain(names, p['user_b'])} • {tail}"
        )
    html = (
        "<h2>🏆 Топ питомцев чата</h2>"
        + rich_table(
            ["№", "Питомец", "Хозяева", "Уровень", "🔥 Дни"],
            [
                [
                    str(i), pet_title(p), _plain(names, p["user_a"]) + " и " + _plain(names, p["user_b"]),
                    str(level_for(p["xp"])), str(p["streak"]),
                ]
                for i, p in enumerate(top, 1)
            ],
        )
        + "<footer>Рейтинг по лучшей серии. Прогресс и опыт сохраняются.</footer>"
    )
    await reply_rich(message, "\n".join(lines), html=html)


@router.message(GroupF, trig(HELP_TRIGGERS))
async def cmd_pet_help(message: Message, config: Config) -> None:
    if config.pets_enabled:
        await reply_rich(message, rules_text(config))


# ---------- имя, возвращение, отпустить ----------

@router.message(GroupF, F.text.func(lambda t: bool(t) and bool(RENAME_RE.match(t))))
async def cmd_rename(message: Message, db: Database, config: Config) -> None:
    if message.from_user is None or not config.pets_enabled:
        return
    pet, error = await _resolve_pet(message, db)
    if pet is None:
        await reply_rich(message, error)
        return
    m = RENAME_RE.match(message.text or "")
    name = clean_name(m.group(1) if m else "")
    if not name:
        await reply_rich(message, "Напиши имя: «назвать питомца Барсик»")
        return
    await db.rename_pet(int(pet["id"]), name)
    pet = await db.get_pet(int(pet["id"]))
    if int(pet["best_streak"]) <= 0:
        await reply_rich(message, 
            f"🥚 Имя <b>{pet_name(pet)}</b> записано — так назовут того, кто вылупится!"
        )
    else:
        await reply_rich(message, f"✨ Теперь питомца зовут {pet_title(pet)}")


@router.message(GroupF, trig(RESTORE_TRIGGERS))
async def cmd_restore(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or not config.pets_enabled:
        return
    pet, error = await _resolve_pet(message, db)
    if pet is None:
        await reply_rich(message, error)
        return
    await reply_rich(message, "🏡 В Питомцах 3.0 питомец остаётся дома: уровень, облик и вещи сохраняются. Открой «мой питомец».")


@router.message(GroupF, trig(ABANDON_TRIGGERS))
async def cmd_abandon(message: Message, db: Database, config: Config) -> None:
    if message.from_user is None or not config.pets_enabled:
        return
    pet, error = await _resolve_pet(message, db)
    if pet is None:
        await reply_rich(message, error)
        return
    streak = int(pet["streak"])
    await reply_rich(message, 
        f"Точно отпустить {pet_title(pet)}? Серия 🔥 {streak} {days(streak)} "
        "пропадёт навсегда.",
        reply_markup=abandon_keyboard(int(pet["id"])),
    )


# ---------- хук: переписка пары кормит питомца ----------

async def _partner_ids(message: Message, db: Database) -> set[int]:
    """Кому адресовано сообщение: автор сообщения-ответа и упомянутые."""
    me = message.from_user.id
    found: set[int] = set()
    reply = message.reply_to_message
    if (
        reply is not None and reply.from_user is not None
        and not reply.from_user.is_bot
        and reply.forum_topic_created is None  # в форумах «ответ» на тему
    ):
        found.add(reply.from_user.id)
    text = message.text or message.caption or ""
    for ent in (message.entities or message.caption_entities or []):
        if ent.type == "text_mention" and ent.user is not None and not ent.user.is_bot:
            found.add(ent.user.id)
        elif ent.type == "mention":
            username = ent.extract_from(text).lstrip("@")
            row = await db.find_by_username(message.chat.id, username)
            if row is not None:
                found.add(int(row["user_id"]))
    found.discard(me)
    return found


async def track_pet_message(message: Message, db: Database, config: Config) -> None:
    """Вызывается для каждого сообщения в группе из роутера подсчёта."""
    if not config.pets_enabled:
        return
    user = message.from_user
    if user is None or user.is_bot or message.via_bot is not None:
        return
    if message.forward_origin is not None:
        return
    partners = await _partner_ids(message, db)
    if not partners:
        return
    rewards = parse_rewards(config.pet_rewards)
    today, yesterday = today_day(config), yesterday_day(config)
    for partner in partners:
        event = await db.record_pet_message(
            message.chat.id, user.id, partner, today, yesterday, rewards
        )
        if not event:
            continue
        from handlers.pet_menu import chat_feeds_pet, send_pet_photo

        try:
            await chat_feeds_pet(db, event.get("pet"), config)
        except Exception as e:
            logger.warning("Не смог покормить питомца перепиской: %s", e)
        if not event["fed"] or not config.pet_feed_notice:
            continue
        try:
            text = await fed_text(db, event)
            key = fed_photo_key(event)
            if key:
                await send_pet_photo(message.bot, db, message.chat.id, key, text,
                                     reply_to=message.message_id)
            else:
                await reply_rich(message, text)
        except Exception as e:  # не мешаем подсчёту из-за сбоя отправки
            logger.warning("Не смог сообщить о питомце: %s", e)


def fed_photo_key(event: dict) -> str | None:
    """Фото — только в особые моменты: вылупился или подрос."""
    pet = event["pet"]
    streak = int(event["streak"])
    sp = species_by_code(pet["species"]).code
    if int(event["prev_best"]) <= 0:
        return f"{sp}_baby"
    stage = stage_for(growth_days(pet))
    if stage is not None and stage.min_streak == streak and streak > 1:
        return f"{sp}_{stage.code}"
    return None


async def fed_text(db: Database, event: dict) -> str:
    pet = event["pet"]
    streak = int(event["streak"])
    prev_best = int(event["prev_best"])
    names = await db.user_names(pet["chat_id"], [pet["user_a"], pet["user_b"]])
    pair = f"{_plain(names, pet['user_a'])} и {_plain(names, pet['user_b'])}"
    lines = []
    if prev_best <= 0:
        sp = species_by_code(pet["species"])
        lines.append(f"🐣 <b>Из яйца вылупился {sp.emoji} {sp.title}!</b>")
        if (pet["name"] or "").strip():
            lines.append(f"Его зовут <b>{pet_name(pet)}</b>.")
        else:
            lines.append("Дайте ему имя: «назвать питомца Барсик»")
    else:
        lines.append(f"{pet_title(pet)} накормлен 🍖")
    lines.append(f"🔥 {pair}: серия <b>{streak}</b> {days(streak)}")
    stage = stage_for(growth_days(pet))
    if stage is not None and stage.min_streak == streak and streak > 1:
        lines.append(f"✨ Питомец подрос — теперь он <b>{stage.title}</b> {stage.badge}!")
    if event["reward"]:
        r = int(event["reward"])
        lines.append(f"🎁 За {streak} {days(streak)} подряд каждому +{fmt(r)} 🍬")
    return "\n".join(lines)


# ---------- фоновая задача: побеги и напоминания ----------

async def process_pet_day(
    bot: Bot, db: Database, config: Config, *, now: datetime | None = None,
) -> tuple[int, int]:
    """Один проход: сбежавшие питомцы и вечерние напоминания.

    Возвращает (сколько сбежало, сколько напомнили).
    """
    if not config.pets_enabled:
        return 0, 0
    now = now or datetime.now(config.tz)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Сначала учитываем простой сервера, и ТОЛЬКО потом тратим заморозки
    # или объявляем побег. Это важно сразу после перезапуска.
    shielded, outage_hours = await db.shield_pet_streaks_after_outage(
        now.timestamp(), config.tz, config.pet_outage_grace_hours,
    )
    if shielded:
        logger.warning(
            "🛡 После простоя %.1f ч сохранено %s серий питомцев",
            outage_hours, shielded,
        )

    frozen = await db.use_pet_freezes(yesterday)
    by_chat: dict[int, list] = {}
    for pet in frozen:
        by_chat.setdefault(int(pet["chat_id"]), []).append(pet)
    for chat_id, pets in by_chat.items():
        ids = sorted({p["user_a"] for p in pets} | {p["user_b"] for p in pets})
        names = await db.user_names(chat_id, ids)
        lines = ["🧊 <b>Заморозка спасла серию!</b>", ""]
        for p in pets:
            n = int(p["streak"])
            lines.append(
                f"{pet_title(p)} — {_who(names, p['user_a'])} и "
                f"{_who(names, p['user_b'])} вчера не переписывались, но серия "
                f"🔥 {n} {days(n)} цела. Заморозок осталось: {int(p['freezes'])}."
            )
        lines.append("Сегодня напишите друг другу обязательно!")
        await _send(bot, chat_id, "\n".join(lines))

    lost = await db.sweep_lost_pets(yesterday, now.timestamp())
    by_chat = {}
    for pet in lost:
        by_chat.setdefault(int(pet["chat_id"]), []).append(pet)
    for chat_id, pets in by_chat.items():
        ids = sorted({p["user_a"] for p in pets} | {p["user_b"] for p in pets})
        names = await db.user_names(chat_id, ids)
        lines = ["🏡 <b>Новый старт серии общения</b>", ""]
        for p in pets:
            lines.append(f"{pet_title(p)} — серия прервалась, но питомец, опыт и вещи сохранены.")
        await _send(bot, chat_id, "\n".join(lines))

    reminded = 0
    if 0 <= config.pet_remind_hour <= 23 and now.hour >= config.pet_remind_hour:
        risky = await db.pets_to_remind(today, yesterday)
        reminded = len(risky)
        by_chat = {}
        for pet in risky:
            by_chat.setdefault(int(pet["chat_id"]), []).append(pet)
        for chat_id, pets in by_chat.items():
            ids = sorted({p["user_a"] for p in pets} | {p["user_b"] for p in pets})
            names = await db.user_names(chat_id, ids)
            lines = [
                "⏳ <b>Совместное задание:</b> напишите друг другу до полуночи, "
                "чтобы продолжить серию. Уровень и вещи сохраняются в любом случае:",
                "",
            ]
            for p in pets:
                waiting = [
                    _who(names, uid)
                    for uid, side in ((p["user_a"], "a_day"), (p["user_b"], "b_day"))
                    if p[side] != today
                ]
                streak = int(p["streak"])
                lines.append(
                    f"{pet_title(p)} 🔥 {streak} — ждём: " + ", ".join(waiting)
                )
            await _send(bot, chat_id, "\n".join(lines))
    return len(lost), reminded


async def _send(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await send_rich(bot, chat_id, text)
    except Exception as e:  # выгнали из чата и т.п. — не падаем
        logger.warning("Сообщение о питомцах в чат %s не ушло: %s", chat_id, e)


async def pet_keeper(bot: Bot, db: Database, config: Config) -> None:
    """Раз в 30 секунд разбирает побеги питомцев и шлёт напоминания."""
    if not config.pets_enabled:
        return
    while True:
        try:
            await process_pet_day(bot, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка смотрителя питомцев")
        await asyncio.sleep(30)

