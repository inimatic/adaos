from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.domain import Event
from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.yjs.doc import async_get_ydoc, get_ydoc
from adaos.services.yjs.store import ystore_write_metadata, ystore_write_metadata_sync
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.pending_actions")
_LOCK = threading.RLock()
_ACTIVE_STATUSES = {"pending", "postponed"}
_TERMINAL_STATUSES = {"responded", "expired", "cancelled"}
_NO_VALUE = object()

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
            0,
        )
        action_id = order.pop(drop_index)
        by_id.pop(action_id, None)


def _build_projection(projection: dict[str, Any], *, updated_at: float) -> dict[str, Any]:
    _prune_projection(projection)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    order: list[str] = [action_id for action_id in projection["order"] if action_id in by_id]
    projection["order"] = order
    active_ids = [
        action_id
        for action_id in order
        if _text(by_id.get(action_id, {}).get("status")) in _ACTIVE_STATUSES
    ]
    return {
        "schema_version": 1,
        "by_id": _json_clone(by_id),
        "order": list(order),
        "active": list(active_ids),
        "active_count": len(active_ids),
        "active_items": [_json_clone(by_id[action_id]) for action_id in active_ids],
        "updated_at": updated_at,
    }


def _read_projection(ydoc: Any) -> tuple[Any, dict[str, Any]]:
    data_map = ydoc.get_map("data")
    return data_map, _normalize_projection(data_map.get("pending_actions"))


def _write_projection(data_map: Any, ydoc: Any, snapshot: dict[str, Any]) -> None:
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
        "allowed_actions": _normalize_allowed_actions(selected_actions),
        "default_text_binding": bool(default_text_binding),
        "response_route": route,
        "webspace_id": webspace_id,
        "history": [],
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


def _add_action_to_doc(ydoc: Any, action: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    data_map, projection = _read_projection(ydoc)
    by_id: dict[str, dict[str, Any]] = projection["by_id"]
    order: list[str] = projection["order"]
    action_id = _text(action.get("id"))
    if action_id in by_id:
        raise ValueError(f"pending action already exists: {action_id}")
    by_id[action_id] = _json_clone(action)
    order.append(action_id)
    snapshot = _build_projection(projection, updated_at=_now_ts())
    _write_projection(data_map, ydoc, snapshot)
    return _json_clone(action), snapshot


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
    with _LOCK:
        with _pending_actions_sync_write_meta():
            with get_ydoc(ws, load_mark_roots=["data"], governed=True) as ydoc:
                stored_action, snapshot = _add_action_to_doc(ydoc, action)
    for topic, payload in _event_sequence_for_publish(stored_action, snapshot):
        _emit(ctx, topic, payload)
    return stored_action


async def publish_pending_action_async(**kwargs: Any) -> dict[str, Any]:
    ctx = kwargs.pop("ctx", None) or get_ctx()
    ws = _resolve_webspace_id(kwargs.pop("webspace_id", None))
    action = _normalize_pending_action(ctx=ctx, webspace_id=ws, **kwargs)
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
        raise KeyError(f"pending action not found: {action_id}")
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


@subscribe("pending_actions.publish.request")
async def _on_pending_action_publish(evt: Any) -> None:
    payload = _event_payload(evt)
    payload.pop("_meta", None)
    try:
        await publish_pending_action_async(**payload)
    except Exception:
        _log.warning("failed to publish pending action from event", exc_info=True)


@subscribe("pending_actions.respond.request")
async def _on_pending_action_respond(evt: Any) -> None:
    payload = _event_payload(evt)
    payload.pop("_meta", None)
    action_id = _text(payload.pop("action_id", payload.pop("pending_action_id", "")))
    response_action_id = _text(payload.pop("response_action_id", payload.pop("action", "")))
    try:
        await respond_pending_action_async(action_id, response_action_id, **payload)
    except Exception:
        _log.warning("failed to respond to pending action from event", exc_info=True)


@subscribe("pending_actions.expire.request")
async def _on_pending_action_expire(evt: Any) -> None:
    payload = _event_payload(evt)
    payload.pop("_meta", None)
    try:
        await expire_pending_actions_async(webspace_id=payload.get("webspace_id"))
    except Exception:
        _log.warning("failed to expire pending actions from event", exc_info=True)
