"""Read-only operational projections for application system surfaces.

The module deliberately composes public control-plane contracts instead of
exposing runtime service implementations. Consumers request only the sections
they render, which keeps summary surfaces independent from expensive topology
and reliability snapshots.
"""

from __future__ import annotations

import asyncio
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
    "get_runtime_controls",
    "request_core_update",
    "rename_current_node",
    "rename_local_subnet",
    "set_core_autoupdate",
    "set_runtime_control",
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
        "skills",
        "development",
        "activity",
        "technical",
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
_RUNTIME_CONTROL_INPUT = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "control": {
            "type": "string",
            "enum": [
                "rasa_install",
                "rasa_enabled",
                "log_level",
                "core_auto_update",
                "application_auto_update_default",
            ],
        },
        "value": {},
    },
    "required": ["request_id", "control"],
    "additionalProperties": False,
}
_RUNTIME_CONTROL_OUTPUT = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "schema": {"type": "string"},
        "request_id": {"type": "string"},
        "target": {"type": "string"},
        "accepted": {"type": "boolean"},
        "current": {},
        "desired": {},
        "applied": {"type": "boolean"},
        "controls": {"type": "object"},
        "rasa": {"type": "object"},
    },
    "required": ["ok", "schema"],
    "additionalProperties": True,
}


def _runtime_controls_snapshot() -> dict[str, Any]:
    from adaos.services.nlu.rasa_skill_installer import is_rasa_nlu_enabled
    from adaos.services.operator_controls import read_controls
    from adaos.services.skill.service_supervisor import get_service_supervisor

    controls = read_controls()
    rasa: Mapping[str, Any] | None = None
    try:
        supervisor = get_service_supervisor()
        supervisor.ensure_discovered(force=True)
        candidate = supervisor.status("rasa_nlu_service_skill", check_health=True)
        rasa = candidate if isinstance(candidate, Mapping) else None
        rasa_availability = "ready"
    except Exception:
        rasa_availability = "unavailable"
    return {
        "ok": True,
        "schema": "adaos.sdk.system.runtime_controls.v1",
        "controls": {
            "core_auto_update": bool(controls.get("core_auto_update", True)),
            "application_auto_update_default": bool(
                controls.get("application_auto_update_default", True)
            ),
            "log_level": str(controls.get("log_level") or "INFO").upper(),
            "rasa_enabled": bool(controls.get("rasa_enabled", True)),
        },
        "rasa": {
            "availability": rasa_availability,
            "configured": bool(is_rasa_nlu_enabled()),
            "installed": rasa is not None,
            "running": bool(
                rasa and (rasa.get("running") or rasa.get("external_ready"))
            ),
            "health": rasa.get("health_ok") if rasa else None,
            "environment": rasa.get("env_mode") if rasa else None,
            "version_profile": "lightweight",
            "diet_profile": "deferred",
        },
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "operator_controls",
        "freshness": "current",
    }


@tool(
    "system.runtime_controls.get",
    summary="Read governed node runtime controls and bounded Rasa state.",
    stability="stable",
    idempotent=True,
    output_schema=_RUNTIME_CONTROL_OUTPUT,
)
def get_runtime_controls() -> dict[str, Any]:
    """Return operator controls without exposing service-supervisor internals."""

    access.require("workspace.read")
    return _runtime_controls_snapshot()


