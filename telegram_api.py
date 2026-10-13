"""Tiny Telegram Bot API client, using only Python's standard library."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


class TelegramError(RuntimeError):
    """Only safe categorical metadata; never include URL, token or response body."""

    def __init__(self, message: str, *, kind: str = "unknown",
                 http_status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


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
            raise TelegramError("Telegram HTTP failure", kind="http", http_status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise TelegramError("Telegram connection unavailable", kind="transport") from None
        if len(raw) > 10_000_000:
            raise TelegramError("Telegram API response was too large", kind="protocol")
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise TelegramError("Invalid Telegram API response", kind="protocol") from None
        if not isinstance(obj, dict) or not obj.get("ok"):
            code = obj.get("error_code") if isinstance(obj, dict) else None
            raise TelegramError("Telegram API rejected request", kind="api",
                                http_status=code if type(code) is int else None)
        return obj.get("result")

    def get_updates(self, offset: int, timeout: int = 20) -> list[dict]:
        result = self._call("getUpdates", {
            "offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]
        }, timeout=timeout + 15)
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid updates list", kind="protocol")
        return result

    def send_text(self, chat_id: int, text: str, thread_id: int | None = None,
                  reply_markup: dict | None = None) -> int | None:
        """Send chunks, attaching buttons only to the first; return first message ID."""
        first_id = None
        for index, part in enumerate(split_text(text)):
            data = {"chat_id": chat_id, "text": part,
                    "link_preview_options": {"is_disabled": True}}
            if thread_id is not None:
                data["message_thread_id"] = thread_id
            if reply_markup is not None and index == 0:
                data["reply_markup"] = reply_markup
            result = self._call("sendMessage", data, timeout=15)
            if index == 0 and isinstance(result, dict):
                mid = result.get("message_id")
                first_id = mid if isinstance(mid, int) else None
        return first_id

    def edit_text(self, chat_id: int, message_id: int, text: str,
                  reply_markup: dict | None = None) -> None:
        self._call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": text, "reply_markup": reply_markup or {"inline_keyboard": []},
        }, timeout=10)

    def answer_callback(self, query_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {
            "callback_query_id": query_id, "text": text[:180],
            "show_alert": False,
        }, timeout=10)


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
