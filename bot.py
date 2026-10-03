"""Ириска-бот 🍬 — статистика общения и валюта чата.

Точка входа: python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
from dotenv import load_dotenv

from config import Config, load_config
from db import Database
from handlers import (
    admin_router,
    counting_router,
    games_router,
    quiz_router,
    shop_router,
    user_router,
)
from handlers.common import (
    bonus_block_line,
    current_window,
    is_bonus_hour,
    mention,
)
from handlers.quiz import load_active_chats, resume_queues
from monthly import monthly_stats_announcer
from subscription import (
    NOT_SUBSCRIBED,
    check_subscription,
    setup_channel,
)
from texts import fmt, iriski

logger = logging.getLogger("iriska-bot")


async def bonus_hours_announcer(bot: Bot, db: Database, config: Config) -> None:
    """Объявляет в чатах о начале бонусного часа.

    Следит за переходом «обычное время -> бонусное окно». При старте бота
    анонс не шлётся, даже если окно уже идёт — чтобы редеплой не спамил.
    """
    if not config.bonus_hours or not config.announce_bonus_hours:
        return
    mult = max(config.bonus_hours_mult, 1)
    in_window = is_bonus_hour(datetime.now(config.tz).hour, config.bonus_hours)
    while True:
        await asyncio.sleep(20)
        try:
            hour = datetime.now(config.tz).hour
            now_in = is_bonus_hour(hour, config.bonus_hours)
            if now_in and not in_window:
                window = current_window(hour, config.bonus_hours)
                end = window[1] if window else hour + 1
                text = (
                    f"🔥 <b>Бонусный час!</b> До {end:02d}:00 каждое сообщение "
                    f"даёт <b>х{mult}</b> к ирискам — налетай! 🍬\n"
                    "Свой прогресс: /me • Расписание: /hours"
                )
                for chat_id in await db.known_chats():
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception as e:  # выгнали из чата и т.п. — не падаем
                        logger.warning("Анонс в чат %s не ушёл: %s", chat_id, e)
            in_window = now_in
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка анонсера бонусных часов")


def bonus_revoked_text(
    user_id: int, first_name: str | None, username: str | None,
    revoked: int, balance: int, blocked_until: float, config: Config,
) -> str:
    """Сообщение в чат, когда бонус отозвали за отписку от канала."""
    who = mention(user_id, first_name, username)
    lines = [
        "🍬 <b>Бонус сгорел…</b>",
        f"{who} отписался от канала, а бонус выдаётся только подписчикам — "
        "отзываю его.",
    ]
    if revoked:
        lines.append(
            f"Списано: <b>{fmt(revoked)}</b> {iriski(revoked)}, "
            f"на балансе: <b>{fmt(balance)}</b>."
        )
    else:
        lines.append(
            f"Ирисок на балансе не было — списывать нечего "
            f"(баланс: <b>{fmt(balance)}</b>)."
        )
    lines.append("Стрик обнулён — серия бонусов начнётся заново.")
    if blocked_until > 0:
        lines.append(
            f"{bonus_block_line(blocked_until, config)} — лимит отписок исчерпан."
        )
    return "\n".join(lines)


async def process_bonus_watch(
    bot: Bot, db: Database, config: Config, *, now_ts: float | None = None,
) -> int:
    """Один проход сторожа подписки. Возвращает, сколько бонусов отозвано.

    Наблюдение идёт BONUS_WATCH_HOURS после выдачи бонуса: если человек к этому
    моменту отписался — бонус отзывается. Сбой проверки (сеть, лимиты, бот не
    админ канала) нарушением не считается: ничего не отзываем, попробуем
    в следующий проход. Выключено, если нет CHANNEL_ID или BONUS_WATCH_HOURS=0.
    """
    watch = config.bonus_watch_seconds
    if config.channel_id is None or watch <= 0:
        return 0
    now = time.time() if now_ts is None else now_ts
    # Сначала закрываем окна, которые уже истекли: у этих людей бонус остаётся.
    await db.finish_expired_bonus_watch(now, watch)

    revoked = 0
    for row in await db.active_bonus_watches(now, watch):
        user_id, chat_id = int(row["user_id"]), int(row["chat_id"])
        result = await check_subscription(bot, config.channel_id, user_id)
        if result != NOT_SUBSCRIBED:
            continue  # подписан или проверка не удалась
        status, gone, balance, blocked_until = await db.revoke_bonus(
            chat_id, user_id, now_ts=now,
            abuse_limit=config.bonus_abuse_limit,
            block_seconds=config.bonus_block_seconds,
        )
        if status != "revoked":
            continue
        revoked += 1
        logger.info(
            "Бонус отозван за отписку: user=%s chat=%s списано=%s%s",
            user_id, chat_id, gone,
            f", закрыт до {blocked_until:.0f}" if blocked_until else "",
        )
        if not config.bonus_revoke_notice:
            continue
        try:
            await bot.send_message(
                chat_id,
                bonus_revoked_text(
                    user_id, row["first_name"], row["username"],
                    gone, balance, blocked_until, config,
                ),
            )
        except Exception as e:  # выгнали из чата и т.п. — не падаем
            logger.warning("Не смог сообщить об отзыве бонуса в чат %s: %s", chat_id, e)
    return revoked


async def bonus_watchdog(bot: Bot, db: Database, config: Config) -> None:
    """Сторож подписки: после бонуса следит, что человек не отписался.

    Первый проход — сразу при старте (перезапуск бота не должен откладывать
    разбор отписок), дальше — раз в BONUS_WATCH_INTERVAL секунд.
    """
    if config.channel_id is None:
        return
    if config.bonus_watch_seconds <= 0:
        logger.warning(
            "BONUS_WATCH_HOURS=0 — сторож подписки выключен: отписавшиеся "
            "после бонуса наказания не получат."
        )
        return
    interval = max(config.bonus_watch_interval, 1.0)
    logger.info(
        "Сторож подписки запущен: окно %g ч, проверка раз в %g сек",
        config.bonus_watch_hours, interval,
    )
    while True:
        try:
            await process_bonus_watch(bot, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка сторожа подписки")
        await asyncio.sleep(interval)


async def set_commands(bot: Bot, config: Config) -> None:
    group_cmds = [
        BotCommand(command="me", description="Моя статистика и ириски"),
        BotCommand(command="balance", description="Баланс ирисок"),
        BotCommand(command="bonus", description="Ежедневный бонус 🎁"),
        BotCommand(command="top", description="Топ чата за текущий месяц"),
        BotCommand(command="week", description="Топ за 7 дней"),
        BotCommand(command="day", description="Топ за сегодня"),
        BotCommand(command="casino", description="Слоты: /casino 10 🎰"),
        BotCommand(command="games", description="Правила игр"),
        BotCommand(command="shop", description="Магазин за ириски 🛍"),
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

    # Личное меню админов: команды викторины
    admin_private = private_cmds + [
        BotCommand(command="quiz", description="Викторина: /quiz Вопрос | ответ"),
        BotCommand(command="quizskip", description="Пропустить вопрос викторины"),
        BotCommand(command="quizstop", description="Остановить викторину"),
    ]
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                admin_private, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logger.warning("Не смог задать меню для админа %s: %s", admin_id, e)


def allowed_updates(dp: Dispatcher) -> list[str]:
    """Типы апдейтов, которые бот запрашивает у Telegram.

    Telegram отдаёт только перечисленные типы, поэтому забытый в списке тип —
    это молча неработающая фича. Так вышло с кнопками магазина: жёсткий
    список ["message"] не пускал callback_query, и нажатия не доходили до
    бота, хотя хендлеры были на месте. Чтобы это не повторилось, спрашиваем
    типы у роутеров — новая фича с новым типом апдейта подключится сама.
    """
    return dp.resolve_used_update_types()


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
    # Бонус только для подписчиков: проверяем настройку канала (бот — админ?)
    # и, если CHANNEL_URL не задан, определяем ссылку на канал сами.
    config = await setup_channel(bot, config)
    dp = Dispatcher(db=db, config=config)
    # Порядок важен: сначала команды, подсчёт — последним,
    # чтобы команды и триггеры не попадали в статистику.
    dp.include_router(admin_router)
    dp.include_router(quiz_router)
    dp.include_router(user_router)
    dp.include_router(shop_router)
    dp.include_router(games_router)
    dp.include_router(counting_router)

    announcer = asyncio.create_task(bonus_hours_announcer(bot, db, config))
    monthly_announcer = asyncio.create_task(
        monthly_stats_announcer(bot, db, config)
    )
    watchdog = asyncio.create_task(bonus_watchdog(bot, db, config))
    try:
        await set_commands(bot, config)
        await load_active_chats(db)   # викторины, пережившие рестарт
        await resume_queues(bot, db)  # продолжаем очередь вопросов, если была
        me = await bot.get_me()
        logger.info("Запущен как @%s", me.username)
        if not config.admin_ids:
            logger.warning(
                "ADMIN_IDS не задан — админ определяется только по username (%s). "
                "Надёжнее прописать ID: напиши боту /id в личку.",
                ", ".join(sorted(config.admin_usernames)),
            )
        updates = allowed_updates(dp)
        logger.info("Слушаю апдейты: %s", ", ".join(updates))
        await dp.start_polling(bot, allowed_updates=updates)
    finally:
        announcer.cancel()
        monthly_announcer.cancel()
        watchdog.cancel()
        await asyncio.gather(
            announcer, monthly_announcer, watchdog, return_exceptions=True
        )
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
