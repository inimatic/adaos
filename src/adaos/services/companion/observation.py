"""Correlate browser acknowledgements with executor-created action identities."""
from __future__ import annotations

import threading
from copy import deepcopy

from .policy import enabled

_LOCK = threading.RLock()
_UI: dict[str, dict] = {}
_BOUND: set[int] = set()
OUTCOMES = {
    "desktop.modal.opened": ("ui.modal.open", "completed"),
    "desktop.modal.open_failed": ("ui.modal.open", "failed"),
    "desktop.modal.closed": ("ui.modal.close", "completed"),
    "desktop.modal.close_failed": ("ui.modal.close", "failed"),
    "ui.state.set.applied": ("ui.state.set", "completed"),
    "ui.state.set.failed": ("ui.state.set", "failed"),
    "ui.focus_widget.applied": ("ui.widget.focus", "completed"),
    "ui.focus_widget.failed": ("ui.widget.focus", "failed"),
    "desktop.scenario.changed": ("ui.scenario.open", "completed"),
    "desktop.webspace.home.opened": ("ui.home.open", "completed"),
    "voice.capability.activated": ("ui.affordance.activate", "completed"),
    "voice.capability.failed": ("ui.affordance.activate", "failed"),
}


def live_ui(webspace: str) -> dict:
    with _LOCK:
        return deepcopy(_UI.get(webspace, {}))


def on_outcome(event) -> None:
    if not enabled():
        return
    from adaos.services.root_mcp import companion_plane as plane
    from .ledger import Ledger

    topic = str(getattr(event, "type", ""))
    payload = getattr(event, "payload", {}) or {}
    meta = payload.get("_meta") or {}
    action_id = payload.get("request_id") or meta.get("request_id")
    webspace = payload.get("webspace_id") or meta.get("webspace_id")
    if topic not in OUTCOMES or not webspace:
        return
    operation, status = OUTCOMES[topic]
    with _LOCK:
        ui = _UI.setdefault(webspace, {})
        if status == "completed":
            if operation == "ui.modal.open":
                ui["active_modal"] = payload.get("modal_id")
            elif operation == "ui.modal.close":
                ui["active_modal"] = None
            elif operation == "ui.state.set":
                ui.setdefault("page_state", {})[str(payload.get("key"))] = payload.get("value")
    if not action_id:
        return
    receipt = next((r for r in plane._recent_receipts(webspace_id=webspace, limit=100) if r["action_id"] == action_id), None)
    if not receipt or receipt["operation"] != operation or receipt["status"] not in {"accepted", "dispatched"}:
        return
    observation = {k: payload[k] for k in ("modal_id", "key", "value", "widget_id", "scenario_id", "reason", "error") if k in payload}
    receipt.update(status=status, completed_at=plane._iso_now(), observation={"topic": topic, **observation})
    if status == "failed":
        receipt["error"] = {"code": str(payload.get("reason") or "browser_action_failed")}
    plane._write_receipt(receipt)
    if receipt.get("session_id"):
        try:
            Ledger().append(receipt["session_id"], "observation", receipt, turn=receipt.get("turn_id"), actor="browser")
        except ValueError as exc:
            if str(exc) != "session_sealed":
                raise


def bind(bus) -> None:
    with _LOCK:
        if id(bus) in _BOUND:
            return
        _BOUND.add(id(bus))
    for topic in OUTCOMES:
        bus.subscribe(topic, on_outcome)
