from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services import named_entities
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.named_entities.projection")

_DIAGNOSTICS_LOCK = threading.RLock()
_DIAGNOSTICS: dict[str, Any] = {
    "schema": "adaos.named-entity-projection.diagnostics.v1",
    "attempt_total": 0,
    "written_total": 0,
    "unchanged_total": 0,
    "live_room_total": 0,
    "detached_total": 0,
    "pending_total": 0,
    "reconcile_total": 0,
    "coalesced_total": 0,
    "error_total": 0,
    "last_webspace_id": None,
    "last_outcome": None,
    "last_payload_bytes": 0,
    "last_timings_ms": {},
    "last_updated_at": None,
}


def _elapsed_ms(started_at: float) -> float:
    return round(max(0.0, time.perf_counter() - started_at) * 1000.0, 3)


def _payload_size_bytes(payload: Mapping[str, Any]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except Exception:
        return 0


def _record_projection_attempt(
    *,
    webspace_id: str,
    outcome: str,
    payload_bytes: int,
    timings_ms: Mapping[str, float],
) -> None:
    with _DIAGNOSTICS_LOCK:
        _DIAGNOSTICS["attempt_total"] = int(_DIAGNOSTICS.get("attempt_total") or 0) + 1
        counter = {
            "written": "written_total",
            "unchanged": "unchanged_total",
            "live_room": "live_room_total",
            "detached": "detached_total",
            "pending": "pending_total",
            "error": "error_total",
        }.get(outcome)
        if counter:
            _DIAGNOSTICS[counter] = int(_DIAGNOSTICS.get(counter) or 0) + 1
        if outcome in {"live_room", "detached"}:
            _DIAGNOSTICS["written_total"] = int(_DIAGNOSTICS.get("written_total") or 0) + 1
        _DIAGNOSTICS["last_webspace_id"] = webspace_id
        _DIAGNOSTICS["last_outcome"] = outcome
        _DIAGNOSTICS["last_payload_bytes"] = int(payload_bytes)
        _DIAGNOSTICS["last_timings_ms"] = {
            str(key): round(float(value), 3) for key, value in timings_ms.items()
        }
        _DIAGNOSTICS["last_updated_at"] = time.time()


def named_entity_projection_diagnostics_snapshot() -> dict[str, Any]:
    with _DIAGNOSTICS_LOCK:
        snapshot = dict(_DIAGNOSTICS)
        snapshot["last_timings_ms"] = dict(_DIAGNOSTICS.get("last_timings_ms") or {})
    return snapshot


def reset_named_entity_projection_diagnostics() -> None:
    with _DIAGNOSTICS_LOCK:
        schema = str(_DIAGNOSTICS.get("schema") or "adaos.named-entity-projection.diagnostics.v1")
        _DIAGNOSTICS.clear()
        _DIAGNOSTICS.update(
            {
                "schema": schema,
                "attempt_total": 0,
                "written_total": 0,
                "unchanged_total": 0,
                "live_room_total": 0,
                "detached_total": 0,
                "pending_total": 0,
                "reconcile_total": 0,
                "coalesced_total": 0,
                "error_total": 0,
                "last_webspace_id": None,
                "last_outcome": None,
                "last_payload_bytes": 0,
                "last_timings_ms": {},
                "last_updated_at": None,
            }
        )


def _payload(evt: Any) -> dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")
        return data if isinstance(data, dict) else {}
    return {}


def _topic(evt: Any) -> str:
    if isinstance(evt, dict):
        return str(evt.get("type") or evt.get("topic") or "").strip()
    return str(getattr(evt, "type", "") or getattr(evt, "topic", "") or "").strip()


def _resolve_webspace_id(payload: Mapping[str, Any] | None = None) -> str:
    payload = payload if isinstance(payload, Mapping) else {}
    scope = payload.get("scope") if isinstance(payload.get("scope"), Mapping) else {}
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    token = (
        payload.get("webspace_id")
        or payload.get("workspace_id")
        or scope.get("webspace_id")
        or meta.get("webspace_id")
        or meta.get("workspace_id")
    )
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _write_payload_to_doc(ydoc: Any, txn: Any, payload: Mapping[str, Any]) -> bool:
    registry_map = ydoc.get_map("registry")
    current = registry_map.get("named_entities")
    current_summary = current.get("summary") if isinstance(current, Mapping) else {}
    next_summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if (
        isinstance(current_summary, Mapping)
        and current_summary.get("fingerprint")
        and current_summary.get("fingerprint") == next_summary.get("fingerprint")
    ):
        return False
    registry_map.set(txn, "named_entities", dict(payload))
    return True


_RECONCILE_LOCK = threading.RLock()
_RECONCILE_STATES: dict[str, dict[str, Any]] = {}


def _new_reconcile_state(webspace_id: str) -> dict[str, Any]:
    return {
        "webspace_id": webspace_id,
        "requested_generation": 0,
        "processed_generation": 0,
        "desired_revision": 0,
        "applied_revision": 0,
        "desired_fingerprint": None,
        "applied_fingerprint": None,
        "pending": False,
        "in_flight": False,
        "refresh_required": False,
        "last_reason": None,
        "last_outcome": None,
        "last_error": None,
        "last_updated_at": None,
        "task": None,
    }


def _reconcile_state(webspace_id: str) -> dict[str, Any]:
    state = _RECONCILE_STATES.get(webspace_id)
    if state is None:
        state = _new_reconcile_state(webspace_id)
        _RECONCILE_STATES[webspace_id] = state
    return state


def _public_reconcile_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key != "task"
    }


