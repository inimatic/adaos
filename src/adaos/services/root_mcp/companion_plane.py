from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.eventbus import emit
from adaos.services.id_gen import new_id

from .model import (
    ROOT_MCP_RESPONSE_SCHEMA,
    RootMcpSurface,
    RootMcpToolContract,
    schema_object,
)


CONTEXT_SCHEMA = "adaos.companion.context_frame.v1"
AFFORDANCE_SCHEMA = "adaos.companion.affordance.v1"
ACTION_REQUEST_SCHEMA = "adaos.companion.action_request.v1"
ACTION_RECEIPT_SCHEMA = "adaos.companion.action_receipt.v1"
CAPABILITY_REQUEST_SCHEMA = "adaos.companion.capability_request.v1"
_STATE_SCHEMA = "adaos.companion.control_plane_state.v1"
_STATE_LIMIT = 500
_STATE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_CONTEXT_LIMITS = {
    "available_modal_ids": 64,
    "catalog_apps": 48,
    "catalog_widgets": 48,
    "nodes": 16,
    "published_voice_capabilities": 32,
    "published_voice_affordances": 48,
}


_OPERATIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "ui.scenario.open",
        "title": "Open scenario",
        "effect": "ui_navigation",
        "required": ["scenario_id"],
        "cancellable": False,
    },
    {
        "id": "ui.home.open",
        "title": "Go home",
        "effect": "ui_navigation",
        "required": [],
        "cancellable": False,
    },
    {
        "id": "ui.modal.open",
        "title": "Open modal",
        "effect": "ui_navigation",
        "required": ["modal_id"],
        "cancellable": False,
    },
    {
        "id": "ui.modal.close",
        "title": "Close top modal",
        "effect": "ui_navigation",
        "required": [],
        "cancellable": False,
    },
    {
        "id": "ui.affordance.activate",
        "title": "Activate semantic UI affordance",
        "effect": "ui_action",
        "required": ["affordance_id"],
        "cancellable": False,
    },
    {
        "id": "ui.state.set",
        "title": "Set typed page state",
        "effect": "ui_state",
        "required": ["key", "value"],
        "cancellable": False,
    },
    {
        "id": "ui.widget.focus",
        "title": "Focus semantic widget",
        "effect": "ui_navigation",
        "required": ["widget_id"],
        "cancellable": False,
    },
    {
        "id": "status.codex_tokens.read",
        "title": "Read Codex token usage",
        "effect": "read_only",
        "required": [],
        "cancellable": False,
    },
    {
        "id": "status.node_cpu.read",
        "title": "Read node CPU load",
        "effect": "read_only",
        "required": [],
        "cancellable": False,
    },
    {
        "id": "media.catalog.search",
        "title": "Search Media Center catalog",
        "effect": "read_only",
        "required": ["query"],
        "cancellable": False,
    },
    {
        "id": "action.cancel",
        "title": "Cancel an action when still cancellable",
        "effect": "control",
        "required": ["target_action_id"],
        "cancellable": False,
    },
)
_OPERATION_BY_ID = {str(item["id"]): item for item in _OPERATIONS}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_texts(values: Any, *, limit: int) -> list[str]:
    seen: set[str] = set()
    for value in values or []:
        token = _text(value)
        if not token or token in seen:
            continue
        seen.add(token)
    return sorted(seen, key=lambda item: item.casefold())[:limit]


def _compact_status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result = {
        key: deepcopy(value.get(key))
        for key in ("status", "reason")
        if value.get(key) not in (None, "", [], {})
    }
    return result or None


