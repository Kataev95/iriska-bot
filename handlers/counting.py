"""Подсчёт сообщений и начисление ирисок.

Этот роутер подключается ПОСЛЕДНИМ: до него сообщение проходит через
роутеры команд и триггеров, поэтому команды и запросы вроде «топ»
в статистику не попадают.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.types import Message

from config import Config
from db import Database
from handlers.common import GROUP_TYPES, is_bonus_hour

logger = logging.getLogger(__name__)

router = Router(name="counting")


def norm_hash(text: str) -> str:
    """Хеш нормализованного текста — для отсечения повторов подряд."""
    normalized = " ".join(text.lower().split())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


@router.message(F.chat.type.in_(GROUP_TYPES), F.text)
async def on_group_text(message: Message, db: Database, config: Config) -> None:
    user = message.from_user
    if user is None or user.is_bot:
        return
    if message.via_bot is not None:  # сообщения через inline-ботов
        return
    if message.forward_origin is not None:  # пересланные не считаем
        return

    text = (message.text or "").strip()
    if text.startswith("/"):  # команды не считаем
        return
    if len(text) < config.min_msg_len:
        return

    now_local = datetime.now(config.tz)
    weight = 1
    if config.bonus_hours and is_bonus_hour(now_local.hour, config.bonus_hours):
        weight = max(config.bonus_hours_mult, 1)
    counted, accrued = await db.try_count_message(
        chat_id=message.chat.id,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        day=now_local.strftime("%Y-%m-%d"),
        msg_hash=norm_hash(text),
        now_ts=time.time(),
        cooldown=config.cooldown_seconds,
        per_iriska=config.messages_per_iriska,
        weight=weight,
    )
    if accrued:
        logger.info(
            "+%d ириска(и): user=%s (%s) chat=%s",
            accrued, user.id, user.username or user.first_name, message.chat.id,
        )
