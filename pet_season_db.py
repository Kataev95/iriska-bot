"""PETS WORLD 1.8 — server-side seasons, claims and cosmetic titles.

All rewards are collectible titles shared by two owners. No wallet or item
writes. Claim and equip mutations use the existing BEGIN IMMEDIATE lock.
"""
from pet_league_db import PetLeagueDatabase
from pet_season import CHALLENGES, challenge_entries, season_bounds, title_label


class PetSeasonDatabase(PetLeagueDatabase):
    async def _migrate_seasons(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_season_claims (
                pet_id INTEGER NOT NULL,
                season_start TEXT NOT NULL,
                challenge TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                claimed_by INTEGER NOT NULL,
                PRIMARY KEY(pet_id, season_start, challenge)
            );
            CREATE INDEX IF NOT EXISTS idx_pet_season_claims_pet
                ON pet_season_claims(pet_id,claimed_at DESC);
            CREATE TABLE IF NOT EXISTS pet_season_equipped (
                pet_id INTEGER PRIMARY KEY,
                season_start TEXT NOT NULL,
                challenge TEXT NOT NULL
            );
        """)

    async def _season_metrics(self, pet_id, begin, end, start_day, end_day, score):
        row = await (await self._require().execute(
            """
            SELECT
                (SELECT COUNT(*) FROM pet_trips
                 WHERE pet_id=? AND claimed_at>=? AND claimed_at<?) AS trips,
                (SELECT COUNT(*) FROM pet_daily_claims
                 WHERE pet_id=? AND day>=? AND day<?) AS dailies,
                (SELECT COUNT(*) FROM pet_crafts
                 WHERE pet_id=? AND crafted_at>=? AND crafted_at<?) AS crafts,
                (SELECT COUNT(*) FROM pet_business_jobs
                 WHERE pet_id=? AND collected_at>=? AND collected_at<?) AS batches
            """,
            (pet_id, begin, end, pet_id, start_day, end_day,
             pet_id, begin, end, pet_id, begin, end),
        )).fetchone()
        return {**{key: int(row[key]) for key in
                   ("trips", "dailies", "crafts", "batches")},
                "score": int(score)}

    async def _season_titles(self, pet_id):
        rows = await (await self._require().execute(
            """
            SELECT c.season_start,c.challenge,c.claimed_at,
                   CASE WHEN e.season_start=c.season_start
                             AND e.challenge=c.challenge THEN 1 ELSE 0 END AS equipped
            FROM pet_season_claims c
            LEFT JOIN pet_season_equipped e ON e.pet_id=c.pet_id
            WHERE c.pet_id=?
            ORDER BY c.claimed_at DESC,c.season_start DESC,c.challenge
            """,
            (pet_id,),
        )).fetchall()
        return [{
            "season_start": r["season_start"], "code": r["challenge"],
            "title": title_label(r["challenge"], r["season_start"]),
            "equipped": bool(r["equipped"]),
        } for r in rows if r["challenge"] in CHALLENGES]

    async def season_equipped_title(self, pet_id):
        row = await (await self._require().execute(
            """
            SELECT e.season_start,e.challenge
            FROM pet_season_equipped e JOIN pet_season_claims c
            ON c.pet_id=e.pet_id AND c.season_start=e.season_start
               AND c.challenge=e.challenge
            WHERE e.pet_id=?
            """, (pet_id,),
        )).fetchone()
        if row is None or row["challenge"] not in CHALLENGES:
            return ""
        return title_label(row["challenge"], row["season_start"])

    async def season_state(self, pet_id: int, user_id: int, now: float, tz):
        pet = await self._game_pet(self._require(), pet_id, user_id)
        if pet is None:
            return None
        begin, end, start_day, end_day, season_no = season_bounds(now, tz)
        board = await self._league_scores(
            int(pet["chat_id"]), begin, end, start_day, end_day
        )
        mine = next((row for row in board if row["pet_id"] == pet_id), None)
        metrics = await self._season_metrics(
            pet_id, begin, end, start_day, end_day,
            mine["score"] if mine else 0
        )
        claimed_rows = await (await self._require().execute(
            "SELECT challenge FROM pet_season_claims "
            "WHERE pet_id=? AND season_start=?",
            (pet_id, start_day),
        )).fetchall()
        claimed = {r["challenge"] for r in claimed_rows}
        titles = await self._season_titles(pet_id)
        return {
            "season": {
                "number": season_no, "start": start_day, "end": end_day,
                "seconds_left": max(0, int(end - now)),
            },
            "server_time": now,
            "my_score": int(mine["score"]) if mine else 0,
            "my_rank": int(mine["rank"]) if mine else None,
            "participants": len(board),
            "top": board[:10],
            "challenges": challenge_entries(metrics, claimed),
            "claimed_count": len(claimed & CHALLENGES.keys()),
            "titles": titles,
            "equipped_title": next(
                (r["title"] for r in titles if r["equipped"]), ""
            ),
        }

    async def claim_season_challenge(self, pet_id, user_id, code, now, tz):
        if code not in CHALLENGES:
            return "⚠️ Такого испытания нет"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "⚠️ Питомец недоступен"
            begin, end, start_day, end_day, _ = season_bounds(now, tz)
            if not begin <= now < end:
                return "⏳ Сезон ещё не начался"
            row = await (await db.execute(
                "SELECT 1 FROM pet_season_claims "
                "WHERE pet_id=? AND season_start=? AND challenge=?",
                (pet_id, start_day, code),
            )).fetchone()
            if row:
                return "✅ Награда уже получена. Она в коллекции!"
            board = await self._league_scores(
                int(pet["chat_id"]), begin, end, start_day, end_day
            )
            mine = next((item for item in board if item["pet_id"] == pet_id), None)
            metrics = await self._season_metrics(
                pet_id, begin, end, start_day, end_day,
                mine["score"] if mine else 0,
            )
            rule = CHALLENGES[code]
            if metrics[rule.metric] < rule.goal:
                return "🔒 Испытание ещё не завершено"
            await db.execute(
                "INSERT INTO pet_season_claims"
                "(pet_id,season_start,challenge,claimed_at,claimed_by)"
                " VALUES(?,?,?,?,?)",
                (pet_id, start_day, code, now, user_id),
            )
            return "✅ Получен титул: " + title_label(code, start_day)

    async def equip_season_title(self, pet_id, user_id, code, start_day):
        if code and code not in CHALLENGES:
            return "⚠️ Неизвестный титул"
        async with self._game_transaction() as db:
            pet = await self._game_pet(db, pet_id, user_id)
            if pet is None:
                return "⚠️ Питомец недоступен"
            if not code:
                await db.execute(
                    "DELETE FROM pet_season_equipped WHERE pet_id=?", (pet_id,)
                )
                return "✅ Титул снят"
            exists = await (await db.execute(
                "SELECT 1 FROM pet_season_claims WHERE "
                "pet_id=? AND season_start=? AND challenge=?",
                (pet_id, start_day, code),
            )).fetchone()
            if exists is None:
                return "🔒 Сначала получите этот титул"
            await db.execute(
                """
                INSERT INTO pet_season_equipped(pet_id,season_start,challenge)
                VALUES(?,?,?)
                ON CONFLICT(pet_id) DO UPDATE SET
                season_start=excluded.season_start,
                challenge=excluded.challenge
                """,
                (pet_id, start_day, code),
            )
            return "✅ Установлен титул: " + title_label(code, start_day)
