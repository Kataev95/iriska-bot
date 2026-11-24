"""PETS WORLD 1.9 — privacy-scoped studio and permanent adventure album.

Style changes do not create/consume irиски, XP or inventory. Existing
gameplay events provide the only unlocking metrics and album entries.
"""
from datetime import datetime

from pet_business import BUSINESSES
from pet_chapter import CHAPTER_CODE, CHAPTER_ROUTES, CHAPTER_TITLE
from pet_game import ROUTES
from pet_home import ROOMS
from pet_inventory import MATERIALS, RECIPES
from pet_season import CHALLENGES
from pet_season_db import PetSeasonDatabase
from pet_studio import FRAMES, STICKERS, style_entries


class PetStudioDatabase(PetSeasonDatabase):
    async def _migrate_studio(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_studio (
                pet_id INTEGER PRIMARY KEY,
                frame TEXT NOT NULL DEFAULT 'classic',
                sticker TEXT NOT NULL DEFAULT 'paw',
                updated_at REAL NOT NULL DEFAULT 0
            );
        """)

    async def _chapter_row(self, db, pet_id):
        return await (await db.execute(
            "SELECT c.*,t.route,t.user_id AS started_by,t.claimed_at "
            "FROM pet_chapters c JOIN pet_trips t ON t.id=c.trip_id "
            "WHERE c.pet_id=? AND c.chapter=?", (pet_id, CHAPTER_CODE),
        )).fetchone()

    async def chapter_state(self, pet_id):
        """Caller has verified ownership of the active pet."""
        row = await self._chapter_row(self._require(), pet_id)
        stage = 0
        if row:
            stage = (3 if row["finished_at"] is not None else
                     2 if row["crafted_at"] is not None else
                     1 if row["claimed_at"] is not None else 0)
        return {
            "code": CHAPTER_CODE, "title": CHAPTER_TITLE, "stage": stage,
            "trip_id": row["trip_id"] if row else None,
            "ingredient": CHAPTER_ROUTES[row["route"]] if row else None,
            "shared": bool(row and row["shared"]),
        }

    async def advance_chapter(self, pet_id, user_id, chapter, step, now, tz):
        if chapter != CHAPTER_CODE or step not in ("craft", "finish"):
            return "Глава или действие недоступны"
        async with self._game_transaction() as db:
            if await self._game_pet(db, pet_id, user_id) is None:
                return "Питомец недоступен"
            row = await self._chapter_row(db, pet_id)
            if row is None or row["claimed_at"] is None:
                return "Сначала заберите материалы из похода главы"
            if step == "craft":
                if row["crafted_at"] is not None:
                    return "Указатель уже готов"
                material = CHAPTER_ROUTES[row["route"]]
                spent = await db.execute(
                    "UPDATE pet_materials SET quantity=quantity-1 "
                    "WHERE pet_id=? AND material=? AND quantity>=1", (pet_id, material),
                )
                if spent.rowcount != 1:
                    return f"Нужен материал из общего рюкзака: {MATERIALS[material].title}"
                await db.execute(
                    "UPDATE pet_chapters SET crafted_at=?,crafted_by=? "
                    "WHERE pet_id=? AND chapter=?", (now, user_id, pet_id, chapter),
                )
                await self._record_chapter_activity(db, pet_id, user_id, now, tz)
                return "✅ Указатель готов! Теперь его можно установить"
            if row["crafted_at"] is None:
                return "Сначала изготовьте указатель"
            if row["finished_at"] is not None:
                return "Глава уже завершена"
            shared = len({row["started_by"], row["claimed_by"], row["crafted_by"], user_id}) > 1
            await db.execute(
                "UPDATE pet_chapters SET finished_at=?,finished_by=?,shared=? "
                "WHERE pet_id=? AND chapter=?", (now, user_id, shared, pet_id, chapter),
            )
            await self._record_chapter_activity(db, pet_id, user_id, now, tz)
            return "✅ Тропа открыта! Рамка и запись ждут вас в студии"

    async def _record_chapter_activity(self, db, pet_id, user_id, now, tz):
        day = datetime.fromtimestamp(now, tz).date().isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO pet_daily_activity VALUES(?,?,?,'chapter')",
            (pet_id, day, user_id),
        )

    async def chapter_stats(self, chat_id, today):
        """Aggregate local-calendar return rates for people who launched a chapter trip."""
        return await (await self._require().execute(
            """
            WITH chapters AS (
                SELECT c.*,t.user_id,t.day,t.claimed_at
                FROM pet_chapters c JOIN pet_trips t ON t.id=c.trip_id
                JOIN pets p ON p.id=c.pet_id WHERE p.chat_id=? AND c.chapter=?
            ), cohort AS (
                SELECT user_id,MIN(day) AS day FROM chapters GROUP BY user_id
            ), activity AS (
                SELECT a.user_id,a.day FROM pet_daily_activity a
                JOIN pets p ON p.id=a.pet_id WHERE p.chat_id=?
                UNION
                SELECT t.user_id,t.day FROM pet_trips t
                JOIN pets p ON p.id=t.pet_id WHERE p.chat_id=?
            ), returns AS (
                SELECT c.*,
                    EXISTS(SELECT 1 FROM activity a WHERE a.user_id=c.user_id
                           AND a.day=DATE(c.day,'+1 day')) AS d1,
                    EXISTS(SELECT 1 FROM activity a WHERE a.user_id=c.user_id
                           AND a.day=DATE(c.day,'+7 day')) AS d7
                FROM cohort c
            )
            SELECT
                (SELECT COUNT(*) FROM chapters) AS started,
                (SELECT COUNT(claimed_at) FROM chapters) AS first_step,
                (SELECT COUNT(finished_at) FROM chapters) AS finished,
                (SELECT COUNT(*) FROM chapters WHERE shared=1) AS shared,
                COUNT(CASE WHEN DATE(day,'+1 day')<? THEN 1 END) AS d1_total,
                COUNT(CASE WHEN DATE(day,'+1 day')<? AND d1 THEN 1 END) AS d1_returned,
                COUNT(CASE WHEN DATE(day,'+7 day')<? THEN 1 END) AS d7_total,
                COUNT(CASE WHEN DATE(day,'+7 day')<? AND d7 THEN 1 END) AS d7_returned
            FROM returns
            """, (chat_id, CHAPTER_CODE, chat_id, chat_id, today, today, today, today),
        )).fetchone()

    async def reserve_chapter_announcement(self, chat_id):
        async with self._game_transaction() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO pet_chapter_announcements VALUES(?,?)",
                (chat_id, CHAPTER_CODE),
            )
            return cursor.rowcount == 1

    async def release_chapter_announcement(self, chat_id):
        async with self._game_transaction() as db:
            await db.execute(
                "DELETE FROM pet_chapter_announcements WHERE chat_id=? AND chapter=?",
                (chat_id, CHAPTER_CODE),
            )

    async def studio_equipped(self, pet_id: int) -> dict:
        """Always return safe, known CSS variants, including on old databases."""
        row = await (await self._require().execute(
            "SELECT frame,sticker FROM pet_studio WHERE pet_id=?", (pet_id,)
        )).fetchone()
        frame = row["frame"] if row and row["frame"] in FRAMES else "classic"
        sticker = row["sticker"] if row and row["sticker"] in STICKERS else "paw"
        return {"frame": frame, "sticker": sticker,
                "sticker_icon": STICKERS[sticker].icon}

    async def _studio_metrics(self, pet) -> dict[str, int]:
        pet_id = int(pet["id"])
        metrics = await self._badge_metrics(pet)
        row = await (await self._require().execute(
            """
            SELECT
                (SELECT COUNT(*) FROM (
                    SELECT day FROM pet_daily_activity
                    WHERE pet_id=? AND kind='care' AND user_id IN (?,?)
                    GROUP BY day HAVING COUNT(DISTINCT user_id)=2
                )) AS days_both,
                (SELECT COALESCE(SUM(d.quantity),0)
                    FROM pet_material_drops d
                    JOIN pet_trips t ON t.id=d.trip_id
                    WHERE t.pet_id=? AND t.claimed_at IS NOT NULL
                      AND d.material='crystal') AS crystals,
                (SELECT COUNT(*) FROM pet_season_claims
                    WHERE pet_id=?) AS titles,
                (SELECT COUNT(*) FROM pet_chapters
                    WHERE pet_id=? AND chapter=? AND finished_at IS NOT NULL) AS forest_trail
            """,
            (pet_id, int(pet["user_a"]), int(pet["user_b"]),
             pet_id, pet_id, pet_id, CHAPTER_CODE),
        )).fetchone()
        metrics.update({name: int(row[name])
                        for name in ("days_both", "crystals", "titles", "forest_trail")})
        return metrics

    async def _studio_album(self, pet_id: int, tz) -> list[dict]:
        """The permanent chapter page and 24 recent milestones of this pet.

        Names and event descriptions come from fixed server-side catalogues.
        Care chatter and private owner identifiers never enter the album.
        """
        rows = await (await self._require().execute(
            """
            SELECT created_ts AS ts,'origin' AS kind,'' AS code,0 AS tier
            FROM pets WHERE id=? AND status='active'
            UNION ALL
            SELECT claimed_at,'trip',route,0
            FROM pet_trips WHERE pet_id=? AND claimed_at IS NOT NULL
            UNION ALL
            SELECT crafted_at,'craft',recipe,0
            FROM pet_crafts WHERE pet_id=?
            UNION ALL
            SELECT created_at,'room',room,level
            FROM pet_house_upgrades WHERE pet_id=?
            UNION ALL
            SELECT created_at,'business',business,level
            FROM pet_business_upgrades WHERE pet_id=?
            UNION ALL
            SELECT claimed_at,'title',challenge,0
            FROM pet_season_claims WHERE pet_id=?
            ORDER BY ts DESC LIMIT 24
            """,
            (pet_id,) * 6,
        )).fetchall()
        chapter = await self._chapter_row(self._require(), pet_id)
        if chapter and chapter["finished_at"] is not None:
            rows.insert(0, {
                "ts": chapter["finished_at"], "kind": "chapter",
                "code": CHAPTER_CODE, "tier": chapter["shared"],
            })
        events = []
        for row in rows:
            kind, code = row["kind"], row["code"]
            if kind == "origin":
                icon, title = "🥚", "Началась ваша общая история"
            elif kind == "chapter":
                icon = "💞" if row["tier"] else "🍂"
                title = "Восстановили тропу вдвоём" if row["tier"] else "Восстановлена лесная тропа"
            elif kind == "trip":
                rule = ROUTES.get(code)
                if rule is None:
                    continue
                icon, title = "🧭", "Пройден маршрут: " + rule.title
            elif kind == "craft":
                rule = RECIPES.get(code)
                if rule is None:
                    continue
                icon, title = rule.icon, "Создан сувенир: " + rule.title
            elif kind == "room":
                rule = ROOMS.get(code)
                if rule is None:
                    continue
                icon, title = rule.icon, f"Комната «{rule.title}»: уровень {row['tier']}"
            elif kind == "business":
                rule = BUSINESSES.get(code)
                if rule is None:
                    continue
                icon, title = rule.icon, f"Бизнес «{rule.title}»: уровень {row['tier']}"
            elif kind == "title":
                rule = CHALLENGES.get(code)
                if rule is None:
                    continue
                icon, title = rule.icon, "Награда сезона: " + rule.reward
            else:
                continue
            events.append({
                "kind": kind, "icon": icon, "title": title,
                "day": datetime.fromtimestamp(float(row["ts"]), tz)
                        .strftime("%d.%m.%Y"),
            })
        return events

    async def studio_state(self, pet_id: int, user_id: int, tz):
        pet = await self._game_pet(self._require(), pet_id, user_id)
        if pet is None:
            return None
        equipped = await self.studio_equipped(pet_id)
        metrics = await self._studio_metrics(pet)
        return {
            "equipped": equipped,
            "frames": style_entries(FRAMES, metrics, equipped["frame"]),
            "stickers": style_entries(STICKERS, metrics, equipped["sticker"]),
            "metrics": metrics,
            "album": await self._studio_album(pet_id, tz),
        }

    async def equip_studio_style(self, pet_id: int, user_id: int,
                                 slot: str, code: str, now: float):
        catalog = {"frame": FRAMES, "sticker": STICKERS}.get(slot)
        if catalog is None or code not in catalog:
            return "⚠️ Неизвестное украшение"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "⚠️ Питомец недоступен"
            metrics = await self._studio_metrics(pet)
            rule = catalog[code]
            if int(metrics.get(rule.metric, 0)) < rule.goal:
                return "🔒 Украшение ещё не открыто. Выполните условие."
            await db.execute(
                """
                INSERT INTO pet_studio(pet_id,frame,sticker,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(pet_id) DO UPDATE SET
                    frame=CASE WHEN ?='frame' THEN excluded.frame
                               ELSE pet_studio.frame END,
                    sticker=CASE WHEN ?='sticker' THEN excluded.sticker
                                 ELSE pet_studio.sticker END,
                    updated_at=excluded.updated_at
                """,
                (pet_id, code if slot == "frame" else "classic",
                 code if slot == "sticker" else "paw", now, slot, slot),
            )
            return "✅ Новый стиль питомца сохранён!"
