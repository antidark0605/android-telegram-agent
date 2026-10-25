"""Map Bot state onto the vendored telegram-agent-ui presentation library.

Revision: 3 — 2026-10-25. Pure mapping only: quota collection, task control
and Telegram transport stay in their existing modules.
"""
from __future__ import annotations

import html
import time
import re

from rich_delivery import _html_fallback
from usage_footer import LOCAL_TIMEZONE
from vendor.telegram_ui import (
    DisplayProfile, QuotaWindow, ReplyView, UsageView,
    render_progress, render_reply, render_usage, utf16_length,
)

AGENT = "AGY"
DISPLAY_MODES = ("rich", "classic", "raw")
DISPLAY_LABELS = {"rich": "Rich（結構化）", "classic": "Classic（純文字）",
                  "raw": "Raw（無格式正文）"}
# Quota rows keep the established one-decimal format without a UTC offset.
PROFILE = DisplayProfile(fields=("model",), quota_precision=1,
                         show_reset_offset=False)
ARGON_PROFILE = DisplayProfile(fields=("model",), quota_precision=2,
                               show_reset_offset=False)
UPSTREAM_CANCEL = "task:cancel:"
# Bot-only states layered on upstream ones; neither claims completion.
DELIVERING_ACTIVITY = "📤 Delivering answer"
UNCONFIRMED_LINE = "**傳送**：⚠️ Delivery unconfirmed"
STATUS_STATES = frozenset({"running", "waiting", "delivering", "completed",
                           "cancelled", "failed", "unconfirmed"})
TELEGRAM_FORMATTED_LIMIT = 4096


def directory_listing_messages(path: str, body: str) -> list[tuple[str, str]]:
    """Return complete, UTF-16-bounded HTML chunks and plain fallbacks."""
    safe_path = html.escape(path, quote=False)
    html_header = ("<b>📂 Directory listing</b>\n"
                   f"<b>path</b>: <code>{safe_path}</code>\n"
                   "—————\n")
    plain_header = f"📂 Directory listing\npath: {path}\n—————\n"
    remaining = body or "(empty)"
    messages: list[tuple[str, str]] = []
    first = True
    while remaining:
        prefix = html_header if first else ""
        plain_prefix = plain_header if first else ""
        low, high, best = 1, len(remaining), 0
        while low <= high:
            middle = (low + high) // 2
            candidate = prefix + "<pre>" + html.escape(
                remaining[:middle], quote=False) + "</pre>"
            if utf16_length(candidate) <= TELEGRAM_FORMATTED_LIMIT:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError("Directory listing header exceeds Telegram limit")
        # Prefer a nearby complete line without creating tiny fragments.
        line_end = remaining.rfind("\n", 0, best)
        if line_end >= best // 2:
            best = line_end + 1
        piece, remaining = remaining[:best], remaining[best:]
        formatted = prefix + "<pre>" + html.escape(piece, quote=False) + "</pre>"
        messages.append((formatted, plain_prefix + piece))
        first = False
    return messages


def _family(model) -> str | None:
    value = model.lower() if isinstance(model, str) else ""
    if "argon" in value:
        return "argon"
    if "gemini" in value:
        return "gemini"
    if "claude" in value or "gpt" in value:
        return "3p"
    return None


def _window(row) -> QuotaWindow | None:
    if not isinstance(row, dict):
        return None
    try:
        reset = row.get("reset")
        if reset is not None:
            reset = reset.astimezone(LOCAL_TIMEZONE)
        return QuotaWindow(row["left"], reset)
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        return None


def _profile(model) -> DisplayProfile:
    return ARGON_PROFILE if _family(model) == "argon" else PROFILE


def _explicit_not_applicable(text: str, model) -> str:
    if _family(model) != "argon":
        return text
    return "\n".join("> 5h: N/A" if line == "> 5h: unavailable" else line
                     for line in text.split("\n"))


def usage_view(model, data, cached=False) -> UsageView:
    """Verified parse_quota rows only; context occupancy is never inferred."""
    family = _family(model)
    rows = data if family and isinstance(data, dict) else {}
    return UsageView(context=None,
                     five_hour=_window(rows.get(f"{family}-5h")),
                     weekly=_window(rows.get(f"{family}-weekly")),
                     cached=cached is True)