def named_entity_projection_reconciler_snapshot(*, webspace_id: str | None = None) -> dict[str, Any]:
    selected = str(webspace_id or "").strip()
    with _RECONCILE_LOCK:
        states = list(_RECONCILE_STATES.values())
        if selected:
            states = [state for state in states if state["webspace_id"] == selected]
        return {
            "schema": "adaos.named-entity-projection.reconciler.v1",
            "webspace_id": selected or None,
            "state_total": len(states),
            "pending_total": sum(1 for state in states if state.get("pending")),
            "in_flight_total": sum(1 for state in states if state.get("in_flight")),
            "states": [_public_reconcile_state(state) for state in states],
            "updated_at": time.time(),
        }


def clear_named_entity_projection_reconciler(*, webspace_id: str | None = None) -> None:
    selected = str(webspace_id or "").strip()
    with _RECONCILE_LOCK:
        keys = [selected] if selected else list(_RECONCILE_STATES)
        for key in keys:
            state = _RECONCILE_STATES.pop(key, None)
            task = state.get("task") if isinstance(state, Mapping) else None
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()


def _apply_snapshot_to_live_room(snapshot: named_entities.NamedEntityRegistrySnapshot) -> dict[str, Any]:
    from adaos.services.yjs.doc import mutate_live_room

    payload = dict(snapshot.payload)
    changed = {"value": False}

    def _apply(ydoc: Any, txn: Any) -> None:
        changed["value"] = _write_payload_to_doc(ydoc, txn, payload)

    accepted = mutate_live_room(
        snapshot.webspace_id,
        _apply,
        root_names=["registry"],
        source="named_entity_projection",
        owner="core:named_entities",
        channel="core.named_entities.live_room",
    )
    return {
        "accepted": bool(accepted),
        "written": bool(changed["value"]) if accepted else False,
        "payload": payload,
    }


async def _run_reconciler(webspace_id: str) -> None:
    current_task = asyncio.current_task()
    try:
        while True:
            with _RECONCILE_LOCK:
                state = _reconcile_state(webspace_id)
                target_generation = int(state["requested_generation"])
                refresh = bool(state["refresh_required"])
                state["refresh_required"] = False
                state["in_flight"] = True
                state["last_error"] = None
            total_started = time.perf_counter()
            build_started = time.perf_counter()
            if refresh:
                snapshot = await asyncio.to_thread(
                    named_entities.refresh_named_entity_registry_snapshot,
                    webspace_id=webspace_id,
                )
            else:
                snapshot = await asyncio.to_thread(
                    named_entities.named_entity_registry_snapshot,
                    webspace_id=webspace_id,
                )
            timings_ms = {"snapshot_build": _elapsed_ms(build_started)}
            payload_bytes = _payload_size_bytes(snapshot.payload)
            apply_started = time.perf_counter()
            result = _apply_snapshot_to_live_room(snapshot)
            timings_ms["live_room_apply"] = _elapsed_ms(apply_started)
            timings_ms["total"] = _elapsed_ms(total_started)
            with _RECONCILE_LOCK:
                state = _reconcile_state(webspace_id)
                state["processed_generation"] = target_generation
                state["desired_revision"] = snapshot.revision
                state["desired_fingerprint"] = snapshot.fingerprint
                state["last_updated_at"] = time.time()
                if result["accepted"]:
                    state["applied_revision"] = snapshot.revision
                    state["applied_fingerprint"] = snapshot.fingerprint
                    state["pending"] = False
                    state["last_outcome"] = "written" if result["written"] else "unchanged"
                else:
                    state["pending"] = True
                    state["last_outcome"] = "pending_room"
                more_work = (
                    int(state["requested_generation"]) > target_generation
                    or bool(state["refresh_required"])
                )
            with _DIAGNOSTICS_LOCK:
                _DIAGNOSTICS["reconcile_total"] = int(_DIAGNOSTICS.get("reconcile_total") or 0) + 1
            _record_projection_attempt(
                webspace_id=webspace_id,
                outcome=(
                    "pending"
                    if not result["accepted"]
                    else "live_room"
                    if result["written"]
                    else "unchanged"
                ),
                payload_bytes=payload_bytes,
                timings_ms=timings_ms,
            )
            if not more_work:
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        with _RECONCILE_LOCK:
            state = _reconcile_state(webspace_id)
            state["pending"] = True
            state["last_outcome"] = "error"
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            state["last_updated_at"] = time.time()
        _record_projection_attempt(
            webspace_id=webspace_id,
            outcome="error",
            payload_bytes=0,
            timings_ms={},
        )
        _log.warning("named entity projection reconcile failed webspace=%s: %s", webspace_id, exc, exc_info=True)
    finally:
        with _RECONCILE_LOCK:
            state = _RECONCILE_STATES.get(webspace_id)
            if state is not None:
                state["in_flight"] = False
                if state.get("task") is current_task:
                    state["task"] = None


