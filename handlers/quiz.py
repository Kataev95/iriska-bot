"""Викторины: админ создаёт вопросы в ЛС бота, бот публикует их в чат,
первый правильный ответ в чате забирает приз.

Поддерживаются пачки: /quiz с несколькими строками — каждая строка вопрос.
Бот проводит их по очереди: следующий вопрос публикуется после победы
(или после /quizskip). Очередь хранится в базе и переживает рестарт.

Роутер закрыт AdminFilter — команды викторины видит только админ.
Проверка ответов участников встроена в счётчик сообщений
(handlers/counting.py -> handle_possible_answer), поэтому сообщение
и в статистику попадает, и может выиграть викторину.
"""

from __future__ import annotations

import asyncio
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
NEXT_QUESTION_DELAY = 3  # секунд между победой и следующим вопросом

USAGE = (
    "🧠 <b>Как создать викторину</b> (здесь, в ЛС):\n\n"
    "Один вопрос:\n"
    "<code>/quiz Вопрос | ответ</code>\n\n"
    "Пачка — каждый вопрос с новой строки:\n"
    "<code>/quiz\n"
    "Столица Австралии? | Канберра\n"
    "2+2? | 4; четыре\n"
    "5 | Сложный вопрос? | ответ</code>\n\n"
    "Бот публикует вопросы по очереди: следующий появляется после победы.\n"
    "Несколько верных вариантов — через <code>;</code> • Свой приз — "
    "<code>5 | Вопрос | ответ</code> (по умолчанию 1 🍬)\n\n"
    "/quizskip — пропустить вопрос • /quizstop — остановить всё"
)

# Чаты, где прямо сейчас идёт викторина — кеш, чтобы не дёргать базу
# на каждое сообщение. Заполняется при старте бота из базы.
active_chats: set[int] = set()


async def load_active_chats(db: Database) -> None:
    active_chats.clear()
    active_chats.update(await db.active_quiz_chat_ids())


async def resume_queues(bot: Bot, db: Database) -> None:
    """После рестарта: если в чате остались вопросы без активного — продолжаем."""
    for chat_id in await db.chats_with_queued():
        row = await db.activate_next_quiz(chat_id, time.time())
        if row is None:
            continue
        try:
            await bot.send_message(
                chat_id,
                quiz_text(row["question"], row["prize"], row["seq"], row["total"]),
            )
            active_chats.add(chat_id)
        except Exception as e:
            logger.warning("Не смог продолжить викторину в чате %s: %s", chat_id, e)


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


def parse_quiz_pack(raw: str) -> tuple[list[tuple[int, str, list[str], str]], list[int]]:
    """Разбирает многострочный ввод. Возвращает (вопросы, номера ошибочных строк)."""
    items: list[tuple[int, str, list[str], str]] = []
    bad: list[int] = []
    for line_no, line in enumerate((raw or "").splitlines(), 1):
        if not line.strip():
            continue
        parsed = parse_quiz_args(line)
        if parsed is None:
            bad.append(line_no)
        else:
            items.append(parsed)
    return items, bad


def quiz_text(question: str, prize: int, seq: int = 1, total: int = 1) -> str:
    header = "🧠 <b>ВИКТОРИНА!</b>"
    if total > 1:
        header += f" Вопрос {seq}/{total}."
    header += f" Приз: 🍬 <b>{fmt(prize)}</b>"
    return (
        f"{header}\n\n"
        f"❓ {escape(question)}\n\n"
        "Кто первым напишет правильный ответ в чат — тот и забирает приз!"
    )


@router.message(F.chat.type == ChatType.PRIVATE, Command("quiz"))
async def cmd_quiz(message: Message, command: CommandObject, db: Database, bot: Bot) -> None:
    items, bad = parse_quiz_pack(command.args or "")
    if bad:
        await message.reply(
            "⚠️ Не понял строки: " + ", ".join(map(str, bad)) +
            ".\nФормат каждой строки: <code>Вопрос | ответ</code> — поправь и пришли заново."
        )
        return
    if not items:
        await message.reply(USAGE)
        return

    chats = await db.known_chats()
    if not chats:
        await message.reply(
            "Я пока не вёл статистику ни в одном чате — добавь меня в группу, "
            "и пусть там напишут пару сообщений."
        )
        return

    now = time.time()
    posted = 0
    appended = 0
    for chat_id in chats:
        had_active = (await db.active_quiz(chat_id)) is not None
        await db.enqueue_quizzes(chat_id, items, message.from_user.id if message.from_user else None, now)
        if had_active:
            appended += 1
            continue
        row = await db.activate_next_quiz(chat_id, now)
        if row is None:
            continue
        try:
            await bot.send_message(
                chat_id,
                quiz_text(row["question"], row["prize"], row["seq"], row["total"]),
            )
            active_chats.add(chat_id)
            posted += 1
        except Exception as e:
            logger.warning("Викторина не ушла в чат %s: %s", chat_id, e)
            await db.cancel_quiz(int(row["id"]), now)

    lines = []
    if len(items) == 1:
        lines.append("✅ Викторина опубликована!" if posted else "Вопрос добавлен.")
    else:
        lines.append(
            f"✅ Принял {len(items)} вопросов — публикую по очереди: "
            "следующий появляется после победы."
        )
    if appended:
        lines.append(
            "⚠️ В чате уже шла викторина — новые вопросы добавлены в очередь."
        )
    lines.append("Пропустить вопрос: /quizskip • Остановить всё: /quizstop")
    await message.reply("\n".join(lines))


@router.message(GroupF, Command("quiz"))
async def cmd_quiz_in_group(message: Message) -> None:
    await message.reply(
        "Викторины создаются в личке, чтобы не спалить ответ 😉 "
        "Напиши мне /quiz в ЛС."
    )


@router.message(Command("quizskip"))
async def cmd_quizskip(message: Message, db: Database, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        chat_ids = await db.active_quiz_chat_ids()
    else:
        chat_ids = [message.chat.id]

    skipped = 0
    for chat_id in chat_ids:
        quiz = await db.active_quiz(chat_id)
        if quiz is None:
            continue
        now = time.time()
        await db.cancel_quiz(int(quiz["id"]), now)
        skipped += 1
        nxt = await db.activate_next_quiz(chat_id, now)
        try:
            await bot.send_message(
                chat_id,
                "⏭ Вопрос пропущен. Ответ был: "
                f"<b>{escape(quiz['display_answer'])}</b>",
            )
            if nxt is not None:
                await bot.send_message(
                    chat_id,
                    quiz_text(nxt["question"], nxt["prize"], nxt["seq"], nxt["total"]),
                )
        except Exception as e:
            logger.warning("Не смог написать в чат %s: %s", chat_id, e)
        if nxt is None:
            active_chats.discard(chat_id)

    if skipped:
        await message.reply(f"⏭ Пропущено вопросов: {skipped}")
    else:
        await message.reply("Активных викторин нет.")


@router.message(Command("quizstop"))
async def cmd_quizstop(message: Message, db: Database, bot: Bot) -> None:
    actives, queued = await db.cancel_active_quizzes(time.time())
    if not actives and queued == 0:
        await message.reply("Активных викторин нет.")
        return
    for row in actives:
        chat_id = int(row["chat_id"])
        active_chats.discard(chat_id)
        text = (
            "🛑 Викторина остановлена. Правильный ответ: "
            f"<b>{escape(row['display_answer'])}</b>"
        )
        if queued:
            text += "\nОчередь вопросов очищена."
        try:
            await bot.send_message(chat_id, text)
        except Exception as e:
            logger.warning("Не смог сообщить об остановке в чат %s: %s", chat_id, e)
    parts = [f"Остановлено викторин: {len(actives)}"]
    if queued:
        parts.append(f"удалено из очереди: {queued}")
    await message.reply(", ".join(parts) + ".")


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
    await message.reply(
        f"🎉 <b>Правильно!</b> {display_name(user.first_name, user.username)} "
        f"первым дал верный ответ («{escape(quiz['display_answer'])}») "
        f"и получает 🍬 <b>+{fmt(prize)}</b>!\n"
        f"Баланс: <b>{fmt(balance)}</b> {iriski(balance)}"
    )

    # Очередь: публикуем следующий вопрос после короткой паузы
    nxt = await db.activate_next_quiz(chat_id, time.time())
    if nxt is None:
        active_chats.discard(chat_id)
        remaining_done = quiz["total"] > 1 and quiz["seq"] == quiz["total"]
        if remaining_done:
            try:
                await message.answer("🏁 Викторина окончена — все вопросы отыграны!")
            except Exception:
                pass
        return
    await asyncio.sleep(NEXT_QUESTION_DELAY)
    try:
        await message.answer(
            quiz_text(nxt["question"], nxt["prize"], nxt["seq"], nxt["total"])
        )
    except Exception as e:
        logger.warning("Следующий вопрос не ушёл в чат %s: %s", chat_id, e)
