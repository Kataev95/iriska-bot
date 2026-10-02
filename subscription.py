"""Проверка подписки на канал: бонус выдаётся только подписчикам.

Проверка идёт по Telegram ID пользователя через getChatMember. Для этого бот
должен быть администратором канала — только тогда Telegram гарантирует
корректный ответ про других участников.

Если CHANNEL_ID не задан, проверка выключена и бонус выдаётся всем, как раньше.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from config import Config

logger = logging.getLogger(__name__)

# Результаты проверки (строками — как статусы в db.py)
SUBSCRIBED = "subscribed"
NOT_SUBSCRIBED = "not_subscribed"
CHECK_FAILED = "check_failed"

_MEMBER_STATUSES = {"creator", "administrator", "member"}
_ADMIN_STATUSES = {"creator", "administrator"}
# Так Telegram отвечает, если не знает такого участника в канале
_NOT_IN_CHANNEL_MARKERS = ("user not found", "participant_id_invalid")

CHECK_FAILED_TEXT = (
    "⚠️ Не получилось проверить подписку на канал. "
    "Попробуй ещё раз через минуту."
)


def _status(member) -> str:
    """Статус участника строкой (в aiogram это str-enum)."""
    return str(getattr(member.status, "value", member.status))


def is_member(member) -> bool:
    """Состоит ли пользователь в канале — по ответу getChatMember."""
    status = _status(member)
    if status in _MEMBER_STATUSES:
        return True
    if status == "restricted":  # ограничен, но может всё ещё быть участником
        return bool(getattr(member, "is_member", False))
    return False  # left / kicked


async def check_subscription(bot: Bot, channel_id: int | str, user_id: int) -> str:
    """Проверяет по ID пользователя, подписан ли он на канал.

    Возвращает SUBSCRIBED / NOT_SUBSCRIBED / CHECK_FAILED. Сбой проверки
    (неверный CHANNEL_ID, бот не админ канала, сеть, лимиты Telegram) — это
    НЕ «подписан»: при сбое бонус не выдаём, а причину пишем в лог.
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
    except TelegramBadRequest as e:
        reason = (e.message or "").lower()
        if any(marker in reason for marker in _NOT_IN_CHANNEL_MARKERS):
            logger.warning(
                "Telegram не нашёл user=%s в канале %s (%s) — считаю не подписанным",
                user_id, channel_id, e.message,
            )
            return NOT_SUBSCRIBED
        logger.error(
            "Не удалось проверить подписку user=%s на канал %s: %s",
            user_id, channel_id, e.message,
        )
        return CHECK_FAILED
    except TelegramAPIError as e:  # лимиты, сеть, бота выгнали из канала и т.п.
        logger.error(
            "Не удалось проверить подписку user=%s на канал %s: %s",
            user_id, channel_id, e,
        )
        return CHECK_FAILED
    except Exception:
        logger.exception(
            "Неожиданная ошибка проверки подписки user=%s на канал %s",
            user_id, channel_id,
        )
        return CHECK_FAILED
    return SUBSCRIBED if is_member(member) else NOT_SUBSCRIBED


def not_subscribed_reply(url: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и кнопка «подпишись на канал» для тех, кто ещё не подписан."""
    if url:
        link = f'<a href="{escape(url, quote=True)}">наш канал</a>'
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=url)]
            ]
        )
    else:
        link, markup = "наш канал", None
    text = (
        "🔒 <b>Бонус доступен только подписчикам канала!</b>\n\n"
        f"Подпишись на {link}, а затем снова напиши «бонус» — и ириски твои 🎁"
    )
    return text, markup


def subscribers_phrase(config: Config) -> str:
    """«для подписчиков канала» (со ссылкой, если известна); пусто, если проверка выключена."""
    if config.channel_id is None:
        return ""
    if config.channel_url:
        return (
            "для подписчиков "
            f'<a href="{escape(config.channel_url, quote=True)}">канала</a>'
        )
    return "для подписчиков канала"


async def ensure_subscribed(message: Message, bot: Bot, config: Config) -> bool:
    """Пропускает дальше только подписчиков канала.

    True — можно продолжать (подписан либо проверка выключена).
    False — пользователь уже получил ответ (просьба подписаться со ссылкой
    на канал или сообщение о сбое проверки), обработку нужно прекратить.
    """
    if config.channel_id is None:
        return True
    user = message.from_user
    if user is None:
        return False

    result = await check_subscription(bot, config.channel_id, user.id)
    if result == SUBSCRIBED:
        return True
    if result == NOT_SUBSCRIBED:
        text, markup = not_subscribed_reply(config.channel_url)
        await message.reply(
            text,
            reply_markup=markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    else:
        await message.reply(CHECK_FAILED_TEXT)
    return False


async def setup_channel(bot: Bot, config: Config) -> Config:
    """Самопроверка при старте: канал доступен, бот в нём админ, есть ссылка.

    Только пишет в лог и добирает ссылку на канал, если CHANNEL_URL не задан;
    ничего не роняет — остальной бот должен работать, даже если канал настроен
    неверно (тогда бонус просто никому не выдаётся, а в логе видна причина).
    """
    channel = config.channel_id
    if channel is None:
        logger.warning(
            "CHANNEL_ID не задан — бонус выдаётся БЕЗ проверки подписки на канал."
        )
        return config

    try:
        chat = await bot.get_chat(channel)
    except Exception as e:
        logger.error(
            "Не могу получить канал %s: %s. Проверь CHANNEL_ID и что бот добавлен "
            "в канал администратором — пока это не исправлено, бонус не выдаётся.",
            channel, e,
        )
        return config
    title = chat.title or chat.username or str(channel)

    try:
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(chat_id=channel, user_id=me.id)
        if _status(bot_member) not in _ADMIN_STATUSES:
            logger.error(
                "Бот не администратор канала «%s» — Telegram не гарантирует "
                "проверку подписчиков. Сделай бота админом канала.",
                title,
            )
    except Exception as e:
        logger.error(
            "Не смог проверить права бота в канале «%s»: %s", title, e
        )

    url = config.channel_url
    if not url:
        if chat.username:
            url = f"https://t.me/{chat.username}"
        elif chat.invite_link:
            url = chat.invite_link
    if not url:
        logger.warning(
            "Ссылку на канал «%s» определить не удалось — в подсказке не будет "
            "ссылки. Задай CHANNEL_URL (например, https://t.me/имя_канала).",
            title,
        )

    logger.info(
        "Бонус только для подписчиков канала «%s» (id=%s), ссылка: %s",
        title, chat.id, url or "—",
    )
    return replace(config, channel_url=url) if url != config.channel_url else config
