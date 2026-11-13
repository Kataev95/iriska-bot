"""Rich Message protocol smoke tests without a real Telegram bot token."""
import os
import unittest
from unittest.mock import patch
from rich_ui import asset_url, edit_rich_by_id, panel_html, rich_table, send_rich

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

if __name__ == "__main__":
    unittest.main()
