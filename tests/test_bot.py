import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy import AgentResult
from bot import Task, TelegramAgyBot, status_text
from config import Settings


def update(text, user=101, chat=202, topic=None, chat_type="private", is_bot=False):
    msg = {"from": {"id": user, "is_bot": is_bot},
           "chat": {"id": chat, "type": chat_type}, "text": text}
    if topic is not None:
        msg["message_thread_id"] = topic
    return {"update_id": 9, "message": msg}


def callback(data, mid, user=101, chat=202, topic=None):
    message = {"message_id": mid, "chat": {"id": chat}}
    if topic is not None:
        message["message_thread_id"] = topic
    return {"callback_query": {"id": "query", "from": {"id": user},
                               "message": message,
                               "data": data}}


class FakeAPI:
    def __init__(self):
        self.sent, self.edits, self.acks, self.log = [], [], [], []

    def send_text(self, chat, text, thread=None, reply_markup=None, parse_mode=None):
        self.sent.append((chat, text, thread, reply_markup, parse_mode))
        self.log.append(("send", text))
        return len(self.sent)

    def send_rich(self, chat, markdown, thread=None, artifacts=None):
        self.sent.append((chat, markdown, thread, None, None))
        self.log.append(("rich", markdown))
        if not hasattr(self, "rich_sent"):
            self.rich_sent = []
        self.rich_sent.append((chat, markdown, thread, artifacts))
        return len(self.sent)

    def send_classic(self, chat, text, thread=None, artifacts=None):
        self.sent.append((chat, text, thread, None, None))
        self.log.append(("classic", text))
        if not hasattr(self, "classic_sent"):
            self.classic_sent = []
        self.classic_sent.append((chat, text, thread, artifacts))
        return len(self.sent)

    def edit_text(self, chat, mid, text, reply_markup=None, parse_mode=None):
        self.edits.append((chat, mid, text, reply_markup, parse_mode))
        self.log.append(("edit", text))

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
        quota_patch = patch.object(self.bot.quota, "get", return_value=({}, None, False))
        quota_patch.start()
        self.addCleanup(quota_patch.stop)
        from sessions import scope
        self.bot.sessions.change(scope(101, 202, None), model="gemini-3.8-flash-high", started=True)

    def photo_update(self, caption=None, user=101):
        item = update("", user=user)
        item["message"].pop("text")
        item["message"]["photo"] = [{"file_id": "synthetic", "width": 20, "height": 10}]
        if caption is not None:
            item["message"]["caption"] = caption
        return item

    def document_update(self, caption=None, user=101):
        item = update("", user=user)
        item["message"].pop("text")
        item["message"]["document"] = {
            "file_id": "doc_synthetic", "file_name": "notes.txt",
            "mime_type": "text/plain", "file_size": 12,
        }
        if caption is not None:
            item["message"]["caption"] = caption
        return item

    def test_photo_caption_and_default_routed_as_tasks(self):
        with patch.object(self.bot, "_start_task") as start:
            self.bot.handle_update(self.photo_update("/describe this"))
            self.assertEqual(start.call_args.args[0], "/describe this")
            self.assertEqual(start.call_args.kwargs["photo"]["file_id"], "synthetic")
            self.bot.handle_update(self.photo_update())
            self.assertIn("附圖", start.call_args.args[0])

    def test_unauthorized_photo_never_downloads_or_starts(self):
        with patch("bot.download_photo") as download, patch("bot.AgyRunner") as runner:
            self.bot.handle_update(self.photo_update(user=999))
            download.assert_not_called()
            runner.assert_not_called()

    def test_authorized_document_routes_to_one_task_without_model_path(self):
        with patch.object(self.bot, "_start_task") as start:
            self.bot.handle_update(self.document_update("summarize this"))
        self.assertEqual(start.call_args.args[0], "summarize this")
        self.assertEqual(start.call_args.kwargs["photo"]["kind"], "document")
        self.assertEqual(start.call_args.kwargs["photo"]["file_name"], "notes.txt")

    def test_invalid_attachment_is_generic_for_authorized_and_silent_for_unknown(self):
        invalid = self.document_update()
        invalid["message"]["document"]["mime_type"] = "application/x-private"
        with patch.object(self.bot, "_start_task") as start:
            self.bot.handle_update(invalid)
            start.assert_not_called()
        self.assertIn("Unsupported attachment", self.api.sent[-1][1])
        self.assertNotIn("application/", self.api.sent[-1][1])
        count = len(self.api.sent)
        invalid_unknown = self.document_update(user=999)
        invalid_unknown["message"]["document"]["file_name"] = "../private.md"
        self.bot.handle_update(invalid_unknown)
        self.assertEqual(len(self.api.sent), count)

        unsupported = update("", topic=4)
        unsupported["message"].pop("text")
        unsupported["message"]["video"] = {"file_id": "synthetic-video"}
        self.bot.handle_update(unsupported)
        self.assertIn("Unsupported attachment", self.api.sent[-1][1])
        self.assertEqual(self.api.sent[-1][2], 4)

        invalid_photo = self.photo_update()
        invalid_photo["message"]["photo"] = []
        self.bot.handle_update(invalid_photo)
        self.assertIn("Unsupported attachment", self.api.sent[-1][1])

    def test_unknown_slash_caption_routes_photo_and_document_consistently(self):
        with patch.object(self.bot, "_start_task") as start:
            self.bot.handle_update(self.photo_update("/not-a-command"))
            self.bot.handle_update(self.document_update("/not-a-command"))
        self.assertEqual(start.call_count, 2)
        self.assertEqual([call.args[0] for call in start.call_args_list],
                         ["/not-a-command", "/not-a-command"])
        self.assertEqual([call.kwargs["photo"]["kind"] for call in start.call_args_list],
                         ["photo", "document"])

    def test_photo_download_prompt_and_cleanup(self):
        from sessions import scope
        path = self.bot.settings.workspace / "synthetic.jpg"
        path.write_bytes(b"synthetic")
        runner = MagicMock()
        runner.run.return_value = AgentResult(True, "Synthetic answer")
        runner._cancelled.is_set.return_value = False
        task = Task("synthetic-task", 101, 202, None, scope(101, 202, None), runner,
                    "gemini-test", 0, photo={"file_id": "synthetic"})
        self.bot._active = task
        with patch("bot.download_photo", return_value=path) as download:
            self.bot._execute(task, "Describe this", None)
        download.assert_called_once()
        self.assertIn(str(path), runner.run.call_args.args[0])
        self.assertFalse(path.exists())
        self.assertIsNone(self.bot._active)

    def test_photo_cleanup_error_does_not_leave_task_busy(self):
        from sessions import scope
        path = self.bot.settings.workspace / "synthetic.jpg"
        runner = MagicMock()
        runner.run.return_value = AgentResult(False, "Synthetic failure")
        runner._cancelled.is_set.return_value = False
        task = Task("synthetic-task", 101, 202, None, scope(101, 202, None), runner,
                    "gemini-test", 0, photo={"file_id": "synthetic"})
        self.bot._active = task
        with patch("bot.download_photo", return_value=path), patch.object(Path, "unlink", side_effect=OSError()):
            self.bot._execute(task, "Describe this", None)
        self.assertIsNone(self.bot._active)
        self.assertIn('"event":"photo_cleanup_failed"', self.bot.events.path.read_text())

    def test_photo_gates_before_download(self):
        from sessions import scope
        key = scope(101, 202, None)
        for state in [dict(started=False, conversation_id=None), dict(started=True, model=None)]:
            self.bot.sessions.change(key, **state)
            with patch("bot.download_photo") as download, patch("bot.AgyRunner") as runner:
                self.bot.handle_update(self.photo_update())
                download.assert_not_called()
                runner.assert_not_called()
        self.bot.sessions.change(key, started=True, model="gemini-test")
        self.bot._auxiliary = True
        with patch("bot.download_photo") as download, patch("bot.AgyRunner") as runner:
            self.bot.handle_update(self.photo_update())
            download.assert_not_called()
            runner.assert_not_called()

    def test_unauthorized_rejected(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("hello", user=999))
            self.bot.handle_update(update("hello", chat=999))
            runner.assert_not_called()
        self.assertEqual(self.api.sent, [])

    def test_myid_authorized_and_topic(self):
        self.bot.handle_update(update("/myid"))
        self.assertIn("chat_id: 202", self.api.sent[-1][1])
        self.assertIn("user_id: 101", self.api.sent[-1][1])
        self.assertIn("thread_id: none", self.api.sent[-1][1])
        self.bot.handle_update(update("/myid@SomeBot", topic=19, chat_type="supergroup"))
        self.assertIn("chat_type: supergroup", self.api.sent[-1][1])
        self.assertIn("thread_id: 19", self.api.sent[-1][1])
        self.assertEqual(self.api.sent[-1][2], 19)

    def test_myid_default_rejects_unlisted_and_bot(self):
        self.bot.handle_update(update("/myid", user=999, chat=999))
        self.bot.handle_update(update("/myid", user=999, chat=-999, chat_type="supergroup"))
        self.bot.handle_update(update("/myid", is_bot=True))
        self.assertEqual(self.api.sent, [])

    def test_myid_opt_in_bootstrap_only_own_private_chat(self):
        from dataclasses import replace
        cfg = replace(self.bot.settings, allowed_pairs=frozenset(),
                      allow_unlisted_myid=True)
        newcomer = TelegramAgyBot(cfg, self.api)
        with patch("bot.AgyRunner") as agent:
            newcomer.handle_update(update("/myid", user=999, chat=999))
            self.assertIn("user_id: 999", self.api.sent[-1][1])
            num_sent = len(self.api.sent)
            newcomer.handle_update(update("/help", user=999, chat=999))
            newcomer.handle_update(update("AGY task", user=999, chat=999))
            newcomer.handle_update(update("/myid", user=999, chat=-999,
                                          chat_type="supergroup"))
            newcomer.handle_update(update("/myid", user=999, chat=888))
            newcomer.handle_update(update("/myid", user=999, chat=999, is_bot=True))
            agent.assert_not_called()
        self.assertEqual(num_sent, len(self.api.sent))
        self.assertNotIn("999:999:main", newcomer.sessions.data)
        self.assertNotIn("inbound_authorized", newcomer.events.path.read_text())

    def test_foreign_model_callback_rejected(self):
        with patch("bot.list_models", return_value=[("gemini-3.8-flash-high", "Gemini")]):
            self.bot.handle_update(update("/model"))
        mid = len(self.api.sent)
        self.bot.handle_update(callback("model:0", mid, user=999))
        from sessions import scope
        self.assertEqual(self.bot.sessions.get(scope(101, 202, None))["model"], "gemini-3.8-flash-high")
        self.assertFalse(self.api.acks)

    def test_help_status_and_legacy_cancel_disabled(self):
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("/help"))
            self.bot.handle_update(update("/status"))
            self.bot.handle_update(update("/cancel"))
            runner.assert_not_called()
        self.assertIn("YOLO: enabled", self.api.sent[1][1])
        self.assertIn("Unknown command", self.api.sent[2][1])

    def test_authorized_use_records_destination_without_lifecycle_notice(self):
        self.bot.handle_update(update("/status", topic=23))
        self.assertEqual(len(self.api.sent), 1)
        stored = __import__("json").loads(
            self.bot.lifecycle.path.read_text(encoding="utf8"))
        self.assertFalse(stored["ready_seen"])
        self.assertEqual(stored["destinations"][0]["thread_id"], 23)

    def test_display_and_last_are_scoped_to_current_topic(self):
        from sessions import scope
        key = scope(101, 202, None)
        self.bot.handle_update(update("/format classic"))
        self.assertEqual(self.bot.sessions.get(key)["display_mode"], "classic")
        self.bot.handle_update(update("/display raw"))
        self.assertEqual(self.bot.sessions.get(key)["display_mode"], "raw")
        self.bot._save_last(key, "⚡ [AGY] · gemini-test\n\nanswer", "classic")
        before = len(self.api.sent)
        self.bot.handle_update(update("/last"))
        self.assertEqual(self.api.sent[-1][1], "⚡ [AGY] · gemini-test\n\nanswer")
        self.assertEqual(len(self.api.sent), before + 1)

    def test_raw_body_still_extracts_safe_artifact_marker(self):
        folder = self.bot.settings.workspace / "artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "raw.txt").write_text("synthetic", encoding="utf8")
        text = ('unformatted body\n'
                'TAGW_MEDIA: {"path":"artifacts/raw.txt","kind":"document"}')
        self.assertTrue(self.bot._send_output(202, text, 7, "raw"))
        _, delivered, thread, artifacts = self.api.classic_sent[-1]
        self.assertEqual((delivered, thread), ("unformatted body", 7))
        self.assertEqual([item.filename for item in artifacts], ["raw.txt"])

    def test_topic_model_scoping_and_restart(self):
        with patch("bot.list_models", return_value=[
            ("gemini-3.8-flash-high", "Gemini Flash"),
            ("claude-sonnet-5-5-medium", "Claude Sonnet")
        ]):
            self.bot.handle_update(update("/model", topic=2))
            keyboard = self.api.sent[-1][3]["inline_keyboard"]
            self.assertEqual([len(row) for row in keyboard], [2])
            self.assertEqual(keyboard[0][1]["callback_data"], "model:1")
            mid = len(self.api.sent)
            self.bot.handle_update(callback("model:1", mid, topic=2))
        from sessions import scope
        key_a = scope(101, 202, 2)
        key_b = scope(101, 202, 3)
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")
        self.assertIsNone(self.bot.sessions.get(key_b).get("model"))
        self.bot.handle_update(update("/model default", topic=3))
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")
        self.assertEqual(self.bot.sessions.__class__(self.bot.sessions.path).get(key_a)["model"],
                         "claude-sonnet-5-5-medium")
        self.bot.handle_update(callback("model:1", mid, topic=2))
        self.assertEqual(self.bot.sessions.get(key_a)["model"], "claude-sonnet-5-5-medium")

    def test_new_clears_only_current_session(self):
        from sessions import scope
        a, b = scope(101, 202, None), scope(101, 202, 12)
        self.bot.sessions.change(a, model="gemini-3.8-flash-high", display_mode="raw",
                                 conversation_id="conv-123456789")
        self.bot.sessions.change(b, conversation_id="conv-999999999")
        self.bot.handle_update(update("/new"))
        self.assertNotIn("conversation_id", self.bot.sessions.get(a))
        self.assertEqual(self.bot.sessions.get(a)["model"], "gemini-3.8-flash-high")
        self.assertEqual(self.bot.sessions.get(a)["display_mode"], "raw")
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
        self.assertTrue(any("已完成" in item[2] for item in self.api.edits))
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
        self.assertFalse(any("已完成" in edit[2] for edit in self.api.edits))
        self.assertTrue(any("失敗" in edit[2] for edit in self.api.edits))
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

    def test_stale_stop_callback_rejected_after_agent_finishes(self):
        from sessions import scope
        runner = MagicMock()
        task = Task("mock-task", 101, 202, None, scope(101, 202, None),
                    runner, "gemini-3.8-flash-high", 10,
                    message_id=13, delivering=True)
        self.bot._active = task
        self.bot.handle_update(callback("stop:mock-task", 13))
        runner.cancel.assert_not_called()
        self.assertIn("already finished", self.api.acks[-1][1])
        self.bot._active = None

    def test_agent_final_answer_declared_artifact_is_delivered_only_to_topic(self):
        from sessions import scope
        topic = 44
        self.bot.sessions.change(scope(101, 202, topic),
                                 model="gemini-3.8-flash-high", started=True)
        root = self.bot.settings.workspace
        folder = root / "artifacts"
        folder.mkdir(exist_ok=True)
        (folder / "result.txt").write_text("synthetic artifact")
        output = '## Answer\n\n**中文**\nTAGW_MEDIA: {"path":"artifacts/result.txt"}\n'
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.return_value = AgentResult(
                True, output, "12345678-abc0", {})
            factory.return_value._cancelled.is_set.return_value = False
            self.bot.handle_update(update("prepare a file", topic=topic))
            worker = self.bot._worker
            if worker:
                worker.join(timeout=4)
        self.assertTrue(self.api.rich_sent)
        chat, message, thread, media = self.api.rich_sent[-1]
        self.assertEqual((chat, thread), (202, topic))
        self.assertIn("**中文**", message)
        self.assertNotIn("TAGW_MEDIA", message)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0].filename, "result.txt")
        self.assertIsNone(self.bot._active)

    def test_successful_agy_with_failed_rich_delivery_is_not_confirmed(self):
        from telegram_api import TelegramError
        with patch("bot.AgyRunner") as factory:
            factory.return_value.run.return_value = AgentResult(
                True, "## Synthetic result", "12345678-aabb", {})
            factory.return_value._cancelled.is_set.return_value = False
            with patch.object(self.api, "send_rich", side_effect=TelegramError(
                    "network response unknown", kind="transport")):
                self.bot.handle_update(update("test"))
                worker = self.bot._worker
                if worker:
                    worker.join(timeout=4)
        self.assertTrue(any("Delivery unconfirmed" in edit[2]
                            for edit in self.api.edits))
        self.assertFalse(any("已完成" in edit[2]
                             for edit in self.api.edits))
        self.assertEqual(self.api.edits[-1][3], {"inline_keyboard": []})
        self.assertIsNone(self.bot._active)

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

    def test_ls_fixed_argv_and_authorization(self):
        with patch("bot.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "total 4\n-rw------- 1 owner owner 3 sample.txt\n"
            self.bot.handle_update(update("/ls", user=999))
            run.assert_not_called()
            self.bot.handle_update(update("/ls", topic=7))
            self.assertEqual(run.call_args.args[0], ["ls", "-l", "--", str(self.bot.settings.workspace)])
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(self.api.sent[-1][2], 7)
            self.assertIn("sample.txt", self.api.sent[-1][1])
            self.bot.handle_update(update("/ls ; dangerous"))
            self.assertEqual(run.call_count, 1)

    def test_ls_timeout_failure_and_output_bound(self):
        import subprocess
        with patch("bot.subprocess.run", side_effect=subprocess.TimeoutExpired("ls", 5)):
            self.bot.handle_update(update("/ls"))
        self.assertIn("unavailable", self.api.sent[-1][1])
        with patch("bot.subprocess.run") as run:
            run.return_value.returncode = 1
            self.bot.handle_update(update("/ls"))
            self.assertIn("failed", self.api.sent[-1][1])
            run.return_value.returncode = 0
            run.return_value.stdout = "x" * 15000
            self.bot.handle_update(update("/ls"))
            self.assertIn("truncated", self.api.sent[-1][1])
            self.assertLess(len(self.api.sent[-1][1]), 12100)

    def test_status_native_id_cwd_and_model(self):
        from sessions import scope
        self.bot.sessions.change(scope(101, 202, 8), conversation_id="synthetic-conversation", model="example-medium")
        self.bot.handle_update(update("/status", topic=8))
        text = self.api.sent[-1][1]
        self.assertIn("CWD: " + str(self.bot.settings.workspace), text)
        self.assertIn("Native session ID: synthetic-conversation", text)
        self.assertIn("Selected model: example-medium", text)
        self.bot.handle_update(update("/status", topic=9))
        self.assertIn("pending", self.api.sent[-1][1])

    def test_removed_models_and_default_are_inert(self):
        with patch("bot.list_models") as models:
            self.bot.handle_update(update("/models"))
            self.assertIn("Unknown command", self.api.sent[-1][1])
            self.bot.handle_update(update("/model default"))
            models.assert_not_called()
        with patch("bot.list_models", return_value=[("example-high", "Example (High)")]):
            self.bot.handle_update(update("/model", topic=5))
        keyboard = self.api.sent[-1][3]["inline_keyboard"]
        self.assertEqual([len(row) for row in keyboard], [1])
        self.bot.handle_update(callback("model:default", len(self.api.sent)))
        self.assertIn("expired", self.api.acks[-1][1])

    def test_new_scope_requires_start_and_explicit_model(self):
        from sessions import scope
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("hello", topic=50))
            self.assertIn("/session start", self.api.sent[-1][1])
            self.bot.handle_update(update("/session start", topic=50))
            self.assertTrue(self.bot.sessions.get(scope(101, 202, 50))["started"])
            self.bot.handle_update(update("hello", topic=50))
            self.assertIn("/model", self.api.sent[-1][1])
            runner.assert_not_called()
            self.bot.handle_update(update("/session start", topic=50))
            self.assertIn("already", self.api.sent[-1][1])
            self.bot.handle_update(update("/new", topic=50))
            self.assertTrue(self.bot.sessions.get(scope(101, 202, 50))["started"])
            self.bot.handle_update(update("hello", topic=51))
            self.assertIn("/session start", self.api.sent[-1][1])

    def test_session_start_storage_failure_is_closed(self):
        with patch.object(self.bot.sessions, "change", side_effect=OSError):
            self.bot.handle_update(update("/session start", topic=55))
        self.assertIn("could not be saved", self.api.sent[-1][1])
        with patch("bot.AgyRunner") as runner:
            self.bot.handle_update(update("hello", topic=55))
            runner.assert_not_called()

    def test_global_fresh_text_and_unauthorized_commands_do_not_start(self):
        from sessions import scope
        # Fresh authorized global scope: selecting a model is not activation.
        self.bot.sessions.change(scope(101, 202, None), started=False)
        with patch("bot.AgyRunner") as runner, patch("bot.subprocess.run") as run:
            self.bot.handle_update(update("new topic title"))
            self.assertIn("/session start", self.api.sent[-1][1])
            for cmd in ("/ls", "/status", "/session start", "/model"):
                self.bot.handle_update(update(cmd, user=999))
            runner.assert_not_called()
            run.assert_not_called()
        self.assertFalse(self.bot.sessions.get(scope(101, 202, None))["started"])

    def test_saved_conversation_is_resumed_without_new_start(self):
        from sessions import scope
        self.bot.sessions.change(scope(101, 202, 60), model="example-high", conversation_id="existing-conversation")
        with patch("bot.AgyRunner") as factory:
            runner = factory.return_value
            runner.run.return_value = AgentResult(True, "ok", "existing-conversation")
            runner._cancelled.is_set.return_value = False
            self.bot.handle_update(update("continue", topic=60))
            worker = self.bot._worker
            if worker:
                worker.join(3)
            self.assertEqual(runner.run.call_args.kwargs["conversation_id"], "existing-conversation")
            self.assertEqual(runner.run.call_args.kwargs["model"], "example-high")

    def test_quota_command_does_not_show_quote_separator_markers(self):
        self.bot._quota(202, None, "gemini-test")
        text = self.api.sent[-1][1]
        self.assertIn("Context: unavailable\n5h: unavailable\nWeekly: unavailable", text)
        self.assertNotIn("\n>\n", text)
        self.assertIsNone(self.api.sent[-1][4])  # Plain command stays plain.

    def test_status_timer_text(self):
        self.assertIn("00:00", status_text("Running", __import__("time").monotonic(), None))

    def run_task(self, result, model="gemini-test", key="synthetic-key",
                 cancelled=False, topic=None):
        import time
        from sessions import scope
        runner = MagicMock()
        runner.run.return_value = result
        runner._cancelled.is_set.return_value = cancelled
        task = Task(key, 101, 202, topic, scope(101, 202, topic), runner,
                    model, time.monotonic())
        self.bot._active = task
        self.bot._execute(task, "prompt", None)
        return task

    def test_status_payload_send_edit_same_message_and_delivery_order(self):
        task = self.run_task(AgentResult(True, "Synthetic body"))
        chat, text, thread, markup, mode = self.api.sent[0]
        self.assertEqual((chat, thread, mode), (202, None, "HTML"))
        self.assertIn("<b>任務進度</b>", text)
        self.assertIn("執行中", text)
        self.assertEqual(markup, {"inline_keyboard": [[
            {"text": "🛑 中斷", "callback_data": "stop:" + task.key}]]})
        self.assertEqual(task.message_id, 1)
        self.assertTrue(self.api.edits)
        for edit in self.api.edits:
            self.assertEqual((edit[0], edit[1], edit[4]), (202, 1, "HTML"))
            self.assertIsNotNone(edit[3])
        self.assertIn("Delivering", self.api.edits[0][2])
        self.assertEqual(self.api.edits[0][3], {"inline_keyboard": []})
        self.assertIn("已完成", self.api.edits[-1][2])
        self.assertEqual(self.api.edits[-1][3], {"inline_keyboard": []})
        kinds = [kind for kind, _ in self.api.log]
        delivering = next(i for i, (k, t) in enumerate(self.api.log)
                          if k == "edit" and "Delivering" in t)
        completed = next(i for i, (k, t) in enumerate(self.api.log)
                         if k == "edit" and "已完成" in t)
        self.assertLess(delivering, kinds.index("rich"))
        self.assertLess(kinds.index("rich"), completed)
        self.assertIsNone(self.bot._active)

    def test_timer_edits_status_with_stop_and_stops_when_delivering(self):
        from bot import Pending
        from sessions import scope
        task = Task("timer-key", 101, 202, 7, scope(101, 202, 7), MagicMock(),
                    "gemini-test", __import__("time").monotonic(), message_id=5)
        self.bot._active = task
        self.bot._pending = Pending({"task": task.key}, 9)
        done = MagicMock()
        done.wait.side_effect = [False, True]
        self.bot._timer(task, done)
        chat, mid, text, markup, mode = self.api.edits[-1]
        self.assertEqual((chat, mid, mode), (202, 5, "HTML"))
        self.assertIn("等待回覆", text)
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "stop:timer-key")
        task.delivering = True
        done.wait.side_effect = [False, True]
        count = len(self.api.edits)
        self.bot._timer(task, done)
        self.assertEqual(len(self.api.edits), count)

    def test_emitted_stop_callback_keeps_owner_chat_message_task_checks(self):
        from presentation import progress_message
        from sessions import scope
        runner = MagicMock()
        runner.cancel.return_value = True
        task = Task("emit-key", 101, 202, None, scope(101, 202, None),
                    runner, "gemini-test", 1, message_id=4)
        self.bot._active = task
        _, markup = progress_message("running", task.started, task.key)
        data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(data, "stop:emit-key")
        self.bot.handle_update(callback(data, 4, user=999))
        self.bot.handle_update(callback(data, 4, chat=999))
        self.bot.handle_update(callback(data, 5))
        self.bot.handle_update(callback("stop:other-key", 4))
        self.bot.handle_update(callback("task:cancel:emit-key", 4))
        task.delivering = True
        self.bot.handle_update(callback(data, 4))
        runner.cancel.assert_not_called()
        task.delivering = False
        self.bot.handle_update(callback(data, 4))
        runner.cancel.assert_called_once()

    def test_reply_uses_task_model_verified_quota_and_unknown_context(self):
        from datetime import datetime, timezone
        data = {"3p-5h": {"left": 20.0,
                          "reset": datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc)}}
        self.bot.quota.get.return_value = (data, None, True)
        body = 'Exact *body*\nTAGW_MEDIA: {"path":"artifacts/missing.txt"}'
        # The topic currently selects a Gemini model; the task ran on Claude.
        self.run_task(AgentResult(True, body, None, {"input_tokens": 123456}),
                      model="claude-test")
        markdown = self.api.rich_sent[-1][1]
        self.assertTrue(markdown.startswith("⚡ **[AGY]** · claude\\-test\n"))
        self.assertIn("Exact *body*", markdown)
        self.assertIn("[Artifact omitted", markdown)
        self.assertIn("> 📊 **Usage** · cached", markdown)
        self.assertIn("> Context: unavailable", markdown)
        self.assertIn("5h: 80.0% used · 20.0% left · resets 01/02 11:04", markdown)
        self.assertIn("Weekly: unavailable", markdown)
        self.assertNotIn("123456", markdown)
        self.assertEqual(self.bot.quota.get.call_count, 1)

    def test_cancelled_and_failed_tasks_clear_stop_without_delivering(self):
        for cancelled, label in [(True, "已取消"), (False, "失敗")]:
            self.api.edits.clear()
            task = self.run_task(AgentResult(False, "synthetic stop"), cancelled=cancelled)
            self.assertFalse(any("Delivering" in edit[2] for edit in self.api.edits))
            self.assertIn(label, self.api.edits[-1][2])
            self.assertEqual(self.api.edits[-1][3], {"inline_keyboard": []})
            self.assertIn("Task failed: synthetic stop", self.api.sent[-1][1])
            task.runner.cancel.reset_mock()
            self.bot.handle_update(callback("stop:" + task.key, task.message_id))
            task.runner.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
