"""Atomic farm and fishing loop. Shared pet goods; existing personal iriski wallets."""
from datetime import datetime
from secrets import randbelow

from pet_business_db import PetBusinessDatabase
from pet_countryside import (
    CROPS, FISH, FARM_DAILY_PLANTS, FISH_DAILY_CASTS, COUNTRY_DAILY_SALES,
    PLOT_COUNTS, WATER_PERCENT, BITE_WINDOWS, UPGRADES, roll_fish,
)


class PetCountrysideDatabase(PetBusinessDatabase):
    async def _migrate_countryside(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_country_upgrades (
                pet_id INTEGER NOT NULL, kind TEXT NOT NULL,
                level INTEGER NOT NULL CHECK(level BETWEEN 2 AND 3),
                PRIMARY KEY(pet_id,kind)
            );
            CREATE TABLE IF NOT EXISTS pet_farm_plots (
                pet_id INTEGER NOT NULL, slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 4),
                version INTEGER NOT NULL CHECK(version>=1),
                crop TEXT, ready_at REAL, watered INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(pet_id,slot)
            );
            CREATE TABLE IF NOT EXISTS pet_farm_days (
                pet_id INTEGER NOT NULL, day TEXT NOT NULL,
                planted INTEGER NOT NULL CHECK(planted BETWEEN 1 AND 6),
                PRIMARY KEY(pet_id,day)
            );
            CREATE TABLE IF NOT EXISTS pet_fishing_casts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pet_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, day TEXT NOT NULL,
                bite_at REAL NOT NULL, expires_at REAL NOT NULL,
                rod INTEGER NOT NULL CHECK(rod BETWEEN 1 AND 3),
                resolved_at REAL, fish TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_country_active_cast
                ON pet_fishing_casts(pet_id) WHERE resolved_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_country_cast_day ON pet_fishing_casts(pet_id,day);
            CREATE TABLE IF NOT EXISTS pet_country_goods (
                pet_id INTEGER NOT NULL, kind TEXT NOT NULL, code TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity>=0),
                PRIMARY KEY(pet_id,kind,code)
            );
            CREATE TABLE IF NOT EXISTS pet_country_sales (
                id INTEGER PRIMARY KEY, pet_id INTEGER NOT NULL, day TEXT NOT NULL,
                user_id INTEGER NOT NULL, kind TEXT NOT NULL, code TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity>0), coins INTEGER NOT NULL CHECK(coins>0)
            );
            CREATE INDEX IF NOT EXISTS idx_country_sales_day ON pet_country_sales(pet_id,day);
        """)

    async def _country_levels(self, db, pet_id):
        rows = await (await db.execute(
            "SELECT kind,level FROM pet_country_upgrades WHERE pet_id=?", (pet_id,)
        )).fetchall()
        return {**dict.fromkeys(UPGRADES,1), **{r["kind"]:int(r["level"]) for r in rows}}

    async def _country_goods_add(self, db, pet_id, kind, code, quantity):
        await db.execute(
            "INSERT INTO pet_country_goods VALUES(?,?,?,?) ON CONFLICT(pet_id,kind,code) "
            "DO UPDATE SET quantity=quantity+excluded.quantity", (pet_id,kind,code,quantity)
        )

    async def _country_counts(self, db, pet_id, day):
        planted = await (await db.execute(
            "SELECT planted FROM pet_farm_days WHERE pet_id=? AND day=?", (pet_id,day)
        )).fetchone()
        casts = await (await db.execute(
            "SELECT COUNT(*) FROM pet_fishing_casts WHERE pet_id=? AND day=?", (pet_id,day)
        )).fetchone()
        sales = await (await db.execute(
            "SELECT COALESCE(SUM(coins),0) FROM pet_country_sales WHERE pet_id=? AND day=?", (pet_id,day)
        )).fetchone()
        return (int(planted[0]) if planted else 0,int(casts[0]),int(sales[0]))

    async def countryside_state(self, pet_id, day):
        # Serialize the short read with writes so the pair sees consistent state.
        async with self._game_transaction() as db:
            levels = await self._country_levels(db,pet_id)
            plants, casts, sales = await self._country_counts(db,pet_id,day)
            rows = await (await db.execute(
                "SELECT slot,version,crop,ready_at,watered FROM pet_farm_plots WHERE pet_id=?", (pet_id,)
            )).fetchall()
            plots = {r["slot"]:dict(r) for r in rows}
            last = await (await db.execute(
                "SELECT id,bite_at,expires_at,resolved_at,fish FROM pet_fishing_casts "
                "WHERE pet_id=? ORDER BY id DESC LIMIT 1", (pet_id,)
            )).fetchone()
            goods = await (await db.execute(
                "SELECT kind,code,quantity FROM pet_country_goods WHERE pet_id=?", (pet_id,)
            )).fetchall()
            collection = await (await db.execute(
                "SELECT fish,COUNT(*) AS total FROM pet_fishing_casts "
                "WHERE pet_id=? AND fish IS NOT NULL GROUP BY fish", (pet_id,)
            )).fetchall()
            return {
                "levels": levels,
                "plots": [plots.get(i,{"slot":i,"version":0,"crop":None,"ready_at":None,"watered":0})
                          for i in range(1,PLOT_COUNTS[levels["land"]-1]+1)],
                "plants_remaining": max(0,FARM_DAILY_PLANTS-plants),
                "casts_remaining": max(0,FISH_DAILY_CASTS-casts),
                "sales_remaining": max(0,COUNTRY_DAILY_SALES-sales),
                "last_cast_id": int(last["id"]) if last else 0,
                "cast": {k:last[k] for k in ("id","bite_at","expires_at")} if last and last["resolved_at"] is None else None,
                "last_fish": last["fish"] if last else None,
                "crops": {r["code"]:r["quantity"] for r in goods if r["kind"]=="crop"},
                "fish": {r["code"]:r["quantity"] for r in goods if r["kind"]=="fish"},
                "collection": {r["fish"]:r["total"] for r in collection},
            }

    async def country_upgrade(self, pet_id, user_id, kind, expected_level):
        if kind not in UPGRADES or type(expected_level) is not int:
            return "Неверное улучшение"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            level = (await self._country_levels(db,pet_id))[kind]
            if level != expected_level or level>=3:
                return "Уровень изменился или уже максимальный"
            refusal = await self._business_deduct(db,pet_id,UPGRADES[kind][level-1])
            if refusal:
                return refusal
            await db.execute(
                "INSERT INTO pet_country_upgrades VALUES(?,?,?) ON CONFLICT(pet_id,kind) "
                "DO UPDATE SET level=excluded.level", (pet_id,kind,level+1)
            )
            return f"✅ Улучшение до уровня {level+1}/3!"

    async def farm_action(self, pet_id, user_id, action, crop, slot, version, now, tz):
        if (action not in ("plant","water","harvest") or type(slot) is not int
                or type(version) is not int or version<0):
            return "Неверные данные грядки"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            levels = await self._country_levels(db,pet_id)
            if not 1<=slot<=PLOT_COUNTS[levels["land"]-1]:
                return "Грядка недоступна"
            row = await (await db.execute(
                "SELECT * FROM pet_farm_plots WHERE pet_id=? AND slot=?", (pet_id,slot)
            )).fetchone()
            current = int(row["version"]) if row else 0
            if version != current:
                return "Грядка изменилась. Обновите экран"
            planted = row["crop"] if row else None
            if action=="plant":
                if planted or crop not in CROPS:
                    return "Грядка занята или растение неизвестно"
                day = datetime.fromtimestamp(now,tz).date().isoformat()
                plants,_,_ = await self._country_counts(db,pet_id,day)
                if plants>=FARM_DAILY_PLANTS:
                    return "Лимит посадок на сегодня исчерпан"
                definition = CROPS[crop]
                changed = await db.execute(
                    "UPDATE users SET balance=balance-? WHERE chat_id=? AND user_id=? AND balance>=?",
                    (definition.price,pet["chat_id"],user_id,definition.price)
                )
                if changed.rowcount!=1:
                    return "Не хватает ирисок на семена"
                await db.execute(
                    "INSERT INTO ledger(chat_id,user_id,amount,reason) VALUES(?,?,?,?)",
                    (pet["chat_id"],user_id,-definition.price,f"питомец #{pet_id}, семена {crop}")
                )
                await db.execute(
                    "INSERT INTO pet_farm_plots VALUES(?,?,?,?,?,0) ON CONFLICT(pet_id,slot) "
                    "DO UPDATE SET version=excluded.version,crop=excluded.crop,ready_at=excluded.ready_at,watered=0",
                    (pet_id,slot,current+1,crop,now+definition.seconds)
                )
                await db.execute(
                    "INSERT INTO pet_farm_days VALUES(?,?,1) ON CONFLICT(pet_id,day) "
                    "DO UPDATE SET planted=planted+1", (pet_id,day)
                )
                return f"✅ {definition.icon} Семена посажены!"
            if not planted or planted not in CROPS:
                return "На грядке ничего нет"
            definition=CROPS[planted]
            if action=="water":
                if row["watered"] or now>=row["ready_at"]:
                    return "Грядка уже полита или урожай готов"
                bonuses = await self._progression_effects(db, pet)
                percent = WATER_PERCENT[levels["tool"]-1] + bonuses["water_bonus"]
                finish=max(now,row["ready_at"]-definition.seconds*percent/100)
                await db.execute(
                    "UPDATE pet_farm_plots SET watered=1,ready_at=?,version=version+1 WHERE pet_id=? AND slot=?",
                    (finish,pet_id,slot)
                )
                return "✅ Грядка полита! Урожай созреет быстрее."
            if now<row["ready_at"]:
                return "Урожай ещё не созрел"
            await self._country_goods_add(db,pet_id,"crop",planted,definition.quantity)
            await db.execute(
                "UPDATE pet_farm_plots SET crop=NULL,ready_at=NULL,watered=0,version=version+1 WHERE pet_id=? AND slot=?",
                (pet_id,slot)
            )
            return f"✅ Урожай на складе: {definition.icon} +{definition.quantity}"

    async def fish_cast(self, pet_id,user_id,expected_last,now,tz):
        if type(expected_last) is not int or expected_last<0:
            return "Неверные данные заброса"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            last=await (await db.execute(
                "SELECT * FROM pet_fishing_casts WHERE pet_id=? ORDER BY id DESC LIMIT 1",(pet_id,)
            )).fetchone()
            if expected_last!=(last["id"] if last else 0):
                return "Заброс изменился. Обновите экран"
            if last and last["resolved_at"] is None and now<=last["expires_at"]:
                return "Удочка уже заброшена"
            day=datetime.fromtimestamp(now,tz).date().isoformat()
            _,casts,_=await self._country_counts(db,pet_id,day)
            if casts>=FISH_DAILY_CASTS:
                return "Все забросы на сегодня использованы"
            if last and last["resolved_at"] is None:
                await db.execute("UPDATE pet_fishing_casts SET resolved_at=? WHERE id=?",(now,last["id"]))
            rod=(await self._country_levels(db,pet_id))["rod"]
            bite=now+5+randbelow(5)
            await db.execute(
                "INSERT INTO pet_fishing_casts(pet_id,user_id,day,bite_at,expires_at,rod) VALUES(?,?,?,?,?,?)",
                (pet_id,user_id,day,bite,bite+BITE_WINDOWS[rod-1],rod)
            )
            return "✅ Удочка заброшена. Дождитесь клёва!"

    async def fish_reel(self,pet_id,user_id,cast_id,now):
        if type(cast_id) is not int or cast_id<=0:
            return "Неверный заброс"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            row=await (await db.execute(
                "SELECT * FROM pet_fishing_casts WHERE id=? AND pet_id=? AND resolved_at IS NULL",
                (cast_id,pet_id)
            )).fetchone()
            if row is None:
                return "Заброс уже завершён"
            if now<row["bite_at"]:
                return "Рано! Дождитесь клёва"
            if now>row["expires_at"]:
                await db.execute("UPDATE pet_fishing_casts SET resolved_at=? WHERE id=?",(now,cast_id))
                return "Рыба сорвалась. Попробуйте новый заброс"
            fish=roll_fish(int(row["rod"]))
            await db.execute("UPDATE pet_fishing_casts SET resolved_at=?,fish=? WHERE id=?",(now,fish,cast_id))
            await self._country_goods_add(db,pet_id,"fish",fish,1)
            return f"✅ Улов: {FISH[fish].icon} {FISH[fish].name}!"

    async def country_sell(self,pet_id,user_id,kind,code,expected_stock,now,tz):
        definitions=CROPS if kind=="crop" else FISH if kind=="fish" else {}
        if code not in definitions or type(expected_stock) is not int or expected_stock<=0:
            return "Неверные данные продажи"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            row=await (await db.execute(
                "SELECT quantity FROM pet_country_goods WHERE pet_id=? AND kind=? AND code=?",
                (pet_id,kind,code)
            )).fetchone()
            if not row or row["quantity"]!=expected_stock:
                return "Склад изменился. Обновите экран"
            day=datetime.fromtimestamp(now,tz).date().isoformat()
            _,_,sales=await self._country_counts(db,pet_id,day)
            price=definitions[code].sell
            quantity=min(expected_stock,max(0,COUNTRY_DAILY_SALES-sales)//price)
            if quantity<=0:
                return "Лимит продаж фермы и рыбалки на сегодня исчерпан"
            coins=quantity*price
            await db.execute(
                "UPDATE pet_country_goods SET quantity=quantity-? WHERE pet_id=? AND kind=? AND code=?",
                (quantity,pet_id,kind,code)
            )
            await db.execute("INSERT OR IGNORE INTO users(chat_id,user_id) VALUES(?,?)",(pet["chat_id"],user_id))
            await db.execute(
                "UPDATE users SET balance=balance+?,earned_total=earned_total+? WHERE chat_id=? AND user_id=?",
                (coins,coins,pet["chat_id"],user_id)
            )
            await db.execute("INSERT INTO ledger(chat_id,user_id,amount,reason) VALUES(?,?,?,?)",
                             (pet["chat_id"],user_id,coins,f"питомец #{pet_id}, ферма/рыбалка {code}"))
            await db.execute(
                "INSERT INTO pet_country_sales(pet_id,day,user_id,kind,code,quantity,coins) VALUES(?,?,?,?,?,?,?)",
                (pet_id,day,user_id,kind,code,quantity,coins)
            )
            return f"✅ Продано {quantity} шт. +{coins} 🍬 продавцу!"
