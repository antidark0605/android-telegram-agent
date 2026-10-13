"""Lightweight Telegram ↔ AGY: topic sessions, model picker, live Stop, Ask MCP."""
from __future__ import annotations
import argparse
import json
import secrets
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agy import AgyRunner, list_models, usage_report
from ask_bridge import AskBridge
from config import ConfigError, Settings, load_settings
from sessions import SessionStore, scope, write_private
from telegram_api import TelegramAPI, TelegramError


@dataclass
class Task:
    key: str
    owner: int
    chat: int
    thread: int | None
    session_key: str
    runner: AgyRunner
    model: str | None
    started: float
    message_id: int | None = None


@dataclass
class Picker:
    user: int
    chat: int
    thread: int | None
    message_id: int
    options: list[tuple[str, str]]


@dataclass
class Pending:
    question: dict
    message_id: int


def status_text(state: str, started: float, model: str | None) -> str:
    seconds = max(0, int(time.monotonic() - started))
    return f"{state} · {seconds // 60:02d}:{seconds % 60:02d}\nModel: {model or 'AGY default'}"


def stop_keyboard(key: str) -> dict:
    return {"inline_keyboard": [[{"text": "🛑 Stop", "callback_data": "stop:" + key}]]}


class OffsetStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> int | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf8"))["next_offset"]
            return value if type(value) is int and value >= 0 else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def write(self, offset: int) -> None:
        write_private(self.path, {"next_offset": offset})


