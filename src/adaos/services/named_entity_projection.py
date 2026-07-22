from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Iterable
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services import named_entities
from adaos.services.yjs.json_merge import is_y_map_value, set_map_value_if_changed
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.named_entities.projection")

_DIAGNOSTICS_LOCK = threading.RLock()
_DIAGNOSTICS: dict[str, Any] = {
    "schema": "adaos.named-entity-projection.diagnostics.v1",
    "attempt_total": 0,
    "written_total": 0,
    "unchanged_total": 0,
    "already_applied_total": 0,
    "live_room_total": 0,
    "detached_total": 0,
    "pending_total": 0,
    "reconcile_total": 0,
    "coalesced_total": 0,
    "error_total": 0,
    "fingerprint_skip_total": 0,
    "room_generation_retry_total": 0,
    "last_webspace_id": None,
    "last_outcome": None,
    "last_snapshot_mode": None,
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
    snapshot_mode: str,
) -> None:
    with _DIAGNOSTICS_LOCK:
        _DIAGNOSTICS["attempt_total"] = int(_DIAGNOSTICS.get("attempt_total") or 0) + 1
        counter = {
            "written": "written_total",
            "unchanged": "unchanged_total",
            "already_applied": "already_applied_total",
            "live_room": "live_room_total",
            "detached": "detached_total",
            "pending": "pending_total",
            "error": "error_total",
            "retry": "room_generation_retry_total",
        }.get(outcome)
        if counter:
            _DIAGNOSTICS[counter] = int(_DIAGNOSTICS.get(counter) or 0) + 1
        if outcome in {"live_room", "detached"}:
            _DIAGNOSTICS["written_total"] = int(_DIAGNOSTICS.get("written_total") or 0) + 1
        elif outcome == "already_applied":
            _DIAGNOSTICS["unchanged_total"] = int(_DIAGNOSTICS.get("unchanged_total") or 0) + 1
        if snapshot_mode == "fingerprint_hit":
            _DIAGNOSTICS["fingerprint_skip_total"] = int(
                _DIAGNOSTICS.get("fingerprint_skip_total") or 0
            ) + 1
        _DIAGNOSTICS["last_webspace_id"] = webspace_id
        _DIAGNOSTICS["last_outcome"] = outcome
        _DIAGNOSTICS["last_snapshot_mode"] = snapshot_mode
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
                "already_applied_total": 0,
                "live_room_total": 0,
                "detached_total": 0,
                "pending_total": 0,
                "reconcile_total": 0,
                "coalesced_total": 0,
                "error_total": 0,
                "fingerprint_skip_total": 0,
                "room_generation_retry_total": 0,
                "last_webspace_id": None,
                "last_outcome": None,
                "last_snapshot_mode": None,
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


def _registry_invalidation_sources(topic: str, payload: Mapping[str, Any]) -> tuple[str, ...]:
    if topic == "sys.ready":
        return tuple(named_entities.REGISTRY_SOURCES)
    if topic == "subnet.alias.changed":
        return ("subnet",)
    source = str(payload.get("source") or "").strip()
    entity_ref = str(payload.get("entity_ref") or "").strip()
    entity_kind = str(payload.get("entity_kind") or "").strip()
    if source in {"access_links", "device_inventory"} or entity_ref.startswith("device:"):
        return ("devices",)
    if source.startswith(("node_config", "subnet")) or entity_ref.startswith("assistant:"):
        return ("subnet",)
    if entity_kind in {"modal", "app", "scenario", "webspace", "skill"}:
        return ("static", "lookups")
    return tuple(named_entities.REGISTRY_SOURCES)


def _registry_fingerprint_hints(payload: Mapping[str, Any]) -> dict[str, str]:
    entity_ref = str(payload.get("entity_ref") or "").strip()
    current = payload.get("current") if isinstance(payload.get("current"), Mapping) else {}
    fingerprint = str(
        current.get("current_fingerprint")
        or current.get("fingerprint")
        or payload.get("fingerprint")
        or ""
    ).strip()
    return {entity_ref: fingerprint} if entity_ref and fingerprint else {}


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


