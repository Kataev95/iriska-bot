"""Слой работы с базой SQLite (aiosqlite)."""

from __future__ import annotations

import asyncio
import os
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

CREATE INDEX IF NOT EXISTS idx_daily_chat_day ON daily_stats (chat_id, day);
CREATE INDEX IF NOT EXISTS idx_users_top ON users (chat_id, total_counted DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger (chat_id, user_id);
CREATE INDEX IF NOT EXISTS idx_duels_pending ON duels (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_quizzes_active ON quizzes (chat_id, status);
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

    # ---------- ириски (админ) ----------

    async def adjust_balance(
        self, chat_id: int, user_id: int, amount: int,
        reason: str, admin_id: int | None,
    ) -> tuple[str, int]:
        """Изменяет баланс на amount (может быть отрицательным).

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
                (new_balance, max(amount, 0), chat_id, user_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason, admin_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (chat_id, user_id, amount, reason, admin_id),
            )
            await db.commit()
            return "ok", new_balance

    # ---------- ежедневный бонус ----------

    async def claim_bonus(
        self, chat_id: int, user_id: int,
        username: str | None, first_name: str | None,
        day: str, yesterday: str, base_amount: int, extra_cap: int,
    ) -> tuple[str, int, int, int]:
        """Выдаёт бонус раз в день с учётом серии (стрика).

        Если вчера бонус тоже забирали — серия растёт и добавляет к бонусу
        (+1 за каждый день серии сверх первого, но не больше extra_cap).
        Пропуск дня сбрасывает серию.

        Возвращает (статус, баланс, начислено, серия):
        - ("ok", новый баланс, сумма бонуса, длина серии)
        - ("already", баланс, 0, текущая серия)
        """
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
                "SELECT balance, last_bonus_day, bonus_streak FROM users "
                "WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            row = await cur.fetchone()
            if row["last_bonus_day"] == day:
                await db.commit()
                return "already", int(row["balance"]), 0, int(row["bonus_streak"] or 0)
            if row["last_bonus_day"] == yesterday:
                streak = int(row["bonus_streak"] or 0) + 1
            else:
                streak = 1
            extra = min(max(streak - 1, 0), max(extra_cap, 0))
            amount = base_amount + extra
            new_balance = row["balance"] + amount
            await db.execute(
                "UPDATE users SET balance = ?, earned_total = earned_total + ?, "
                "last_bonus_day = ?, bonus_streak = ? "
                "WHERE chat_id = ? AND user_id = ?",
                (new_balance, amount, day, streak, chat_id, user_id),
            )
            await db.execute(
                "INSERT INTO ledger (chat_id, user_id, amount, reason) "
                "VALUES (?, ?, ?, 'ежедневный бонус')",
                (chat_id, user_id, amount),
            )
            await db.commit()
            return "ok", new_balance, amount, streak

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