@tool(
    "system.runtime_control.set",
    summary="Apply one governed node runtime control.",
    stability="stable",
    idempotent=True,
    input_schema=_RUNTIME_CONTROL_INPUT,
    output_schema=_RUNTIME_CONTROL_OUTPUT,
)
async def set_runtime_control(
    request_id: str,
    control: str,
    value: Any = None,
) -> dict[str, Any]:
    """Apply a bounded operator mutation through the public System SDK."""

    access.require("workspace.write")
    identifier = _request_id(request_id)
    selected = str(control or "").strip().casefold()
    allowed = {
        "rasa_install",
        "rasa_enabled",
        "log_level",
        "core_auto_update",
        "application_auto_update_default",
    }
    if selected not in allowed:
        raise ValueError("unsupported runtime control")

    from adaos.services.nlu.rasa_skill_installer import (
        ensure_rasa_service_skill_installed,
    )
    from adaos.services.operator_controls import read_controls, update_controls
    from adaos.services.skill.service_supervisor import get_service_supervisor

    before = read_controls()
    desired: Any = value
    if selected == "rasa_install":
        await asyncio.to_thread(ensure_rasa_service_skill_installed)
        await get_service_supervisor().refresh_discovered(force=True)
        desired = True
    elif selected in {
        "rasa_enabled",
        "core_auto_update",
        "application_auto_update_default",
    }:
        desired = bool(value)
        update_controls({selected: desired})
        if selected == "rasa_enabled":
            supervisor = get_service_supervisor()
            if desired:
                await asyncio.to_thread(ensure_rasa_service_skill_installed)
                await supervisor.refresh_discovered(force=True)
                await supervisor.start("rasa_nlu_service_skill")
            else:
                await supervisor.stop("rasa_nlu_service_skill")
    elif selected == "log_level":
        desired = str(value or "").strip().upper()
        update_controls({"log_level": desired})

    snapshot = _runtime_controls_snapshot()
    current = (
        snapshot["rasa"].get("installed")
        if selected == "rasa_install"
        else snapshot["controls"].get(selected)
    )
    previous = (
        None if selected == "rasa_install" else before.get(selected)
    )
    return {
        **snapshot,
        "request_id": identifier,
        "target": selected,
        "accepted": True,
        "previous": previous,
        "current": current,
        "desired": desired,
        "applied": current == desired,
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


def _installed_skill_projection(*, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return bounded installed-skill facts from the public control plane."""

    try:
        raw_items = list(control_plane.list_skill_objects())
    except Exception as exc:
        return [], {
            "available": False,
            "total": None,
            "source": "control_plane",
            "freshness": "unavailable",
            "reason": type(exc).__name__,
        }
    items: list[dict[str, Any]] = []
    for value in raw_items[:limit]:
        item = _mapping(value)
        identity = str(item.get("id") or item.get("name") or "").strip()
        if not identity:
            continue
        items.append(
            {
                "id": identity,
                "name": str(item.get("name") or item.get("title") or identity),
                "status": str(item.get("status") or "unknown"),
                "version": _mapping(item.get("versioning")).get("current")
                or item.get("version"),
            }
        )
    return items, {
        "available": True,
        "total": len(raw_items),
        "returned": len(items),
        "truncated": len(raw_items) > len(items),
        "source": "control_plane",
        "freshness": "current",
    }


def _development_delivery_projection() -> dict[str, Any]:
    """Aggregate local Development Report delivery without exposing report content."""

    try:
        from adaos.sdk import applications

        reports = [
            dict(item)
            for item in applications.list_development_reports()
            if isinstance(item, Mapping)
        ]
    except Exception as exc:
        return {
            "available": False,
            "source": "development_reports",
            "freshness": "unavailable",
            "reason": type(exc).__name__,
        }
    delivered_states = {
        "delivered",
        "received",
        "triaged",
        "accepted",
        "declined",
        "duplicate",
        "planned",
        "prerelease_available",
        "released",
        "awaiting_local_verification",
        "verified",
        "still_reproduces",
    }
    accepted_states = {
        "accepted",
        "planned",
        "prerelease_available",
        "released",
        "awaiting_local_verification",
        "verified",
        "still_reproduces",
    }
    statuses = [str(item.get("status") or "unknown").strip().lower() for item in reports]
    delivered = [item for item, status in zip(reports, statuses) if status in delivered_states]
    timestamps = sorted(
        str(item.get("updated_at") or item.get("created_at") or "").strip()
        for item in delivered
        if str(item.get("updated_at") or item.get("created_at") or "").strip()
    )
    return {
        "available": True,
        "total": len(reports),
        "delivered": sum(status in delivered_states for status in statuses),
        "accepted": sum(status in accepted_states for status in statuses),
        "pending": sum(status in {"draft", "queued"} for status in statuses),
        "last_delivery_at": timestamps[-1] if timestamps else None,
        "source": "development_reports",
        "freshness": "current",
    }


def _root_activity_events(*, limit: int) -> dict[str, Any]:
    """Return bounded, user-impacting Root activity for progressive disclosure."""

    try:
        from adaos.sdk.data import root_mcp

        response = _mapping(root_mcp.get_local_activity_log(limit=min(200, max(20, limit * 6))))
        envelope = _mapping(response.get("response"))
        result = _mapping(envelope.get("result") or response.get("result"))
        raw_events = result.get("events") if result else response.get("events")
        events = _objects(raw_events, limit=min(200, max(20, limit * 6)))
    except Exception as exc:
        return {
            "available": False,
            "items": [],
            "count": 0,
            "source": "root_audit",
            "freshness": "unavailable",
            "reason": type(exc).__name__,
        }
    keywords = {
        "update",
        "lifecycle",
        "member",
        "device",
        "pair",
        "unlink",
        "application",
        "incident",
        "health",
        "threshold",
        "diagnostic",
        "subnet",
        "policy",
    }
    items: list[dict[str, Any]] = []
    for event in events:
        tool_id = str(event.get("tool_id") or event.get("kind") or "").strip()
        if not any(token in tool_id.lower() for token in keywords):
            continue
        items.append(
            {
                "id": str(event.get("event_id") or event.get("trace_id") or "") or None,
                "kind": str(event.get("kind") or tool_id or "system"),
                "status": str(event.get("status") or "unknown"),
                "summary": str(event.get("summary") or tool_id or "System activity")[:300],
                "recorded_at": event.get("finished_at") or event.get("started_at"),
            }
        )
        if len(items) >= limit:
            break
    return {
        "available": True,
        "items": items,
        "count": len(items),
        "source": "root_audit",
        "freshness": "current",
    }


def _technical_projection(
    *,
    subject: Mapping[str, Any],
    capacity: Mapping[str, Any],
    reliability: Mapping[str, Any],
    update: Mapping[str, Any],
    subnet_id: str | None,
    webspace_id: str | None,
) -> dict[str, Any]:
    """Build a small troubleshooting projection, never an unbounded log dump."""

    context = _mapping(reliability.get("context"))
    connections = _projection_objects(reliability, kind="connection", limit=50)
    connection_states = [
        str(item.get("status") or item.get("health") or "unknown").strip().lower()
        for item in connections
    ]
    raw_capacity = _mapping(capacity.get("resources"))
    bounded_capacity = {
        str(key)[:80]: value
        for key, value in list(sorted(raw_capacity.items(), key=lambda item: str(item[0])))[:20]
        if value is None or isinstance(value, (bool, int, float, str))
    }
    allowed_update_fields = (
        "state",
        "phase",
        "action",
        "message",
        "reason",
        "planned",
        "runtime",
        "transition_id",
        "started_at",
        "updated_at",
    )
    return {
        "available": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "freshness": "current",
        "source": "bounded_operational_projection",
        "identifiers": {
            "node_id": subject.get("id"),
            "subnet_id": subnet_id,
            "webspace_id": str(webspace_id or "").strip() or None,
        },
        "runtime": {
            "status": subject.get("status"),
            "version": _mapping(subject.get("versioning")).get("current"),
            "capacity": bounded_capacity,
        },
        "connectivity": {
            "status": context.get("state") or reliability.get("status") or "unknown",
            "observed": len(connections),
            "ready": sum(value in {"online", "ready", "connected"} for value in connection_states),
        },
        "update": {
            key: update.get(key)
            for key in allowed_update_fields
            if update.get(key) is not None
        },
    }


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
    if selected.intersection({"services", "connections", "quotas", "incidents", "technical"}):
        try:
            reliability = _reliability_projection(webspace_id=webspace_id)
        except Exception as exc:
            reliability = {
                "status": "unavailable",
                "context": {"state": "unavailable", "reason": type(exc).__name__},
                "objects": [],
                "incidents": [],
            }
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
            member_devices: list[dict[str, Any]] = []
            seen_member_refs: set[str] = set()
            for raw_item in control_plane.list_device_objects():
                item = dict(raw_item)
                relations = _mapping(item.get("relations"))
                member_ref = next(
                    (
                        str(value).strip()
                        for value in relations.get("connected_to") or ()
                        if str(value).strip().startswith("member:")
                    ),
                    "",
                )
                if not member_ref or member_ref in seen_member_refs:
                    continue
                seen_member_refs.add(member_ref)
                member_devices.append(
                    {
                        **item,
                        "id": member_ref,
                        "kind": "member",
                        "role": "member",
                        "is_hub": False,
                    }
                )
            hub = {
                **subject,
                "kind": "member",
                "role": "hub",
                "is_hub": True,
            }
            # System is a node dashboard. Browser/ReDevice endpoints remain in
            # Devices and must never consume the member-tab budget here.
            members = [hub, *member_devices][:bounded_limit]
        except Exception:
            members = [
                {**subject, "kind": "member", "role": "hub", "is_hub": True}
            ]
        result["members"] = members
        result["member_summary"] = {
            "online": sum(
                str(item.get("status") or item.get("connection") or "").lower()
                in {"online", "connected", "heartbeat", "ready"}
                for item in members
            ),
            "total": len(members),
        }
    if "skills" in selected:
        skills, skill_summary = _installed_skill_projection(limit=bounded_limit)
        result["skills"] = skills
        result["skill_summary"] = skill_summary
    if "development" in selected:
        result["development_delivery"] = _development_delivery_projection()
    if "activity" in selected:
        result["activity"] = _root_activity_events(limit=min(bounded_limit, 50))
    if "technical" in selected:
        technical_update = _mapping(result.get("update"))
        if not technical_update:
            try:
                technical_update = _mapping(current_update_status())
            except Exception as exc:
                technical_update = {"state": "unavailable", "reason": type(exc).__name__}
        result["technical"] = _technical_projection(
            subject=subject,
            capacity=capacity,
            reliability=reliability,
            update=technical_update,
            subnet_id=subnet_id or None,
            webspace_id=webspace_id,
        )

    result["counts"] = {
        key: len(value)
        for key, value in result.items()
        if key in {"services", "connections", "quotas", "incidents"}
        and isinstance(value, list)
    }
    return result
