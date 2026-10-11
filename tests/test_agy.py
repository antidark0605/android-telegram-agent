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
            json.dumps({"event": "result", "result": {"response": "verified"}}),
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

    def test_result_as_string(self):
        self.assertTrue(parse_response('{"result":"hello"}').ok)


class RunnerTests(unittest.TestCase):
    def test_subprocess_uses_argv_not_shell_and_private_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            proc = MagicMock()
            proc.communicate.return_value = ('{"response":"model OK"}', "")
            proc.returncode = 0
            with patch("agy.subprocess.Popen", return_value=proc) as popen:
                result = AgyRunner("agy", Path(td), 30).run("test prompt")
            self.assertTrue(result.ok)
            args, kw = popen.call_args
            self.assertEqual(args[0], ["agy", "-p", "test prompt", "--output-format", "json"])
            self.assertFalse(kw.get("shell", False))
            self.assertTrue(kw["start_new_session"])
            self.assertEqual(kw["cwd"], td)

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
