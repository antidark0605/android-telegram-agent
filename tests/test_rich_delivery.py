"""No-network tests for rich responses and scoped artifacts."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifacts import extract_artifacts
from rich_delivery import RichDelivery, _multipart_parts, _stream_payload, rich_chunks
from telegram_api import TelegramAPI, TelegramError


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "artifacts"
        self.folder.mkdir()
        (self.folder / "summary.pdf").write_bytes(b"PDF-file")
        (self.folder / "image.png").write_bytes(b"png-file")

    def test_marker_dedup_and_fenced_code(self):
        marker = 'TAGW_MEDIA: {"path":"artifacts/summary.pdf","kind":"auto"}'
        text = "## Test\n" + marker + "\n" + marker + "\n"
        cleaned, files = extract_artifacts(text, self.root)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].kind, "document")
        self.assertNotIn("TAGW_MEDIA", cleaned)
        quoted = "\x60\x60\x60\n" + marker + "\n\x60\x60\x60\n"
        cleaned, files = extract_artifacts(quoted, self.root)
        self.assertEqual(files, [])
        self.assertIn(marker, cleaned)

    def test_no_symlinks_path_escape_or_oversized_file(self):
        (self.root / "secret.txt").write_text("private")
        (self.folder / "link.txt").symlink_to(self.root / "secret.txt")
        invalid = ["artifacts/../secret.txt", "/tmp/secret.txt",
                   "artifacts/link.txt", "artifacts/missing.txt"]
        for name in invalid:
            with self.subTest(name=name):
                cleaned, assets = extract_artifacts(
                    'TAGW_MEDIA: {"path":' + __import__("json").dumps(name) + "}",
                    self.root)
                self.assertEqual(assets, [])
                self.assertIn("Artifact omitted", cleaned)
                self.assertNotIn("secret.txt", cleaned)
        with (self.folder / "huge.bin").open("wb") as fd:
            fd.truncate(21 * 1024 * 1024)
        _, assets = extract_artifacts(
            'TAGW_MEDIA: {"path":"artifacts/huge.bin"}', self.root)
        self.assertEqual(assets, [])

    def test_max_four_artifacts_and_root_symlink(self):
        for index in range(5):
            (self.folder / f"{index}.txt").write_text(str(index))
        text = "\n".join(
            'TAGW_MEDIA: {"path":"artifacts/%s.txt"}' % index
            for index in range(5))
        cleaned, assets = extract_artifacts(text, self.root)
        self.assertEqual(len(assets), 4)
        self.assertIn("Artifact omitted", cleaned)


class RichTests(unittest.TestCase):
    def setUp(self):
        self.api = TelegramAPI("synthetic-token")
        self.send = RichDelivery(self.api)

    def test_markdown_unicode_and_topic(self):
        with patch.object(self.api, "_call", return_value={"message_id": 5}) as call:
            self.assertEqual(self.send.send(123, 456, "### 中文\n**bold**"), 5)
        self.assertEqual(call.call_args.args[0], "sendRichMessage")
        args = call.call_args.args[1]
        self.assertEqual(args["message_thread_id"], 456)
        self.assertIn("**bold**", args["rich_message"]["markdown"])

    def test_400_fallback_and_network_timeout_not_retried(self):
        calls = []
        def reject(method, payload, timeout=20):
            calls.append(method)
            if method == "sendRichMessage":
                raise TelegramError("rejected", kind="http", http_status=400)
            return {"message_id": 15}
        with patch.object(self.api, "_call", side_effect=reject):
            self.assertEqual(self.send.send(1, 2, "## 題目\n**重要**"), 15)
        self.assertEqual(calls, ["sendRichMessage", "sendMessage"])
        calls.clear()
        def uncertain(method, payload, timeout=20):
            calls.append(method)
            raise TelegramError("network", kind="transport")
        with patch.object(self.api, "_call", side_effect=uncertain):
            with self.assertRaises(TelegramError):
                self.send.send(1, 2, "**danger**")
        self.assertEqual(calls, ["sendRichMessage"])

    def test_chunk_size_and_unicode(self):
        answer = "中文加粗**strong**\n" * 5000
        chunks = rich_chunks(answer)
        self.assertTrue(len(chunks) > 4)
        self.assertEqual("".join(chunks), answer)
        self.assertTrue(all(len(piece) <= 11000 for piece in chunks))

    def test_stream_multipart_photo_and_file_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "artifacts").mkdir()
            file = root / "artifacts" / "chart.png"
            file.write_bytes(b"A" * 151000)
            _, assets = extract_artifacts(
                'TAGW_MEDIA: {"path":"artifacts/chart.png"}', root)
            before = _multipart_parts({"chat_id": 1}, [("upload0", assets[0])], "test")
            chunks = list(_stream_payload(before, [("upload0", assets[0])]))
            self.assertEqual(len(b"".join(chunks)), before[3])
            self.assertLessEqual(max(map(len, chunks)), 65536)
            self.assertIn(b"A" * 100, b"".join(chunks))
            file.write_bytes(b"replacement")
            with self.assertRaises(OSError):
                b"".join(_stream_payload(before, [("upload0", assets[0])]))

    def test_real_multipart_request_is_bounded_and_topic_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "artifacts").mkdir()
            (root / "artifacts" / "diagram.png").write_bytes(b"X" * 160000)
            _, media = extract_artifacts(
                'TAGW_MEDIA: {"path":"artifacts/diagram.png"}', root)
            observed = []
            class Response:
                def __enter__(self):
                    return self
                def __exit__(self, *_args):
                    return False
                def read(self, _limit):
                    return b'{"ok":true,"result":{"message_id":55}}'
            def fake_open(req, timeout=90):
                parts = list(req.data)
                self.assertLessEqual(max(map(len, parts)), 65536)
                payload = b"".join(parts)
                self.assertEqual(int(req.headers["Content-length"]), len(payload))
                self.assertIn(b'"media":"attach://upload0"', payload)
                self.assertIn(b"tg://photo?id=media0", payload)
                observed.append((req.full_url, payload))
                return Response()
            with patch("rich_delivery.urllib.request.urlopen", side_effect=fake_open):
                self.assertEqual(self.send.send(7, 21, "## Diagram", media), 55)
            self.assertEqual(len(observed), 1)
            self.assertTrue(observed[0][0].endswith("/sendRichMessage"))

    def test_no_symlinked_artifact_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "safe"
            real.mkdir()
            (real / "secret.txt").write_text("fake secret")
            (root / "artifacts").symlink_to(real, target_is_directory=True)
            rendered, files = extract_artifacts(
                'TAGW_MEDIA: {"path":"artifacts/secret.txt"}', root)
            self.assertEqual(files, [])
            self.assertIn("omitted", rendered)

    def test_rich_media_schema_and_standard_media_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "artifacts").mkdir()
            (root / "artifacts" / "summary.pdf").write_text("hello")
            _, assets = extract_artifacts(
                'TAGW_MEDIA: {"path":"artifacts/summary.pdf"}', root)
            uploads = []
            def fake_upload(method, payload, attachments):
                uploads.append((method, payload, attachments))
                if method == "sendRichMessage":
                    raise TelegramError("rejected", kind="http", http_status=400)
                return {"message_id": 23}
            with (patch.object(self.send, "_multipart", side_effect=fake_upload),
                  patch.object(self.api, "_call", return_value={"message_id": 22})):
                self.assertEqual(self.send.send(100, 222, "## Summary", assets), 22)
            self.assertEqual([u[0] for u in uploads],
                             ["sendRichMessage", "sendDocument"])
            media = uploads[0][1]["rich_message"]
            self.assertEqual(media["media"][0]["media"]["type"], "document")
            self.assertEqual(media["media"][0]["media"]["media"], "attach://upload0")
            self.assertIn("tg://document?id=media0", media["markdown"])
            self.assertEqual(uploads[1][1]["message_thread_id"], 222)


if __name__ == "__main__":
    unittest.main()
