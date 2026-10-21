"""Tiny Telegram Bot API client, using only Python's standard library."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request

from vendor.telegram_ui import split_text as ui_split_text
from vendor.telegram_ui import utf16_length

# Conservative cap on the raw formatted string, markup included.
FORMATTED_TEXT_LIMIT = 4096


class TelegramError(RuntimeError):
    """Only safe categorical metadata; never include URL, token or response body."""

    def __init__(self, message: str, *, kind: str = "unknown",
                 http_status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.retry_after = retry_after


class TelegramAPI:
    def __init__(self, token: str):
        self._base = "https://api.telegram.org/bot" + token + "/"
        self._file_base = "https://api.telegram.org/file/bot" + token + "/"

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
            retry_after = _retry_after_from_bytes(exc.read(100_000))
            raise TelegramError("Telegram HTTP failure", kind="http", http_status=exc.code,
                                retry_after=retry_after) from None
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
            retry_after = _retry_after_from_object(obj)
            raise TelegramError("Telegram API rejected request", kind="api",
                                http_status=code if type(code) is int else None,
                                retry_after=retry_after)
        return obj.get("result")

    def get_updates(self, offset: int, timeout: int = 20) -> list[dict]:
        result = self._call("getUpdates", {
            "offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]
        }, timeout=timeout + 15)
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid updates list", kind="protocol")
        return result

    def get_me(self) -> dict:
        """Confirm that the credential belongs to a Bot without exposing identity."""
        result = self._call("getMe", {}, timeout=10)
        if not isinstance(result, dict) or result.get("is_bot") is not True:
            raise TelegramError("Telegram returned an invalid Bot identity",
                                kind="protocol")
        return result

    @staticmethod
    def _command_scope(scope: str | None) -> dict:
        allowed = {"all_private_chats", "all_group_chats",
                   "all_chat_administrators"}
        if scope is None:
            return {}
        if scope not in allowed:
            raise TelegramError("Unsupported Telegram command scope",
                                kind="protocol")
        return {"scope": {"type": scope}}

    def get_my_commands(self, scope: str | None = None) -> list[dict[str, object]]:
        """Read a language-neutral command menu for one bounded scope."""
        result = self._call("getMyCommands", self._command_scope(scope), timeout=10)
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid command list",
                                kind="protocol")
        commands = []
        for item in result:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("command"), str)
                    or not isinstance(item.get("description"), str)):
                raise TelegramError("Telegram returned an invalid command entry",
                                    kind="protocol")
            command = {"command": item["command"],
                       "description": item["description"]}
            ephemeral = item.get("is_ephemeral", False)
            if type(ephemeral) is not bool:
                raise TelegramError("Telegram returned an invalid command entry",
                                    kind="protocol")
            if ephemeral:
                command["is_ephemeral"] = True
            commands.append(command)
        return commands

    def delete_my_commands(self, scope: str) -> None:
        """Clear a category override so it inherits the canonical default menu."""
        if self._call("deleteMyCommands", self._command_scope(scope), timeout=10) is not True:
            raise TelegramError("Telegram did not confirm command-menu deletion",
                                kind="protocol")

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        """Replace the default, language-neutral command menu."""
        if not isinstance(commands, list) or len(commands) > 100:
            raise TelegramError("Invalid Telegram command payload", kind="protocol")
        for item in commands:
            if (not isinstance(item, dict)
                    or set(item) != {"command", "description"}
                    or not isinstance(item["command"], str)
                    or not isinstance(item["description"], str)
                    or re.fullmatch(r"[a-z0-9_]{1,32}", item["command"]) is None
                    or not 1 <= len(item["description"]) <= 256):
                raise TelegramError("Invalid Telegram command payload", kind="protocol")
        if self._call("setMyCommands", {"commands": commands}, timeout=10) is not True:
            raise TelegramError("Telegram did not confirm the command menu",
                                kind="protocol")

    def send_text(self, chat_id: int, text: str, thread_id: int | None = None,
                  reply_markup: dict | None = None,
                  parse_mode: str | None = None) -> int | None:
        """Send chunks, attaching buttons only to the first; return first message ID.

        Formatted text (parse_mode) is a short status sent whole: splitting
        could cut HTML tags apart, so oversized formatted text is rejected.
        """
        first_id = None
        if parse_mode:
            parts = [_check_formatted(text or "(empty response)")]
        else:
            parts = split_text(text)
        for index, part in enumerate(parts):
            data = {"chat_id": chat_id, "text": part,
                    "link_preview_options": {"is_disabled": True}}
            if parse_mode:
                data["parse_mode"] = parse_mode
            if thread_id is not None:
                data["message_thread_id"] = thread_id
            if reply_markup is not None and index == 0:
                data["reply_markup"] = reply_markup
            result = self._call("sendMessage", data, timeout=15)
            if index == 0 and isinstance(result, dict):
                mid = result.get("message_id")
                first_id = mid if isinstance(mid, int) else None
        return first_id

    def send_classic(self, chat_id: int, text: str, thread_id: int | None = None,
                     artifacts=None) -> int | None:
        """Send unparsed UTF-16-safe text, optionally followed by media.

        The artifact argument is intentionally handled by RichDelivery so the
        same bounded multipart implementation is used for classic and rich
        modes.  No Markdown is passed to Telegram with a missing parse mode.
        """
        if artifacts:
            from rich_delivery import RichDelivery
            return RichDelivery(self).send_classic(chat_id, thread_id, text, artifacts)
        return self.send_text(chat_id, text, thread_id)

    def send_rich(self, chat_id: int, markdown: str, thread_id: int | None = None,
                  artifacts=None) -> int | None:
        """Deliver formatted Markdown and optionally stream declared media."""
        from rich_delivery import RichDelivery
        return RichDelivery(self).send(chat_id, thread_id, markdown, artifacts)

    def edit_text(self, chat_id: int, message_id: int, text: str,
                  reply_markup: dict | None = None,
                  parse_mode: str | None = None) -> None:
        if parse_mode:
            _check_formatted(text)
        data = {"chat_id": chat_id, "message_id": message_id,
                "text": text, "reply_markup": reply_markup or {"inline_keyboard": []}}
        if parse_mode:
            data["parse_mode"] = parse_mode
        self._call("editMessageText", data, timeout=10)

    def answer_callback(self, query_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {
            "callback_query_id": query_id, "text": text[:180],
            "show_alert": False,
        }, timeout=10)

    def send_chat_action(self, chat_id: int, action: str = "typing",
                         thread_id: int | None = None) -> None:
        """Publish one bounded activity indication to the current chat/topic."""
        if action != "typing":
            raise TelegramError("Unsupported chat action", kind="protocol")
        data = {"chat_id": chat_id, "action": action}
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if self._call("sendChatAction", data, timeout=10) is not True:
            raise TelegramError("Telegram did not confirm chat action",
                                kind="protocol")


def _check_formatted(text: str) -> str:
    """Reject formatted text over FORMATTED_TEXT_LIMIT UTF-16 units; never split it."""
    if utf16_length(text) > FORMATTED_TEXT_LIMIT:
        raise TelegramError("Formatted Telegram message was too long", kind="protocol")
    return text


def split_text(text: str, limit: int = 3800) -> list[str]:
    """Plain-text chunks of at most `limit` UTF-16 units; keeps every character.

    Markdown, code fences and tables spanning a boundary are not repaired.
    """
    if not text:
        return ["(empty response)"]
    return ui_split_text(text, limit)


def _retry_after_from_object(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    params = value.get("parameters")
    if not isinstance(params, dict):
        return None
    delay = params.get("retry_after")
    return delay if type(delay) is int and 0 <= delay <= 3600 else None


def _retry_after_from_bytes(raw: bytes) -> int | None:
    try:
        import json
        return _retry_after_from_object(json.loads(raw))
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
