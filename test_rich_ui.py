"""Rich Message protocol smoke tests without a real Telegram bot token."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiogram.exceptions import TelegramBadRequest
from unittest.mock import patch
from rich_ui import asset_url, edit_rich, edit_rich_by_id, panel_html, rich_table, send_rich
from handlers.pet_menu import send_pet_photo

class FakeBot:
    def __init__(self):
        self.calls = []
    async def __call__(self, method):
        self.calls.append(method)
        return {"ok": True}
    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        return {"legacy": True}

class RichUITests(unittest.IsolatedAsyncioTestCase):
    def test_markup_and_escape(self):
        self.assertIn("<h2>", panel_html("🍬 Hello\n\nWorld"))
        output = rich_table(["Name"], [["<x & y>"]])
        self.assertIn("&lt;x &amp; y&gt;", output)
        self.assertNotIn("<x & y>", output)
        with self.assertRaises(ValueError):
            asset_url("../private")

    async def test_native_send(self):
        bot = FakeBot()
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "1"}):
            await send_rich(bot, -100, "fallback", html="<h2>🐾 HUD</h2>")
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0].__api_method__, "sendRichMessage")
        self.assertEqual(bot.calls[0].rich_message["html"], "<h2>🐾 HUD</h2>")

    async def test_native_edit(self):
        bot = FakeBot()
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "1"}):
            done = await edit_rich_by_id(bot, -100, 123, "fallback",
                                         html="<h2>🐾 Новый HUD</h2>")
        self.assertTrue(done)
        self.assertEqual(bot.calls[0].__api_method__, "editMessageText")

    async def test_legacy_fallback(self):
        bot = FakeBot()
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "0"}):
            await send_rich(bot, -100, "legacy")
        self.assertEqual(bot.calls[0][1], "legacy")


class FailingRichEditBot(FakeBot):
    async def __call__(self, method):
        self.calls.append(method)
        if method.__api_method__ == "editMessageText":
            raise TelegramBadRequest(method=method, message="can't edit this rich media")
        return {"ok": True}


class RejectingPhotoUrlBot(FakeBot):
    async def __call__(self, method):
        self.calls.append(method)
        html = method.rich_message["html"]
        if "raw.githubusercontent.com" in html:
            raise TelegramBadRequest(method=method, message="cannot fetch photo")
        return SimpleNamespace(message_id=100, photo=None)


class PetImageDB:
    async def get_pet_media(self, key):
        return None


class RichRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_refused_edit_does_not_downgrade_rich(self):
        bot = FailingRichEditBot()
        plain_edit = AsyncMock()
        message = SimpleNamespace(
            photo=None, bot=bot, chat=SimpleNamespace(id=-100), message_id=25,
            edit_text=plain_edit,
        )
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "1"}):
            ok = await edit_rich(
                message, "<b>fallback</b>", html="<h2>Rich</h2>",
                image_url=asset_url("dragon_baby"),
            )
        self.assertFalse(ok)
        plain_edit.assert_not_awaited()
        self.assertEqual(len(bot.calls), 1)
        self.assertIn("<img", bot.calls[0].rich_message["html"])

    async def test_photo_url_error_retries_native_rich_media(self):
        bot = RejectingPhotoUrlBot()
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "1"}):
            result = await send_pet_photo(
                bot, PetImageDB(), -100, "dragon_baby", "Legacy",
                rich_html="<h2>🐲 Барсик</h2>", show_image=True,
            )
        self.assertEqual(result.message_id, 100)
        self.assertEqual(len(bot.calls), 2)
        self.assertEqual(bot.calls[1].__api_method__, "sendRichMessage")
        self.assertIn("tg://photo?id=pet", bot.calls[1].rich_message["html"])
        self.assertEqual(
            bot.calls[1].rich_message["media"][0]["media"]["type"], "photo",
        )
        self.assertIn("<h2>", bot.calls[1].rich_message["html"])

    async def test_edit_with_explicit_media_keeps_photo(self):
        bot = FakeBot()
        with patch.dict(os.environ, {"RICH_MESSAGES_ENABLED": "1"}):
            ok = await edit_rich_by_id(
                bot, -100, 25, "text", html="<h2>🐲 Барсик</h2>",
                image_media="cached-file-id",
            )
        self.assertTrue(ok)
        self.assertIn("tg://photo?id=pet", bot.calls[0].rich_message["html"])
        self.assertEqual(
            bot.calls[0].rich_message["media"][0]["media"]["media"],
            "cached-file-id",
        )

if __name__ == "__main__":
    unittest.main()
