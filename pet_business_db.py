"""Atomic shared businesses for PETS WORLD 1.5.

No autonomous income. Real goods and coins appear ONLY after authorized
explicit player actions. All operations take PetGameDatabase._game_transaction
to serialize overlapping actions with both owners, purchases and adventures.
"""
from datetime import datetime

from pet_business import (
    BUSINESSES, DAILY_PRODUCTION_STARTS, DAILY_SALE_COIN_LIMIT,
    MAX_BUSINESS_LEVEL,
)
from pet_inventory import MATERIALS


class PetBusinessDatabase:
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
            CREATE INDEX IF NOT EXISTS idx_pet_business_sales_daily
                ON pet_business_sales(pet_id,day);
        """)

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
                "SELECT id,business,finish_at,output_qty FROM pet_business_jobs "
                "WHERE pet_id=? AND collected_at IS NULL",
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
        level_by = {r["business"]: int(r["level"]) for r in levels}
        stock_by = {r["business"]: int(r["quantity"]) for r in stocks}
        job_by = {r["business"]: r for r in jobs}
        state = {}
        for code in BUSINESSES:
            active = job_by.get(code)
            state[code] = {
                "level": level_by.get(code, 0),
                "stock": stock_by.get(code, 0),
                "job": {
                    "id": int(active["id"]),
                    "finish_at": float(active["finish_at"]),
                    "quantity": int(active["output_qty"]),
                } if active else None,
            }
        return {
            "units": state,
            "starts_remaining": max(0, DAILY_PRODUCTION_STARTS - int(started[0])),
            "sales_coins_remaining": max(0, DAILY_SALE_COIN_LIMIT - int(sales[0])),
        }

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
            await db.execute(
                "INSERT INTO pet_business_jobs("
                "pet_id,business,user_id,day,started_at,finish_at,output_qty) "
                "VALUES(?,?,?,?,?,?,?)",
                (pet_id, code, user_id, day, now,
                 now + definition.production_seconds, quantity),
            )
            return (
                f"✅ {definition.icon} Производство запущено! "
                f"Готово через {definition.production_seconds//60} минут."
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

    async def sell_business_goods(self, pet_id, user_id, code, now, tz):
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
            day = datetime.fromtimestamp(now, tz).date().isoformat()
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
            await db.execute(
                "INSERT INTO pet_business_sales("
                "pet_id,business,user_id,day,quantity,coins,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (pet_id, code, user_id, day, quantity, coins, now),
            )
            return (
                f"✅ Продано {definition.good_icon} {quantity} шт. "
                f"🍬 +{coins} ирисок продавцу. Лимит: {remaining-coins} 🍬 сегодня."
            )
