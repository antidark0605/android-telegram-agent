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
            data = json.loads(path.read_text(encoding="utf8"))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> dict:
        with self.lock:
            return dict(self.data.get(key, {}))

    def change(self, key: str, **updates) -> dict:
        with self.lock:
            row = dict(self.data.get(key, {}))
            row.update(updates)
            self.data[key] = row
            write_private(self.path, self.data)
            return dict(row)

    def reset(self, key: str) -> None:
        with self.lock:
            row = self.data.get(key, {})
            self.data[key] = {"model": row.get("model")}
            write_private(self.path, self.data)
