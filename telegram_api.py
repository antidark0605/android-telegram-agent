"""Tiny Telegram Bot API client, using only Python's standard library."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class TelegramError(RuntimeError):
    """Deliberately never includes a URL, token, or Telegram response body."""


class TelegramAPI:
    def __init__(self, token: str):
        self._base = "https://api.telegram.org/bot" + token + "/"

    def _call(self, method: str, payload: dict, timeout: int = 40):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._base + method,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read(10_000_001)
        except urllib.error.HTTPError as exc:
            raise TelegramError(f"Telegram API HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise TelegramError("Telegram connection unavailable") from None
        if len(raw) > 10_000_000:
            raise TelegramError("Telegram API response was too large")
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise TelegramError("Invalid Telegram API response") from None
        if not isinstance(obj, dict) or not obj.get("ok"):
            code = obj.get("error_code") if isinstance(obj, dict) else None
            raise TelegramError(f"Telegram API rejected request ({code or 'unknown'})")
        return obj.get("result")

    def get_updates(self, offset: int, timeout: int = 20) -> list[dict]:
        result = self._call("getUpdates", {
            "offset": offset, "timeout": timeout, "allowed_updates": ["message"]
        }, timeout=timeout + 15)
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid updates list")
        return result

    def send_text(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        # sendMessage's text limit is 4096 characters, not bytes.
        for part in split_text(text):
            data = {"chat_id": chat_id, "text": part,
                    "link_preview_options": {"is_disabled": True}}
            if thread_id is not None:
                data["message_thread_id"] = thread_id
            self._call("sendMessage", data, timeout=25)


def split_text(text: str, limit: int = 3800) -> list[str]:
    if not text:
        return ["(empty response)"]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        else:
            split_at += 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return chunks