def usage_markdown(model, data, cached=False) -> str:
    """Render one model-scoped usage footer with explicit provider N/A states."""
    return _explicit_not_applicable(
        render_usage(usage_view(model, data, cached), profile=_profile(model)), model)


def reply_markdown(body: str, model, data, cached=False) -> str:
    """Header with the task's own model, the untouched body, then Usage."""
    usage = usage_view(model, data, cached)
    try:
        view = ReplyView(AGENT, model if isinstance(model, str) else "unavailable",
                         body, usage)
    except ValueError:
        view = ReplyView(AGENT, "unavailable", body, usage)
    return _explicit_not_applicable(render_reply(view, profile=_profile(model)), model)


def usage_plain(model, data, cached=False) -> str:
    """Usage rows for the plain /quota reply, without quote markers."""
    text = usage_markdown(model, data, cached)
    return "\n".join(line.removeprefix("> ") for line in text.splitlines()
                     if line != ">")


def normalize_display_mode(value) -> str:
    if value not in DISPLAY_MODES:
        return "rich"
    return value


def display_keyboard(current: str) -> dict:
    """Small, callback-safe per-topic display-mode menu."""
    current = normalize_display_mode(current)
    return {"inline_keyboard": [[
        {"text": ("✓ " if mode == current else "") + DISPLAY_LABELS[mode],
         "callback_data": "display:" + mode}
        for mode in DISPLAY_MODES
    ]]}


def render_classic(body: str, model, data, cached=False) -> str:
    """Readable unparsed reply; no Markdown is sent with a missing parse mode."""
    safe_model = model if isinstance(model, str) and "\n" not in model else "unavailable"
    plain_body = body or "(empty response)"
    plain_body = re.sub(r"^\s{0,3}(?:#{1,6}\s+|```[^\n]*|~~~[^\n]*)$", "", plain_body,
                        flags=re.MULTILINE)
    plain_body = re.sub(r"\*\*(.+?)\*\*", r"\1", plain_body)
    plain_body = re.sub(r"(?<!\*)\*([^*\n]+)\*", r"\1", plain_body)
    plain_body = re.sub(r"`([^`\n]+)`", r"\1", plain_body)
    plain_body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", plain_body)
    usage = usage_plain(safe_model, data, cached)
    usage = usage.replace("**", "").replace("`", "").replace("\\-", "-")
    return f"⚡ [AGY] · {safe_model}\n\n{plain_body}\n\n{usage}"


def render_response(body: str, model, data, cached=False, mode="rich") -> str:
    """Choose one explicit per-topic response representation."""
    mode = normalize_display_mode(mode)
    if mode == "raw":
        return body or "(empty response)"
    if mode == "classic":
        return render_classic(body, model, data, cached)
    return reply_markdown(body, model, data, cached)


def stop_markup(markup: dict, task_key: str) -> dict:
    """Translate upstream task:cancel:<id> into the Bot's stop:<id> callback."""
    rows = []
    for row in markup.get("inline_keyboard", []):
        buttons = []
        for button in row:
            if button.get("callback_data") != UPSTREAM_CANCEL + task_key:
                raise ValueError("Unexpected progress button")
            buttons.append({"text": button["text"], "callback_data": "stop:" + task_key})
        rows.append(buttons)
    return {"inline_keyboard": rows}


def progress_message(state: str, started: float, task_key: str,
                     now: float | None = None) -> tuple[str, dict]:
    """Return Telegram HTML text and reply_markup for one status message."""
    if state not in STATUS_STATES:
        raise ValueError("Unknown status state")
    elapsed = max(0, int((time.monotonic() if now is None else now) - started))
    upstream = {"delivering": "running", "unconfirmed": "failed"}.get(state, state)
    view = render_progress(state=upstream, elapsed_seconds=elapsed, task_id=task_key,
                           activity=DELIVERING_ACTIVITY if state == "delivering" else None,
                           flow=None)
    text = view.text + ("\n" + UNCONFIRMED_LINE if state == "unconfirmed" else "")
    # Stop is unavailable once the agent finished and delivery has begun.
    markup = ({"inline_keyboard": []} if state == "delivering"
              else stop_markup(view.reply_markup, task_key))
    return _html_fallback(text), markup
