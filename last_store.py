"""Bounded private per-topic manual resend state.

Revision: 2 — 2026-10-21.  This is a user-requested retry mechanism, not an
exactly-once queue: an ambiguous Telegram send is never replayed automatically.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sessions import write_private

TTL_SECONDS = 24 * 60 * 60
MAX_ENTRIES = 32
MAX_TEXT = 50_000


class LastStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save(self, key: str, text: str, mode: str, cwd: str = ".") -> None:
        if (not isinstance(key, str) or not isinstance(text, str)
                or not isinstance(cwd, str)):
            raise ValueError("Invalid last response")
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n[Last response truncated]"
        rows = self._read()
        now = time.time()
        rows = {k: v for k, v in rows.items()
                if isinstance(v, dict) and isinstance(v.get("saved_at"), (int, float))
                and now - float(v["saved_at"]) < TTL_SECONDS}
        rows[key] = {"saved_at": now, "mode": mode, "text": text, "cwd": cwd}
        if len(rows) > MAX_ENTRIES:
            ordered = sorted(rows.items(), key=lambda item: float(item[1]["saved_at"]))
            rows = dict(ordered[-MAX_ENTRIES:])
        write_private(self.path, rows)

    def get(self, key: str) -> dict | None:
        row = self._read().get(key)
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            return None
        saved = row.get("saved_at")
        if type(saved) not in (int, float) or time.time() - float(saved) >= TTL_SECONDS:
            return None
        cwd = row.get("cwd", ".")
        if not isinstance(cwd, str):
            return None
        return {"text": row["text"], "mode": row.get("mode", "rich"), "cwd": cwd}
