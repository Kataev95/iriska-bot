"""Read-only achievements and weekly leaderboard for PETS WORLD 1.7.

Source of truth: existing durable SQLite game records. No new tables, timers,
writable scores, or automatic currency/XP rewards. Works on old databases.
"""
from pet_league import BADGES, WEEKLY_POINTS, badge_entries, week_bounds


class PetLeagueDatabase:
    async def _league_scores(self, chat_id: int, begin: float, end: float,
                             begin_day: str, end_day: str) -> list[dict]:
        """Weekly event log derived from completed, server-authenticated actions.

        All six types are bounded by existing rules (daily quests, daily
        expeditions/production, or one-time upgrades). Never count bought
        inventory, market volume, coin balances or unclaimed progress.
        """
        db = self._require()
        rows = await (
            await db.execute(
                """
                WITH events AS (
                    SELECT pet_id, ? AS points, 1 AS trips
                    FROM pet_trips
                    WHERE claimed_at>=? AND claimed_at<?
                    UNION ALL
                    SELECT pet_id, ? AS points, 0 AS trips
                    FROM pet_daily_claims
                    WHERE day>=? AND day<?
                    UNION ALL
                    SELECT pet_id, ? AS points, 0 AS trips
                    FROM pet_business_jobs
                    WHERE collected_at>=? AND collected_at<?
                    UNION ALL
                    SELECT pet_id, ? AS points, 0 AS trips
                    FROM pet_crafts
                    WHERE crafted_at>=? AND crafted_at<?
                    UNION ALL
                    SELECT pet_id, ? AS points, 0 AS trips
                    FROM pet_house_upgrades
                    WHERE created_at>=? AND created_at<?
                    UNION ALL
                    SELECT pet_id, ? AS points, 0 AS trips
                    FROM pet_business_upgrades
                    WHERE created_at>=? AND created_at<?
                )
                SELECT p.id AS pet_id,
                       COALESCE(NULLIF(trim(p.name), ''),
                                'Питомец #' || p.id) AS pet_name,
                       p.species AS species,
                       SUM(events.points) AS score,
                       SUM(events.trips) AS trips
                FROM pets p
                JOIN events ON events.pet_id=p.id
                WHERE p.chat_id=? AND p.status='active'
                GROUP BY p.id
                ORDER BY score DESC, trips DESC, p.id ASC
                """,
                (
                    WEEKLY_POINTS["trip"], begin, end,
                    WEEKLY_POINTS["daily"], begin_day, end_day,
                    WEEKLY_POINTS["batch"], begin, end,
                    WEEKLY_POINTS["craft"], begin, end,
                    WEEKLY_POINTS["room"], begin, end,
                    WEEKLY_POINTS["business"], begin, end,
                    chat_id,
                ),
            )
        ).fetchall()
        return [
            {
                "rank": rank,
                "pet_id": int(r["pet_id"]),
                "pet_name": r["pet_name"],
                "species": r["species"],
                "score": int(r["score"]),
                "trips": int(r["trips"]),
            }
            for rank, r in enumerate(rows, 1)
        ]

    async def _badge_metrics(self, pet) -> dict[str, int]:
        """Lifetime metrics remain earned even after resources are consumed."""
        pet_id = int(pet["id"])
        row = await (
            await self._require().execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM pet_trips
                     WHERE pet_id=? AND claimed_at IS NOT NULL) AS trips,
                    (SELECT COUNT(*) FROM pet_crafts
                     WHERE pet_id=?) AS crafts,
                    (SELECT COUNT(*) FROM pet_house_upgrades
                     WHERE pet_id=?) AS rooms,
                    (SELECT COUNT(*) FROM pet_business_jobs
                     WHERE pet_id=? AND collected_at IS NOT NULL) AS batches,
                    (SELECT COUNT(*) FROM pet_market_listings
                     WHERE seller_pet_id=? AND state='sold') AS sales,
                    (SELECT COUNT(*) FROM pet_daily_claims
                     WHERE pet_id=?) AS daily
                """,
                (pet_id,)*6,
            )
        ).fetchone()
        return {
            "trips": int(row["trips"]),
            "crafts": int(row["crafts"]),
            "rooms": int(row["rooms"]),
            "batches": int(row["batches"]),
            "sales": int(row["sales"]),
            "daily": int(row["daily"]),
            "xp": int(pet["xp"]),
            "best_streak": int(pet["best_streak"]),
        }

    async def league_state(self, pet_id: int, user_id: int, now: float, tz):
        """Private entrypoint: only the two co-owners may inspect their chat.

        League boards include only active pets of that exact Telegram chat.
        Unranked pets see 0 points and no place; no fake participants.
        """
        pet = await self._game_pet(self._require(), pet_id, user_id)
        if pet is None:
            return None
        week_start, week_end, start_day, end_day = week_bounds(now, tz)
        previous_start, _, prev_day, _ = week_bounds(week_start-1, tz)
        current = await self._league_scores(
            int(pet["chat_id"]), week_start, week_end, start_day, end_day
        )
        previous = await self._league_scores(
            int(pet["chat_id"]), previous_start, week_start,
            prev_day, start_day
        )
        mine = next(
            (row for row in current if row["pet_id"] == pet_id), None
        )
        my_previous = next(
            (row for row in previous if row["pet_id"] == pet_id), None
        )
        metrics = await self._badge_metrics(pet)
        badges = badge_entries(metrics)
        return {
            "week_start": start_day,
            "week_end": end_day,
            "server_time": now,
            "seconds_left": max(0, int(week_end-now)),
            "points": dict(WEEKLY_POINTS),
            "my_pet_id": pet_id,
            "my_score": mine["score"] if mine else 0,
            "my_rank": mine["rank"] if mine else None,
            "my_previous_rank": my_previous["rank"] if my_previous else None,
            "participants": len(current),
            "top": current[:10],
            "previous_top": previous[:3],
            "badges": badges,
            "badges_unlocked": sum(b["unlocked"] for b in badges),
            "metrics": metrics,
        }
