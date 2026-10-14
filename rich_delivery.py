"""Telegram Bot API 10.3 Rich Message + bounded streaming media delivery."""
from __future__ import annotations

import html
import hashlib
import json
import mimetypes
import os
import re
import socket
import stat
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Iterable

from artifacts import Artifact
from telegram_api import TelegramError, split_text

MAX_RICH_CHARS = 11000  # below 32768 characters, leaving room for media links
API_FALLBACK_STATUSES = {400, 404}  # definitely rejected, not an ambiguous timeout


def rich_chunks(text: str) -> list[str]:
    """Prefer paragraph boundaries without splitting Unicode code points."""
    if not text:
        return [""]
    pieces: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > MAX_RICH_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:MAX_RICH_CHARS])
            line = line[MAX_RICH_CHARS:]
        if len(current) + len(line) > MAX_RICH_CHARS and current:
            pieces.append(current)
            current = ""
        current += line
    if current:
        pieces.append(current)
    return pieces


def _html_fallback(markdown: str) -> str:
    """Conservative readable subset, escaped before any Telegram HTML markup."""
    output = []
    code_lines = []
    fenced = False
    for line in markdown.splitlines():
        if re.match(r"^\s{0,3}(\x60{3,}|~{3,})", line):
            if fenced:
                output.append("<pre>" + html.escape("\n".join(code_lines)) + "</pre>")
                code_lines = []
            fenced = not fenced
            continue
        if fenced:
            code_lines.append(line)
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        escaped = html.escape(line, quote=False)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"(?<!\*)\x60([^\x60\n]+)\x60", r"<code>\1</code>", escaped)
        output.append(escaped)
    if fenced and code_lines:
        output.append("<pre>" + html.escape("\n".join(code_lines)) + "</pre>")
    return "\n".join(output) or "(empty response)"


def _media_kind(artifact: Artifact) -> str:
    if artifact.kind not in {"photo", "video", "audio", "document"}:
        raise ValueError("Invalid Telegram artifact kind")
    return artifact.kind


def _multipart_parts(fields: dict, attachments: list[tuple[str, Artifact]],
                     boundary: str):
    """Precompute metadata only, not file contents."""
    before = []
    for key, value in fields.items():
        piece = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        before.append((
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
            f"{piece}\r\n"
        ).encode("utf-8"))
    files = []
    for field, artifact in attachments:
        filename = artifact.filename.replace('"', "_")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        files.append((
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode("utf-8"))
    closing = f"--{boundary}--\r\n".encode("ascii")
    size = sum(map(len, before)) + sum(map(len, files)) + len(closing)
    size += sum(a.size + 2 for _, a in attachments)  # CRLF following binary
    return before, files, closing, size


def _check_file(info, item: Artifact) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_size != item.size
            or info.st_dev != item.device or info.st_ino != item.inode
            or info.st_mtime_ns != item.mtime_ns
            or info.st_ctime_ns != item.ctime_ns):
        raise OSError("Artifact changed since validation")


def _confirmed_message_id(result) -> int:
    mid = result.get("message_id") if isinstance(result, dict) else None
    if type(mid) is not int or mid <= 0:
        raise TelegramError("Telegram did not confirm a message id", kind="protocol")
    return mid


def _stream_payload(parts, attachments: list[tuple[str, Artifact]]) -> Iterable[bytes]:
    fields, heads, closing, _ = parts
    yield from fields
    for header, (_name, item) in zip(heads, attachments):
        yield header
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(item.path, flags)
        with os.fdopen(fd, "rb", buffering=0) as file:
            _check_file(os.fstat(file.fileno()), item)
            remaining = item.size
            digest = hashlib.sha256()
            while remaining:
                _check_file(os.fstat(file.fileno()), item)
                data = file.read(min(64 * 1024, remaining))
                if not data:
                    raise OSError("Artifact ended before its validated size")
                _check_file(os.fstat(file.fileno()), item)
                digest.update(data)
                remaining -= len(data)
                yield data
            # A mutation while the last chunk was consumed must also fail.
            _check_file(os.fstat(file.fileno()), item)
            if digest.hexdigest() != item.sha256:
                raise OSError("Artifact content changed since validation")
        yield b"\r\n"
    yield closing


