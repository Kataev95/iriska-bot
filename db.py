"""Слой работы с базой SQLite (aiosqlite)."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id         INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    username        TEXT,
    first_name      TEXT,
    total_counted   INTEGER NOT NULL DEFAULT 0,
    balance         INTEGER NOT NULL DEFAULT 0,
    earned_total    INTEGER NOT NULL DEFAULT 0,
    progress        INTEGER NOT NULL DEFAULT 0,
    last_counted_ts REAL    NOT NULL DEFAULT 0,
    last_msg_hash   TEXT,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT    NOT NULL,
    admin_id   INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS duels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id       INTEGER NOT NULL,
    challenger_id INTEGER NOT NULL,
    target_id     INTEGER NOT NULL,
    amount        INTEGER NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    winner_id     INTEGER,
    created_ts    REAL    NOT NULL,
    resolved_ts   REAL
);

CREATE TABLE IF NOT EXISTS quizzes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        INTEGER NOT NULL,
    question       TEXT    NOT NULL,
    answers        TEXT    NOT NULL,
    display_answer TEXT    NOT NULL DEFAULT '',
    prize          INTEGER NOT NULL DEFAULT 1,
    seq            INTEGER NOT NULL DEFAULT 1,
    total          INTEGER NOT NULL DEFAULT 1,
    status         TEXT    NOT NULL DEFAULT 'active',
    created_by     INTEGER,
    winner_id      INTEGER,
    created_ts     REAL    NOT NULL,
    resolved_ts    REAL
);

CREATE TABLE IF NOT EXISTS bot_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    item_code   TEXT    NOT NULL,
    item_title  TEXT    NOT NULL,
    price       INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'new',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    resolved_by INTEGER
);

-- 🐣 Общий питомец пары (как «огонёк» в TikTok). user_a < user_b.
-- status: pending (приглашение) -> active; declined / expired / abandoned.
-- a_day / b_day — последний день, когда A написал B и B написал A.
CREATE TABLE IF NOT EXISTS pets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    user_a          INTEGER NOT NULL,
    user_b          INTEGER NOT NULL,
    invited_by      INTEGER NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    species         TEXT    NOT NULL,
    name            TEXT,
    streak          INTEGER NOT NULL DEFAULT 0,
    best_streak     INTEGER NOT NULL DEFAULT 0,
    last_fed_day    TEXT,
    a_day           TEXT,
    b_day           TEXT,
    lost_streak     INTEGER NOT NULL DEFAULT 0,
    lost_ts         REAL    NOT NULL DEFAULT 0,
    last_remind_day TEXT,
    created_ts      REAL    NOT NULL,
    resolved_ts     REAL
);

-- 🐾 Питомцы 2.0: картинки (кэш file_id Telegram), кулдауны ухода, события.
CREATE TABLE IF NOT EXISTS pet_media (
    key     TEXT PRIMARY KEY,
    file_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pet_actions (
    pet_id  INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action  TEXT    NOT NULL,
    ts      REAL    NOT NULL,
    PRIMARY KEY (pet_id, user_id, action)
);

-- status: open -> done / expired. pressed — кто уже нажал (через запятую).
CREATE TABLE IF NOT EXISTS pet_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id      INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open',
    pressed     TEXT    NOT NULL DEFAULT '',
    message_id  INTEGER,
    created_ts  REAL    NOT NULL,
    expires_ts  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_chat_day ON daily_stats (chat_id, day);
CREATE INDEX IF NOT EXISTS idx_users_top ON users (chat_id, total_counted DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger (chat_id, user_id);
CREATE INDEX IF NOT EXISTS idx_duels_pending ON duels (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_quizzes_active ON quizzes (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_chat ON orders (chat_id, status, id);
CREATE INDEX IF NOT EXISTS idx_pets_chat ON pets (chat_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pets_pair ON pets (chat_id, user_a, user_b)
    WHERE status IN ('pending', 'active');
CREATE INDEX IF NOT EXISTS idx_pet_events_open ON pet_events (status, expires_ts);
"""


