"""🐣 Общий питомец: логика без Telegram и базы.

Механика как у «огонька» в TikTok, только вместо огонька — питомец:
двое заводят общее яйцо и каждый день пишут друг другу в чате (ответом
на сообщение или упоминанием). День засчитывается, когда за сутки
написали ОБА. Дни подряд — это серия: питомец вылупляется, растёт и
меняет облик. Пропустили день — питомец сбегает, серия сгорает, но её
можно вернуть за ириски в течение PET_RESTORE_HOURS.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Species:
    code: str
    emoji: str
    title: str


SPECIES: tuple[Species, ...] = (
    Species("cat", "🐱", "Котик"),
    Species("dog", "🐶", "Пёсик"),
    Species("fox", "🦊", "Лисёнок"),
    Species("panda", "🐼", "Панда"),
    Species("bunny", "🐰", "Зайка"),
    Species("hamster", "🐹", "Хомяк"),
    Species("frog", "🐸", "Лягушонок"),
    Species("penguin", "🐧", "Пингвин"),
    Species("unicorn", "🦄", "Единорог"),
    Species("dragon", "🐲", "Дракончик"),
)
_BY_CODE = {s.code: s for s in SPECIES}


def random_species() -> Species:
    return secrets.choice(SPECIES)


def species_by_code(code: str | None) -> Species:
    return _BY_CODE.get(code or "", SPECIES[0])


@dataclass(frozen=True)
class Stage:
    min_streak: int
    title: str
    badge: str


# Стадии роста по длине серии (дней подряд).
STAGES: tuple[Stage, ...] = (
    Stage(1, "Малыш", "🍼"),
    Stage(7, "Подросток", "🎒"),
    Stage(30, "Взрослый", "💪"),
    Stage(100, "Легенда", "👑"),
)


def stage_for(streak: int) -> Stage | None:
    """Стадия по серии; None — ещё яйцо (серия 0)."""
    current = None
    for stage in STAGES:
        if streak >= stage.min_streak:
            current = stage
    return current


def next_stage(streak: int) -> Stage | None:
    for stage in STAGES:
        if streak < stage.min_streak:
            return stage
    return None


def parse_rewards(raw: str) -> dict[int, int]:
    """«3=2,7=5,30=20» -> {3: 2, 7: 5, 30: 20}. Мусор молча пропускается."""
    rewards: dict[int, int] = {}
    for part in (raw or "").replace(";", ",").split(","):
        m = re.fullmatch(r"\s*(\d+)\s*=\s*(\d+)\s*", part)
        if not m:
            continue
        day, amount = int(m.group(1)), int(m.group(2))
        if day > 0 and amount > 0:
            rewards[day] = amount
    return rewards


NAME_MAX = 20


def clean_name(raw: str) -> str:
    """Имя питомца: одна строка, без лишних пробелов, не длиннее NAME_MAX."""
    name = " ".join((raw or "").split())
    return name[:NAME_MAX]


def pet_name(row) -> str:
    """Имя для показа (HTML-safe): своё имя или название вида."""
    name = (row["name"] or "").strip()
    return escape(name) if name else species_by_code(row["species"]).title


def pet_face(row) -> str:
    """Эмодзи питомца с учётом стадии: яйцо, пока не вылупился."""
    if int(row["best_streak"] or 0) <= 0:
        return "🥚"
    return species_by_code(row["species"]).emoji


def pet_title(row) -> str:
    """«🦊 Лисёнок» / «🥚 Яйцо» — заголовок питомца."""
    if int(row["best_streak"] or 0) <= 0:
        return "🥚 Яйцо"
    return f"{pet_face(row)} {pet_name(row)}"


def stage_line(streak: int) -> str:
    stage = stage_for(streak)
    if stage is None:
        return "🥚 Яйцо"
    return f"{stage.badge} {stage.title}"
