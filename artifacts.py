"""Explicit, scoped artifact delivery. No directory scanning or arbitrary paths."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_ARTIFACTS = 4
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
_MARKER = re.compile(r"^[ \t]*TAGW_MEDIA:[ \t]*(\{.*\})[ \t]*$")
_FENCE = re.compile(r"^[ ]{0,3}(\x60{3,}|~{3,})")


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: str
    filename: str
    size: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


def _file_signature(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _content_digest(path: Path, expected) -> str:
    """Hash at most the validated size, without loading the entire artifact."""
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as file:
        current = os.fstat(file.fileno())
        if not stat.S_ISREG(current.st_mode) or _file_signature(current) != _file_signature(expected):
            raise OSError("Artifact changed during validation")
        remaining = expected.st_size
        while remaining:
            block = file.read(min(65536, remaining))
            if not block:
                raise OSError("Artifact ended during validation")
            digest.update(block)
            remaining -= len(block)
        if _file_signature(os.fstat(file.fileno())) != _file_signature(expected):
            raise OSError("Artifact changed during validation")
    return digest.hexdigest()


def _kind(name: str, requested: str, size: int) -> str:
    ext = Path(name).suffix.lower()
    if requested not in {"auto", "photo", "document", "audio", "video"}:
        raise ValueError("Unsupported artifact kind")
    if requested == "auto":
        if ext in {".jpg", ".jpeg", ".png", ".webp"} and size <= 10_000_000:
            return "photo"
        if ext in {".mp3", ".m4a"}:
            return "audio"
        if ext in {".mp4"}:
            return "video"
        return "document"
    if requested == "photo" and size > 10_000_000:
        return "document"
    return requested


def _file_for(workspace: Path, name: str, kind: str) -> Artifact:
    if (not name or len(name) > 250 or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("Invalid artifact path")
    rel = Path(name)
    if rel.is_absolute() or not rel.parts or rel.parts[0] != "artifacts":
        raise ValueError("Only the artifacts directory is permitted")
    if any(part in {".", "..", ""} for part in rel.parts):
        raise ValueError("Unsafe artifact path")
    if (workspace / "artifacts").is_symlink():
        raise ValueError("Symlink artifact root is forbidden")
    root = (workspace / "artifacts").resolve(strict=True)
    filename = workspace / rel
    # Forbid symlinks anywhere below the artifact root, even if their target
    # remains inside it. Reject anything outside the root after resolution.
    current = workspace / "artifacts"
    for part in rel.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink artifacts are not allowed")
    target = filename.resolve(strict=True)
    if not target.is_relative_to(root):
        raise ValueError("Artifact path escapes workspace")
    info = target.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
        raise ValueError("Artifact must be a regular file within the size limit")
    if len(target.name) > 120:
        raise ValueError("Artifact name is too long")
    return Artifact(target, _kind(target.name, kind, info.st_size),
                    target.name, info.st_size, info.st_dev, info.st_ino,
                    info.st_mtime_ns, info.st_ctime_ns,
                    _content_digest(target, info))


def extract_artifacts(text: str, workspace: Path) -> tuple[str, list[Artifact]]:
    """Remove real standalone markers, preserve fenced examples, fail closed.

    Invalid/unsafe markers are replaced with a generic warning rather than
    exposing filesystem names or attempting to access unintended paths.
    """
    output: list[str] = []
    found: list[Artifact] = []
    seen: set[Path] = set()
    total = 0
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        match_fence = _FENCE.match(stripped)
        if match_fence:
            seq = match_fence.group(1)
            if fence is None:
                fence = (seq[0], len(seq))
            elif seq[0] == fence[0] and len(seq) >= fence[1] and not stripped[match_fence.end():].strip():
                fence = None
            output.append(line)
            continue
        match = _MARKER.match(stripped) if fence is None else None
        if match is None:
            if fence is None and stripped.lstrip().startswith("TAGW_MEDIA:"):
                output.append("[Artifact omitted: invalid delivery metadata]\n")
            else:
                output.append(line)
            continue
        try:
            obj = json.loads(match.group(1))
            if not isinstance(obj, dict) or not isinstance(obj.get("path"), str):
                raise ValueError("Invalid marker")
            artifact = _file_for(workspace, obj["path"], obj.get("kind", "auto"))
            if artifact.path not in seen:
                if len(found) >= MAX_ARTIFACTS or total + artifact.size > MAX_TOTAL_BYTES:
                    raise ValueError("Artifact count/total size limit exceeded")
                found.append(artifact)
                seen.add(artifact.path)
                total += artifact.size
        except (ValueError, OSError, TypeError):
            output.append("[Artifact omitted: outside allowed directory or size limit]\n")
    return "".join(output).strip(), found
