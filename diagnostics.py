"""Bounded, private, structured operational diagnostics.

Never pass request text, response bodies, URLs, tokens, IDs or exceptions here.
Only allowlisted event names and numeric counters may be written.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path

EVENTS = frozenset({
    "bot_started", "bot_stopped", "poll_ok", "poll_failed",
    "inbound_authorized", "callback_authorized", "offset_failed",
    "send_attempt", "send_ok", "send_failed", "edit_failed",
    "callback_failed", "task_started", "task_finished",
    "task_stop_requested", "handler_failed", "quota_failed",
    "session_failed", "photo_cleanup_failed",
})
KINDS = frozenset({
    "http", "transport", "protocol", "api", "storage", "unknown",
    "message", "callback", "success", "failed", "stopped",
    "stop", "model", "ask", "other",
})
STAGES = frozenset({
    "startup", "get_updates", "process_update", "offset_write",
    "send_message", "edit_message", "answer_callback", "agy",
    "quota", "shutdown", "session_write", "cleanup",
})


class EventLogger:
    """Append 0600 JSONL. Two bounded backups; logging is best effort."""

    def __init__(self, path: Path, max_bytes: int = 524288):
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def log(self, event: str, *, kind: str | None = None,
            stage: str | None = None, http_status: int | None = None,
            elapsed_ms: int | None = None, retry_s: int | None = None,
            count: int | None = None) -> None:
        if event not in EVENTS:
            return
        row = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "event": event}
        if kind in KINDS:
            row["kind"] = kind
        if stage in STAGES:
            row["stage"] = stage
        for name, value, maximum in (
            ("http_status", http_status, 599), ("elapsed_ms", elapsed_ms, 86400000),
            ("retry_s", retry_s, 3600), ("count", count, 10000),
        ):
            if type(value) is int and 0 <= value <= maximum:
                row[name] = value
        payload = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf8")
        with self.lock:
            try:
                self._append(payload)
            except OSError:
                # A disk/logging failure must never reveal exception details or
                # stop a working Telegram Bot. Operations remain independent.
                return

    def _append(self, payload: bytes) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.is_symlink():
            return
        if self.path.exists() and self.path.stat().st_size + len(payload) > self.max_bytes:
            if not self._rotate():
                return
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                return
            os.write(fd, payload)
        finally:
            os.close(fd)

    def _rotate(self) -> bool:
        older = self.path.with_suffix(self.path.suffix + ".2")
        newer = self.path.with_suffix(self.path.suffix + ".1")
        if older.is_symlink() or newer.is_symlink():
            return False
        if newer.exists():
            newer.replace(older)
        if self.path.exists():
            self.path.replace(newer)
        return True
