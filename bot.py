"""Minimal non-root Telegram ↔ AGY bridge with task status buttons."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agy import AgyRunner, MODEL_SLUG, list_models
from config import ConfigError, Settings, load_settings
from telegram_api import TelegramAPI, TelegramError


class OffsetStore:
    """Atomic private JSON state, initially used for the Telegram update offset."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("next_offset")
            return value if type(value) is int and value >= 0 else None
        except (OSError, ValueError, AttributeError):
            return None

    def write(self, value: int) -> None:
        private_json(self.path, {"next_offset": value})


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass
class Task:
    key: str
    owner: int
    chat: int
    thread: int | None
    runner: AgyRunner
    model: str | None
    started_at: float
    message_id: int | None = None


@dataclass
class Picker:
    owner: int
    chat: int
    thread: int | None
    message_id: int
    models: list[tuple[str, str]]


def task_status(state: str, started: float, model: str | None) -> str:
    elapsed = max(0, int(time.monotonic() - started))
    duration = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
    model_label = model or "AGY default"
    return f"{state} · {duration}\nModel: {model_label}"


class TelegramAgyBot:
    def __init__(self, settings: Settings, api: TelegramAPI):
        self.settings = settings
        self.api = api
        self.state = OffsetStore(settings.state_path)
        self._lock = threading.Lock()
        self._active: Task | None = None
        self._worker: threading.Thread | None = None
        self._picker: Picker | None = None
        self._selected_path = settings.config_path.parent / "selected-model.json"
        self._model = self._read_model()
        self._stop = threading.Event()

    def _read_model(self) -> str | None:
        try:
            value = json.loads(self._selected_path.read_text(encoding="utf-8")).get("model")
            return value if isinstance(value, str) and MODEL_SLUG.fullmatch(value) else None
        except (OSError, ValueError, AttributeError):
            return None

    def _reply(self, chat: int, text: str, thread: int | None = None,
               reply_markup: dict | None = None) -> int | None:
        try:
            return self.api.send_text(chat, text, thread, reply_markup=reply_markup)
        except TelegramError:
            print("Telegram send failed; check network/bot permission.", flush=True)
            return None

    def _edit(self, chat: int, message_id: int, text: str,
              markup: dict | None = None) -> bool:
        try:
            self.api.edit_text(chat, message_id, text, markup)
            return True
        except TelegramError:
            return False

    def _answer(self, query_id: str, message: str = "") -> None:
        try:
            self.api.answer_callback(query_id, message)
        except TelegramError:
            pass

    def handle_update(self, update: dict) -> None:
        if isinstance(update.get("callback_query"), dict):
            self._callback(update["callback_query"])
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return
        user_id, chat_id = sender.get("id"), chat.get("id")
        if (type(user_id) is not int or type(chat_id) is not int
                or (user_id, chat_id) not in self.settings.allowed_pairs):
            return
        content = message.get("text")
        if not isinstance(content, str) or not content.strip():
            return
        thread = message.get("message_thread_id")
        if type(thread) is not int:
            thread = None
        text = content.strip()
        cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if cmd in ("/start", "/help"):
            self._reply(chat_id, "Android Telegram Agent (AGY)\n"
                        "Send a prompt to start a task with a live timer and Stop button.\n"
                        "/start /help /status /models /model\n"
                        "Sessions are planned for a later release.", thread)
        elif cmd == "/status":
            with self._lock:
                active, model = self._active, self._model
            self._reply(chat_id, task_status("⏳ Running", active.started_at, active.model)
                        if active else f"Idle · Model: {model or 'AGY default'}", thread)
        elif cmd in ("/models", "/model"):
            option = text[len(text.split(maxsplit=1)[0]):].strip()
            if option.lower() == "default" and cmd == "/model":
                with self._lock:
                    busy = self._active is not None
                    if not busy:
                        self._model = None
                        private_json(self._selected_path, {"model": None})
                self._reply(chat_id, "Finish the running task first." if busy
                            else "Model reset to AGY default.", thread)
            else:
                self._models(user_id, chat_id, thread)
        elif cmd == "/cancel":
            # Backward-compatible emergency fallback; main UI uses Stop button.
            with self._lock:
                task = self._active
            self._reply(chat_id, "Stop requested." if task and task.runner.cancel()
                        else "No running task.", thread)
        elif text.startswith("/"):
            self._reply(chat_id, "Unknown command. Try /help.", thread)
        elif len(text) > 4000:
            self._reply(chat_id, "Prompt exceeds 4000 characters.", thread)
        else:
            self._start_task(text, user_id, chat_id, thread)

    def _start_task(self, prompt: str, owner: int, chat: int,
                    thread: int | None) -> None:
        with self._lock:
            if self._active is not None:
                busy = True
            else:
                busy = False
                runner = AgyRunner(self.settings.agy_command, self.settings.workspace,
                                   self.settings.task_timeout_seconds)
                task = Task(secrets.token_hex(8), owner, chat, thread,
                            runner, self._model, time.monotonic())
                worker = threading.Thread(target=self._execute, args=(task, prompt),
                                          daemon=True, name="agy-task")
                self._active = task
                self._worker = worker
                worker.start()
        if busy:
            self._reply(chat, "AGY is busy. Use the Stop button on the running task.",
                        thread)

    def _ticker(self, task: Task, done: threading.Event) -> None:
        while not done.wait(5):
            with self._lock:
                valid = self._active is task
                message_id = task.message_id
            if not valid or message_id is None:
                return
            self._edit(task.chat, message_id,
                       task_status("⏳ Running", task.started_at, task.model),
                       stop_keyboard(task.key))

    def _execute(self, task: Task, prompt: str) -> None:
        done = threading.Event()
        ticker = None
        ok = False
        message = "Task failed unexpectedly."
        try:
            mid = self._reply(task.chat,
                              task_status("⏳ Running", task.started_at, task.model),
                              task.thread, stop_keyboard(task.key))
            with self._lock:
                task.message_id = mid
            if mid is not None:
                ticker = threading.Thread(target=self._ticker, args=(task, done),
                                          daemon=True, name="task-timer")
                ticker.start()
            outcome = task.runner.run(prompt, model=task.model)
            ok, message = outcome.ok, outcome.message
        except Exception:
            message = "Task failed unexpectedly; no private details were shared."
        finally:
            done.set()
            if ticker is not None:
                ticker.join(timeout=12)
            if task.message_id is not None:
                state = "✅ Completed" if ok else ("🛑 Stopped" if
                          task.runner._cancelled.is_set() else "❌ Failed")
                self._edit(task.chat, task.message_id,
                           task_status(state, task.started_at, task.model))
            self._reply(task.chat, message if ok else "Task failed: " + message,
                        task.thread)
            with self._lock:
                if self._active is task:
                    self._active = None
                    self._worker = None

    def _models(self, user: int, chat: int, thread: int | None) -> None:
        with self._lock:
            busy = self._active is not None
        if busy:
            self._reply(chat, "Finish the running task before changing models.", thread)
            return
        models = list_models(self.settings.agy_command, self.settings.workspace)
        if not models:
            self._reply(chat, "Could not read AGY model list. Verify 'agy models' in Termux.",
                        thread)
            return
        with self._lock:
            current = self._model
        keyboard = {
            "inline_keyboard": [
                [{"text": ("✓ " if slug == current else "") + name[:48],
                  "callback_data": f"model:{index}"}]
                for index, (slug, name) in enumerate(models)
            ] + [[{"text": "AGY default", "callback_data": "model:default"}]]
        }
        msgid = self._reply(chat, "Choose AGY model for future tasks\n"
                            f"Current: {current or 'AGY default'}", thread, keyboard)
        with self._lock:
            self._picker = Picker(user, chat, thread, msgid, models) if msgid else None

    def _callback(self, query: dict) -> None:
        sender, message = query.get("from"), query.get("message")
        if not isinstance(sender, dict) or not isinstance(message, dict):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return
        uid, chat_id, mid = sender.get("id"), chat.get("id"), message.get("message_id")
        if (type(uid) is not int or type(chat_id) is not int
                or type(mid) is not int
                or (uid, chat_id) not in self.settings.allowed_pairs):
            return
        qid, data = query.get("id"), query.get("data")
        if not isinstance(qid, str) or not isinstance(data, str):
            return
        if data.startswith("stop:"):
            with self._lock:
                task = self._active
                allowed = (task is not None and task.key == data[5:]
                           and task.owner == uid and task.chat == chat_id
                           and task.message_id == mid)
            if allowed and task.runner.cancel():
                self._answer(qid, "Stopping task…")
            else:
                self._answer(qid, "Task already finished or unavailable.")
        elif data.startswith("model:"):
            with self._lock:
                picker, busy = self._picker, self._active is not None
                valid = (picker is not None and picker.owner == uid
                         and picker.chat == chat_id and picker.message_id == mid)
                if not valid or busy:
                    selected = None
                elif data == "model:default":
                    selected = ""
                else:
                    index = data[6:]
                    idx = int(index) if index.isascii() and index.isdecimal() else -1
                    selected = picker.models[idx][0] if 0 <= idx < len(picker.models) else None
                if selected is not None:
                    self._model = selected or None
                    private_json(self._selected_path, {"model": self._model})
                    self._picker = None
            if selected is None:
                self._answer(qid, "Model picker expired or task is running.")
            else:
                self._answer(qid, "Model updated.")
                self._edit(chat_id, mid,
                           f"Model selected: {selected or 'AGY default'}")

    def run(self) -> None:
        offset = self.state.read()
        if offset is None:
            last = self.api.get_updates(-1, timeout=0)
            offset = max((u["update_id"] for u in last if
                          isinstance(u.get("update_id"), int)), default=-1) + 1
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
                        if type(update_id) is not int or update_id < offset:
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
                task, worker = self._active, self._worker
            if task:
                task.runner.cancel()
            if worker:
                worker.join(timeout=15)

    def stop(self) -> None:
        self._stop.set()


def stop_keyboard(key: str) -> dict:
    return {"inline_keyboard": [[{"text": "🛑 Stop", "callback_data": "stop:" + key}]]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Android Termux Telegram ↔ AGY bridge")
    parser.add_argument("--config", required=True, help="absolute path to private config")
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
