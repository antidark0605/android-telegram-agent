"""Immutable view data and pure Telegram Markdown rendering."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping

GAP = '\n\n\u00a0\n\n'
STATES = {
    'running': '執行中', 'waiting': '等待回覆', 'completed': '已完成',
    'cancelled': '已取消', 'failed': '失敗',
}
TERMINAL = frozenset({'completed', 'cancelled', 'failed'})


def _label(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError('Expected a nonempty label of at most 300 characters')
    if any(ord(c) < 32 or ord(c) == 127 or c in '\u0085\u2028\u2029' for c in value):
        raise ValueError('Labels must occupy one line')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~\-])', r'\\\1', value)


@dataclass(frozen=True)
class ContextUsage:
    used_tokens: int
    window_tokens: int

    def __post_init__(self):
        if (type(self.used_tokens) is not int or type(self.window_tokens) is not int
                or self.window_tokens <= 0 or not 0 <= self.used_tokens <= self.window_tokens):
            raise ValueError('Invalid context occupancy')


@dataclass(frozen=True)
class QuotaWindow:
    remaining_percent: float
    resets_at: datetime | None = None
    used_percent: float | None = None

    def __post_init__(self):
        value = self.remaining_percent
        if (type(value) not in (int, float) or not 0 <= value <= 100
                or not math.isfinite(value)):
            raise ValueError('Invalid remaining percentage')
        at = self.resets_at
        if at is not None and (not isinstance(at, datetime) or at.utcoffset() is None):
            raise ValueError('Reset time requires a timezone')
        used = self.used_percent
        if used is not None and (type(used) not in (int, float) or not 0 <= used <= 100
                                 or not math.isfinite(used)):
            raise ValueError('Invalid used percentage')


@dataclass(frozen=True)
class QuotaRow:
    label: str
    window: QuotaWindow | None = None

    def __post_init__(self):
        _label(self.label)
        if self.window is not None and not isinstance(self.window, QuotaWindow):
            raise ValueError('Expected QuotaWindow')


@dataclass(frozen=True)
class ReplyIdentity:
    display_name: str | None = None
    effort: str | None = None
    account: str | None = None
    speed: str | None = None

    def __post_init__(self):
        for value in (self.display_name, self.effort, self.account, self.speed):
            if value is not None:
                _label(value)


@dataclass(frozen=True)
class DisplayProfile:
    icons: tuple[tuple[str, str], ...] = ()
    fallback_icon: str = '⚡'
    fields: tuple[str, ...] = ('model', 'effort', 'account', 'speed')
    account_mode: str = 'append'
    hide_effort_for: tuple[str, ...] = ()
    quota_precision: int = 1
    show_reset_offset: bool = True

    def __post_init__(self):
        _label(self.fallback_icon)
        if not isinstance(self.icons, tuple):
            raise ValueError('Expected immutable icon pairs')
        keys = []
        for pair in self.icons:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError('Expected agent/icon pair')
            for value in pair:
                _label(value)
            keys.append(pair[0])
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate agent icon')
        if (not isinstance(self.fields, tuple)
                or any(type(v) is not str or v not in ('model', 'effort', 'account', 'speed')
                       for v in self.fields)
                or len(self.fields) != len(set(self.fields))):
            raise ValueError('Invalid or duplicate identity field')
        if self.account_mode not in ('append', 'replace', 'hide'):
            raise ValueError('Unknown account display mode')
        if not isinstance(self.hide_effort_for, tuple):
            raise ValueError('Expected immutable agent ids')
        for agent in self.hide_effort_for:
            _label(agent)
        if type(self.quota_precision) is not int or not 0 <= self.quota_precision <= 4:
            raise ValueError('Quota precision must be an integer from zero to four')
        if type(self.show_reset_offset) is not bool:
            raise ValueError('Expected reset offset boolean')

    @classmethod
    def from_dict(cls, config: Mapping) -> DisplayProfile:
        """Validate an already parsed configuration; never reads files."""
        if not isinstance(config, Mapping) or any(k not in cls.__dataclass_fields__ for k in config):
            raise ValueError('Unknown display configuration key or invalid mapping')
        data = dict(config)
        if 'icons' in data:
            if not isinstance(data['icons'], Mapping):
                raise ValueError('Expected icon mapping')
            data['icons'] = tuple(data['icons'].items())
        for key in ('fields', 'hide_effort_for'):
            if key in data:
                if not isinstance(data[key], (list, tuple)):
                    raise ValueError('Expected configuration array')
                data[key] = tuple(data[key])
        return cls(**data)


@dataclass(frozen=True)
class UsageView:
    context: ContextUsage | None = None
    five_hour: QuotaWindow | None = None
    weekly: QuotaWindow | None = None
    cached: bool = False
    quota_rows: tuple[QuotaRow, ...] | None = None

    def __post_init__(self):
        if self.context is not None and not isinstance(self.context, ContextUsage):
            raise ValueError('Expected ContextUsage')
        if any(v is not None and not isinstance(v, QuotaWindow) for v in (self.five_hour, self.weekly)):
            raise ValueError('Expected QuotaWindow')
        if type(self.cached) is not bool:
            raise ValueError('Expected cached boolean')
        if self.quota_rows is not None:
            if (not isinstance(self.quota_rows, tuple)
                    or any(not isinstance(row, QuotaRow) for row in self.quota_rows)):
                raise ValueError('Expected immutable quota rows')
            if self.five_hour is not None or self.weekly is not None:
                raise ValueError('Custom quota rows cannot override legacy windows')
            labels = [row.label for row in self.quota_rows]
            if len(labels) != len(set(labels)):
                raise ValueError('Duplicate quota row label')


@dataclass(frozen=True)
class ReplyView:
    agent: str
    model: str | None
    body: str
    usage: UsageView | None = None
    identity: ReplyIdentity | None = None

    def __post_init__(self):
        _label(self.agent)
        if self.model is not None:
            _label(self.model)
        if not isinstance(self.body, str):
            raise ValueError('Expected Markdown body')
        if self.usage is not None and not isinstance(self.usage, UsageView):
            raise ValueError('Expected UsageView')
        if self.identity is not None and not isinstance(self.identity, ReplyIdentity):
            raise ValueError('Expected ReplyIdentity')


@dataclass(frozen=True)
class ProgressView:
    text: str
    reply_markup: dict


def _window(label: str, data: QuotaWindow | None, profile: DisplayProfile) -> str:
    label = _label(label)
    if data is None:
        return label + ': unavailable'
    left = data.remaining_percent
    used = 100-left if data.used_percent is None else data.used_percent
    precision = profile.quota_precision
    row = f'{label}: {used:.{precision}f}% used · {left:.{precision}f}% left'
    if data.resets_at is not None:
        row += f' · resets {data.resets_at:%m/%d %H:%M}'
        if profile.show_reset_offset:
            row += f' {data.resets_at:%z}'
    return row


def render_usage(view: UsageView, *, profile: DisplayProfile = DisplayProfile()) -> str:
    if not isinstance(profile, DisplayProfile):
        raise ValueError('Expected DisplayProfile')
    context = 'Context: unavailable'
    if view.context is not None:
        data = view.context
        context = (f'Context: {data.used_tokens:,} / {data.window_tokens:,} tokens '
                   f'({data.used_tokens / data.window_tokens * 100:.2f}%)')
    windows = view.quota_rows
    if windows is None:
        windows = (QuotaRow('5h', view.five_hour), QuotaRow('Weekly', view.weekly))
    rows = ['📊 **Usage**' + (' · cached' if view.cached else ''), context]
    rows.extend(_window(row.label, row.window, profile) for row in windows)
    # Quote paragraphs retain row boundaries when Markdown soft breaks fold.
    return '\n>\n'.join('> ' + row for row in rows)


def render_reply(view: ReplyView, *, profile: DisplayProfile = DisplayProfile()) -> str:
    if not isinstance(profile, DisplayProfile):
        raise ValueError('Expected DisplayProfile')
    identity = view.identity or ReplyIdentity()
    name = identity.display_name or view.agent
    if profile.account_mode == 'replace' and identity.account is not None:
        name = identity.account
    icon = dict(profile.icons).get(view.agent, profile.fallback_icon)
    header = f'{_label(icon)} **[{_label(name)}]**'
    values = {'model': view.model, 'effort': identity.effort,
              'account': identity.account, 'speed': identity.speed}
    if profile.account_mode != 'append':
        values['account'] = None
    if view.agent in profile.hide_effort_for:
        values['effort'] = None
    for field in profile.fields:
        if values[field] is not None:
            header += ' · ' + _label(values[field])
    answer = header + GAP + view.body
    return answer if view.usage is None else answer + GAP + render_usage(view.usage, profile=profile)


def render_progress(*, state: str, elapsed_seconds: int, task_id: str,
                    activity: str | None = None,
                    flow: tuple[str, ...] | None = ('分析', '修改', '測試', '完成')) -> ProgressView:
    if state not in STATES:
        raise ValueError('Unknown task state')
    if type(elapsed_seconds) is not int or elapsed_seconds < 0:
        raise ValueError('Elapsed seconds must be a nonnegative integer')
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', task_id):
        raise ValueError('Invalid opaque task identifier')
    lines = ['**任務進度**', '—————']
    if flow is not None:
        if not isinstance(flow, tuple) or not 1 <= len(flow) <= 8:
            raise ValueError('Expected one to eight flow labels')
        lines.append('**流程**：' + ' → '.join(_label(step) for step in flow))
    lines += [f'**目前**：{STATES[state]}', f'**已執行**：{elapsed_seconds} 秒']
    if activity is not None and state not in TERMINAL:
        lines.append('**最近活動**：' + _label(activity))
    buttons = [] if state in TERMINAL else [[{
        'text': '🛑 中斷', 'callback_data': 'task:cancel:' + task_id,
    }]]
    return ProgressView('\n'.join(lines), {'inline_keyboard': buttons})


def utf16_length(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def split_text(text: str, limit: int = 4096) -> list[str]:
    """Plain-text splitting; preserves all characters, including newlines.

    This does not split Markdown with formatting guarantees. The caller must
    select its appropriate Telegram rendering mode for these plain chunks.
    """
    if type(limit) is not int or limit < 2:
        raise ValueError('Limit must allow a complete Unicode code point')
    chunks, start = [], 0
    while start < len(text):
        end, units, last_break = start, 0, None
        while end < len(text):
            width = utf16_length(text[end])
            if units + width > limit:
                break
            units += width
            end += 1
            if text[end - 1] == '\n':
                last_break = end
        if end < len(text) and last_break is not None:
            end = last_break
        chunks.append(text[start:end])
        start = end
    return chunks