NAMED_ENTITIES_V2_KEY = "namedEntitiesV2"
NAMED_ENTITIES_V2_SCHEMA = "adaos.named-entities.projection.v2"


def _legacy_projection_enabled() -> bool:
    return str(os.getenv("ADAOS_NAMED_ENTITY_LEGACY_PROJECTION") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _v2_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    conflicts = payload.get("conflicts") if isinstance(payload.get("conflicts"), list) else []
    entities = {
        str(item.get("canonical_ref") or "").strip(): dict(item)
        for item in items
        if isinstance(item, Mapping) and str(item.get("canonical_ref") or "").strip()
    }
    conflicts_by_key: dict[str, Any] = {}
    for index, item in enumerate(conflicts):
        if not isinstance(item, Mapping):
            continue
        locale = str(item.get("locale") or "und")
        identity = str(item.get("normalized") or item.get("label") or index)
        conflicts_by_key[f"{locale}:{identity}"] = dict(item)
    return {
        "meta": {
            "schema": NAMED_ENTITIES_V2_SCHEMA,
            "version": 2,
            "webspace_id": str(payload.get("webspace_id") or ""),
            "revision": int(summary.get("registry_revision") or 0),
            "fingerprint": str(summary.get("fingerprint") or ""),
            "count": len(entities),
            "conflict_count": len(conflicts_by_key),
            "updated_at": summary.get("updated_at"),
        },
        "entities": entities,
        "conflicts": conflicts_by_key,
    }


def _remove_map_key(y_map: Any, txn: Any, key: str) -> bool:
    try:
        if y_map.get(key) is None:
            return False
        y_map.pop(txn, key)
        return True
    except Exception:
        return False


def _write_incremental_v2_payload(
    registry_map: Any,
    txn: Any,
    payload: Mapping[str, Any],
    changed_refs: Iterable[str],
) -> bool:
    projected = _v2_payload(payload)
    current = registry_map.get(NAMED_ENTITIES_V2_KEY)
    if not is_y_map_value(current):
        changed, _mode = set_map_value_if_changed(
            registry_map,
            txn,
            NAMED_ENTITIES_V2_KEY,
            projected,
        )
        return changed
    changed = False
    meta_changed, _mode = set_map_value_if_changed(current, txn, "meta", projected["meta"])
    changed = meta_changed or changed
    entities = current.get("entities")
    if not is_y_map_value(entities):
        entities_changed, _mode = set_map_value_if_changed(
            current,
            txn,
            "entities",
            projected["entities"],
        )
        changed = entities_changed or changed
    else:
        next_entities = projected["entities"]
        for canonical_ref in sorted({str(item).strip() for item in changed_refs if str(item).strip()}):
            next_entity = next_entities.get(canonical_ref)
            if next_entity is None:
                changed = _remove_map_key(entities, txn, canonical_ref) or changed
                continue
            entity_changed, _mode = set_map_value_if_changed(
                entities,
                txn,
                canonical_ref,
                next_entity,
            )
            changed = entity_changed or changed
    conflicts_changed, _mode = set_map_value_if_changed(
        current,
        txn,
        "conflicts",
        projected["conflicts"],
    )
    return conflicts_changed or changed


def _write_payload_to_doc(
    ydoc: Any,
    txn: Any,
    payload: Mapping[str, Any],
    *,
    changed_refs: Iterable[str] | None = None,
) -> bool:
    registry_map = ydoc.get_map("registry")
    if changed_refs is None:
        changed, _mode = set_map_value_if_changed(
            registry_map,
            txn,
            NAMED_ENTITIES_V2_KEY,
            _v2_payload(payload),
        )
    else:
        changed = _write_incremental_v2_payload(
            registry_map,
            txn,
            payload,
            changed_refs,
        )
    if _legacy_projection_enabled():
        legacy_changed, _legacy_mode = set_map_value_if_changed(
            registry_map,
            txn,
            "named_entities",
            dict(payload),
        )
        changed = changed or legacy_changed
    else:
        changed = _remove_map_key(registry_map, txn, "named_entities") or changed
    return changed


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
        "applied_room_generation": None,
        "pending": False,
        "in_flight": False,
        "refresh_required": False,
        "refresh_all_required": False,
        "dirty_sources": set(),
        "fingerprint_hints": {},
        "fingerprint_hints_complete": True,
        "allow_detached_build": False,
        "last_reason": None,
        "last_outcome": None,
        "last_error": None,
        "last_command": None,
        "last_snapshot_mode": None,
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
    result = {
        key: value
        for key, value in state.items()
        if key != "task"
    }
    result["dirty_sources"] = sorted(str(item) for item in state.get("dirty_sources") or ())
    result["fingerprint_hints"] = dict(state.get("fingerprint_hints") or {})
    return result


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


async def _apply_snapshot_to_live_room(
    snapshot: named_entities.NamedEntityRegistrySnapshot,
    *,
    changed_refs: Iterable[str] | None = None,
    expected_room_generation: int | str | None = None,
) -> dict[str, Any]:
    from adaos.services.yjs.doc import submit_live_room_mutation

    payload = dict(snapshot.payload)
    selected_refs = tuple(changed_refs) if changed_refs is not None else None

    def _apply(ydoc: Any, txn: Any) -> bool:
        return _write_payload_to_doc(
            ydoc,
            txn,
            payload,
            changed_refs=selected_refs,
        )

    command = await submit_live_room_mutation(
        snapshot.webspace_id,
        _apply,
        root_names=["registry"],
        source="named_entity_projection",
        owner="core:named_entities",
        channel="core.named_entities.live_room",
        expected_room_generation=expected_room_generation,
    )
    command["projection_patch_mode"] = "incremental" if selected_refs is not None else "full"
    command["changed_ref_total"] = len(selected_refs or ())
    return {
        "accepted": bool(command.get("applied")),
        "written": bool(command.get("mutator_result")) if command.get("applied") else False,
        "payload": payload,
        "command": command,
    }


def _current_live_room_generation(webspace_id: str) -> int | str | None:
    from adaos.services.yjs.doc import live_room_generation

    return live_room_generation(webspace_id)


def _already_applied_result(
    snapshot: named_entities.NamedEntityRegistrySnapshot,
    room_generation: int | str,
) -> dict[str, Any]:
    return {
        "accepted": True,
        "written": False,
        "payload": dict(snapshot.payload),
        "command": {
            "accepted": True,
            "applied": False,
            "changed": False,
            "reason": "already_applied",
            "handoff": "skipped",
            "room_generation": room_generation,
            "expected_room_generation": room_generation,
            "queue_ms": 0.0,
            "apply_ms": 0.0,
            "total_ms": 0.0,
            "update_bytes": 0,
            "encode_mode": "none",
            "projection_patch_mode": "skipped",
            "changed_ref_total": 0,
            "error": None,
        },
    }


async def _run_reconciler(webspace_id: str) -> None:
    current_task = asyncio.current_task()
    try:
        while True:
            total_started = time.perf_counter()
            room_generation = _current_live_room_generation(webspace_id)
            with _RECONCILE_LOCK:
                state = _reconcile_state(webspace_id)
                target_generation = int(state["requested_generation"])
                allow_detached_build = bool(state.get("allow_detached_build"))
                if room_generation is None and not allow_detached_build:
                    state["pending"] = True
                    state["last_outcome"] = "pending_room"
                    state["last_error"] = None
                    state["last_snapshot_mode"] = "deferred_room_not_ready"
                    state["last_command"] = {
                        "accepted": False,
                        "applied": False,
                        "changed": False,
                        "reason": "room_not_ready",
                        "handoff": "skipped",
                        "room_generation": None,
                        "expected_room_generation": None,
                        "queue_ms": 0.0,
                        "apply_ms": 0.0,
                        "total_ms": 0.0,
                        "update_bytes": 0,
                        "encode_mode": "none",
                        "projection_patch_mode": "deferred",
                        "changed_ref_total": 0,
                        "error": None,
                    }
                    state["last_updated_at"] = time.time()
                    state["in_flight"] = False
                    with _DIAGNOSTICS_LOCK:
                        _DIAGNOSTICS["reconcile_total"] = int(_DIAGNOSTICS.get("reconcile_total") or 0) + 1
                    _record_projection_attempt(
                        webspace_id=webspace_id,
                        outcome="pending",
                        payload_bytes=0,
                        timings_ms={"deferred_room_not_ready": _elapsed_ms(total_started)},
                        snapshot_mode="deferred_room_not_ready",
                    )
                    return
                refresh = bool(state["refresh_required"])
                refresh_all = bool(state["refresh_all_required"])
                dirty_sources = tuple(sorted(state["dirty_sources"]))
                fingerprint_hints = dict(state["fingerprint_hints"])
                fingerprint_hints_complete = bool(state["fingerprint_hints_complete"])
                state["refresh_required"] = False
                state["refresh_all_required"] = False
                state["dirty_sources"].clear()
                state["fingerprint_hints"].clear()
                state["fingerprint_hints_complete"] = True
                state["allow_detached_build"] = False
                state["in_flight"] = True
                state["last_error"] = None
            build_started = time.perf_counter()
            if refresh:
                registry = named_entities.get_named_entity_registry()
                if (
                    not refresh_all
                    and fingerprint_hints_complete
                    and fingerprint_hints
                    and registry.fingerprints_match(
                        fingerprint_hints,
                        webspace_id=webspace_id,
                    )
                ):
                    snapshot = registry.get(webspace_id=webspace_id)
                    snapshot_mode = "fingerprint_hit"
                else:
                    snapshot = await asyncio.to_thread(
                        named_entities.refresh_named_entity_registry_snapshot,
                        webspace_id=webspace_id,
                        dirty_sources=None if refresh_all else dirty_sources,
                    )
                    snapshot_mode = "full_refresh" if refresh_all else "source_refresh"
            else:
                snapshot = await asyncio.to_thread(
                    named_entities.named_entity_registry_snapshot,
                    webspace_id=webspace_id,
                )
                snapshot_mode = "cache"
            timings_ms = {"snapshot_build": _elapsed_ms(build_started)}
            try:
                for source, value in dict(getattr(snapshot, "source_timings_ms", {}) or {}).items():
                    timings_ms[f"source.{source}"] = float(value)
                for phase, value in dict(getattr(snapshot, "phase_timings_ms", {}) or {}).items():
                    timings_ms[f"registry.{phase}"] = float(value)
            except Exception:
                pass
            payload_bytes = _payload_size_bytes(snapshot.payload)
            apply_started = time.perf_counter()
            with _RECONCILE_LOCK:
                state = _reconcile_state(webspace_id)
                applied_revision = int(state.get("applied_revision") or 0)
                applied_room_generation = state.get("applied_room_generation")
                already_applied = (
                    room_generation is not None
                    and state.get("applied_fingerprint") == snapshot.fingerprint
                    and applied_room_generation == room_generation
                )
                incremental_refs = (
                    snapshot.changed_refs
                    if room_generation is not None
                    and applied_room_generation == room_generation
                    and applied_revision + 1 == snapshot.revision
                    else None
                )
            if already_applied:
                result = _already_applied_result(snapshot, room_generation)
            else:
                result = await _apply_snapshot_to_live_room(
                    snapshot,
                    changed_refs=incremental_refs,
                    expected_room_generation=room_generation,
                )
            timings_ms["live_room_apply"] = _elapsed_ms(apply_started)
            command = result.get("command") if isinstance(result.get("command"), Mapping) else {}
            command_reason = str(command.get("reason") or "room_not_ready")
            timings_ms["command_queue"] = float(command.get("queue_ms") or 0.0)
            timings_ms["command_apply"] = float(command.get("apply_ms") or 0.0)
            timings_ms["total"] = _elapsed_ms(total_started)
            with _RECONCILE_LOCK:
                state = _reconcile_state(webspace_id)
                state["processed_generation"] = target_generation
                state["desired_revision"] = snapshot.revision
                state["desired_fingerprint"] = snapshot.fingerprint
                state["last_updated_at"] = time.time()
                state["last_snapshot_mode"] = snapshot_mode
                state["last_command"] = {
                    key: command.get(key)
                    for key in (
                        "accepted",
                        "applied",
                        "changed",
                        "reason",
                        "handoff",
                        "room_generation",
                        "expected_room_generation",
                        "queue_ms",
                        "apply_ms",
                        "total_ms",
                        "update_bytes",
                        "encode_mode",
                        "projection_patch_mode",
                        "changed_ref_total",
                        "error",
                    )
                    if key in command
                }
                if result["accepted"]:
                    state["applied_revision"] = snapshot.revision
                    state["applied_fingerprint"] = snapshot.fingerprint
                    state["applied_room_generation"] = command.get("room_generation")
                    state["pending"] = False
                    state["last_outcome"] = (
                        "written"
                        if result["written"]
                        else "already_applied"
                        if command.get("reason") == "already_applied"
                        else "unchanged"
                    )
                    state["last_error"] = None
                else:
                    state["pending"] = True
                    state["last_outcome"] = (
                        "pending_room"
                        if command_reason == "room_not_ready"
                        else "room_generation_changed"
                        if command_reason == "room_generation_changed"
                        else "command_rejected"
                    )
                    state["last_error"] = (
                        None
                        if command_reason == "room_not_ready"
                        else str(command.get("error") or command_reason)
                    )
                more_work = (
                    int(state["requested_generation"]) > target_generation
                    or bool(state["refresh_required"])
                    or command_reason == "room_generation_changed"
                )
            with _DIAGNOSTICS_LOCK:
                _DIAGNOSTICS["reconcile_total"] = int(_DIAGNOSTICS.get("reconcile_total") or 0) + 1
            _record_projection_attempt(
                webspace_id=webspace_id,
                outcome=(
                    "pending" if not result["accepted"] and command_reason == "room_not_ready"
                    else "retry"
                    if not result["accepted"] and command_reason == "room_generation_changed"
                    else "error"
                    if not result["accepted"]
                    else "already_applied"
                    if command_reason == "already_applied"
                    else "live_room"
                    if result["written"]
                    else "unchanged"
                ),
                payload_bytes=payload_bytes,
                timings_ms=timings_ms,
                snapshot_mode=snapshot_mode,
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
            snapshot_mode="error",
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
    dirty_sources: Iterable[str] | None = None,
    fingerprint_hints: Mapping[str, str] | None = None,
    allow_detached_build: bool = False,
) -> dict[str, Any]:
    webspace = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    loop = asyncio.get_running_loop()
    with _RECONCILE_LOCK:
        state = _reconcile_state(webspace)
        state["requested_generation"] = int(state["requested_generation"]) + 1
        state["refresh_required"] = bool(state["refresh_required"] or refresh)
        if refresh:
            if dirty_sources is None:
                state["refresh_all_required"] = True
                state["dirty_sources"].clear()
                state["fingerprint_hints"].clear()
                state["fingerprint_hints_complete"] = False
            elif not state["refresh_all_required"]:
                state["dirty_sources"].update(
                    source
                    for source in dirty_sources
                    if source in named_entities.REGISTRY_SOURCES
                )
                state["fingerprint_hints"].update(
                    {
                        str(canonical_ref): str(fingerprint)
                        for canonical_ref, fingerprint in dict(fingerprint_hints or {}).items()
                        if str(canonical_ref).strip() and str(fingerprint).strip()
                    }
                )
                if not fingerprint_hints:
                    state["fingerprint_hints_complete"] = False
        state["last_reason"] = str(reason or "registry_changed")
        if allow_detached_build or wait:
            state["allow_detached_build"] = True
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
        allow_detached_build=True,
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
                dirty_sources=_registry_invalidation_sources(_topic(evt), payload),
                fingerprint_hints=_registry_fingerprint_hints(payload),
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
