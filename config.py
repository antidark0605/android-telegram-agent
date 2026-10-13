"""Validated, private runtime settings. No device-specific defaults."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Invalid or unsafe local configuration."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_pairs: frozenset[tuple[int, int]]
    agy_command: str
    workspace: Path
    task_timeout_seconds: int | None
    config_path: Path
    yolo: bool = False
    print_timeout: str = "24h"

    @property
    def state_path(self) -> Path:
        return self.config_path.parent / "telegram-offset.json"


def _integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ConfigError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ConfigError(f"{field} outside supported range")
    return result


def load_settings(filename: str | Path) -> Settings:
    candidate_path = Path(filename).expanduser()
    if candidate_path.is_symlink():
        raise ConfigError("configuration must not be a symbolic link")
    path = candidate_path.resolve()
    project_dir = Path(__file__).resolve().parent
    if path.is_relative_to(project_dir):
        raise ConfigError("runtime configuration must be outside the source checkout")
    if not path.is_file():
        raise ConfigError("configuration file not found")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError("configuration must be owned by this user and chmod 600")
    if path.parent.stat().st_mode & stat.S_IWOTH:
        raise ConfigError("configuration directory must not be world-writable")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ConfigError("invalid JSON configuration") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration must be an object")
    token = raw.get("bot_token")
    if (not isinstance(token, str) or len(token) < 12
            or token.startswith("YOUR_") or any(ch.isspace() for ch in token)):
        raise ConfigError("bot_token must be a real, nonempty Telegram token")
    pairs = raw.get("allowed_pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ConfigError("allowed_pairs must contain at least one user/chat pair")
    allowed: set[tuple[int, int]] = set()
    for item in pairs:
        if not isinstance(item, dict):
            raise ConfigError("invalid allowed_pairs entry")
        uid = _integer(item.get("user_id"), "user_id", minimum=1, maximum=2**63 - 1)
        chat = _integer(item.get("chat_id"), "chat_id", minimum=-(2**63 - 1), maximum=2**63 - 1)
        if chat == 0:
            raise ConfigError("chat_id cannot be zero")
        allowed.add((uid, chat))
    command = raw.get("agy_command", "agy")
    if (not isinstance(command, str) or not command or command.startswith("-")
            or "\x00" in command or any(c.isspace() for c in command)):
        raise ConfigError("agy_command must be a single executable name or path")
    workspace = raw.get("workspace")
    if not isinstance(workspace, str) or not workspace or workspace.startswith("YOUR_"):
        raise ConfigError("workspace must be an existing absolute private directory")
    work = Path(workspace).expanduser().resolve()
    home = Path.home().resolve()
    if (not Path(workspace).expanduser().is_absolute()
            or not work.is_dir() or work == home
            or not work.is_relative_to(home)):
        raise ConfigError("workspace must be an existing subdirectory of the Termux home")
    if path.is_relative_to(work) or work.is_relative_to(path.parent):
        raise ConfigError("workspace and configuration must be separate")
    if work.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ConfigError("workspace must not be group/world writable")
    timeout_raw = raw.get("task_timeout_seconds", None)
    timeout = None if timeout_raw is None else _integer(
        timeout_raw, "task_timeout_seconds", minimum=15, maximum=86400)
    yolo = raw.get("yolo", False)
    if type(yolo) is not bool:
        raise ConfigError("yolo must be a boolean")
    print_timeout = raw.get("print_timeout", "24h")
    if (not isinstance(print_timeout, str)
            or not __import__("re").fullmatch(r"[1-9][0-9]{0,4}[smh]", print_timeout)):
        raise ConfigError("print_timeout must be a positive duration like 24h")
    return Settings(token, frozenset(allowed), command, work, timeout, path, yolo, print_timeout)
