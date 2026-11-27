"""Long-term PETS progression. Legacy XP and professions remain intact."""
from pet_game import MAX_LEVEL, level_for, xp_for_level

PROFESSION_LEVELS = {"explorer":5,"artisan":5,"merchant":5,"guardian":5,"gardener":5,"engineer":5}
STAGES = (
    (1,"Малыш","🍼"),(5,"Подросток","🎒"),(10,"Взрослый","💪"),
    (20,"Легенда","👑"),(35,"Герой долины","🛡"),(50,"Мастер мира","✨"),
    (75,"Хранитель звёзд","🌠"),(100,"Бессмертная легенда","🏆"),
)
# One skill point each five levels. All skills cost one point per rank.
SKILLS = {
    "trail": {"name":"Следопыт","icon":"🧭","branch":"adventure","level":5,"max":5,"effect":"−1 энергия за поход за ранг"},
    "wisdom": {"name":"Мудрость","icon":"📚","branch":"adventure","level":20,"max":5,"requires":"trail","rank":2,"effect":"+3% базового XP за поход за ранг"},
    "care": {"name":"Забота","icon":"💗","branch":"care","level":5,"max":5,"effect":"+2 сытости при кормлении за ранг"},
    "rest": {"name":"Восстановление","icon":"🌙","branch":"care","level":20,"max":5,"requires":"care","rank":2,"effect":"+2 энергии при сне за ранг"},
    "craft": {"name":"Механик","icon":"⚙️","branch":"craft","level":10,"max":5,"effect":"−2% времени новой партии за ранг"},
    "garden": {"name":"Зелёный палец","icon":"🌱","branch":"craft","level":25,"max":5,"requires":"craft","rank":2,"effect":"+2 п.п. ускорения полива за ранг"},
}
MILESTONES = {
    10: {"wood":3,"leaf":3}, 20: {"stone":3,"wood":5},
    30: {"wood":6,"stone":4}, 40: {"stone":5,"crystal":1},
    50: {"wood":8,"crystal":2}, 60: {"stone":8,"crystal":2},
    70: {"wood":10,"stone":8}, 80: {"crystal":3,"stone":8},
    90: {"crystal":4,"wood":10}, 100: {"crystal":5,"stone":10},
}
# A specialization is independent of the existing profession. One permanent choice.
SPECIALIZATIONS = {
    "wanderer":{"name":"Странник","icon":"🧭","effect":"+5% базового XP за поход"},
    "healer":{"name":"Целитель","icon":"💚","effect":"+5 энергии при сне"},
    "builder":{"name":"Созидатель","icon":"🛠","effect":"−5% времени новых партий"},
}
SPECIALIZATION_LEVEL=30

def skill_points(level):
    return min(MAX_LEVEL,max(0,level))//5

def evolution(pet):
    if not pet["best_streak"]:
        return {"level":0,"name":"Яйцо","icon":"🥚"}
    # Legacy portraits can already represent a higher visual stage by best streak.
    from pet_game import growth_days
    days=growth_days(pet)
    level=max(level_for(pet["xp"]),20 if days>=100 else 10 if days>=30 else 5 if days>=7 else 1)
    stage=max((x for x in STAGES if x[0]<=level),key=lambda x:x[0])
    return {"level":stage[0],"name":stage[1],"icon":stage[2]}

def effects(pet,skills,specialization):
    profession=pet["profession"]
    return {
        "trip_energy":(3 if profession=="artisan" else 0)+skills.get("trail",0),
        "trip_xp_percent":100+(20 if profession=="explorer" else 0)+3*skills.get("wisdom",0)+(5 if specialization=="wanderer" else 0),
        "trip_coins":1 if profession=="merchant" else 0,
        "feed":(5 if profession=="guardian" else 0)+2*skills.get("care",0),
        "sleep":2*skills.get("rest",0)+(5 if specialization=="healer" else 0),
        "production_percent":100-(10 if profession=="engineer" else 0)-2*skills.get("craft",0)-(5 if specialization=="builder" else 0),
        "water_bonus":(10 if profession=="gardener" else 0)+2*skills.get("garden",0),
    }

def progression_config():
    return {"max_level":MAX_LEVEL,"stages":[{"level":lv,"name":name,"icon":icon} for lv,name,icon in STAGES],
            "skills":[{"code":code,**skill} for code,skill in SKILLS.items()],
            "milestones":[{"level":lv,"materials":materials} for lv,materials in MILESTONES.items()],
            "specializations":[{"code":code,**spec} for code,spec in SPECIALIZATIONS.items()],
            "specialization_level":SPECIALIZATION_LEVEL,
            "thresholds":[xp_for_level(i) for i in range(1,MAX_LEVEL+1)]}
