"""Read-only operational projections for application system surfaces.

The module deliberately composes public control-plane contracts instead of
exposing runtime service implementations. Consumers request only the sections
they render, which keeps summary surfaces independent from expensive topology
and reliability snapshots.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import shutil
from pathlib import Path
import threading
import time
from typing import Any

from adaos.sdk import access, control_plane
from adaos.sdk.core.decorators import tool
from adaos.sdk.status import current_update_status

__all__ = [
    "get_operational_snapshot",
    "request_core_update",
    "rename_current_node",
    "rename_local_subnet",
    "set_core_autoupdate",
]

_ALL_SECTIONS = frozenset(
    {
        "summary",
        "services",
        "connections",
        "quotas",
        "incidents",
        "update",
        "applications",
        "resources",
        "members",
    }
)
_RELIABILITY_CACHE_TTL_S = 2.0
_RELIABILITY_CACHE: dict[str, tuple[float, int, dict[str, Any]]] = {}
_RELIABILITY_CACHE_LOCK = threading.Lock()
_RELIABILITY_BUILD_LOCKS: dict[str, threading.Lock] = {}

_RENAME_INPUT = {
    "type": "object",
    "properties": {"display_name": {"type": "string", "minLength": 1, "maxLength": 120}},
    "required": ["display_name"],
    "additionalProperties": False,
}
_RENAME_OUTPUT = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "target": {"type": "string"},
        "display_name": {"type": "string"},
        "current": {"type": "string"},
        "desired": {"type": "string"},
        "applied": {"type": "boolean"},
    },
    "required": ["ok", "target", "display_name", "current", "desired", "applied"],
    "additionalProperties": True,
}
_CORE_AUTOUPDATE_INPUT = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "enabled": {"type": "boolean"},
    },
    "required": ["request_id", "enabled"],
    "additionalProperties": False,
}
_CORE_UPDATE_INPUT = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "countdown_sec": {"type": "number", "minimum": 5, "maximum": 3600},
        "dry_run": {"type": "boolean", "default": False},
    },
    "required": ["request_id"],
    "additionalProperties": False,
}
_CORE_CONTROL_OUTPUT = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "request_id": {"type": "string"},
        "target": {"type": "string"},
        "accepted": {"type": "boolean"},
        "current": {},
        "desired": {},
        "applied": {"type": "boolean"},
        "status": {"type": "object"},
    },
    "required": ["ok", "request_id", "target", "accepted"],
    "additionalProperties": True,
}


def _request_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("request_id is required")
    if len(token) > 160:
        raise ValueError("request_id must contain at most 160 characters")
    return token


def _post_local_admin(path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Call the active local control owner without exposing its credential."""

    import requests

    from adaos.apps.cli.active_control import resolve_control_base_url, resolve_control_token

    base_url = resolve_control_base_url(prefer_local=True)
    token = resolve_control_token(base_url=base_url)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            f"{base_url.rstrip('/')}{path}",
            headers={"X-AdaOS-Token": token, "Accept": "application/json"},
            json=dict(payload),
            timeout=30.0,
        )
        response.raise_for_status()
        result = response.json()
    finally:
        session.close()
    if not isinstance(result, Mapping):
        raise RuntimeError("system_control_response_invalid")
    return dict(result)


@tool(
    "system.core_autoupdate.set",
    summary="Set the governed Core automatic-update preference for this node.",
    stability="stable",
    idempotent=True,
    input_schema=_CORE_AUTOUPDATE_INPUT,
    output_schema=_CORE_CONTROL_OUTPUT,
)
def set_core_autoupdate(request_id: str, enabled: bool) -> dict[str, Any]:
    """Persist the desired Core autoupdate state with explicit current/desired output."""

    access.require("workspace.write")
    identifier = _request_id(request_id)
    from adaos.services.operator_controls import read_controls, update_controls

    before = bool(read_controls().get("core_auto_update", True))
    after = bool(update_controls({"core_auto_update": bool(enabled)}).get("core_auto_update"))
    return {
        "ok": True,
        "request_id": identifier,
        "target": "core_autoupdate",
        "accepted": True,
        "current": after,
        "previous": before,
        "desired": bool(enabled),
        "applied": after == bool(enabled),
    }


