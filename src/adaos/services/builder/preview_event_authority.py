"""Core attestation for Builder Preview stream-control events."""

from __future__ import annotations

import threading
import time
from typing import Any


_CONTROL_EVENT_TYPES = {
    "webio.stream.snapshot.requested",
    "webio.stream.subscription.changed",
    "webio.yjs.snapshot.requested",
    "webio.yjs.subscription.changed",
}
_RELATION_CACHE_TTL_S = 30.0
_RELATION_CACHE_LOCK = threading.RLock()
_RELATION_CACHE: dict[str, tuple[float, Any]] = {}


def _incoming_preview_relation(webspace_id: str) -> Any:
    now = time.monotonic()
    with _RELATION_CACHE_LOCK:
        cached = _RELATION_CACHE.get(webspace_id)
        if cached is not None and now - cached[0] <= _RELATION_CACHE_TTL_S:
            return cached[1]
    from adaos.services.workspaces.relations import WebspaceRelationshipRegistry

    relation = WebspaceRelationshipRegistry.from_context().get_incoming(webspace_id)
    with _RELATION_CACHE_LOCK:
        _RELATION_CACHE[webspace_id] = (now, relation)
    return relation


def remember_preview_event_relation(relation: Any) -> None:
    """Prime attestation after Core persists a Builder relation."""

    target = str(getattr(relation, "target_webspace_id", "") or "").strip()
    if not target:
        return
    with _RELATION_CACHE_LOCK:
        _RELATION_CACHE[target] = (time.monotonic(), relation)


def invalidate_preview_event_relation(webspace_id: str) -> None:
    with _RELATION_CACHE_LOCK:
        _RELATION_CACHE.pop(str(webspace_id or "").strip(), None)


def verified_builder_preview_control_payload(
    event_type: str,
    payload: dict[str, Any],
    *,
    active_scenario_id: str | None = None,
) -> dict[str, Any]:
    """Return an event payload attested from persistent Core topology.

    Callers may provide the current Yjs selector as an additional consistency
    witness.  If it conflicts with the Builder relation, no authority is
    attached.  Failure to read topology is compatibility-safe: the event still
    flows, but without a Preview data binding.
    """

    result = dict(payload or {})
    webspace_id = str(result.get("webspace_id") or "").strip()
    if not webspace_id or event_type not in _CONTROL_EVENT_TYPES:
        return result
    try:
        from adaos.services.workspaces.relations import BUILDER_PROJECT_PREVIEW

        relation = _incoming_preview_relation(webspace_id)
        if relation is None or relation.purpose != BUILDER_PROJECT_PREVIEW:
            return result
        relation_scenario = str(relation.metadata.get("scenario_id") or "").strip()
        active_scenario = str(active_scenario_id or "").strip()
        if active_scenario and relation_scenario and active_scenario != relation_scenario:
            return result
        scenario_id = active_scenario or relation_scenario
        if not scenario_id:
            return result
        meta = dict(result.get("_meta") or {})
        meta["verified_builder_preview"] = {
            "schema": "adaos.builder.preview_event_authority.v1",
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "relation_id": relation.relation_id,
            "relation_generation": int(relation.generation),
            "source_webspace_id": relation.source_webspace_id,
        }
        result["webspace_id"] = webspace_id
        result["scenario_id"] = scenario_id
        result["_meta"] = meta
    except Exception:
        return result
    return result


__all__ = [
    "invalidate_preview_event_relation",
    "remember_preview_event_relation",
    "verified_builder_preview_control_payload",
]
