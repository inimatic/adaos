"""Read-only operational projections for application system surfaces.

The module deliberately composes public control-plane contracts instead of
exposing runtime service implementations. Consumers request only the sections
they render, which keeps summary surfaces independent from expensive topology
and reliability snapshots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import threading
import time
from typing import Any

from adaos.sdk import control_plane
from adaos.sdk.status import current_update_status

__all__ = ["get_operational_snapshot"]

_ALL_SECTIONS = frozenset(
    {
        "summary",
        "services",
        "connections",
        "quotas",
        "incidents",
        "update",
    }
)
_RELIABILITY_CACHE_TTL_S = 2.0
_RELIABILITY_CACHE: dict[str, tuple[float, int, dict[str, Any]]] = {}
_RELIABILITY_CACHE_LOCK = threading.Lock()
_RELIABILITY_BUILD_LOCKS: dict[str, threading.Lock] = {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _objects(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)][:limit]


def _requested_sections(sections: Iterable[str] | str | None) -> set[str]:
    if sections is None:
        return {"summary"}
    raw = [sections] if isinstance(sections, str) else list(sections)
    selected = {str(item or "").strip().lower() for item in raw}
    selected.discard("")
    if "all" in selected:
        return set(_ALL_SECTIONS)
    unknown = selected - _ALL_SECTIONS
    if unknown:
        raise ValueError(f"unsupported system sections: {', '.join(sorted(unknown))}")
    return selected or {"summary"}


def _bounded_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(500, parsed))


def _reliability_projection(*, webspace_id: str | None) -> dict[str, Any]:
    cache_key = str(webspace_id or "").strip()
    source_identity = id(control_plane.get_reliability_projection)
    now = time.monotonic()
    cached = _RELIABILITY_CACHE.get(cache_key)
    if (
        cached is not None
        and cached[1] == source_identity
        and now - cached[0] <= _RELIABILITY_CACHE_TTL_S
    ):
        return dict(cached[2])
    with _RELIABILITY_CACHE_LOCK:
        build_lock = _RELIABILITY_BUILD_LOCKS.setdefault(cache_key, threading.Lock())
    with build_lock:
        now = time.monotonic()
        cached = _RELIABILITY_CACHE.get(cache_key)
        if (
            cached is not None
            and cached[1] == source_identity
            and now - cached[0] <= _RELIABILITY_CACHE_TTL_S
        ):
            return dict(cached[2])
        projection = _mapping(
            control_plane.get_reliability_projection(webspace_id=webspace_id)
        )
        _RELIABILITY_CACHE[cache_key] = (
            time.monotonic(),
            source_identity,
            projection,
        )
        return dict(projection)


def _projection_objects(
    projection: Mapping[str, Any],
    *,
    kind: str,
    limit: int,
) -> list[dict[str, Any]]:
    expected = str(kind or "").strip().lower()
    raw = projection.get("objects")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, Mapping)):
        return []
    items: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        if str(item.get("kind") or "").strip().lower() != expected:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _incident_items(projection: Mapping[str, Any], *, limit: int) -> list[dict[str, Any]]:
    incidents = _objects(projection.get("incidents"), limit=limit)
    seen = {
        str(item.get("id") or item.get("incident_id") or "").strip()
        for item in incidents
    }
    for owner in [projection.get("subject"), *list(projection.get("objects") or [])]:
        if not isinstance(owner, Mapping):
            continue
        owner_id = str(owner.get("id") or "").strip()
        for raw in list(owner.get("incidents") or []):
            if not isinstance(raw, Mapping):
                continue
            item = dict(raw)
            item.setdefault("owner_id", owner_id or None)
            identity = str(item.get("id") or item.get("incident_id") or "").strip()
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            incidents.append(item)
            if len(incidents) >= limit:
                return incidents
    return incidents[:limit]


def get_operational_snapshot(
    *,
    sections: Iterable[str] | str | None = None,
    webspace_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a bounded, section-driven system read model.

    ``summary`` reads only local identity and capacity. Reliability is loaded
    once and shared by ``incidents``; runtime, connection and quota sections
    use their dedicated control-plane projections.
    """

    selected = _requested_sections(sections)
    bounded_limit = _bounded_limit(limit)
    subject = _mapping(control_plane.get_self_object())
    capacity = _mapping(control_plane.get_local_capacity_object())
    result: dict[str, Any] = {
        "ok": True,
        "schema": "adaos.sdk.system.operational_snapshot.v1",
        "webspace_id": str(webspace_id or "").strip() or None,
        "sections": sorted(selected),
        "subject": subject,
        "capacity": capacity,
    }

    reliability: dict[str, Any] = {}
    if selected.intersection({"services", "connections", "quotas", "incidents"}):
        reliability = _reliability_projection(webspace_id=webspace_id)
    if "services" in selected:
        result["services"] = _projection_objects(
            reliability,
            kind="runtime",
            limit=bounded_limit,
        )
    if "connections" in selected:
        result["connections"] = _projection_objects(
            reliability,
            kind="connection",
            limit=bounded_limit,
        )
    if "quotas" in selected:
        result["quotas"] = _projection_objects(
            reliability,
            kind="quota",
            limit=bounded_limit,
        )
    if "incidents" in selected:
        result["incidents"] = _incident_items(reliability, limit=bounded_limit)
        result["reliability"] = {
            "id": reliability.get("id"),
            "kind": reliability.get("kind"),
            "title": reliability.get("title"),
            "summary": reliability.get("summary"),
            "context": _mapping(reliability.get("context")),
        }
    if "update" in selected:
        result["update"] = _mapping(current_update_status())

    result["counts"] = {
        key: len(value)
        for key, value in result.items()
        if key in {"services", "connections", "quotas", "incidents"}
        and isinstance(value, list)
    }
    return result
