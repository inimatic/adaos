from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services import conversation_store


REQUEST_SCHEMA = "adaos.conversation_federated_retrieval.request.v1"
RESPONSE_SCHEMA = "adaos.conversation_federated_retrieval.response.v1"


def _text(value: Any, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _deadline(timeout_ms: int) -> float:
    return time.monotonic() + (max(0, timeout_ms) / 1000.0)


def _expired(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _clip(text: Any, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "…"


def _score(query: str, text: str) -> float:
    q = query.casefold().strip()
    t = text.casefold()
    if not q:
        return 0.25
    if q == t:
        return 1.0
    if q in t:
        return 0.85
    terms = [part for part in q.split() if part]
    if not terms:
        return 0.25
    hits = sum(1 for term in terms if term in t)
    return round(max(0.05, hits / len(terms) * 0.7), 3)


def normalize_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _mapping(payload)
    requester = _mapping(raw.get("requester"))
    requester_owner = _text(requester.get("owner") or raw.get("requester_owner"))
    if not requester_owner:
        raise ValueError("requester.owner is required")
    scopes = _mapping(raw.get("scopes"))
    limits = _mapping(raw.get("limits"))
    policy = _mapping(raw.get("policy"))
    owner_scope = [_text(item) for item in _list(scopes.get("owners")) if _text(item)]
    if not owner_scope:
        owner_scope = [requester_owner]
    request_id = _text(raw.get("request_id")) or f"fedreq.{uuid.uuid4().hex[:12]}"
    timeout_ms = _bounded_int(limits.get("timeout_ms"), default=250, minimum=0, maximum=5_000)
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": request_id,
        "requester": {
            "owner": requester_owner,
            "actor_id": _text(requester.get("actor_id")),
            "node_id": _text(requester.get("node_id")),
        },
        "query": _text(raw.get("query")),
        "target_nodes": [_text(item) for item in _list(raw.get("target_nodes")) if _text(item)],
        "scopes": {
            "webspace_id": _text(scopes.get("webspace_id"), "default"),
            "owners": owner_scope,
            "memory_scopes": [_text(item) for item in _list(scopes.get("memory_scopes")) if _text(item)] or ["skill_user"],
            "conversation_ids": [_text(item) for item in _list(scopes.get("conversation_ids")) if _text(item)],
        },
        "limits": {
            "timeout_ms": timeout_ms,
            "per_node_timeout_ms": _bounded_int(limits.get("per_node_timeout_ms"), default=timeout_ms, minimum=0, maximum=5_000),
            "max_fragments": _bounded_int(limits.get("max_fragments"), default=8, minimum=1, maximum=50),
            "max_fragment_chars": _bounded_int(limits.get("max_fragment_chars"), default=600, minimum=80, maximum=2_000),
        },
        "policy": {
            "allow_cross_owner": bool(policy.get("allow_cross_owner")),
            "allow_remote": bool(policy.get("allow_remote")),
            "consent_required": policy.get("consent_required", True) is not False,
            "return_raw_database_rows": False,
        },
    }


def _allowed_owners(request: Mapping[str, Any]) -> tuple[set[str], list[dict[str, Any]]]:
    requester_owner = _text(_mapping(request.get("requester")).get("owner"))
    requested = {_text(item) for item in _list(_mapping(request.get("scopes")).get("owners")) if _text(item)}
    if not requested:
        requested = {requester_owner}
    if _mapping(request.get("policy")).get("allow_cross_owner"):
        return requested, []
    allowed = {requester_owner}
    denied = sorted(owner for owner in requested if owner and owner not in allowed)
    return allowed, [
        {
            "owner": owner,
            "reason": "cross_owner_denied",
            "policy": "allow_cross_owner=false",
        }
        for owner in denied
    ]


def _memory_fragments(request: Mapping[str, Any], *, deadline: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    query = _text(request.get("query"))
    scopes = _mapping(request.get("scopes"))
    limits = _mapping(request.get("limits"))
    allowed_owners, denials = _allowed_owners(request)
    fragments: list[dict[str, Any]] = []
    partial = False
    for owner in sorted(allowed_owners):
        for scope in _list(scopes.get("memory_scopes")):
            if _expired(deadline):
                partial = True
                denials.append({"owner": owner, "scope": scope, "reason": "timeout"})
                return fragments, denials, partial
            rows = conversation_store.search_memory(
                query,
                scope=_text(scope),
                owner=owner,
                limit=int(limits.get("max_fragments") or 8),
            )
            for row in rows:
                text = _text(row.get("text") or row.get("key"))
                fragments.append(
                    {
                        "kind": "memory",
                        "text": _clip(text, int(limits.get("max_fragment_chars") or 600)),
                        "summary": _clip(text, 180),
                        "score": _score(query, text),
                        "source_ref": {
                            "type": "memory",
                            "memory_id": row.get("memory_id") or row.get("id"),
                            "scope": row.get("scope"),
                            "owner": row.get("owner"),
                            "subject_id": row.get("subject_id"),
                        },
                        "visibility": "fragment",
                    }
                )
    return fragments, denials, partial


def _conversation_fragments(request: Mapping[str, Any], *, deadline: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    query = _text(request.get("query"))
    scopes = _mapping(request.get("scopes"))
    limits = _mapping(request.get("limits"))
    fragments: list[dict[str, Any]] = []
    denials: list[dict[str, Any]] = []
    partial = False
    if not query:
        return fragments, denials, partial
    for conversation_id in _list(scopes.get("conversation_ids")):
        cid = _text(conversation_id)
        if not cid:
            continue
        if _expired(deadline):
            partial = True
            denials.append({"conversation_id": cid, "reason": "timeout"})
            break
        for message in conversation_store.list_messages(cid, limit=200, ascending=False):
            text = _text(message.get("text"))
            score = _score(query, text)
            if score < 0.2:
                continue
            fragments.append(
                {
                    "kind": "conversation_message",
                    "text": _clip(text, int(limits.get("max_fragment_chars") or 600)),
                    "summary": _clip(text, 180),
                    "score": score,
                    "source_ref": {
                        "type": "conversation_message",
                        "conversation_id": cid,
                        "message_id": message.get("id"),
                        "seq": message.get("seq"),
                        "thread_id": message.get("thread_id"),
                        "owner": message.get("owner"),
                    },
                    "visibility": "fragment",
                }
            )
            if len(fragments) >= int(limits.get("max_fragments") or 8):
                return fragments, denials, partial
    return fragments, denials, partial


def execute_local_request(payload: Mapping[str, Any] | None, *, node_id: str = "local") -> dict[str, Any]:
    started = time.monotonic()
    try:
        request = normalize_request(payload)
    except ValueError as exc:
        return {
            "schema": RESPONSE_SCHEMA,
            "request_id": _text((_mapping(payload)).get("request_id")),
            "node_id": node_id,
            "status": "denied",
            "partial": False,
            "fragments": [],
            "denials": [{"reason": "invalid_request", "detail": str(exc)}],
            "diagnostics": {"elapsed_ms": 0},
        }
    deadline = _deadline(int(_mapping(request.get("limits")).get("per_node_timeout_ms") or 0))
    fragments: list[dict[str, Any]] = []
    denials: list[dict[str, Any]] = []
    partial = False
    if _expired(deadline):
        partial = True
        denials.append({"reason": "timeout_before_retrieval"})
    else:
        memory, memory_denials, memory_partial = _memory_fragments(request, deadline=deadline)
        conv, conv_denials, conv_partial = _conversation_fragments(request, deadline=deadline)
        fragments.extend(memory)
        fragments.extend(conv)
        denials.extend(memory_denials)
        denials.extend(conv_denials)
        partial = memory_partial or conv_partial
    max_fragments = int(_mapping(request.get("limits")).get("max_fragments") or 8)
    fragments = sorted(fragments, key=lambda item: float(item.get("score") or 0.0), reverse=True)[:max_fragments]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    status = "partial" if partial else "ok"
    if not fragments and denials and all(item.get("reason") in {"cross_owner_denied", "invalid_request"} for item in denials):
        status = "denied"
    audit = _record_retrieval_audit(
        request=request,
        node_id=node_id,
        status=status,
        fragments=fragments,
        denials=denials,
        elapsed_ms=elapsed_ms,
    )
    diagnostics = {
        "elapsed_ms": elapsed_ms,
        "timeout_ms": _mapping(request.get("limits")).get("per_node_timeout_ms"),
        "fragment_count": len(fragments),
        "remote_sql": False,
    }
    if audit:
        diagnostics["audit_event_id"] = audit.get("audit_event_id")
    return {
        "schema": RESPONSE_SCHEMA,
        "request_id": request["request_id"],
        "node_id": node_id,
        "status": status,
        "partial": bool(partial),
        "fragments": fragments,
        "denials": denials,
        "diagnostics": diagnostics,
    }


def _record_retrieval_audit(
    *,
    request: Mapping[str, Any],
    node_id: str,
    status: str,
    fragments: Sequence[Mapping[str, Any]],
    denials: Sequence[Mapping[str, Any]],
    elapsed_ms: int,
) -> dict[str, Any] | None:
    requester = _mapping(request.get("requester"))
    scopes = _mapping(request.get("scopes"))
    conversation_ids = [_text(item) for item in _list(scopes.get("conversation_ids")) if _text(item)]
    target_nodes = [_text(item) for item in _list(request.get("target_nodes")) if _text(item)]
    source_ref_counts: dict[str, int] = {}
    for fragment in fragments:
        ref = _mapping(fragment.get("source_ref"))
        kind = _text(ref.get("type") or fragment.get("kind"), "unknown")
        source_ref_counts[kind] = source_ref_counts.get(kind, 0) + 1
    try:
        return conversation_store.append_audit_event(
            event_type="conversation.federated_retrieval.audit.v1",
            action="execute_local_retrieval",
            conversation_id=conversation_ids[0] if len(conversation_ids) == 1 else None,
            status=status,
            actor_owner=_text(requester.get("owner")),
            actor_id=_text(requester.get("actor_id")),
            reason=status,
            counts={
                "returned": len(fragments),
                "denied": len(denials),
                "target_nodes": len(target_nodes) or 1,
                "owners": len(_list(scopes.get("owners"))),
                "conversation_ids": len(conversation_ids),
            },
            meta={
                "schema": "adaos.conversation_federated_retrieval.audit_meta.v1",
                "request_id": _text(request.get("request_id")),
                "node_id": node_id,
                "requester_node_id": _text(requester.get("node_id")),
                "target_nodes": target_nodes or [node_id],
                "owner_scope": [_text(item) for item in _list(scopes.get("owners")) if _text(item)],
                "memory_scopes": [_text(item) for item in _list(scopes.get("memory_scopes")) if _text(item)],
                "conversation_ids": conversation_ids,
                "denial_reasons": sorted({_text(item.get("reason")) for item in denials if isinstance(item, Mapping) and _text(item.get("reason"))}),
                "source_ref_counts": source_ref_counts,
                "elapsed_ms": elapsed_ms,
                "remote_sql": False,
            },
        )
    except Exception:
        return None
