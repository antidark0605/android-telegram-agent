"""Allowlisted official CLI quota data with a short cache; see presentation.py."""
from __future__ import annotations

import json
import math
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

LOCAL_TIMEZONE = timezone(timedelta(hours=8))
CACHE_SECONDS = 60
GROUP_FAMILIES = {
    'gemini models': 'gemini',
    'gemini': 'gemini',
    'claude and gpt models': '3p',
    'claude and gpt': '3p',
    'gemini 4 argon': 'argon',
}
WINDOWS = {'5h': '5h', 'weekly': 'weekly'}


def _reset_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.timestamp() < 0:
            return None
        return parsed.astimezone(LOCAL_TIMEZONE)
    except (ValueError, OverflowError, OSError):
        return None


def _quota_row(bucket: object) -> tuple[str, dict] | None:
    if not isinstance(bucket, dict):
        return None
    window = bucket.get('window')
    key = WINDOWS.get(window) if isinstance(window, str) else None
    fraction = bucket.get('remaining_fraction')
    if (key is None or type(fraction) not in (int, float)
            or not 0 <= fraction <= 1 or not math.isfinite(fraction)):
        return None
    return key, {'left': fraction * 100,
                 'reset': _reset_time(bucket.get('reset_time'))}


def parse_quota(document: object) -> dict:
    if not isinstance(document, dict) or document.get('status') != 'SUCCESS':
        return {}
    if type(document.get('num_turns')) is not int or document['num_turns'] != 0:
        return {}
    if any(document.get(k) for k in ('error', 'is_error', 'denied_actions', 'permission_denials')):
        return {}
    command = document.get('command')
    if not isinstance(command, dict) or command.get('name') != 'usage':
        return {}
    data = command.get('data')
    groups = data.get('groups') if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return {}
    found: dict[str, dict] = {}
    recognized_group = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = group.get('name')
        family = GROUP_FAMILIES.get(name.strip().lower()) if isinstance(name, str) else None
        buckets = group.get('buckets')
        if family is None or not isinstance(buckets, list):
            continue
        recognized_group = True
        for bucket in buckets:
            row = _quota_row(bucket)
            if row is None:
                continue
            window, value = row
            key = f'{family}-{window}'
            if key in found:  # Ambiguous duplicate family/window fails closed.
                return {}
            found[key] = value
    return found if recognized_group else {}


class QuotaService:
    def __init__(self, command, workspace):
        self.command, self.workspace = command, workspace
        self.lock = threading.Lock()
        self.last_fetch = None
        self.data = {}
        self.at = None

    def get(self):
        with self.lock:
            now = time.monotonic()
            cached = self.last_fetch is not None and now - self.last_fetch < CACHE_SECONDS
            if not cached:
                self.data = {}
                try:
                    result = subprocess.run(
                        [self.command, '-p', '/usage', '--output-format', 'json',
                         '--print-timeout', '30s'], cwd=self.workspace,
                        stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=45, check=False)
                    if result.returncode == 0 and len(result.stdout) <= 100000:
                        self.data = parse_quota(json.loads(result.stdout))
                except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired):
                    pass
                self.last_fetch = time.monotonic()
                self.at = datetime.now(LOCAL_TIMEZONE)
            return dict(self.data), self.at, cached
