"""Меню приключений, профессий и ежедневных заданий."""

import time
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from db import Database
from pet_game import PROFESSIONS, ROUTES, TASKS, level_for, next_level_xp
from pets import pet_title

router = Router(name="pet_game")


def button(text, action, pid, arg=""):
    return InlineKeyboardButton(
        text=text, callback_data=f"pg:{action}:{pid}" + (f":{arg}" if arg else "")
    )


@router.callback_query(F.data.startswith("pg:"))
async def on_game(callback: CallbackQuery, db: Database, config: Config):
    parts = (callback.data or "").split(":")
    if len(parts) not in (3, 4) or not parts[2].isdigit() or not config.pets_enabled:
        await callback.answer()
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    action, pid = parts[1], int(parts[2])
    arg = parts[3] if len(parts) == 4 else ""
    uid = callback.from_user.id
    pet = await db.get_pet(pid)
    if (
        pet is None
        or pet["status"] != "active"
        or uid not in (pet["user_a"], pet["user_b"])
    ):
        await callback.answer("Это не твой питомец", show_alert=True)
        return
    if not pet["best_streak"]:
        await callback.answer(
            "Сначала напишите друг другу, чтобы питомец вылупился", show_alert=True
        )
        return
    # Acknowledge promptly; durable state does not depend on editing Telegram.
    await callback.answer()
    now = time.time()
    day = datetime.fromtimestamp(now, config.tz).date().isoformat()
    note = ""
    if action == "start":
        note = await db.start_trip(pid, uid, arg, now, config.tz)
    elif action == "claim" and arg.isdigit():
        note = await db.claim_trip(pid, uid, int(arg), now, config.tz)
    elif action == "daily":
        note = await db.claim_daily(pid, uid, day)
    elif action == "choose":
        note = await db.choose_profession(pid, uid, arg)
    pet = await db.get_pet(pid)
    level = level_for(pet["xp"])
    target = next_level_xp(pet["xp"])
    lines = [
        f"🧭 <b>{pet_title(pet)} · Питомцы 3.0</b>",
        f"⭐ Уровень {level}/20 • {pet['xp']} XP",
    ]
    if target:
        lines.append(f"До следующего уровня: {target - pet['xp']} XP")
    if note:
        lines.extend(["", escape(note)])
    rows = []
    if action in ("tasks", "daily"):
        completed, claimed = await db.daily_state(pid, day)
        lines.extend(["", f"📋 Задания на {day} ({escape(config.tz.key)})"])
        for code, (title, xp) in TASKS.items():
            mark = (
                "🎁 Получено"
                if code in claimed
                else "✅ Выполнено"
                if completed[code]
                else "▫️ Не выполнено"
            )
            lines.append(f"{mark}: {title} — {xp} XP")
        lines.append(
            "Опыт общий. Заберите выполненное до полуночи; прошлые награды не переносятся."
        )
        rows.append([button("🎁 Забрать опыт", "daily", pid)])
    elif action in ("professions", "confirm", "choose"):
        if pet["profession"]:
            title, detail = PROFESSIONS[pet["profession"]]
            lines.extend(["", f"Профессия: {title}", detail])
        elif level < 5:
            lines.append("Профессия откроется на 5-м уровне.")
        elif action == "confirm" and arg in PROFESSIONS:
            title, detail = PROFESSIONS[arg]
            lines.extend(
                [
                    "",
                    f"Выбрать «{title}»? {detail}",
                    "Выбор общий для пары. Обсудите с партнёром: смена пока недоступна.",
                ]
            )
            rows.append([button("✅ Подтвердить выбор", "choose", pid, arg)])
        else:
            lines.extend(
                ["", "Профессия общая для пары. Пока выбрать можно только один раз."]
            )
            for code, (title, detail) in PROFESSIONS.items():
                lines.append(f"{title}: {detail}")
                rows.append([button(title, "confirm", pid, code)])
    else:
        trip = await db.active_trip(pid)
        if trip:
            lines.extend(["", f"Маршрут: {ROUTES[trip['route']].title}"])
            remaining = trip["finish_at"] - now
            lines.append(
                f"Осталось примерно {max(1, int(remaining / 60))} мин."
                if remaining > 0
                else "✅ Поход завершён. Награда ждёт вас!"
            )
            lines.append(
                f"Награда: {trip['xp']} XP питомцу и {trip['coins']} 🍬 отправителю."
            )
            rows.append([button("🎁 Забрать награду", "claim", pid, str(trip["id"]))])
        else:
            lines.extend(
                [
                    "",
                    "Выбери маршрут. Требуется от 20 сытости; каждый поход расходует ещё 5 сытости.",
                ]
            )
            for code, r in ROUTES.items():
                energy = r.energy - (3 if pet["profession"] == "artisan" else 0)
                xp = r.xp * 120 // 100 if pet["profession"] == "explorer" else r.xp
                coins = r.coins + (1 if pet["profession"] == "merchant" else 0)
                lines.append(
                    f"{r.title}: {r.seconds // 60} мин. • {energy} ⚡ • {xp} XP • {coins} 🍬"
                )
                rows.append([button(r.title, "start", pid, code)])
            lines.append(
                "До 3 стартов в день на человека во всех чатах. Один поход на питомца; второй хозяин тоже может забрать результат."
            )
    rows.extend(
        [
            [button("🧭 Походы", "menu", pid), button("📋 Задания", "tasks", pid)],
            [button("🎓 Профессия", "professions", pid)],
            [InlineKeyboardButton(text="🐾 К питомцу", callback_data=f"pm:main:{pid}")],
        ]
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = "\n".join(lines)
    try:
        if callback.message.photo:
            await callback.message.answer(text, reply_markup=markup)
        else:
            await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise
