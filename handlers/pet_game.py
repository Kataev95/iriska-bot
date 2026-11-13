"""Compact mobile-first Adventures, Daily Tasks and Development screens.

Profession is a character-development feature; do not bury it inside Adventures.
Legacy pg:* callbacks remain supported on older messages.
"""

from __future__ import annotations

import time
from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Config
from db import Database
from pet_game import (
    PROFESSIONS, ROUTES, TASKS, growth_days, level_for, next_level_xp,
    xp_for_level,
)
from pets import pet_title, stage_line
from rich_ui import answer_rich, edit_rich
from texts import days

router = Router(name="pet_game")

PROFESSION_ACTIONS = frozenset({"professions", "confirm", "choose"})
TASK_ACTIONS = frozenset({"tasks", "daily"})
TRIP_ACTIONS = frozenset({"menu", "start", "claim"})


def button(text: str, action: str, pid: int, arg: str = "") -> InlineKeyboardButton:
    data = f"pg:{action}:{pid}" + (f":{arg}" if arg else "")
    return InlineKeyboardButton(text=text, callback_data=data)


def experience_line(pet) -> str:
    xp = int(pet["xp"])
    level = level_for(xp)
    target = next_level_xp(xp)
    if target is None:
        return f"⭐ Уровень {level}/20 · {xp} XP · 🏆 Максимум"
    return f"⭐ Уровень {level}/20 · {xp} XP · до следующего {target - xp} XP"


def xp_bar(pet, width: int = 10) -> str:
    xp = int(pet["xp"])
    level = level_for(xp)
    target = next_level_xp(xp)
    if target is None:
        return "▰" * width
    base = xp_for_level(level)
    filled = min(width, max(0, int(width * (xp - base) / max(1, target - base))))
    return "▰" * filled + "▱" * (width - filled)


