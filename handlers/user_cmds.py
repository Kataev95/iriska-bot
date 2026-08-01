"""Команды участников: статистика, топы, баланс, вывод, справка."""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from config import Config
from db import Database
from handlers.common import (
    GroupF,
    build_profile,
    is_bonus_hour,
    today_day,
    trig,
    week_ago_day,
    windows_text,
)
from texts import display_name, fmt, help_text, iriski, msgs, place

router = Router(name="user")

STATS_TRIGGERS = {"стата", "моя стата", "статистика", "профиль"}
DAY_TRIGGERS = {"топ дня", "топ за сегодня", "дневной топ"}
HOURS_TRIGGERS = {"бонусные часы", "бонус часы", "часы бонуса"}
BALANCE_TRIGGERS = {"баланс", "мой баланс", "ириски", "мои ириски"}
TOP_TRIGGERS = {"топ", "топ чата", "общий топ"}
WEEK_TRIGGERS = {"топ недели", "топ за неделю", "недельный топ"}
WITHDRAW_TRIGGERS = {"вывод", "вывести ириски"}
HELP_TRIGGERS = {"помощь", "как заработать", "правила ирисок"}


@router.message(GroupF, Command("me", "stats", "profile"))
@router.message(GroupF, trig(STATS_TRIGGERS))
async def cmd_me(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None:
        return
    row = await db.get_user(message.chat.id, user.id)
    if row is None:
        await message.reply(
            "Я тебя ещё не считал 🙈 Напиши пару сообщений в чат и загляни снова."
        )
        return
    await message.reply(await build_profile(db, config, message.chat.id, row))


@router.message(GroupF, Command("balance"))
@router.message(GroupF, trig(BALANCE_TRIGGERS))
async def cmd_balance(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None:
        return
    row = await db.get_user(message.chat.id, user.id)
    balance = row["balance"] if row else 0
    if balance >= config.withdraw_threshold:
        await message.reply(
            f"🍬 Баланс: <b>{fmt(balance)}</b> {iriski(balance)} — ✅ можно выводить!\n"
            f"Пиши {config.admin_contact}"
        )
    else:
        need = config.withdraw_threshold - balance
        await message.reply(
            f"🍬 Баланс: <b>{fmt(balance)}</b> {iriski(balance)}\n"
            f"До вывода ({fmt(config.withdraw_threshold)}) осталось "
            f"{fmt(need)} {iriski(need)}. Подробнее: /me"
        )


@router.message(GroupF, Command("top"))
@router.message(GroupF, trig(TOP_TRIGGERS))
async def cmd_top(message: Message, db: Database, config: Config) -> None:
    rows = await db.top_alltime(message.chat.id, 10)
    if not rows:
        await message.reply("Пока никто ничего не наболтал 😴 Начните общаться!")
        return
    totals = await db.chat_totals(message.chat.id)
    lines = ["🏆 <b>Топ чата за всё время</b>", ""]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{place(i)} {display_name(r['first_name'], r['username'])} — "
            f"<b>{fmt(r['total_counted'])}</b> {msgs(r['total_counted'])} "
            f"(🍬 {fmt(r['balance'])})"
        )
    lines.append("")
    lines.append(
        f"Всего: {fmt(totals['msgs'])} {msgs(totals['msgs'])} от "
        f"{fmt(totals['users'])} участников, заработано 🍬 {fmt(totals['earned'])}"
    )
    await message.reply("\n".join(lines))


@router.message(GroupF, Command("week", "topweek"))
@router.message(GroupF, trig(WEEK_TRIGGERS))
async def cmd_week(message: Message, db: Database, config: Config) -> None:
    rows = await db.top_since(message.chat.id, week_ago_day(config), 10)
    if not rows:
        await message.reply("За последние 7 дней тишина 😴")
        return
    lines = ["📅 <b>Топ за последние 7 дней</b>", ""]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{place(i)} {display_name(r['first_name'], r['username'])} — "
            f"<b>{fmt(r['cnt'])}</b> {msgs(r['cnt'])}"
        )
    await message.reply("\n".join(lines))