async def request_named_entity_projection(
    *,
    webspace_id: str | None = None,
    reason: str = "registry_changed",
    refresh: bool = True,
    wait: bool = False,
) -> dict[str, Any]:
    webspace = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    loop = asyncio.get_running_loop()
    with _RECONCILE_LOCK:
        state = _reconcile_state(webspace)
        state["requested_generation"] = int(state["requested_generation"]) + 1
        state["refresh_required"] = bool(state["refresh_required"] or refresh)
        state["last_reason"] = str(reason or "registry_changed")
        task = state.get("task")
        if not isinstance(task, asyncio.Task) or task.done():
            task = loop.create_task(_run_reconciler(webspace), name=f"named-entity-projection:{webspace}")
            state["task"] = task
        else:
            with _DIAGNOSTICS_LOCK:
                _DIAGNOSTICS["coalesced_total"] = int(_DIAGNOSTICS.get("coalesced_total") or 0) + 1
    if wait:
        await asyncio.shield(task)
    with _RECONCILE_LOCK:
        return _public_reconcile_state(_reconcile_state(webspace))


async def notify_named_entity_room_ready(webspace_id: str) -> dict[str, Any]:
    return await request_named_entity_projection(
        webspace_id=webspace_id,
        reason="room_ready",
        refresh=False,
        wait=False,
    )


async def project_named_entity_registry(*, webspace_id: str | None = None) -> dict[str, Any]:
    webspace = webspace_id or default_webspace_id()
    await request_named_entity_projection(
        webspace_id=webspace,
        reason="explicit_projection",
        refresh=True,
        wait=True,
    )
    return dict(named_entities.named_entity_registry_snapshot(webspace_id=webspace).payload)


@subscribe("sys.ready")
async def on_sys_ready(evt: Any) -> None:
    await on_entity_registry_changed(evt)


@subscribe(named_entities.ENTITY_REGISTRY_CHANGED)
@subscribe("subnet.alias.changed")
async def on_entity_registry_changed(evt: Any) -> None:
    try:
        payload = _payload(evt)
        webspace_ids = [_resolve_webspace_id(payload)]
        if _topic(evt) == "subnet.alias.changed":
            default_webspace = default_webspace_id()
            if default_webspace not in webspace_ids:
                webspace_ids.append(default_webspace)
        for webspace_id in webspace_ids:
            await request_named_entity_projection(
                webspace_id=webspace_id,
                reason=_topic(evt) or "entity.registry.changed",
                refresh=True,
                wait=False,
            )
    except Exception:
        _log.debug("failed to project named entity registry", exc_info=True)


__all__ = [
    "clear_named_entity_projection_reconciler",
    "named_entity_projection_diagnostics_snapshot",
    "named_entity_projection_reconciler_snapshot",
    "notify_named_entity_room_ready",
    "on_entity_registry_changed",
    "on_sys_ready",
    "project_named_entity_registry",
    "request_named_entity_projection",
    "reset_named_entity_projection_diagnostics",
]
