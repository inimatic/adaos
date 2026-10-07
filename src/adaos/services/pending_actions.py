from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.domain import Event
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.pending_action_inventory import validate_legacy_publication
from adaos.services.root_mcp.opaque_cursor import decode_opaque_cursor, encode_opaque_cursor
from adaos.services.yjs.doc import async_get_ydoc, get_ydoc, submit_live_room_mutation
from adaos.services.yjs.store import ystore_write_metadata, ystore_write_metadata_sync
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.pending_actions")
_LOCK = threading.RLock()
_ACTIVE_STATUSES = {"pending", "postponed"}
_TERMINAL_STATUSES = {"responded", "expired", "cancelled"}
_NO_VALUE = object()
_QUERY_FIELD_MASKS: dict[str, tuple[str, ...]] = {
    "summary": (
        "id",
        "kind",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "title",
        "summary",
        "priority",
        "roadmap_id",
        "producer",
        "owner_scope",
        "domain_ref",
        "allowed_actions",
        "contract_ref",
    ),
    "detail": (
        "id",
        "kind",
        "status",
        "created_at",
        "updated_at",
        "finished_at",
        "expires_at",
        "title",
        "summary",
        "title_i18n",
        "summary_i18n",
        "request_text",
        "request_locale",
        "preferred_locales",
        "priority",
        "roadmap_id",
        "producer",
        "owner_scope",
        "domain_ref",
        "allowed_actions",
        "default_text_binding",
        "payload_ref",
        "metadata",
        "response",
        "last_response",
        "cancellation",
        "contract_ref",
    ),
    "audit": (
        "id",
        "kind",
        "status",
        "created_at",
        "updated_at",
        "finished_at",
        "expires_at",
        "roadmap_id",
        "producer",
        "owner_scope",
        "domain_ref",
        "history",
        "contract_ref",
    ),
}

_DEFAULT_ACTIONS: dict[str, dict[str, Any]] = {
    "test": {
        "label": "Test",
        "label_i18n": {"key": "pending_actions.action.test"},
        "terminal": False,
    },
    "preview": {
        "label": "Preview",
        "label_i18n": {"key": "pending_actions.action.preview"},
        "terminal": False,
    },
    "approve": {
        "label": "Approve",
        "label_i18n": {"key": "pending_actions.action.approve"},
        "terminal": True,
    },
    "refuse": {
        "label": "Refuse",
        "label_i18n": {"key": "pending_actions.action.refuse"},
        "terminal": True,
    },
    "postpone": {
        "label": "Later",
        "label_i18n": {"key": "pending_actions.action.postpone"},
        "terminal": False,
    },
}


def _pending_actions_async_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="pending_actions.core",
        owner="core:pending_actions",
        channel="core.pending_actions.async",
        governed=True,
    )


def _pending_actions_sync_write_meta():
    return ystore_write_metadata_sync(
        root_names=["data"],
        source="pending_actions.core",
        owner="core:pending_actions",
        channel="core.pending_actions.sync",
        governed=True,
    )


def _now_ts() -> float:
    return time.time()


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _content_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _resolve_webspace_id(webspace_id: str | None) -> str:
    return _text(webspace_id) or default_webspace_id()


def _max_items() -> int:
    raw = _text(os.getenv("ADAOS_PENDING_ACTIONS_MAX_ITEMS"))
    if not raw:
        return 200
    try:
        value = int(raw)
    except ValueError:
        return 200
    return max(20, min(value, 5000))


def _limit_from_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = _text(os.getenv(name))
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _max_payload_bytes() -> int:
    return _limit_from_env(
        "ADAOS_PENDING_ACTION_MAX_BYTES",
        default=64 * 1024,
        minimum=1024,
        maximum=1024 * 1024,
    )


def _max_outstanding() -> int:
    return _limit_from_env(
        "ADAOS_PENDING_ACTION_MAX_OUTSTANDING",
        default=500,
        minimum=20,
        maximum=5000,
    )


def _max_outstanding_per_producer() -> int:
    return _limit_from_env(
        "ADAOS_PENDING_ACTION_MAX_OUTSTANDING_PER_PRODUCER",
        default=50,
        minimum=1,
        maximum=500,
    )


def _ctx_node_id(ctx: AgentContext | None) -> str:
    if ctx is None:
        return ""
    config = getattr(ctx, "config", None)
    candidates = [
        getattr(config, "node_id_value", None),
        getattr(config, "node_id", None),
    ]
    node_settings = getattr(config, "node_settings", None)
    candidates.append(getattr(node_settings, "id", None))
    if isinstance(config, Mapping):
        candidates.extend([config.get("node_id_value"), config.get("node_id")])
    for candidate in candidates:
        token = _text(candidate)
        if token:
            return token
    return ""


