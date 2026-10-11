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
        self.markups = []
        self.edited = []
        self.answered = []

    def send_text(self, chat_id, text, thread_id=None, reply_markup=None):
        self.sent.append((chat_id, text, thread_id))
        self.markups.append(reply_markup)
        return len(self.sent)

    def edit_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append((chat_id, message_id, text, reply_markup))

    def answer_callback(self, query_id, text=""):
        self.answered.append((query_id, text))


def make_callback(data, *, message_id, user=101, chat=202):
    return {
        "update_id": 11,
        "callback_query": {
            "id": "synthetic-callback",
            "from": {"id": user},
            "message": {"message_id": message_id, "chat": {"id": chat}},
            "data": data,
        },
    }


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
            factory.return_value.run.assert_called_once_with("hello", model=None)
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
        def slow_run(_text, model=None):
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

    def test_inline_stop_button_with_correct_owner_and_message(self):
        entered, finish = threading.Event(), threading.Event()
        def slow_run(_text, model=None):
            entered.set()
            finish.wait(3)
            return AgentResult(False, "cancelled")
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.side_effect = slow_run
            factory.return_value.cancel.return_value = True
            self.bot.handle_update(make_update("long prompt"))
            self.assertTrue(entered.wait(2))
            running = self.api.markups[0]
            self.assertIn("Stop", running["inline_keyboard"][0][0]["text"])
            token = running["inline_keyboard"][0][0]["callback_data"]
            self.bot.handle_update(make_callback(token, message_id=1, user=999))
            factory.return_value.cancel.assert_not_called()
            self.bot.handle_update(make_callback(token, message_id=999))
            factory.return_value.cancel.assert_not_called()
            self.bot.handle_update(make_callback("stop:old", message_id=1))
            factory.return_value.cancel.assert_not_called()
            self.bot.handle_update(make_callback(token, message_id=1))
            factory.return_value.cancel.assert_called_once()
            self.assertIn("Stopping", self.api.answered[-1][1])
            finish.set()
            worker = self.bot._worker
            if worker: worker.join(2)
        self.assertTrue(any(x[3] is None for x in self.api.edited))

    def test_model_picker_and_next_task(self):
        options = [("gemini-3.8-flash-high", "Gemini 3.8 Flash High"),
                   ("claude-sonnet-4-6", "Claude Sonnet")]
        with patch("bot.list_models", return_value=options):
            self.bot.handle_update(make_update("/models"))
        markup = self.api.markups[-1]
        self.assertIn("model:0", str(markup))
        message_id = len(self.api.sent)
        self.bot.handle_update(make_callback("model:0", message_id=message_id,
                                             user=999))
        self.assertIsNone(self.bot._model)
        self.bot.handle_update(make_callback("model:0", message_id=message_id))
        self.assertEqual(self.bot._model, options[0][0])
        self.assertEqual(self.bot._read_model(), options[0][0])
        with patch("bot.AgyRunner") as runner:
            runner.return_value.run.return_value = AgentResult(True, "answer")
            self.bot.handle_update(make_update("test"))
            worker = self.bot._worker
            if worker: worker.join(2)
            runner.return_value.run.assert_called_once_with("test", model=options[0][0])
        self.bot.handle_update(make_update("/model default"))
        self.assertIsNone(self.bot._model)
        self.bot.handle_update(make_callback("model:0", message_id=message_id))
        self.assertIsNone(self.bot._model)  # Stale picker must remain invalid.

    def test_model_picker_refuses_busy_and_stale(self):
        with patch("bot.list_models", return_value=[("gemini-3.8-flash-high", "Gemini")]):
            self.bot.handle_update(make_update("/model"))
        key_id = len(self.api.sent)
        with self.bot._lock:
            from bot import Task
            from unittest.mock import MagicMock
            self.bot._active = Task("faketask", 101, 202, None, MagicMock(),
                                    None, 1.0)
        self.bot.handle_update(make_callback("model:0", message_id=key_id))
        self.assertIsNone(self.bot._model)
        self.bot.handle_update(make_update("/model default"))
        self.assertIsNone(self.bot._model)
        with self.bot._lock:
            self.bot._active = None
        self.bot.handle_update(make_callback("model:bad", message_id=key_id))
        self.assertIsNone(self.bot._model)

    def test_legacy_cancel_cannot_cross_thread(self):
        from bot import Task
        from unittest.mock import MagicMock
        runner = MagicMock()
        with self.bot._lock:
            self.bot._active = Task("task-key", 101, 202, 303, runner, None, 1.0)
        self.bot.handle_update(make_update("/cancel"))
        runner.cancel.assert_not_called()
        self.bot.handle_update(make_update("/cancel", thread=303))
        runner.cancel.assert_called_once()
        with self.bot._lock:
            self.bot._active = None

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
