import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agy import AgentResult
from bot import OffsetStore, TelegramAgyBot
from config import Settings


class DummyAPI:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, thread_id=None):
        self.sent.append((chat_id, text, thread_id))


def make_update(text, user=101, chat=202, thread=None):
    message = {"from": {"id": user}, "chat": {"id": chat}, "text": text}
    if thread is not None:
        message["message_thread_id"] = thread
    return {"update_id": 9, "message": message}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        settings = Settings("fake-token-UNIT-TEST", frozenset({(101, 202)}),
                            "agy", root, 30, root / "private.json")
        self.api = DummyAPI()
        self.bot = TelegramAgyBot(settings, self.api)

    def test_unauthorized_user_and_chat_silently_ignored(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(make_update("hello", user=999))
            self.bot.handle_update(make_update("hello", chat=999))
            runner.assert_not_called()
        self.assertEqual(self.api.sent, [])

    def test_help_and_unknown_command_not_sent_to_agy(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(make_update("/help"))
            self.bot.handle_update(make_update("/unknown"))
            runner.assert_not_called()
        self.assertEqual(len(self.api.sent), 2)

    def test_one_real_prompt_routes_to_runner(self):
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.return_value = AgentResult(True, "model reply")
            self.bot.handle_update(make_update("hello"))
            worker = self.bot._worker
            if worker is not None:
                worker.join(2)
            factory.return_value.run.assert_called_once_with("hello")
            self.assertIn((202, "model reply", None), self.api.sent)

    def test_thread_context_is_preserved(self):
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.return_value = AgentResult(True, "reply")
            self.bot.handle_update(make_update("hello", thread=303))
            worker = self.bot._worker
            if worker is not None:
                worker.join(2)
            self.assertIn((202, "reply", 303), self.api.sent)

    def test_busy_and_cancel(self):
        entered, finish = threading.Event(), threading.Event()
        def slow_run(_text):
            entered.set()
            finish.wait(3)
            return AgentResult(False, "cancelled")
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.side_effect = slow_run
            factory.return_value.cancel.return_value = True
            self.bot.handle_update(make_update("first"))
            self.assertTrue(entered.wait(2))
            self.bot.handle_update(make_update("second"))
            self.bot.handle_update(make_update("/cancel"))
            factory.return_value.run.assert_called_once()
            factory.return_value.cancel.assert_called_once()
            self.assertTrue(any("busy" in m[1] for m in self.api.sent))
            finish.set()
            worker = self.bot._worker
            if worker is not None:
                worker.join(2)

    def test_long_prompt_rejected(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(make_update("x" * 4001))
            runner.assert_not_called()
        self.assertTrue(any("exceeds" in m[1] for m in self.api.sent))


class OffsetStoreTests(unittest.TestCase):
    def test_atomic_offset_storage(self):
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / "offset.json"
            store = OffsetStore(file)
            self.assertIsNone(store.read())
            store.write(42)
            self.assertEqual(store.read(), 42)
            self.assertEqual(os.stat(file).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