def _compact_rows(
    values: Any,
    *,
    limit: int,
    fields: tuple[str, ...],
    include_activation: bool = False,
) -> list[dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        identity = _text(value.get("id") or value.get("scenario_id") or value.get("name"))
        if not identity or identity in rows_by_id:
            continue
        row = {
            key: deepcopy(value.get(key))
            for key in fields
            if value.get(key) not in (None, "", [], {})
        }
        availability = _compact_status(value.get("availability"))
        if availability:
            row["availability"] = availability
        if include_activation:
            row["activation"] = [
                deepcopy(dict(item))
                for item in value.get("activation") or []
                if isinstance(item, Mapping)
            ][:16]
        rows_by_id[identity] = row
    return [
        rows_by_id[identity]
        for identity in sorted(rows_by_id, key=lambda item: item.casefold())[:limit]
    ]


def _inventory_summary(source: Mapping[str, Any], projected: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, limit in _CONTEXT_LIMITS.items():
        source_rows = list(source.get(key) or [])
        if key == "available_modal_ids":
            unique_count = len({_text(item) for item in source_rows if _text(item)})
        else:
            unique_count = len(
                {
                    _text(item.get("id") or item.get("scenario_id") or item.get("name"))
                    for item in source_rows
                    if isinstance(item, Mapping)
                    and _text(item.get("id") or item.get("scenario_id") or item.get("name"))
                }
            )
        projected_count = len(projected.get(key) or [])
        result[key] = {
            "source_count": len(source_rows),
            "unique_count": unique_count,
            "projected_count": projected_count,
            "limit": limit,
            "truncated": unique_count > projected_count,
        }
    return result


def _state_path() -> Path:
    raw = get_ctx().paths.root_mcp_state_dir()
    root = Path(raw() if callable(raw) else raw)
    return root / "companion_plane" / "state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    return {
        "schema": _STATE_SCHEMA,
        "receipts": [
            dict(item)
            for item in payload.get("receipts") or []
            if isinstance(item, Mapping)
        ][-_STATE_LIMIT:],
    }


def _write_receipt(receipt: Mapping[str, Any]) -> None:
    path = _state_path()
    with mutation_lock(path.with_suffix(".lock"), timeout_s=30.0):
        state = _read_state()
        state["receipts"] = [
            *state["receipts"],
            deepcopy(dict(receipt)),
        ][-_STATE_LIMIT:]
        atomic_write_json(path, state)


def _recent_receipts(*, webspace_id: str | None, limit: int) -> list[dict[str, Any]]:
    token = _text(webspace_id)
    rows = list(reversed(_read_state()["receipts"]))
    if token:
        rows = [item for item in rows if _text(item.get("webspace_id")) == token]
    return rows[: max(1, min(int(limit), 100))]


def _action_surface(webspace_id: str, *, include_live: bool = True) -> dict[str, Any]:
    from adaos.services.nlu.teacher_read_model import get_contextual_action_surface

    value = get_contextual_action_surface(
        webspace_id=webspace_id,
        include_live=include_live,
        include_hints=False,
        max_actions=200,
    )
    return dict(value) if isinstance(value, Mapping) else {}


def _affordances(surface: Mapping[str, Any]) -> list[dict[str, Any]]:
    runtime = surface.get("runtime_state") if isinstance(surface.get("runtime_state"), Mapping) else {}
    modal_ids = [
        _text(item)
        for item in runtime.get("available_modal_ids") or []
        if _text(item)
    ]
    voice = [
        dict(item)
        for item in surface.get("voice_affordances") or []
        if isinstance(item, Mapping)
    ]
    rows: list[dict[str, Any]] = []
    for raw in _OPERATIONS:
        operation_id = str(raw["id"])
        available = True
        reason = None
        if operation_id == "ui.modal.open" and not modal_ids:
            available = False
            reason = "runtime_modal_inventory_unavailable"
        elif operation_id == "ui.affordance.activate" and not voice:
            available = False
            reason = "published_voice_affordances_unavailable"
        rows.append(
            {
                "schema": AFFORDANCE_SCHEMA,
                **deepcopy(raw),
                "available": available,
                "reason": reason,
                "context_guarded": raw["effect"] != "read_only",
            }
        )
    return rows


def build_context_frame(
    *, webspace_id: str = "desktop", include_live: bool = True
) -> dict[str, Any]:
    webspace = _text(webspace_id) or "desktop"
    surface = _action_surface(webspace, include_live=include_live)
    runtime = (
        dict(surface.get("runtime_state"))
        if isinstance(surface.get("runtime_state"), Mapping)
        else {}
    )
    source_inventory = {
        "available_modal_ids": list(runtime.get("available_modal_ids") or []),
        "catalog_apps": list(runtime.get("catalog_apps") or []),
        "catalog_widgets": list(runtime.get("catalog_widgets") or []),
        "nodes": list(runtime.get("nodes") or []),
        "published_voice_capabilities": list(surface.get("voice_capabilities") or []),
        "published_voice_affordances": list(surface.get("voice_affordances") or []),
    }
    projected_inventory = {
        "available_modal_ids": _unique_texts(
            source_inventory["available_modal_ids"],
            limit=_CONTEXT_LIMITS["available_modal_ids"],
        ),
        "catalog_apps": _compact_rows(
            source_inventory["catalog_apps"],
            limit=_CONTEXT_LIMITS["catalog_apps"],
            fields=("id", "scenario_id", "title", "launchModal"),
        ),
        "catalog_widgets": _compact_rows(
            source_inventory["catalog_widgets"],
            limit=_CONTEXT_LIMITS["catalog_widgets"],
            fields=("id", "title"),
        ),
        "nodes": _compact_rows(
            source_inventory["nodes"],
            limit=_CONTEXT_LIMITS["nodes"],
            fields=("id", "name", "status"),
        ),
        "published_voice_capabilities": _compact_rows(
            source_inventory["published_voice_capabilities"],
            limit=_CONTEXT_LIMITS["published_voice_capabilities"],
            fields=("id", "title", "kind", "side_effect_class"),
        ),
        "published_voice_affordances": _compact_rows(
            source_inventory["published_voice_affordances"],
            limit=_CONTEXT_LIMITS["published_voice_affordances"],
            fields=(
                "id",
                "title",
                "kind",
                "labels",
                "aliases",
                "side_effect_class",
                "parent",
                "visibility",
                "widget_id",
            ),
            include_activation=True,
        ),
    }
    stable = {
        "schema": CONTEXT_SCHEMA,
        "webspace_id": webspace,
        "current_scenario": runtime.get("current_scenario"),
        **projected_inventory,
    }
    context_digest = _digest(stable)
    return {
        **stable,
        "action_surface_fingerprint": surface.get("fingerprint"),
        "context_id": f"companion-context:{context_digest.split(':', 1)[1][:24]}",
        "context_digest": context_digest,
        "generated_at": _iso_now(),
        "inventory": _inventory_summary(source_inventory, projected_inventory),
        "affordances": _affordances(surface),
        "source_refs": [
            {
                "kind": "nlu_contextual_action_surface",
                "id": surface.get("surface_id"),
                "digest": surface.get("fingerprint"),
            },
            {
                "kind": "webspace_runtime",
                "id": webspace,
                "freshness": "live" if include_live else "descriptor_only",
            },
        ],
        "boundaries": {
            "raw_dom_access": False,
            "arbitrary_event_emit": False,
            "arbitrary_skill_invoke": False,
            "allowed_operation_ids": list(_OPERATION_BY_ID),
        },
    }


def _request(arguments: Mapping[str, Any]) -> dict[str, Any]:
    raw = arguments.get("request")
    body = dict(raw) if isinstance(raw, Mapping) else {
        key: value for key, value in arguments.items() if key != "_mcp_context"
    }
    params = body.get("params")
    body["params"] = dict(params) if isinstance(params, Mapping) else {}
    body["operation"] = _text(body.get("operation"))
    body["webspace_id"] = _text(body.get("webspace_id")) or "desktop"
    body["request_id"] = _text(body.get("request_id")) or f"companion-request:{new_id()}"
    return body


def _find_voice_affordance(
    frame: Mapping[str, Any], affordance_id: str
) -> dict[str, Any] | None:
    for item in frame.get("published_voice_affordances") or []:
        if isinstance(item, Mapping) and _text(item.get("id")) == affordance_id:
            return dict(item)
    return None


def _published_ids(frame: Mapping[str, Any], collection: str, *fields: str) -> set[str]:
    result: set[str] = set()
    for item in frame.get(collection) or []:
        if not isinstance(item, Mapping):
            continue
        for field in fields:
            token = _text(item.get(field))
            if token:
                result.add(token)
        item_id = _text(item.get("id"))
        if item_id.startswith("scenario:"):
            result.add(item_id.split(":", 1)[1])
    return result


def validate_action_request(
    request: Mapping[str, Any], *, frame: Mapping[str, Any]
) -> dict[str, Any]:
    operation = _text(request.get("operation"))
    params = request.get("params") if isinstance(request.get("params"), Mapping) else {}
    definition = _OPERATION_BY_ID.get(operation)
    errors: list[dict[str, Any]] = []
    if definition is None:
        errors.append({"code": "operation_not_allowed", "field": "operation"})
    else:
        for key in definition.get("required") or []:
            if key not in params or params.get(key) in (None, ""):
                errors.append({"code": "parameter_required", "field": f"params.{key}"})
        if definition["effect"] != "read_only":
            supplied_digest = _text(request.get("context_digest"))
            if not supplied_digest:
                errors.append({"code": "context_digest_required", "field": "context_digest"})
            elif supplied_digest != _text(frame.get("context_digest")):
                errors.append({"code": "context_stale", "field": "context_digest"})
    if operation == "ui.modal.open" and not errors:
        available = {_text(item) for item in frame.get("available_modal_ids") or []}
        modal_id = _text(params.get("modal_id"))
        if available and modal_id not in available:
            errors.append({"code": "modal_not_in_current_context", "field": "params.modal_id"})
    if operation == "ui.scenario.open" and not errors:
        available = _published_ids(frame, "catalog_apps", "scenario_id")
        scenario_id = _text(params.get("scenario_id"))
        if available and scenario_id not in available:
            errors.append({"code": "scenario_not_in_current_context", "field": "params.scenario_id"})
    if operation == "ui.widget.focus" and not errors:
        available = _published_ids(frame, "catalog_widgets", "id")
        widget_id = _text(params.get("widget_id"))
        if available and widget_id not in available:
            errors.append({"code": "widget_not_in_current_context", "field": "params.widget_id"})
    if operation == "ui.state.set" and not errors:
        key = _text(params.get("key"))
        if not _STATE_KEY_RE.fullmatch(key) or key.startswith("_"):
            errors.append({"code": "state_key_not_allowed", "field": "params.key"})
    if operation == "ui.affordance.activate" and not errors:
        affordance = _find_voice_affordance(frame, _text(params.get("affordance_id")))
        if affordance is None:
            errors.append({"code": "affordance_not_in_current_context", "field": "params.affordance_id"})
        else:
            availability = affordance.get("availability") if isinstance(affordance.get("availability"), Mapping) else {}
            if availability.get("status") == "not_currently_reachable":
                errors.append({"code": "affordance_not_reachable", "field": "params.affordance_id"})
    return {
        "ok": not errors,
        "schema": ACTION_REQUEST_SCHEMA,
        "request": deepcopy(dict(request)),
        "operation": deepcopy(definition) if definition else None,
        "errors": errors,
        "context_digest": frame.get("context_digest"),
    }


def _emit_host(topic: str, payload: Mapping[str, Any], *, trace_id: str | None) -> None:
    emit(
        get_ctx().bus,
        topic,
        dict(payload),
        "root_mcp.companion_plane",
        source_authority="root_mcp",
        trace_id=trace_id,
        schema=ACTION_REQUEST_SCHEMA,
        version=1,
        generate_event_id=True,
    )


def _read_codex_tokens() -> dict[str, Any]:
    from adaos.services.economic_policy import current_subnet_economic_status

    status = current_subnet_economic_status()
    usage = status.get("usage") if isinstance(status.get("usage"), Mapping) else {}
    tokens = usage.get("codex.api.tokens") if isinstance(usage.get("codex.api.tokens"), Mapping) else {}
    summary_keys = (
        "used_24h",
        "used_7d",
        "used_30d",
        "quota_limit",
        "quota_used",
        "quota_remaining",
        "quota_ratio",
        "quota_warn",
        "quota_exhausted",
        "quota_period",
        "quota_unit",
        "metering",
        "observed",
        "last_model",
        "last_status",
        "last_seen_at",
    )
    return {
        "available": bool(tokens),
        "resource": "codex.api.tokens",
        "usage": {
            key: deepcopy(tokens.get(key))
            for key in summary_keys
            if tokens.get(key) is not None
        },
        "source": status.get("source"),
        "generated_at": status.get("generated_at"),
        "authority_warnings": list(status.get("usage_authority_warnings") or []),
    }


def _read_node_cpu(webspace_id: str) -> dict[str, Any]:
    from adaos.sdk.system import get_operational_snapshot

    snapshot = get_operational_snapshot(
        sections={"resources"}, webspace_id=webspace_id, limit=1
    )
    resources = snapshot.get("resources") if isinstance(snapshot.get("resources"), Mapping) else {}
    cpu = resources.get("cpu") if isinstance(resources.get("cpu"), Mapping) else {}
    return {
        "available": resources.get("available") is True and cpu.get("percent") is not None,
        "cpu": dict(cpu),
        "observed_at": resources.get("observed_at") or snapshot.get("observed_at"),
        "freshness": resources.get("freshness"),
        "source": resources.get("source") or "local_host",
        **({"reason": resources.get("reason")} if resources.get("reason") else {}),
    }


def _run_skill_tool(skill: str, tool: str, payload: Mapping[str, Any]) -> Any:
    from adaos.adapters.db import SqliteSkillRegistry
    from adaos.services.skill.manager import SkillManager

    ctx = get_ctx()
    manager = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=ctx.bus,
        caps=ctx.caps,
        settings=ctx.settings,
    )
    return manager.run_tool(skill, tool, dict(payload), timeout=30.0)


