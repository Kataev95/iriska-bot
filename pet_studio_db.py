"""PETS WORLD 1.9 — privacy-scoped studio and permanent adventure album.

Style changes do not create/consume irиски, XP or inventory. Existing
gameplay events provide the only unlocking metrics and album entries.
"""
from datetime import datetime

from pet_business import BUSINESSES
from pet_game import ROUTES
from pet_home import ROOMS
from pet_inventory import RECIPES
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
                    WHERE pet_id=?) AS titles
            """,
            (pet_id, int(pet["user_a"]), int(pet["user_b"]),
             pet_id, pet_id),
        )).fetchone()
        metrics.update({name: int(row[name])
                        for name in ("days_both", "crystals", "titles")})
        return metrics

    async def _studio_album(self, pet_id: int, tz) -> list[dict]:
        """Latest 24 verifiable milestones of this exact pet, no fake entries.

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
            (pet_id,)*6,
        )).fetchall()
        events = []
        for row in rows:
            kind, code = row["kind"], row["code"]
            if kind == "origin":
                icon, title = "🥚", "Началась ваша общая история"
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
