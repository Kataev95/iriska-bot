"""Админ-команды: начисление и списание ирисок, статистика, заказы магазина.

Роутер целиком закрыт AdminFilter: для всех остальных эти команды
просто не существуют. Обычные сообщения админа проходят дальше
и считаются как у всех.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from config import Config
from db import Database
from handlers.common import GroupF, build_profile, mention, replied_message
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
    reply = replied_message(message)
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


# ---------- заказы магазина ----------

ORDER_STATUS = {
    "new": "🆕 новый",
    "done": "✅ выполнен",
    "refunded": "↩️ возврат",
}


def _order_when(raw: str | None, config: Config) -> str:
    """«2026-10-02 11:30:00» (UTC из базы) -> «02.10 14:30» по часовому поясу бота."""
    try:
        created = datetime.fromisoformat(raw or "")
    except ValueError:
        return ""
    local = created.replace(tzinfo=timezone.utc).astimezone(config.tz)
    return local.strftime("%d.%m %H:%M")


def _order_line(order, config: Config) -> str:
    who = "—"
    buyer = order["buyer_name"] or order["buyer_username"]
    if buyer:
        who = mention(order["user_id"], order["buyer_name"], order["buyer_username"])
        if order["buyer_username"]:
            who += f" (@{order['buyer_username']})"
    elif order["user_id"]:
        who = f"id <code>{order['user_id']}</code>"
    when = _order_when(order["created_at"], config)
    head = f"<b>№{order['id']}</b> — {order['item_title']} — 🍬 {fmt(order['price'])}"
    status = "" if order["status"] == "new" else f" — {ORDER_STATUS.get(order['status'], order['status'])}"
    return f"{head}{status}\n👤 {who}{', ' + when if when else ''}"


@router.message(GroupF, Command("orders"))
async def cmd_orders(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    """Новые заказы магазина в этом чате: /orders (или /orders all — все)."""
    show_all = (command.args or "").strip().lower() in {"all", "все", "всё"}
    orders = await db.list_orders(message.chat.id, "new" if not show_all else "", 20)
    if not orders:
        await message.reply(
            "Заказов нет 🎉" if show_all else "Новых заказов нет 🎉"
        )
        return
    title = "Все заказы чата" if show_all else "Новые заказы"
    lines = [f"🛍 <b>{title}</b> — {len(orders)}", ""]
    lines.extend(_order_line(o, config) for o in orders)
    if not show_all:
        lines.extend([
            "",
            "Закрыть: <code>/order_done 12</code> • "
            "Вернуть ириски: <code>/order_refund 12</code>",
        ])
    await message.reply("\n".join(lines))


async def _order_action(
    message: Message, raw_args: str, db: Database, *, refund: bool,
) -> None:
    args = (raw_args or "").split()
    if not args or not args[0].lstrip("#").isdigit():
        cmd = "order_refund" if refund else "order_done"
        await message.reply(
            f"Укажи номер заказа: <code>/{cmd} 12</code> "
            "(номер виден в /orders)"
        )
        return
    order_id = int(args[0].lstrip("#"))
    if refund:
        status, balance = await db.refund_order(
            order_id, message.chat.id, message.from_user.id
        )
        if status == "ok":
            await message.reply(
                f"↩️ Заказ №{order_id} отменён, ириски вернулись покупателю.\n"
                f"Его баланс: <b>{fmt(balance)}</b> {iriski(balance)}"
            )
        else:
            await message.reply(
                f"Заказ №{order_id} не найден среди новых — "
                "проверь номер в /orders"
            )
        return
    done = await db.complete_order(order_id, message.chat.id, message.from_user.id)
    if done:
        await message.reply(f"✅ Заказ №{order_id} отмечен выполненным")
    else:
        await message.reply(
            f"Заказ №{order_id} не найден среди новых — проверь номер в /orders"
        )


@router.message(GroupF, Command("order_done"))
async def cmd_order_done(
    message: Message, command: CommandObject, db: Database, config: Config,
) -> None:
    """Отмечает заказ выполненным: /order_done 12."""
    await _order_action(message, command.args or "", db, refund=False)


@router.message(GroupF, Command("order_refund"))
async def cmd_order_refund(
    message: Message, command: CommandObject, db: Database, config: Config,
) -> None:
    """Отменяет заказ и возвращает ириски: /order_refund 12."""
    await _order_action(message, command.args or "", db, refund=True)


# ---------- разовая амнистия после простоя (ноябрь 2026) ----------

@router.message(Command("pet_amnesty"))
async def cmd_pet_amnesty(
    message: Message, command: CommandObject, db: Database, config: Config,
) -> None:
    """Ручное восстановление питомцев + 100 🍬 каждому владельцу в его чате.

    Чтобы не допустить ошибочного массового начисления, требуется точный
    Telegram ID администратора из ADMIN_IDS, личка и явное подтверждение.
    """
    from html import escape

    from pet_amnesty import apply, backup_database, preview

    if message.from_user is None or message.from_user.id not in config.admin_ids:
        await message.reply(
            "🔒 Эта операция доступна только ID из ADMIN_IDS. "
            "Узнай свой ID командой /id и добавь его в настройки бота."
        )
        return
    if message.chat.type != "private":
        await message.reply("🔒 Запусти /pet_amnesty в личке бота.")
        return

    info = await preview(db)
    if info["applied"]:
        await message.reply(
            "✅ Амнистия за ноябрь 2026 уже была проведена. "
            "Второй раз 100 ирисок не начисляю.\n"
            f"Отметка: <code>{escape(str(info['marker']))}</code>"
        )
        return
    if info["pets"] == 0:
        await message.reply(
            "⚠️ В этой базе нет ни одного активного питомца. "
            "Начисления не делал. Проверь DB_PATH и подключённый постоянный "
            "том с прежней базой данных."
        )
        return

    if (command.args or "").strip() != "APPLY_2026_11":
        await message.reply(
            "🐾 <b>Амнистия питомцев за простой бота</b>\n\n"
            f"Всего активных питомцев (включая яйца): <b>{info['pets']}</b>\n"
            f"Сбежавших сейчас: <b>{info['away']}</b>\n"
            f"Из них серия доступна лишь по рекорду: <b>{info['fallback']}</b>\n"
            f"Получателей (уникально в каждом чате): <b>{info['owners']}</b>\n"
            f"К начислению: <b>{info['total_bonus']}</b> 🍬\n\n"
            "Серии сбежавших восстановлю бесплатно, "
            "каждому владельцу начислю <b>100 🍬</b> в каждом его чате. "
            "Если lost_streak уже утрачен, за основу возьму лучший рекорд "
            "(он может быть старше простоя). Не трогаю добровольно "
            "брошенных питомцев.\n\n"
            "Создам резервную копию SQLite перед изменениями. "
            "Операция доступна <b>один раз</b>.\n"
            "Подтвердить: <code>/pet_amnesty APPLY_2026_11</code>"
        )
        return

    try:
        backup_path = await backup_database(db)
    except Exception:
        await message.reply(
            "❌ Не удалось сделать резервную копию базы. "
            "Амнистию НЕ запускал. Проверь свободное место и права на каталог БД."
        )
        return

    try:
        result = await apply(db, config, message.from_user.id)
    except Exception:
        await message.reply(
            "❌ Во время операции произошла ошибка, транзакция отменена. "
            f"Резервная копия: <code>{escape(backup_path)}</code>"
        )
        return
    if result["status"] != "ok":
        await message.reply(
            "Амнистия не выполнена (уже применена или нет питомцев). "
            f"Копия: <code>{escape(backup_path)}</code>"
        )
        return
    await message.reply(
        "✅ <b>Амнистия проведена!</b>\n"
        f"🐾 Возвращено сбежавших: <b>{result['restored']}</b>\n"
        f"🛡 Сохранено серий живых питомцев: <b>{result['protected']}</b>\n"
        f"🍬 Получили по 100 ирисок: <b>{result['owners']}</b> "
        f"(учёт отдельно по каждому чату)\n"
        f"💰 Начислено всего: <b>{result['total_bonus']}</b> 🍬\n"
        f"💾 Копия БД: <code>{escape(backup_path)}</code>\n"
        "Двойное начисление заблокировано. "
        "Чтобы серия не сбежала снова, сегодня оба хозяина "
        "должны написать друг другу."
    )
