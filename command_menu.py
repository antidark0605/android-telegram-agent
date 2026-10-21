"""Canonical Telegram slash-command menu for the implemented bot routes."""
from __future__ import annotations


BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Show available commands"),
    ("help", "Show available commands"),
    ("myid", "Show this user, chat and topic ID"),
    ("session", "Activate: /session start [CWD]"),
    ("new", "Start a new AGY conversation for this topic"),
    ("model", "Choose an explicit AGY model"),
    ("format", "Choose rich, classic or raw replies"),
    ("last", "Resend this topic's last saved reply"),
    ("ls", "List workspace files with ls -l"),
    ("status", "Show session and task status"),
    ("context", "Show last task token accounting"),
    ("quota", "Show AGY usage limits when available"),
    ("usage", "Alias for /quota"),
)

# These category scopes override the default menu when populated. This bot has
# one command surface, so they remain empty and inherit the canonical default.
INHERITED_COMMAND_SCOPES = (
    "all_private_chats",
    "all_group_chats",
    "all_chat_administrators",
)


def telegram_commands() -> list[dict[str, str]]:
    """Return a fresh Bot API payload so callers cannot mutate the canonical data."""
    return [{"command": command, "description": description}
            for command, description in BOT_COMMANDS]
