"""Resolve a per-session CWD inside the configured private workspace."""
from __future__ import annotations

import os
import stat
from pathlib import Path


class WorkspaceError(ValueError):
    """A safe user-facing CWD validation failure."""


def resolve_workspace(root: Path, requested: str | None = None) -> tuple[Path, str]:
    """Return a verified CWD and its stable path relative to ``root``.

    Relative inputs are interpreted below the configured workspace. Absolute
    inputs are accepted only when they resolve to that same bounded tree.
    """
    try:
        if root.is_symlink():
            raise WorkspaceError("Configured workspace cannot be a symbolic link.")
        configured = root.resolve(strict=True)
    except OSError:
        raise WorkspaceError("Configured workspace does not exist or cannot be accessed.") from None
    if root.absolute() != configured:
        raise WorkspaceError("Configured workspace cannot be a symbolic link.")
    root = configured
    if requested is not None and not isinstance(requested, str):
        raise WorkspaceError("Saved CWD state is invalid.")
    value = (requested or ".").strip()
    if (not value or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise WorkspaceError("CWD is empty or invalid.")
    raw = Path(value)
    if ".." in raw.parts:
        raise WorkspaceError("CWD cannot contain parent-directory traversal.")
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspaceError("CWD does not exist or cannot be accessed.") from None
    # Reject aliases even when they ultimately point back inside the root. This
    # avoids persisting a path whose target can later be replaced.
    try:
        if candidate.absolute() != resolved:
            raise WorkspaceError("CWD cannot contain symbolic links.")
        resolved.stat()
    except OSError:
        raise WorkspaceError("CWD does not exist or cannot be accessed.") from None
    if not resolved.is_relative_to(root):
        raise WorkspaceError("CWD must stay inside the configured workspace.")
    relative = resolved.relative_to(root)
    current = root
    directories = [root]
    for part in relative.parts:
        current = current / part
        directories.append(current)
    for current in directories:
        try:
            info = current.stat()
        except OSError:
            raise WorkspaceError("CWD does not exist or cannot be accessed.") from None
        if not stat.S_ISDIR(info.st_mode):
            raise WorkspaceError("CWD is not a directory.")
        if info.st_uid != os.getuid():
            raise WorkspaceError("CWD is not owned by the Bot's Termux user.")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise WorkspaceError("CWD has unsafe group or public write permission.")
        if not os.access(current, os.R_OK | os.W_OK | os.X_OK):
            raise WorkspaceError("The Bot lacks read, write or search permission for this CWD.")
    return resolved, "." if not relative.parts else relative.as_posix()
