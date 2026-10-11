"""On-demand AGY process with bounded execution and strict JSON responses."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    message: str


def _result_object(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    inner = value.get("result")
    if isinstance(inner, dict):
        return inner
    return value


def parse_response(output: str) -> AgentResult:
    """Accept a JSON result object or the terminal result of JSON-lines output."""
    try:
        data = json.loads(output.strip())
    except (json.JSONDecodeError, ValueError):
        records = []
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        terminal = [r for r in records if r.get("event") == "result"]
        if not terminal:
            return AgentResult(False, "AGY returned no valid terminal JSON result.")
        data = terminal[-1]
    if not isinstance(data, dict):
        return AgentResult(False, "AGY returned an unexpected JSON response.")
    result = _result_object(data)
    if result is None:
        return AgentResult(False, "AGY returned an unexpected result object.")
    for obj in (data, result):
        status = str(obj.get("status") or obj.get("subtype") or "").lower()
        if status in ("error", "failed", "failure", "cancelled", "denied"):
            return AgentResult(False, "AGY reported a failed or rejected request.")
        if obj.get("error"):
            return AgentResult(False, "AGY reported an error (details withheld).")
        denied = obj.get("denied_actions") or obj.get("permission_denials")
        if denied:
            return AgentResult(False, "AGY reported denied tool permissions; task not confirmed.")
    response = result.get("response") or result.get("text")
    if not isinstance(response, str):
        candidate = data.get("result")
        response = candidate if isinstance(candidate, str) else None
    if not isinstance(response, str) or not response.strip():
        return AgentResult(False, "AGY completed without a verified model response.")
    return AgentResult(True, response.strip())


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


class AgyRunner:
    def __init__(self, command: str, workspace: Path, timeout: int):
        self.command = command
        self.workspace = workspace
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._cancelled = threading.Event()

    def cancel(self) -> bool:
        with self._lock:
            if self._cancelled.is_set():
                return False
            self._cancelled.set()
            proc = self._process
        if proc is not None:
            _kill_group(proc)
        return True

    def run(self, prompt: str) -> AgentResult:
        if self._cancelled.is_set():
            return AgentResult(False, "AGY task cancelled.")
        argv = [self.command, "-p", prompt, "--output-format", "json"]
        try:
            proc = subprocess.Popen(
                argv, cwd=str(self.workspace), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
                text=True, encoding="utf-8", errors="replace",
            )
        except (OSError, ValueError):
            return AgentResult(False, "Cannot start AGY. Check command and Termux installation.")
        with self._lock:
            self._process = proc
            if self._cancelled.is_set():
                _kill_group(proc)
        try:
            try:
                stdout, _stderr = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return AgentResult(False, "AGY task timed out.")
            if self._cancelled.is_set():
                return AgentResult(False, "AGY task cancelled.")
            if proc.returncode != 0:
                return AgentResult(False, f"AGY exited with error code {proc.returncode}.")
            if len(stdout) > 2_000_000:
                return AgentResult(False, "AGY JSON output exceeded the size limit.")
            return parse_response(stdout)
        finally:
            with self._lock:
                if self._process is proc:
                    self._process = None
