"""Shared Telegram inbound authorization rules.

A user/chat pair is required for every task, command and callback. During
explicit onboarding, an unlisted private chat may request *only its own IDs*.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class TelegramAccess:
    allowed_pairs: FrozenSet[tuple[int, int]]
    allow_unlisted_myid: bool = False

    def is_allowed(self, user_id: object, chat_id: object) -> bool:
        return (type(user_id) is int and type(chat_id) is int
                and (user_id, chat_id) in self.allowed_pairs)

    def may_discover_ids(self, user_id: object, chat_id: object,
                         chat_type: object, is_bot: object = False) -> bool:
        if self.is_allowed(user_id, chat_id):
            return True
        return (
            self.allow_unlisted_myid
            and type(user_id) is int and type(chat_id) is int
            and user_id > 0 and chat_id == user_id
            and chat_type == "private"
            and is_bot is not True
        )


def identity_reply(user_id: int, chat_id: int, chat_type: str,
                   thread_id: int | None) -> str:
    """Only caller's Telegram-provided IDs, never config or other users."""
    return (
        "🆔 Telegram IDs\n"
        f"chat_id: {chat_id}\n"
        f"chat_type: {chat_type}\n"
        f"user_id: {user_id}\n"
        f"thread_id: {thread_id if thread_id is not None else 'none'}"
    )
