from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Literal, Mapping

from adaos.services import conversation_safety, conversation_store


MemoryWriteKind = Literal["conversation_fact", "skill_preference", "agent_preference", "global_user"]


@dataclass(frozen=True, slots=True)
class ContextBudgets:
    max_tokens: int = 4_000
    max_messages: int = 20
    max_memory_items: int = 12
    timeout_ms: int = 250

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContextBudgets":
        data = dict(value or {})
        return cls(
            max_tokens=_positive_int(data.get("max_tokens"), 4_000, minimum=128, maximum=64_000),
            max_messages=_positive_int(data.get("max_messages"), 20, minimum=0, maximum=200),
            max_memory_items=_positive_int(data.get("max_memory_items"), 12, minimum=0, maximum=100),
            timeout_ms=_positive_int(data.get("timeout_ms"), 250, minimum=1, maximum=10_000),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_tokens": self.max_tokens,
            "max_messages": self.max_messages,
            "max_memory_items": self.max_memory_items,
            "timeout_ms": self.timeout_ms,
        }


def estimate_tokens(text: Any) -> int:
    """Stable local token estimate used when no model tokenizer is available."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return 0
    return max(1, int(math.ceil(len(cleaned) / 4)))


def build_context_packet(
    *,
    conversation_id: str,
    requester_owner: str,
    channel_id: str | None = None,
    thread_id: str | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    memory_owner: str | None = None,
    include_global_user: bool = True,
    allow_cross_owner_memory: bool = False,
    budgets: Mapping[str, Any] | ContextBudgets | None = None,
) -> dict[str, Any]:
    """Build a deterministic, budgeted packet for LLM/runtime consumption.

    The first implementation intentionally uses recent ledger messages and
    owner-scoped memory only. It records unavailable retrieval sources as
    deterministic fallbacks so callers can inspect why no segment/FTS/vector
    evidence is present.
    """
    started = time.monotonic()
    cid = str(conversation_id or "").strip()
    owner = str(requester_owner or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    if not owner:
        raise ValueError("requester_owner is required")
    limits = budgets if isinstance(budgets, ContextBudgets) else ContextBudgets.from_mapping(budgets)
    clean_thread_id = str(thread_id or "").strip()
    clean_topic_ref = dict(topic_ref or {}) if isinstance(topic_ref, Mapping) else {}
    if not clean_thread_id:
        clean_thread_id = str(clean_topic_ref.get("thread_id") or "").strip()
    search_index = conversation_store.search_index_health()
    fts_available = bool(search_index.get("fts_available"))
    fallbacks = [
        "summaries_unavailable",
        "semantic_retrieval_unavailable",
    ]
    if not fts_available:
        fallbacks.insert(0, "fts_unavailable")
    diagnostics: dict[str, Any] = {
        "schema": "adaos.context.diagnostics.v1",
        "selected_sources": [],
        "skipped_sources": [],
        "policy_denials": [],
        "fallbacks": fallbacks,
        "search_index": search_index,
        "safety_flags": [],
        "budget_exhausted": False,
    }
    packet: dict[str, Any] = {
        "schema": "adaos.context.packet.v1",
        "conversation_id": cid,
        "requester_owner": owner,
        "channel_id": str(channel_id or "").strip() or None,
        "thread_id": clean_thread_id or None,
        "topic": clean_topic_ref or None,
        "agent_id": str(agent_id or "").strip() or None,
        "budgets": limits.to_dict(),
        "messages": [],
        "segments": [],
        "memory": [],
        "evidence_refs": [],
        "token_estimate": 0,
        "diagnostics": diagnostics,
    }
    if clean_thread_id:
        diagnostics["thread_filter"] = clean_thread_id
    if clean_topic_ref:
        topic_id = str(clean_topic_ref.get("topic_id") or clean_topic_ref.get("id") or "").strip()
        if topic_id:
            packet["topic_id"] = topic_id
    remaining_tokens = limits.max_tokens

    messages = _select_recent_messages(cid, limits.max_messages, thread_id=clean_thread_id or None)
    selected_messages: list[dict[str, Any]] = []
    for message in reversed(messages):
        if _timed_out(started, limits.timeout_ms):
            diagnostics["budget_exhausted"] = True
            diagnostics["skipped_sources"].append({"type": "message", "reason": "timeout_budget"})
            break
        text = str(message.get("text") or "")
        cost = estimate_tokens(text)
        if cost > remaining_tokens:
            diagnostics["budget_exhausted"] = True
            diagnostics["skipped_sources"].append(_source_ref("message", message, reason="token_budget"))
            continue
        remaining_tokens -= cost
        item = _context_message(message, cost)
        _attach_safety(item, diagnostics)
        selected_messages.append(item)
        diagnostics["selected_sources"].append(item["source_ref"])
        packet["evidence_refs"].append(item["source_ref"])
    selected_messages.reverse()
    packet["messages"] = selected_messages

    memory_candidate_owner = str(memory_owner or owner).strip() or owner
    memory_sources = _memory_queries(
        requester_owner=owner,
        memory_owner=memory_candidate_owner,
        conversation_id=cid,
        agent_id=agent_id,
        include_global_user=include_global_user,
        allow_cross_owner_memory=allow_cross_owner_memory,
        diagnostics=diagnostics,
    )
    selected_memory: list[dict[str, Any]] = []
    selected_memory_ids: set[str] = set()
    for query in memory_sources:
        if len(selected_memory) >= limits.max_memory_items:
            diagnostics["budget_exhausted"] = True
            break
        if _timed_out(started, limits.timeout_ms):
            diagnostics["budget_exhausted"] = True
            diagnostics["skipped_sources"].append({"type": "memory", "reason": "timeout_budget", **query})
            break
        rows = conversation_store.list_memory(
            scope=query["scope"],
            owner=query["owner"],
            subject_id=query.get("subject_id"),
            limit=max(1, limits.max_memory_items - len(selected_memory)),
        )
        for row in rows:
            memory_id = str(row.get("id") or "")
            if memory_id and memory_id in selected_memory_ids:
                continue
            if len(selected_memory) >= limits.max_memory_items:
                diagnostics["budget_exhausted"] = True
                break
            text = str(row.get("text") or row.get("key") or "")
            cost = estimate_tokens(text)
            if cost > remaining_tokens:
                diagnostics["budget_exhausted"] = True
                diagnostics["skipped_sources"].append(_memory_ref(row, reason="token_budget"))
                continue
            remaining_tokens -= cost
            item = _context_memory(row, cost)
            _attach_safety(item, diagnostics)
            selected_memory.append(item)
            if memory_id:
                selected_memory_ids.add(memory_id)
            diagnostics["selected_sources"].append(item["source_ref"])
            packet["evidence_refs"].append(item["source_ref"])
    packet["memory"] = selected_memory
    packet["token_estimate"] = limits.max_tokens - remaining_tokens
    diagnostics["latency_ms"] = int(round((time.monotonic() - started) * 1000))
    diagnostics["selected_message_count"] = len(selected_messages)
    diagnostics["selected_memory_count"] = len(selected_memory)
    return packet


def memory_write_policy(
    kind: MemoryWriteKind,
    *,
    owner: str,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    consent_state: str | None = None,
    visibility: str | None = None,
) -> dict[str, Any]:
    clean_owner = str(owner or "").strip()
    if not clean_owner:
        raise ValueError("owner is required")
    clean_kind = str(kind or "").strip()
    if clean_kind == "conversation_fact":
        subject_id = str(conversation_id or "").strip()
        if not subject_id:
            raise ValueError("conversation_id is required for conversation_fact")
        return {
            "kind": clean_kind,
            "scope": "conversation",
            "owner": clean_owner,
            "subject_id": subject_id,
            "consent_state": consent_state or "session",
            "policy": {"visibility": visibility or "conversation", "reuse": "conversation_only"},
        }
    if clean_kind == "skill_preference":
        return {
            "kind": clean_kind,
            "scope": "skill_user",
            "owner": clean_owner,
            "subject_id": clean_owner,
            "consent_state": consent_state or "skill_scoped",
            "policy": {"visibility": visibility or "owner_only", "reuse": "owner_only"},
        }
    if clean_kind == "agent_preference":
        subject_id = str(agent_id or "").strip()
        if not subject_id:
            raise ValueError("agent_id is required for agent_preference")
        return {
            "kind": clean_kind,
            "scope": "agent_user",
            "owner": clean_owner,
            "subject_id": subject_id,
            "consent_state": consent_state or "skill_scoped",
            "policy": {"visibility": visibility or "owner_only", "reuse": "agent_only"},
        }
    if clean_kind == "global_user":
        return {
            "kind": clean_kind,
            "scope": "global_user",
            "owner": clean_owner,
            "subject_id": "user:default",
            "consent_state": consent_state or "global",
            "policy": {"visibility": visibility or "user_visible", "reuse": "cross_owner_with_consent"},
        }
    raise ValueError(f"unknown memory write kind: {kind!r}")


def _positive_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _select_recent_messages(conversation_id: str, limit: int, *, thread_id: str | None = None) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    projection = conversation_store.list_projection(
        conversation_id,
        thread_id=str(thread_id or "").strip() or None,
        limit=limit,
        max_items=max(limit, 1),
    )
    messages = projection.get("messages") if isinstance(projection, dict) else []
    return [dict(item) for item in messages if isinstance(item, Mapping)]


def _context_message(message: Mapping[str, Any], token_estimate: int) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or ""),
        "seq": int(message.get("seq") or 0),
        "role": str(message.get("from") or message.get("role") or ""),
        "text": str(message.get("text") or ""),
        "ts": float(message.get("ts") or 0.0),
        "actor_id": message.get("active_agent_id"),
        "actor_label": message.get("active_agent_label"),
        "token_estimate": token_estimate,
        "trust_boundary": "retrieved_untrusted_evidence",
        "source_ref": _source_ref("message", message),
    }


def _source_ref(kind: str, message: Mapping[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    ref = {
        "type": "conversation_message",
        "kind": kind,
        "conversation_id": str(message.get("conversation_id") or ""),
        "message_id": str(message.get("id") or ""),
        "seq": int(message.get("seq") or 0),
    }
    if reason:
        ref["reason"] = reason
    return ref


def _memory_queries(
    *,
    requester_owner: str,
    memory_owner: str,
    conversation_id: str,
    agent_id: str | None,
    include_global_user: bool,
    allow_cross_owner_memory: bool,
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    owner_allowed = memory_owner == requester_owner or allow_cross_owner_memory
    if not owner_allowed:
        diagnostics["policy_denials"].append(
            {
                "type": "memory",
                "reason": "cross_owner_denied",
                "requester_owner": requester_owner,
                "memory_owner": memory_owner,
            }
        )
        return queries
    if include_global_user:
        queries.append({"scope": "global_user", "owner": memory_owner, "subject_id": "user:default"})
        queries.append({"scope": "global_user", "owner": memory_owner})
    queries.append({"scope": "skill_user", "owner": memory_owner, "subject_id": memory_owner})
    if agent_id:
        queries.append({"scope": "agent_user", "owner": memory_owner, "subject_id": str(agent_id)})
    queries.append({"scope": "conversation", "owner": memory_owner, "subject_id": conversation_id})

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for query in queries:
        key = (str(query["scope"]), str(query["owner"]), query.get("subject_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(query)
    return unique


def _context_memory(row: Mapping[str, Any], token_estimate: int) -> dict[str, Any]:
    source_ref = _memory_ref(row)
    policy = row.get("policy") if isinstance(row.get("policy"), Mapping) else {}
    return {
        "id": str(row.get("id") or ""),
        "scope": str(row.get("scope") or ""),
        "owner": str(row.get("owner") or ""),
        "subject_id": row.get("subject_id"),
        "key": row.get("key"),
        "text": str(row.get("text") or ""),
        "value": dict(row.get("value") or {}) if isinstance(row.get("value"), Mapping) else {},
        "confidence": row.get("confidence"),
        "consent_state": str(row.get("consent_state") or "unknown"),
        "visibility": str(row.get("visibility") or policy.get("visibility") or "owner_only"),
        "source_ref": source_ref,
        "token_estimate": token_estimate,
        "trust_boundary": "retrieved_untrusted_evidence",
    }


def _memory_ref(row: Mapping[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    source = row.get("source_ref") if isinstance(row.get("source_ref"), Mapping) else {}
    ref = {
        "type": "memory_item",
        "memory_id": str(row.get("id") or ""),
        "scope": str(row.get("scope") or ""),
        "owner": str(row.get("owner") or ""),
        "source_ref": dict(source),
    }
    if reason:
        ref["reason"] = reason
    return ref


def _timed_out(started: float, timeout_ms: int) -> bool:
    return (time.monotonic() - started) * 1000 > max(1, timeout_ms)


def _attach_safety(item: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    safety = conversation_safety.inspect_retrieved_text(
        item.get("text"),
        source_ref=item.get("source_ref") if isinstance(item.get("source_ref"), Mapping) else {},
    )
    item["safety"] = safety
    if safety.get("flags"):
        diagnostics.setdefault("safety_flags", []).append(
            {
                "risk_level": safety.get("risk_level"),
                "flags": list(safety.get("flags") or []),
                "source_ref": dict(safety.get("source_ref") or {}),
            }
        )
