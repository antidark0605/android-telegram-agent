"""Validate and download a bounded Telegram OGG voice (caller authorizes first)."""
import os
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from incoming_media import _valid_file_id, _remote_path, _check_folder
from telegram_api import TelegramError
LIMIT = 8 * 1024 * 1024

def select_voice(value):
    if not isinstance(value, dict) or not _valid_file_id(value.get("file_id")):
        return None
    n, seconds = value.get("file_size"), value.get("duration")
    if type(n) is not int or not 0 < n <= LIMIT:
        return None
    if type(seconds) is not int or not 0 < seconds <= 120:
        return None
    if value.get("mime_type") not in (None, "audio/ogg"):
        return None
    return {"file_id": value["file_id"], "file_size": n, "duration": seconds}

def download_voice(api, voice, private_dir: Path):
    if select_voice(voice) is None:
        raise ValueError("Invalid voice")
    meta = api._call("getFile", {"file_id": voice["file_id"]}, timeout=15)
    if not isinstance(meta, dict):
        raise ValueError("Invalid Telegram file metadata")
    path = _remote_path(meta.get("file_path"))
    if meta.get("file_size") not in (None, voice["file_size"]):
        raise ValueError("Voice file size mismatch")
    folder = _check_folder(private_dir)
    fd, name = tempfile.mkstemp(prefix="voice-", suffix=".ogg", dir=folder)
    saved = Path(name)
    try:
        os.fchmod(fd, 0o600)
        url = api._file_base + urllib.parse.quote(path, safe="/")
        with os.fdopen(fd, "wb") as out:
            fd = -1
            try:
                with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
                    size = 0
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > LIMIT:
                            raise ValueError("Voice too large")
                        out.write(chunk)
            except (OSError, TimeoutError, urllib.error.URLError):
                raise TelegramError("Voice download failed", kind="transport") from None
        with saved.open("rb") as stream:
            ogg = stream.read(4) == b"OggS"
        if size != voice["file_size"] or not ogg:
            raise ValueError("Invalid OGG voice data")
        return saved
    except Exception:
        saved.unlink(missing_ok=True)
        raise
    finally:
        if fd >= 0:
            os.close(fd)