class RichDelivery:
    def __init__(self, api):
        self.api = api

    def _multipart(self, method: str, fields: dict,
                   attachments: list[tuple[str, Artifact]]) -> dict:
        boundary = "tg-" + uuid.uuid4().hex
        parts = _multipart_parts(fields, attachments, boundary)
        if parts[3] > 50_000_000:
            raise ValueError("Total upload exceeds Telegram's standard Bot API limit")
        req = urllib.request.Request(
            self.api._base + method,
            data=_stream_payload(parts, attachments),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(parts[3])},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                raw = res.read(10_000_001)
        except urllib.error.HTTPError as exc:
            raise TelegramError("Telegram HTTP failure", kind="http",
                                http_status=exc.code) from None
        except (OSError, urllib.error.URLError, TimeoutError, socket.timeout):
            raise TelegramError("Telegram upload unavailable", kind="transport") from None
        if len(raw) > 10_000_000:
            raise TelegramError("Telegram response too large", kind="protocol")
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise TelegramError("Invalid Telegram upload response", kind="protocol") from None
        if not isinstance(obj, dict) or not obj.get("ok"):
            code = obj.get("error_code") if isinstance(obj, dict) else None
            raise TelegramError("Telegram rejected upload", kind="api",
                                http_status=code if type(code) is int else None)
        return obj.get("result")

    def _rich(self, chat: int, thread: int | None, markdown: str,
              attachments: list[Artifact] | None = None) -> int | None:
        payload: dict = {"chat_id": chat}
        if thread is not None:
            payload["message_thread_id"] = thread
        if attachments:
            entries = []
            tags = []
            uploads = []
            for i, item in enumerate(attachments):
                kind = _media_kind(item)
                marker_id = f"media{i}"
                name = f"upload{i}"
                entries.append({"id": marker_id, "media": {
                    "type": kind, "media": f"attach://{name}"}})
                tags.append(f"![](tg://{kind}?id={marker_id})")
                uploads.append((name, item))
            payload["rich_message"] = {
                "markdown": ((markdown.rstrip() + "\n\n") if markdown.strip() else "")
                            + "\n".join(tags),
                "media": entries,
            }
            result = self._multipart("sendRichMessage", payload, uploads)
        else:
            payload["rich_message"] = {"markdown": markdown or "(empty response)"}
            result = self.api._call("sendRichMessage", payload, timeout=20)
        return _confirmed_message_id(result)

    def _text_fallback(self, chat: int, thread: int | None, markdown: str) -> int | None:
        first = None
        for part in split_text(markdown or "(empty response)", limit=3000):
            payload = {"chat_id": chat, "text": _html_fallback(part),
                       "parse_mode": "HTML"}
            if thread is not None:
                payload["message_thread_id"] = thread
            result = self.api._call("sendMessage", payload, timeout=20)
            message_id = _confirmed_message_id(result)
            if first is None:
                first = message_id
        return first

    def _standard_media(self, chat: int, thread: int | None,
                        items: list[Artifact]) -> None:
        for item in items:
            kind = _media_kind(item)
            field = kind
            method = {"photo": "sendPhoto", "video": "sendVideo",
                      "audio": "sendAudio", "document": "sendDocument"}[kind]
            payload = {"chat_id": chat}
            if thread is not None:
                payload["message_thread_id"] = thread
            result = self._multipart(method, payload, [(field, item)])
            _confirmed_message_id(result)

    def send(self, chat: int, thread: int | None, markdown: str,
             artifacts: list[Artifact] | None = None) -> int | None:
        """Rich by default; fallback only on explicit HTTP reject (never timeout).

        In case of a network timeout the response may have been accepted: do not
        blindly retry or send a duplicate with a different method.
        """
        assets = artifacts or []
        chunks = rich_chunks(markdown)
        first_id = None
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            try:
                message_id = self._rich(chat, thread, chunk, assets if last else None)
            except TelegramError as exc:
                if exc.http_status not in API_FALLBACK_STATUSES:
                    raise
                message_id = self._text_fallback(chat, thread, chunk)
                if last and assets:
                    self._standard_media(chat, thread, assets)
            if first_id is None:
                first_id = message_id
        return first_id
