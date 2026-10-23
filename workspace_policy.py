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


def resolve_or_create_workspace(root: Path,
                                requested: str | None = None) -> tuple[Path, str]:
    """Resolve a safe workspace, creating a missing requested directory tree."""
    configured, _relative = resolve_workspace(root)
    if requested is None:
        return configured, "."
    if not isinstance(requested, str):
        raise WorkspaceError("Saved CWD state is invalid.")
    value = requested.strip()
    if (len(value) >= 2 and value[0] == value[-1]
            and value[0] in {"'", '"'}):
        value = value[1:-1].strip()
    if (not value or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise WorkspaceError("CWD is empty or invalid.")
    raw = Path(value)
    if ".." in raw.parts:
        raise WorkspaceError("CWD cannot contain parent-directory traversal.")
    candidate = raw if raw.is_absolute() else configured / raw
    candidate = candidate.absolute()
    if not candidate.is_relative_to(configured):
        raise WorkspaceError("CWD must stay inside the configured workspace.")

    missing: list[Path] = []
    current = candidate
    while not current.exists() and not current.is_symlink():
        if current == configured:
            break
        missing.append(current)
        current = current.parent
    # Validate the nearest existing ancestor before writing anything.
    resolve_workspace(configured, str(current))
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass  # Revalidation below rejects files and symlinks.
        except PermissionError:
            raise WorkspaceError(
                "The Bot lacks permission to create this CWD."
            ) from None
        except OSError:
            raise WorkspaceError("CWD could not be created.") from None
        # mode 0700 combined with the process umask cannot add public bits;
        # revalidation also catches a file or symlink created in a race.
        resolve_workspace(configured, str(directory))
    return resolve_workspace(configured, str(candidate))


def resolve_list_directory(workspace: Path, requested: str | None = None) -> Path:
    """Resolve an accessible ``/ls`` directory from the current Topic CWD.

    Relative targets start at the current Topic CWD. Absolute paths, parent
    traversal and symlinks are allowed for this read-only command; the resolved
    target must exist, be a directory and be readable/searchable by the Bot's
    operating-system user. Session and artifact paths use stricter resolvers.
    """
    try:
        current = workspace.resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspaceError("Current Topic CWD does not exist or cannot be accessed.") from None
    value = (requested or ".").strip()
    if (len(value) >= 2 and value[0] == value[-1]
            and value[0] in {"'", '"'}):
        value = value[1:-1].strip()
    if (not value or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise WorkspaceError("Path is empty or invalid.")
    try:
        raw = Path(value).expanduser()
    except RuntimeError:
        raise WorkspaceError("Path cannot be expanded for the Bot user.") from None
    candidate = raw if raw.is_absolute() else current / raw
    try:
        target = candidate.resolve(strict=True)
        info = target.stat()
    except PermissionError:
        raise WorkspaceError("The Bot lacks permission to access this path.") from None
    except (OSError, RuntimeError):
        raise WorkspaceError("Path does not exist or cannot be accessed.") from None
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError("Path is not a directory.")
    if not os.access(target, os.R_OK | os.X_OK):
        raise WorkspaceError("The Bot lacks permission to read or search this directory.")
    return target
