"""Atomic shared businesses for PETS WORLD 1.5.

No autonomous income. Real goods and coins appear ONLY after authorized
explicit player actions. All operations take PetGameDatabase._game_transaction
to serialize overlapping actions with both owners, purchases and adventures.
"""
from datetime import datetime

from pet_business import (
    BUSINESSES, DAILY_PRODUCTION_STARTS, DAILY_SALE_COIN_LIMIT,
    MAX_BUSINESS_LEVEL, TYCOON_MAX_TAPS, TYCOON_TAP_COOLDOWN,
    TYCOON_TAP_SECONDS, TYCOON_WORKER_COSTS, TYCOON_ORDER_QUANTITY, production_duration,
)
from pet_inventory import MATERIALS
from pet_market_db import PetMarketDatabase


class PetBusinessDatabase(PetMarketDatabase):
    async def _migrate_business(self):
        db = self._require()
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS pet_business_units (
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 2),
                built_at REAL NOT NULL,
                PRIMARY KEY(pet_id,business)
            );
            CREATE TABLE IF NOT EXISTS pet_business_upgrades (
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 2),
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(pet_id,business,level)
            );
            CREATE TABLE IF NOT EXISTS pet_business_jobs (
                id INTEGER PRIMARY KEY,
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                started_at REAL NOT NULL,
                finish_at REAL NOT NULL,
                output_qty INTEGER NOT NULL CHECK(output_qty > 0),
                collected_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pet_business_active
                ON pet_business_jobs(pet_id,business)
                WHERE collected_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_pet_business_daily
                ON pet_business_jobs(pet_id,day);
            CREATE TABLE IF NOT EXISTS pet_business_goods (
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                PRIMARY KEY(pet_id,business)
            );
            CREATE TABLE IF NOT EXISTS pet_business_sales (
                id INTEGER PRIMARY KEY,
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                coins INTEGER NOT NULL CHECK(coins > 0),
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pet_tycoon_workers (
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 2),
                PRIMARY KEY(pet_id,business)
            );
            CREATE TABLE IF NOT EXISTS pet_tycoon_orders (
                pet_id INTEGER NOT NULL,
                business TEXT NOT NULL,
                day TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                sale_id INTEGER NOT NULL UNIQUE,
                PRIMARY KEY(pet_id,business,day)
            );
            CREATE TABLE IF NOT EXISTS pet_tycoon_work (
                job_id INTEGER PRIMARY KEY,
                taps INTEGER NOT NULL CHECK(taps BETWEEN 1 AND 30),
                last_tap_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pet_business_sales_daily
                ON pet_business_sales(pet_id,day);
        """)
        await self._migrate_market()

    async def business_state(self, pet_id, day):
        """Read-only UI data. Caller checks ownership before invoking this."""
        db = self._require()
        levels = await (
            await db.execute(
                "SELECT business,level FROM pet_business_units WHERE pet_id=?",
                (pet_id,),
            )
        ).fetchall()
        stocks = await (
            await db.execute(
                "SELECT business,quantity FROM pet_business_goods WHERE pet_id=?",
                (pet_id,),
            )
        ).fetchall()
        jobs = await (
            await db.execute(
                "SELECT j.id,j.business,j.finish_at,j.output_qty, "
                "COALESCE(w.taps,0) AS taps,COALESCE(w.last_tap_at,0) AS last_tap_at "
                "FROM pet_business_jobs j LEFT JOIN pet_tycoon_work w ON w.job_id=j.id "
                "WHERE j.pet_id=? AND j.collected_at IS NULL",
                (pet_id,),
            )
        ).fetchall()
        started = await (
            await db.execute(
                "SELECT COUNT(*) FROM pet_business_jobs WHERE pet_id=? AND day=?",
                (pet_id, day),
            )
        ).fetchone()
        sales = await (
            await db.execute(
                "SELECT COALESCE(SUM(coins),0) FROM pet_business_sales "
                "WHERE pet_id=? AND day=?",
                (pet_id, day),
            )
        ).fetchone()
        workers = await (await db.execute(
            "SELECT business,level FROM pet_tycoon_workers WHERE pet_id=?", (pet_id,)
        )).fetchall()
        worker_by = {r["business"]: int(r["level"]) for r in workers}
        orders = await (await db.execute(
            "SELECT business FROM pet_tycoon_orders WHERE pet_id=? AND day=?", (pet_id, day)
        )).fetchall()
        completed_orders = {row["business"] for row in orders}
        level_by = {r["business"]: int(r["level"]) for r in levels}
        stock_by = {r["business"]: int(r["quantity"]) for r in stocks}
        job_by = {r["business"]: r for r in jobs}
        state = {}
        for code in BUSINESSES:
            active = job_by.get(code)
            state[code] = {
                "level": level_by.get(code, 0),
                "stock": stock_by.get(code, 0),
                "workers": worker_by.get(code, 0),
                "order_done": code in completed_orders,
                "job": self._tycoon_job(active) if active else None,
            }
        return {
            "units": state,
            "starts_remaining": max(0, DAILY_PRODUCTION_STARTS - int(started[0])),
            "sales_coins_remaining": max(0, DAILY_SALE_COIN_LIMIT - int(sales[0])),
        }

    @staticmethod
    def _tycoon_job(row):
        return {
            "id": int(row["id"]), "finish_at": float(row["finish_at"]),
            "quantity": int(row["output_qty"]), "taps": int(row["taps"]),
            "tap_ready_at": float(row["last_tap_at"]) + TYCOON_TAP_COOLDOWN,
        }

    async def hire_tycoon_worker(self, pet_id, user_id, code, expected_level):
        if code not in BUSINESSES or type(expected_level) is not int:
            return "Неверные данные работника"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            unit = await (await db.execute(
                "SELECT level FROM pet_business_units WHERE pet_id=? AND business=?",
                (pet_id, code),
            )).fetchone()
            if unit is None:
                return "Сначала постройте предприятие"
            row = await (await db.execute(
                "SELECT level FROM pet_tycoon_workers WHERE pet_id=? AND business=?",
                (pet_id, code),
            )).fetchone()
            level = int(row["level"]) if row else 0
            if expected_level != level:
                return "Работники изменились. Обновите экран"
            if level >= 2:
                return "Команда уже полностью улучшена"
            if level >= int(unit["level"]):
                return "Сначала улучшите оборудование"
            refusal = await self._business_deduct(db, pet_id, TYCOON_WORKER_COSTS[code][level])
            if refusal:
                return refusal
            await db.execute(
                "INSERT INTO pet_tycoon_workers(pet_id,business,level) VALUES(?,?,?) "
                "ON CONFLICT(pet_id,business) DO UPDATE SET level=excluded.level",
                (pet_id, code, level+1),
            )
            return "✅ Работник нанят! Новые партии будут производиться быстрее."

    async def tap_tycoon_job(self, pet_id, user_id, code, job_id, expected_taps, now):
        """One server-timed tap on an already paid batch; no direct goods or coins."""
        def response(message, job=None, accepted=False):
            return {"message": message, "job": job, "accepted": accepted}
        if (code not in BUSINESSES or type(job_id) is not int or job_id <= 0
                or type(expected_taps) is not int or not 0 <= expected_taps <= TYCOON_MAX_TAPS):
            return response("Неверные данные нажатия")
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return response("Питомец недоступен")
            row = await (await db.execute(
                "SELECT j.id,j.finish_at,j.output_qty,u.level,"
                "COALESCE(w.taps,0) AS taps,COALESCE(w.last_tap_at,0) AS last_tap_at "
                "FROM pet_business_jobs j JOIN pet_business_units u "
                "ON u.pet_id=j.pet_id AND u.business=j.business "
                "LEFT JOIN pet_tycoon_work w ON w.job_id=j.id "
                "WHERE j.pet_id=? AND j.business=? AND j.collected_at IS NULL",
                (pet_id, code),
            )).fetchone()
            if row is None:
                return response("Партия уже забрана или не найдена")
            job = self._tycoon_job(row)
            if job["id"] != job_id or job["taps"] != expected_taps:
                return response("Партия обновлена", job)
            if now >= job["finish_at"]:
                return response("Партия уже готова", job)
            if job["taps"] >= TYCOON_MAX_TAPS:
                return response("Лимит ручной работы исчерпан", job)
            if now < job["tap_ready_at"]:
                return response("Подождите немного", job)
            finish = max(now, job["finish_at"] - TYCOON_TAP_SECONDS[int(row["level"])-1])
            await db.execute(
                "UPDATE pet_business_jobs SET finish_at=? WHERE id=?",
                (finish, job_id),
            )
            await db.execute(
                "INSERT INTO pet_tycoon_work(job_id,taps,last_tap_at) VALUES(?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET taps=excluded.taps,last_tap_at=excluded.last_tap_at",
                (job_id, expected_taps+1, now),
            )
            job.update(finish_at=finish, taps=expected_taps+1,
                       tap_ready_at=now+TYCOON_TAP_COOLDOWN)
            return response("✅ Партия готова!" if finish <= now else "✅ Работа выполнена", job, True)

    async def _business_deduct(self, db, pet_id, costs):
        """Check every cost before writing, with an atomic balance update."""
        rows = await (
            await db.execute(
                "SELECT material,quantity FROM pet_materials WHERE pet_id=?",
                (pet_id,),
            )
        ).fetchall()
        stock = {r["material"]: int(r["quantity"]) for r in rows}
        missing = [
            f"{MATERIALS[code].icon} {MATERIALS[code].title}: {qty-stock.get(code,0)}"
            for code, qty in costs if stock.get(code, 0) < qty
        ]
        if missing:
            return "Не хватает материалов: " + ", ".join(missing)
        for code, qty in costs:
            result = await db.execute(
                "UPDATE pet_materials SET quantity=quantity-? "
                "WHERE pet_id=? AND material=? AND quantity>=?",
                (qty, pet_id, code, qty),
            )
            if result.rowcount != 1:
                raise RuntimeError("Изменился запас материалов, действие отменено")
        return None

    async def build_business(self, pet_id, user_id, code, now, expected_level):
        definition = BUSINESSES.get(code)
        if definition is None:
            return "Неизвестное предприятие"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            row = await (
                await db.execute(
                    "SELECT level FROM pet_business_units "
                    "WHERE pet_id=? AND business=?",
                    (pet_id, code),
                )
            ).fetchone()
            level = int(row["level"]) if row else 0
            if expected_level != level:
                return "Уровень предприятия изменился. Обновите экран"
            if level >= MAX_BUSINESS_LEVEL:
                return "Предприятие уже полностью улучшено"
            refusal = await self._business_deduct(
                db, pet_id, definition.upgrades[level]
            )
            if refusal:
                return refusal
            await db.execute(
                "INSERT INTO pet_business_units(pet_id,business,level,built_at) "
                "VALUES(?,?,?,?) ON CONFLICT(pet_id,business) DO UPDATE SET "
                "level=excluded.level,built_at=excluded.built_at",
                (pet_id, code, level+1, now),
            )
            await db.execute(
                "INSERT INTO pet_business_upgrades("
                "pet_id,business,level,user_id,created_at) VALUES(?,?,?,?,?)",
                (pet_id, code, level+1, user_id, now),
            )
            return (
                f"✅ {definition.icon} {definition.title}: уровень {level+1}/2. "
                "Теперь можно выпускать товары!"
            )

    async def start_business_job(self, pet_id, user_id, code, now, tz):
        definition = BUSINESSES.get(code)
        if definition is None:
            return "Неизвестное предприятие"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            unit = await (
                await db.execute(
                    "SELECT level FROM pet_business_units "
                    "WHERE pet_id=? AND business=?",
                    (pet_id, code),
                )
            ).fetchone()
            if unit is None:
                return "Сначала постройте предприятие"
            pending = await (
                await db.execute(
                    "SELECT id FROM pet_business_jobs WHERE pet_id=? "
                    "AND business=? AND collected_at IS NULL",
                    (pet_id, code),
                )
            ).fetchone()
            if pending:
                return "Партия уже запущена — дождитесь и заберите готовый товар"
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            starts = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_business_jobs WHERE pet_id=? AND day=?",
                    (pet_id, day),
                )
            ).fetchone()
            if int(starts[0]) >= DAILY_PRODUCTION_STARTS:
                return "Общий лимит: 4 запуска производства в день. Возвращайтесь завтра"
            refusal = await self._business_deduct(
                db, pet_id, definition.input_materials
            )
            if refusal:
                return refusal
            quantity = definition.outputs[int(unit["level"])-1]
            workers = await (await db.execute(
                "SELECT level FROM pet_tycoon_workers WHERE pet_id=? AND business=?",
                (pet_id, code),
            )).fetchone()
            seconds = production_duration(code, int(workers["level"]) if workers else 0)
            await db.execute(
                "INSERT INTO pet_business_jobs("
                "pet_id,business,user_id,day,started_at,finish_at,output_qty) "
                "VALUES(?,?,?,?,?,?,?)",
                (pet_id, code, user_id, day, now,
                 now + seconds, quantity),
            )
            return (
                f"✅ {definition.icon} Производство запущено! "
                f"Готово через {(seconds+59)//60} минут."
            )

    async def collect_business_job(self, pet_id, user_id, code, job_id, now):
        definition = BUSINESSES.get(code)
        if definition is None:
            return "Неизвестное предприятие"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            row = await (
                await db.execute(
                    "SELECT * FROM pet_business_jobs "
                    "WHERE pet_id=? AND business=? AND id=?",
                    (pet_id, code, job_id),
                )
            ).fetchone()
            if row is None or row["collected_at"] is not None:
                return "Товар уже забран или партия не найдена"
            if now < row["finish_at"]:
                return "Производство ещё не завершено"
            changed = await db.execute(
                "UPDATE pet_business_jobs SET collected_at=? "
                "WHERE id=? AND pet_id=? AND collected_at IS NULL",
                (now, job_id, pet_id),
            )
            if changed.rowcount != 1:
                return "Эта партия уже забрана"
            await db.execute(
                "INSERT INTO pet_business_goods(pet_id,business,quantity) "
                "VALUES(?,?,?) ON CONFLICT(pet_id,business) "
                "DO UPDATE SET quantity=quantity+excluded.quantity",
                (pet_id, code, int(row["output_qty"])),
            )
            return (
                f"✅ На общий склад: {definition.good_icon} "
                f"+{row['output_qty']} {definition.good_title.lower()}."
            )

    async def sell_business_goods(self, pet_id, user_id, code, now, tz, *, order=False, expected_stock=None):
        definition = BUSINESSES.get(code)
        if definition is None:
            return "Неизвестное предприятие"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            stock = await (
                await db.execute(
                    "SELECT quantity FROM pet_business_goods "
                    "WHERE pet_id=? AND business=?",
                    (pet_id, code),
                )
            ).fetchone()
            if stock is None or int(stock["quantity"]) <= 0:
                return "Нет готовых товаров для продажи"
            if order and (type(expected_stock) is not int or expected_stock != int(stock["quantity"])):
                return "Склад изменился. Обновите заказ"
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            if order:
                done = await (await db.execute(
                    "SELECT 1 FROM pet_tycoon_orders WHERE pet_id=? AND business=? AND day=?",
                    (pet_id, code, day),
                )).fetchone()
                if done:
                    return "Сегодняшний заказ уже выполнен"
            earned = await (
                await db.execute(
                    "SELECT COALESCE(SUM(coins),0) FROM pet_business_sales "
                    "WHERE pet_id=? AND day=?",
                    (pet_id, day),
                )
            ).fetchone()
            remaining = max(0, DAILY_SALE_COIN_LIMIT-int(earned[0]))
            quantity = min(int(stock["quantity"]), remaining//definition.sell_price)
            if quantity <= 0:
                return "Дневной лимит продаж исчерпан. Попробуйте завтра"
            if order:
                if quantity < TYCOON_ORDER_QUANTITY:
                    return "Для заказа нужно 2 товара и достаточный лимит продаж"
                quantity = TYCOON_ORDER_QUANTITY
            coins = quantity*definition.sell_price
            changed = await db.execute(
                "UPDATE pet_business_goods SET quantity=quantity-? "
                "WHERE pet_id=? AND business=? AND quantity>=?",
                (quantity, pet_id, code, quantity),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Недостаточно товаров: продажа отменена")
            await db.execute(
                "INSERT OR IGNORE INTO users(chat_id,user_id) VALUES(?,?)",
                (pet["chat_id"], user_id),
            )
            await db.execute(
                "UPDATE users SET balance=balance+?,earned_total=earned_total+? "
                "WHERE chat_id=? AND user_id=?",
                (coins, coins, pet["chat_id"], user_id),
            )
            await db.execute(
                "INSERT INTO ledger(chat_id,user_id,amount,reason) "
                "VALUES(?,?,?,?)",
                (pet["chat_id"], user_id, coins,
                 f"питомец #{pet_id}, бизнес {code}: {quantity} шт."),
            )
            sale = await db.execute(
                "INSERT INTO pet_business_sales("
                "pet_id,business,user_id,day,quantity,coins,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (pet_id, code, user_id, day, quantity, coins, now),
            )
            if order:
                await db.execute(
                    "INSERT INTO pet_tycoon_orders(pet_id,business,day,user_id,sale_id) VALUES(?,?,?,?,?)",
                    (pet_id, code, day, user_id, sale.lastrowid),
                )
            return (
                ("✅ Заказ выполнен! " if order else "✅ Продано ")
                + f"{definition.good_icon} {quantity} шт. "
                f"🍬 +{coins} ирисок продавцу. Лимит: {remaining-coins} 🍬 сегодня."
            )
