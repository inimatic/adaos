"""Core contracts for submitting and observing Builder Prototype requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from adaos.services.builder_intent import capture_intent, compile_prototype_brief


class PrototypeExecutionPort(Protocol):
    """Execution boundary below the public Builder SDK."""

    adapter_id: str

    def submit_turn(
        self, payload: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]: ...

    def get_session(
        self,
        session_id: str,
        *,
        webspace_id: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


def submit_request(
    port: PrototypeExecutionPort,
    statement: str,
    *,
    webspace_id: str,
    locale: str,
    conversation_context: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source_kind: Literal["chat", "api", "e2e", "unknown"] = "api",
    auto_apply: bool = True,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Admit one user turn before delegating execution through a named port."""

    text = str(statement or "").strip()
    if not text:
        raise ValueError("Builder Prototype request statement is required")
    target_webspace = str(webspace_id or "").strip()
    if not target_webspace:
        raise ValueError("Builder Prototype request webspace_id is required")
    language = str(locale or "").strip().lower() or "en"
    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    intent = capture_intent(
        text,
        locale=language,
        source={
            "kind": source_kind,
            "message_id": str(meta.get("message_id") or "").strip() or None,
        },
    )
    brief = compile_prototype_brief(intent)
    payload = {
        "text": text,
        "webspace_id": target_webspace,
        "auto_apply": bool(auto_apply),
        "conversation_context": dict(conversation_context or {}),
        "_meta": {
            **meta,
            "webspace_id": target_webspace,
            "source_webspace_id": target_webspace,
            "locale": language,
            "prototype_intent_id": intent["intent_id"],
            "prototype_brief_digest": brief["digest"],
        },
    }
    execution = port.submit_turn(payload, timeout_seconds=max(0.1, timeout_seconds))
    if not isinstance(execution, Mapping):
        raise RuntimeError("Builder Prototype execution port returned a non-object")
    return {
        **dict(execution),
        "sdk": {
            "schema": "adaos.builder.prototype_request_receipt.v1",
            "intent": intent,
            "brief": brief,
            "execution_adapter": str(port.adapter_id),
        },
    }


def candidate_status(
    port: PrototypeExecutionPort,
    session_id: str,
    *,
    webspace_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Read one candidate session without exposing backend tool addressing."""

    token = str(session_id or "").strip()
    if not token:
        raise ValueError("Builder candidate session_id is required")
    target_webspace = str(webspace_id or "").strip()
    if not target_webspace:
        raise ValueError("Builder candidate webspace_id is required")
    execution = port.get_session(
        token,
        webspace_id=target_webspace,
        timeout_seconds=max(0.1, timeout_seconds),
    )
    if not isinstance(execution, Mapping):
        raise RuntimeError("Builder Prototype execution port returned a non-object")
    return {
        **dict(execution),
        "sdk": {
            "schema": "adaos.builder.prototype_candidate_status.v1",
            "session_id": token,
            "execution_adapter": str(port.adapter_id),
        },
    }


__all__ = [
    "PrototypeExecutionPort",
    "candidate_status",
    "submit_request",
]
