"""Private lifecycle readiness evidence and recently used destinations.

Revision: 1 — 2026-10-18. The store contains only Telegram routing integers,
use ordering, and a readiness flag. It never stores content or host details.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sessions import write_private

MAX_DESTINATIONS = 128


class LifecycleStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf8"))
        except FileNotFoundError:
            return {"revision": 1, "ready_seen": False, "destinations": []}
        except (OSError, ValueError, TypeError):
            return {"revision": 1, "ready_seen": False, "destinations": []}
        if not isinstance(raw, dict):
            return {"revision": 1, "ready_seen": False, "destinations": []}
        rows = raw.get("destinations")
        valid = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                user, chat = row.get("user_id"), row.get("chat_id")
                thread, used = row.get("thread_id"), row.get("used_at_ns")
                if (type(user) is int and user > 0 and type(chat) is int and chat != 0
                        and (thread is None or type(thread) is int)
                        and type(used) is int and used >= 0):
                    valid.append({"user_id": user, "chat_id": chat,
                                  "thread_id": thread, "used_at_ns": used})
        return {"revision": 1, "ready_seen": raw.get("ready_seen") is True,
                "destinations": valid[-MAX_DESTINATIONS:]}

    def remember(self, user: int, chat: int, thread: int | None) -> None:
        if (type(user) is not int or user <= 0 or type(chat) is not int or chat == 0
                or (thread is not None and type(thread) is not int)):
            raise ValueError("Invalid lifecycle destination")
        with self.lock:
            data = self._read()
            rows = [row for row in data["destinations"]
                    if (row["user_id"], row["chat_id"]) != (user, chat)]
            rows.append({"user_id": user, "chat_id": chat, "thread_id": thread,
                         "used_at_ns": time.time_ns()})
            data["destinations"] = rows[-MAX_DESTINATIONS:]
            write_private(self.path, data)

    def begin_instance(self) -> tuple[str, list[tuple[int, int, int | None]]]:
        """Persist readiness and return prior destinations plus honest state."""
        with self.lock:
            data = self._read()
            state = "restarted" if data["ready_seen"] else "ready"
            targets = [(row["user_id"], row["chat_id"], row["thread_id"])
                       for row in sorted(data["destinations"],
                                         key=lambda item: item["used_at_ns"],
                                         reverse=True)]
            data["ready_seen"] = True
            write_private(self.path, data)
            return state, targets