@router.message(GroupF, Command("day", "today"))
@router.message(GroupF, trig(DAY_TRIGGERS))
async def cmd_day(message: Message, db: Database, config: Config) -> None:
    rows = await db.top_since(message.chat.id, today_day(config), 10)
    if not rows:
        await message.reply("Сегодня пока тишина 😴 Самое время начать!")
        return
    lines = ["☀️ <b>Топ за сегодня</b>", ""]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{place(i)} {display_name(r['first_name'], r['username'])} — "
            f"<b>{fmt(r['cnt'])}</b> {msgs(r['cnt'])}"
        )
    await message.reply("\n".join(lines))


@router.message(GroupF, Command("withdraw"))
@router.message(GroupF, trig(WITHDRAW_TRIGGERS))
async def cmd_withdraw(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None:
        return
    row = await db.get_user(message.chat.id, user.id)
    balance = row["balance"] if row else 0
    threshold = config.withdraw_threshold
    if balance >= threshold:
        await message.reply(
            f"🎉 У тебя 🍬 <b>{fmt(balance)}</b> — минимум для вывода набран!\n"
            f"📩 Напиши {config.admin_contact}, он оформит выплату и спишет "
            f"ириски с баланса."
        )
    else:
        need = threshold - balance
        await message.reply(
            f"📤 Вывод доступен от 🍬 {fmt(threshold)}.\n"
            f"У тебя сейчас <b>{fmt(balance)}</b> — осталось накопить "
            f"{fmt(need)} {iriski(need)}.\n"
            f"Напоминаю: 1 ириска за каждые {fmt(config.messages_per_iriska)} "
            f"сообщений 😉"
        )


@router.message(GroupF, Command("hours"))
@router.message(GroupF, trig(HOURS_TRIGGERS))
async def cmd_hours(message: Message, config: Config) -> None:
    if not config.bonus_hours:
        await message.reply("Бонусные часы сейчас не настроены.")
        return
    mult = max(config.bonus_hours_mult, 1)
    now = datetime.now(config.tz)
    tz_label = "МСК" if config.tz.key == "Europe/Moscow" else config.tz.key
    if is_bonus_hour(now.hour, config.bonus_hours):
        status = f"🔥 <b>Сейчас бонусный час</b> — каждое сообщение идёт х{mult}!"
    else:
        status = "Сейчас обычное время — заглядывай в бонусные окна 😉"
    await message.reply(
        f"⏰ <b>Бонусные часы</b> — прогресс к ирискам х{mult}:\n"
        f"{windows_text(config.bonus_hours)} ({tz_label})\n\n{status}"
    )


@router.message(Command("help"))
@router.message(GroupF, trig(HELP_TRIGGERS))
async def cmd_help(message: Message, config: Config) -> None:
    hours_line = ""
    if config.bonus_hours:
        hours_line = (
            f"х{max(config.bonus_hours_mult, 1)} в "
            f"{windows_text(config.bonus_hours)}"
        )
    await message.reply(
        help_text(
            per=config.messages_per_iriska,
            threshold=config.withdraw_threshold,
            min_len=config.min_msg_len,
            cooldown=config.cooldown_seconds,
            contact=config.admin_contact,
            hours_line=hours_line,
        )
    )


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: Message, config: Config) -> None:
    await message.reply(
        "Привет! Я — Ириска-бот 🍬\n"
        "Считаю сообщения в чате и начисляю ириски за активность.\n\n"
        "Добавь меня в группу — и статистика начнёт копиться.\n"
        "Справка: /help\n"
        "Узнать свой ID (для настройки админки): /id"
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    lines = [f"🆔 Твой ID: <code>{user.id}</code>"]
    reply = message.reply_to_message
    if reply and reply.from_user:
        target = reply.from_user
        lines.append(
            f"ID автора сообщения ({display_name(target.first_name, target.username)}): "
            f"<code>{target.id}</code>"
        )
    await message.reply("\n".join(lines))
