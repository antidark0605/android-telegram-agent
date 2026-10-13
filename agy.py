"""Official AGY CLI adapter, one on-demand process group per task."""
from __future__ import annotations
import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

MODEL_SLUG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9.]+){1,8}")
CONVERSATION_ID = re.compile(r"[a-zA-Z0-9_-]{8,128}")

@dataclass(frozen=True)
class AgentResult:
    ok: bool
    message: str
    conversation_id: str | None = None
    usage: dict = field(default_factory=dict)
    actual_model: str | None = None

def parse_response(output: str) -> AgentResult:
    try:
        obj = json.loads(output.strip())
    except ValueError:
        # Terminal event only: ignore partial intermediary narration.
        events = []
        for line in output.splitlines():
            try:
                item = json.loads(line)
                if isinstance(item, dict) and item.get("event") == "result":
                    events.append(item)
            except ValueError:
                pass
        obj = events[-1] if events else None
    if not isinstance(obj, dict):
        return AgentResult(False, "AGY returned no terminal JSON result.")
    inner = obj.get("result")
    result = inner if isinstance(inner, dict) else obj
    if not isinstance(result, dict):
        return AgentResult(False, "AGY returned an unexpected result object.")
    statuses = [d["status"] for d in (obj, result) if "status" in d]
    if not statuses or any(str(v).upper() != "SUCCESS" for v in statuses):
        return AgentResult(False, "AGY did not report a successful terminal status.")
    for d in (obj, result):
        if d.get("error") or d.get("is_error"):
            return AgentResult(False, "AGY reported an error.")
        if d.get("denied_actions") or d.get("permission_denials"):
            return AgentResult(False, "AGY reported denied tool permissions; task not confirmed.")
    response = result.get("response")
    if not isinstance(response, str) or not response.strip():
        return AgentResult(False, "AGY completed without a verified model response.")
    cid = result.get("conversation_id")
    if not isinstance(cid, str) or not CONVERSATION_ID.fullmatch(cid):
        cid = None
    usage = result.get("usage")
    model = result.get("model")
    return AgentResult(
        True, response.strip(), cid,
        {k: v for k, v in usage.items() if k in {
            "input_tokens", "output_tokens", "thinking_tokens",
            "cache_read_tokens", "total_tokens"} and type(v) is int and v >= 0}
        if isinstance(usage, dict) else {},
        model if isinstance(model, str) and len(model) < 90 else None,
    )

def _kill_group(proc: subprocess.Popen) -> None:
    # Child starts its own process group; cleanup happens even if leader exited.
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
    def __init__(self, command: str, workspace: Path, timeout: int | None,
                 print_timeout: str = "24h"):
        self.command, self.workspace = command, workspace
        self.timeout, self.print_timeout = timeout, print_timeout
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

    def run(self, prompt: str, model: str | None = None,
            conversation_id: str | None = None, yolo: bool = False) -> AgentResult:
        if self._cancelled.is_set():
            return AgentResult(False, "AGY task cancelled.")
        argv = [self.command, "-p", prompt, "--output-format", "json",
                "--print-timeout", self.print_timeout]
        if model:
            if not MODEL_SLUG.fullmatch(model):
                return AgentResult(False, "Invalid model selector.")
            argv.extend(["--model", model])
        if conversation_id:
            if not CONVERSATION_ID.fullmatch(conversation_id):
                return AgentResult(False, "Invalid conversation identifier.")
            argv.extend(["--conversation", conversation_id])
        if yolo:
            argv.append("--dangerously-skip-permissions")
        try:
            proc = subprocess.Popen(
                argv, cwd=str(self.workspace), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                start_new_session=True, text=True, encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return AgentResult(False, "Cannot start AGY; check Termux installation.")
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
            if proc.returncode:
                return AgentResult(False, f"AGY exited with error code {proc.returncode}.")
            if len(stdout) > 2_000_000:
                return AgentResult(False, "AGY JSON output exceeded size limit.")
            return parse_response(stdout)
        finally:
            _kill_group(proc)
            with self._lock:
                if self._process is proc:
                    self._process = None

def parse_models(output: str) -> list[tuple[str, str]]:
    found = []
    for line in output.splitlines():
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        match = re.match(
            r"^(?:[-*•]\s*)?([a-z][a-z0-9]*(?:-[a-z0-9.]+){1,8})\s{2,}(.+?)\s*$", clean)
        if match and MODEL_SLUG.fullmatch(match[1]) and match[1] not in {m[0] for m in found}:
            found.append((match[1], match[2][:80]))
    return found[:40]

def list_models(command: str, workspace: Path) -> list[tuple[str, str]]:
    try:
        proc = subprocess.run([command, "models"], cwd=str(workspace),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=15, check=False)
        return parse_models(proc.stdout) if proc.returncode == 0 and len(proc.stdout) <= 100_000 else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []

def usage_report(command: str, workspace: Path) -> str | None:
    """Standalone AGY built-in /usage report, not a made-up context percentage."""
    try:
        proc = subprocess.run([command, "-p", "/usage", "--output-format", "json",
                               "--print-timeout", "30s"],
                              cwd=str(workspace), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=45,
                              encoding="utf-8", errors="replace")
        if proc.returncode or len(proc.stdout) > 100_000:
            return None
        obj = json.loads(proc.stdout)
        report = obj.get("response") if isinstance(obj, dict) else None
        if not isinstance(report, str) or not report.strip():
            return None
        # Do not forward account emails, authorization strings, or file paths.
        report = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[account]", report)
        return report.strip()[:2500]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
