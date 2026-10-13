"""Synthetic-only diagnostics tests; no network or real account data."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from diagnostics import EventLogger
from telegram_api import TelegramError
from bot import TelegramAgyBot
from config import Settings


class EventLogTests(unittest.TestCase):
    def test_allowlist_and_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            logger = EventLogger(path)
            logger.log("send_failed", stage="send_message", kind="http",
                       http_status=409, count=1)
            logger.log("secret-token-should-not-appear", kind="private-secret")
            logger.log("poll_failed", stage="get_updates", kind="anything private",
                       http_status=9999)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["http_status"], 409)
            self.assertNotIn("kind", records[1])
            self.assertNotIn("http_status", records[1])
            self.assertIn("utc", records[0])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("secret", path.read_text())

    def test_rotation_and_no_symlink_follow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            logger = EventLogger(path, max_bytes=200)
            for _ in range(20):
                logger.log("poll_ok", count=1)
            self.assertTrue(path.with_suffix(".jsonl.1").exists())
            self.assertLess(path.stat().st_size, 250)
            path.unlink()
            protected = Path(tmp) / "protected.txt"
            protected.write_text("untouched")
            path.symlink_to(protected)
            logger.log("send_failed", kind="api")
            self.assertEqual(protected.read_text(), "untouched")

    def test_write_failure_does_not_interrupt_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = EventLogger(Path(tmp) / "events.jsonl")
            with patch.object(logger, "_append", side_effect=OSError("raw-private-text")):
                logger.log("send_failed", kind="transport")


class BotLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name)
        settings = Settings("synthetic-test-key", frozenset({(101, 202)}),
                            "agy", p, None, p / "config.json")
        self.api = unittest.mock.MagicMock()
        self.bot = TelegramAgyBot(settings, self.api)

    def _records(self):
        return [json.loads(line) for line in self.bot.events.path.read_text().splitlines()]

    def test_send_error_classification_no_body_or_token(self):
        self.api.send_text.side_effect = TelegramError("dangerous fake text",
                                                       kind="http", http_status=429)
        self.assertIsNone(self.bot._send(202, "sensitive prompt"))
        rows = self._records()
        self.assertEqual(rows[0]["event"], "send_attempt")
        self.assertEqual(rows[1]["event"], "send_failed")
        self.assertEqual(rows[1]["http_status"], 429)
        text = self.bot.events.path.read_text()
        self.assertNotIn("sensitive prompt", text)
        self.assertNotIn("dangerous fake text", text)

    def test_poll_http_conflict_and_recovery(self):
        self.bot.state.write(0)
        counter = [0]

        def get_updates(offset, timeout=3):
            counter[0] += 1
            if counter[0] == 1:
                raise TelegramError("unsafe private exception", kind="http",
                                    http_status=409)
            self.bot.stop()
            return []

        self.api.get_updates.side_effect = get_updates
        with patch.object(self.bot._stop, "wait", return_value=False):
            self.bot.run()
        rows = self._records()
        errors = [r for r in rows if r["event"] == "poll_failed"]
        self.assertEqual(errors[0]["http_status"], 409)
        self.assertEqual(errors[0]["retry_s"], 2)
        self.assertTrue(any(r["event"] == "poll_ok" for r in rows))
        self.assertTrue(any(r["event"] == "bot_stopped" for r in rows))
        self.assertNotIn("unsafe private exception", self.bot.events.path.read_text())

    def test_offset_failure_is_separate_and_stops(self):
        self.bot.state.write(0)
        self.api.get_updates.return_value = [{
            "update_id": 1,
            "message": {"from": {"id": 999}, "chat": {"id": 333}, "text": "ignored"},
        }]
        with patch.object(self.bot.state, "write", side_effect=OSError("secret path")):
            with self.assertRaises(OSError):
                self.bot.run()
        rows = self._records()
        self.assertTrue(any(r["event"] == "offset_failed" and
                            r["stage"] == "offset_write" for r in rows))
        self.assertFalse(any(r["event"] == "poll_failed" for r in rows))
        self.assertNotIn("secret path", self.bot.events.path.read_text())


if __name__ == "__main__":
    unittest.main()
