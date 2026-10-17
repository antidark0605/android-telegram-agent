"""Map Bot state onto the vendored telegram-agent-ui presentation library.

Revision: 1 — 2026-10-17. Pure mapping only: quota collection, task control
and Telegram transport stay in their existing modules.
"""
from __future__ import annotations

import time

from rich_delivery import _html_fallback
from usage_footer import LOCAL_TIMEZONE
from vendor.telegram_ui import (
    DisplayProfile, QuotaWindow, ReplyView, UsageView,
    render_progress, render_reply, render_usage,
)

AGENT = "AGY"
# Quota rows keep the established one-decimal format without a UTC offset.
PROFILE = DisplayProfile(fields=("model",), quota_precision=1,
                         show_reset_offset=False)
UPSTREAM_CANCEL = "task:cancel:"
# Bot-only states layered on upstream ones; neither claims completion.
DELIVERING_ACTIVITY = "📤 Delivering answer"
UNCONFIRMED_LINE = "**傳送**：⚠️ Delivery unconfirmed"
STATUS_STATES = frozenset({"running", "waiting", "delivering", "completed",
                           "cancelled", "failed", "unconfirmed"})


def _family(model) -> str | None:
    if isinstance(model, str) and model.startswith("gemini-"):
        return "gemini"
    if isinstance(model, str) and model.startswith(("claude-", "gpt-")):
        return "3p"
    return None


def _window(row) -> QuotaWindow | None:
    if not isinstance(row, dict):
        return None
    try:
        return QuotaWindow(row["left"], row["reset"].astimezone(LOCAL_TIMEZONE))
    except (KeyError, AttributeError, TypeError, ValueError, OverflowError):
        return None


def usage_view(model, data, cached=False) -> UsageView:
    """Verified parse_quota rows only; context occupancy is never inferred."""
    family = _family(model)
    rows = data if family and isinstance(data, dict) else {}
    return UsageView(context=None,
                     five_hour=_window(rows.get(f"{family}-5h")),
                     weekly=_window(rows.get(f"{family}-weekly")),
                     cached=cached is True)


def reply_markdown(body: str, model, data, cached=False) -> str:
    """Header with the task's own model, the untouched body, then Usage."""
    usage = usage_view(model, data, cached)
    try:
        view = ReplyView(AGENT, model if isinstance(model, str) else "unavailable",
                         body, usage)
    except ValueError:
        view = ReplyView(AGENT, "unavailable", body, usage)
    return render_reply(view, profile=PROFILE)


def usage_plain(model, data, cached=False) -> str:
    """Usage rows for the plain /quota reply, without quote markers."""
    text = render_usage(usage_view(model, data, cached), profile=PROFILE)
    return "\n".join(line.removeprefix("> ") for line in text.splitlines()
                     if line != ">")


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
