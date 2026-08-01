"""Ириска-бот 🍬 — статистика общения и валюта чата.

Точка входа: python bot.py
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from dotenv import load_dotenv

from config import load_config
from db import Database
from handlers import admin_router, counting_router, games_router, user_router

logger = logging.getLogger("iriska-bot")


async def set_commands(bot: Bot) -> None:
    group_cmds = [
        BotCommand(command="me", description="Моя статистика и ириски"),
        BotCommand(command="balance", description="Баланс ирисок"),
        BotCommand(command="bonus", description="Ежедневный бонус 🎁"),
        BotCommand(command="top", description="Топ чата за всё время"),
        BotCommand(command="week", description="Топ за 7 дней"),
        BotCommand(command="day", description="Топ за сегодня"),
        BotCommand(command="casino", description="Слоты: /casino 10 🎰"),
        BotCommand(command="games", description="Правила игр"),
        BotCommand(command="hours", description="Бонусные часы ⏰"),
        BotCommand(command="withdraw", description="Вывести ириски"),
        BotCommand(command="help", description="Как это работает"),
    ]
    private_cmds = [
        BotCommand(command="help", description="Как это работает"),
        BotCommand(command="id", description="Узнать свой Telegram ID"),
    ]
    await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())


async def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(db=db, config=config)
    # Порядок важен: сначала команды, подсчёт — последним,
    # чтобы команды и триггеры не попадали в статистику.
    dp.include_router(admin_router)
    dp.include_router(user_router)
    dp.include_router(games_router)
    dp.include_router(counting_router)

    try:
        await set_commands(bot)
        me = await bot.get_me()
        logger.info("Запущен как @%s", me.username)
        if not config.admin_ids:
            logger.warning(
                "ADMIN_IDS не задан — админ определяется только по username (%s). "
                "Надёжнее прописать ID: напиши боту /id в личку.",
                ", ".join(sorted(config.admin_usernames)),
            )
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
