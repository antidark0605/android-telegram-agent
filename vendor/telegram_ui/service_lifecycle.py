"""Pure service lifecycle notices; all event evidence belongs to the caller."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .rendering import _label

__all__ = ['ServiceLifecycleView', 'render_service_lifecycle']

_STATES = {
    'ready': '服務已就緒',
    'restarted': '服務已重新啟動並就緒',
    'recovered': '服務已恢復並就緒',
}
_REASONS = {
    'external_restart': '外部重新啟動服務',
    'host_reboot': '主機重新開機',
}


def _lifecycle_label(value: str) -> str:
    # Enforce this even when installed with the older label helper.
    if isinstance(value, str) and any(c in value for c in '\u0085\u2028\u2029'):
        raise ValueError('Labels must occupy one line')
    return _label(value)


@dataclass(frozen=True)
class ServiceLifecycleView:
    """Verified gateway readiness, never native model/task readiness.

    ``started_at`` is the caller-verified service-instance start time, not the
    notification time. ``reason=None`` means the cause has not been verified.
    Labels and release identifiers must already be safe for public display.
    """

    agent_label: str
    state: Literal['ready', 'restarted', 'recovered']
    started_at: datetime
    release: str | None = None
    reason: Literal['external_restart', 'host_reboot'] | None = None

    def __post_init__(self):
        _lifecycle_label(self.agent_label)
        if type(self.state) is not str or self.state not in _STATES:
            raise ValueError('Unknown service lifecycle state')
        if not isinstance(self.started_at, datetime) or self.started_at.utcoffset() is None:
            raise ValueError('Service start time requires a timezone')
        if self.release is not None:
            _lifecycle_label(self.release)
        if self.reason is not None and (type(self.reason) is not str or self.reason not in _REASONS):
            raise ValueError('Unknown service lifecycle reason')


def render_service_lifecycle(view: ServiceLifecycleView) -> str:
    """Return Markdown only; does not discover, route or deliver an event."""
    if not isinstance(view, ServiceLifecycleView):
        raise ValueError('Expected ServiceLifecycleView')
    lines = [
        '🔔 **服務通知**',
        '**服務**：' + _lifecycle_label(view.agent_label),
        '**狀態**：' + _STATES[view.state],
        '**啟動時間**：`' + view.started_at.isoformat(timespec='seconds') + '`',
        '**原因**：' + (_REASONS[view.reason] if view.reason is not None else '原因未確認'),
    ]
    if view.release is not None:
        lines.append('**版本**：' + _lifecycle_label(view.release))
    return '\n'.join(lines)
