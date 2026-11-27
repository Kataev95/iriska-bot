"""Shared durable progression: transactional skills, specialties and milestone claims."""
from pet_arcade_db import PetArcadeDatabase
from pet_game import level_for
from pet_progression import (
    SKILLS, MILESTONES, SPECIALIZATIONS, SPECIALIZATION_LEVEL,
    skill_points, evolution, effects,
)


class PetProgressionDatabase(PetArcadeDatabase):
    async def _migrate_progression(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_skill_ranks (
                pet_id INTEGER NOT NULL, code TEXT NOT NULL,
                rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 5),
                PRIMARY KEY(pet_id,code)
            );
            CREATE TABLE IF NOT EXISTS pet_specializations (
                pet_id INTEGER PRIMARY KEY, code TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pet_level_claims (
                pet_id INTEGER NOT NULL, level INTEGER NOT NULL,
                user_id INTEGER NOT NULL, claimed_at REAL NOT NULL,
                PRIMARY KEY(pet_id,level)
            );
        """)

    async def _progression_choices(self, db, pet_id):
        rows = await (await db.execute(
            "SELECT code,rank FROM pet_skill_ranks WHERE pet_id=?", (pet_id,)
        )).fetchall()
        spec = await (await db.execute(
            "SELECT code FROM pet_specializations WHERE pet_id=?", (pet_id,)
        )).fetchone()
        return {r["code"]:r["rank"] for r in rows}, spec["code"] if spec else ""

    async def _progression_effects(self, db, pet):
        ranks, spec = await self._progression_choices(db,pet["id"])
        return effects(pet,ranks,spec)

    async def progression_state(self, pet):
        async with self._game_transaction() as db:
            ranks,spec=await self._progression_choices(db,pet["id"])
            rows=await (await db.execute(
                "SELECT level FROM pet_level_claims WHERE pet_id=? ORDER BY level",(pet["id"],)
            )).fetchall()
            level=level_for(pet["xp"])
            return {"skills":ranks,"specialization":spec,
                    "points":max(0,skill_points(level)-sum(ranks.values())),
                    "earned_points":skill_points(level),
                    "claimed":[r["level"] for r in rows],
                    "evolution":evolution(pet),"effects":effects(pet,ranks,spec)}

    async def learn_skill(self, pet_id,user_id,code,expected_rank):
        if code not in SKILLS or type(expected_rank) is not int:
            return "Неизвестный навык"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            level=level_for(pet["xp"])
            ranks,_=await self._progression_choices(db,pet_id)
            rank=ranks.get(code,0)
            rule=SKILLS[code]
            if expected_rank!=rank or rank>=rule["max"]:
                return "Ранг изменился или навык максимальный"
            if level<rule["level"]:
                return "Навык ещё не открыт"
            if rule.get("requires") and ranks.get(rule["requires"],0)<rule["rank"]:
                return "Сначала улучшите предыдущий навык ветки"
            if sum(ranks.values())>=skill_points(level):
                return "Недостаточно очков навыков"
            await db.execute(
                "INSERT INTO pet_skill_ranks VALUES(?,?,?) ON CONFLICT(pet_id,code) DO UPDATE SET rank=excluded.rank",
                (pet_id,code,rank+1)
            )
            return f"✅ {rule['name']} — ранг {rank+1}!"

    async def choose_specialization(self,pet_id,user_id,code):
        if code not in SPECIALIZATIONS:
            return "Неизвестная специализация"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            if level_for(pet["xp"])<SPECIALIZATION_LEVEL:
                return "Специализация откроется на 30-м уровне"
            _,spec=await self._progression_choices(db,pet_id)
            if spec:
                return "Специализация уже выбрана"
            await db.execute("INSERT INTO pet_specializations VALUES(?,?)",(pet_id,code))
            return f"✅ Специализация: {SPECIALIZATIONS[code]['name']}"

    async def claim_level_milestone(self,pet_id,user_id,level,now):
        if type(level) is not int or level not in MILESTONES:
            return "Неизвестная награда"
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None or not pet["best_streak"]:
                return "Питомец недоступен"
            if level_for(pet["xp"])<level:
                return "Уровень ещё не достигнут"
            changed=await db.execute(
                "INSERT OR IGNORE INTO pet_level_claims VALUES(?,?,?,?)",(pet_id,level,user_id,now)
            )
            if changed.rowcount!=1:
                return "Награда уже получена"
            for material,qty in MILESTONES[level].items():
                await db.execute(
                    "INSERT INTO pet_materials(pet_id,material,quantity) VALUES(?,?,?) "
                    "ON CONFLICT(pet_id,material) DO UPDATE SET quantity=quantity+excluded.quantity",
                    (pet_id,material,qty)
                )
            return f"✅ Награда за {level}-й уровень в общем рюкзаке!"
