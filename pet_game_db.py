"""Атомарные действия игры: права, затраты и награды в одной транзакции."""

from contextlib import asynccontextmanager
from datetime import datetime

from pet_care import (
    ACTIONS,
    FREEZE_MAX,
    SHOP_BY_CODE,
    TOGETHER_JOY,
    TOGETHER_WINDOW,
    check_action,
    clamp,
    decay,
    join_set,
    parse_set,
)
from pet_game import DAILY_START_LIMIT, PROFESSIONS, ROUTES, TASKS, legacy_xp, level_for


class PetGameDatabase:
    async def _migrate_pet_game(self):
        db = self._require()
        cols = {
            r["name"]
            for r in await (await db.execute("PRAGMA table_info(pets)")).fetchall()
        }
        if "xp" not in cols:
            await db.execute(
                "ALTER TABLE pets ADD COLUMN xp INTEGER NOT NULL DEFAULT 0"
            )
        if "profession" not in cols:
            await db.execute(
                "ALTER TABLE pets ADD COLUMN profession TEXT NOT NULL DEFAULT ''"
            )
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS pet_trips (
                id INTEGER PRIMARY KEY, pet_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                day TEXT NOT NULL, route TEXT NOT NULL, started_at REAL NOT NULL,
                finish_at REAL NOT NULL, xp INTEGER NOT NULL, coins INTEGER NOT NULL,
                claimed_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS pet_trip_active ON pet_trips(pet_id)
                WHERE claimed_at IS NULL;
            CREATE INDEX IF NOT EXISTS pet_trip_daily ON pet_trips(user_id, day);
            CREATE TABLE IF NOT EXISTS pet_daily_activity (
                pet_id INTEGER NOT NULL, day TEXT NOT NULL, user_id INTEGER NOT NULL,
                kind TEXT NOT NULL, PRIMARY KEY(pet_id, day, user_id, kind)
            );
            CREATE TABLE IF NOT EXISTS pet_daily_claims (
                pet_id INTEGER NOT NULL, day TEXT NOT NULL, task TEXT NOT NULL,
                PRIMARY KEY(pet_id, day, task)
            );
        """)
        marker = await (
            await db.execute("SELECT value FROM bot_meta WHERE key='pets3_migrated'")
        ).fetchone()
        if marker is None:
            rows = await (await db.execute("SELECT * FROM pets")).fetchall()
            for row in rows:
                best = max(row["best_streak"], row["streak"], row["lost_streak"])
                await db.execute(
                    "UPDATE pets SET xp=MAX(xp, ?), best_streak=? WHERE id=?",
                    (legacy_xp(best), best, row["id"]),
                )
            await db.execute(
                "INSERT INTO bot_meta(key,value) VALUES('pets3_migrated','1')"
            )

    @asynccontextmanager
    async def _game_transaction(self):
        async with self._lock:
            db = self._require()
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _game_pet(self, db, pet_id, user_id):
        pet = await (
            await db.execute("SELECT * FROM pets WHERE id=?", (pet_id,))
        ).fetchone()
        if (
            pet is None
            or pet["status"] != "active"
            or user_id not in (pet["user_a"], pet["user_b"])
        ):
            return None
        return pet

    async def _game_refresh(self, db, pet, now, tz):
        f, j, e = decay(
            pet["food"],
            pet["joy"],
            pet["energy"],
            pet["care_ts"],
            now,
            tz,
            bool(pet["house"]),
        )
        await db.execute(
            "UPDATE pets SET food=?,joy=?,energy=?,care_ts=? WHERE id=?",
            (f, j, e, now, pet["id"]),
        )
        return await (
            await db.execute("SELECT * FROM pets WHERE id=?", (pet["id"],))
        ).fetchone()

    async def game_refresh(self, pet_id, now, tz, food=0, joy=0, energy=0):
        async with self._game_transaction() as db:
            pet = await (
                await db.execute("SELECT * FROM pets WHERE id=?", (pet_id,))
            ).fetchone()
            if pet is None:
                return None
            pet = await self._game_refresh(db, pet, now, tz)
            await db.execute(
                "UPDATE pets SET food=?,joy=?,energy=? WHERE id=?",
                (
                    clamp(pet["food"] + food),
                    clamp(pet["joy"] + joy),
                    clamp(pet["energy"] + energy),
                    pet_id,
                ),
            )
            return await (
                await db.execute("SELECT * FROM pets WHERE id=?", (pet_id,))
            ).fetchone()

    async def game_care(self, pet_id, user_id, action, now, tz):
        if action not in ACTIONS:
            return "Неизвестное действие"
        act = ACTIONS[action]
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            pet = await self._game_refresh(db, pet, now, tz)
            refusal = check_action(
                action,
                pet["food"],
                pet["joy"],
                pet["energy"],
                datetime.fromtimestamp(now, tz).hour,
            )
            if refusal:
                return refusal
            last = await (
                await db.execute(
                    "SELECT ts FROM pet_actions WHERE pet_id=? AND user_id=? AND action=?",
                    (pet_id, user_id, action),
                )
            ).fetchone()
            if last and now - last["ts"] < act.cooldown:
                return f"Повтори через {max(1, int((act.cooldown - now + last['ts']) / 60))} мин."
            partner = pet["user_b"] if user_id == pet["user_a"] else pet["user_a"]
            other = await (
                await db.execute(
                    "SELECT ts FROM pet_actions WHERE pet_id=? AND user_id=? AND action=?",
                    (pet_id, partner, action),
                )
            ).fetchone()
            together = other is not None and 0 <= now - other["ts"] <= TOGETHER_WINDOW
            await db.execute(
                "INSERT INTO pet_actions VALUES(?,?,?,?) ON CONFLICT(pet_id,user_id,action) DO UPDATE SET ts=excluded.ts",
                (pet_id, user_id, action, now),
            )
            await db.execute(
                "UPDATE pets SET food=?,joy=?,energy=? WHERE id=?",
                (
                    clamp(pet["food"] + act.food),
                    clamp(pet["joy"] + act.joy + (TOGETHER_JOY if together else 0)),
                    clamp(pet["energy"] + act.energy),
                    pet_id,
                ),
            )
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            await db.execute(
                "INSERT OR IGNORE INTO pet_daily_activity VALUES(?,?,?,'care')",
                (pet_id, day, user_id),
            )
            return (
                ("💕 Вы позаботились вдвоём! " if together else "✅ ")
                + act.done
                + " Задание обновлено."
            )

    async def game_buy(self, pet_id, user_id, code, now, tz):
        item = SHOP_BY_CODE.get(code)
        if item is None:
            return "Неизвестный товар"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            pet = await self._game_refresh(db, pet, now, tz)
            owned = parse_set(pet["items"])
            if (
                item.kind == "wear"
                and code in owned
                or item.kind == "house"
                and pet["house"]
            ):
                return "Уже куплено"
            if item.kind == "freeze" and pet["freezes"] >= FREEZE_MAX:
                return "Запас заморозок полон"
            if item.kind == "food" and pet["food"] >= 95:
                return "Питомец уже сыт"
            cur = await db.execute(
                "UPDATE users SET balance=balance-? WHERE chat_id=? AND user_id=? AND balance>=?",
                (item.price, pet["chat_id"], user_id, item.price),
            )
            if cur.rowcount != 1:
                return f"Не хватает ирисок: нужно {item.price} 🍬"
            await db.execute(
                "INSERT INTO ledger(chat_id,user_id,amount,reason) VALUES(?,?,?,?)",
                (pet["chat_id"], user_id, -item.price, f"питомец: {item.title}"),
            )
            if item.kind == "food":
                await db.execute(
                    "UPDATE pets SET food=?,joy=? WHERE id=?",
                    (
                        clamp(pet["food"] + item.food),
                        clamp(pet["joy"] + item.joy),
                        pet_id,
                    ),
                )
            elif item.kind == "wear":
                await db.execute(
                    "UPDATE pets SET items=?,worn=? WHERE id=?",
                    (
                        join_set(owned + [code]),
                        join_set(parse_set(pet["worn"]) + [code]),
                        pet_id,
                    ),
                )
            elif item.kind == "freeze":
                await db.execute(
                    "UPDATE pets SET freezes=freezes+1 WHERE id=?", (pet_id,)
                )
            else:
                await db.execute("UPDATE pets SET house=1 WHERE id=?", (pet_id,))
            return f"✅ Товар куплен: {item.title} за {item.price} 🍬"

    async def game_wear(self, pet_id, user_id, code):
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or code not in parse_set(pet["items"]):
                return False
            worn = parse_set(pet["worn"])
            worn.remove(code) if code in worn else worn.append(code)
            await db.execute(
                "UPDATE pets SET worn=? WHERE id=?", (join_set(worn), pet_id)
            )
            return True

    async def start_trip(self, pet_id, user_id, route, now, tz):
        if route not in ROUTES:
            return "Неизвестный маршрут"
        r = ROUTES[route]
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not pet["best_streak"]:
                return "Сначала питомец должен вылупиться"
            if await (
                await db.execute(
                    "SELECT id FROM pet_trips WHERE pet_id=? AND claimed_at IS NULL",
                    (pet_id,),
                )
            ).fetchone():
                return "Поход уже начат — дождись окончания и забери награду"
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            count = (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM pet_trips WHERE user_id=? AND day=?",
                        (user_id, day),
                    )
                ).fetchone()
            )[0]
            if count >= DAILY_START_LIMIT:
                return "Сегодня уже начаты 3 похода. Новые — завтра"
            pet = await self._game_refresh(db, pet, now, tz)
            energy = r.energy - (3 if pet["profession"] == "artisan" else 0)
            if pet["food"] < 20 or pet["energy"] < energy:
                return f"Нужно 20 сытости и {energy} энергии. Покорми питомца или уложи спать"
            xp = r.xp * 120 // 100 if pet["profession"] == "explorer" else r.xp
            coins = r.coins + (1 if pet["profession"] == "merchant" else 0)
            await db.execute(
                "UPDATE pets SET food=food-5,energy=energy-? WHERE id=?",
                (energy, pet_id),
            )
            await db.execute(
                "INSERT INTO pet_trips(pet_id,user_id,day,route,started_at,finish_at,xp,coins) VALUES(?,?,?,?,?,?,?,?)",
                (pet_id, user_id, day, route, now, now + r.seconds, xp, coins),
            )
            return "✅ Питомец отправился в путь! Награда будет ждать вас"

    async def active_trip(self, pet_id):
        return await (
            await self._require().execute(
                "SELECT * FROM pet_trips WHERE pet_id=? AND claimed_at IS NULL",
                (pet_id,),
            )
        ).fetchone()

    async def claim_trip(self, pet_id, user_id, trip_id, now, tz):
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "Питомец недоступен"
            trip = await (
                await db.execute(
                    "SELECT * FROM pet_trips WHERE id=? AND pet_id=?", (trip_id, pet_id)
                )
            ).fetchone()
            if trip is None or trip["claimed_at"] is not None:
                return "Эта награда уже получена или поход не найден"
            if now < trip["finish_at"]:
                return "Поход ещё не закончен"
            await db.execute(
                "UPDATE pet_trips SET claimed_at=? WHERE id=?", (now, trip_id)
            )
            await db.execute("UPDATE pets SET xp=xp+? WHERE id=?", (trip["xp"], pet_id))
            await db.execute(
                "INSERT OR IGNORE INTO users(chat_id,user_id) VALUES(?,?)",
                (pet["chat_id"], trip["user_id"]),
            )
            await db.execute(
                "UPDATE users SET balance=balance+?,earned_total=earned_total+? WHERE chat_id=? AND user_id=?",
                (trip["coins"], trip["coins"], pet["chat_id"], trip["user_id"]),
            )
            await db.execute(
                "INSERT INTO ledger(chat_id,user_id,amount,reason) VALUES(?,?,?,?)",
                (
                    pet["chat_id"],
                    trip["user_id"],
                    trip["coins"],
                    f"поход #{trip_id}: {trip['route']}",
                ),
            )
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            await db.execute(
                "INSERT OR IGNORE INTO pet_daily_activity VALUES(?,?,?,'adventure')",
                (pet_id, day, user_id),
            )
            return f"{ROUTES[trip['route']].story}\n⭐ +{trip['xp']} XP питомцу, 🍬 +{trip['coins']} отправившему хозяину."

    async def choose_profession(self, pet_id, user_id, code):
        if code not in PROFESSIONS:
            return "Неизвестная профессия"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "Питомец недоступен"
            if level_for(pet["xp"]) < 5:
                return "Профессия откроется на 5-м уровне"
            if pet["profession"]:
                return "Профессия уже выбрана"
            await db.execute("UPDATE pets SET profession=? WHERE id=?", (code, pet_id))
            return f"✅ Профессия: {PROFESSIONS[code][0]}"

    async def _daily_state(self, db, pet_id, day):
        rows = await (
            await db.execute(
                "SELECT * FROM pet_daily_activity WHERE pet_id=? AND day=?",
                (pet_id, day),
            )
        ).fetchall()
        care = {r["user_id"] for r in rows if r["kind"] == "care"}
        completed = {
            "care": bool(care),
            "together": len(care) >= 2,
            "adventure": any(r["kind"] == "adventure" for r in rows),
        }
        claimed = {
            r["task"]
            for r in await (
                await db.execute(
                    "SELECT task FROM pet_daily_claims WHERE pet_id=? AND day=?",
                    (pet_id, day),
                )
            ).fetchall()
        }
        return completed, claimed

    async def daily_state(self, pet_id, day):
        return await self._daily_state(self._require(), pet_id, day)

    async def claim_daily(self, pet_id, user_id, day):
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            completed, claimed = await self._daily_state(db, pet_id, day)
            xp = 0
            for code, (_, reward) in TASKS.items():
                if completed[code] and code not in claimed:
                    await db.execute(
                        "INSERT INTO pet_daily_claims VALUES(?,?,?)",
                        (pet_id, day, code),
                    )
                    xp += reward
            await db.execute("UPDATE pets SET xp=xp+? WHERE id=?", (xp, pet_id))
            return (
                f"⭐ +{xp} XP питомцу за задания"
                if xp
                else "Новых выполненных заданий пока нет"
            )
