"""Minimal non-root Telegram ↔ AGY CLI bridge for Android Termux."""
from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import threading
import time
from pathlib import Path

from agy import AgyRunner
from config import ConfigError, Settings, load_settings
from telegram_api import TelegramAPI, TelegramError


class OffsetStore:
    """Persist update offset to avoid replaying prompts after process restart."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("next_offset")
            return value if isinstance(value, int) and value >= 0 else None
        except (OSError, ValueError, AttributeError):
            return None

    def write(self, value: int) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".offset-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({"next_offset": value}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class TelegramAgyBot:
    def __init__(self, settings: Settings, api: TelegramAPI):
        self.settings = settings
        self.api = api
        self.state = OffsetStore(settings.state_path)
        self._lock = threading.Lock()
        self._runner: AgyRunner | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    def _reply(self, chat: int, text: str, thread_id: int | None = None) -> None:
        try:
            self.api.send_text(chat, text, thread_id)
        except TelegramError:
            # No raw URLs, bot tokens, prompts, device paths, or Telegram IDs in logs.
            print("Telegram send failed; check network/bot permission.", flush=True)

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return
        user_id, chat_id = sender.get("id"), chat.get("id")
        if (not isinstance(user_id, int) or not isinstance(chat_id, int)
                or (user_id, chat_id) not in self.settings.allowed_pairs):
            return  # Unauthorized: no reply, subprocess, or sensitive inspection.
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        thread_id = message.get("message_thread_id")
        if not isinstance(thread_id, int):
            thread_id = None
        stripped = text.strip()
        cmd = stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in ("/start", "/help"):
            self._reply(chat_id, "Android Telegram Agent (AGY only)\n"
                        "Send text to ask AGY.\n/start /help /status /cancel\n"
                        "Sessions and model selection are planned for a later version.",
                        thread_id)
        elif cmd == "/status":
            with self._lock:
                busy = self._worker is not None and self._worker.is_alive()
            self._reply(chat_id, "AGY task: busy" if busy else "AGY task: idle", thread_id)
        elif cmd == "/cancel":
            with self._lock:
                runner = self._runner
            if runner is not None and runner.cancel():
                self._reply(chat_id, "Cancellation requested.", thread_id)
            else:
                self._reply(chat_id, "No running task.", thread_id)
        elif stripped.startswith("/"):
            self._reply(chat_id, "Unknown command. Try /help.", thread_id)
        elif len(stripped) > 4000:
            self._reply(chat_id, "Prompt exceeds 4000 characters.", thread_id)
        else:
            self._start_task(stripped, chat_id, thread_id)

    def _start_task(self, prompt: str, chat_id: int, thread_id: int | None) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                busy = True
            else:
                busy = False
                runner = AgyRunner(self.settings.agy_command, self.settings.workspace,
                                   self.settings.task_timeout_seconds)
                worker = threading.Thread(
                    target=self._execute, args=(runner, prompt, chat_id, thread_id),
                    daemon=True, name="agy-task",
                )
                self._runner = runner
                self._worker = worker
                worker.start()
        if busy:
            self._reply(chat_id, "AGY is busy. Use /cancel or retry later.", thread_id)

    def _execute(self, runner: AgyRunner, prompt: str, chat_id: int,
                 thread_id: int | None) -> None:
        try:
            outcome = runner.run(prompt)
            self._reply(chat_id, outcome.message if outcome.ok else "Task failed: " + outcome.message,
                        thread_id)
        except Exception:
            self._reply(chat_id, "Task failed unexpectedly; no private details were shared.",
                        thread_id)
        finally:
            with self._lock:
                if self._runner is runner:
                    self._runner = None
                    self._worker = None

    def run(self) -> None:
        offset = self.state.read()
        # On the very first boot, discard existing updates rather than run stale commands.
        if offset is None:
            last = self.api.get_updates(-1, timeout=0)
            offset = max((u["update_id"] for u in last if isinstance(u.get("update_id"), int)),
                         default=-1) + 1
            self.state.write(offset)
        print("Telegram polling started (AGY on demand; no inbound port).", flush=True)
        backoff = 2
        try:
            while not self._stop.is_set():
                try:
                    updates = self.api.get_updates(offset)
                    backoff = 2
                    for item in updates:
                        if not isinstance(item, dict):
                            continue
                        update_id = item.get("update_id")
                        if not isinstance(update_id, int) or update_id < offset:
                            continue
                        try:
                            self.handle_update(item)
                        finally:
                            offset = update_id + 1
                            self.state.write(offset)
                except (TelegramError, OSError):
                    print("Telegram polling/network error; retrying.", flush=True)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30)
        finally:
            with self._lock:
                runner = self._runner
                worker = self._worker
            if runner:
                runner.cancel()
            if worker:
                worker.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="Android Termux Telegram ↔ AGY bridge")
    parser.add_argument("--config", required=True, help="absolute path to chmod-600 private config")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print("Configuration error:", str(exc), flush=True)
        return 2
    bot = TelegramAgyBot(settings, TelegramAPI(settings.bot_token))
    def terminate(_signum, _frame):
        bot.stop()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        bot.run()
    except TelegramError:
        print("Telegram connection or token error during startup.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
