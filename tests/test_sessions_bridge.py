import json
import os
import sys
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
            s.change(one, conversation_id="conv-first", display_mode="classic")
            s.change(two, conversation_id="conv-second")
            self.assertEqual(s.get(one)["conversation_id"], "conv-first")
            self.assertEqual(SessionStore(file).get(two)["conversation_id"], "conv-second")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            s.reset(one)
            self.assertEqual(s.get(two)["conversation_id"], "conv-second")
            self.assertNotIn("conversation_id", s.get(one))
            self.assertEqual(s.get(one)["display_mode"], "classic")
            self.assertEqual(s.get(one)["cwd"], ".")

    def test_reset_preserves_selected_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td) / "sessions.json")
            key = scope(1, 2, 3)
            store.change(key, cwd="projects/demo", conversation_id="conversation")
            store.reset(key)
            self.assertEqual(store.get(key)["cwd"], "projects/demo")
            self.assertNotIn("conversation_id", store.get(key))


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
            (workspace / ".agents").mkdir(parents=True, mode=0o700)
            (workspace / ".agents").chmod(0o700)
            config = workspace / ".agents" / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"telegram-ask": {
                "command": "/different"}}}), encoding="utf8")
            config.chmod(0o600)
            with self.assertRaises(ValueError):
                AskBridge(root / "ask").install(workspace)

    def test_config_migrates_managed_entry_from_prior_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            agents = workspace / ".agents"
            agents.mkdir(parents=True, mode=0o700)
            agents.chmod(0o700)
            bridge = AskBridge(root / "ask")
            config = agents / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"other": {"command": "safe"},
                "telegram-ask": {
                "command": sys.executable,
                "args": [str(root / "prior-release" / "ask_bridge.py"),
                         "mcp", str(bridge.directory)]}}}), encoding="utf8")
            config.chmod(0o600)
            bridge.install(workspace)
            servers = json.loads(config.read_text())["mcpServers"]
            entry = servers["telegram-ask"]
            self.assertEqual(entry["command"], sys.executable)
            self.assertEqual(entry["args"][0], str(Path(__import__(
                "ask_bridge").__file__).resolve()))
            self.assertEqual(servers["other"], {"command": "safe"})
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_config_rejects_managed_shape_for_another_bridge_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            agents = workspace / ".agents"
            agents.mkdir(parents=True, mode=0o700)
            agents.chmod(0o700)
            bridge = AskBridge(root / "ask")
            config = agents / "mcp_config.json"
            config.write_text(json.dumps({"mcpServers": {"telegram-ask": {
                "command": sys.executable,
                "args": [str(root / "prior-release" / "ask_bridge.py"),
                         "mcp", str(root / "other-bridge")]}}}), encoding="utf8")
            config.chmod(0o600)
            with self.assertRaises(ValueError):
                bridge.install(workspace)

    def test_managed_entry_requires_exact_safe_shape(self):
        with tempfile.TemporaryDirectory() as td:
            bridge = AskBridge(Path(td) / "ask")
            script = str(Path(td) / "release" / "ask_bridge.py")
            valid = {"command": sys.executable,
                     "args": [script, "mcp", str(bridge.directory)]}
            self.assertTrue(bridge._managed_entry(valid))
            invalid = [
                {**valid, "env": {}},
                {**valid, "command": "/different/python"},
                {**valid, "args": ["ask_bridge.py", "mcp", str(bridge.directory)]},
                {**valid, "args": [str(Path(td) / "other.py"), "mcp",
                                    str(bridge.directory)]},
                {**valid, "args": [script, "other", str(bridge.directory)]},
            ]
            self.assertTrue(all(not bridge._managed_entry(value) for value in invalid))

    def test_install_rejects_symlinked_agents_and_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            agents = workspace / ".agents"
            agents.symlink_to(outside, target_is_directory=True)
            bridge = AskBridge(root / "ask")
            with self.assertRaises(ValueError):
                bridge.install(workspace)
            self.assertFalse((outside / "mcp_config.json").exists())

            agents.unlink()
            agents.mkdir(mode=0o700)
            target = outside / "private.json"
            target.write_text("{}", encoding="utf8")
            target.chmod(0o600)
            (agents / "mcp_config.json").symlink_to(target)
            with self.assertRaises(ValueError):
                bridge.install(workspace)
            self.assertEqual(target.read_text(encoding="utf8"), "{}")

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
