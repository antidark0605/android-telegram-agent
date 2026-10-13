import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ask_bridge import AskBridge, _ask, mcp_main
from sessions import SessionStore, scope


class SessionTests(unittest.TestCase):
    def test_per_topic_persistent(self):
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / "sessions.json"
            s = SessionStore(file)
            one, two = scope(123, 456, 1), scope(123, 456, 2)
            s.change(one, conversation_id="conv-first")
            s.change(two, conversation_id="conv-second")
            self.assertEqual(s.get(one)["conversation_id"], "conv-first")
            self.assertEqual(SessionStore(file).get(two)["conversation_id"], "conv-second")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            s.reset(one)
            self.assertEqual(s.get(two)["conversation_id"], "conv-second")
            self.assertNotIn("conversation_id", s.get(one))


    def test_transactional_session_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            store = SessionStore(path)
            key = scope(1, 2, 3)
            store.change(key, conversation_id="existing-session")
            initial = path.read_bytes()
            with patch("sessions.write_private", side_effect=OSError):
                with self.assertRaises(OSError):
                    store.change(key, conversation_id="new-session")
                with self.assertRaises(OSError):
                    store.reset(key)
            self.assertEqual(store.get(key)["conversation_id"], "existing-session")
            self.assertEqual(path.read_bytes(), initial)
            self.assertEqual(SessionStore(path).get(key)["conversation_id"],
                             "existing-session")

    def test_corrupt_state_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text("invalid-json")
            with self.assertRaises(ValueError):
                SessionStore(path)

class BridgeTests(unittest.TestCase):
    def test_local_mcp_config_created_without_global_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            ask = AskBridge(root / "ask")
            ask.install(workspace)
            path = workspace / ".agents" / "mcp_config.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            cfg = json.loads(path.read_text())
            self.assertIn("telegram-ask", cfg["mcpServers"])
            self.assertTrue(cfg["mcpServers"]["telegram-ask"]["args"][0].endswith("ask_bridge.py"))
            ask.install(workspace)  # idempotent

    def test_config_refuses_conflicting_mcp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            (workspace / ".agents").mkdir(parents=True)
            config = workspace / ".agents" / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"telegram-ask": {
                "command": "/different"}}}), encoding="utf8")
            with self.assertRaises(ValueError):
                AskBridge(root / "ask").install(workspace)

    def test_mcp_question_answer_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = AskBridge(Path(td) / "ask")
            bridge.begin("task-1", 101, 202, 303)
            result = []
            def ask_thread():
                result.append(_ask(bridge.directory, {
                    "question": "Pick one",
                    "choices": [{"label": "A", "description": "First choice", "value": "first"}]}))
            t = threading.Thread(target=ask_thread)
            t.start()
            question = None
            for _ in range(30):
                question = bridge.next_question("task-1")
                if question:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(question)
            self.assertEqual(question["topic"], 303)
            bridge.answer(question, "first")
            t.join(timeout=2)
            self.assertEqual(result, ["first"])
            bridge.end("task-1")
            self.assertFalse(bridge.active.exists())

    def test_mcp_stop_unblocks_wait(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = AskBridge(Path(td) / "ask")
            bridge.begin("task-2", 101, 202, None)
            result = []
            t = threading.Thread(target=lambda: result.append(
                _ask(bridge.directory, {"question": "Continue?"})))
            t.start()
            for _ in range(30):
                if bridge.next_question("task-2"):
                    break
                time.sleep(0.05)
            bridge.end("task-2")
            t.join(timeout=2)
            self.assertIn("ended", result[0])

    def test_choices_must_have_description(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = AskBridge(Path(td) / "ask")
            bridge.begin("task-3", 1, 2, None)
            answer = _ask(bridge.directory, {
                "question": "?", "choices": [{"label": "A", "value": "a"}]})
            self.assertIn("description", answer)
            bridge.end("task-3")


if __name__ == "__main__":
    unittest.main()
