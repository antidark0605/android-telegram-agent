import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy import AgentResult
from bot import Task, TelegramAgyBot, status_text, stop_keyboard
from config import Settings


def update(text, user=101, chat=202, topic=None):
    msg = {"from": {"id": user}, "chat": {"id": chat}, "text": text}
    if topic is not None:
        msg["message_thread_id"] = topic
    return {"update_id": 9, "message": msg}


def callback(data, mid, user=101, chat=202):
    return {"callback_query": {"id": "query", "from": {"id": user},
                               "message": {"message_id": mid, "chat": {"id": chat}},
                               "data": data}}


class FakeAPI:
    def __init__(self):
        self.sent, self.edits, self.acks = [], [], []

    def send_text(self, chat, text, thread=None, reply_markup=None):
        self.sent.append((chat, text, thread, reply_markup))
        return len(self.sent)

    def edit_text(self, chat, mid, text, reply_markup=None):
        self.edits.append((chat, mid, text, reply_markup))

    def answer_callback(self, query, text=""):
        self.acks.append((query, text))


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.api = FakeAPI()
        cfg = Settings("test-only", frozenset({(101, 202)}), "agy",
                       root, None, root / "private.json", True, "24h")
        self.bot = TelegramAgyBot(cfg, self.api)

    def test_unauthorized_rejected(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("hello", user=999))
            self.bot.handle_update(update("hello", chat=999))
            runner.assert_not_called()
        self.assertEqual(self.api.sent, [])

    def test_help_status_and_legacy_cancel_disabled(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("/help"))
            self.bot.handle_update(update("/status"))
            self.bot.handle_update(update("/cancel"))
            runner.assert_not_called()
        self.assertIn("YOLO: enabled", self.api.sent[1][1])
        self.assertIn("Unknown command", self.api.sent[2][1])

    def test_topic_model_scoping_and_restart(self):
        with patch("bot.list_models", return_value=[
            ("gemini-3.8-flash-high", "Gemini Flash"),
            ("claude-sonnet-5-5-medium", "Claude Sonnet")
        ]):
            self.bot.handle_update(update("/models", topic=2))
            mid = len(self.api.sent)
            self.bot.handle_update(callback("model:1", mid))
        from sessions import scope
        key_a = scope(101, 202, 2)
        key_b = scope(101, 202, 3)
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")
        self.assertIsNone(self.bot.sessions.get(key_b).get("model"))
        self.bot.handle_update(update("/model default", topic=3))
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")
        self.assertEqual(self.bot.sessions.__class__(self.bot.sessions.path).get(key_a)["model"],
                         "claude-sonnet-5-5-medium")
        self.bot.handle_update(callback("model:1", mid))
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")

    def test_new_clears_only_current_session(self):
        from sessions import scope
        a, b = scope(101, 202, None), scope(101, 202, 12)
        self.bot.sessions.change(a, model="gemini-3.8-flash-high",
                                 conversation_id="conv-123456789")
        self.bot.sessions.change(b, conversation_id="conv-999999999")
        self.bot.handle_update(update("/new"))
        self.assertNotIn("conversation_id", self.bot.sessions.get(a))
        self.assertEqual(self.bot.sessions.get(a)["model"], "gemini-3.8-flash-high")
        self.assertEqual(self.bot.sessions.get(b)["conversation_id"], "conv-999999999")

    def test_ask_pending_free_text(self):
        from bot import Pending
        from sessions import scope
        runner = MagicMock()
        task = Task("abcd1234", 101, 202, 7, scope(101, 202, 7), runner, None, 1)
        question = {"task": task.key, "id": "a" * 32, "chat": 202,
                    "topic": 7, "choices": []}
        with self.bot._lock:
            self.bot._active = task
            self.bot._pending = Pending(question, 9)
        with patch.object(self.bot.bridge, "answer") as answer:
            self.bot.handle_update(update("my answer", topic=7))
        answer.assert_called_once_with(question, "my answer")
        self.assertIsNone(self.bot._pending)

    def test_wrong_topic_does_not_answer(self):
        from bot import Pending
        from sessions import scope
        task = Task("task1234", 101, 202, 7, scope(101, 202, 7), MagicMock(), None, 1)
        question = {"task": task.key, "id": "b" * 32, "chat": 202,
                    "topic": 7, "choices": []}
        self.bot._active = task
        self.bot._pending = Pending(question, 3)
        with patch.object(self.bot.bridge, "answer") as answer:
            self.bot.handle_update(update("different topic", topic=8))
        answer.assert_not_called()

    def test_stop_callback_scope_and_stale(self):
        from sessions import scope
        runner = MagicMock()
        runner.cancel.return_value = True
        task = Task("12ab34cd", 101, 202, None, scope(101, 202, None),
                    runner, None, 1, message_id=4)
        self.bot._active = task
        self.bot.handle_update(callback("stop:12ab34cd", 4, user=999))
        self.bot.handle_update(callback("stop:old", 4))
        self.bot.handle_update(callback("stop:12ab34cd", 8))
        runner.cancel.assert_not_called()
        self.bot.handle_update(callback("stop:12ab34cd", 4))
        runner.cancel.assert_called_once()

    def test_real_task_updates_session_and_status(self):
        from sessions import scope
        key = scope(101, 202, None)
        with patch("bot.AgyRunner") as factory:
            instance = factory.return_value
            instance.run.return_value = AgentResult(
                True, "model result", "12345678-aaaa",
                {"input_tokens": 11})
            instance._cancelled.is_set.return_value = False
            self.bot.handle_update(update("hello"))
            worker = self.bot._worker
            if worker:
                worker.join(3)
        self.assertEqual(self.bot.sessions.get(key)["conversation_id"], "12345678-aaaa")
        self.assertEqual(self.bot.sessions.get(key)["last_usage"]["input_tokens"], 11)
        self.assertTrue(any("Completed" in item[2] for item in self.api.edits))
        self.assertTrue(any("model result" in item[1] for item in self.api.sent))
        self.assertIsNone(self.bot._active)

    def test_successful_agy_but_failed_session_save_is_not_marked_complete(self):
        with patch("bot.AgyRunner") as factory:
            runner = factory.return_value
            runner.run.return_value = AgentResult(
                True, "synthetic model answer", "12345678-aaaa",
                {"input_tokens": 11})
            runner._cancelled.is_set.return_value = False
            with patch.object(self.bot.sessions, "change", side_effect=OSError):
                self.bot.handle_update(update("test question"))
                worker = self.bot._worker
                if worker:
                    worker.join(3)
        self.assertFalse(any("✅ Completed" in edit[2] for edit in self.api.edits))
        self.assertTrue(any("❌ Failed" in edit[2] for edit in self.api.edits))
        self.assertTrue(any("session state could not be saved" in item[1]
                            for item in self.api.sent))
        from sessions import scope
        self.assertNotIn("conversation_id",
                         self.bot.sessions.get(scope(101, 202, None)))
        lines = self.bot.events.path.read_text(encoding="utf8").splitlines()
        self.assertTrue(any('"event":"session_failed"' in line for line in lines))
        self.assertTrue(any('"kind":"failed"' in line for line in lines))
        self.assertIsNone(self.bot._active)

    def test_session_reset_failure_is_reported(self):
        with patch.object(self.bot.sessions, "reset", side_effect=OSError):
            self.bot.handle_update(update("/new"))
        self.assertIn("could not be saved", self.api.sent[-1][1])

    def test_busy_only_one_process(self):
        entered, exit_now = threading.Event(), threading.Event()
        def slow_run(*args, **kwargs):
            entered.set()
            exit_now.wait(2)
            return AgentResult(False, "stopped")
        with patch("bot.AgyRunner") as runner:
            runner.return_value.run.side_effect = slow_run
            runner.return_value._cancelled.is_set.return_value = True
            self.bot.handle_update(update("long"))
            self.assertTrue(entered.wait(2))
            self.bot.handle_update(update("second"))
            self.assertTrue(any("busy" in x[1] for x in self.api.sent))
            exit_now.set()
            worker = self.bot._worker
            if worker: worker.join(3)
            runner.return_value.run.assert_called_once()

    def test_status_timer_text(self):
        self.assertIn("00:00", status_text("Running", __import__("time").monotonic(), None))
        self.assertIn("Stop", str(stop_keyboard("abc")))


if __name__ == "__main__":
    unittest.main()
