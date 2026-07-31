"""Админ-команды: начисление и списание ирисок, просмотр статистики участника.

Роутер целиком закрыт AdminFilter: для всех остальных эти команды
просто не существуют. Обычные сообщения админа проходят дальше
и считаются как у всех.
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from config import Config
from db import Database
from handlers.common import GroupF, build_profile
from texts import display_name, fmt, iriski

MAX_AMOUNT = 1_000_000

ADMIN_TRIG_RE = re.compile(r"^\s*(начислить|списать)\s+(\d+)\s*$", re.IGNORECASE)


class AdminFilter(BaseFilter):
    async def __call__(self, message: Message, config: Config) -> bool:
        user = message.from_user
        if user is None:
            return False
        if user.id in config.admin_ids:
            return True
        return bool(user.username) and user.username.lower() in config.admin_usernames


router = Router(name="admin")
router.message.filter(AdminFilter())


async def _resolve_target(message: Message, db: Database, args: list[str]):
    """Определяет, кому начисляем/списываем: reply или @username.

    Возвращает (строка users | None, оставшиеся args, текст ошибки | None).
    """
    chat_id = message.chat.id
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        target = reply.from_user
        await db.ensure_user(chat_id, target.id, target.username, target.first_name)
        return await db.get_user(chat_id, target.id), args, None
    if args and args[0].startswith("@"):
        row = await db.find_by_username(chat_id, args[0])
        if row is None:
            return None, args[1:], (
                f"Не нашёл {args[0]} в статистике этого чата. "
                "Пусть напишет хоть одно сообщение — или ответь командой "
                "на его сообщение."
            )
        return row, args[1:], None
    return None, args, (
        "Ответь командой на сообщение участника или укажи @username: "
        "<code>/give @user 50</code>"
    )


def _parse_amount(args: list[str]) -> int | None:
    if not args or not args[0].isdigit():
        return None
    amount = int(args[0])
    if not 1 <= amount <= MAX_AMOUNT:
        return None
    return amount


async def _give(message: Message, db: Database, row, amount: int, admin_id: int) -> None:
    status, balance = await db.adjust_balance(
        message.chat.id, row["user_id"], amount, "начислено админом", admin_id
    )
    name = display_name(row["first_name"], row["username"])
    if status == "ok":
        await message.reply(
            f"✅ Начислено 🍬 <b>{fmt(amount)}</b> — {name}.\n"
            f"Баланс: <b>{fmt(balance)}</b> {iriski(balance)}"
        )
    else:
        await message.reply("Не получилось — участник не найден.")


async def _take(message: Message, db: Database, row, amount: int, admin_id: int) -> None:
    status, balance = await db.adjust_balance(
        message.chat.id, row["user_id"], -amount, "списано админом (вывод)", admin_id
    )
    name = display_name(row["first_name"], row["username"])
    if status == "ok":
        await message.reply(
            f"✅ Списано 🍬 <b>{fmt(amount)}</b> у {name}.\n"
            f"Остаток: <b>{fmt(balance)}</b> {iriski(balance)}"
        )
    elif status == "insufficient":
        await message.reply(
            f"⚠️ У {name} только 🍬 <b>{fmt(balance)}</b> — "
            f"списать {fmt(amount)} не выйдет."
        )
    else:
        await message.reply("Не получилось — участник не найден.")


@router.message(GroupF, Command("give"))
async def cmd_give(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    args = (command.args or "").split()
    row, args, err = await _resolve_target(message, db, args)
    if err:
        await message.reply(err)
        return
    amount = _parse_amount(args)
    if amount is None:
        await message.reply(
            "Укажи количество: <code>/give 50</code> (ответом на сообщение) "
            "или <code>/give @user 50</code>"
        )
        return
    await _give(message, db, row, amount, message.from_user.id)


@router.message(GroupF, Command("take"))
async def cmd_take(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    args = (command.args or "").split()
    row, args, err = await _resolve_target(message, db, args)
    if err:
        await message.reply(err)
        return
    amount = _parse_amount(args)
    if amount is None:
        await message.reply(
            "Укажи количество: <code>/take 300</code> (ответом на сообщение) "
            "или <code>/take @user 300</code>"
        )
        return
    await _take(message, db, row, amount, message.from_user.id)


@router.message(GroupF, F.text.func(lambda t: bool(t) and bool(ADMIN_TRIG_RE.match(t))))
async def admin_text_ops(message: Message, db: Database, config: Config) -> None:
    """Текстовые формы: ответить «начислить 50» или «списать 300» на сообщение."""
    match = ADMIN_TRIG_RE.match(message.text or "")
    if match is None:
        return
    word = match.group(1).lower()
    amount = int(match.group(2))
    if not 1 <= amount <= MAX_AMOUNT:
        await message.reply("Слишком много 😅")
        return
    row, _, err = await _resolve_target(message, db, [])
    if err:
        await message.reply(
            "Ответь «начислить N» или «списать N» на сообщение участника."
        )
        return
    if word == "начислить":
        await _give(message, db, row, amount, message.from_user.id)
    else:
        await _take(message, db, row, amount, message.from_user.id)


@router.message(GroupF, Command("who"))
async def cmd_who(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    """Статистика любого участника: /who ответом на сообщение или /who @user."""
    args = (command.args or "").split()
    row, _, err = await _resolve_target(message, db, args)
    if err:
        await message.reply(err)
        return
    await message.reply(await build_profile(db, config, message.chat.id, row))
