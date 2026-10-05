"""One fail-closed switch shared by discovery, routing and execution."""
from __future__ import annotations

import os
from typing import Any, Mapping

ENABLE_FLAG = "ADAOS_COMPANION_SAGE_ENABLED"
AGENT_ID = "agent:conversation_companions:sage"
EXPERIMENTAL_NLU = frozenset({"neuro_nlu_lite_skill", "neural_nlu_service_skill"})


def enabled() -> bool:
    return os.environ.get(ENABLE_FLAG, "").strip().lower() == "true"


def require_enabled() -> None:
    if not enabled():
        raise PermissionError("companion_disabled: set ADAOS_COMPANION_SAGE_ENABLED=true")


def agent_available(record: Mapping[str, Any]) -> bool:
    return str(record.get("id") or record.get("agent_id") or "") != AGENT_ID or enabled()


def guard_skill_call(skill: str, tool: str, payload: Mapping[str, Any]) -> None:
    if skill in EXPERIMENTAL_NLU:
        raise PermissionError("experimental_nlu_removed: supported engines are regexp and Rasa")
    meta = payload.get("_meta") or {}
    is_sage = skill == "conversation_companions" and (
        payload.get("character_id") == "sage"
        or meta.get("active_agent_id") == AGENT_ID
        or meta.get("agent_id") == AGENT_ID
        or tool in {"get_companion_context", "list_companion_activity", "execute_companion_action",
                    "capture_capability_request", "learning_session", "learning_feedback", "learning_review"}
    )
    if is_sage:
        require_enabled()
    if meta.get("companion_session_id") and (
        skill in {"builder_skill", "nlu_teacher_skill", "rasa_nlu_service_skill"}
        and tool not in {"parse", "get_status", "get_diagnostics", "list_models"}
    ):
        raise PermissionError("companion_session_frozen: authoring runs after session seal")