class Database:
    """Одно соединение + asyncio-lock на запись: для чата этого более чем достаточно."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Догоняющие миграции для баз, созданных старыми версиями бота."""
        db = self._require()
        cur = await db.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in await cur.fetchall()}
        if "last_bonus_day" not in cols:
            await db.execute("ALTER TABLE users ADD COLUMN last_bonus_day TEXT")
        if "bonus_streak" not in cols:
            await db.execute(
                "ALTER TABLE users ADD COLUMN bonus_streak INTEGER NOT NULL DEFAULT 0"
            )
        # Сторож подписки: когда и сколько выдали, сколько было отписок,
        # до какого момента бонус закрыт за нарушения.
        if "bonus_claimed_ts" not in cols:
            await db.execute(
                "ALTER TABLE users ADD COLUMN bonus_claimed_ts REAL NOT NULL DEFAULT 0"
            )
        if "bonus_claimed_amount" not in cols:
            await db.execute(
                "ALTER TABLE users ADD COLUMN bonus_claimed_amount "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "bonus_violations" not in cols:
            await db.execute(
                "ALTER TABLE users ADD COLUMN bonus_violations "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "bonus_blocked_until" not in cols:
            await db.execute(
                "ALTER TABLE users ADD COLUMN bonus_blocked_until "
                "REAL NOT NULL DEFAULT 0"
            )
        # 🐾 Питомцы 2.0: шкалы ухода, аксессуары, заморозки, домик, события.
        cur = await db.execute("PRAGMA table_info(pets)")
        pcols = {row["name"] for row in await cur.fetchall()}
        for col, ddl in (
            ("food", "REAL NOT NULL DEFAULT 80"),
            ("joy", "REAL NOT NULL DEFAULT 80"),
            ("energy", "REAL NOT NULL DEFAULT 80"),
            ("care_ts", "REAL NOT NULL DEFAULT 0"),
            ("items", "TEXT NOT NULL DEFAULT ''"),
            ("worn", "TEXT NOT NULL DEFAULT ''"),
            ("freezes", "INTEGER NOT NULL DEFAULT 0"),
            ("house", "INTEGER NOT NULL DEFAULT 0"),
            ("next_event_ts", "REAL NOT NULL DEFAULT 0"),
            ("events_day", "TEXT"),
            ("events_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if pcols and col not in pcols:
                await db.execute(f"ALTER TABLE pets ADD COLUMN {col} {ddl}")
        cur = await db.execute("PRAGMA table_info(quizzes)")
        qcols = {row["name"] for row in await cur.fetchall()}
        if qcols and "seq" not in qcols:
            await db.execute(
                "ALTER TABLE quizzes ADD COLUMN seq INTEGER NOT NULL DEFAULT 1"
            )
        if qcols and "total" not in qcols:
            await db.execute(
                "ALTER TABLE quizzes ADD COLUMN total INTEGER NOT NULL DEFAULT 1"
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("База не инициализирована — вызови connect()")
        return self._db

    # ---------- подсчёт сообщений ----------

    async def try_count_message(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        first_name: str | None,
        day: str,
        msg_hash: str,
        now_ts: float,
        cooldown: float,
        per_iriska: int,
        weight: int = 1,
        dedupe: bool = True,
    ) -> tuple[bool, int]:
        """Пробует засчитать сообщение.

        Возвращает (засчитано ли, сколько ирисок начислено этим сообщением).
        Антинакрутка опциональна: cooldown <= 0 отключает паузу между
        зачётами, dedupe=False разрешает повторы одного сообщения подряд.

        weight — вклад сообщения в прогресс к ириске (бонусные часы: 2).
        Счётчики сообщений (total_counted, daily_stats) всегда растут на 1.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT progress, last_counted_ts, last_msg_hash FROM users "
                "WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if row is not None:
                if cooldown > 0 and now_ts - row["last_counted_ts"] < cooldown:
                    return False, 0
                if dedupe and row["last_msg_hash"] == msg_hash:
                    return False, 0
                progress = row["progress"] + max(weight, 1)
            else:
                progress = max(weight, 1)

            accrued = 0
            if per_iriska > 0:
                accrued = progress // per_iriska
                progress = progress % per_iriska

            await db.execute(
                """
                INSERT INTO users (chat_id, user_id, username, first_name,
                                   total_counted, balance, earned_total, progress,
                                   last_counted_ts, last_msg_hash)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    username        = excluded.username,
                    first_name      = excluded.first_name,
                    total_counted   = total_counted + 1,
                    balance         = balance + excluded.balance,
                    earned_total    = earned_total + excluded.earned_total,
                    progress        = excluded.progress,
                    last_counted_ts = excluded.last_counted_ts,
                    last_msg_hash   = excluded.last_msg_hash
                """,
                (chat_id, user_id, username, first_name,
                 accrued, accrued, progress, now_ts, msg_hash),
            )
            await db.execute(
                """
                INSERT INTO daily_stats (chat_id, user_id, day, count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT (chat_id, user_id, day) DO UPDATE SET count = count + 1
                """,
                (chat_id, user_id, day),
            )
            if accrued:
                await db.execute(
                    "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                    "VALUES (?, ?, ?, 'за активность в чате')",
                    (chat_id, user_id, accrued),
                )
            await db.commit()
            return True, accrued

    # ---------- пользователи ----------

    async def ensure_user(
        self, chat_id: int, user_id: int,
        username: str | None, first_name: str | None,
    ) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                """
                INSERT INTO users (chat_id, user_id, username, first_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (chat_id, user_id, username, first_name),
            )
            await db.commit()

    async def get_user(self, chat_id: int, user_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        return await cur.fetchone()

    async def find_by_username(self, chat_id: int, username: str) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM users WHERE chat_id = ? AND lower(username) = lower(?)",
            (chat_id, username.lstrip("@")),
        )
        return await cur.fetchone()

    # ---------- статистика ----------

    async def rank(self, chat_id: int, total_counted: int) -> int:
        cur = await self._require().execute(
            "SELECT COUNT(*) + 1 AS r FROM users "
            "WHERE chat_id = ? AND total_counted > ?",
            (chat_id, total_counted),
        )
        row = await cur.fetchone()
        return int(row["r"])

    async def top_alltime(self, chat_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self._require().execute(
            "SELECT user_id, username, first_name, total_counted, balance "
            "FROM users WHERE chat_id = ? AND total_counted > 0 "
            "ORDER BY total_counted DESC, user_id LIMIT ?",
            (chat_id, limit),
        )
        return list(await cur.fetchall())

    async def top_since(self, chat_id: int, day_from: str, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self._require().execute(
            """
            SELECT u.user_id, u.username, u.first_name, SUM(d.count) AS cnt
            FROM daily_stats d
            JOIN users u ON u.chat_id = d.chat_id AND u.user_id = d.user_id
            WHERE d.chat_id = ? AND d.day >= ?
            GROUP BY d.user_id
            ORDER BY cnt DESC, u.user_id
            LIMIT ?
            """,
            (chat_id, day_from, limit),
        )
        return list(await cur.fetchall())

    async def user_count_since(self, chat_id: int, user_id: int, day_from: str) -> int:
        cur = await self._require().execute(
            "SELECT COALESCE(SUM(count), 0) AS c FROM daily_stats "
            "WHERE chat_id = ? AND user_id = ? AND day >= ?",
            (chat_id, user_id, day_from),
        )
        row = await cur.fetchone()
        return int(row["c"])

    async def user_count_on(self, chat_id: int, user_id: int, day: str) -> int:
        cur = await self._require().execute(
            "SELECT COALESCE(SUM(count), 0) AS c FROM daily_stats "
            "WHERE chat_id = ? AND user_id = ? AND day = ?",
            (chat_id, user_id, day),
        )
        row = await cur.fetchone()
        return int(row["c"])

    async def known_chats(self) -> list[int]:
        """Чаты, где бот уже вёл статистику (для анонсов)."""
        cur = await self._require().execute(
            "SELECT DISTINCT chat_id FROM users"
        )
        return [int(r["chat_id"]) for r in await cur.fetchall()]

    async def chat_totals(self, chat_id: int) -> aiosqlite.Row:
        cur = await self._require().execute(
            "SELECT COUNT(*) AS users, "
            "COALESCE(SUM(total_counted), 0) AS msgs, "
            "COALESCE(SUM(balance), 0) AS balance, "
            "COALESCE(SUM(earned_total), 0) AS earned "
            "FROM users WHERE chat_id = ? AND total_counted > 0",
            (chat_id,),
        )
        return await cur.fetchone()

    # ---------- месячный цикл статистики ----------

    async def ensure_stats_period(self, period: str) -> str:
        """Инициализирует текущий месяц статистики и возвращает его ключ YYYY-MM."""
        db = self._require()
        async with self._lock:
            await db.execute(
                "INSERT OR IGNORE INTO bot_meta (key, value) VALUES ('stats_period', ?)",
                (period,),
            )
            cur = await db.execute(
                "SELECT value FROM bot_meta WHERE key = 'stats_period'"
            )
            row = await cur.fetchone()
            await db.commit()
            return str(row["value"])

    async def close_stats_period(
        self, expected_period: str, next_period: str, now_ts: float,
    ) -> list[dict] | None:
        """Снимает месячные итоги и атомарно обнуляет статистику и балансы.

        Возвращает снимки по чатам. Если другой вызов уже закрыл этот период,
        возвращает None — это защищает от повторного анонса после рестарта.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT value FROM bot_meta WHERE key = 'stats_period'"
            )
            row = await cur.fetchone()
            if row is None or row["value"] != expected_period:
                return None

            cur = await db.execute("SELECT DISTINCT chat_id FROM users ORDER BY chat_id")
            chat_ids = [int(r["chat_id"]) for r in await cur.fetchall()]
            snapshots: list[dict] = []
            for chat_id in chat_ids:
                cur = await db.execute(
                    "SELECT user_id, username, first_name, total_counted, balance "
                    "FROM users WHERE chat_id = ? AND total_counted > 0 "
                    "ORDER BY total_counted DESC, user_id LIMIT 10",
                    (chat_id,),
                )
                top = [dict(r) for r in await cur.fetchall()]
                cur = await db.execute(
                    "SELECT COUNT(*) AS users, COALESCE(SUM(total_counted), 0) AS msgs, "
                    "COALESCE(SUM(balance), 0) AS balance "
                    "FROM users WHERE chat_id = ? AND total_counted > 0",
                    (chat_id,),
                )
                totals = dict(await cur.fetchone())
                snapshots.append(
                    {"chat_id": chat_id, "top": top, "totals": totals}
                )

            # Сгорание остаётся в журнале, чтобы экономику можно было проверить.
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "SELECT chat_id, user_id, -balance, 'ежемесячное сгорание' "
                "FROM users WHERE balance > 0"
            )
            await db.execute(
                "UPDATE users SET total_counted = 0, balance = 0, earned_total = 0, "
                "progress = 0, last_counted_ts = 0, last_msg_hash = NULL, "
                "last_bonus_day = NULL, bonus_streak = 0, "
                "bonus_claimed_ts = 0, bonus_claimed_amount = 0"
            )
            await db.execute("DELETE FROM daily_stats")
            await db.execute(
                "UPDATE duels SET status = 'cancelled', resolved_ts = ? "
                "WHERE status = 'pending'",
                (now_ts,),
            )
            await db.execute(
                "UPDATE bot_meta SET value = ? WHERE key = 'stats_period'",
                (next_period,),
            )
            await db.commit()
            return snapshots

    # ---------- ириски (админ) ----------

    async def adjust_balance(
        self, chat_id: int, user_id: int, amount: int,
        reason: str, admin_id: int | None, earned: int | None = None,
    ) -> tuple[str, int]:
        """Изменяет баланс на amount (может быть отрицательным).

        earned — сколько добавить к «всего заработано» (по умолчанию весь
        плюс). Возвраты ставок и покупок передают 0, чтобы не раздувать стату.

        Возвращает (статус, баланс):
        - ("ok", новый баланс)
        - ("not_found", 0) — участник не найден
        - ("insufficient", текущий баланс) — не хватает ирисок для списания
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT balance FROM users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                return "not_found", 0
            new_balance = row["balance"] + amount
            if new_balance < 0:
                return "insufficient", int(row["balance"])
            await db.execute(
                "UPDATE users SET balance = ?, earned_total = earned_total + ? "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, max(amount if earned is None else earned, 0),
                 chat_id, user_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason, admin_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, amount, reason, admin_id),
            )
            await db.commit()
            return "ok", new_balance

    # ---------- магазин ----------

    async def create_order(
        self, chat_id: int, user_id: int,
        username: str | None, first_name: str | None,
        item_code: str, item_title: str, price: int,
    ) -> tuple[str, int, int | None]:
        """Покупка в магазине: списывает ириски и создаёт заказ.

        Возвращает (статус, баланс, id заказа):
        - ("ok", новый баланс, id) — покупка прошла;
        - ("insufficient", текущий баланс, None) — ирисок не хватает;
        - ("not_found", 0, None) — участника нет в статистике чата.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT balance FROM users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                return "not_found", 0, None
            balance = int(row["balance"])
            if price > balance:
                return "insufficient", balance, None

            new_balance = balance - price
            await db.execute(
                "UPDATE users SET balance = ?, username = COALESCE(?, username), "
                "first_name = COALESCE(?, first_name) "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, username, first_name, chat_id, user_id),
            )
            if price > 0:
                await db.execute(
                    "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                    "VALUES (?, ?, ?, ?)",
                    (chat_id, user_id, -price, f"магазин: {item_title}"),
                )
            cur = await db.execute(
                "INSERT INTO orders (chat_id, user_id, item_code, item_title, price) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, item_code, item_title, price),
            )
            order_id = int(cur.lastrowid)
            await db.commit()
            return "ok", new_balance, order_id

    async def list_orders(
        self, chat_id: int, status: str = "new", limit: int = 20,
    ) -> list[aiosqlite.Row]:
        """Заказы чата (свежие первыми) вместе с именем покупателя.

        Пустой status — заказы в любом статусе.
        """
        sql = (
            "SELECT o.*, u.first_name AS buyer_name, u.username AS buyer_username "
            "FROM orders o "
            "LEFT JOIN users u ON u.chat_id = o.chat_id AND u.user_id = o.user_id "
            "WHERE o.chat_id = ?"
        )
        params: list = [chat_id]
        if status:
            sql += " AND o.status = ?"
            params.append(status)
        sql += " ORDER BY o.id LIMIT ?"
        params.append(limit)
        cur = await self._require().execute(sql, params)
        return list(await cur.fetchall())

    async def complete_order(
        self, order_id: int, chat_id: int, admin_id: int | None,
    ) -> bool:
        """Закрывает заказ: статус 'new' -> 'done'. False, если не нашёл."""
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "UPDATE orders SET status = 'done', resolved_at = datetime('now'), "
                "resolved_by = ? WHERE id = ? AND chat_id = ? AND status = 'new'",
                (admin_id, order_id, chat_id),
            )
            changed = cur.rowcount
            await db.commit()
            return bool(changed)

    async def refund_order(
        self, order_id: int, chat_id: int, admin_id: int | None,
    ) -> tuple[str, int]:
        """Возвращает ириски за невыполненный заказ (статус 'refunded').

        Возвращает (статус, баланс покупателя):
        - ("ok", новый баланс);
        - ("not_found", 0) — заказа нет, он в другом чате или уже закрыт.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT user_id, item_title, price FROM orders "
                "WHERE id = ? AND chat_id = ? AND status = 'new'",
                (order_id, chat_id),
            )
            row = await cur.fetchone()
            if row is None:
                return "not_found", 0

            await db.execute(
                "UPDATE users SET balance = balance + ? "
                "WHERE chat_id = ? AND user_id = ?",
                (row["price"], chat_id, row["user_id"]),
            )
            cur = await db.execute(
                "SELECT balance FROM users WHERE chat_id = ? AND user_id = ?",
                (chat_id, row["user_id"]),
            )
            new_balance = int((await cur.fetchone())["balance"])
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason, admin_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, row["user_id"], row["price"],
                 f"магазин: возврат за заказ №{order_id}", admin_id),
            )
            await db.execute(
                "UPDATE orders SET status = 'refunded', "
                "resolved_at = datetime('now'), resolved_by = ? WHERE id = ?",
                (admin_id, order_id),
            )
            await db.commit()
            return "ok", new_balance

    # ---------- ежедневный бонус ----------

    async def claim_bonus(
        self, chat_id: int, user_id: int,
        username: str | None, first_name: str | None,
        day: str, yesterday: str, base_amount: int, extra_cap: int,
        now_ts: float | None = None, abuse_limit: int = 0, block_seconds: float = 0.0,
    ) -> tuple[str, int, int, int, float]:
        """Выдаёт бонус раз в день с учётом серии (стрика).

        Если вчера бонус тоже забирали — серия растёт и добавляет к бонусу
        (+1 за каждый день серии сверх первого, но не больше extra_cap).
        Пропуск дня сбрасывает серию.

        Выданный бонус ставится под наблюдение сторожа подписки: момент выдачи
        и сумма пишутся в bonus_claimed_ts / bonus_claimed_amount. Если бонус
        был отозван за отписку, last_bonus_day остаётся занятым — повторно за
        этот день бонус не выдаётся.

        abuse_limit > 0 — включена блокировка за отписки: пока
        bonus_blocked_until в будущем, бонус закрыт (статус "blocked").
        abuse_limit = 0 отключает блокировку.

        Возвращает (статус, баланс, начислено, серия, закрыт до):
        - ("ok", новый баланс, сумма бонуса, длина серии, 0)
        - ("already", баланс, 0, текущая серия, 0)
        - ("blocked", баланс, 0, текущая серия, момент разблокировки)
        """
        now = time.time() if now_ts is None else float(now_ts)
        db = self._require()
        async with self._lock:
            await db.execute(
                """
                INSERT INTO users (chat_id, user_id, username, first_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (chat_id, user_id, username, first_name),
            )
            cur = await db.execute(
                "SELECT balance, last_bonus_day, bonus_streak, bonus_blocked_until "
                "FROM users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            blocked_until = float(row["bonus_blocked_until"] or 0.0)
            if abuse_limit > 0 and blocked_until > now:
                await db.commit()
                return (
                    "blocked", int(row["balance"]), 0,
                    int(row["bonus_streak"] or 0), blocked_until,
                )
            if row["last_bonus_day"] == day:
                await db.commit()
                return (
                    "already", int(row["balance"]), 0,
                    int(row["bonus_streak"] or 0), 0.0,
                )
            if row["last_bonus_day"] == yesterday:
                streak = int(row["bonus_streak"] or 0) + 1
            else:
                streak = 1
            extra = min(max(streak - 1, 0), max(extra_cap, 0))
            amount = base_amount + extra
            new_balance = row["balance"] + amount
            await db.execute(
                "UPDATE users SET balance = ?, earned_total = earned_total + ?, "
                "last_bonus_day = ?, bonus_streak = ?, bonus_claimed_ts = ?, "
                "bonus_claimed_amount = ?, bonus_blocked_until = 0 "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, amount, day, streak, now, amount, chat_id, user_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "VALUES (?, ?, ?, 'ежедневный бонус')",
                (chat_id, user_id, amount),
            )
            await db.commit()
            return "ok", new_balance, amount, streak, 0.0

    # ---------- сторож подписки ----------

    async def bonus_block_until(
        self, chat_id: int, user_id: int, now_ts: float,
    ) -> float:
        """До какого момента бонус закрыт за отписки (0 — не закрыт)."""
        row = await self.get_user(chat_id, user_id)
        if row is None:
            return 0.0
        until = float(row["bonus_blocked_until"] or 0.0)
        return until if until > now_ts else 0.0

    async def active_bonus_watches(
        self, now_ts: float, watch_seconds: float,
    ) -> list[aiosqlite.Row]:
        """Кого должен проверить сторож: бонус выдан и окно наблюдения идёт.

        Окно — watch_seconds от момента выдачи (bonus_claimed_ts). После его
        окончания бонус остаётся у человека, слежение снимается
        (finish_expired_bonus_watch).
        """
        if watch_seconds <= 0:
            return []
        cur = await self._require().execute(
            "SELECT chat_id, user_id, username, first_name, bonus_claimed_ts, "
            "bonus_claimed_amount, bonus_violations, bonus_blocked_until "
            "FROM users WHERE bonus_claimed_amount > 0 AND bonus_claimed_ts > 0 "
            "AND bonus_claimed_ts > ? ORDER BY bonus_claimed_ts, user_id",
            (now_ts - watch_seconds,),
        )
        return list(await cur.fetchall())

    async def finish_expired_bonus_watch(
        self, now_ts: float, watch_seconds: float,
    ) -> int:
        """Снимает наблюдение с тех, у кого окно уже истекло.

        Возвращает, сколько записей закрыто. Отдельного «штрафа» нет: человек
        сохраняет бонус, а подписку бот проверит при следующей выдаче.
        """
        if watch_seconds <= 0:
            return 0
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "UPDATE users SET bonus_claimed_ts = 0, bonus_claimed_amount = 0 "
                "WHERE bonus_claimed_amount > 0 AND bonus_claimed_ts > 0 "
                "AND bonus_claimed_ts <= ?",
                (now_ts - watch_seconds,),
            )
            await db.commit()
            return int(cur.rowcount)

    async def revoke_bonus(
        self, chat_id: int, user_id: int, *,
        now_ts: float, abuse_limit: int, block_seconds: float,
    ) -> tuple[str, int, int, float]:
        """Отзывает бонус за отписку от канала.

        Списывается начисленное, но не больше текущего баланса — в минус
        баланс не уходит. Стрик сбрасывается. Нарушения копятся; при
        достижении abuse_limit бонус закрывается до now_ts + block_seconds,
        а счётчик нарушений обнуляется (после паузы квота начинается заново).
        abuse_limit = 0 отключает и счётчик, и блокировку.

        Возвращает (статус, списано, новый баланс, закрыт до):
        - ("revoked", списано, баланс, момент разблокировки или 0)
        - ("noop", 0, баланс, 0) — отзывать нечего
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT balance, bonus_claimed_amount, bonus_violations, "
                "bonus_blocked_until FROM users "
                "WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if row is None:
                return "noop", 0, 0, 0.0
            balance = int(row["balance"])
            claimed = int(row["bonus_claimed_amount"] or 0)
            if claimed <= 0:
                return "noop", 0, balance, 0.0

            revoked = min(claimed, max(balance, 0))
            new_balance = balance - revoked
            violations = int(row["bonus_violations"] or 0)
            blocked_until = float(row["bonus_blocked_until"] or 0.0)
            if abuse_limit > 0:
                violations += 1
                if violations >= abuse_limit:
                    blocked_until = now_ts + max(block_seconds, 0.0)
                    violations = 0
            if blocked_until <= now_ts:  # старую/нулевую блокировку не храним
                blocked_until = 0.0
            await db.execute(
                "UPDATE users SET balance = ?, bonus_streak = 0, "
                "bonus_claimed_ts = 0, bonus_claimed_amount = 0, "
                "bonus_violations = ?, bonus_blocked_until = ? "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, violations, blocked_until, chat_id, user_id),
            )
            # Запись в журнал — всегда, даже если списывать было нечего:
            # по ней видно, почему бонус сгорел.
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "VALUES (?, ?, ?, 'бонус отозван: отписка от канала')",
                (chat_id, user_id, -revoked),
            )
            await db.commit()
            return "revoked", revoked, new_balance, blocked_until

    # ---------- викторины ----------

    async def create_quiz(
        self, chat_id: int, question: str, answers: list[str],
        display_answer: str, prize: int, created_by: int | None, now_ts: float,
    ) -> int:
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "INSERT INTO quizzes (chat_id, question, answers, display_answer, "
                "prize, created_by, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, question, "\n".join(answers), display_answer,
                 prize, created_by, now_ts),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def active_quiz(self, chat_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM quizzes WHERE chat_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        )
        return await cur.fetchone()

    async def active_quiz_chat_ids(self) -> list[int]:
        cur = await self._require().execute(
            "SELECT DISTINCT chat_id FROM quizzes WHERE status = 'active'"
        )
        return [int(r["chat_id"]) for r in await cur.fetchall()]

    async def try_win_quiz(
        self, quiz_id: int, chat_id: int, user_id: int,
        username: str | None, first_name: str | None, now_ts: float,
    ) -> tuple[str, int, int]:
        """Первый правильный ответ побеждает атомарно.

        Возвращает ("ok", приз, новый баланс) или ("late", 0, 0),
        если викторина уже закрыта (кто-то успел раньше).
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT prize FROM quizzes WHERE id = ? AND status = 'active'",
                (quiz_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return "late", 0, 0
            prize = int(row["prize"])
            await db.execute(
                "UPDATE quizzes SET status = 'won', winner_id = ?, resolved_ts = ? "
                "WHERE id = ?",
                (user_id, now_ts, quiz_id),
            )
            await db.execute(
                """
                INSERT INTO users (chat_id, user_id, username, first_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (chat_id, user_id, username, first_name),
            )
            cur = await db.execute(
                "SELECT balance FROM users WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            balance = (await cur.fetchone())["balance"]
            new_balance = balance + prize
            await db.execute(
                "UPDATE users SET balance = ?, earned_total = earned_total + ? "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, prize, chat_id, user_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "VALUES (?, ?, ?, 'викторина: приз')",
                (chat_id, user_id, prize),
            )
            await db.commit()
            return "ok", prize, new_balance

    async def cancel_quiz(self, quiz_id: int, now_ts: float) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "UPDATE quizzes SET status = 'cancelled', resolved_ts = ? "
                "WHERE id = ? AND status IN ('active', 'queued')",
                (now_ts, quiz_id),
            )
            await db.commit()

    async def cancel_active_quizzes(self, now_ts: float) -> tuple[list[aiosqlite.Row], int]:
        """Останавливает все викторины: активные и очередь.

        Возвращает (активные строки для показа ответов, сколько удалено из очереди).
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute("SELECT * FROM quizzes WHERE status = 'active'")
            active_rows = list(await cur.fetchall())
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM quizzes WHERE status = 'queued'"
            )
            queued = int((await cur.fetchone())["c"])
            if active_rows or queued:
                await db.execute(
                    "UPDATE quizzes SET status = 'cancelled', resolved_ts = ? "
                    "WHERE status IN ('active', 'queued')",
                    (now_ts,),
                )
                await db.commit()
            return active_rows, queued

    async def enqueue_quizzes(
        self, chat_id: int, items: list[tuple[int, str, list[str], str]],
        created_by: int | None, now_ts: float,
    ) -> int:
        """Добавляет пачку вопросов в очередь чата. items: (приз, вопрос, ответы, показ)."""
        db = self._require()
        async with self._lock:
            total = len(items)
            for i, (prize, question, answers, display) in enumerate(items, 1):
                await db.execute(
                    "INSERT INTO quizzes (chat_id, question, answers, display_answer, "
                    "prize, seq, total, status, created_by, created_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (chat_id, question, "\n".join(answers), display,
                     prize, i, total, created_by, now_ts),
                )
            await db.commit()
            return total

    async def activate_next_quiz(self, chat_id: int, now_ts: float) -> aiosqlite.Row | None:
        """Продвигает первый вопрос из очереди в активные (если активного нет)."""
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT 1 FROM quizzes WHERE chat_id = ? AND status = 'active' LIMIT 1",
                (chat_id,),
            )
            if await cur.fetchone() is not None:
                return None
            cur = await db.execute(
                "SELECT * FROM quizzes WHERE chat_id = ? AND status = 'queued' "
                "ORDER BY id LIMIT 1",
                (chat_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await db.execute(
                "UPDATE quizzes SET status = 'active' WHERE id = ?",
                (int(row["id"]),),
            )
            await db.commit()
            return row

    async def queued_count(self, chat_id: int) -> int:
        cur = await self._require().execute(
            "SELECT COUNT(*) AS c FROM quizzes WHERE chat_id = ? AND status = 'queued'",
            (chat_id,),
        )
        return int((await cur.fetchone())["c"])

    async def chats_with_queued(self) -> list[int]:
        cur = await self._require().execute(
            "SELECT DISTINCT chat_id FROM quizzes WHERE status = 'queued'"
        )
        return [int(r["chat_id"]) for r in await cur.fetchall()]

    # ---------- дуэли ----------

    async def expire_old_duels(self, chat_id: int, now_ts: float, ttl: float) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "UPDATE duels SET status = 'expired', resolved_ts = ? "
                "WHERE chat_id = ? AND status = 'pending' AND created_ts < ?",
                (now_ts, chat_id, now_ts - ttl),
            )
            await db.commit()

    async def has_pending_duel(self, chat_id: int, user_id: int) -> bool:
        cur = await self._require().execute(
            "SELECT 1 FROM duels WHERE chat_id = ? AND status = 'pending' "
            "AND (challenger_id = ? OR target_id = ?) LIMIT 1",
            (chat_id, user_id, user_id),
        )
        return await cur.fetchone() is not None

    async def create_duel(
        self, chat_id: int, challenger_id: int, target_id: int,
        amount: int, now_ts: float,
    ) -> int:
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "INSERT INTO duels (chat_id, challenger_id, target_id, amount, created_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, challenger_id, target_id, amount, now_ts),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def pending_duel_for_target(self, chat_id: int, target_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM duels WHERE chat_id = ? AND target_id = ? "
            "AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (chat_id, target_id),
        )
        return await cur.fetchone()

    async def pending_duel_by_challenger(self, chat_id: int, challenger_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM duels WHERE chat_id = ? AND challenger_id = ? "
            "AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (chat_id, challenger_id),
        )
        return await cur.fetchone()

    async def set_duel_status(self, duel_id: int, status: str, resolved_ts: float) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "UPDATE duels SET status = ?, resolved_ts = ? WHERE id = ?",
                (status, resolved_ts, duel_id),
            )
            await db.commit()

    async def settle_duel(
        self, duel_id: int, chat_id: int, winner_id: int, loser_id: int,
        amount: int, now_ts: float,
    ) -> tuple[str, int, int]:
        """Перевод ставки проигравшего победителю.

        Возвращает (статус, баланс победителя, баланс проигравшего):
        - ("ok", ...) — дуэль рассчитана
        - ("loser_broke", 0, баланс) — у проигравшего уже нет ставки, дуэль отменена
        - ("missing", 0, 0) — участник пропал из базы, дуэль отменена
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT user_id, balance FROM users "
                "WHERE chat_id = ? AND user_id IN (?, ?)",
                (chat_id, winner_id, loser_id),
            )
            balances = {r["user_id"]: r["balance"] for r in await cur.fetchall()}
            if winner_id not in balances or loser_id not in balances:
                await db.execute(
                    "UPDATE duels SET status = 'cancelled', resolved_ts = ? WHERE id = ?",
                    (now_ts, duel_id),
                )
                await db.commit()
                return "missing", 0, 0
            if balances[loser_id] < amount:
                await db.execute(
                    "UPDATE duels SET status = 'cancelled', resolved_ts = ? WHERE id = ?",
                    (now_ts, duel_id),
                )
                await db.commit()
                return "loser_broke", 0, int(balances[loser_id])
            winner_balance = balances[winner_id] + amount
            loser_balance = balances[loser_id] - amount
            await db.execute(
                "UPDATE users SET balance = ?, earned_total = earned_total + ? "
                "WHERE chat_id = ? AND user_id = ?",
                (winner_balance, amount, chat_id, winner_id),
            )
            await db.execute(
                "UPDATE users SET balance = ? WHERE chat_id = ? AND user_id = ?",
                (loser_balance, chat_id, loser_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "VALUES (?, ?, ?, 'дуэль: выигрыш'), (?, ?, ?, 'дуэль: проигрыш')",
                (chat_id, winner_id, amount, chat_id, loser_id, -amount),
            )
            await db.execute(
                "UPDATE duels SET status = 'done', winner_id = ?, resolved_ts = ? WHERE id = ?",
                (winner_id, now_ts, duel_id),
            )
            await db.commit()
            return "ok", winner_balance, loser_balance

    # ---------- 🐣 общий питомец ----------

    @staticmethod
    def _pair(u1: int, u2: int) -> tuple[int, int]:
        return (u1, u2) if u1 < u2 else (u2, u1)

    async def get_pet(self, pet_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM pets WHERE id = ?", (pet_id,)
        )
        return await cur.fetchone()

    async def pet_for_pair(
        self, chat_id: int, u1: int, u2: int,
    ) -> aiosqlite.Row | None:
        """Живой питомец (или висящее приглашение) пары в чате."""
        a, b = self._pair(u1, u2)
        cur = await self._require().execute(
            "SELECT * FROM pets WHERE chat_id = ? AND user_a = ? AND user_b = ? "
            "AND status IN ('pending', 'active') ORDER BY id DESC LIMIT 1",
            (chat_id, a, b),
        )
        return await cur.fetchone()

    async def user_pets(self, chat_id: int, user_id: int) -> list[aiosqlite.Row]:
        """Активные питомцы участника: самые длинные серии первыми."""
        cur = await self._require().execute(
            "SELECT * FROM pets WHERE chat_id = ? AND status = 'active' "
            "AND (user_a = ? OR user_b = ?) "
            "ORDER BY streak DESC, best_streak DESC, id",
            (chat_id, user_id, user_id),
        )
        return list(await cur.fetchall())

    async def _active_pet_count(self, chat_id: int, user_id: int) -> int:
        cur = await self._require().execute(
            "SELECT COUNT(*) AS c FROM pets WHERE chat_id = ? AND status = 'active' "
            "AND (user_a = ? OR user_b = ?)",
            (chat_id, user_id, user_id),
        )
        return int((await cur.fetchone())["c"])

    async def expire_pet_invites(self, now_ts: float, ttl: float) -> int:
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "UPDATE pets SET status = 'expired', resolved_ts = ? "
                "WHERE status = 'pending' AND created_ts < ?",
                (now_ts, now_ts - ttl),
            )
            await db.commit()
            return int(cur.rowcount)

    async def create_pet_invite(
        self, chat_id: int, inviter: int, target: int, species: str,
        now_ts: float, max_per_user: int,
    ) -> tuple[str, aiosqlite.Row | None]:
        """Приглашение завести питомца. Возвращает (статус, строка питомца):

        - ("ok", новое приглашение);
        - ("exists", питомец или приглашение, которое уже есть у пары);
        - ("limit_inviter" | "limit_target", None) — у кого-то уже максимум.
        """
        a, b = self._pair(inviter, target)
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pets WHERE chat_id = ? AND user_a = ? AND user_b = ? "
                "AND status IN ('pending', 'active') LIMIT 1",
                (chat_id, a, b),
            )
            row = await cur.fetchone()
            if row is not None:
                return "exists", row
            if max_per_user > 0:
                if await self._active_pet_count(chat_id, inviter) >= max_per_user:
                    return "limit_inviter", None
                if await self._active_pet_count(chat_id, target) >= max_per_user:
                    return "limit_target", None
            cur = await db.execute(
                "INSERT INTO pets (chat_id, user_a, user_b, invited_by, species, "
                "created_ts) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, a, b, inviter, species, now_ts),
            )
            pet_id = int(cur.lastrowid)
            await db.commit()
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            return "ok", await cur.fetchone()

    async def answer_pet_invite(
        self, pet_id: int, user_id: int, accept: bool,
        now_ts: float, ttl: float, max_per_user: int,
    ) -> tuple[str, aiosqlite.Row | None]:
        """Ответ на приглашение.

        Принять может только приглашённый; отказаться — он же, а пригласивший
        может отозвать приглашение. Статусы: "accepted", "declined",
        "cancelled", "not_yours", "gone" (уже решено или нет), "expired",
        "limit" (у кого-то уже максимум питомцев).
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            row = await cur.fetchone()
            if row is None or row["status"] != "pending":
                return "gone", row
            if user_id not in (row["user_a"], row["user_b"]):
                return "not_yours", row
            if now_ts - float(row["created_ts"]) > ttl:
                await db.execute(
                    "UPDATE pets SET status = 'expired', resolved_ts = ? WHERE id = ?",
                    (now_ts, pet_id),
                )
                await db.commit()
                return "expired", row
            is_inviter = user_id == row["invited_by"]
            if not accept:
                status = "cancelled" if is_inviter else "declined"
                await db.execute(
                    "UPDATE pets SET status = 'declined', resolved_ts = ? WHERE id = ?",
                    (now_ts, pet_id),
                )
                await db.commit()
                return status, row
            if is_inviter:
                return "not_yours", row
            if max_per_user > 0:
                for uid in (row["user_a"], row["user_b"]):
                    if await self._active_pet_count(row["chat_id"], uid) >= max_per_user:
                        return "limit", row
            await db.execute(
                "UPDATE pets SET status = 'active', resolved_ts = ? WHERE id = ?",
                (now_ts, pet_id),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            return "accepted", await cur.fetchone()

    async def record_pet_message(
        self, chat_id: int, from_id: int, to_id: int,
        day: str, yesterday: str, rewards: dict[int, int] | None = None,
    ) -> dict | None:
        """Отмечает, что from_id написал to_id сегодня.

        Если у пары есть активный питомец и сегодня написали оба — питомец
        «накормлен»: серия растёт (+1, если вчера тоже кормили, иначе с 1),
        а на круглых сериях из rewards обоим начисляются ириски.

        Возвращает None, если питомца нет, иначе словарь:
        fed (накормлен этим сообщением), streak, prev_best, reward, pet.
        """
        if from_id == to_id:
            return None
        a, b = self._pair(from_id, to_id)
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pets WHERE chat_id = ? AND user_a = ? AND user_b = ? "
                "AND status = 'active' LIMIT 1",
                (chat_id, a, b),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            side = "a_day" if from_id == a else "b_day"
            a_day = day if side == "a_day" else row["a_day"]
            b_day = day if side == "b_day" else row["b_day"]
            fed = a_day == day and b_day == day and row["last_fed_day"] != day
            streak = int(row["streak"])
            prev_best = int(row["best_streak"])
            reward = 0
            if fed:
                streak = streak + 1 if row["last_fed_day"] == yesterday else 1
                best = max(prev_best, streak)
                await db.execute(
                    f"UPDATE pets SET {side} = ?, streak = ?, best_streak = ?, "
                    "last_fed_day = ?, lost_streak = 0, lost_ts = 0 WHERE id = ?",
                    (day, streak, best, day, row["id"]),
                )
                reward = int((rewards or {}).get(streak, 0))
                if reward > 0:
                    for uid in (a, b):
                        await db.execute(
                            "INSERT OR IGNORE INTO users (chat_id, user_id) "
                            "VALUES (?, ?)",
                            (chat_id, uid),
                        )
                        await db.execute(
                            "UPDATE users SET balance = balance + ?, "
                            "earned_total = earned_total + ? "
                            "WHERE chat_id = ? AND user_id = ?",
                            (reward, reward, chat_id, uid),
                        )
                        await db.execute(
                            "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                            "VALUES (?, ?, ?, ?)",
                            (chat_id, uid, reward,
                             f"питомец: серия {streak} дн."),
                        )
            elif row[side] != day:
                await db.execute(
                    f"UPDATE pets SET {side} = ? WHERE id = ?", (day, row["id"])
                )
            else:
                return {"fed": False, "streak": streak, "prev_best": prev_best,
                        "reward": 0, "pet": row}
            await db.commit()
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (row["id"],))
            pet = await cur.fetchone()
            return {"fed": fed, "streak": streak, "prev_best": prev_best,
                    "reward": reward, "pet": pet}

    async def sweep_lost_pets(
        self, yesterday: str, now_ts: float,
    ) -> list[aiosqlite.Row]:
        """Питомцы, которых не кормили вчера, сбегают: серия -> 0.

        Прежняя серия сохраняется в lost_streak — её можно вернуть за ириски
        (restore_pet). Возвращает только что сбежавших.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pets WHERE status = 'active' AND streak > 0 "
                "AND last_fed_day IS NOT NULL AND last_fed_day < ?",
                (yesterday,),
            )
            rows = list(await cur.fetchall())
            if not rows:
                return []
            ids = [int(r["id"]) for r in rows]
            marks = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE pets SET lost_streak = streak, lost_ts = ?, streak = 0 "
                f"WHERE id IN ({marks})",
                (now_ts, *ids),
            )
            await db.commit()
            cur = await db.execute(
                f"SELECT * FROM pets WHERE id IN ({marks}) ORDER BY chat_id, id",
                ids,
            )
            return list(await cur.fetchall())

    async def restore_pet(
        self, pet_id: int, payer_id: int, price: int,
        now_ts: float, window_seconds: float, yesterday: str,
    ) -> tuple[str, int, aiosqlite.Row | None]:
        """Вернуть сбежавшего питомца за ириски (платит payer_id).

        Возвращает (статус, баланс плательщика, питомец):
        "ok", "nothing" (возвращать нечего), "expired" (окно прошло),
        "insufficient" (не хватает ирисок).
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            row = await cur.fetchone()
            if row is None or row["status"] != "active" or int(row["lost_streak"]) <= 0:
                return "nothing", 0, row
            if now_ts - float(row["lost_ts"]) > window_seconds:
                await db.execute(
                    "UPDATE pets SET lost_streak = 0, lost_ts = 0 WHERE id = ?",
                    (pet_id,),
                )
                await db.commit()
                return "expired", 0, row
            cur = await db.execute(
                "SELECT balance FROM users WHERE chat_id = ? AND user_id = ?",
                (row["chat_id"], payer_id),
            )
            urow = await cur.fetchone()
            balance = int(urow["balance"]) if urow else 0
            if balance < price:
                return "insufficient", balance, row
            new_balance = balance - price
            if price > 0:
                await db.execute(
                    "UPDATE users SET balance = ? WHERE chat_id = ? AND user_id = ?",
                    (new_balance, row["chat_id"], payer_id),
                )
                await db.execute(
                    "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                    "VALUES (?, ?, ?, 'питомец: возвращение серии')",
                    (row["chat_id"], payer_id, -price),
                )
            await db.execute(
                "UPDATE pets SET streak = lost_streak, lost_streak = 0, lost_ts = 0, "
                "last_fed_day = ? WHERE id = ?",
                (yesterday, pet_id),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            return "ok", new_balance, await cur.fetchone()

    # ---------- 🐾 питомцы 2.0 ----------

    PET_FIELDS = frozenset({
        "food", "joy", "energy", "care_ts", "items", "worn", "freezes", "house",
        "next_event_ts", "events_day", "events_count",
    })

    async def update_pet(self, pet_id: int, **fields) -> aiosqlite.Row | None:
        """Обновить поля ухода питомца (только из белого списка)."""
        bad = set(fields) - self.PET_FIELDS
        if bad:
            raise ValueError(f"нельзя менять поля питомца: {bad}")
        db = self._require()
        async with self._lock:
            if fields:
                sets = ", ".join(f"{k} = ?" for k in fields)
                await db.execute(
                    f"UPDATE pets SET {sets} WHERE id = ?", (*fields.values(), pet_id)
                )
                await db.commit()
            cur = await db.execute("SELECT * FROM pets WHERE id = ?", (pet_id,))
            return await cur.fetchone()

    async def get_pet_media(self, key: str) -> str | None:
        cur = await self._require().execute(
            "SELECT file_id FROM pet_media WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        return row["file_id"] if row else None

    async def set_pet_media(self, key: str, file_id: str) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "INSERT INTO pet_media (key, file_id) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET file_id = excluded.file_id",
                (key, file_id),
            )
            await db.commit()

    async def drop_pet_media(self, key: str) -> None:
        db = self._require()
        async with self._lock:
            await db.execute("DELETE FROM pet_media WHERE key = ?", (key,))
            await db.commit()

    async def get_pet_action(self, pet_id: int, user_id: int, action: str) -> float:
        cur = await self._require().execute(
            "SELECT ts FROM pet_actions WHERE pet_id = ? AND user_id = ? AND action = ?",
            (pet_id, user_id, action),
        )
        row = await cur.fetchone()
        return float(row["ts"]) if row else 0.0

    async def set_pet_action(
        self, pet_id: int, user_id: int, action: str, ts: float,
    ) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "INSERT INTO pet_actions (pet_id, user_id, action, ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(pet_id, user_id, action) DO UPDATE SET ts = excluded.ts",
                (pet_id, user_id, action, ts),
            )
            await db.commit()

    async def pets_for_events(self) -> list[aiosqlite.Row]:
        """Живые вылупившиеся питомцы с идущей серией."""
        cur = await self._require().execute(
            "SELECT * FROM pets WHERE status = 'active' AND streak > 0 ORDER BY id"
        )
        return list(await cur.fetchall())

    async def create_pet_event(
        self, pet_id: int, chat_id: int, kind: str, now_ts: float, ttl: float,
    ) -> int:
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "INSERT INTO pet_events (pet_id, chat_id, kind, created_ts, expires_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (pet_id, chat_id, kind, now_ts, now_ts + ttl),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def set_pet_event_message(self, event_id: int, message_id: int) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "UPDATE pet_events SET message_id = ? WHERE id = ?", (message_id, event_id)
            )
            await db.commit()

    async def get_pet_event(self, event_id: int) -> aiosqlite.Row | None:
        cur = await self._require().execute(
            "SELECT * FROM pet_events WHERE id = ?", (event_id,)
        )
        return await cur.fetchone()

    async def press_pet_event(
        self, event_id: int, user_id: int, now_ts: float, need_both: bool,
    ) -> tuple[str, aiosqlite.Row | None]:
        """Нажатие на событие. Возвращает (статус, событие):
        "gone" — уже закрыто/истекло, "again" — уже нажимал,
        "wait" — ждём второго (для совместных), "done" — событие закрыто этим нажатием.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute("SELECT * FROM pet_events WHERE id = ?", (event_id,))
            ev = await cur.fetchone()
            if ev is None or ev["status"] != "open" or float(ev["expires_ts"]) < now_ts:
                return "gone", ev
            pressed = [x for x in ev["pressed"].split(",") if x]
            if str(user_id) in pressed:
                return "again", ev
            pressed.append(str(user_id))
            done = not need_both or len(pressed) >= 2
            await db.execute(
                "UPDATE pet_events SET pressed = ?, status = ? WHERE id = ?",
                (",".join(pressed), "done" if done else "open", event_id),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM pet_events WHERE id = ?", (event_id,))
            return ("done" if done else "wait"), await cur.fetchone()

    async def expire_pet_events(self, now_ts: float) -> list[aiosqlite.Row]:
        """Закрывает просроченные события и возвращает их."""
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pet_events WHERE status = 'open' AND expires_ts < ?",
                (now_ts,),
            )
            rows = list(await cur.fetchall())
            if rows:
                ids = [int(r["id"]) for r in rows]
                marks = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE pet_events SET status = 'expired' WHERE id IN ({marks})", ids
                )
                await db.commit()
            return rows

    async def use_pet_freezes(self, yesterday: str) -> list[aiosqlite.Row]:
        """🧊 Заморозка: питомцы, пропустившие вчера, но с заморозкой в запасе,
        не сбегают — тратится одна заморозка, вчерашний день засчитывается."""
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pets WHERE status = 'active' AND streak > 0 "
                "AND freezes > 0 AND last_fed_day IS NOT NULL AND last_fed_day < ?",
                (yesterday,),
            )
            rows = list(await cur.fetchall())
            if not rows:
                return []
            ids = [int(r["id"]) for r in rows]
            marks = ",".join("?" * len(ids))
            await db.execute(
                f"UPDATE pets SET freezes = freezes - 1, last_fed_day = ? "
                f"WHERE id IN ({marks})",
                (yesterday, *ids),
            )
            await db.commit()
            cur = await db.execute(
                f"SELECT * FROM pets WHERE id IN ({marks}) ORDER BY chat_id, id", ids
            )
            return list(await cur.fetchall())

    async def rename_pet(self, pet_id: int, name: str) -> None:
        db = self._require()
        async with self._lock:
            await db.execute(
                "UPDATE pets SET name = ? WHERE id = ?", (name or None, pet_id)
            )
            await db.commit()

    async def abandon_pet(self, pet_id: int, user_id: int, now_ts: float) -> bool:
        """Отпустить питомца (может любой из пары). False — нечего отпускать."""
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "UPDATE pets SET status = 'abandoned', resolved_ts = ? "
                "WHERE id = ? AND status = 'active' AND (user_a = ? OR user_b = ?)",
                (now_ts, pet_id, user_id, user_id),
            )
            await db.commit()
            return bool(cur.rowcount)

    async def pets_to_remind(self, today: str, yesterday: str) -> list[aiosqlite.Row]:
        """Питомцы под угрозой: кормили вчера, сегодня ещё нет.

        Каждого питомца отмечаем, чтобы напоминание ушло раз в день.
        """
        db = self._require()
        async with self._lock:
            cur = await db.execute(
                "SELECT * FROM pets WHERE status = 'active' AND streak > 0 "
                "AND last_fed_day = ? AND COALESCE(last_remind_day, '') != ? "
                "ORDER BY chat_id, streak DESC, id",
                (yesterday, today),
            )
            rows = list(await cur.fetchall())
            if rows:
                ids = [int(r["id"]) for r in rows]
                marks = ",".join("?" * len(ids))
                await db.execute(
                    f"UPDATE pets SET last_remind_day = ? WHERE id IN ({marks})",
                    (today, *ids),
                )
                await db.commit()
            return rows

    async def top_pets(self, chat_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self._require().execute(
            "SELECT * FROM pets WHERE chat_id = ? AND status = 'active' "
            "AND best_streak > 0 ORDER BY streak DESC, best_streak DESC, id LIMIT ?",
            (chat_id, limit),
        )
        return list(await cur.fetchall())

    async def user_names(self, chat_id: int, user_ids: list[int]) -> dict[int, aiosqlite.Row]:
        if not user_ids:
            return {}
        marks = ",".join("?" * len(user_ids))
        cur = await self._require().execute(
            f"SELECT user_id, username, first_name FROM users "
            f"WHERE chat_id = ? AND user_id IN ({marks})",
            (chat_id, *user_ids),
        )
        return {int(r["user_id"]): r for r in await cur.fetchall()}
