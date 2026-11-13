"""Telegram Bot API 10.1+ Rich Messages and fallback for aiogram 3.x."""

from __future__ import annotations

import logging
import os
import re
from html import escape
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message, ReplyParameters

logger = logging.getLogger(__name__)


class _SendRich(TelegramMethod[Message]):
    __returning__ = Message
    __returning_type__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int | str
    rich_message: dict[str, Any]
    reply_markup: InlineKeyboardMarkup | None = None
    reply_parameters: ReplyParameters | None = None
    disable_notification: bool | None = None


class _EditRich(TelegramMethod[Message]):
    __returning__ = Message
    __returning_type__ = Message
    __api_method__ = "editMessageText"

    chat_id: int | str
    message_id: int
    rich_message: dict[str, Any]
    reply_markup: InlineKeyboardMarkup | None = None


def panel_html(text: str) -> str:
    """Enhance existing regular HTML as headings and paragraphs."""
    lines = text.strip().splitlines()
    if not lines:
        return "<p>Нет данных</p>"
    content = [f"<h2>{lines[0]}</h2>"]
    separator = False
    for line in lines[1:]:
        if not line.strip():
            separator = True
            continue
        if separator:
            content.append("<hr/>")
            separator = False
        content.append(f"<p>{line}</p>")
    return "".join(content)


def rich_table(headers: list[str], rows: list[list[str]], *, caption: str = "") -> str:
    """Escape user-controlled data in table cells."""
    head = "".join(f"<th>{escape(str(v))}</th>" for v in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>"
        for row in rows
    )
    name = f"<caption>{escape(caption)}</caption>" if caption else ""
    return f"<table bordered striped compact>{name}<tr>{head}</tr>{body}</table>"


def asset_url(key: str) -> str:
    if not re.fullmatch(r"[a-z_]+", key):
        raise ValueError("Unsafe pet asset key")
    return ("https://raw.githubusercontent.com/Kataev95/iriska-bot/main/"
            f"assets/pets/{key}.jpg")


def _payload(html: str, image_url: str | None = None) -> dict[str, str]:
    if image_url:
        return {"html": f'<figure><img src="{escape(image_url, quote=True)}"/></figure>' + html}
    return {"html": html}


async def _plain_send(bot, chat_id, text, *, reply_markup=None,
                      reply_parameters=None, disable_notification=None, **kwargs):
    """Do not pass absent optional arguments (works with minimal test doubles)."""
    options = dict(kwargs)
    if reply_markup is not None:
        options["reply_markup"] = reply_markup
    if reply_parameters is not None:
        options["reply_parameters"] = reply_parameters
    if disable_notification is not None:
        options["disable_notification"] = disable_notification
    return await bot.send_message(chat_id, text, **options)


async def send_rich(
    bot, chat_id: int | str, text: str, *, html: str | None = None,
    image_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_parameters: ReplyParameters | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool | None = None,
    fallback: bool = True,
    **kwargs,
) -> Message:
    if reply_to_message_id is not None and reply_parameters is None:
        reply_parameters = ReplyParameters(
            message_id=reply_to_message_id, allow_sending_without_reply=True,
        )
    if os.environ.get('RICH_MESSAGES_ENABLED', '1') == '0':
        return await _plain_send(
            bot, chat_id, text, reply_markup=reply_markup,
            reply_parameters=reply_parameters,
            disable_notification=disable_notification, **kwargs,
        )
    doc = html or panel_html(text)
    try:
        return await bot(_SendRich(
            chat_id=chat_id, rich_message=_payload(doc, image_url),
            reply_markup=reply_markup, reply_parameters=reply_parameters,
            disable_notification=disable_notification,
        ))
    except TelegramBadRequest as exc:
        if image_url:
            try:
                return await bot(_SendRich(
                    chat_id=chat_id, rich_message=_payload(doc),
                    reply_markup=reply_markup, reply_parameters=reply_parameters,
                    disable_notification=disable_notification,
                ))
            except TelegramBadRequest as second:
                exc = second
        if not fallback:
            raise exc
        logger.warning("Rich send unavailable: %s", exc)
        return await _plain_send(
            bot, chat_id, text, reply_markup=reply_markup,
            reply_parameters=reply_parameters,
            disable_notification=disable_notification, **kwargs,
        )


async def reply_rich(message: Message, text: str, **kwargs) -> Message:
    reply_parameters = kwargs.pop(
        "reply_parameters",
        ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True),
    )
    return await send_rich(
        message.bot, message.chat.id, text, reply_parameters=reply_parameters, **kwargs,
    )


async def answer_rich(message: Message, text: str, **kwargs) -> Message:
    return await send_rich(message.bot, message.chat.id, text, **kwargs)


async def edit_rich_by_id(
    bot, chat_id: int | str, message_id: int, text: str, *,
    html: str | None = None,
    image_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    if os.environ.get('RICH_MESSAGES_ENABLED', '1') == '0':
        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id,
            reply_markup=reply_markup,
        )
        return True
    doc = html or panel_html(text)
    try:
        await bot(_EditRich(
            chat_id=chat_id, message_id=message_id,
            rich_message=_payload(doc, image_url), reply_markup=reply_markup,
        ))
        return True
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return True
        if image_url:
            try:
                await bot(_EditRich(
                    chat_id=chat_id, message_id=message_id,
                    rich_message=_payload(doc), reply_markup=reply_markup,
                ))
                return True
            except TelegramBadRequest as second:
                if "not modified" in str(second):
                    return True
                raise second from exc
        raise


async def edit_rich(
    message: Message, text: str, *,
    html: str | None = None,
    image_url: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Prefer editing one HUD. Old photo cards still support caption edits."""
    if not message.photo:
        try:
            return await edit_rich_by_id(
                message.bot, message.chat.id, message.message_id, text,
                html=html, image_url=image_url, reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            logger.warning("Rich edit unavailable: %s", exc)
    try:
        if message.photo:
            await message.edit_caption(caption=text[:1024], reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            return True
        logger.warning("Unable to edit HUD: %s", exc)
        return False
