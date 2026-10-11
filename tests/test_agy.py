import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agy import AgyRunner, parse_response


class AgyParsingTests(unittest.TestCase):
    def test_plain_result(self):
        self.assertEqual(parse_response(json.dumps({
            "status": "SUCCESS", "response": "hello Android"
        })).message, "hello Android")

    def test_nested_result(self):
        result = parse_response(json.dumps({
            "event": "result", "result": {
                "status": "SUCCESS", "response": "real model output"
            }
        }))
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "real model output")

    def test_json_lines_only_terminal_result(self):
        value = '\n'.join([
            json.dumps({"event": "init", "conversation_id": "synthetic"}),
            json.dumps({"event": "step_update", "text_delta": "not a final result"}),
            json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "verified"}}),
        ])
        self.assertEqual(parse_response(value).message, "verified")

    def test_reject_non_json(self):
        self.assertFalse(parse_response("SUCCESS: done").ok)

    def test_reject_empty_response(self):
        self.assertFalse(parse_response('{"status":"SUCCESS","response":""}').ok)

    def test_reject_denials_even_if_success(self):
        result = parse_response(json.dumps({
            "result": {"status": "SUCCESS", "response": "done",
                       "denied_actions": ["write_file"]}
        }))
        self.assertFalse(result.ok)
        self.assertIn("denied", result.message)

    def test_reject_error_without_exposing_error_detail(self):
        result = parse_response('{"status":"ERROR","error":"PRIVATE_PLACEHOLDER"}')
        self.assertFalse(result.ok)
        self.assertNotIn("PRIVATE_PLACEHOLDER", result.message)

    def test_reject_undocumented_string_result(self):
        self.assertFalse(parse_response('{"result":"hello"}').ok)

    def test_reject_missing_status(self):
        self.assertFalse(parse_response('{"response":"looks okay"}').ok)
        self.assertFalse(parse_response('{"result":{"response":"looks okay"}}').ok)

    def test_reject_nonterminal_status_even_with_response(self):
        for status in ("CANCELED", "CANCELLED", "INTERRUPTED", "INVALID",
                       "WAITING", "RUNNING", "ERROR"):
            with self.subTest(status=status):
                response = json.dumps({"status": status, "response": "unfinished"})
                self.assertFalse(parse_response(response).ok)

    def test_reject_conflicting_outer_and_inner_status(self):
        response = json.dumps({
            "status": "SUCCESS", "result": {
                "status": "CANCELED", "response": "not complete"
            }
        })
        self.assertFalse(parse_response(response).ok)

    def test_reject_response_text_alias_without_official_response_field(self):
        self.assertFalse(parse_response('{"status":"SUCCESS","text":"hi"}').ok)


class RunnerTests(unittest.TestCase):
    def test_subprocess_uses_argv_not_shell_and_private_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            proc = MagicMock()
            proc.communicate.return_value = ('{"status":"SUCCESS","response":"model OK"}', "")
            proc.returncode = 0
            proc.pid = 123456  # Synthetic PID, no real process is touched.
            with (patch("agy.subprocess.Popen", return_value=proc) as popen,
                  patch("agy.os.killpg") as killpg):
                result = AgyRunner("agy", Path(td), 30).run("test prompt")
            killpg.assert_called_once()
            self.assertTrue(result.ok)
            args, kw = popen.call_args
            self.assertEqual(args[0], ["agy", "-p", "test prompt", "--output-format", "json"])
            self.assertFalse(kw.get("shell", False))
            self.assertTrue(kw["start_new_session"])
            self.assertEqual(kw["cwd"], td)
            self.assertEqual(kw["stdin"], __import__("subprocess").DEVNULL)

    def test_cleanup_group_even_if_leader_already_exited(self):
        from agy import _kill_group
        proc = MagicMock()
        proc.pid = 123456
        proc.poll.return_value = 0
        with patch("agy.os.killpg") as killpg:
            _kill_group(proc)
        killpg.assert_called_once()
        proc.kill.assert_not_called()

    def test_cleanup_after_timeout(self):
        import subprocess
        proc = MagicMock()
        proc.pid = 123456
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(["agy"], 20),
            ("", ""),
        ]
        with (patch("agy.subprocess.Popen", return_value=proc),
              patch("agy.os.killpg") as killpg):
            result = AgyRunner("agy", Path("/tmp"), 20).run("prompt")
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message)
        self.assertGreaterEqual(killpg.call_count, 2)

    def test_cleanup_when_parser_rejects_result(self):
        proc = MagicMock()
        proc.pid = 123456
        proc.returncode = 0
        proc.communicate.return_value = ('{"status":"CANCELED","response":"partial"}', "")
        with (patch("agy.subprocess.Popen", return_value=proc),
              patch("agy.os.killpg") as killpg):
            result = AgyRunner("agy", Path("/tmp"), 30).run("prompt")
        self.assertFalse(result.ok)
        killpg.assert_called_once()

    def test_start_failure_is_sanitized(self):
        with patch("agy.subprocess.Popen", side_effect=FileNotFoundError("private path")):
            result = AgyRunner("agy", Path("/tmp"), 30).run("prompt")
        self.assertFalse(result.ok)
        self.assertNotIn("private path", result.message)

    def test_cancel_before_start(self):
        runner = AgyRunner("agy", Path("/tmp"), 30)
        self.assertTrue(runner.cancel())
        self.assertFalse(runner.run("prompt").ok)


if __name__ == "__main__":
    unittest.main()
