"""Викторины: админ создаёт вопрос в ЛС бота, бот публикует его в чат,
первый правильный ответ в чате забирает приз.

Роутер закрыт AdminFilter — команды викторины видит только админ.
Проверка ответов участников встроена в счётчик сообщений
(handlers/counting.py -> handle_possible_answer), поэтому сообщение
и в статистику попадает, и может выиграть викторину.
"""

from __future__ import annotations

import logging
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db import Database
from handlers.admin_cmds import AdminFilter
from handlers.common import GroupF, norm
from texts import display_name, fmt, iriski

logger = logging.getLogger(__name__)

router = Router(name="quiz")
router.message.filter(AdminFilter())

MAX_PRIZE = 1000

USAGE = (
    "🧠 <b>Как создать викторину</b> (здесь, в ЛС):\n\n"
    "<code>/quiz Вопрос | ответ</code>\n"
    "Несколько верных вариантов: <code>/quiz Вопрос | ответ1; ответ2</code>\n"
    "Свой приз: <code>/quiz 5 | Вопрос | ответ</code> (по умолчанию 1 🍬)\n\n"
    "Бот опубликует вопрос в чат. Кто первым напишет правильный ответ — "
    "получит приз. Остановить и показать ответ: /quizstop"
)

# Чаты, где прямо сейчас идёт викторина — кеш, чтобы не дёргать базу
# на каждое сообщение. Заполняется при старте бота из базы.
active_chats: set[int] = set()


async def load_active_chats(db: Database) -> None:
    active_chats.clear()
    active_chats.update(await db.active_quiz_chat_ids())


def parse_quiz_args(raw: str) -> tuple[int, str, list[str], str] | None:
    """'5 | Вопрос | ответ1; ответ2' -> (приз, вопрос, норм-ответы, ответ для показа).

    Приз распознаётся, только если частей три и более и первая — целое число.
    """
    parts = [p.strip() for p in (raw or "").split("|") if p.strip()]
    if len(parts) < 2:
        return None
    prize = 1
    if len(parts) >= 3 and parts[0].isdigit():
        prize = min(max(int(parts[0]), 1), MAX_PRIZE)
        parts = parts[1:]
    question = parts[0]
    display = ""
    answers: list[str] = []
    for chunk in parts[1:]:
        for piece in chunk.split(";"):
            normalized = norm(piece)
            if normalized:
                answers.append(normalized)
                if not display:
                    display = piece.strip()
    if not question or not answers:
        return None
    return prize, question, answers, display


def quiz_text(question: str, prize: int) -> str:
    return (
        f"🧠 <b>ВИКТОРИНА!</b> Приз: 🍬 <b>{fmt(prize)}</b>\n\n"
        f"❓ {escape(question)}\n\n"
        "Кто первым напишет правильный ответ в чат — тот и забирает приз!"
    )


@router.message(F.chat.type == ChatType.PRIVATE, Command("quiz"))
async def cmd_quiz(message: Message, command: CommandObject, db: Database, bot: Bot) -> None:
    parsed = parse_quiz_args(command.args or "")
    if parsed is None:
        await message.reply(USAGE)
        return
    prize, question, answers, display = parsed

    chats = await db.known_chats()
    if not chats:
        await message.reply(
            "Я пока не вёл статистику ни в одном чате — добавь меня в группу, "
            "и пусть там напишут пару сообщений."
        )
        return

    sent = 0
    busy = 0
    for chat_id in chats:
        if await db.active_quiz(chat_id) is not None:
            busy += 1
            continue
        quiz_id = await db.create_quiz(
            chat_id, question, answers, display, prize,
            message.from_user.id if message.from_user else None, time.time(),
        )
        try:
            await bot.send_message(chat_id, quiz_text(question, prize))
        except Exception as e:
            logger.warning("Викторина не ушла в чат %s: %s", chat_id, e)
            await db.cancel_quiz(quiz_id, time.time())
            continue
        active_chats.add(chat_id)
        sent += 1

    lines = []
    if sent:
        lines.append(f"✅ Викторина опубликована! Приз: 🍬 {fmt(prize)}")
        lines.append("Остановить и показать ответ: /quizstop")
    else:
        lines.append("Не получилось опубликовать викторину.")
    if busy:
        lines.append(
            f"⚠️ Пропущено чатов с уже идущей викториной: {busy}. "
            "Сначала останови её: /quizstop"
        )
    await message.reply("\n".join(lines))


@router.message(GroupF, Command("quiz"))
async def cmd_quiz_in_group(message: Message) -> None:
    await message.reply(
        "Викторины создаются в личке, чтобы не спалить ответ 😉 "
        "Напиши мне /quiz в ЛС."
    )


@router.message(Command("quizstop"))
async def cmd_quizstop(message: Message, db: Database, bot: Bot) -> None:
    rows = await db.cancel_active_quizzes(time.time())
    if not rows:
        await message.reply("Активных викторин нет.")
        return
    for row in rows:
        chat_id = int(row["chat_id"])
        active_chats.discard(chat_id)
        try:
            await bot.send_message(
                chat_id,
                "🛑 Викторина остановлена. Правильный ответ: "
                f"<b>{escape(row['display_answer'])}</b>",
            )
        except Exception as e:
            logger.warning("Не смог сообщить об остановке в чат %s: %s", chat_id, e)
    await message.reply(f"Остановлено викторин: {len(rows)}")


async def handle_possible_answer(message: Message, db: Database) -> None:
    """Проверка ответа на активную викторину. Зовётся из счётчика сообщений."""
    chat_id = message.chat.id
    if chat_id not in active_chats:
        return
    user = message.from_user
    if user is None or user.is_bot:
        return
    quiz = await db.active_quiz(chat_id)
    if quiz is None:
        active_chats.discard(chat_id)
        return
    if norm(message.text) not in set(quiz["answers"].split("\n")):
        return
    status, prize, balance = await db.try_win_quiz(
        int(quiz["id"]), chat_id, user.id, user.username, user.first_name, time.time()
    )
    if status != "ok":
        return  # кто-то успел на мгновение раньше
    active_chats.discard(chat_id)
    await message.reply(
        f"🎉 <b>Правильно!</b> {display_name(user.first_name, user.username)} "
        f"первым дал верный ответ («{escape(quiz['display_answer'])}») "
        f"и получает 🍬 <b>+{fmt(prize)}</b>!\n"
        f"Баланс: <b>{fmt(balance)}</b> {iriski(balance)}"
    )
