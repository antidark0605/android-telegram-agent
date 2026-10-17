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


def parse_quota(document: object) -> dict:
    if not isinstance(document, dict) or document.get('status') != 'SUCCESS':
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
    found = {}
    for group in groups:
        buckets = group.get('buckets') if isinstance(group, dict) else None
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            bid = bucket.get('id')
            expected = {'gemini-5h': '5h', 'gemini-weekly': 'weekly',
                        '3p-5h': '5h', '3p-weekly': 'weekly'}
            if not isinstance(bid, str) or bid not in expected or bucket.get('window') != expected[bid]:
                continue
            fraction = bucket.get('remaining_fraction')
            if type(fraction) not in (int, float) or not 0 <= fraction <= 1 or not math.isfinite(fraction):
                continue
            reset = bucket.get('reset_time')
            try:
                at = datetime.fromisoformat(reset.replace('Z', '+00:00')) if isinstance(reset, str) else None
                if at is None or at.tzinfo is None:
                    continue
                at = at.astimezone(LOCAL_TIMEZONE)
            except (ValueError, OverflowError):
                continue
            if bid in found:  # Ambiguous duplicate data fails closed.
                return {}
            found[bid] = {'left': fraction * 100, 'reset': at}
    return found


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