def _search_media(params: Mapping[str, Any]) -> dict[str, Any]:
    query = _text(params.get("query"))
    limit = max(1, min(int(params.get("limit") or 10), 30))
    result = _run_skill_tool(
        "media_center_skill",
        "deep_search",
        {
            "query": query,
            "profile_id": _text(params.get("profile_id")) or "default",
            "media_kind": _text(params.get("media_kind")) or "playable",
            "limit": limit,
            "max_agents": max(1, min(int(params.get("max_agents") or 4), 16)),
        },
    )
    return dict(result) if isinstance(result, Mapping) else {"ok": True, "result": result}


def _cancel_action(target_action_id: str) -> dict[str, Any]:
    target = next(
        (
            item
            for item in _read_state()["receipts"]
            if _text(item.get("action_id")) == target_action_id
        ),
        None,
    )
    if target is None:
        return {"cancelled": False, "reason": "action_not_found"}
    if target.get("status") not in {"pending", "queued"} or not target.get("cancellable"):
        return {
            "cancelled": False,
            "reason": "action_not_cancellable",
            "target_status": target.get("status"),
        }
    return {"cancelled": False, "reason": "cancellation_adapter_unavailable"}


def execute_action_request(
    request: Mapping[str, Any], *, frame: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    body = dict(request)
    webspace = _text(body.get("webspace_id")) or "desktop"
    current_frame = dict(frame) if isinstance(frame, Mapping) else build_context_frame(webspace_id=webspace)
    validation = validate_action_request(body, frame=current_frame)
    action_id = f"companion-action:{new_id()}"
    now = _iso_now()
    receipt: dict[str, Any] = {
        "schema": ACTION_RECEIPT_SCHEMA,
        "action_id": action_id,
        "request_id": _text(body.get("request_id")) or None,
        "operation": _text(body.get("operation")),
        "webspace_id": webspace,
        "status": "rejected" if not validation["ok"] else "accepted",
        "cancellable": False,
        "accepted_at": now if validation["ok"] else None,
        "completed_at": None,
        "context_digest": current_frame.get("context_digest"),
        "validation": validation,
        "result": None,
        "error": None,
    }
    if not validation["ok"]:
        receipt["error"] = {
            "code": validation["errors"][0]["code"],
            "details": validation["errors"],
        }
        _write_receipt(receipt)
        return receipt

    operation = _text(body.get("operation"))
    params = body.get("params") if isinstance(body.get("params"), Mapping) else {}
    trace_id = _text(body.get("trace_id")) or None
    try:
        if operation == "ui.scenario.open":
            _emit_host(
                "desktop.scenario.set",
                {
                    "scenario_id": _text(params.get("scenario_id")),
                    "webspace_id": webspace,
                    "request_source": "companion",
                    "request_id": action_id,
                },
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "desktop.scenario.set"}
        elif operation == "ui.home.open":
            _emit_host(
                "desktop.webspace.go_home",
                {"webspace_id": webspace, "request_source": "companion", "request_id": action_id},
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "desktop.webspace.go_home"}
        elif operation == "ui.modal.open":
            _emit_host(
                "desktop.modal.open",
                {
                    "modal_id": _text(params.get("modal_id")),
                    "webspace_id": webspace,
                    "request_source": "companion",
                    "request_id": action_id,
                },
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "desktop.modal.open"}
        elif operation == "ui.modal.close":
            _emit_host(
                "desktop.modal.close",
                {"webspace_id": webspace, "request_source": "companion", "request_id": action_id},
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "desktop.modal.close"}
        elif operation == "ui.state.set":
            _emit_host(
                "ui.state.set",
                {
                    "key": _text(params.get("key")),
                    "value": params.get("value"),
                    "webspace_id": webspace,
                    "request_id": action_id,
                },
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "ui.state.set"}
        elif operation == "ui.widget.focus":
            _emit_host(
                "ui.focus_widget",
                {
                    "widget_id": _text(params.get("widget_id")),
                    "webspace_id": webspace,
                    "request_id": action_id,
                },
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {"topic": "ui.focus_widget"}
        elif operation == "ui.affordance.activate":
            affordance_id = _text(params.get("affordance_id"))
            affordance = _find_voice_affordance(current_frame, affordance_id) or {}
            activation = list(affordance.get("activation") or [])
            _emit_host(
                "voice.capability.activate",
                {
                    "affordance_id": affordance_id,
                    "activation_plan": json.dumps(activation, ensure_ascii=False),
                    "webspace_id": webspace,
                    "request_id": action_id,
                },
                trace_id=trace_id,
            )
            receipt["status"] = "dispatched"
            receipt["result"] = {
                "topic": "voice.capability.activate",
                "activation_step_count": len(activation),
            }
        elif operation == "status.codex_tokens.read":
            receipt["status"] = "completed"
            receipt["result"] = _read_codex_tokens()
        elif operation == "status.node_cpu.read":
            receipt["status"] = "completed"
            receipt["result"] = _read_node_cpu(webspace)
        elif operation == "media.catalog.search":
            receipt["status"] = "completed"
            receipt["result"] = _search_media(params)
        elif operation == "action.cancel":
            receipt["status"] = "completed"
            receipt["result"] = _cancel_action(_text(params.get("target_action_id")))
        else:  # pragma: no cover - guarded by validation
            raise ValueError("operation_not_implemented")
        if receipt["status"] == "completed":
            receipt["completed_at"] = _iso_now()
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["completed_at"] = _iso_now()
        receipt["error"] = {
            "code": type(exc).__name__,
            "message": str(exc)[:500],
        }
    _write_receipt(receipt)
    return receipt


def _capture_capability_request(arguments: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.sdk.development_tickets import create_ticket

    summary = _text(arguments.get("summary"))
    if not summary:
        raise ValueError("summary is required")
    desired = _text(arguments.get("desired_outcome")) or summary
    webspace = _text(arguments.get("webspace_id")) or "desktop"
    result = create_ticket(
        summary,
        kind="development_request",
        target_scope={
            "type": "companion_capability",
            "id": _text(arguments.get("capability_id")) or "unclassified",
            "webspace_id": webspace,
        },
        origin_scope={
            "type": "companion",
            "id": _text(arguments.get("companion_id")) or "sage",
            "surface": "companion_control_plane",
        },
        severity=_text(arguments.get("severity")) or "medium",
        blocking=False,
        source="companion_capability_request",
        status="proposed",
        actor=_text(arguments.get("companion_id")) or "sage",
        metadata={
            "schema": CAPABILITY_REQUEST_SCHEMA,
            "desired_outcome": desired,
            "utterance": _text(arguments.get("utterance")) or None,
            "context_digest": _text(arguments.get("context_digest")) or None,
        },
        owner_area="project",
        component_ref="application:companion_console",
    )
    return {
        "schema": CAPABILITY_REQUEST_SCHEMA,
        "status": "recorded",
        "duplicate": bool(result.get("ticket_duplicate")),
        "ticket": result.get("ticket"),
        "signal": result.get("signal"),
    }


def contracts() -> list[RootMcpToolContract]:
    from adaos.services.companion.policy import enabled

    if not enabled():
        return []
    response = deepcopy(ROOT_MCP_RESPONSE_SCHEMA)
    published = {
        "published_by": "plane:companion_control",
        "adapter": "adaos.services.root_mcp.companion_plane",
    }
    request_schema = schema_object(
        properties={
            "request_id": {"type": "string", "maxLength": 200},
            "operation": {"type": "string", "enum": list(_OPERATION_BY_ID)},
            "params": {"type": "object", "additionalProperties": True},
            "webspace_id": {"type": "string", "maxLength": 128},
            "context_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "utterance": {"type": "string", "maxLength": 4000},
        },
        required=["operation"],
    )
    return [
        RootMcpToolContract(
            id="companion.context.get",
            title="Get Companion context frame",
            surface=RootMcpSurface.OPERATIONS,
            summary="Compile current scenario, semantic UI surface, skills and bounded Companion affordances.",
            input_schema=schema_object(
                properties={
                    "webspace_id": {"type": "string", "maxLength": 128},
                    "include_live": {"type": "boolean", "default": True},
                }
            ),
            output_schema=deepcopy(response),
            required_capability="companion.read",
            metadata={**published, "handler": "companion_context_get"},
        ),
        RootMcpToolContract(
            id="companion.action.preview",
            title="Preview Companion action",
            surface=RootMcpSurface.OPERATIONS,
            summary="Validate an action against the current context without dispatching it.",
            input_schema=request_schema,
            output_schema=deepcopy(response),
            required_capability="companion.read",
            metadata={**published, "handler": "companion_action_preview"},
        ),
        RootMcpToolContract(
            id="companion.action.execute",
            title="Execute Companion action",
            surface=RootMcpSurface.OPERATIONS,
            summary="Execute one allowlisted semantic action and return a durable action receipt.",
            input_schema=request_schema,
            output_schema=deepcopy(response),
            required_capability="companion.execute",
            side_effects="write",
            metadata={**published, "handler": "companion_action_execute"},
        ),
        RootMcpToolContract(
            id="companion.activity.list",
            title="List Companion action receipts",
            surface=RootMcpSurface.OPERATIONS,
            summary="Return recent bounded action receipts without conversation text.",
            input_schema=schema_object(
                properties={
                    "webspace_id": {"type": "string", "maxLength": 128},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                }
            ),
            output_schema=deepcopy(response),
            required_capability="companion.read",
            metadata={**published, "handler": "companion_activity_list"},
        ),
        RootMcpToolContract(
            id="companion.capability_request.capture",
            title="Capture missing Companion capability",
            surface=RootMcpSurface.OPERATIONS,
            summary="Record an unmet user request as a deduplicated AdaOS Dev Ticket for later Builder work.",
            input_schema=schema_object(
                properties={
                    "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "desired_outcome": {"type": "string", "maxLength": 4000},
                    "capability_id": {"type": "string", "maxLength": 160},
                    "utterance": {"type": "string", "maxLength": 4000},
                    "webspace_id": {"type": "string", "maxLength": 128},
                    "companion_id": {"type": "string", "maxLength": 128},
                    "context_digest": {"type": "string", "maxLength": 80},
                    "severity": {"enum": ["info", "low", "medium", "high"]},
                },
                required=["summary"],
            ),
            output_schema=deepcopy(response),
            required_capability="companion.request",
            side_effects="write",
            metadata={**published, "handler": "companion_capability_request_capture"},
        ),
    ]


def _handle_context(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {
        "context": build_context_frame(
            webspace_id=_text(arguments.get("webspace_id")) or "desktop",
            include_live=bool(arguments.get("include_live", True)),
        )
    }


def _handle_preview(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    frame = build_context_frame(webspace_id=request["webspace_id"])
    return {"preview": validate_action_request(request, frame=frame), "context": frame}


def _handle_execute(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = _request(arguments)
    frame = build_context_frame(webspace_id=request["webspace_id"])
    if dry_run:
        return {"preview": validate_action_request(request, frame=frame), "context": frame}
    return {"receipt": execute_action_request(request, frame=frame)}


def _handle_activity(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    rows = _recent_receipts(
        webspace_id=_text(arguments.get("webspace_id")) or None,
        limit=int(arguments.get("limit") or 30),
    )
    return {"receipts": rows, "count": len(rows), "conversation_text_redacted": True}


def _handle_capability_request(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_capture": True, "request": request}
    return {"capability_request": _capture_capability_request(request)}


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    from functools import wraps
    from adaos.services.companion.policy import require_enabled

    def gated(handler):
        @wraps(handler)
        def invoke(*args, **kwargs):
            require_enabled()
            return handler(*args, **kwargs)
        return invoke

    return {name: gated(handler) for name, handler in {
        "companion.context.get": _handle_context,
        "companion.action.preview": _handle_preview,
        "companion.action.execute": _handle_execute,
        "companion.activity.list": _handle_activity,
        "companion.capability_request.capture": _handle_capability_request,
    }.items()}


__all__ = [
    "ACTION_RECEIPT_SCHEMA",
    "ACTION_REQUEST_SCHEMA",
    "AFFORDANCE_SCHEMA",
    "CAPABILITY_REQUEST_SCHEMA",
    "CONTEXT_SCHEMA",
    "build_context_frame",
    "contracts",
    "execute_action_request",
    "handlers",
    "validate_action_request",
]
