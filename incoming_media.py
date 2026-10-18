"""Bounded, authorized Telegram photo/document downloads for one AGY task.

Revision: 1 — 2026-10-18.

The caller performs the user/chat/topic authorization before invoking these
helpers.  This module only accepts Telegram file identifiers and keeps the
result below the private workspace's incoming-media directory.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from telegram_api import TelegramError

MAX_INCOMING_BYTES = 20 * 1024 * 1024  # Telegram Bot API getFile limit.
MAX_FILENAME = 120
FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,512}")
REMOTE_PATH = re.compile(r"[A-Za-z0-9_./-]{1,512}")

# Documents are deliberately a small, predictable set.  A sender cannot make
# the bot invoke a tool merely by choosing an arbitrary MIME type.
DOCUMENT_MIME = frozenset({
    "application/pdf", "application/zip", "application/json",
    "application/octet-stream", "text/plain", "text/csv", "text/html",
    "image/jpeg", "image/png", "image/webp", "audio/mpeg", "audio/mp4",
    "video/mp4",
})
MIME_SUFFIX = {
    "application/pdf": ".pdf", "application/zip": ".zip",
    "application/json": ".json", "application/octet-stream": ".bin",
    "text/plain": ".txt", "text/csv": ".csv", "text/html": ".html",
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "video/mp4": ".mp4",
}


def _valid_file_id(value: object) -> bool:
    return isinstance(value, str) and bool(FILE_ID.fullmatch(value))


def _positive_size(value: object) -> bool:
    return type(value) is int and 0 < value <= MAX_INCOMING_BYTES


def _safe_filename(value: object, mime: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_FILENAME:
        raise ValueError("Invalid incoming filename")
    if ("/" in value or "\\" in value or value in {".", ".."}
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Unsafe incoming filename")
    suffix = Path(value).suffix.lower()
    expected = MIME_SUFFIX.get(mime)
    if expected and suffix and suffix != expected:
        raise ValueError("Incoming filename MIME mismatch")
    return value


def select_document(value: object) -> dict | None:
    """Return a validated Telegram document descriptor, or None."""
    if not isinstance(value, dict) or not _valid_file_id(value.get("file_id")):
        return None
    mime = value.get("mime_type")
    if not isinstance(mime, str) or mime.lower() not in DOCUMENT_MIME:
        return None
    mime = mime.lower()
    declared_size = value.get("file_size")
    if declared_size is not None and not _positive_size(declared_size):
        return None
    try:
        filename = _safe_filename(value.get("file_name"), mime)
    except ValueError:
        return None
    return {"kind": "document", "file_id": value["file_id"],
            "file_name": filename, "mime_type": mime,
            "file_size": declared_size}


def select_photo(value: object) -> dict | None:
    """Choose the largest valid Telegram photo variant."""
    if not isinstance(value, list):
        return None
    valid = [item for item in value if isinstance(item, dict)
             and _valid_file_id(item.get("file_id"))
             and type(item.get("width")) is int and item["width"] > 0
             and type(item.get("height")) is int and item["height"] > 0
             and (item.get("file_size") is None or _positive_size(item["file_size"]))]
    if not valid:
        return None
    item = max(valid, key=lambda row: row["width"] * row["height"])
    return {"kind": "photo", "file_id": item["file_id"],
            "width": item["width"], "height": item["height"],
            "file_size": item.get("file_size")}


def select_incoming(message: dict) -> dict | None:
    photo = select_photo(message.get("photo"))
    if photo is not None:
        return photo
    return select_document(message.get("document"))


def _remote_path(value: object) -> str:
    if (not isinstance(value, str) or not REMOTE_PATH.fullmatch(value)
            or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise TelegramError("Unsafe incoming download metadata", kind="protocol")
    return value


def _check_folder(workspace: Path, directory_name: str = "incoming-media") -> Path:
    if directory_name not in {"incoming-media", "incoming-images"}:
        raise ValueError("Invalid incoming media directory")
    folder = workspace / directory_name
    if folder.is_symlink():
        raise ValueError("Unsafe incoming media directory")
    folder.mkdir(mode=0o700, exist_ok=True)
    info = folder.stat()
    if (not folder.is_dir() or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or not folder.resolve().is_relative_to(workspace.resolve())):
        raise ValueError("Unsafe incoming media directory")
    folder.chmod(0o700)
    return folder


def download_media(api, media: dict, workspace: Path, cancelled=None,
                   directory_name: str = "incoming-media") -> Path:
    """Download one already-authorized photo/document into a private path."""
    if not isinstance(media, dict) or media.get("kind") not in {"photo", "document"}:
        raise ValueError("Unsupported incoming media")
    file_id = media.get("file_id")
    if not _valid_file_id(file_id):
        raise ValueError("Invalid incoming file id")
    declared = media.get("file_size")
    if declared is not None and not _positive_size(declared):
        raise ValueError("Incoming media exceeds the supported size")
    deadline = time.monotonic() + 60

    def check_running():
        if cancelled is not None and cancelled.is_set():
            raise ValueError("Incoming media task was stopped")
        if time.monotonic() >= deadline:
            raise ValueError("Incoming media download timed out")

    check_running()
    info = api._call("getFile", {"file_id": file_id}, timeout=15)
    if not isinstance(info, dict):
        raise TelegramError("Invalid incoming download metadata", kind="protocol")
    remote = _remote_path(info.get("file_path"))
    remote_size = info.get("file_size")
    if remote_size is not None and not _positive_size(remote_size):
        raise ValueError("Incoming media exceeds the supported size")
    if declared is not None and remote_size is not None and declared != remote_size:
        raise ValueError("Incoming media size changed")
    expected = remote_size if remote_size is not None else declared
    folder = _check_folder(workspace, directory_name)
    suffix = ".bin"
    if media["kind"] == "document":
        suffix = MIME_SUFFIX[media["mime_type"]]
        # The filename is checked even though it is not used as a path.
        _safe_filename(media["file_name"], media["mime_type"])
    fd, name = tempfile.mkstemp(prefix="incoming-", suffix=suffix, dir=folder)
    saved = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            total = 0
            prefix = b""
            req = urllib.request.Request(
                api._file_base + urllib.parse.quote(remote, safe="/"))
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    while True:
                        check_running()
                        block = response.read(65536)
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_INCOMING_BYTES:
                            raise ValueError("Incoming media exceeds the supported size")
                        prefix = (prefix + block)[:16]
                        out.write(block)
            except urllib.error.HTTPError as exc:
                raise TelegramError("Incoming media download rejected", kind="http",
                                    http_status=exc.code) from None
            except (OSError, urllib.error.URLError, TimeoutError):
                raise TelegramError("Incoming media download unavailable", kind="transport") from None
        check_running()
        if total == 0 or (expected is not None and total != expected):
            raise ValueError("Incoming media download was incomplete")
        final = saved
        if media["kind"] == "photo":
            if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
                final = saved.with_suffix(".png")
            elif prefix.startswith(b"\xff\xd8\xff"):
                final = saved.with_suffix(".jpg")
            elif prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
                final = saved.with_suffix(".webp")
            else:
                raise ValueError("Unsupported photo format")
        if final != saved:
            saved.rename(final)
        return final
    except Exception:
        saved.unlink(missing_ok=True)
        raise
