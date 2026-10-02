"""Магазин ирисок: покупка услуг за ириски.

Схема работы: участник открывает /shop (или пишет «магазин»), выбирает
товар кнопкой, подтверждает покупку — ириски списываются с баланса,
создаётся заказ со статусом new, а админам уходит уведомление в личку.
Админы ведут заказы командами /orders, /order_done и /order_refund.

Сообщения магазина перехватываются до счётчика статистики и в неё не попадают.
"""

from __future__ import annotations

import logging
import time
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from config import Config
from db import Database
from handlers.common import GroupF, mention, trig
from shop import ShopItem, catalog, find_item
from texts import display_name, fmt, iriski

logger = logging.getLogger(__name__)

router = Router(name="shop")

SHOP_TRIGGERS = {"магазин", "шоп", "лавка"}

# Защита от двойного нажатия «Купить»: одна покупка в пару секунд.
PURCHASE_COOLDOWN = 3.0
_last_purchase: dict[tuple[int, int], float] = {}


# ---------- тексты ----------

def shop_text(items: tuple[ShopItem, ...], balance: int, contact: str) -> str:
    lines = [
        "🛍 <b>Магазин ирисок</b>",
        "",
        f"💰 Твой баланс: <b>{fmt(balance)}</b> {iriski(balance)}",
        "",
    ]
    for item in items:
        lines.append(f"{item.label()} — <b>{fmt(item.price)}</b> 🍬")
    lines.extend([
        "",
        "👇 Жми кнопку, чтобы посмотреть детали и подтвердить покупку.",
        f"📩 После покупки админ свяжется с тобой: {contact}",
    ])
    return "\n".join(lines)


def confirm_text(item: ShopItem, balance: int, contact: str) -> str:
    lines = [
        "🧾 <b>Подтверди покупку</b>",
        "",
        f"{item.label()} — <b>{fmt(item.price)}</b> 🍬",
        item.summary,
        "",
    ]
    if balance >= item.price:
        left = balance - item.price
        lines.extend([
            f"💰 Баланс: <b>{fmt(balance)}</b> → останется <b>{fmt(left)}</b> "
            f"{iriski(left)}",
            "",
            f"📩 После покупки админ свяжется с тобой: {contact}",
        ])
    else:
        need = item.price - balance
        lines.extend([
            f"⚠️ Не хватает <b>{fmt(need)}</b> {iriski(need)}: "
            f"на балансе сейчас <b>{fmt(balance)}</b>.",
            "Заработать ириски поможет /help.",
        ])
    return "\n".join(lines)


def receipt_text(
    item: ShopItem, order_id: int, balance: int, buyer: str, contact: str,
) -> str:
    return "\n".join([
        "✅ <b>Покупка оформлена!</b>",
        "",
        f"🧾 Заказ <b>№{order_id}</b> — {item.label()}",
        f"💸 Списано: <b>{fmt(item.price)}</b> 🍬",
        f"💰 Остаток: <b>{fmt(balance)}</b> {iriski(balance)}",
        f"👤 Покупатель: {buyer}",
        "",
        f"📩 Админ свяжется с вами: {contact}",
    ])


def shortage_text(item: ShopItem, balance: int) -> str:
    need = item.price - balance
    return (
        f"⚠️ Не хватает <b>{fmt(need)}</b> {iriski(need)} на {item.label()} "
        f"(цена <b>{fmt(item.price)}</b> 🍬, на балансе <b>{fmt(balance)}</b>).\n"
        "Заработать ириски поможет /help."
    )


# ---------- клавиатуры ----------

def catalog_keyboard(items: tuple[ShopItem, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{item.label()} — {fmt(item.price)} 🍬",
            callback_data=f"shop:item:{item.code}",
        )]
        for item in items
    ])