@tool(
    "system.core_update.request",
    summary="Reconcile this node with the governed Core release desired by Root.",
    stability="stable",
    idempotent=True,
    input_schema=_CORE_UPDATE_INPUT,
    output_schema=_CORE_CONTROL_OUTPUT,
)
def request_core_update(
    request_id: str,
    *,
    countdown_sec: float = 60.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Request the already-governed desired release; never accept a caller URL or revision."""

    access.require("workspace.write")
    identifier = _request_id(request_id)
    countdown = max(5.0, min(3600.0, float(countdown_sec)))
    if dry_run:
        status = _mapping(current_update_status())
        return {
            "ok": True,
            "request_id": identifier,
            "target": "core_update",
            "accepted": False,
            "dry_run": True,
            "current": status.get("state") or "idle",
            "desired": "root_governed_release",
            "applied": False,
            "status": status,
        }
    response = _post_local_admin(
        "/api/admin/update/reconcile",
        {
            "reason": f"sdk.system.core_update:{identifier}",
            "countdown_sec": countdown,
        },
    )
    status = _mapping(response.get("status"))
    return {
        "ok": bool(response.get("ok", True)),
        "request_id": identifier,
        "target": "core_update",
        "accepted": bool(response.get("accepted")),
        "reason": response.get("reason"),
        "current": status.get("state") or "unknown",
        "desired": "root_governed_release",
        "applied": bool(response.get("accepted")),
        "status": status,
        "result": _mapping(response.get("result")),
    }


def _display_name(value: Any) -> str:
    token = " ".join(str(value or "").strip().split())
    if not token:
        raise ValueError("display_name is required")
    if len(token) > 120:
        raise ValueError("display_name must contain at most 120 characters")
    return token


@tool(
    "system.local_subnet.rename",
    summary="Rename the local subnet through its durable public identity service.",
    stability="stable",
    idempotent=True,
    input_schema=_RENAME_INPUT,
    output_schema=_RENAME_OUTPUT,
)
def rename_local_subnet(display_name: str) -> dict[str, Any]:
    """Persist the local subnet display name without changing its identity."""

    from adaos.services.subnet_alias import save_subnet_alias

    name = _display_name(display_name)
    subject = _mapping(control_plane.get_self_object())
    subnet_id = str(
        subject.get("subnet_id")
        or _mapping(subject.get("identity")).get("subnet_id")
        or subject.get("id")
        or ""
    ).strip()
    saved = str(save_subnet_alias(name, subnet_id=subnet_id) or name)
    return {
        "ok": True,
        "target": "local_subnet",
        "subnet_id": subnet_id or None,
        "display_name": saved,
        "current": saved,
        "desired": name,
        "applied": saved == name,
    }


@tool(
    "system.current_node.rename",
    summary="Rename the current AdaOS node through its durable node configuration.",
    stability="stable",
    idempotent=True,
    input_schema=_RENAME_INPUT,
    output_schema=_RENAME_OUTPUT,
)
def rename_current_node(display_name: str) -> dict[str, Any]:
    """Persist the current node display name without changing node identity."""

    from adaos.services.node_config import set_node_names

    name = _display_name(display_name)
    config = set_node_names([name])
    names = list(getattr(getattr(config, "node_settings", None), "node_names", []) or [])
    current = str(names[0] if names else name)
    return {
        "ok": True,
        "target": "current_node",
        "node_id": str(getattr(config, "node_id", "") or "") or None,
        "display_name": current,
        "current": current,
        "desired": name,
        "applied": current == name,
    }


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


def _resource_snapshot() -> dict[str, Any]:
    """Return a cheap host-capacity sample with explicit provenance.

    Percent values are instantaneous UI hints, not billing or scheduling
    authority. Consumers may retain bounded samples to draw trends.
    """

    observed_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "schema": "adaos.sdk.system.resource_snapshot.v1",
        "observed_at": observed_at,
        "freshness": "live_sample",
        "source": "local_host",
        "available": True,
    }
    try:
        import psutil  # type: ignore

        memory = psutil.virtual_memory()
        result["cpu"] = {"percent": round(float(psutil.cpu_percent(interval=None)), 1)}
        result["memory"] = {
            "percent": round(float(memory.percent), 1),
            "used_bytes": int(memory.used),
            "total_bytes": int(memory.total),
        }
    except Exception as exc:
        result["available"] = False
        result["reason"] = f"{type(exc).__name__}: resource metrics unavailable"
    try:
        disk = shutil.disk_usage(Path.cwd())
        result["disk"] = {
            "percent": round((float(disk.used) / float(disk.total)) * 100.0, 1) if disk.total else 0.0,
            "used_bytes": int(disk.used),
            "total_bytes": int(disk.total),
        }
    except OSError:
        result.setdefault("reason", "disk metrics unavailable")
    return result


def _installed_application_summaries(*, webspace_id: str | None, limit: int) -> list[dict[str, Any]]:
    from adaos.sdk import applications

    try:
        models = applications.list_applications(
            installed_only=True,
            include_development=False,
            webspace_id=webspace_id,
            view="summary",
            limit=limit,
        )
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for model in models[:limit]:
        application = _mapping(model.get("application"))
        display = _mapping(application.get("display"))
        summary = _mapping(model.get("installation_summary"))
        items.append(
            {
                "id": str(application.get("application_id") or ""),
                "title": str(display.get("title") or application.get("application_id") or "Application"),
                "version": summary.get("effective_version") or model.get("effective_version"),
                "channel": summary.get("effective_channel") or model.get("effective_channel"),
                "status": summary.get("status") or ("active" if model.get("installed") else "unknown"),
                "update_state": model.get("update_state") or summary.get("update_state"),
                "has_update": bool(model.get("has_update") or summary.get("has_update")),
            }
        )
    return items


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
    subnet_refs = list(_mapping(subject.get("relations")).get("subnet") or [])
    subnet_ref = str(subnet_refs[0] if subnet_refs else "").strip()
    subnet_id = subnet_ref.split(":", 1)[1] if subnet_ref.startswith("subnet:") else subnet_ref
    try:
        from adaos.services.subnet_alias import display_subnet_alias, load_subnet_alias

        subnet_name = display_subnet_alias(
            load_subnet_alias(subnet_id=subnet_id or None),
            subnet_id or None,
        )
    except Exception:
        subnet_name = subnet_id or None
    result: dict[str, Any] = {
        "ok": True,
        "schema": "adaos.sdk.system.operational_snapshot.v1",
        "webspace_id": str(webspace_id or "").strip() or None,
        "sections": sorted(selected),
        "subject": subject,
        "capacity": capacity,
        "subnet": {
            "available": bool(subnet_id),
            "subnet_id": subnet_id or None,
            "display_name": subnet_name,
            "source": "local_node_identity",
            "freshness": "current" if subnet_id else "unavailable",
        },
        "service_summary": {
            "subscription": {"available": False, "reason": "provider_not_admitted"},
            "budget": {"available": False, "reason": "provider_not_admitted"},
            "ai_services": {"available": False, "reason": "provider_not_admitted"},
        },
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {"authority": "local_node", "projection": "read_only"},
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
        try:
            from adaos.services.operator_controls import read_controls

            controls = read_controls()
            result["update_controls"] = {
                "core_autoupdate": bool(controls.get("core_auto_update", True)),
                "source": "operator_controls",
                "mutable": True,
            }
        except Exception as exc:
            result["update_controls"] = {
                "core_autoupdate": None,
                "source": "unavailable",
                "mutable": False,
                "reason": type(exc).__name__,
            }
    if "applications" in selected:
        result["applications"] = _installed_application_summaries(
            webspace_id=webspace_id,
            limit=bounded_limit,
        )
        result["application_updates"] = {
            "available": sum(bool(item.get("has_update")) for item in result["applications"]),
            "total": len(result["applications"]),
        }
    if "resources" in selected:
        result["resources"] = _resource_snapshot()
    if "members" in selected:
        try:
            members = [dict(item) for item in control_plane.list_device_objects()[:bounded_limit]]
        except Exception:
            members = []
        result["members"] = members
        result["member_summary"] = {
            "online": sum(
                str(item.get("status") or item.get("connection") or "").lower()
                in {"online", "connected", "heartbeat", "ready"}
                for item in members
            ),
            "total": len(members),
        }

    result["counts"] = {
        key: len(value)
        for key, value in result.items()
        if key in {"services", "connections", "quotas", "incidents"}
        and isinstance(value, list)
    }
    return result
