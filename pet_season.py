"""PETS WORLD 1.8: deterministic four-week seasons and cosmetic rewards.

All challenge progress is derived from the same durable gameplay events as the
weekly league; a browser cannot submit progress, scores or a custom clock.
Season titles have NO iriski, XP, inventory or combat advantage.
"""
from dataclasses import dataclass
from datetime import date, datetime, time as time_of_day, timedelta


SEASON_ANCHOR = date(2026, 11, 9)  # First launch Monday
SEASON_DAYS = 28


@dataclass(frozen=True)
class Challenge:
    icon: str
    title: str
    description: str
    metric: str
    goal: int
    reward: str


CHALLENGES = {
    "explorer": Challenge(
        "🧭", "Тропами приключений", "Заберите награды за 3 похода",
        "trips", 3, "🗺️ Следопыт",
    ),
    "helper": Challenge(
        "🎯", "Команда мечты", "Заберите 8 ежедневных наград",
        "dailies", 8, "🤝 Надёжный друг",
    ),
    "maker": Challenge(
        "🛠️", "Искра творчества", "Создайте хотя бы 1 сувенир",
        "crafts", 1, "🎨 Творец",
    ),
    "producer": Challenge(
        "⚙️", "Деловая хватка", "Заберите 3 партии товаров из бизнеса",
        "batches", 3, "🏭 Предприниматель",
    ),
    "star": Challenge(
        "🌟", "Герой сезона", "Наберите 160 очков сезонной активности",
        "score", 160, "🏆 Герой сезона",
    ),
}


def season_bounds(now: float, tz):
    """Return [start, end) local dates and Unix timestamps, DST-safe.

    Windows are pinned to the launch Monday, not a restart or server uptime.
    """
    today = datetime.fromtimestamp(now, tz).date()
    index = max(0, (today - SEASON_ANCHOR).days // SEASON_DAYS)
    start_date = SEASON_ANCHOR + timedelta(days=index * SEASON_DAYS)
    end_date = start_date + timedelta(days=SEASON_DAYS)
    begin = datetime.combine(start_date, time_of_day.min, tzinfo=tz).timestamp()
    end = datetime.combine(end_date, time_of_day.min, tzinfo=tz).timestamp()
    return begin, end, start_date.isoformat(), end_date.isoformat(), index + 1


def title_label(challenge_code: str, season_start: str) -> str:
    """A permanent collectible with a visible season stamp."""
    challenge = CHALLENGES[challenge_code]
    try:
        season_no = max(1, (date.fromisoformat(season_start) - SEASON_ANCHOR).days
                        // SEASON_DAYS + 1)
    except ValueError:
        season_no = 1
    return f"{challenge.reward} · S{season_no}"


def challenge_entries(metrics: dict, claimed_codes: set[str]) -> list[dict]:
    result = []
    for code, rule in CHALLENGES.items():
        progress = max(0, int(metrics.get(rule.metric, 0)))
        result.append({
            "code": code,
            "icon": rule.icon,
            "title": rule.title,
            "description": rule.description,
            "progress": min(progress, rule.goal),
            "goal": rule.goal,
            "ready": progress >= rule.goal,
            "claimed": code in claimed_codes,
            "reward": rule.reward,
        })
    return result