def game_hud_html(
    pet, action: str, note: str, now: float, trip=None,
    day: str = "", completed=None, claimed=None, arg: str = "",
) -> str:
    """Short rich HTML pages suitable for a narrow Telegram chat bubble."""
    title = (
        "📋 Задания" if action in TASK_ACTIONS else
        "⭐ Развитие" if action == "progress" else
        "🎓 Профессия" if action in PROFESSION_ACTIONS else
        "🧭 Приключения"
    )
    doc = [
        f"<h2>{title} · {pet_title(pet)}</h2>",
        f"<p>{experience_line(pet)}</p>",
    ]
    if note:
        doc.append(f"<blockquote>{escape(note)}</blockquote>")

    if action in TASK_ACTIONS:
        doc.append("<h3>📋 Сегодня</h3>")
        completed = completed or {}
        claimed = claimed or set()
        for code, (task_title, xp) in TASKS.items():
            state = "🎁" if code in claimed else "✅" if completed.get(code) else "▫️"
            doc.append(f"<p>{state} {escape(task_title)} · <b>+{xp} XP</b></p>")
        doc.append("<footer>🎁 — награда забрана. Новые задания появляются в полночь.</footer>")

    elif action == "progress":
        streak = int(pet["streak"])
        level = level_for(pet["xp"])
        profession = PROFESSIONS.get(pet["profession"])
        doc.extend([
            f"<p>{xp_bar(pet)}</p>",
            f"<p>{stage_line(growth_days(pet))} · 🔥 {streak} {days(streak)}</p>",
            "<h3>🎓 Профессия</h3>",
        ])
        if profession:
            doc.append(f"<p>✅ {escape(profession[0])} · {escape(profession[1])}</p>")
        elif level >= 5:
            doc.append("<p>🔓 Доступен выбор специализации.</p>")
        else:
            doc.append(f"<p>🔒 Откроется на 5 уровне. Ещё {5 - level} ур.</p>")
        doc.append("<footer>Уровень, стадия и профессия общие у двух хозяев.</footer>")

    elif action in PROFESSION_ACTIONS:
        level = level_for(pet["xp"])
        current = PROFESSIONS.get(pet["profession"])
        if current:
            doc.append("<h3>✅ Выбрана</h3>")
            doc.append(f"<p>{escape(current[0])} · {escape(current[1])}</p>")
        elif level < 5:
            doc.append("<p>🔒 Профессии открываются на 5 уровне.</p>")
        else:
            doc.append("<h3>Выбор специализации</h3>")
            for code, (job, effect) in PROFESSIONS.items():
                selected = "✅ " if action == "confirm" and arg == code else ""
                doc.append(f"<p>{selected}<b>{escape(job)}</b> · {escape(effect)}</p>")
            if action == "confirm" and arg in PROFESSIONS:
                doc.append(
                    "<blockquote>⚠️ Выбор общий для двух хозяев и пока не меняется. "
                    "Подтвердите кнопкой ниже.</blockquote>"
                )

    elif trip is not None:
        route = ROUTES[trip["route"]]
        remaining_seconds = max(0, int(trip["finish_at"] - now))
        remaining_mins = max(1, (remaining_seconds + 59) // 60) if remaining_seconds else 0
        doc.append(f"<h3>{route.title}</h3>")
        doc.append(
            "<p>🎁 Можно забрать награду!</p>"
            if remaining_seconds == 0 else
            f"<p>⏳ Осталось примерно <b>{remaining_mins} мин</b></p>"
        )
        doc.append(
            f"<p>⭐ {trip['xp']} XP · 🍬 {trip['coins']} ирисок</p>"
        )
        doc.append("<footer>Результат сохранится даже после перезапуска бота.</footer>")

    else:
        doc.append("<h3>Выберите маршрут</h3>")
        for code, route in ROUTES.items():
            energy = route.energy - (3 if pet["profession"] == "artisan" else 0)
            xp = route.xp * 120 // 100 if pet["profession"] == "explorer" else route.xp
            coins = route.coins + (1 if pet["profession"] == "merchant" else 0)
            minutes = route.seconds // 60
            duration = f"{minutes} мин" if minutes < 60 else f"{minutes // 60} ч"
            doc.append(
                f"<p><b>{route.title}</b> · ⏱ {duration}<br/>"
                f"⚡ {energy} · ⭐ {xp} XP · 🍬 {coins}</p>"
            )
        doc.append("<footer>3 старта в день на игрока. Сытость — от 20%.</footer>")
    return "".join(doc)


def game_keyboard(pet, action: str, now: float, trip=None, completed=None, claimed=None,
                  arg: str = "") -> InlineKeyboardMarkup:
    pid = int(pet["id"])
    level = level_for(pet["xp"])
    rows: list[list[InlineKeyboardButton]] = []

    if action in TASK_ACTIONS:
        if any(completed.get(code) and code not in (claimed or set())
               for code in TASKS):
            rows.append([button("🎁 Забрать опыт", "daily", pid)])
        rows.append([
            button("🧭 Походы", "menu", pid),
            button("⭐ Развитие", "progress", pid),
        ])

    elif action == "progress":
        rows.append([button("🎓 Профессия", "professions", pid)])
        rows.append([button("🧭 Походы", "menu", pid)])

    elif action in PROFESSION_ACTIONS:
        if not pet["profession"] and level >= 5:
            if action == "confirm" and arg in PROFESSIONS:
                rows.append([button("✅ Подтвердить", "choose", pid, arg)])
                rows.append([button("↩️ Другой вариант", "professions", pid)])
            else:
                for code, (title, _) in PROFESSIONS.items():
                    rows.append([button(title, "confirm", pid, code)])
        rows.append([button("⬅️ Развитие", "progress", pid)])

    else:
        if trip:
            if now >= trip["finish_at"]:
                rows.append([button("🎁 Забрать награду", "claim", pid, str(trip["id"]))])
            else:
                rows.append([button("🔄 Проверить поход", "menu", pid)])
        else:
            for code, route in ROUTES.items():
                rows.append([button(route.title, "start", pid, code)])
        rows.append([button("📋 Задания", "tasks", pid)])

    rows.append([
        InlineKeyboardButton(text="🐾 К питомцу", callback_data=f"pm:main:{pid}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("pg:"))
async def on_game(callback: CallbackQuery, db: Database, config: Config) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) not in (3, 4) or not parts[2].isdigit() or not config.pets_enabled:
        await callback.answer()
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    action, pid = parts[1], int(parts[2])
    arg = parts[3] if len(parts) == 4 else ""
    if action not in (TRIP_ACTIONS | TASK_ACTIONS | PROFESSION_ACTIONS | {"progress"}):
        await callback.answer("Неизвестный раздел", show_alert=True)
        return

    user_id = callback.from_user.id
    pet = await db.get_pet(pid)
    if pet is None or pet["status"] != "active" or user_id not in (
        pet["user_a"], pet["user_b"]
    ):
        await callback.answer("Это не твой питомец", show_alert=True)
        return
    if not pet["best_streak"]:
        await callback.answer(
            "Сначала напишите друг другу, чтобы питомец вылупился",
            show_alert=True,
        )
        return

    # Acknowledge early; rewards are finalized in SQLite, never in UI callbacks.
    await callback.answer()
    now = time.time()
    day = datetime.fromtimestamp(now, config.tz).date().isoformat()
    note = ""
    if action == "start":
        note = await db.start_trip(pid, user_id, arg, now, config.tz)
    elif action == "claim" and arg.isdigit():
        note = await db.claim_trip(pid, user_id, int(arg), now, config.tz)
    elif action == "daily":
        note = await db.claim_daily(pid, user_id, day)
    elif action == "choose":
        note = await db.choose_profession(pid, user_id, arg)

    pet = await db.get_pet(pid)
    render_action = "progress" if action == "choose" and note.startswith("✅") else action
    if render_action in TASK_ACTIONS:
        completed, claimed = await db.daily_state(pid, day)
    else:
        completed, claimed = {}, set()

    trip = await db.active_trip(pid) if render_action in TRIP_ACTIONS else None
    lines = [f"{pet_title(pet)} · Питомцы 3.0 · {experience_line(pet)}"]
    if note:
        lines.append(escape(note))
    if render_action in TASK_ACTIONS:
        lines.append("📋 Задания:")
        for code, (title, xp) in TASKS.items():
            state = "🎁" if code in claimed else "✅" if completed.get(code) else "▫️"
            lines.append(f"{state} {title} · {xp} XP")
    elif render_action in PROFESSION_ACTIONS:
        if pet["profession"]:
            lines.append(f"🎓 {PROFESSIONS[pet['profession']][0]}")
        elif level_for(pet["xp"]) < 5:
            lines.append("Профессия откроется на 5 уровне")
        else:
            lines.append("🎓 Выберите профессию:")
    elif render_action == "progress":
        lines.extend(["⭐ Развитие", f"Опыт: {xp_bar(pet)}"])
    elif trip:
        route = ROUTES[trip["route"]]
        lines.extend([f"🧭 {route.title}", f"Награда: {trip['xp']} XP и {trip['coins']} 🍬"])
    else:
        lines.extend([
            "🧭 Маршруты:",
            *[f"{route.title} · {route.seconds//60} мин · {route.xp} XP"
              for route in ROUTES.values()],
        ])
    fallback = "\n".join(lines)
    html = game_hud_html(pet, render_action, note, now, trip, day, completed, claimed, arg)
    markup = game_keyboard(pet, render_action, now, trip, completed, claimed, arg)
    if callback.message.photo:
        await answer_rich(callback.message, fallback, html=html, reply_markup=markup)
    else:
        if not await edit_rich(callback.message, fallback, html=html, reply_markup=markup):
            await answer_rich(callback.message, fallback, html=html, reply_markup=markup)
