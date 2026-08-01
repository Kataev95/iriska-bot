"""Игры и бонусы: ежедневный бонус, слоты 🎰, дуэли на ириски.

Все операции идут через баланс и журнал ledger. Баланс не может уйти
в минус. Игровые сообщения («казино 10», «принять» и т.п.) перехватываются
до счётчика статистики и в неё не попадают.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import Config
from db import Database
from handlers.common import GroupF, today_day, trig
from texts import display_name, fmt, iriski

router = Router(name="games")

CASINO_RE = re.compile(r"^\s*(?:казино|слоты)\s+(\d+)\s*$", re.IGNORECASE)
DUEL_RE = re.compile(r"^\s*дуэль\s+(\d+)\s*$", re.IGNORECASE)

BONUS_TRIGGERS = {"бонус", "ежедневный бонус"}
ACCEPT_TRIGGERS = {"принять", "принимаю"}
DECLINE_TRIGGERS = {"отказ", "отказываюсь"}
CANCEL_TRIGGERS = {"отмена дуэли", "отменить дуэль"}
GAMES_TRIGGERS = {"игры", "правила игр"}
CASINO_HINT_TRIGGERS = {"казино", "слоты"}

# кулдаун казино держим в памяти: после рестарта просто обнулится
_last_casino: dict[tuple[int, int], float] = {}


def mention(user_id: int, first_name: str | None, username: str | None) -> str:
    name = (first_name or "").strip() or (("@" + username) if username else "боец")
    if len(name) > 25:
        name = name[:24] + "…"
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'


# ---------- слоты: разбор значения барабанов ----------

def slot_reels(value: int) -> tuple[int, int, int]:
    """Значение дайса 1..64 -> три барабана (0=bar, 1=виноград, 2=лимон, 3=семёрка)."""
    v = value - 1
    return (v % 4, (v // 4) % 4, (v // 16) % 4)


def slot_multiplier(value: int) -> tuple[int, str]:
    """Множитель выплаты и подпись результата."""
    a, b, c = slot_reels(value)
    if (a, b, c) == (3, 3, 3):
        return 10, "7️⃣7️⃣7️⃣ ДЖЕКПОТ!"
    if a == b == c:
        return 5, "Три в ряд!"
    if (a == 3) + (b == 3) + (c == 3) == 2:
        return 1, "Две семёрки — ставка вернулась."
    return 0, "Мимо 😔"


# ---------- ежедневный бонус ----------

@router.message(GroupF, Command("bonus"))
@router.message(GroupF, trig(BONUS_TRIGGERS))
async def cmd_bonus(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if not config.bonus_enabled:
        await message.reply("Ежедневные бонусы сейчас выключены.")
        return
    lo = max(config.bonus_min, 0)
    hi = max(config.bonus_max, lo)
    amount = lo + (secrets.randbelow(hi - lo + 1) if hi > lo else 0)
    status, balance = await db.claim_bonus(
        message.chat.id, user.id, user.username, user.first_name,
        today_day(config), amount,
    )
    if status == "already":
        await message.reply(
            f"Ты уже забирал бонус сегодня 😉\n"
            f"Баланс: 🍬 <b>{fmt(balance)}</b>. Возвращайся завтра!"
        )
    else:
        await message.reply(
            f"🎁 Ежедневный бонус: <b>+{amount}</b> 🍬\n"
            f"Баланс: <b>{fmt(balance)}</b> {iriski(balance)}. Завтра будет ещё!"
        )


# ---------- казино (слоты) ----------

@router.message(GroupF, Command("casino"))
@router.message(GroupF, F.text.func(lambda t: bool(t) and bool(CASINO_RE.match(t))))
async def cmd_casino(
    message: Message, db: Database, config: Config,
    command: CommandObject | None = None,
) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if not config.games_enabled:
        await message.reply("Игры сейчас выключены.")
        return

    raw = None
    if command is not None:
        args = (command.args or "").split()
        raw = args[0] if args else None
    else:
        m = CASINO_RE.match(message.text or "")
        raw = m.group(1) if m else None
    if raw is None or not raw.isdigit():
        await message.reply(
            "🎰 Слоты: напиши <b>«казино 10»</b> или /casino 10 — ставка спишется с баланса.\n"
            "Выплаты: 7️⃣7️⃣7️⃣ — х10, три одинаковых — х5, две семёрки — возврат ставки."
        )
        return
    bet = int(raw)
    if not config.casino_min_bet <= bet <= config.casino_max_bet:
        await message.reply(
            f"Ставка от {fmt(config.casino_min_bet)} до {fmt(config.casino_max_bet)} 🍬"
        )
        return

    key = (message.chat.id, user.id)
    now = time.time()
    elapsed = now - _last_casino.get(key, 0.0)
    if elapsed < config.casino_cooldown:
        await message.reply(f"Не так быстро 🎰 Подожди {int(config.casino_cooldown - elapsed) + 1} сек.")
        return

    await db.ensure_user(message.chat.id, user.id, user.username, user.first_name)
    status, balance = await db.adjust_balance(
        message.chat.id, user.id, -bet, "казино: ставка", None
    )
    if status != "ok":
        await message.reply(
            f"Не хватает ирисок: на балансе 🍬 <b>{fmt(balance)}</b>. "
            f"Общайся в чате или забери ежедневный бонус («бонус»)."
        )
        return
    _last_casino[key] = now

    try:
        dice_msg = await message.answer_dice(emoji="🎰")
    except Exception:
        # не получилось крутануть — возвращаем ставку
        await db.adjust_balance(message.chat.id, user.id, bet, "казино: возврат ставки", None)
        await message.reply("Автомат заело 🎰 Ставка возвращена, попробуй ещё раз.")
        return

    mult, label = slot_multiplier(dice_msg.dice.value if dice_msg.dice else 0)
    await asyncio.sleep(2.2)  # даём анимации докрутиться

    if mult == 0:
        await dice_msg.reply(
            f"{label}\nСтавка 🍬 {fmt(bet)} сгорела. Баланс: <b>{fmt(balance)}</b>"
        )
    elif mult == 1:
        _, balance = await db.adjust_balance(
            message.chat.id, user.id, bet, "казино: возврат ставки", None
        )
        await dice_msg.reply(f"{label}\nБаланс: <b>{fmt(balance)}</b>")
    else:
        payout = bet * mult
        _, balance = await db.adjust_balance(
            message.chat.id, user.id, payout, "казино: выигрыш", None
        )
        await dice_msg.reply(
            f"🎉 {label} Выигрыш: <b>+{fmt(payout)}</b> 🍬\nБаланс: <b>{fmt(balance)}</b>"
        )


@router.message(GroupF, trig(CASINO_HINT_TRIGGERS))
async def casino_hint(message: Message, config: Config) -> None:
    if not config.games_enabled:
        return
    await message.reply(
        f"🎰 Напиши <b>«казино N»</b>, где N — ставка от {fmt(config.casino_min_bet)} "
        f"до {fmt(config.casino_max_bet)} 🍬\n"
        "Выплаты: 7️⃣7️⃣7️⃣ — х10, три одинаковых — х5, две семёрки — возврат ставки."
    )


# ---------- дуэли ----------

@router.message(GroupF, Command("duel"))
@router.message(GroupF, F.text.func(lambda t: bool(t) and bool(DUEL_RE.match(t))))
async def cmd_duel(
    message: Message, db: Database, config: Config,
    command: CommandObject | None = None,
) -> None:
    challenger = message.from_user
    if challenger is None or challenger.is_bot:
        return
    if not config.games_enabled:
        await message.reply("Игры сейчас выключены.")
        return
    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        await message.reply(
            "⚔️ Дуэль объявляется ответом на сообщение соперника: <b>«дуэль 10»</b>"
        )
        return
    target = reply.from_user
    if target.is_bot:
        await message.reply("С ботами не дерёмся 🤖")
        return
    if target.id == challenger.id:
        await message.reply("Сам с собой? Так нельзя 😅")
        return

    raw = None
    if command is not None:
        args = (command.args or "").split()
        raw = args[0] if args else None
    else:
        m = DUEL_RE.match(message.text or "")
        raw = m.group(1) if m else None
    if raw is None or not raw.isdigit():
        await message.reply("Укажи ставку: <b>«дуэль 10»</b> ответом на сообщение соперника.")
        return
    amount = int(raw)
    if not config.duel_min_bet <= amount <= config.duel_max_bet:
        await message.reply(
            f"Ставка дуэли от {fmt(config.duel_min_bet)} до {fmt(config.duel_max_bet)} 🍬"
        )
        return

    now = time.time()
    await db.expire_old_duels(message.chat.id, now, config.duel_ttl)
    if await db.has_pending_duel(message.chat.id, challenger.id):
        await message.reply("У тебя уже есть активная дуэль — сначала разберись с ней («отмена дуэли»).")
        return
    if await db.has_pending_duel(message.chat.id, target.id):
        await message.reply("У соперника уже есть активная дуэль. Подожди.")
        return

    await db.ensure_user(message.chat.id, challenger.id, challenger.username, challenger.first_name)
    await db.ensure_user(message.chat.id, target.id, target.username, target.first_name)
    ch_row = await db.get_user(message.chat.id, challenger.id)
    if ch_row["balance"] < amount:
        await message.reply(f"У тебя только 🍬 <b>{fmt(ch_row['balance'])}</b> — не хватает на ставку.")
        return
    tg_row = await db.get_user(message.chat.id, target.id)
    if tg_row["balance"] < amount:
        await message.reply(
            f"У соперника нет 🍬 {fmt(amount)} — выбери ставку поменьше."
        )
        return

    await db.create_duel(message.chat.id, challenger.id, target.id, amount, now)
    minutes = max(int(config.duel_ttl // 60), 1)
    await message.reply(
        f"⚔️ <b>Дуэль!</b> {display_name(challenger.first_name, challenger.username)} "
        f"ставит 🍬 <b>{fmt(amount)}</b> против "
        f"{mention(target.id, target.first_name, target.username)}.\n"
        f"Ответь <b>«принять»</b> или <b>«отказ»</b> — вызов действует {minutes} мин."
    )


@router.message(GroupF, Command("accept"))
@router.message(GroupF, trig(ACCEPT_TRIGGERS))
async def cmd_accept(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot or not config.games_enabled:
        return
    now = time.time()
    await db.expire_old_duels(message.chat.id, now, config.duel_ttl)
    duel = await db.pending_duel_for_target(message.chat.id, user.id)
    if duel is None:
        return  # обычное слово «принять» без вызова — молчим
    amount = duel["amount"]
    challenger_id = duel["challenger_id"]

    winner_id = secrets.choice([challenger_id, user.id])
    loser_id = user.id if winner_id == challenger_id else challenger_id
    status, winner_balance, loser_balance = await db.settle_duel(
        duel["id"], message.chat.id, winner_id, loser_id, amount, now
    )
    if status != "ok":
        await message.reply("У одной из сторон уже нет ставки — дуэль отменена 🕊")
        return

    w_row = await db.get_user(message.chat.id, winner_id)
    l_row = await db.get_user(message.chat.id, loser_id)
    w_name = display_name(w_row["first_name"], w_row["username"])
    l_name = display_name(l_row["first_name"], l_row["username"])
    coin = "Орёл" if winner_id == challenger_id else "Решка"
    await message.reply(
        f"🪙 Подбрасываю монетку… <b>{coin}!</b>\n"
        f"🏆 Побеждает <b>{w_name}</b> — забирает 🍬 <b>{fmt(amount)}</b> у {l_name}.\n"
        f"Баланс: {w_name} — {fmt(winner_balance)}, {l_name} — {fmt(loser_balance)}"
    )


@router.message(GroupF, Command("decline"))
@router.message(GroupF, trig(DECLINE_TRIGGERS))
async def cmd_decline(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot or not config.games_enabled:
        return
    now = time.time()
    await db.expire_old_duels(message.chat.id, now, config.duel_ttl)
    duel = await db.pending_duel_for_target(message.chat.id, user.id)
    if duel is None:
        return
    await db.set_duel_status(duel["id"], "declined", now)
    await message.reply("🕊 Дуэль отклонена — ириски целы.")


@router.message(GroupF, trig(CANCEL_TRIGGERS))
async def cmd_cancel_duel(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    now = time.time()
    await db.expire_old_duels(message.chat.id, now, config.duel_ttl)
    duel = await db.pending_duel_by_challenger(message.chat.id, user.id)
    if duel is None:
        return
    await db.set_duel_status(duel["id"], "cancelled", now)
    await message.reply("Дуэль отменена.")


# ---------- правила игр ----------

@router.message(Command("games"))
@router.message(GroupF, trig(GAMES_TRIGGERS))
async def cmd_games(message: Message, config: Config) -> None:
    await message.reply(
        "🎮 <b>Игры и бонусы</b>\n\n"
        f"🎁 <b>Бонус</b> — напиши «бонус»: раз в день +{config.bonus_min}–{max(config.bonus_max, config.bonus_min)} 🍬\n\n"
        f"🎰 <b>Слоты</b> — «казино 10» (ставка {fmt(config.casino_min_bet)}–{fmt(config.casino_max_bet)} 🍬):\n"
        "7️⃣7️⃣7️⃣ — х10 • три одинаковых — х5 • две семёрки — возврат\n\n"
        f"⚔️ <b>Дуэль</b> — ответь «дуэль 10» на сообщение соперника "
        f"(ставка {fmt(config.duel_min_bet)}–{fmt(config.duel_max_bet)} 🍬). "
        "Тот пишет «принять» или «отказ». Монетка решает — победитель забирает банк.\n\n"
        "Все ириски честно ходят между балансами и записываются в журнал 🍬"
    )
