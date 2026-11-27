"""Durable, versioned arcade sessions. Rewards are shared pet XP, not iriski."""
import json
from datetime import datetime
from pet_countryside_db import PetCountrysideDatabase
from pet_arcade import (
    GAMES, DAILY_ATTEMPTS, DAILY_REWARDS, REWARD_XP, REWARD_SCORE,
    SESSION_SECONDS, new_game, public_state, advance,
)


class PetArcadeDatabase(PetCountrysideDatabase):
    async def _migrate_arcade(self):
        await self._require().executescript("""
            CREATE TABLE IF NOT EXISTS pet_arcade_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pet_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                day TEXT NOT NULL, game TEXT NOT NULL, expires_at REAL NOT NULL,
                version INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                finished_at REAL, score INTEGER CHECK(score BETWEEN 0 AND 100),
                reward_xp INTEGER NOT NULL DEFAULT 0 CHECK(reward_xp>=0),
                outcome TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_arcade_active
                ON pet_arcade_runs(pet_id) WHERE finished_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_arcade_day ON pet_arcade_runs(pet_id,day,game);
            CREATE INDEX IF NOT EXISTS idx_arcade_best ON pet_arcade_runs(pet_id,game,score);
        """)

    async def _arcade_state(self, db, pet_id, user_id, now, tz):
        day = datetime.fromtimestamp(now,tz).date().isoformat()
        last = await (await db.execute(
            "SELECT * FROM pet_arcade_runs WHERE pet_id=? ORDER BY id DESC LIMIT 1",(pet_id,)
        )).fetchone()
        daily = await (await db.execute(
            "SELECT game,COUNT(*) AS attempts,MAX(score) AS best FROM pet_arcade_runs "
            "WHERE pet_id=? AND day=? GROUP BY game",(pet_id,day)
        )).fetchall()
        best = await (await db.execute(
            "SELECT game,MAX(score) AS best FROM pet_arcade_runs WHERE pet_id=? AND score IS NOT NULL GROUP BY game",(pet_id,)
        )).fetchall()
        # Rewards count on the day they are earned, including sessions started before midnight.
        start = datetime.fromisoformat(day).replace(tzinfo=tz).timestamp()
        rewarded = await (await db.execute(
            "SELECT COUNT(*) FROM pet_arcade_runs WHERE pet_id=? AND reward_xp>0 AND finished_at>=?",
            (pet_id,start)
        )).fetchone()
        records = {code:{"remaining":DAILY_ATTEMPTS,"today":None,"best":None} for code in GAMES}
        for row in daily:
            records[row["game"]].update(remaining=max(0,DAILY_ATTEMPTS-row["attempts"]),today=row["best"])
        for row in best:
            records[row["game"]]["best"] = row["best"]
        active = None
        result = None
        if last:
            if last["finished_at"] is None and now<=last["expires_at"]:
                mine = last["user_id"]==user_id
                active = {"id":last["id"],"game":last["game"],"version":last["version"],
                          "expires_at":last["expires_at"],"mine":mine,
                          "state":public_state(last["game"],json.loads(last["state"]),now) if mine else None}
            elif last["finished_at"] is not None:
                result = {"game":last["game"],"score":last["score"],"xp":last["reward_xp"],
                          "outcome":last["outcome"]}
            else:
                result = {"game":last["game"],"score":None,"xp":0,"outcome":"expired"}
        return {"records":records,"rewards_remaining":max(0,DAILY_REWARDS-rewarded[0]),
                "last_id":last["id"] if last else 0,"active":active,"result":result}

    async def arcade_state(self,pet_id,user_id,now,tz):
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None:return None
            return await self._arcade_state(db,pet_id,user_id,now,tz)

    async def arcade_action(self,pet_id,user_id,action,game,payload,now,tz):
        async with self._game_transaction() as db:
            pet=await self._game_pet(db,pet_id,user_id)
            if pet is None:return None
            async def reply(message):
                return {"message":message,"arcade":await self._arcade_state(db,pet_id,user_id,now,tz)}
            if not pet["best_streak"]:
                return await reply("Дождитесь вылупления питомца")
            last=await (await db.execute(
                "SELECT * FROM pet_arcade_runs WHERE pet_id=? ORDER BY id DESC LIMIT 1",(pet_id,)
            )).fetchone()
            if action=="start":
                expected=payload.get("expected_last")
                if game not in GAMES or type(expected) is not int or expected!=(last["id"] if last else 0):
                    return await reply("Игра изменилась. Обновите экран")
                if last and last["finished_at"] is None and now<=last["expires_at"]:
                    return await reply("Сначала завершите текущую игру")
                day=datetime.fromtimestamp(now,tz).date().isoformat()
                count=await (await db.execute(
                    "SELECT COUNT(*) FROM pet_arcade_runs WHERE pet_id=? AND day=? AND game=?",
                    (pet_id,day,game)
                )).fetchone()
                if count[0]>=DAILY_ATTEMPTS:
                    return await reply("Попытки этой игры на сегодня закончились")
                if last and last["finished_at"] is None:
                    await db.execute("UPDATE pet_arcade_runs SET finished_at=?,outcome='expired' WHERE id=?",(now,last["id"]))
                await db.execute(
                    "INSERT INTO pet_arcade_runs(pet_id,user_id,day,game,expires_at,state) VALUES(?,?,?,?,?,?)",
                    (pet_id,user_id,day,game,now+SESSION_SECONDS,json.dumps(new_game(game,now)))
                )
                return await reply("✅ Игра началась!")
            ident,version=payload.get("run_id"),payload.get("version")
            if (not last or type(ident) is not int or type(version) is not int
                    or ident!=last["id"] or version!=last["version"] or last["finished_at"] is not None):
                return await reply("Игровой ход устарел")
            if last["user_id"]!=user_id:
                return await reply("Эта попытка принадлежит второму владельцу")
            if now>last["expires_at"]:
                await db.execute("UPDATE pet_arcade_runs SET finished_at=?,outcome='expired' WHERE id=?",(now,ident))
                return await reply("Время игры истекло")
            if action=="cancel":
                await db.execute("UPDATE pet_arcade_runs SET finished_at=?,outcome='cancelled' WHERE id=?",(now,ident))
                return await reply("Игра завершена без награды")
            if action!="move":
                return await reply("Неизвестное действие")
            state=json.loads(last["state"])
            message,accepted,finished,score=advance(last["game"],state,payload.get("move"),now)
            if not accepted:return await reply(message)
            if not finished:
                await db.execute("UPDATE pet_arcade_runs SET version=version+1,state=? WHERE id=?",
                                 (json.dumps(state),ident))
                return await reply(message)
            current=await self._arcade_state(db,pet_id,user_id,now,tz)
            reward=REWARD_XP if score>=REWARD_SCORE and current["rewards_remaining"]>0 else 0
            await db.execute(
                "UPDATE pet_arcade_runs SET version=version+1,state=?,finished_at=?,score=?,reward_xp=?,outcome='completed' WHERE id=?",
                (json.dumps(state),now,score,reward,ident)
            )
            if reward:
                await db.execute("UPDATE pets SET xp=xp+? WHERE id=?",(reward,pet_id))
            return await reply(f"✅ Результат: {score}/100"+(f" · +{reward} XP" if reward else ""))
