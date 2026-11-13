"""SQLite marketplace for PETS WORLD 1.6 — escrow and zero-sum trades.

No background cron or volatile cache. Every market request lazily expires
stale offers and refunds escrow. DB has one writer lock and BEGIN IMMEDIATE
through the inherited _game_transaction.
"""
from datetime import datetime

from pet_market import (
    ITEM_TYPES, LISTING_TTL_SECONDS, MAX_ACTIVE_LISTINGS_PER_PET,
    MAX_LISTINGS_PER_PET_DAY, MAX_BUYS_PER_PET_DAY,
    MAX_ITEM_QUANTITY, MAX_UNIT_PRICE, item_details,
)


class PetMarketDatabase:
    async def _migrate_market(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                seller_pet_id INTEGER NOT NULL,
                seller_user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                item_kind TEXT NOT NULL CHECK(item_kind IN ('material','good')),
                item_code TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 5),
                unit_price INTEGER NOT NULL CHECK(unit_price BETWEEN 1 AND 20),
                total_price INTEGER NOT NULL CHECK(total_price > 0),
                state TEXT NOT NULL DEFAULT 'active'
                  CHECK(state IN ('active','sold','cancelled','expired')),
                buyer_pet_id INTEGER,
                buyer_user_id INTEGER,
                created_ts REAL NOT NULL,
                expires_ts REAL NOT NULL,
                settled_ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_pet_market_chat_active
                ON pet_market_listings(chat_id,state,created_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_pet_market_expiry
                ON pet_market_listings(state,expires_ts);
            CREATE INDEX IF NOT EXISTS idx_pet_market_seller_day
                ON pet_market_listings(seller_pet_id,day);
            CREATE INDEX IF NOT EXISTS idx_pet_market_buyer_day
                ON pet_market_listings(buyer_pet_id,settled_ts);
        """)

    async def _market_adjust_stock(self, db, pet_id, kind, code, qty):
        """Credit escrow/transfer by server-validated item identifier."""
        if kind == "material":
            await db.execute(
                "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?) "
                "ON CONFLICT(pet_id,material) DO UPDATE "
                "SET quantity=quantity+excluded.quantity",
                (pet_id, code, qty),
            )
        else:
            await db.execute(
                "INSERT INTO pet_business_goods(pet_id,business,quantity) "
                "VALUES(?,?,?) ON CONFLICT(pet_id,business) "
                "DO UPDATE SET quantity=quantity+excluded.quantity",
                (pet_id, code, qty),
            )

    async def _market_debit_stock(self, db, pet_id, kind, code, qty):
        if kind == "material":
            cur = await db.execute(
                "UPDATE pet_materials SET quantity=quantity-? "
                "WHERE pet_id=? AND material=? AND quantity>=?",
                (qty, pet_id, code, qty),
            )
        else:
            cur = await db.execute(
                "UPDATE pet_business_goods SET quantity=quantity-? "
                "WHERE pet_id=? AND business=? AND quantity>=?",
                (qty, pet_id, code, qty),
            )
        return cur.rowcount == 1

    async def _expire_market_locked(self, db, chat_id, now):
        """Settle each expired offer exactly once, returning reserved goods."""
        rows = await (
            await db.execute(
                "SELECT * FROM pet_market_listings WHERE chat_id=? "
                "AND state='active' AND expires_ts<=? ORDER BY id",
                (chat_id, now),
            )
        ).fetchall()
        for row in rows:
            cur = await db.execute(
                "UPDATE pet_market_listings SET state='expired',settled_ts=? "
                "WHERE id=? AND state='active'",
                (now, row["id"]),
            )
            if cur.rowcount == 1:
                await self._market_adjust_stock(
                    db, row["seller_pet_id"], row["item_kind"],
                    row["item_code"], int(row["quantity"]),
                )

    async def market_state(self, pet_id, user_id, now, tz):
        """Chat-private market with authorized view of offers for a shared pet."""
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return None
            chat_id = int(pet["chat_id"])
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            await self._expire_market_locked(db, chat_id, now)
            rows = await (
                await db.execute(
                    "SELECT l.*,p.user_a AS owner_a,p.user_b AS owner_b,"
                    "CASE WHEN p.name IS NOT NULL AND length(trim(p.name))>0 "
                    "THEN p.name ELSE 'Питомец #' || p.id END AS pet_name "
                    "FROM pet_market_listings l "
                    "JOIN pets p ON p.id=l.seller_pet_id "
                    "WHERE l.chat_id=? AND l.state='active' "
                    "AND p.status='active' ORDER BY l.created_ts DESC,l.id DESC LIMIT 50",
                    (chat_id,),
                )
            ).fetchall()
            listing_count = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_market_listings "
                    "WHERE seller_pet_id=? AND day=?",
                    (pet_id, day),
                )
            ).fetchone()
            bought = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_market_listings "
                    "WHERE buyer_pet_id=? AND state='sold' "
                    "AND settled_ts>=? AND settled_ts<?",
                    (
                        pet_id,
                        datetime.fromtimestamp(now, tz).replace(
                            hour=0,minute=0,second=0,microsecond=0
                        ).timestamp(),
                        datetime.fromtimestamp(now, tz).replace(
                            hour=0,minute=0,second=0,microsecond=0
                        ).timestamp()+86400,
                    ),
                )
            ).fetchone()
            # Note: the 24h range is valid for Europe/Moscow configured TZ.
            return {
                "chat_id": chat_id,
                "remaining_posts": max(0, MAX_LISTINGS_PER_PET_DAY-int(listing_count[0])),
                "remaining_buys": max(0, MAX_BUYS_PER_PET_DAY-int(bought[0])),
                "remaining_active": max(
                    0, MAX_ACTIVE_LISTINGS_PER_PET -
                    sum(int(r["seller_pet_id"])==pet_id for r in rows)
                ),
                "listings": [
                    {
                        "id": int(r["id"]),
                        "seller_pet_id": int(r["seller_pet_id"]),
                        "pet_name": r["pet_name"],
                        "kind": r["item_kind"],
                        "code": r["item_code"],
                        "quantity": int(r["quantity"]),
                        "unit_price": int(r["unit_price"]),
                        "total_price": int(r["total_price"]),
                        "expires_at": float(r["expires_ts"]),
                        "own": int(r["seller_pet_id"]) == pet_id,
                        "can_buy": (
                            int(r["seller_pet_id"]) != pet_id
                            and user_id not in (r["owner_a"],r["owner_b"])
                        ),
                    }
                    for r in rows
                ],
            }

    async def create_market_listing(
        self, pet_id, user_id, kind, code, quantity, unit_price, now, tz
    ):
        info = item_details(kind, code)
        if info is None:
            return "Этот предмет нельзя выставить на рынок"
        if not (
            type(quantity) is int and 1 <= quantity <= MAX_ITEM_QUANTITY
            and type(unit_price) is int and 1 <= unit_price <= MAX_UNIT_PRICE
        ):
            return "Неверное количество или цена"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None or not int(pet["best_streak"]):
                return "Питомец недоступен"
            await self._expire_market_locked(db, pet["chat_id"], now)
            count = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_market_listings "
                    "WHERE seller_pet_id=? AND state='active'",
                    (pet_id,),
                )
            ).fetchone()
            if count[0] >= MAX_ACTIVE_LISTINGS_PER_PET:
                return "У питомца уже максимум активных объявлений (3)"
            day = datetime.fromtimestamp(now, tz).date().isoformat()
            count_day = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_market_listings "
                    "WHERE seller_pet_id=? AND day=?",
                    (pet_id, day),
                )
            ).fetchone()
            if count_day[0] >= MAX_LISTINGS_PER_PET_DAY:
                return "Сегодня уже размещено 5 объявлений. Приходите завтра"
            if not await self._market_debit_stock(
                db, pet_id, kind, code, quantity
            ):
                return "На общем складе недостаточно предметов"
            await db.execute(
                "INSERT INTO pet_market_listings("
                "chat_id,seller_pet_id,seller_user_id,day,item_kind,item_code,"
                "quantity,unit_price,total_price,created_ts,expires_ts)"
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pet["chat_id"], pet_id, user_id, day, kind, code,
                 quantity, unit_price, quantity*unit_price, now,
                 now+LISTING_TTL_SECONDS),
            )
            return (
                f"✅ На рынок выставлено: {info['icon']} {quantity} шт., "
                f"цена {quantity*unit_price} 🍬 за всё. Срок — 48 часов."
            )

    async def cancel_market_listing(self, pet_id, user_id, listing_id, now):
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "Питомец недоступен"
            await self._expire_market_locked(db, pet["chat_id"], now)
            listing = await (
                await db.execute(
                    "SELECT * FROM pet_market_listings WHERE id=? "
                    "AND seller_pet_id=? AND chat_id=?",
                    (listing_id, pet_id, pet["chat_id"]),
                )
            ).fetchone()
            if listing is None or listing["state"] != "active":
                return "Объявление уже закрыто"
            cur = await db.execute(
                "UPDATE pet_market_listings SET state='cancelled',settled_ts=? "
                "WHERE id=? AND state='active'",
                (now, listing_id),
            )
            if cur.rowcount != 1:
                return "Объявление уже закрыто"
            await self._market_adjust_stock(
                db, pet_id, listing["item_kind"], listing["item_code"],
                int(listing["quantity"]),
            )
            return "✅ Объявление снято, все предметы вернулись на общий склад"

    async def buy_market_listing(self, pet_id, user_id, listing_id, now, tz):
        async with self._game_transaction() as db:
            buyer = await self._game_pet(db, pet_id, user_id)
            if buyer is None or not int(buyer["best_streak"]):
                return "Питомец недоступен"
            chat_id = int(buyer["chat_id"])
            await self._expire_market_locked(db, chat_id, now)
            listing = await (
                await db.execute(
                    "SELECT * FROM pet_market_listings "
                    "WHERE id=? AND chat_id=?",
                    (listing_id, chat_id),
                )
            ).fetchone()
            if listing is None or listing["state"] != "active":
                return "Объявление уже продано или закрыто"
            if int(listing["seller_pet_id"]) == pet_id:
                return "Нельзя купить предложение собственного питомца"
            seller = await (
                await db.execute(
                    "SELECT user_a,user_b,status FROM pets WHERE id=?",
                    (listing["seller_pet_id"],),
                )
            ).fetchone()
            if seller is None or seller["status"] != "active":
                return "Питомец продавца больше недоступен"
            if user_id in (seller["user_a"],seller["user_b"]):
                return "Нельзя покупать товары питомца, которым вы владеете"
            day_start = datetime.fromtimestamp(now, tz).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
            next_start = (
                datetime.fromtimestamp(now, tz).date()
                + __import__("datetime").timedelta(days=1)
            )
            day_end = datetime.combine(next_start,datetime.min.time(),tzinfo=tz).timestamp()
            bought = await (
                await db.execute(
                    "SELECT COUNT(*) FROM pet_market_listings "
                    "WHERE buyer_pet_id=? AND state='sold' "
                    "AND settled_ts>=? AND settled_ts<?",
                    (pet_id, day_start, day_end),
                )
            ).fetchone()
            if bought[0] >= MAX_BUYS_PER_PET_DAY:
                return "Сегодня уже куплено 8 объявлений. Приходите завтра"
            price = int(listing["total_price"])
            paid = await db.execute(
                "UPDATE users SET balance=balance-? "
                "WHERE chat_id=? AND user_id=? AND balance>=?",
                (price, chat_id, user_id, price),
            )
            if paid.rowcount != 1:
                return "Недостаточно ирисок на личном балансе"
            settled = await db.execute(
                "UPDATE pet_market_listings SET state='sold',buyer_pet_id=?,"
                "buyer_user_id=?,settled_ts=? "
                "WHERE id=? AND state='active' AND expires_ts>?",
                (pet_id, user_id, now, listing_id, now),
            )
            if settled.rowcount != 1:
                # All writes would otherwise be committed at the end of the
                # transaction; this can only happen if inconsistent timestamps.
                raise RuntimeError("Объявление изменилось во время оплаты")
            await self._market_adjust_stock(
                db, pet_id, listing["item_kind"], listing["item_code"],
                int(listing["quantity"]),
            )
            await db.execute(
                "INSERT OR IGNORE INTO users(chat_id,user_id) VALUES(?,?)",
                (chat_id, listing["seller_user_id"]),
            )
            await db.execute(
                "UPDATE users SET balance=balance+? WHERE chat_id=? AND user_id=?",
                (price, chat_id, listing["seller_user_id"]),
            )
            await db.execute(
                "INSERT INTO ledger(chat_id,user_id,amount,reason) "
                "VALUES(?,?,?,?)",
                (chat_id, user_id, -price, f"рынок PETS: покупка #{listing_id}"),
            )
            await db.execute(
                "INSERT INTO ledger(chat_id,user_id,amount,reason) "
                "VALUES(?,?,?,?)",
                (chat_id, listing["seller_user_id"], price,
                 f"рынок PETS: продажа #{listing_id}"),
            )
            info = item_details(listing["item_kind"], listing["item_code"])
            return (
                f"✅ Куплено {info['icon']} {listing['quantity']} шт. "
                f"за {price} 🍬. Товар в рюкзаке общего питомца!"
            )
