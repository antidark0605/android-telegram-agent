"""Lightweight Telegram ↔ AGY: topic sessions, model picker, live Stop, Ask MCP."""
from __future__ import annotations
import argparse
import json
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from access import TelegramAccess, identity_reply
from incoming_media import download_media, select_document, select_photo
from incoming_photo import download_photo
from agy import AgyRunner, list_models
from ask_bridge import AskBridge
from config import ConfigError, Settings, load_settings
from command_menu import INHERITED_COMMAND_SCOPES, telegram_commands
from diagnostics import EventLogger
from last_store import LastStore
from lifecycle_store import LifecycleStore
from sessions import SessionStore, scope, write_private
from telegram_api import TelegramAPI, TelegramError
from presentation import (DISPLAY_LABELS, DISPLAY_MODES, display_keyboard,
                          normalize_display_mode, progress_message,
                          render_response, usage_plain)
from usage_footer import QuotaService
from workspace_policy import (WorkspaceError, resolve_list_directory,
                              resolve_workspace)
from vendor.telegram_ui.service_lifecycle import (ServiceLifecycleView,
                                                   render_service_lifecycle)
from rich_delivery import _html_fallback

ATTACHMENT_FIELDS = frozenset({
    "photo", "document", "audio", "video", "animation", "voice",
    "video_note", "sticker",
})


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
    delivering: bool = False
    photo: dict | None = None
    display_mode: str = "rich"
    workspace: Path | None = None


@dataclass
class Picker:
    user: int
    chat: int
    thread: int | None
    message_id: int
    options: list[tuple[str, str]]
    kind: str = "model"


@dataclass
class Pending:
    question: dict
    message_id: int


def status_text(state: str, started: float, model: str | None) -> str:
    seconds = max(0, int(time.monotonic() - started))
    return f"{state} · {seconds // 60:02d}:{seconds % 60:02d}\nModel: {model or 'not selected'}"


class OffsetStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> int | None:
        try:
            raw = self.path.read_text(encoding="utf8")
        except FileNotFoundError:
            return None  # First-time setup only.
        try:
            value = json.loads(raw)["next_offset"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Invalid private Telegram offset state") from None
        if type(value) is not int or value < 0:
            raise ValueError("Invalid private Telegram offset state")
        return value

    def write(self, offset: int) -> None:
        write_private(self.path, {"next_offset": offset})


class TelegramAgyBot:
    def __init__(self, settings: Settings, api: TelegramAPI):
        self.settings, self.api = settings, api
        self.access = TelegramAccess(settings.allowed_pairs, settings.allow_unlisted_myid)
        self.events = EventLogger(settings.config_path.parent / 'events.jsonl')
        self.state = OffsetStore(settings.state_path)
        self.sessions = SessionStore(settings.config_path.parent / "sessions.json")
        self.bridge = AskBridge(settings.config_path.parent / "ask-bridge")
        self.last = LastStore(settings.config_path.parent / "last.json")
        self.lifecycle = LifecycleStore(settings.config_path.parent / "lifecycle.json")
        self._lock = threading.RLock()
        self._active: Task | None = None
        self._worker: threading.Thread | None = None
        self._picker: Picker | None = None
        self._pending: Pending | None = None
        self._auxiliary = False
        self._stop = threading.Event()
        self._running = False
        self._started_at = datetime.now(timezone.utc)
        self._lifecycle_attempted: set[tuple[int, int]] = set()
        self.quota = QuotaService(settings.agy_command, settings.workspace)

    def _sync_command_menu(self) -> None:
        """Make Telegram's server-side menu match the implemented routes."""
        expected = telegram_commands()
        try:
            current = self.api.get_my_commands()
            if current != expected:
                self.api.set_my_commands(expected)
                current = self.api.get_my_commands()
            if current != expected:
                raise TelegramError("Telegram command menu was not confirmed",
                                    kind="protocol")
            for command_scope in INHERITED_COMMAND_SCOPES:
                if self.api.get_my_commands(command_scope):
                    self.api.delete_my_commands(command_scope)
                    if self.api.get_my_commands(command_scope):
                        raise TelegramError("Telegram scoped menu was not cleared",
                                            kind="protocol")
        except (TelegramError, AttributeError, TypeError):
            # Menu metadata is advisory; task polling must remain available.
            self.events.log("command_menu_failed", stage="command_menu")
            return
        self.events.log("command_menu_ok", stage="command_menu",
                        count=len(expected))

    def _send(self, chat: int, text: str, thread: int | None = None,
              markup: dict | None = None, parse_mode: str | None = None) -> int | None:
        self.events.log("send_attempt", stage="send_message")
        # Plain commands stay plain; only status messages opt into HTML.
        extra = {"parse_mode": parse_mode} if parse_mode else {}
        try:
            message_id = self.api.send_text(chat, text, thread, reply_markup=markup, **extra)
            self.events.log("send_ok", stage="send_message")
            return message_id
        except TelegramError as exc:
            self.events.log("send_failed", stage="send_message", kind=exc.kind,
                            http_status=exc.http_status)
            return None

    def _send_output(self, chat: int, text: str, thread: int | None = None,
                     mode: str = "rich", workspace: Path | None = None) -> bool:
        """Post one already-rendered answer using its explicit display mode."""
        from artifacts import extract_artifacts
        self.events.log("send_attempt", stage="send_message")
        try:
            workspace = resolve_workspace(
                self.settings.workspace, str(workspace or self.settings.workspace))[0]
            cleaned, media = extract_artifacts(text, workspace)
            mode = normalize_display_mode(mode)
            if mode == "rich" and hasattr(self.api, "send_rich"):
                message_id = self.api.send_rich(chat, cleaned, thread, artifacts=media)
            elif hasattr(self.api, "send_classic"):
                message_id = self.api.send_classic(chat, cleaned, thread, artifacts=media)
            else:
                # Simple in-memory test transports may only implement send_text.
                message_id = self.api.send_text(chat, cleaned, thread)
            if type(message_id) is not int:
                raise TelegramError("Telegram did not confirm a message id",
                                    kind="protocol")
            self.events.log("send_ok", stage="send_message")
            return True
        except TelegramError as exc:
            self.events.log("send_failed", stage="send_message", kind=exc.kind,
                            http_status=exc.http_status)
            return False
        except (OSError, ValueError):
            self.events.log("send_failed", stage="send_message", kind="storage")
            return False

    def _record_destination(self, user: int, chat: int, thread: int | None) -> None:
        """Persist the latest Topic for one already-authorized user/chat pair."""
        try:
            self.lifecycle.remember(user, chat, thread)
        except (OSError, ValueError, TypeError):
            self.events.log("lifecycle_failed", kind="storage", stage="destination_save")

    def _notify_lifecycle(self) -> None:
        """Notify prior used destinations once after polling state is ready."""
        try:
            state, targets = self.lifecycle.begin_instance()
        except (OSError, ValueError, TypeError):
            self.events.log("lifecycle_failed", kind="storage", stage="startup_state")
            return
        try:
            notice = render_service_lifecycle(ServiceLifecycleView(
                agent_label="AGY", state=state,
                started_at=self._started_at, reason=None))
            rendered = _html_fallback(notice)
        except (TypeError, ValueError):
            self.events.log("lifecycle_failed", kind="protocol", stage="startup_notice")
            return
        for user, chat, thread in targets:
            pair = (user, chat)
            # Mark before sending: a lost acknowledgement must not cause an
            # automatic duplicate within this process instance.
            with self._lock:
                if pair in self._lifecycle_attempted:
                    continue
                self._lifecycle_attempted.add(pair)
            if not self.access.is_allowed(user, chat):
                continue
            try:
                sent = self._send(chat, rendered, thread, parse_mode="HTML")
            except Exception:
                # Lifecycle delivery is advisory and must never block polling.
                sent = None
            if sent is None:
                self.events.log("lifecycle_failed", kind="unconfirmed",
                                stage="startup_notice")

    def _save_last(self, key: str, text: str, mode: str,
                   workspace: Path | None = None) -> None:
        try:
            _path, relative = resolve_workspace(
                self.settings.workspace, str(workspace or self.settings.workspace))
            self.last.save(key, text, normalize_display_mode(mode), relative)
        except (OSError, ValueError, TypeError):
            self.events.log("last_response_failed", kind="storage", stage="last_save")

    def _edit(self, chat: int, message_id: int, text: str,
              markup: dict | None = None, parse_mode: str | None = None) -> bool:
        extra = {"parse_mode": parse_mode} if parse_mode else {}
        try:
            self.api.edit_text(chat, message_id, text, markup, **extra)
            return True
        except TelegramError as exc:
            self.events.log("edit_failed", stage="edit_message", kind=exc.kind,
                            http_status=exc.http_status)
            return False

    def _progress(self, task: Task, state: str) -> tuple[str, dict] | None:
        try:
            return progress_message(state, task.started, task.key)
        except ValueError:
            # Never raise from status rendering: task cleanup must still run.
            self.events.log("edit_failed", kind="protocol", stage="edit_message")
            return None

    def _edit_status(self, task: Task, state: str) -> None:
        """Edit the task's own status message in place, with its keyboard."""
        payload = self._progress(task, state)
        if payload is not None and task.message_id is not None:
            self._edit(task.chat, task.message_id, payload[0], payload[1],
                       parse_mode="HTML")

    def _typing(self, task: Task) -> None:
        """Refresh Telegram's short-lived typing action without affecting a task."""
        with self._lock:
            waiting = (self._pending is not None and
                       self._pending.question.get("task") == task.key)
            if self._active is not task or task.delivering or waiting:
                return
        try:
            self.api.send_chat_action(task.chat, "typing", task.thread)
        except (TelegramError, AttributeError):
            self.events.log("chat_action_failed", stage="send_chat_action")

    def _workspace(self, row: dict) -> Path:
        return resolve_workspace(self.settings.workspace, row.get("cwd"))[0]

    def _ack(self, query_id: str, text: str = "") -> None:
        try:
            self.api.answer_callback(query_id, text)
        except TelegramError as exc:
            self.events.log("callback_failed", stage="answer_callback", kind=exc.kind,
                            http_status=exc.http_status)

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
        if sender.get("is_bot") is True:
            return  # Do not process bot-originated messages or bootstrap IDs.
        thread = message.get("message_thread_id")
        if type(thread) is not int:
            thread = None
        attachment_present = any(field in message for field in ATTACHMENT_FIELDS)
        photo = select_photo(message.get("photo"))
        document = None if photo else select_document(message.get("document"))
        incoming = photo or document
        content = ((message.get("caption") or "請查看附圖或文件並描述其內容。")
                   if incoming else message.get("text"))
        if incoming is None and attachment_present:
            content = ""
        elif not isinstance(content, str) or not content.strip():
            return
        text = content.strip() if isinstance(content, str) else ""
        cmd, _, arg = text.partition(" ")
        cmd = "" if incoming else cmd.split("@", 1)[0].lower()
        authorized = self.access.is_allowed(user, chat_id)
        if cmd == "/myid":
            if not self.access.may_discover_ids(
                user, chat_id, chat.get("type"), sender.get("is_bot", False)
            ):
                return
            if authorized:
                self.events.log("inbound_authorized", kind="message")
                self._record_destination(user, chat_id, thread)
            chat_type = chat.get("type")
            if chat_type not in ("private", "group", "supergroup", "channel"):
                chat_type = "unknown"
            self._send(chat_id, identity_reply(user, chat_id, chat_type, thread), thread)
            return
        if not authorized:
            return  # Nothing, including AGY/Ask/commands, for unlisted pairs.
        self.events.log("inbound_authorized", kind="message")
        self._record_destination(user, chat_id, thread)
        if attachment_present and incoming is None:
            self._send(chat_id, "Unsupported attachment. Send one supported photo or document "
                       "within the allowed file type and size limits.", thread)
            return
        key = scope(user, chat_id, thread)
        with self._lock:
            pending = self._pending
            active = self._active
        if incoming and pending and active:
            self._send(chat_id, "請先用文字回答目前的提問，再傳送附件。", thread)
            return
        # While an AGY MCP question is pending, free text is an answer.
        if (pending and active and pending.question["task"] == active.key
                and active.owner == user and active.chat == chat_id
                and active.thread == thread and not text.startswith("/")):
            self._answer_question(pending, text[:3000])
            return
        if cmd in ("/start", "/help"):
            self._send(chat_id, "AGY Telegram Agent\n"
                       "Text: continue an activated topic conversation\n"
                       "/session start [CWD]: activate this chat/topic\n"
                       "/myid: show your user/chat/topic IDs\n"
                       "/new: start new conversation in this topic\n"
                       "/model: choose an explicit model\n"
                       "/format: choose rich/classic/raw reply display\n"
                       "/last: manually resend the last saved reply\n"
                       "/ls [PATH]: list the Topic CWD or one of its subdirectories (ls -l)\n"
                       "/status, /context, /quota: show status\n"
                       "Tap 🛑 中斷 (Stop) to interrupt running tasks.\n"
                       "No collaboration or kanban.", thread)
        elif cmd == "/status":
            row = self.sessions.get(key)
            text_out = (status_text("⏳ Running", active.started, active.model)
                        if active and active.session_key == key else "Idle")
            try:
                cwd = self._workspace(row)
                cwd_text = str(cwd)
            except WorkspaceError:
                cwd_text = "unavailable; re-enter /session start [CWD]"
            self._send(chat_id, f"{text_out}\nSelected model: {row.get('model') or 'not selected'}"
                       f"\nLast reported model: {row.get('last_actual_model') or 'not reported by AGY'}"
                       f"\nCWD: {cwd_text}"
                       f"\nNative session ID: {row.get('conversation_id') or 'pending (created on first task)'}"
                       f"\nDisplay: {normalize_display_mode(row.get('display_mode'))}"
                       f"\nYOLO: {'enabled' if self.settings.yolo else 'disabled'}", thread)
        elif cmd == "/session":
            fields = arg.strip().split(maxsplit=1)
            if not fields or fields[0].lower() != "start":
                self._send(chat_id, "Use /session start [CWD] in the intended chat/topic.", thread)
            else:
                row = self.sessions.get(key)
                requested = fields[1] if len(fields) == 2 else None
                active_session = (row.get("started") is True
                                  or bool(row.get("conversation_id")))
                if active and active.session_key == key:
                    self._send(chat_id, "Stop the running task first.", thread)
                elif active_session and requested is None:
                    self._send(chat_id, "This chat/topic already has an active session.", thread)
                else:
                    try:
                        cwd, relative = resolve_workspace(
                            self.settings.workspace, requested)
                        self.bridge.install(cwd)
                    except WorkspaceError as exc:
                        self._send(chat_id, f"CWD rejected: {exc} Fix it, then re-enter "
                                   "/session start [CWD].", thread)
                    except PermissionError:
                        self._send(chat_id, "CWD rejected: the Bot cannot create its private "
                                   "workspace files there. Fix permissions, then re-enter "
                                   "/session start [CWD].", thread)
                    except ValueError:
                        self._send(chat_id, "CWD rejected: its private agent configuration "
                                   "is unsafe or conflicts with this Bot. Fix it, then re-enter "
                                   "/session start [CWD].", thread)
                    except OSError:
                        self._send(chat_id, "Session activation could not be saved.", thread)
                    else:
                        try:
                            if active_session and row.get("cwd", ".") != relative:
                                self.sessions.reset(key, cwd=relative)
                                changed = True
                            elif active_session:
                                changed = False
                            else:
                                self.sessions.change(key, started=True, cwd=relative)
                                changed = True
                        except (OSError, ValueError):
                            self._send(chat_id, "Session activation could not be saved.", thread)
                        else:
                            if active_session and not changed:
                                self._send(chat_id, "This chat/topic already uses that CWD.", thread)
                            elif active_session:
                                self._send(chat_id, f"CWD updated for this chat/topic.\nCWD: {cwd}\n"
                                           "The next task starts a new AGY conversation.", thread)
                            else:
                                self._send(chat_id, f"Session activated for this chat/topic.\n"
                                           f"CWD: {cwd}\nChoose /model, then send a task.", thread)
        elif cmd == "/new":
            if active and active.session_key == key:
                self._send(chat_id, "Stop the running task first.", thread)
            else:
                try:
                    self.sessions.reset(key)
                except (OSError, ValueError):
                    self.events.log("session_failed", kind="storage",
                                    stage="session_write")
                    self._send(chat_id, "Session reset could not be saved.", thread)
                else:
                    self._send(chat_id, "New AGY conversation for this topic. CWD, model and "
                               "display mode preserved.", thread)
        elif cmd == "/model":
            if arg.strip():
                self._send(chat_id, "Use /model and choose an explicit model.", thread)
            else:
                self._models(user, chat_id, thread, key)
        elif cmd == "/format":
            self._display(user, chat_id, thread, key, arg.strip())
        elif cmd == "/last":
            self._last_reply(chat_id, thread, key)
        elif cmd == "/ls":
            try:
                cwd = self._workspace(self.sessions.get(key))
                target = resolve_list_directory(
                    self.settings.workspace, cwd, arg.strip() or None)
            except WorkspaceError as exc:
                self._send(chat_id, f"Path rejected: {exc} Re-enter /ls [PATH].", thread)
            else:
                self._list_workspace(chat_id, thread, target)
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
            with self._lock:
                available = self._active is None and not self._auxiliary
                if available:
                    self._auxiliary = True
            if not available:
                self._send(chat_id, "AGY is busy; retry quota later.", thread)
            else:
                threading.Thread(target=self._quota, args=(chat_id, thread, self.sessions.get(key).get("model")),
                                 daemon=True, name="agy-quota").start()
        elif text.startswith("/") and incoming is None:
            self._send(chat_id, "Unknown command. Try /help.", thread)
        elif len(text) > 4000:
            self._send(chat_id, "Prompt exceeds 4000 characters.", thread)
        else:
            self._start_task(text, user, chat_id, thread, key, photo=incoming)

    def _list_workspace(self, chat: int, thread: int | None, workspace: Path) -> None:
        try:
            result = subprocess.run(
                ["ls", "-l", "--", str(workspace)],
                cwd=str(workspace), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, check=False)
            if result.returncode != 0:
                self._send(chat, "Workspace listing failed.", thread)
                return
            output = result.stdout or "Workspace listing is empty."
            header = f"Path: {workspace}\n"
            remaining = 12000 - len(header) - len("\n[Listing truncated]")
            if len(output) > remaining:
                output = output[:remaining] + "\n[Listing truncated]"
            self._send(chat, header + output, thread)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._send(chat, "Workspace listing unavailable.", thread)

    def _quota(self, chat: int, thread: int | None, model: str | None = None) -> None:
        try:
            data, _at, cached = self.quota.get()
            self._send(chat, usage_plain(model, data, cached), thread)
        finally:
            with self._lock:
                self._auxiliary = False

    def _models(self, user: int, chat: int, thread: int | None, key: str) -> None:
        with self._lock:
            busy = self._active is not None or self._auxiliary
        if busy:
            self._send(chat, "Model selection is unavailable during an active task.", thread)
            return
        try:
            workspace = self._workspace(self.sessions.get(key))
        except WorkspaceError as exc:
            self._send(chat, f"CWD unavailable: {exc} Re-enter /session start [CWD].", thread)
            return
        options = list_models(self.settings.agy_command, workspace)
        if not options:
            self._send(chat, "AGY models unavailable; check 'agy models' in Termux.", thread)
            return
        current = self.sessions.get(key).get("model")
        model_buttons = [{"text": ("✓ " if slug == current else "") + label[:45],
                          "callback_data": f"model:{i}"}
                         for i, (slug, label) in enumerate(options)]
        buttons = [model_buttons[i:i + 2] for i in range(0, len(model_buttons), 2)]
        mid = self._send(chat, f"Current model: {current or 'not selected'}\n"
                         "Choose a model for this topic:", thread,
                         {"inline_keyboard": buttons})
        if mid:
            with self._lock:
                self._picker = Picker(user, chat, thread, mid, options)

    def _display(self, user: int, chat: int, thread: int | None, key: str,
                 requested: str = "") -> None:
        with self._lock:
            busy = self._active is not None or self._auxiliary
        if busy:
            self._send(chat, "Display selection is unavailable during an active task.", thread)
            return
        if requested:
            if requested not in DISPLAY_MODES:
                self._send(chat, "Use /format with rich, classic or raw.", thread)
                return
            try:
                self.sessions.change(key, display_mode=requested)
            except (OSError, ValueError):
                self._send(chat, "Display mode could not be saved.", thread)
            else:
                self._send(chat, "Reply display: " + DISPLAY_LABELS[requested], thread)
            return
        current = normalize_display_mode(self.sessions.get(key).get("display_mode"))
        mid = self._send(chat, "Reply display for this chat/topic: "
                         + DISPLAY_LABELS[current], thread, display_keyboard(current))
        if mid:
            with self._lock:
                self._picker = Picker(user, chat, thread, mid,
                                      [(mode, DISPLAY_LABELS[mode]) for mode in DISPLAY_MODES],
                                      kind="display")

    def _last_reply(self, chat: int, thread: int | None, key: str) -> None:
        row = self.last.get(key)
        if row is None:
            self._send(chat, "No saved reply for this chat/topic.", thread)
            return
        try:
            workspace = resolve_workspace(self.settings.workspace, row.get("cwd"))[0]
        except WorkspaceError as exc:
            self._send(chat, f"Saved reply CWD unavailable: {exc}", thread)
            return
        self._send_output(chat, row["text"], thread, row.get("mode", "rich"), workspace)

    def _start_task(self, prompt: str, user: int, chat: int,
                    thread: int | None, key: str, photo: dict | None = None) -> None:
        with self._lock:
            if self._active is not None or self._auxiliary:
                busy = True
            else:
                busy = False
                row = self.sessions.get(key)
                if row.get("started") is not True and not row.get("conversation_id"):
                    self._send(chat, "No active session. Use /session start in the intended chat/topic first.", thread)
                    return
                if not row.get("model"):
                    self._send(chat, "Choose a model with /model before starting a task.", thread)
                    return
                try:
                    workspace = self._workspace(row)
                except WorkspaceError as exc:
                    self._send(chat, f"CWD unavailable: {exc} Re-enter "
                               "/session start [CWD].", thread)
                    return
                runner = AgyRunner(self.settings.agy_command, workspace,
                                   self.settings.task_timeout_seconds,
                                   self.settings.print_timeout)
                display_mode = normalize_display_mode(row.get("display_mode"))
                task = Task(secrets.token_hex(8), user, chat, thread, key, runner,
                            row.get("model"), time.monotonic(), photo=photo,
                            display_mode=display_mode, workspace=workspace)
                self._active = task
                self.events.log("task_started", stage="agy")
                self._worker = threading.Thread(
                    target=self._execute,
                    args=(task, prompt, row.get("conversation_id")),
                    daemon=True, name="agy-task")
                self._worker.start()
        if busy:
            self._send(chat, "AGY is busy. Use Stop on the running task.", thread)

    def _timer(self, task: Task, done: threading.Event) -> None:
        with self._lock:
            waiting = (self._pending is not None and
                       self._pending.question.get("task") == task.key)
        if not waiting:
            self._typing(task)
        while not done.wait(4):
            with self._lock:
                if self._active is not task or task.delivering:
                    return
                waiting = (self._pending is not None and
                           self._pending.question.get("task") == task.key)
            self._edit_status(task, "waiting" if waiting else "running")
            if not waiting:
                self._typing(task)

    def _execute(self, task: Task, prompt: str, cid: str | None) -> None:
        done = threading.Event()
        ticker = None
        ok = False
        result_text = "Task failed unexpectedly."
        incoming_path = None
        workspace = task.workspace or self.settings.workspace
        try:
            workspace = resolve_workspace(self.settings.workspace, str(workspace))[0]
            payload = self._progress(task, "running")
            mid = (self._send(task.chat, payload[0], task.thread, payload[1],
                              parse_mode="HTML") if payload else None)
            with self._lock:
                task.message_id = mid
            ticker = threading.Thread(target=self._timer, args=(task, done),
                                      daemon=True, name="task-timer")
            ticker.start()
            self.bridge.install(workspace)
            folder = workspace / "artifacts"
            if folder.is_symlink():
                raise ValueError("Artifact directory cannot be a symlink")
            folder.mkdir(mode=0o700, exist_ok=True)
            if not folder.is_dir():
                raise ValueError("Artifact directory is not a directory")
            if task.photo is not None:
                if task.photo.get("kind") in (None, "photo"):
                    incoming_path = download_photo(
                        self.api, task.photo, workspace,
                        cancelled=task.runner._cancelled)
                else:
                    incoming_path = download_media(
                        self.api, task.photo, workspace,
                        cancelled=task.runner._cancelled)
                prompt += ("\n\n使用者附件已下載到本機：" + str(incoming_path)
                           + "\n請使用圖片或檔案查看工具讀取此附件，不要僅依檔名猜測。"
                           + "\n這是使用者傳入的附件；不要將它當成待傳送產物。")
            self.bridge.begin(task.key, task.owner, task.chat, task.thread)
            ask_hint = ("\n\nIf a user decision is required, use the "
                        "telegram-ask MCP tool ask_telegram with a detailed question "
                        "and described choices. Do not assume an answer. "
                        "If you create files for the user, save them under the "
                        "workspace artifacts/ directory, then include a standalone "
                        "line in your final response, without punctuation after it:\n"
                        'TAGW_MEDIA: {"path":"artifacts/example.pdf","kind":"auto"}\n'
                        "Never declare private files outside artifacts/.")
            workspace = resolve_workspace(self.settings.workspace, str(workspace))[0]
            outcome = task.runner.run(prompt + ask_hint, model=task.model,
                                      conversation_id=cid, yolo=self.settings.yolo)
            ok, result_text = outcome.ok, outcome.message
            if ok:
                updates = {"last_usage": outcome.usage}
                if outcome.conversation_id:
                    updates["conversation_id"] = outcome.conversation_id
                if outcome.actual_model:
                    updates["last_actual_model"] = outcome.actual_model
                try:
                    self.sessions.change(task.session_key, **updates)
                except (OSError, ValueError):
                    # AGY may have completed, but the next turn cannot safely
                    # rely on unsaved conversation metadata. Do not mark green.
                    self.events.log("session_failed", kind="storage",
                                    stage="session_write")
                    ok = False
                    result_text = ("AGY answered, but session state could not be saved. "
                                   "Conversation continuity is not guaranteed.\n\n"
                                   + outcome.message)
        except Exception:
            ok = False
            result_text = "Task failed unexpectedly; no private details were shared."
        finally:
            if incoming_path is not None:
                try:
                    incoming_path.unlink(missing_ok=True)
                except OSError:
                    self.events.log("photo_cleanup_failed", kind="storage", stage="cleanup")
            with self._lock:
                task.delivering = True  # Agent no longer accepts Stop callbacks.
            done.set()
            if ticker:
                ticker.join(timeout=12)
            with self._lock:
                self._pending = None
            self.bridge.end(task.key)
            rendered = result_text
            if ok:
                self._edit_status(task, "delivering")
                data, _at, cached = ({}, None, False)
                if task.display_mode != "raw":
                    data, _at, cached = self.quota.get()
                # The header names the task's own model, not the topic's current one.
                rendered = render_response(result_text, task.model, data, cached,
                                           task.display_mode)
            self._save_last(task.session_key, rendered if ok else result_text,
                            task.display_mode if ok else "raw", workspace)
            delivered = (self._send_output(task.chat, rendered, task.thread,
                                           task.display_mode, workspace) if ok else True)
            if not ok:
                self._send(task.chat, "Task failed: " + result_text, task.thread)
            # Completed only after Telegram acknowledged the answer; terminal
            # states carry an empty keyboard, removing Stop.
            self._edit_status(task, "completed" if ok and delivered else
                              "unconfirmed" if ok else
                              "cancelled" if task.runner._cancelled.is_set()
                              else "failed")
            task_kind = ("success" if ok else
                         "stopped" if task.runner._cancelled.is_set() else "failed")
            self.events.log("task_finished", kind=task_kind, stage="agy",
                            elapsed_ms=int((time.monotonic() - task.started) * 1000))
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
        thread = msg.get("message_thread_id")
        if type(thread) is not int:
            thread = None
        if (type(mid) is not int or not self.access.is_allowed(uid, cid)
                or who.get("is_bot") is True):
            return
        data, qid = cb.get("data"), cb.get("id")
        if (not isinstance(data, str) or len(data.encode("utf-8")) > 64
                or not isinstance(qid, str) or not qid):
            return
        action = "stop" if data.startswith("stop:") else (
            "model" if data.startswith("model:") else
            "display" if data.startswith("display:") else
            "ask" if data.startswith("ask:") else "other")
        self.events.log("callback_authorized", kind=action)
        self._record_destination(uid, cid, thread)
        if data.startswith("stop:"):
            with self._lock:
                task = self._active
                valid = (task is not None and task.key == data[5:]
                         and task.owner == uid and task.chat == cid
                         and task.thread == thread
                         and task.message_id == mid
                         and not task.delivering)
            accepted = bool(valid and task.runner.cancel())
            if accepted:
                self.events.log("task_stop_requested", stage="agy")
            self._ack(qid, "Stopping…" if accepted
                      else "Task already finished.")
        elif data.startswith("model:"):
            save_failed = False
            with self._lock:
                picker = self._picker
                busy = self._active is not None or self._auxiliary
                valid = (picker is not None and picker.user == uid
                         and picker.chat == cid and picker.thread == thread
                         and picker.kind == "model" and picker.message_id == mid)
                if valid and not busy and data[6:].isascii() and data[6:].isdecimal():
                    idx = int(data[6:])
                    model = picker.options[idx][0] if idx < len(picker.options) else False
                else:
                    model = False
                if model is not False:
                    try:
                        self.sessions.change(scope(uid, cid, picker.thread), model=model)
                    except (OSError, ValueError):
                        self.events.log("session_failed", kind="storage",
                                        stage="session_write")
                        model = False
                        save_failed = True
                    else:
                        self._picker = None
            if model is False:
                self._ack(qid, "Model selection could not be saved."
                          if save_failed
                          else "Model menu expired or task is running.")
            else:
                self._ack(qid, "Model updated.")
                self._edit(cid, mid, f"Model: {model or 'not selected'}")
        elif data.startswith("display:"):
            mode = data[8:]
            with self._lock:
                picker = self._picker
                busy = self._active is not None or self._auxiliary
                valid = (picker is not None and picker.kind == "display"
                         and picker.user == uid and picker.chat == cid
                         and picker.thread == thread and picker.message_id == mid
                         and mode in DISPLAY_MODES)
                if valid and not busy:
                    try:
                        self.sessions.change(scope(uid, cid, thread), display_mode=mode)
                    except (OSError, ValueError):
                        valid = False
                    else:
                        self._picker = None
            if not valid:
                self._ack(qid, "Display menu expired or task is running.")
            else:
                self._ack(qid, "Display mode updated.")
                self._edit(cid, mid, "Reply display: " + DISPLAY_LABELS[mode])
        elif data.startswith("ask:"):
            with self._lock:
                pending, task = self._pending, self._active
                fields = data.split(":")
                valid = (pending is not None and task is not None
                         and len(fields) == 3 and fields[1] == pending.question["id"][:16]
                         and pending.message_id == mid and task.key == pending.question["task"]
                         and uid == task.owner and cid == task.chat
                         and thread == task.thread
                         and pending.question.get("topic") == thread)
                opts = pending.question["choices"] if valid else []
                idx = int(fields[2]) if fields[-1].isascii() and fields[-1].isdecimal() else -1
            if valid and 0 <= idx < len(opts):
                self._answer_question(pending, opts[idx]["value"])
                self._ack(qid, "Answer recorded.")
            else:
                self._ack(qid, "Question expired.")

    def run(self) -> None:
        self.bridge.install(self.settings.workspace)
        try:
            offset = self.state.read()
        except (OSError, ValueError):
            self.events.log("offset_failed", kind="storage", stage="startup")
            raise
        if offset is None:
            stale = self.api.get_updates(-1, timeout=0)
            offset = max((item["update_id"] for item in stale
                          if type(item.get("update_id")) is int), default=-1) + 1
            try:
                self.state.write(offset)
            except OSError:
                self.events.log("offset_failed", kind="storage", stage="startup")
                raise
        self._sync_command_menu()
        with self._lock:
            self._running = True
        self.events.log("bot_started", stage="startup")
        try:
            self._notify_lifecycle()
        except Exception:
            # This advisory path cannot make an otherwise ready poller fail.
            self.events.log("lifecycle_failed", kind="unknown", stage="startup_notice")
        print("Telegram polling started (AGY on demand).", flush=True)
        backoff = 2
        last_heartbeat = time.monotonic() - 60.0
        try:
            while not self._stop.is_set():
                self._poll_questions()
                try:
                    updates = self.api.get_updates(offset, timeout=3)
                except TelegramError as exc:
                    delay = exc.retry_after if exc.retry_after is not None else backoff
                    self.events.log("poll_failed", kind=exc.kind, stage="get_updates",
                                    http_status=exc.http_status, retry_s=delay)
                    self._stop.wait(delay)
                    backoff = min(max(backoff * 2, delay), 30)
                    continue
                except OSError:
                    self.events.log("poll_failed", kind="transport", stage="get_updates",
                                    retry_s=backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
                now = time.monotonic()
                if updates or now - last_heartbeat >= 60:
                    self.events.log("poll_ok", stage="get_updates", count=len(updates))
                    last_heartbeat = now
                backoff = 2
                for item in updates:
                    if not isinstance(item, dict):
                        continue
                    update_id = item.get("update_id")
                    if type(update_id) is not int or update_id < offset:
                        continue
                    try:
                        self.handle_update(item)
                    except (OSError, ValueError, TypeError):
                        self.events.log("handler_failed", kind="unknown",
                                        stage="process_update")
                    finally:
                        offset = update_id + 1
                        try:
                            self.state.write(offset)
                        except OSError:
                            # Failing closed avoids replaying side-effecting
                            # requests indefinitely when offset storage breaks.
                            self.events.log("offset_failed", kind="storage",
                                            stage="offset_write")
                            raise
                self._poll_questions()
        finally:
            with self._lock:
                self._running = False
            self.events.log("bot_stopped", stage="shutdown")
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
    try:
        bot = TelegramAgyBot(settings, TelegramAPI(settings.bot_token))
    except (OSError, ValueError):
        print("Bot could not load private state; inspect local files.", flush=True)
        return 1
    def terminate(_sig, _frame):
        bot.stop()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        bot.run()
    except (TelegramError, ValueError, OSError):
        print("Bot stopped: inspect private diagnostic events and configuration.", flush=True)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
