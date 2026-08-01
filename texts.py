"""Тексты и форматирование."""

from __future__ import annotations

from html import escape


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русские формы множественного числа: 1 ириска, 2 ириски, 5 ирисок."""
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def iriski(n: int) -> str:
    return plural(n, "ириска", "ириски", "ирисок")


def msgs(n: int) -> str:
    return plural(n, "сообщение", "сообщения", "сообщений")


def fmt(n: int) -> str:
    """12345 -> '12 345'."""
    return f"{int(n):,}".replace(",", " ")


def display_name(first_name: str | None, username: str | None) -> str:
    name = (first_name or "").strip()
    if not name:
        name = ("@" + username) if username else "Без имени"
    if len(name) > 25:
        name = name[:24] + "…"
    return escape(name)


_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def place(i: int) -> str:
    return _MEDALS.get(i, f"{i}.")


def help_text(per: int, threshold: int, min_len: int, cooldown: float, contact: str) -> str:
    return (
        "🍬 <b>Как заработать ириски</b>\n\n"
        "Ириски — валюта нашего чата. Начисляются за общение: "
        f"<b>1 ириска за каждые {fmt(per)} сообщений</b> "
        f"(то есть {fmt(per * 10)} сообщений = 10 ирисок).\n\n"
        "<b>Что засчитывается:</b>\n"
        f"• только текстовые сообщения от {min_len} символов\n"
        f"• не чаще одного зачёта в {cooldown:g} сек\n"
        "• пересланные сообщения, стикеры, фото без текста и команды не считаются\n"
        "• повтор одного и того же сообщения подряд не считается\n\n"
        f"<b>Вывод:</b> накопил <b>{fmt(threshold)}</b> {iriski(threshold)} — "
        f"пиши {contact} и забирай.\n\n"
        "<b>Команды:</b>\n"
        "/me — моя статистика (или напиши «стата»)\n"
        "/balance — баланс ирисок («баланс»)\n"
        "/bonus — ежедневный бонус («бонус»)\n"
        "/top — топ чата за всё время («топ»)\n"
        "/week — топ за 7 дней («топ недели»)\n"
        "/day — топ за сегодня («топ дня»)\n"
        "/casino 10 — слоты («казино 10»)\n"
        "/games — правила игр («игры»)\n"
        "/withdraw — вывод ирисок («вывод»)\n"
        "/help — эта справка\n\n"
        "🎮 Дуэль: ответь «дуэль 10» на сообщение соперника — монетка решит, "
        "кто заберёт банк."
    )