def confirm_keyboard(item: ShopItem, balance: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if balance >= item.price:
        rows.append([InlineKeyboardButton(
            text=f"✅ Купить за {fmt(item.price)} 🍬",
            callback_data=f"shop:buy:{item.code}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅️ Назад в магазин", callback_data="shop:menu",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🛍 Открыть магазин", callback_data="shop:menu"),
    ]])


# ---------- уведомления админам ----------

async def notify_admins(
    bot: Bot, config: Config, *,
    chat: Chat, user: User, item: ShopItem, order_id: int, balance: int,
) -> None:
    """Сообщает админам в личку о новом заказе. Молча переживает любые сбои."""
    if not config.shop_notify_admins or not config.admin_ids:
        return
    buyer = mention(user.id, user.first_name, user.username)
    if user.username:
        buyer += f" (@{user.username})"
    chat_name = escape(chat.title or chat.username or str(chat.id))
    text = "\n".join([
        f"🛍 <b>Новый заказ №{order_id}</b>",
        "",
        f"{item.label()} — <b>{fmt(item.price)}</b> 🍬",
        item.summary,
        "",
        f"👤 Покупатель: {buyer} (id <code>{user.id}</code>)",
        f"💬 Чат: <b>{chat_name}</b>",
        f"💰 Остаток у покупателя: <b>{fmt(balance)}</b> {iriski(balance)}",
        "",
        f"✍️ Напиши покупателю в чате, потом закрой заказ: "
        f"/order_done {order_id}",
        "📋 Все заказы чата: /orders",
    ])
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # админ не начинал диалог с ботом и т.п.
            logger.warning(
                "Не смог сообщить админу %s о заказе №%s: %s",
                admin_id, order_id, exc,
            )


# ---------- команды ----------

async def _safe_edit(
    message: Message, text: str, markup: InlineKeyboardMarkup,
) -> bool:
    """Правит сообщение магазина, прощая повторные нажатия без изменений.

    False — сообщение обновить не удалось (например, оно слишком старое).
    """
    try:
        await message.edit_text(text, reply_markup=markup)
        return True
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return True
        logger.warning("Не смог обновить сообщение магазина: %s", exc)
        return False


@router.message(GroupF, Command("shop"))
@router.message(GroupF, trig(SHOP_TRIGGERS))
async def cmd_shop(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if not config.shop_enabled:
        await message.reply("🛍 Магазин сейчас закрыт — заходи позже.")
        return
    row = await db.get_user(message.chat.id, user.id)
    balance = int(row["balance"]) if row else 0
    items = catalog(config.shop_prices)
    await message.reply(
        shop_text(items, balance, config.shop_contact),
        reply_markup=catalog_keyboard(items),
    )


@router.message(GroupF, Command("buy"))
async def cmd_buy(
    message: Message, command: CommandObject, db: Database, config: Config,
) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if not config.shop_enabled:
        await message.reply("🛍 Магазин сейчас закрыт — заходи позже.")
        return
    items = catalog(config.shop_prices)
    raw = (command.args or "").strip().split()
    item = find_item(items, raw[0]) if raw else None
    if item is None:
        hint = ", ".join(f"/buy {i.code}" for i in items[:3])
        await message.reply(
            "Не знаю такого товара 🤔\n"
            f"Смотри каталог: /shop — например {hint}…"
        )
        return
    row = await db.get_user(message.chat.id, user.id)
    balance = int(row["balance"]) if row else 0
    await message.reply(
        confirm_text(item, balance, config.shop_contact),
        reply_markup=confirm_keyboard(item, balance),
    )


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("shop:"))
async def on_shop_callback(
    callback: CallbackQuery, db: Database, config: Config, bot: Bot,
) -> None:
    message = callback.message
    user = callback.from_user
    if message is None or user is None or user.is_bot:
        await callback.answer()
        return
    if not config.shop_enabled:
        await callback.answer("Магазин сейчас закрыт", show_alert=True)
        return

    parts = (callback.data or "").split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    code = parts[2] if len(parts) > 2 else ""
    items = catalog(config.shop_prices)
    row = await db.get_user(message.chat.id, user.id)
    balance = int(row["balance"]) if row else 0

    if action == "menu":
        await _safe_edit(
            message, shop_text(items, balance, config.shop_contact),
            catalog_keyboard(items),
        )
        await callback.answer()
        return

    item = find_item(items, code)
    if item is None:
        await callback.answer("Товар пропал из магазина 🤷", show_alert=True)
        return

    if action == "item":
        await _safe_edit(
            message, confirm_text(item, balance, config.shop_contact),
            confirm_keyboard(item, balance),
        )
        await callback.answer()
        return

    if action != "buy":
        await callback.answer()
        return

    key = (message.chat.id, user.id)
    now = time.time()
    if now - _last_purchase.get(key, 0.0) < PURCHASE_COOLDOWN:
        await callback.answer("Уже оформляю, секунду…")
        return
    if balance < item.price:
        await _safe_edit(message, shortage_text(item, balance), back_keyboard())
        await callback.answer("Недостаточно ирисок", show_alert=True)
        return

    status, new_balance, order_id = await db.create_order(
        message.chat.id, user.id, user.username, user.first_name,
        item.code, item.title, item.price,
    )
    if status != "ok" or order_id is None:
        # Баланс мог измениться, пока человек думал над подтверждением.
        current = new_balance if status == "insufficient" else balance
        await _safe_edit(message, shortage_text(item, current), back_keyboard())
        await callback.answer("Недостаточно ирисок", show_alert=True)
        return

    _last_purchase[key] = now
    receipt = receipt_text(
        item, order_id, new_balance,
        display_name(user.first_name, user.username),
        config.shop_contact,
    )
    if not await _safe_edit(message, receipt, back_keyboard()):
        # Сообщение не отредактировалось — покупатель всё равно должен
        # получить чек с номером заказа.
        try:
            await message.reply(receipt, reply_markup=back_keyboard())
        except Exception:
            logger.exception("Не смог отправить чек по заказу №%s", order_id)
    await callback.answer(f"Куплено! Заказ №{order_id}")
    await notify_admins(
        bot, config, chat=message.chat, user=user,
        item=item, order_id=order_id, balance=new_balance,
    )
