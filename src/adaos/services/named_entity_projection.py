from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services import named_entities
from adaos.services.yjs.store import ystore_write_metadata
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


def _write_payload_to_doc(ydoc: Any, txn: Any, payload: Mapping[str, Any]) -> None:
    registry_map = ydoc.get_map("registry")
    current = registry_map.get("named_entities")
    current_summary = current.get("summary") if isinstance(current, Mapping) else {}
    next_summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if (
        isinstance(current_summary, Mapping)
        and current_summary.get("fingerprint")
        and current_summary.get("fingerprint") == next_summary.get("fingerprint")
    ):
        return
    registry_map.set(txn, "named_entities", dict(payload))


async def project_named_entity_registry(*, webspace_id: str | None = None) -> dict[str, Any]:
    from adaos.services.yjs.doc import async_get_ydoc, mutate_live_room

    total_started = time.perf_counter()
    webspace = webspace_id or default_webspace_id()
    build_started = time.perf_counter()
    snapshot = named_entities.refresh_named_entity_registry_snapshot(webspace_id=webspace)
    payload = dict(snapshot.payload)
    timings_ms = {"snapshot_build": _elapsed_ms(build_started)}
    payload_bytes = _payload_size_bytes(payload)
    changed = {"value": False}

    def _apply(ydoc: Any, txn: Any) -> None:
        before = ydoc.get_map("registry").get("named_entities")
        _write_payload_to_doc(ydoc, txn, payload)
        after = ydoc.get_map("registry").get("named_entities")
        changed["value"] = before != after

    live_started = time.perf_counter()
    if mutate_live_room(
        webspace,
        _apply,
        root_names=["registry"],
        source="named_entity_projection",
        owner="core:named_entities",
        channel="core.named_entities.live_room",
    ):
        timings_ms["live_room_apply"] = _elapsed_ms(live_started)
        timings_ms["total"] = _elapsed_ms(total_started)
        _record_projection_attempt(
            webspace_id=webspace,
            outcome="live_room" if changed["value"] else "unchanged",
            payload_bytes=payload_bytes,
            timings_ms=timings_ms,
        )
        return payload

    timings_ms["live_room_apply"] = _elapsed_ms(live_started)
    detached_started = time.perf_counter()
    async with ystore_write_metadata(
        root_names=["registry"],
        source="named_entity_projection",
        owner="core:named_entities",
        channel="core.named_entities.async",
    ):
        async with async_get_ydoc(
            webspace,
            publish_live_room=False,
            load_mark_roots=["registry"],
            write_source="named_entity_projection",
            write_owner="core:named_entities",
            write_channel="core.named_entities.async",
        ) as ydoc:
            with ydoc.begin_transaction() as txn:
                _apply(ydoc, txn)
    timings_ms["detached_apply"] = _elapsed_ms(detached_started)
    timings_ms["total"] = _elapsed_ms(total_started)
    _record_projection_attempt(
        webspace_id=webspace,
        outcome="detached" if changed["value"] else "unchanged",
        payload_bytes=payload_bytes,
        timings_ms=timings_ms,
    )
    return payload


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
            await project_named_entity_registry(webspace_id=webspace_id)
    except Exception:
        _log.debug("failed to project named entity registry", exc_info=True)


__all__ = [
    "named_entity_projection_diagnostics_snapshot",
    "on_entity_registry_changed",
    "on_sys_ready",
    "project_named_entity_registry",
    "reset_named_entity_projection_diagnostics",
]
