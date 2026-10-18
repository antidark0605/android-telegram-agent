"""Focused regressions for the unified transport and private media boundary."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot import TelegramAgyBot
from config import Settings
from incoming_media import MAX_INCOMING_BYTES, download_media, select_document
from last_store import LastStore, MAX_ENTRIES
from presentation import (display_keyboard, normalize_display_mode,
                          render_classic, render_response)
from rich_delivery import rich_chunks
from telegram_api import TelegramAPI, TelegramError


class IncomingDocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.api = MagicMock(_file_base="https://example.invalid/file/")
        self.document = {"file_id": "doc_123", "file_name": "report.pdf",
                         "mime_type": "application/pdf", "file_size": 8}

    def test_document_requires_allowlisted_mime_and_safe_name(self):
        self.assertEqual(select_document(self.document)["kind"], "document")
        self.assertIsNone(select_document({**self.document, "mime_type": "application/x-secret"}))
        self.assertIsNone(select_document({**self.document, "file_name": "../report.pdf"}))
        self.assertIsNone(select_document({**self.document, "file_name": "report.txt"}))

    def test_document_download_is_private_and_bounded(self):
        self.api._call.return_value = {"file_path": "documents/report.pdf", "file_size": 8}
        self.document["file_size"] = 8
        with patch("incoming_media.urllib.request.urlopen", return_value=io.BytesIO(b"%PDF-x\n!")):
            path = download_media(self.api, {"kind": "document", **self.document}, self.root)
        self.assertEqual(path.suffix, ".pdf")
        self.assertEqual(path.read_bytes(), b"%PDF-x\n!")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(path.is_relative_to(self.root / "incoming-media"))

    def test_document_size_limit_is_checked_before_get_file(self):
        oversized = {"kind": "document", **self.document,
                     "file_size": MAX_INCOMING_BYTES + 1}
        with self.assertRaises(ValueError):
            download_media(self.api, oversized, self.root)
        self.api._call.assert_not_called()


class DisplayAndLastTests(unittest.TestCase):
    def test_explicit_display_modes(self):
        self.assertEqual(normalize_display_mode("bad"), "rich")
        self.assertIn("display:classic", str(display_keyboard("rich")))
        body = "**bold** 😀"
        self.assertEqual(render_response(body, "gemini-test", {}, mode="raw"), body)
        self.assertIn("[AGY]", render_classic(body, "gemini-test", {}))
        self.assertNotIn("**", render_classic(body, "gemini-test", {}))

    def test_last_store_ttl_and_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            store = LastStore(Path(td) / "last.json")
            for i in range(MAX_ENTRIES + 2):
                store.save(str(i), "answer", "raw")
            self.assertIsNone(store.get("0"))
            self.assertEqual(store.get(str(MAX_ENTRIES + 1))["mode"], "raw")
            rows = json.loads(store.path.read_text())
            self.assertLessEqual(len(rows), MAX_ENTRIES)
            with patch("last_store.time.time", return_value=10**12):
                self.assertIsNone(store.get(str(MAX_ENTRIES + 1)))


class TransportBoundaryTests(unittest.TestCase):
    def test_rich_chunks_obey_utf16_units(self):
        chunks = rich_chunks("😀" * 7000)
        from vendor.telegram_ui import utf16_length
        self.assertEqual("".join(chunks), "😀" * 7000)
        self.assertTrue(all(utf16_length(x) <= 11000 for x in chunks))

    def test_429_retry_after_is_safe_metadata(self):
        api = TelegramAPI("synthetic-token")
        body = b'{"ok":false,"error_code":429,"parameters":{"retry_after":7}}'
        error = urllib.error.HTTPError("https://example.invalid", 429, "busy", {}, io.BytesIO(body))
        with patch("telegram_api.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TelegramError) as caught:
                api._call("sendMessage", {"chat_id": 1, "text": "x"})
        self.assertEqual((caught.exception.kind, caught.exception.http_status,
                          caught.exception.retry_after), ("http", 429, 7))
        self.assertNotIn("synthetic-token", str(caught.exception))

    def test_lifecycle_notice_is_deduplicated_after_authorized_use(self):
        class Fake:
            def __init__(self): self.sent = []
            def send_text(self, *args, **kwargs):
                self.sent.append((args, kwargs)); return len(self.sent)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Settings("synthetic", frozenset({(1, 2)}), "agy", root,
                           None, root / "config.json", False, "24h")
            api = Fake(); bot = TelegramAgyBot(cfg, api); bot._running = True
            bot._remember_destination(1, 2, 9)
            bot._remember_destination(1, 2, 9)
            self.assertEqual(len(api.sent), 1)
            self.assertIn("原因未確認", api.sent[0][0][1])
            self.assertNotIn("模型", api.sent[0][0][1])


if __name__ == "__main__":
    unittest.main()
