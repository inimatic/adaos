from __future__ import annotations

from typing import Any, Mapping, Sequence

from adaos.services import voice_runtime as _service


def get_listening_policy() -> dict[str, Any]:
    return _service.read_voice_policy()


def set_listening_mode(mode: str, *, source: str = "sdk") -> dict[str, Any]:
    return _service.set_voice_policy(listening_mode=mode, source=source)


def listening_service() -> dict[str, Any]:
    return _service.listening_service_projection()


def advance_long_form(
    state: Mapping[str, Any] | None,
    *,
    text: str,
    intent_name: str,
    active_agent_id: str = "",
    addressed_agent_id: str = "",
) -> dict[str, Any]:
    return _service.advance_long_form_session(
        state,
        text=text,
        intent_name=intent_name,
        active_agent_id=active_agent_id,
        addressed_agent_id=addressed_agent_id,
    )


def arbitrate_room_activation(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _service.choose_activation_candidate(candidates)


__all__ = [
    "advance_long_form",
    "arbitrate_room_activation",
    "get_listening_policy",
    "listening_service",
    "set_listening_mode",
]
