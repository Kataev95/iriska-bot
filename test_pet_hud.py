"""Mobile PETS HUD: menus are short, navigation is logical and purchases need confirmation."""

import unittest
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from handlers.pet_game import game_hud_html, game_keyboard
from handlers.pet_menu import (
    SHOP_GROUPS,
    main_keyboard,
    rich_pet_html,
    rich_pet_photo_html,
    rich_pet_shop_html,
    rich_shop_category_html,
    rich_shop_preview_html,
    shop_category_keyboard,
    shop_items,
    shop_keyboard,
    shop_preview_keyboard,
)
from pet_care import SHOP_BY_CODE


def callbacks(markup):
    return [
        item.callback_data
        for row in markup.inline_keyboard
        for item in row
        if item.callback_data is not None
    ]


def specimen():
    return {
        "id": 9, "species": "dragon", "name": "Барсик",
        "streak": 1, "best_streak": 1, "last_fed_day": None,
        "a_day": None, "b_day": None, "user_a": 111, "user_b": 222,
        "food": 89, "joy": 85, "energy": 85, "xp": 0,
        "profession": "", "freezes": 0, "house": 0,
        "worn": "", "items": "",
    }


class HUDTests(unittest.TestCase):
    def setUp(self):
        self.pet = specimen()
        self.cfg = SimpleNamespace(tz=ZoneInfo("Europe/Moscow"))

    def test_main_is_compact_without_enormous_image_or_table(self):
        html = rich_pet_html(self.pet, {}, self.cfg)
        self.assertIn("Барсик", html)
        self.assertIn("1 день", html)
        self.assertNotIn("1 дней", html)
        self.assertIn("89%", html)
        self.assertNotIn("<figure", html)
        self.assertNotIn("<table", html)
        self.assertIn("📸", rich_pet_photo_html(self.pet, {}))

    def test_profession_is_in_development_not_adventures(self):
        primary = callbacks(main_keyboard(self.pet, self.cfg, False))
        self.assertIn("pg:progress:9", primary)
        self.assertIn("pm:photo:9", primary)
        self.assertNotIn("pg:professions:9", primary)
        adv = callbacks(game_keyboard(self.pet, "menu", 0))
        self.assertNotIn("pg:professions:9", adv)
        growth = callbacks(game_keyboard(self.pet, "progress", 0))
        self.assertIn("pg:professions:9", growth)
        profession = callbacks(game_keyboard(self.pet, "professions", 0))
        self.assertIn("pg:progress:9", profession)

    def test_tasks_and_routes_without_wide_tables(self):
        daily = game_hud_html(
            self.pet, "tasks", "", 0, day="2026-11-13",
            completed={"care": True, "adventure": False, "together": False},
            claimed=set(),
        )
        self.assertIn("📋 Задания", daily)
        self.assertIn("✅", daily)
        self.assertNotIn("<table", daily)
        routes = game_hud_html(self.pet, "menu", "", 0)
        self.assertIn("Парк", routes)
        self.assertIn("Руины", routes)
        self.assertNotIn("<table", routes)

    def test_shop_filters_and_confirms(self):
        self.assertEqual(len(SHOP_GROUPS), 3)
        overview = callbacks(shop_keyboard(self.pet))
        self.assertEqual(len([k for k in overview if ":shopcat:" in k]), 3)
        self.assertNotIn("pm:buy:9:bow", overview)
        food = shop_items("food")
        self.assertEqual({item.code for item in food}, {"cookie", "cake"})
        html = rich_shop_category_html(self.pet, 25, "food")
        self.assertIn("Печенье", html)
        self.assertNotIn("Цилиндр", html)
        self.assertIn("<table", html)
        categories = callbacks(shop_category_keyboard(self.pet, "food"))
        self.assertIn("pm:preview:9:cookie", categories)
        self.assertNotIn("pm:buy:9:cookie", categories)
        item = SHOP_BY_CODE["tophat"]
        self.assertIn("pm:buy:9:tophat", callbacks(shop_preview_keyboard(self.pet, item, 25)))
        self.assertNotIn("pm:buy:9:tophat", callbacks(shop_preview_keyboard(self.pet, item, 0)))
        self.assertIn("10", rich_shop_preview_html(self.pet, 25, item))  # remaining 10 🍬
        self.assertNotIn("<table", rich_pet_shop_html(self.pet, 25))


if __name__ == "__main__":
    unittest.main()