class TelegramAgyBot:
    def __init__(self, settings: Settings, api: TelegramAPI):
        self.settings, self.api = settings, api
        self.state = OffsetStore(settings.state_path)
        self.sessions = SessionStore(settings.config_path.parent / "sessions.json")
        self.bridge = AskBridge(settings.config_path.parent / "ask-bridge")
        self._lock = threading.RLock()
        self._active: Task | None = None
        self._worker: threading.Thread | None = None
        self._picker: Picker | None = None
        self._pending: Pending | None = None
        self._stop = threading.Event()

    def _send(self, chat: int, text: str, thread: int | None = None,
              markup: dict | None = None) -> int | None:
        try:
            return self.api.send_text(chat, text, thread, reply_markup=markup)
        except TelegramError:
            print("Telegram send failed.", flush=True)
            return None

    def _edit(self, chat: int, message_id: int, text: str,
              markup: dict | None = None) -> bool:
        try:
            self.api.edit_text(chat, message_id, text, markup)
            return True
        except TelegramError:
            return False

    def _ack(self, query_id: str, text: str = "") -> None:
        try:
            self.api.answer_callback(query_id, text)
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
        user, chat_id = sender.get("id"), chat.get("id")
        if (type(user) is not int or type(chat_id) is not int
                or (user, chat_id) not in self.settings.allowed_pairs):
            return
        thread = message.get("message_thread_id")
        if type(thread) is not int:
            thread = None
        content = message.get("text")
        if not isinstance(content, str) or not content.strip():
            return
        text = content.strip()
        key = scope(user, chat_id, thread)
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()
        with self._lock:
            pending = self._pending
            active = self._active
        # While an AGY MCP question is pending, free text is an answer.
        if (pending and active and pending.question["task"] == active.key
                and active.owner == user and active.chat == chat_id
                and active.thread == thread and not text.startswith("/")):
            self._answer_question(pending, text[:3000])
            return
        if cmd in ("/start", "/help"):
            self._send(chat_id, "AGY Telegram Agent\n"
                       "Text: continue current topic conversation\n"
                       "/new: start new conversation in this topic\n"
                       "/model, /models: choose model\n"
                       "/status, /context, /quota: show status\n"
                       "Tap 🛑 Stop to interrupt running tasks.\n"
                       "No collaboration or kanban.", thread)
        elif cmd == "/status":
            row = self.sessions.get(key)
            text_out = (status_text("⏳ Running", active.started, active.model)
                        if active and active.session_key == key else "Idle")
            self._send(chat_id, f"{text_out}\nSelected model: {row.get('model') or 'AGY default'}"
                       f"\nSession: {'active' if row.get('conversation_id') else 'new'}"
                       f"\nYOLO: {'enabled' if self.settings.yolo else 'disabled'}", thread)
        elif cmd == "/new":
            if active and active.session_key == key:
                self._send(chat_id, "Stop the running task first.", thread)
            else:
                self.sessions.reset(key)
                self._send(chat_id, "New AGY conversation for this topic. Model preserved.", thread)
        elif cmd in ("/model", "/models"):
            if cmd == "/model" and arg.strip().lower() == "default":
                if active and active.session_key == key:
                    self._send(chat_id, "Stop the running task first.", thread)
                else:
                    self.sessions.change(key, model=None)
                    with self._lock:
                        self._picker = None
                    self._send(chat_id, "Model: AGY default.", thread)
            else:
                self._models(user, chat_id, thread, key)
        elif cmd == "/context":
            row = self.sessions.get(key)
            usage = row.get("last_usage", {})
            if not isinstance(usage, dict) or not usage:
                self._send(chat_id, "Context occupancy: unavailable from Headless JSON.\n"
                           "No last-task token usage available.", thread)
            else:
                details = ", ".join(f"{k}: {v}" for k, v in sorted(usage.items()))
                self._send(chat_id, "Context occupancy: unavailable from Headless JSON.\n"
                           "Last completed task tokens (NOT context used %):\n" + details, thread)
        elif cmd in ("/quota", "/usage"):
            if active:
                self._send(chat_id, "Quota query unavailable while an AGY task is active.", thread)
            else:
                threading.Thread(target=self._quota, args=(chat_id, thread),
                                 daemon=True, name="agy-quota").start()
        elif text.startswith("/"):
            self._send(chat_id, "Unknown command. Try /help.", thread)
        elif len(text) > 4000:
            self._send(chat_id, "Prompt exceeds 4000 characters.", thread)
        else:
            self._start_task(text, user, chat_id, thread, key)

    def _quota(self, chat: int, thread: int | None) -> None:
        report = usage_report(self.settings.agy_command, self.settings.workspace)
        self._send(chat, "AGY /usage:\n" + report if report else
                   "AGY rate-limit report unavailable in this CLI environment.", thread)

    def _models(self, user: int, chat: int, thread: int | None, key: str) -> None:
        with self._lock:
            busy = self._active is not None
        if busy:
            self._send(chat, "Model selection is unavailable during an active task.", thread)
            return
        options = list_models(self.settings.agy_command, self.settings.workspace)
        if not options:
            self._send(chat, "AGY models unavailable; check 'agy models' in Termux.", thread)
            return
        current = self.sessions.get(key).get("model")
        buttons = [[{"text": ("✓ " if slug == current else "") + label[:45],
                     "callback_data": f"model:{i}"}]
                   for i, (slug, label) in enumerate(options)]
        buttons.append([{"text": "AGY default", "callback_data": "model:default"}])
        mid = self._send(chat, f"Current model: {current or 'AGY default'}\n"
                         "Choose a model for this topic:", thread,
                         {"inline_keyboard": buttons})
        if mid:
            with self._lock:
                self._picker = Picker(user, chat, thread, mid, options)

    def _start_task(self, prompt: str, user: int, chat: int,
                    thread: int | None, key: str) -> None:
        with self._lock:
            if self._active is not None:
                busy = True
            else:
                busy = False
                row = self.sessions.get(key)
                runner = AgyRunner(self.settings.agy_command, self.settings.workspace,
                                   self.settings.task_timeout_seconds,
                                   self.settings.print_timeout)
                task = Task(secrets.token_hex(8), user, chat, thread, key, runner,
                            row.get("model"), time.monotonic())
                self._active = task
                self._worker = threading.Thread(
                    target=self._execute,
                    args=(task, prompt, row.get("conversation_id")),
                    daemon=True, name="agy-task")
                self._worker.start()
        if busy:
            self._send(chat, "AGY is busy. Use Stop on the running task.", thread)

    def _timer(self, task: Task, done: threading.Event) -> None:
        while not done.wait(5):
            with self._lock:
                if self._active is not task:
                    return
                mid = task.message_id
                waiting = (self._pending is not None and
                           self._pending.question.get("task") == task.key)
            if mid is not None:
                self._edit(task.chat, mid,
                           status_text("❓ Waiting for Telegram answer" if waiting
                                       else "⏳ Running", task.started, task.model),
                           stop_keyboard(task.key))

    def _execute(self, task: Task, prompt: str, cid: str | None) -> None:
        done = threading.Event()
        ticker = None
        ok = False
        result_text = "Task failed unexpectedly."
        try:
            mid = self._send(task.chat,
                             status_text("⏳ Running", task.started, task.model),
                             task.thread, stop_keyboard(task.key))
            with self._lock:
                task.message_id = mid
            if mid is not None:
                ticker = threading.Thread(target=self._timer, args=(task, done),
                                          daemon=True, name="task-timer")
                ticker.start()
            self.bridge.begin(task.key, task.owner, task.chat, task.thread)
            ask_hint = ("\n\nIf a user decision is required, use the "
                        "telegram-ask MCP tool ask_telegram with a detailed question "
                        "and described choices. Do not assume an answer.")
            outcome = task.runner.run(prompt + ask_hint, model=task.model,
                                      conversation_id=cid, yolo=self.settings.yolo)
            ok, result_text = outcome.ok, outcome.message
            if ok:
                updates = {"last_usage": outcome.usage}
                if outcome.conversation_id:
                    updates["conversation_id"] = outcome.conversation_id
                if outcome.actual_model:
                    updates["last_actual_model"] = outcome.actual_model
                self.sessions.change(task.session_key, **updates)
        except Exception:
            result_text = "Task failed unexpectedly; no private details were shared."
        finally:
            done.set()
            if ticker:
                ticker.join(timeout=12)
            with self._lock:
                self._pending = None
            self.bridge.end(task.key)
            if task.message_id is not None:
                label = "✅ Completed" if ok else (
                    "🛑 Stopped" if task.runner._cancelled.is_set() else "❌ Failed")
                self._edit(task.chat, task.message_id,
                           status_text(label, task.started, task.model))
            self._send(task.chat, result_text if ok else "Task failed: " + result_text,
                       task.thread)
            with self._lock:
                if self._active is task:
                    self._active, self._worker = None, None

    def _poll_questions(self) -> None:
        with self._lock:
            task = self._active
            if self._pending is not None:
                return
        if task is None:
            return
        q = self.bridge.next_question(task.key)
        if q is None:
            return
        # Options require descriptions; include them in the question message.
        lines = ["❓ " + str(q.get("question") or "")[:3500]]
        opts = q.get("choices") or []
        buttons = []
        for i, item in enumerate(opts):
            lines.append(f"{i + 1}. {item['label']}: {item['description']}")
            buttons.append([{"text": item["label"][:45],
                             "callback_data": f"ask:{q['id'][:16]}:{i}"}])
        if opts:
            lines.append("Or send a free-text answer.")
        else:
            lines.append("Reply with text in this chat/topic.")
        mid = self._send(task.chat, "\n".join(lines), task.thread,
                         {"inline_keyboard": buttons} if buttons else None)
        if mid:
            with self._lock:
                if self._active is task:
                    self._pending = Pending(q, mid)
        else:
            # Retry delivery; do not leave a question silently unanswerable.
            retry = self.bridge.questions / (q["id"] + ".json")
            sent = retry.with_suffix(".sent")
            try:
                sent.rename(retry)
            except OSError:
                pass

    def _answer_question(self, pending: Pending, answer: str) -> None:
        with self._lock:
            if self._pending is not pending:
                return
            self._pending = None
        self.bridge.answer(pending.question, answer)
        self._edit(pending.question["chat"], pending.message_id,
                   "✅ Answer received. AGY continues.")
        self._send(pending.question["chat"], "Answer sent to AGY.",
                   pending.question["topic"])

    def _callback(self, cb: dict) -> None:
        who, msg = cb.get("from"), cb.get("message")
        if not isinstance(who, dict) or not isinstance(msg, dict):
            return
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return
        uid, cid, mid = who.get("id"), chat.get("id"), msg.get("message_id")
        if (type(uid) is not int or type(cid) is not int or type(mid) is not int
                or (uid, cid) not in self.settings.allowed_pairs):
            return
        data, qid = cb.get("data"), cb.get("id")
        if not isinstance(data, str) or not isinstance(qid, str):
            return
        if data.startswith("stop:"):
            with self._lock:
                task = self._active
                valid = (task is not None and task.key == data[5:]
                         and task.owner == uid and task.chat == cid
                         and task.message_id == mid)
            self._ack(qid, "Stopping…" if valid and task.runner.cancel()
                      else "Task already finished.")
        elif data.startswith("model:"):
            with self._lock:
                picker = self._picker
                busy = self._active is not None
                valid = (picker is not None and picker.user == uid
                         and picker.chat == cid and picker.message_id == mid)
                if valid and not busy and data == "model:default":
                    model = None
                elif valid and not busy and data[6:].isascii() and data[6:].isdecimal():
                    idx = int(data[6:])
                    model = picker.options[idx][0] if idx < len(picker.options) else False
                else:
                    model = False
                if model is not False:
                    self.sessions.change(scope(uid, cid, picker.thread), model=model)
                    self._picker = None
            if model is False:
                self._ack(qid, "Model menu expired or task is running.")
            else:
                self._ack(qid, "Model updated.")
                self._edit(cid, mid, f"Model: {model or 'AGY default'}")
        elif data.startswith("ask:"):
            with self._lock:
                pending, task = self._pending, self._active
                fields = data.split(":")
                valid = (pending is not None and task is not None
                         and len(fields) == 3 and fields[1] == pending.question["id"][:16]
                         and pending.message_id == mid and task.key == pending.question["task"]
                         and uid == task.owner and cid == task.chat)
                opts = pending.question["choices"] if valid else []
                idx = int(fields[2]) if fields[-1].isascii() and fields[-1].isdecimal() else -1
            if valid and 0 <= idx < len(opts):
                self._answer_question(pending, opts[idx]["value"])
                self._ack(qid, "Answer recorded.")
            else:
                self._ack(qid, "Question expired.")

    def run(self) -> None:
        self.bridge.install(self.settings.workspace)
        offset = self.state.read()
        if offset is None:
            stale = self.api.get_updates(-1, timeout=0)
            offset = max((item["update_id"] for item in stale
                          if type(item.get("update_id")) is int), default=-1) + 1
            self.state.write(offset)
        print("Telegram polling started (AGY on demand).", flush=True)
        backoff = 2
        try:
            while not self._stop.is_set():
                self._poll_questions()
                try:
                    updates = self.api.get_updates(offset, timeout=3)
                    backoff = 2
                    for item in updates:
                        update_id = item.get("update_id")
                        if type(update_id) is not int or update_id < offset:
                            continue
                        try:
                            self.handle_update(item)
                        finally:
                            offset = update_id + 1
                            self.state.write(offset)
                    self._poll_questions()
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Termux Telegram ↔ AGY")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print("Configuration error:", str(exc), flush=True)
        return 2
    bot = TelegramAgyBot(settings, TelegramAPI(settings.bot_token))
    def terminate(_sig, _frame):
        bot.stop()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        bot.run()
    except (TelegramError, ValueError):
        print("Bot startup failed: verify configuration and network.", flush=True)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
