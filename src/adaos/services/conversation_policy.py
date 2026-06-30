from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.services import conversation_store


POLICY_INSPECTION_SCHEMA = "adaos.conversation.policy_inspection.v1"


def inspect_turn_policy(
    *,
    turn_trace_id: str | None = None,
    webspace_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    trace = None
    trace_id = str(turn_trace_id or "").strip()
    if trace_id:
        trace = conversation_store.get_turn_trace(trace_id)
    else:
        ws = str(webspace_id or "").strip()
        if not ws:
            raise ValueError("webspace_id is required when turn_trace_id is not provided")
        trace = conversation_store.latest_turn_trace(webspace_id=ws, conversation_id=str(conversation_id or "").strip() or None)
    if not trace:
        return {
            "schema": POLICY_INSPECTION_SCHEMA,
            "ok": False,
            "status": "not_found",
            "turn_trace_id": trace_id or None,
            "webspace_id": str(webspace_id or "").strip() or None,
            "conversation_id": str(conversation_id or "").strip() or None,
        }
    policy = trace.get("policy_decision") if isinstance(trace.get("policy_decision"), Mapping) else {}
    renderer = trace.get("renderer") if isinstance(trace.get("renderer"), Mapping) else {}
    selected_channel = _first_text(trace.get("channel_id"), policy.get("selected_channel"), policy.get("dialog_channel_id"))
    selected_agent = _first_text(trace.get("agent_id"), policy.get("selected_agent_id"), policy.get("selected_agent"))
    selected_tool = _first_text(trace.get("selected_tool"), policy.get("selected_tool"), policy.get("tool"))
    return {
        "schema": POLICY_INSPECTION_SCHEMA,
        "ok": True,
        "status": str(trace.get("status") or "unknown"),
        "turn_trace_id": str(trace.get("turn_trace_id") or ""),
        "conversation_id": str(trace.get("conversation_id") or "") or None,
        "message_id": str(trace.get("message_id") or "") or None,
        "webspace_id": str(trace.get("webspace_id") or "") or None,
        "selected": {
            "channel_id": selected_channel or None,
            "agent_id": selected_agent or None,
            "tool": selected_tool or None,
            "renderer": _renderer_summary(renderer),
        },
        "explanation": {
            "reasons": _policy_reasons(policy),
            "fallback": _first_text(policy.get("fallback"), policy.get("diagnostic"), policy.get("result_status")) or None,
            "repair_state": _first_text(policy.get("dialog_repair_state"), policy.get("repair_state")) or None,
            "materialization_status": _first_text(policy.get("materialization_status"), policy.get("result_status")) or None,
        },
        "policy_decision": dict(policy),
        "renderer": dict(renderer),
        "source_refs": [
            {
                "type": "turn_trace",
                "turn_trace_id": str(trace.get("turn_trace_id") or ""),
                "conversation_id": str(trace.get("conversation_id") or "") or None,
            }
        ],
    }


def inspect_last_turn_policy(*, webspace_id: str, conversation_id: str | None = None) -> dict[str, Any]:
    return inspect_turn_policy(webspace_id=webspace_id, conversation_id=conversation_id)


def _first_text(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _policy_reasons(policy: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ("reason", "routing_reason", "dialog_policy_reason", "diagnostic"):
        token = str(policy.get(key) or "").strip()
        if token and token not in reasons:
            reasons.append(token)
    return reasons


def _renderer_summary(renderer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "receiver": renderer.get("receiver"),
            "projection": renderer.get("projection"),
            "surface": renderer.get("surface"),
            "response_route": renderer.get("response_route"),
        }.items()
        if value not in (None, "")
    }
