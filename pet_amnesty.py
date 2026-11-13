"""Разовая амнистия питомцев после простоя бота в ноябре 2026 года.

Ничего не происходит само при деплое: запуск только админ-командой
/pet_amnesty APPLY_2026_11. Итог записывается в bot_meta, повторная
выдача запрещена даже после рестарта.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta

from config import Config
from db import Database

AMNESTY_KEY = "pet_amnesty_outage_2026_11"
COMPENSATION = 100
REASON = "компенсация за простой бота (питомцы, ноябрь 2026)"

# Уникальные (чат, человек). Если у человека несколько питомцев в одном чате,
# начисляем только 100 ирисок; балансы разных чатов всегда независимы.
OWNERS_SQL = """
SELECT chat_id, user_a AS user_id FROM pets WHERE status = 'active'
UNION
SELECT chat_id, user_b AS user_id FROM pets WHERE status = 'active'
"""


async def preview(db: Database) -> dict:
    """Без изменений в БД: показать, что затронет разовая амнистия."""
    conn = db._require()
    async with db._lock:
        cur = await conn.execute(
            "SELECT value FROM bot_meta WHERE key = ?", (AMNESTY_KEY,)
        )
        marker = await cur.fetchone()
        cur = await conn.execute(
            """SELECT COUNT(*) AS pets,
                      COALESCE(SUM(CASE WHEN streak = 0 AND best_streak > 0
                                        THEN 1 ELSE 0 END), 0) AS away,
                      COALESCE(SUM(CASE WHEN streak = 0 AND best_streak > 0
                                             AND lost_streak = 0
                                        THEN 1 ELSE 0 END), 0) AS fallback
               FROM pets WHERE status = 'active'"""
        )
        row = await cur.fetchone()
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM (" + OWNERS_SQL + ")"
        )
        owners = int((await cur.fetchone())["n"])
        return {
            "applied": marker is not None,
            "marker": marker["value"] if marker else None,
            "pets": int(row["pets"]),
            "away": int(row["away"]),
            "fallback": int(row["fallback"]),
            "owners": owners,
            "total_bonus": owners * COMPENSATION,
        }


async def backup_database(db: Database) -> str:
    """SQLite online backup (не копирование файла с активным WAL)."""
    if db.path == ":memory:":
        raise RuntimeError("Нельзя делать резервную копию in-memory SQLite")
    destination = f"{db.path}.before_pet_amnesty_{int(time.time())}.db"
    if os.path.exists(destination):
        raise FileExistsError(f"Копия уже существует: {destination}")
    target = sqlite3.connect(destination)
    try:
        # Встроенный sqlite backup API корректно учитывает WAL.
        await db._require().backup(target)
    except BaseException:
        target.close()
        try:
            os.remove(destination)
        except OSError:
            pass
        raise
    else:
        target.close()
    return destination


async def apply(db: Database, config: Config, admin_id: int) -> dict:
    """Транзакционно возвращает сбежавших и начисляет по 100 раз за всё время.

    Активно заведённые питомцы (status=active) не удаляются при побеге:
    streak становится 0, а прежняя серия — lost_streak. Если пользователь
    уже потерял lost_streak, используем best_streak как приближение.
    Явно брошенных (abandoned) и отклонённые приглашения не трогаем.
    """
    conn = db._require()
    now = datetime.now(config.tz)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    async with db._lock:
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute(
                "SELECT value FROM bot_meta WHERE key = ?", (AMNESTY_KEY,)
            )
            if await cur.fetchone() is not None:
                await conn.rollback()
                return {"status": "already"}

            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM pets WHERE status = 'active'"
            )
            pets = int((await cur.fetchone())["n"])
            if pets == 0:
                await conn.rollback()
                return {"status": "empty"}

            cur = await conn.execute(
                """SELECT COUNT(*) AS n FROM pets
                   WHERE status = 'active' AND streak = 0
                     AND best_streak > 0 AND lost_streak = 0"""
            )
            fallback = int((await cur.fetchone())["n"])

            # Восстанавливаем старую серию, шкалы ухода и даём день на общение.
            # Если уже накормили сегодня, сегодняшний день сохраняем.
            cur = await conn.execute(
                """UPDATE pets
                   SET streak = CASE WHEN lost_streak > 0
                                     THEN lost_streak ELSE best_streak END,
                       lost_streak = 0, lost_ts = 0,
                       last_fed_day = CASE WHEN last_fed_day = ?
                                           THEN last_fed_day ELSE ? END,
                       last_remind_day = NULL,
                       food = MAX(food, 80),
                       joy = MAX(joy, 80),
                       energy = MAX(energy, 80),
                       care_ts = ?
                   WHERE status = 'active' AND streak = 0 AND best_streak > 0""",
                (today, yesterday, now.timestamp()),
            )
            restored = int(cur.rowcount)

            # Не даём смотрителю немедленно сбросить пока живые серии после простоя.
            cur = await conn.execute(
                """UPDATE pets SET last_fed_day = ?, last_remind_day = NULL
                   WHERE status = 'active' AND streak > 0
                     AND last_fed_day IS NOT NULL AND last_fed_day < ?""",
                (yesterday, yesterday),
            )
            protected = int(cur.rowcount)

            cur = await conn.execute(
                "SELECT chat_id, user_id FROM (" + OWNERS_SQL + ") "
                "ORDER BY chat_id, user_id"
            )
            owners = list(await cur.fetchall())
            for owner in owners:
                chat_id = int(owner["chat_id"])
                user_id = int(owner["user_id"])
                await conn.execute(
                    "INSERT OR IGNORE INTO users (chat_id, user_id) VALUES (?, ?)",
                    (chat_id, user_id),
                )
                await conn.execute(
                    """UPDATE users
                       SET balance = balance + ?,
                           earned_total = earned_total + ?
                       WHERE chat_id = ? AND user_id = ?""",
                    (COMPENSATION, COMPENSATION, chat_id, user_id),
                )
                await conn.execute(
                    """INSERT INTO ledger
                       (chat_id, user_id, amount, reason, admin_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (chat_id, user_id, COMPENSATION, REASON, admin_id),
                )
            await conn.execute(
                "INSERT INTO bot_meta (key, value) VALUES (?, ?)",
                (AMNESTY_KEY, f"{now.isoformat()} admin={admin_id}"),
            )
            await conn.commit()
            return {
                "status": "ok",
                "restored": restored,
                "protected": protected,
                "fallback": fallback,
                "pets": pets,
                "owners": len(owners),
                "total_bonus": len(owners) * COMPENSATION,
            }
        except BaseException:
            await conn.rollback()
            raise
