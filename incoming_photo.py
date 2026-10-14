"""Authorized Telegram photos stored briefly in a private task directory."""
from __future__ import annotations

import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from telegram_api import TelegramError

MAX_PHOTO_BYTES = 10 * 1024 * 1024


def select_photo(value):
    if not isinstance(value, list):
        return None
    valid = [p for p in value if isinstance(p, dict)
             and isinstance(p.get('file_id'), str) and 0 < len(p['file_id']) <= 512
             and type(p.get('width')) is int and p['width'] > 0
             and type(p.get('height')) is int and p['height'] > 0]
    return max(valid, key=lambda p: p['width'] * p['height']) if valid else None


def download_photo(api, photo, workspace: Path, cancelled=None) -> Path:
    deadline = time.monotonic() + 60
    def check_running():
        if cancelled is not None and cancelled.is_set():
            raise ValueError('Photo task was stopped')
        if time.monotonic() >= deadline:
            raise ValueError('Photo download timed out')
    check_running()
    declared = photo.get('file_size')
    if declared is not None and (type(declared) is not int or not 0 < declared <= MAX_PHOTO_BYTES):
        raise ValueError('Photo exceeds the supported size')
    info = api._call('getFile', {'file_id': photo['file_id']}, timeout=15)
    if not isinstance(info, dict):
        raise TelegramError('Invalid photo download metadata', kind='protocol')
    remote = info.get('file_path')
    if (not isinstance(remote, str) or len(remote) > 512
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', remote)
            or PurePosixPath(remote).is_absolute()
            or any(p in ('', '.', '..') for p in remote.split('/'))):
        raise TelegramError('Unsafe photo download metadata', kind='protocol')
    size = info.get('file_size')
    if size is not None and (type(size) is not int or not 0 < size <= MAX_PHOTO_BYTES):
        raise ValueError('Photo exceeds the supported size')
    check_running()
    folder = workspace / 'incoming-images'
    if folder.is_symlink():
        raise ValueError('Unsafe photo directory')
    folder.mkdir(mode=0o700, exist_ok=True)
    stat = folder.stat()
    if (not folder.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o022
            or not folder.resolve().is_relative_to(workspace.resolve())):
        raise ValueError('Unsafe photo directory')
    fd, name = tempfile.mkstemp(prefix='photo-', dir=folder)
    saved = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as out:
            req = urllib.request.Request(api._file_base + urllib.parse.quote(remote, safe='/'))
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    total = 0
                    prefix = b''
                    while True:
                        check_running()
                        block = response.read1(min(65536, MAX_PHOTO_BYTES + 1 - total))
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_PHOTO_BYTES:
                            raise ValueError('Photo exceeds the supported size')
                        prefix = (prefix + block)[:16]
                        out.write(block)
            except urllib.error.HTTPError as exc:
                raise TelegramError('Photo download rejected', kind='http', http_status=exc.code) from None
            except (OSError, urllib.error.URLError, TimeoutError):
                raise TelegramError('Photo download unavailable', kind='transport') from None
        check_running()
        if total == 0 or (size is not None and total != size):
            raise ValueError('Photo download was incomplete')
        suffix = '.png' if prefix.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if prefix.startswith(b'\xff\xd8\xff') else None
        if suffix is None:
            raise ValueError('Unsupported photo format')
        final = saved.with_suffix(suffix)
        saved.rename(final)
        return final
    except Exception:
        saved.unlink(missing_ok=True)
        raise