def _normalize_actor(value: Any, *, ctx: AgentContext | None, default_type: str = "system") -> dict[str, Any]:
    actor = _mapping(value)
    actor_type = _text(actor.get("type")) or default_type
    actor["type"] = actor_type
    if not _text(actor.get("node_id")):
        node_id = _ctx_node_id(ctx)
        if node_id:
            actor["node_id"] = node_id
    skill_id = _text(actor.get("skill_id"))
    scenario_id = _text(actor.get("scenario_id"))
    system_id = _text(actor.get("system_id"))
    if actor_type == "system" and not system_id:
        actor["system_id"] = "core"
    if not _text(actor.get("instance_id")):
        node_id = _text(actor.get("node_id"))
        if skill_id and node_id:
            actor["instance_id"] = f"{skill_id}@{node_id}"
        elif scenario_id and node_id:
            actor["instance_id"] = f"{scenario_id}@{node_id}"
        elif system_id:
            actor["instance_id"] = system_id
    return actor


def _actor_instance_key(value: Any) -> str:
    """Return a stable admission key for both old and current producer shapes."""

    actor = _mapping(value)
    instance_id = _text(actor.get("instance_id"))
    if instance_id:
        return instance_id
    actor_type = _text(actor.get("type")) or "unknown"
    for field in ("skill_id", "scenario_id", "system_id", "device_id", "user_id", "id"):
        actor_id = _text(actor.get(field))
        if actor_id:
            node_id = _text(actor.get("node_id"))
            return f"{actor_type}:{field}:{actor_id}@{node_id}" if node_id else f"{actor_type}:{field}:{actor_id}"
    return ""


def _normalize_i18n(value: Any) -> dict[str, Any] | None:
    data = _mapping(value)
    key = _text(data.get("key"))
    if not key:
        return None
    params = data.get("params")
    data["key"] = key
    data["params"] = _mapping(params)
    return data


def _normalize_expires_at(*, expires_at: Any = _NO_VALUE, ttl_s: Any = None, now: float) -> float | None:
    if expires_at is not _NO_VALUE:
        if expires_at is None:
            return None
        if isinstance(expires_at, str):
            raw = expires_at.strip()
            if raw in {"", "0"}:
                raise ValueError("expires_at must be missing or null for no expiration")
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError("expires_at must be a positive unix timestamp") from exc
        else:
            try:
                value = float(expires_at)
            except Exception as exc:
                raise ValueError("expires_at must be a positive unix timestamp") from exc
        if value <= 0:
            raise ValueError("expires_at must be a positive unix timestamp")
        return value

    if ttl_s is None:
        return None
    if isinstance(ttl_s, str):
        raw = ttl_s.strip()
        if raw in {"", "0"}:
            raise ValueError("ttl_s must be missing or null for no expiration")
        try:
            ttl = float(raw)
        except ValueError as exc:
            raise ValueError("ttl_s must be a positive number of seconds") from exc
    else:
        try:
            ttl = float(ttl_s)
        except Exception as exc:
            raise ValueError("ttl_s must be a positive number of seconds") from exc
    if ttl <= 0:
        raise ValueError("ttl_s must be a positive number of seconds")
    return now + ttl


def _normalize_allowed_actions(actions: Sequence[Any] | None) -> list[dict[str, Any]]:
    if actions is None:
        actions = ("approve", "refuse", "postpone")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in actions:
        if isinstance(raw, str):
            action_id = _text(raw)
            action = dict(_DEFAULT_ACTIONS.get(action_id, {}))
            action["id"] = action_id
        else:
            action = _mapping(raw)
            action_id = _text(action.get("id"))
            defaults = _DEFAULT_ACTIONS.get(action_id, {})
            for key in ("label", "label_i18n", "terminal"):
                if key not in action and key in defaults:
                    action[key] = _json_clone(defaults[key])
            action["id"] = action_id
        if not action_id:
            raise ValueError("allowed_actions[].id is required")
        if action_id in seen:
            raise ValueError(f"duplicate allowed action id: {action_id}")
        if "terminal" not in action:
            action["terminal"] = True
        label_i18n = _normalize_i18n(action.get("label_i18n"))
        if label_i18n is not None:
            action["label_i18n"] = label_i18n
        normalized.append(action)
        seen.add(action_id)
    if not normalized:
        raise ValueError("at least one allowed action is required")
    return normalized


def _normalize_response_route(
    *,
    response_route: Mapping[str, Any] | None = None,
    response_topic: str | None = None,
    ctx: AgentContext | None,
) -> dict[str, Any]:
    route = _mapping(response_route)
    if response_topic and "topic" not in route:
        route["topic"] = response_topic
    if "type" not in route:
        route["type"] = "event"
    topic = _text(route.get("topic"))
    if _text(route.get("type")) != "event":
        raise ValueError("only event response routes are supported")
    if not topic:
        raise ValueError("response_route.topic is required")
    route["topic"] = topic
    target = _mapping(route.get("target"))
    if target:
        route["target"] = _normalize_actor(target, ctx=ctx, default_type=_text(target.get("type")) or "skill")
    return route


