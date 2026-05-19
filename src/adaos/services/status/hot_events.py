from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HotEventDecision:
    admitted: bool
    reason: str
    key: str
    retry_after_ms: int = 0
    suppressed_total: int = 0
    coalesced_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason": self.reason,
            "key": self.key,
            "retry_after_ms": self.retry_after_ms,
            "suppressed_total": self.suppressed_total,
            "coalesced_total": self.coalesced_total,
        }


@dataclass(slots=True)
class _HotEventState:
    window_started_at: float = 0.0
    window_count: int = 0
    last_admitted_at: float = 0.0
    suppressed_total: int = 0
    coalesced_total: int = 0
    last_reason: str = ""


class HotEventBudget:
    def __init__(
        self,
        *,
        debounce_ms: int = 1000,
        window_ms: int = 10000,
        max_events: int = 5,
    ) -> None:
        self._debounce_ms = max(0, int(debounce_ms or 0))
        self._window_ms = max(1, int(window_ms or 1))
        self._max_events = max(1, int(max_events or 1))
        self._lock = threading.RLock()
        self._states: dict[str, _HotEventState] = {}

    def admit(
        self,
        topic: str,
        *,
        key: str | None = None,
        now_ts: float | None = None,
        debounce_ms: int | None = None,
        window_ms: int | None = None,
        max_events: int | None = None,
    ) -> HotEventDecision:
        topic_id = str(topic or "").strip() or "unknown"
        key_id = str(key or "").strip()
        budget_key = f"{topic_id}/{key_id}" if key_id else topic_id
        now = float(now_ts if now_ts is not None else time.time())
        debounce_s = max(0, int(self._debounce_ms if debounce_ms is None else debounce_ms)) / 1000.0
        window_s = max(0.001, int(self._window_ms if window_ms is None else window_ms) / 1000.0)
        limit = max(1, int(self._max_events if max_events is None else max_events))
        with self._lock:
            state = self._states.get(budget_key)
            if state is None:
                state = _HotEventState(window_started_at=now)
                self._states[budget_key] = state
            if now - state.window_started_at >= window_s:
                state.window_started_at = now
                state.window_count = 0
            if state.last_admitted_at and debounce_s and now - state.last_admitted_at < debounce_s:
                retry = int(max(0.0, debounce_s - (now - state.last_admitted_at)) * 1000)
                state.suppressed_total += 1
                state.coalesced_total += 1
                state.last_reason = "debounce"
                return HotEventDecision(
                    admitted=False,
                    reason="debounce",
                    key=budget_key,
                    retry_after_ms=retry,
                    suppressed_total=state.suppressed_total,
                    coalesced_total=state.coalesced_total,
                )
            if state.window_count >= limit:
                retry = int(max(0.0, window_s - (now - state.window_started_at)) * 1000)
                state.suppressed_total += 1
                state.coalesced_total += 1
                state.last_reason = "budget_exceeded"
                return HotEventDecision(
                    admitted=False,
                    reason="budget_exceeded",
                    key=budget_key,
                    retry_after_ms=retry,
                    suppressed_total=state.suppressed_total,
                    coalesced_total=state.coalesced_total,
                )
            state.window_count += 1
            state.last_admitted_at = now
            state.last_reason = "admitted"
            return HotEventDecision(
                admitted=True,
                reason="admitted",
                key=budget_key,
                suppressed_total=state.suppressed_total,
                coalesced_total=state.coalesced_total,
            )

    def snapshot(self, *, now_ts: float | None = None) -> dict[str, Any]:
        now = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            items = [
                {
                    "key": key,
                    "window_age_s": max(0.0, now - state.window_started_at),
                    "window_count": state.window_count,
                    "last_admitted_at": state.last_admitted_at or None,
                    "suppressed_total": state.suppressed_total,
                    "coalesced_total": state.coalesced_total,
                    "last_reason": state.last_reason or None,
                }
                for key, state in sorted(self._states.items())
            ]
        return {
            "schema": "adaos.hot_event_budget.v1",
            "updated_at": now,
            "debounce_ms": self._debounce_ms,
            "window_ms": self._window_ms,
            "max_events": self._max_events,
            "total": len(items),
            "items": items,
        }


__all__ = ["HotEventBudget", "HotEventDecision"]
