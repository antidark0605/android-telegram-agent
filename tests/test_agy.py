import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agy import AgyRunner, AgentResult, list_models, parse_models, parse_response, usage_report


class ParserTests(unittest.TestCase):
    def test_success_with_session_and_usage(self):
        value = parse_response(json.dumps({
            "status": "SUCCESS", "conversation_id": "00000000-0000-4000-8000-000000000001",
            "response": "answer", "usage": {"input_tokens": 123, "output_tokens": 10,
                                           "bad_field": "secret"}}))
        self.assertTrue(value.ok)
        self.assertEqual(value.conversation_id, "00000000-0000-4000-8000-000000000001")
        self.assertEqual(value.usage, {"input_tokens": 123, "output_tokens": 10})

    def test_nested_and_streamed_result(self):
        result = {"event": "result", "result": {"status": "SUCCESS", "response": "yes"}}
        self.assertTrue(parse_response(json.dumps(result)).ok)
        self.assertTrue(parse_response('{"event":"init"}\n' + json.dumps(result)).ok)

    def test_errors_fail_closed(self):
        for data in [
            "not json", '{"response":"missing status"}',
            '{"status":"CANCELED","response":"partial"}',
            '{"status":"SUCCESS","response":""}',
            '{"status":"SUCCESS","response":"done","denied_actions":[{"action":"command"}]}',
            '{"status":"SUCCESS","response":"done","error":"secret"}',
            '{"status":"SUCCESS","text":"unknown"}',
        ]:
            with self.subTest(data=data):
                result = parse_response(data)
                self.assertFalse(result.ok)
                self.assertNotIn("secret", result.message)

    def test_model_parser(self):
        parsed = parse_models("gemini-3.8-flash-high    Gemini 3.8 Flash High\n"
                              "claude-sonnet-5-5-medium   Claude Medium")
        self.assertEqual(len(parsed), 2)


class AgyRunnerTests(unittest.TestCase):
    def _proc(self):
        proc = MagicMock()
        proc.pid = 123456
        proc.returncode = 0
        proc.communicate.return_value = (
            '{"status":"SUCCESS","response":"ok","conversation_id":"12345678-abcd"}', "")
        return proc

    def test_per_topic_resume_model_yolo_timeout(self):
        proc = self._proc()
        with (patch("agy.subprocess.Popen", return_value=proc) as popen,
              patch("agy.os.killpg") as kill):
            reply = AgyRunner("agy", Path("/tmp"), None, "24h").run(
                "prompt", model="gemini-3.8-flash-high",
                conversation_id="12345678-abcd", yolo=True)
        self.assertTrue(reply.ok)
        args, kwargs = popen.call_args
        self.assertIn("--conversation", args[0])
        self.assertIn("--model", args[0])
        self.assertIn("--dangerously-skip-permissions", args[0])
        self.assertEqual(args[0][-1], "--dangerously-skip-permissions")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIsNone(proc.communicate.call_args.kwargs["timeout"])
        kill.assert_called_once()

    def test_default_safe_no_yolo(self):
        proc = self._proc()
        with (patch("agy.subprocess.Popen", return_value=proc) as spawn,
              patch("agy.os.killpg")):
            AgyRunner("agy", Path("/tmp"), None).run("hello")
        args = spawn.call_args.args[0]
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--conversation", args)

    def test_invalid_model_or_session_rejected_before_start(self):
        with patch("agy.subprocess.Popen") as spawn:
            runner = AgyRunner("agy", Path("/tmp"), 30)
            self.assertFalse(runner.run("a", model="bad;command").ok)
            self.assertFalse(runner.run("a", conversation_id="bad").ok)
        spawn.assert_not_called()

    def test_cancel_active_process(self):
        proc = self._proc()
        with (patch("agy.subprocess.Popen", return_value=proc),
              patch("agy.os.killpg") as kill):
            runner = AgyRunner("agy", Path("/tmp"), 30)
            self.assertTrue(runner.run("p").ok)
            self.assertTrue(runner.cancel())
        self.assertGreaterEqual(kill.call_count, 1)

    def test_timeout(self):
        proc = self._proc()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired("agy", 15), ("", "")]
        with (patch("agy.subprocess.Popen", return_value=proc),
              patch("agy.os.killpg") as kill):
            answer = AgyRunner("agy", Path("/tmp"), 15).run("hello")
        self.assertFalse(answer.ok)
        self.assertIn("timed out", answer.message)
        self.assertGreaterEqual(kill.call_count, 2)

    def test_models_subprocess(self):
        proc = MagicMock(returncode=0, stdout="gemini-3.8-flash-high   Gemini Flash")
        with patch("agy.subprocess.run", return_value=proc) as call:
            choices = list_models("agy", Path("/tmp"))
        self.assertEqual(choices[0][0], "gemini-3.8-flash-high")
        self.assertEqual(call.call_args.args[0], ["agy", "models"])

    def test_usage_report_allows_only_numeric_quota_schema(self):
        data = {
            "status": "SUCCESS", "response": "Free text is never sent to Telegram",
            "rate_limits": {
                "five_hour": {"remaining_percent": 80, "reset_in_seconds": 3600,
                              "account_email": "never-export@example.test"},
                "weekly": {"remaining_percent": 12.5, "reset_in_seconds": 1800},
                "unexpected": {"secret": "never-export"},
            },
        }
        proc = MagicMock(returncode=0, stdout=json.dumps(data))
        with patch("agy.subprocess.run", return_value=proc):
            report = usage_report("agy", Path("/tmp"))
        self.assertEqual(report,
                         "5-hour: 80% remaining, reset in 60 min\\n"
                         "Weekly: 12.5% remaining, reset in 30 min".replace("\\n", "\n"))
        self.assertNotIn("never-export", report)

    def test_usage_report_rejects_failed_denied_and_unstructured_results(self):
        variants = [
            {"status": "ERROR", "response": "Looks like a quota: 99%",
             "rate_limits": {"five_hour": {"remaining_percent": 99}}},
            {"status": "SUCCESS", "response": "Not authorized",
             "denied_actions": [{"action": "command"}],
             "rate_limits": {"five_hour": {"remaining_percent": 99}}},
            {"status": "SUCCESS", "response": "Remaining 80% reset in 3h"},
            {"response": "Remaining 80%"},
            {"status": "SUCCESS", "response": "ok",
             "rate_limits": {"weekly": {"remaining_percent": "90%"}}},
            {"status": "SUCCESS", "response": "ok",
             "rate_limits": {"weekly": {"remaining_percent": True}}},
            {"status": "SUCCESS", "response": "ok",
             "rate_limits": {"weekly": {"remaining_percent": 109}}},
        ]
        for data in variants:
            with self.subTest(data=data):
                proc = MagicMock(returncode=0, stdout=json.dumps(data))
                with patch("agy.subprocess.run", return_value=proc):
                    self.assertIsNone(usage_report("agy", Path("/tmp")))

    def test_usage_report_requires_success_exit(self):
        proc = MagicMock(returncode=1,
                         stdout='{"status":"SUCCESS","response":"ok",'
                                '"rate_limits":{"weekly":{"remaining_percent":50}}}')
        with patch("agy.subprocess.run", return_value=proc):
            self.assertIsNone(usage_report("agy", Path("/tmp")))


if __name__ == "__main__":
    unittest.main()