def _normalize_projection(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    raw_by_id = _mapping(raw.get("by_id"))
    by_id: dict[str, dict[str, Any]] = {}
    for raw_id, item in raw_by_id.items():
        action_id = _text(raw_id)
        action = _mapping(item)
        if action_id and action:
            action["id"] = _text(action.get("id")) or action_id
            by_id[action_id] = action
    order: list[str] = []
    for raw_id in _list(raw.get("order")):
        action_id = _text(raw_id)
        if action_id and action_id in by_id and action_id not in order:
            order.append(action_id)
    for action_id in by_id:
        if action_id not in order:
            order.append(action_id)
    return {"by_id": by_id, "order": order}


def _prune_projection(projection: dict[str, Any]) -> None:
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    order: list[str] = projection["order"]
    limit = _max_items()
    while len(order) > limit:
        drop_index = next(
            (
                idx
                for idx, action_id in enumerate(order)
                if _text(by_id.get(action_id, {}).get("status")) in _TERMINAL_STATUSES
            ),
            None,
        )
        if drop_index is None:
            # Projection/history limits must never discard an unresolved human
            # decision. Admission limits prevent unbounded active growth.
            break
        action_id = order.pop(drop_index)
        by_id.pop(action_id, None)


def _project_expired_actions(projection: dict[str, Any], *, now: float) -> None:
    """Derive an honest view even when the periodic expiry write has not run."""

    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    for action_id, action in list(by_id.items()):
        if _text(action.get("status")) not in _ACTIVE_STATUSES:
            continue
        expires_at = action.get("expires_at")
        if expires_at is None:
            continue
        try:
            due = float(expires_at) <= now
        except (TypeError, ValueError):
            due = False
        if due:
            by_id[action_id] = _mark_expired(action, now=now)


def _build_projection(projection: dict[str, Any], *, updated_at: float) -> dict[str, Any]:
    _project_expired_actions(projection, now=updated_at)
    _prune_projection(projection)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    order: list[str] = [action_id for action_id in projection["order"] if action_id in by_id]
    projection["order"] = order
    active_ids = [
        action_id
        for action_id in order
        if _text(by_id.get(action_id, {}).get("status")) in _ACTIVE_STATUSES
    ]
    history_ids = [
        action_id
        for action_id in reversed(order)
        if _text(by_id.get(action_id, {}).get("status")) in _TERMINAL_STATUSES
    ]
    return {
        "schema_version": 1,
        "by_id": _json_clone(by_id),
        "order": list(order),
        "active": list(active_ids),
        "active_count": len(active_ids),
        "active_items": [_json_clone(by_id[action_id]) for action_id in active_ids],
        "history_count": len(history_ids),
        "history_items": [_json_clone(by_id[action_id]) for action_id in history_ids],
        "updated_at": updated_at,
    }


def _read_projection(ydoc: Any) -> tuple[Any, dict[str, Any]]:
    data_map = ydoc.get_map("data")
    return data_map, _normalize_projection(data_map.get("pending_actions"))


def _write_projection(data_map: Any, ydoc: Any, snapshot: dict[str, Any], txn: Any = None) -> None:
    if txn is not None:
        data_map.set(txn, "pending_actions", snapshot)
        return
    with ydoc.begin_transaction() as txn:
        data_map.set(txn, "pending_actions", snapshot)


def _event_payload(evt: Any) -> dict[str, Any]:
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(evt, Mapping):
        return dict(evt)
    return {}


def _emit(ctx: AgentContext | None, topic: str, payload: Mapping[str, Any], *, source: str = "pending_actions.core") -> None:
    if ctx is None:
        return
    bus = getattr(ctx, "bus", None)
    if bus is None:
        return
    try:
        bus.publish(Event(type=topic, payload=dict(payload), source=source, ts=_now_ts()))
    except Exception:
        _log.debug("failed to publish pending action event topic=%s", topic, exc_info=True)


def _event_sequence_for_publish(action: dict[str, Any], snapshot: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("pending_actions.created", {"action": action, "webspace_id": action.get("webspace_id")}),
        (
            "pending_actions.changed",
            {
                "webspace_id": action.get("webspace_id"),
                "pending_actions": snapshot,
            },
        ),
    ]


def _event_sequence_for_response(
    action: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    response: dict[str, Any],
    duplicate: bool,
) -> list[tuple[str, dict[str, Any]]]:
    if duplicate:
        return []
    return [
        (
            "pending_actions.responded",
            {
                "action": action,
                "response": response,
                "webspace_id": action.get("webspace_id"),
            },
        ),
        (
            "pending_actions.changed",
            {
                "webspace_id": action.get("webspace_id"),
                "pending_actions": snapshot,
            },
        ),
    ]


def _publish_route_response(ctx: AgentContext | None, action: dict[str, Any], response: dict[str, Any]) -> None:
    route = _mapping(action.get("response_route"))
    topic = _text(route.get("topic"))
    if not topic:
        return
    payload = {
        "pending_action_id": action.get("id"),
        "pending_action": action,
        "response": response,
        "response_action_id": response.get("response_action_id"),
        "webspace_id": action.get("webspace_id"),
        "domain_ref": _mapping(action.get("domain_ref")),
        "route_target": _mapping(route.get("target")),
    }
    route_payload = _mapping(route.get("payload"))
    if route_payload:
        payload["route_payload"] = route_payload
    _emit(ctx, topic, payload, source="pending_actions.response_route")


def _normalize_pending_action(
    *,
    ctx: AgentContext | None,
    webspace_id: str,
    action_id: str | None = None,
    kind: str,
    title: str = "",
    summary: str = "",
    title_i18n: Mapping[str, Any] | None = None,
    summary_i18n: Mapping[str, Any] | None = None,
    request_text: str = "",
    request_locale: str = "",
    preferred_locales: Sequence[str] | None = None,
    producer: Mapping[str, Any] | None = None,
    owner_scope: Mapping[str, Any] | None = None,
    domain_ref: Mapping[str, Any] | None = None,
    allowed_actions: Sequence[Any] | None = None,
    actions: Sequence[Any] | None = None,
    default_text_binding: bool | None = None,
    response_route: Mapping[str, Any] | None = None,
    response_topic: str | None = None,
    ttl_s: Any = None,
    expires_at: Any = _NO_VALUE,
    priority: int | None = None,
    payload_ref: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now_ts()
    kind_token = _text(kind)
    if not kind_token:
        raise ValueError("kind is required")
    normalized_id = _text(action_id) or f"pa.{int(now * 1000)}.{uuid.uuid4().hex[:8]}"
    producer_actor = _normalize_actor(producer or {}, ctx=ctx, default_type="system")
    normalized_owner_scope = _mapping(owner_scope)
    normalized_owner_scope["webspace_id"] = _text(normalized_owner_scope.get("webspace_id")) or webspace_id
    if "node_id" not in normalized_owner_scope and _text(producer_actor.get("node_id")):
        normalized_owner_scope["node_id"] = _text(producer_actor.get("node_id"))
    route = _normalize_response_route(response_route=response_route, response_topic=response_topic, ctx=ctx)
    selected_actions = allowed_actions if allowed_actions is not None else actions
    normalized_actions = _normalize_allowed_actions(selected_actions)
    publication_contract = validate_legacy_publication(
        kind=kind_token,
        response_topic=_text(route.get("topic")),
        choices=[_text(item.get("id")) for item in normalized_actions],
    )
    action = {
        "id": normalized_id,
        "kind": kind_token,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "expires_at": _normalize_expires_at(expires_at=expires_at, ttl_s=ttl_s, now=now),
        "title": _text(title),
        "summary": _text(summary),
        "request_text": _text(request_text),
        "request_locale": _text(request_locale),
        "preferred_locales": [_text(item) for item in _list(preferred_locales) if _text(item)],
        "producer": producer_actor,
        "owner_scope": normalized_owner_scope,
        "domain_ref": _mapping(domain_ref),
        "allowed_actions": normalized_actions,
        "default_text_binding": bool(default_text_binding),
        "response_route": route,
        "webspace_id": webspace_id,
        "history": [],
        "contract_ref": publication_contract["inventory"],
        "roadmap_id": publication_contract["roadmap_id"],
    }
    normalized_title_i18n = _normalize_i18n(title_i18n)
    if normalized_title_i18n is not None:
        action["title_i18n"] = normalized_title_i18n
    normalized_summary_i18n = _normalize_i18n(summary_i18n)
    if normalized_summary_i18n is not None:
        action["summary_i18n"] = normalized_summary_i18n
    if priority is not None:
        action["priority"] = int(priority)
    normalized_payload_ref = _mapping(payload_ref)
    if normalized_payload_ref:
        action["payload_ref"] = normalized_payload_ref
    normalized_metadata = _mapping(metadata)
    if normalized_metadata:
        action["metadata"] = normalized_metadata
    return action


def _add_action_to_doc(ydoc: Any, action: dict[str, Any], txn: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    data_map, projection = _read_projection(ydoc)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    order: list[str] = projection["order"]
    action_id = _text(action.get("id"))
    if action_id in by_id:
        raise ValueError(f"pending action already exists: {action_id}")
    encoded = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _max_payload_bytes():
        raise ValueError(
            f"pending_action_payload_too_large: {len(encoded)} > {_max_payload_bytes()} bytes"
        )
    now = _now_ts()
    _project_expired_actions(projection, now=now)
    active = [item for item in by_id.values() if _text(item.get("status")) in _ACTIVE_STATUSES]
    if len(active) >= _max_outstanding():
        raise ValueError("pending_action_outstanding_limit")
    producer_instance = _actor_instance_key(action.get("producer"))
    if producer_instance:
        producer_active = sum(
            1
            for item in active
            if _actor_instance_key(item.get("producer")) == producer_instance
        )
        if producer_active >= _max_outstanding_per_producer():
            raise ValueError("pending_action_producer_outstanding_limit")
    by_id[action_id] = _json_clone(action)
    order.append(action_id)
    snapshot = _build_projection(projection, updated_at=now)
    _write_projection(data_map, ydoc, snapshot, txn)
    return _json_clone(action), snapshot


async def _publish_action_on_live_room(ws: str, action: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    # Normalize outside the owner loop; only the small atomic projection mutation
    # crosses into it. No YDoc/transaction may escape this callback.
    result = await submit_live_room_mutation(
        ws, lambda doc, txn: _add_action_to_doc(doc, action, txn),
        root_names=["data"], source="pending_actions.core", owner="core:pending_actions",
        channel="core.pending_actions.async", governed=True,
    )
    if result.get("applied"):
        return result["mutator_result"]
    if result.get("reason") == "room_not_ready":
        return None
    raise RuntimeError(result.get("error") or result.get("reason") or "pending_action_publish_failed")


def publish_pending_action(
    *,
    ctx: AgentContext | None = None,
    webspace_id: str | None = None,
    action_id: str | None = None,
    kind: str,
    title: str = "",
    summary: str = "",
    title_i18n: Mapping[str, Any] | None = None,
    summary_i18n: Mapping[str, Any] | None = None,
    request_text: str = "",
    request_locale: str = "",
    preferred_locales: Sequence[str] | None = None,
    producer: Mapping[str, Any] | None = None,
    owner_scope: Mapping[str, Any] | None = None,
    domain_ref: Mapping[str, Any] | None = None,
    allowed_actions: Sequence[Any] | None = None,
    actions: Sequence[Any] | None = None,
    default_text_binding: bool | None = None,
    response_route: Mapping[str, Any] | None = None,
    response_topic: str | None = None,
    ttl_s: Any = None,
    expires_at: Any = _NO_VALUE,
    priority: int | None = None,
    payload_ref: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    action = _normalize_pending_action(
        ctx=ctx,
        webspace_id=ws,
        action_id=action_id,
        kind=kind,
        title=title,
        summary=summary,
        title_i18n=title_i18n,
        summary_i18n=summary_i18n,
        request_text=request_text,
        request_locale=request_locale,
        preferred_locales=preferred_locales,
        producer=producer,
        owner_scope=owner_scope,
        domain_ref=domain_ref,
        allowed_actions=allowed_actions,
        actions=actions,
        default_text_binding=default_text_binding,
        response_route=response_route,
        response_topic=response_topic,
        ttl_s=ttl_s,
        expires_at=expires_at,
        priority=priority,
        payload_ref=payload_ref,
        metadata=metadata,
    )
    try:
        with _LOCK:
            with _pending_actions_sync_write_meta():
                with get_ydoc(ws, load_mark_roots=["data"], governed=True) as ydoc:
                    stored_action, snapshot = _add_action_to_doc(ydoc, action)
    except RuntimeError as exc:
        if str(exc) != "sync_get_ydoc_live_room_requires_owner_handoff":
            raise
        # This synchronous entry point is also used by diagnostics workers.
        # Never wait for the owner while holding the projection lock, and never
        # try to block an async caller's loop: it must use the async API.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            stored = asyncio.run(_publish_action_on_live_room(ws, action))
            if stored is None:
                raise RuntimeError("pending_action_live_room_not_ready") from exc
            stored_action, snapshot = stored
        else:
            raise RuntimeError("pending_action_publish_requires_async_api") from exc
    for topic, payload in _event_sequence_for_publish(stored_action, snapshot):
        _emit(ctx, topic, payload)
    return stored_action


async def publish_pending_action_async(**kwargs: Any) -> dict[str, Any]:
    ctx = kwargs.pop("ctx", None) or get_ctx()
    ws = _resolve_webspace_id(kwargs.pop("webspace_id", None))
    action = _normalize_pending_action(ctx=ctx, webspace_id=ws, **kwargs)
    stored = await _publish_action_on_live_room(ws, action)
    if stored is not None:
        stored_action, snapshot = stored
    else:
        with _LOCK:
            async with _pending_actions_async_write_meta():
                async with async_get_ydoc(
                    ws,
                    load_mark_roots=["data"],
                    governed=True,
                    write_source="pending_actions.core",
                    write_owner="core:pending_actions",
                    write_channel="core.pending_actions.async",
                ) as ydoc:
                    stored_action, snapshot = _add_action_to_doc(ydoc, action)
    for topic, payload in _event_sequence_for_publish(stored_action, snapshot):
        _emit(ctx, topic, payload)
    return stored_action


def _allowed_action_by_id(action: dict[str, Any], response_action_id: str) -> dict[str, Any] | None:
    for item in _list(action.get("allowed_actions")):
        candidate = _mapping(item)
        if _text(candidate.get("id")) == response_action_id:
            return candidate
    return None


def _same_terminal_response(action: dict[str, Any], *, response_action_id: str, idempotency_key: str = "") -> bool:
    response = _mapping(action.get("response"))
    if not response:
        return False
    if idempotency_key and _text(response.get("idempotency_key")) == idempotency_key:
        return True
    return _text(response.get("response_action_id")) == response_action_id


def _append_history(action: dict[str, Any], item: Mapping[str, Any]) -> None:
    history = [_mapping(x) for x in _list(action.get("history")) if _mapping(x)]
    history.append(dict(item))
    action["history"] = history[-50:]


def _mark_expired(action: dict[str, Any], *, now: float) -> dict[str, Any]:
    action["status"] = "expired"
    action["updated_at"] = now
    action["finished_at"] = now
    _append_history(action, {"kind": "expired", "ts": now})
    return action


def _respond_in_doc(
    ydoc: Any,
    *,
    action_id: str,
    response_action_id: str,
    responder: Mapping[str, Any] | None,
    response_payload: Mapping[str, Any] | None,
    idempotency_key: str,
    ctx: AgentContext | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    data_map, projection = _read_projection(ydoc)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    action = _mapping(by_id.get(action_id))
    if not action:
        raise ValueError("pending action not found")
    status = _text(action.get("status")) or "pending"
    if status == "responded" and _same_terminal_response(
        action,
        response_action_id=response_action_id,
        idempotency_key=idempotency_key,
    ):
        snapshot = _build_projection(projection, updated_at=_now_ts())
        return action, snapshot, _mapping(action.get("response")), True
    if status in _TERMINAL_STATUSES:
        raise ValueError(f"pending action is already terminal: {status}")
    now = _now_ts()
    expires_at = action.get("expires_at")
    if expires_at is not None:
        try:
            is_expired = float(expires_at) <= now
        except Exception:
            is_expired = False
        if is_expired:
            by_id[action_id] = _mark_expired(action, now=now)
            snapshot = _build_projection(projection, updated_at=now)
            _write_projection(data_map, ydoc, snapshot)
            raise ValueError("pending action is expired")
    selected = _allowed_action_by_id(action, response_action_id)
    if selected is None:
        raise ValueError(f"response action is not allowed: {response_action_id}")
    response = {
        "response_action_id": response_action_id,
        "responder": _normalize_actor(responder or {}, ctx=ctx, default_type="user"),
        "payload": _mapping(response_payload),
        "responded_at": now,
    }
    if idempotency_key:
        response["idempotency_key"] = idempotency_key
    terminal = bool(selected.get("terminal", True))
    action["updated_at"] = now
    action["last_response"] = response
    _append_history(
        action,
        {
            "kind": "response",
            "response_action_id": response_action_id,
            "terminal": terminal,
            "ts": now,
        },
    )
    if terminal:
        action["status"] = "responded"
        action["finished_at"] = now
        action["response"] = response
    else:
        action["status"] = "postponed" if response_action_id == "postpone" else "pending"
    by_id[action_id] = action
    snapshot = _build_projection(projection, updated_at=now)
    _write_projection(data_map, ydoc, snapshot)
    return _json_clone(action), snapshot, response, False


def respond_pending_action(
    action_id: str,
    response_action_id: str,
    *,
    ctx: AgentContext | None = None,
    webspace_id: str | None = None,
    responder: Mapping[str, Any] | None = None,
    response_payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    action_id = _text(action_id)
    response_action_id = _text(response_action_id)
    if not action_id:
        raise ValueError("action_id is required")
    if not response_action_id:
        raise ValueError("response_action_id is required")
    with _LOCK:
        with _pending_actions_sync_write_meta():
            with get_ydoc(ws, load_mark_roots=["data"], governed=True) as ydoc:
                action, snapshot, response, duplicate = _respond_in_doc(
                    ydoc,
                    action_id=action_id,
                    response_action_id=response_action_id,
                    responder=responder,
                    response_payload=response_payload,
                    idempotency_key=_text(idempotency_key),
                    ctx=ctx,
                )
    for topic, payload in _event_sequence_for_response(action, snapshot, response=response, duplicate=duplicate):
        _emit(ctx, topic, payload)
    if not duplicate:
        _publish_route_response(ctx, action, response)
    return {
        "action": action,
        "response": response,
        "duplicate": duplicate,
        "terminal": _text(action.get("status")) in _TERMINAL_STATUSES,
    }


async def respond_pending_action_async(
    action_id: str,
    response_action_id: str,
    *,
    ctx: AgentContext | None = None,
    webspace_id: str | None = None,
    responder: Mapping[str, Any] | None = None,
    response_payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    action_id = _text(action_id)
    response_action_id = _text(response_action_id)
    if not action_id:
        raise ValueError("action_id is required")
    if not response_action_id:
        raise ValueError("response_action_id is required")
    with _LOCK:
        async with _pending_actions_async_write_meta():
            async with async_get_ydoc(
                ws,
                load_mark_roots=["data"],
                governed=True,
                write_source="pending_actions.core",
                write_owner="core:pending_actions",
                write_channel="core.pending_actions.async",
            ) as ydoc:
                action, snapshot, response, duplicate = _respond_in_doc(
                    ydoc,
                    action_id=action_id,
                    response_action_id=response_action_id,
                    responder=responder,
                    response_payload=response_payload,
                    idempotency_key=_text(idempotency_key),
                    ctx=ctx,
                )
    for topic, payload in _event_sequence_for_response(action, snapshot, response=response, duplicate=duplicate):
        _emit(ctx, topic, payload)
    if not duplicate:
        _publish_route_response(ctx, action, response)
    return {
        "action": action,
        "response": response,
        "duplicate": duplicate,
        "terminal": _text(action.get("status")) in _TERMINAL_STATUSES,
    }


def list_pending_actions(
    *,
    webspace_id: str | None = None,
    include_terminal: bool = True,
) -> dict[str, Any]:
    ws = _resolve_webspace_id(webspace_id)
    with get_ydoc(ws, read_only=True, load_mark_roots=["data"], governed=True) as ydoc:
        _, projection = _read_projection(ydoc)
        snapshot = _build_projection(projection, updated_at=_now_ts())
    if include_terminal:
        return snapshot
    active_by_id = {action_id: snapshot["by_id"][action_id] for action_id in snapshot["active"]}
    return {
        **snapshot,
        "by_id": active_by_id,
        "order": list(snapshot["active"]),
        "active_items": [_json_clone(active_by_id[action_id]) for action_id in snapshot["active"]],
    }


def _principal_can_read(action: Mapping[str, Any], principal: Mapping[str, Any] | None) -> bool:
    if principal is None:
        return True
    actor_kind = _text(principal.get("kind"))
    actor_id = _text(principal.get("id"))
    application_id = _text(principal.get("application_id"))
    scope = _mapping(action.get("owner_scope"))
    scoped_application = _text(scope.get("application_id"))
    if scoped_application and scoped_application != application_id:
        return False
    scoped_user = _text(scope.get("user_id")) or (
        _text(scope.get("id")) if _text(scope.get("type")) == "user" else ""
    )
    if scoped_user and not (actor_kind == "user" and actor_id == scoped_user):
        return False
    return True


def query_pending_actions(
    *,
    webspace_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    statuses: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    field_mask: str = "summary",
    principal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one ACL-filtered, digest-bound page from the legacy projection."""

    page_size = max(1, min(int(limit), 100))
    mask_name = _text(field_mask) or "summary"
    fields = _QUERY_FIELD_MASKS.get(mask_name)
    if fields is None:
        raise ValueError(f"unsupported pending action field mask: {mask_name}")
    status_filter = sorted({_text(item) for item in statuses or () if _text(item)})
    kind_filter = sorted({_text(item) for item in kinds or () if _text(item)})
    query = {
        "webspace_id": _resolve_webspace_id(webspace_id),
        "statuses": status_filter,
        "kinds": kind_filter,
    }
    snapshot = list_pending_actions(webspace_id=query["webspace_id"], include_terminal=True)
    ordered = [
        snapshot["by_id"][action_id]
        for action_id in reversed(snapshot["order"])
        if action_id in snapshot["by_id"]
    ]
    selected = [
        item
        for item in ordered
        if (not status_filter or _text(item.get("status")) in status_filter)
        and (not kind_filter or _text(item.get("kind")) in kind_filter)
        and _principal_can_read(item, principal)
    ]
    digest = _content_digest(selected)
    offset = decode_opaque_cursor(
        cursor,
        namespace="pending_actions.query.v1",
        query=query,
        field_mask={"name": mask_name, "fields": fields},
        content_digest=digest,
    )
    page = selected[offset : offset + page_size]
    next_offset = offset + len(page)
    next_cursor = (
        encode_opaque_cursor(
            namespace="pending_actions.query.v1",
            offset=next_offset,
            query=query,
            field_mask={"name": mask_name, "fields": fields},
            content_digest=digest,
        )
        if next_offset < len(selected)
        else None
    )
    return {
        "schema": "adaos.pending_action.query_page.v1",
        "items": [
            {field: _json_clone(item[field]) for field in fields if field in item}
            for item in page
        ],
        "next_cursor": next_cursor,
        "limit": page_size,
        "field_mask": mask_name,
        "content_digest": digest,
        "has_more": next_cursor is not None,
    }


async def list_pending_actions_async(
    *,
    webspace_id: str | None = None,
    include_terminal: bool = True,
) -> dict[str, Any]:
    ws = _resolve_webspace_id(webspace_id)
    async with async_get_ydoc(ws, read_only=True, load_mark_roots=["data"], governed=True) as ydoc:
        _, projection = _read_projection(ydoc)
        snapshot = _build_projection(projection, updated_at=_now_ts())
    if include_terminal:
        return snapshot
    active_by_id = {action_id: snapshot["by_id"][action_id] for action_id in snapshot["active"]}
    return {
        **snapshot,
        "by_id": active_by_id,
        "order": list(snapshot["active"]),
        "active_items": [_json_clone(active_by_id[action_id]) for action_id in snapshot["active"]],
    }


def _expire_in_doc(ydoc: Any, *, now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data_map, projection = _read_projection(ydoc)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    expired: list[dict[str, Any]] = []
    for action_id, action in list(by_id.items()):
        status = _text(action.get("status")) or "pending"
        if status not in _ACTIVE_STATUSES:
            continue
        expires_at = action.get("expires_at")
        if expires_at is None:
            continue
        try:
            is_expired = float(expires_at) <= now
        except Exception:
            is_expired = False
        if is_expired:
            by_id[action_id] = _mark_expired(action, now=now)
            expired.append(_json_clone(by_id[action_id]))
    snapshot = _build_projection(projection, updated_at=now)
    if expired:
        _write_projection(data_map, ydoc, snapshot)
    return expired, snapshot


def _cancel_in_doc(
    ydoc: Any,
    *,
    action_id: str,
    reason: str,
    actor: Mapping[str, Any] | None,
    ctx: AgentContext | None,
    now: float,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    data_map, projection = _read_projection(ydoc)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    action = by_id.get(action_id)
    if not isinstance(action, dict):
        return {"id": action_id, "status": "cancelled", "stale": True}, _build_projection(projection, updated_at=now), True
    if _text(action.get("status")) in _TERMINAL_STATUSES:
        return _json_clone(action), _build_projection(projection, updated_at=now), True
    action["status"] = "cancelled"
    action["updated_at"] = now
    action["finished_at"] = now
    action["cancellation"] = {
        "reason": _text(reason)[:1000] or "superseded",
        "actor": _normalize_actor(actor or {}, ctx=ctx, default_type="system"),
        "cancelled_at": now,
    }
    _append_history(
        action,
        {
            "kind": "cancelled",
            "reason": action["cancellation"]["reason"],
            "ts": now,
        },
    )
    by_id[action_id] = action
    snapshot = _build_projection(projection, updated_at=now)
    _write_projection(data_map, ydoc, snapshot)
    return _json_clone(action), snapshot, False


def cancel_pending_action(
    action_id: str,
    *,
    reason: str,
    ctx: AgentContext | None = None,
    webspace_id: str | None = None,
    actor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Terminally close an obsolete action without forging a user response."""

    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    token = _text(action_id)
    if not token:
        raise ValueError("action_id is required")
    with _LOCK:
        with _pending_actions_sync_write_meta():
            with get_ydoc(ws, load_mark_roots=["data"], governed=True) as ydoc:
                action, snapshot, duplicate = _cancel_in_doc(
                    ydoc,
                    action_id=token,
                    reason=reason,
                    actor=actor,
                    ctx=ctx,
                    now=_now_ts(),
                )
    if not duplicate:
        _emit(ctx, "pending_actions.cancelled", {"action": action, "webspace_id": ws})
        _emit(ctx, "pending_actions.changed", {"webspace_id": ws, "pending_actions": snapshot})
    return {"action": action, "snapshot": snapshot, "duplicate": duplicate}


def expire_pending_actions(*, ctx: AgentContext | None = None, webspace_id: str | None = None) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    with _LOCK:
        with _pending_actions_sync_write_meta():
            with get_ydoc(ws, load_mark_roots=["data"], governed=True) as ydoc:
                expired, snapshot = _expire_in_doc(ydoc, now=_now_ts())
    for action in expired:
        _emit(ctx, "pending_actions.expired", {"action": action, "webspace_id": ws})
    if expired:
        _emit(ctx, "pending_actions.changed", {"webspace_id": ws, "pending_actions": snapshot})
    return {"expired": expired, "snapshot": snapshot}


async def expire_pending_actions_async(
    *,
    ctx: AgentContext | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws = _resolve_webspace_id(webspace_id)
    with _LOCK:
        async with _pending_actions_async_write_meta():
            async with async_get_ydoc(
                ws,
                load_mark_roots=["data"],
                governed=True,
                write_source="pending_actions.core",
                write_owner="core:pending_actions",
                write_channel="core.pending_actions.async",
            ) as ydoc:
                expired, snapshot = _expire_in_doc(ydoc, now=_now_ts())
    for action in expired:
        _emit(ctx, "pending_actions.expired", {"action": action, "webspace_id": ws})
    if expired:
        _emit(ctx, "pending_actions.changed", {"webspace_id": ws, "pending_actions": snapshot})
    return {"expired": expired, "snapshot": snapshot}


# There are deliberately no generic event-bus subscribers for publish,
# respond, or expire requests. The local bus has no authenticated caller
# principal, so accepting these control operations there would let any skill
# or browser-forwarded event forge producer/responder authority.
