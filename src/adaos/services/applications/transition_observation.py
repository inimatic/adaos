"""Bounded, read-only progress over the Core cutover journal; no data or effects."""
from __future__ import annotations

import os
from collections import Counter
from threading import Lock
from typing import Any, Mapping

_ACTIVE: Counter[tuple[str, str]] = Counter()
_LOCK = Lock()


def transition_worker(operation_id: str, worker_id: str, delta: int) -> None:
    with _LOCK:
        key = (operation_id, worker_id)
        _ACTIVE[key] += delta
        if _ACTIVE[key] <= 0:
            del _ACTIVE[key]


def observe_transition(record: Mapping[str, Any]) -> dict[str, Any]:
    progress = record.get("progress") or {}
    steps = list((record.get("intent") or {}).get("steps") or [])
    receipts = record.get("receipts") or {}
    state = "recovery_required"
    if record.get("aborted"):
        state = "cancelled"
    elif record.get("completed"):
        state = "succeeded"
    elif progress.get("state") == "applying" and progress.get("worker_pid") == os.getpid():
        with _LOCK:
            if _ACTIVE.get((str(record.get("operation_id")), str(progress.get("worker_id"))), 0):
                state = "applying"
    # An interrupted or legacy journal is not evidence of a live worker.
    # Another process may own it: report uncertainty, never start a new effect.
    return {
        "schema": "adaos.application.runtime_transition_progress.v1",
        "operation_id": record.get("operation_id"),
        "status": state,
        "phase": record.get("running_step") or state,
        "completed_steps": [step for step in steps if step in receipts],
        "total_steps": len(steps),
        "updated_at": progress.get("updated_at"),
        "failure": progress.get("failure"),
        "runtime_fenced": state not in {"succeeded", "cancelled"},
        "retry_allowed": False,
        "next_action": "wait" if state == "applying" else (
            "review_exact_recovery" if state == "recovery_required" else "none"),
    }
