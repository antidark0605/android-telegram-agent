"""Per-topic AGY session settings stored locally."""
from __future__ import annotations
import json
import os
import tempfile
import threading
from pathlib import Path

def write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def scope(user_id: int, chat_id: int, thread_id: int | None) -> str:
    return f"{user_id}:{chat_id}:{thread_id if thread_id is not None else 'main'}"

class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        try:
            raw = path.read_text(encoding="utf8")
        except FileNotFoundError:
            self.data = {}
        else:
            try:
                data = json.loads(raw)
            except ValueError as exc:
                raise ValueError("Invalid private session state") from None
            if not isinstance(data, dict) or any(
                not isinstance(key, str) or not isinstance(value, dict)
                for key, value in data.items()
            ):
                raise ValueError("Invalid private session state")
            self.data = data

    def get(self, key: str) -> dict:
        with self.lock:
            return dict(self.data.get(key, {}))

    def change(self, key: str, **updates) -> dict:
        with self.lock:
            row = dict(self.data.get(key, {}))
            row.update(updates)
            candidate = dict(self.data)
            candidate[key] = row
            write_private(self.path, candidate)
            self.data = candidate
            return dict(row)

    def reset(self, key: str, *, cwd: str | None = None) -> None:
        with self.lock:
            row = self.data.get(key, {})
            candidate = dict(self.data)
            candidate[key] = {"model": row.get("model"),
                              "display_mode": row.get("display_mode", "rich"),
                              "cwd": row.get("cwd", ".") if cwd is None else cwd,
                              "started": row.get("started") is True or bool(row.get("conversation_id"))}
            write_private(self.path, candidate)
            self.data = candidate
