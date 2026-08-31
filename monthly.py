"""Завершение месячного цикла статистики и сгорание ирисок."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime

from aiogram import Bot

from config import Config
from db import Database
from texts import display_name, fmt, msgs, place

logger = logging.getLogger(__name__)

MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def next_month_key(year: int, month: int) -> str:
    if month == 12:
        return month_key(year + 1, 1)
    return month_key(year, month + 1)


def stats_period_for(now: datetime) -> str:
    """Период, куда попадёт новое сообщение в этот момент.

    В 23:59 последнего дня месяца уже начинается новый месячный цикл,
    как того требует механика бота.
    """
    last_day = calendar.monthrange(now.year, now.month)[1]
    if now.day == last_day and (now.hour, now.minute) >= (23, 59):
        return next_month_key(now.year, now.month)
    return month_key(now.year, now.month)


def period_names(period: str) -> tuple[str, str]:
    """Названия месяца в именительном и родительном падежах."""
    year, month = (int(part) for part in period.split("-", 1))
    nominative = f"{MONTHS_NOMINATIVE[month - 1].capitalize()} {year}"
    genitive = f"{MONTHS_GENITIVE[month - 1]} {year}"
    return nominative, genitive


def monthly_summary_text(period: str, snapshot: dict) -> str:
    nominative, genitive = period_names(period)
    top = snapshot["top"]
    totals = snapshot["totals"]
    lines = [
        f"🏁 <b>{nominative} подошёл к концу!</b>",
        "",
        f"🏆 <b>Топ-10 активных участников — итоги {genitive}:</b>",
    ]
    if top:
        for i, row in enumerate(top, 1):
            count = int(row["total_counted"])
            lines.append(
                f"{place(i)} {display_name(row['first_name'], row['username'])} — "
                f"<b>{fmt(count)}</b> {msgs(count)}"
            )
        lines.extend(
            [
                "",
                f"📊 За месяц: <b>{fmt(totals['msgs'])}</b> "
                f"{msgs(totals['msgs'])}; участников: <b>{fmt(totals['users'])}</b>.",
            ]
        )
    else:
        lines.append("В этом месяце засчитанных сообщений не было.")
    lines.extend(
        [
            "",
            "🔥 <b>Месячная статистика обнулена, все накопленные ириски сгорели.</b>",
            "🌱 Начинается новый месяц — приятного общения и новых побед! 🍬",
        ]
    )
    return "\n".join(lines)


async def process_month_boundary(
    bot: Bot, db: Database, config: Config, now: datetime | None = None,
) -> bool:
    """Закрывает месяц, если наступила его граница. Операция идемпотентна."""
    current = now or datetime.now(config.tz)
    calendar_period = month_key(current.year, current.month)
    stored_period = await db.ensure_stats_period(calendar_period)
    desired_period = stats_period_for(current)
    if stored_period == desired_period:
        return False
    if stored_period > desired_period:
        logger.warning(
            "Период статистики %s опережает локальную дату %s",
            stored_period, desired_period,
        )
        return False

    snapshots = await db.close_stats_period(
        stored_period, desired_period, current.timestamp()
    )
    if snapshots is None:
        return False
    for snapshot in snapshots:
        chat_id = int(snapshot["chat_id"])
        try:
            await bot.send_message(
                chat_id, monthly_summary_text(stored_period, snapshot)
            )
        except Exception as exc:
            logger.warning(
                "Не смог отправить месячные итоги в чат %s: %s", chat_id, exc
            )
    logger.info(
        "Месячный период %s закрыт, новый период: %s",
        stored_period, desired_period,
    )
    return True


async def monthly_stats_announcer(bot: Bot, db: Database, config: Config) -> None:
    """Проверяет границу месяца и запускает сброс ровно один раз."""
    while True:
        try:
            await process_month_boundary(bot, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка завершения месячной статистики")
        await asyncio.sleep(15)
