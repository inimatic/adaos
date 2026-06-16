from __future__ import annotations

from typing import Any, Mapping

from adaos.services import endpoint_audio as _svc


def build_capture_command(
    endpoint: Mapping[str, Any],
    *,
    code: str,
    mode: str = "vad",
    lang: str | None = "ru",
    max_duration_ms: int = 8000,
    owner_node_id: str = "member",
    owner_skill_id: str = "redevice_voice",
) -> dict[str, Any]:
    return _svc.build_capture_command(
        endpoint,
        code=code,
        mode=mode,
        lang=lang,
        max_duration_ms=max_duration_ms,
        owner_node_id=owner_node_id,
        owner_skill_id=owner_skill_id,
    )


def compact_audio_endpoint(endpoint: Mapping[str, Any], *, selected_code: str = "") -> dict[str, Any]:
    return _svc.compact_endpoint(endpoint, selected_code=selected_code)


def endpoint_audio_policy(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    return _svc.policy_report(endpoint)


def process_endpoint_audio_event(
    state: dict[str, Any],
    endpoint: Mapping[str, Any],
    *,
    webspace_id: str | None = None,
    source: str = "endpoint_audio_service",
    dispatch: bool = True,
) -> dict[str, Any] | None:
    return _svc.process_endpoint_event(
        state,
        endpoint,
        webspace_id=webspace_id,
        source=source,
        dispatch=dispatch,
    )


def endpoint_audio_stt_status(lang: str | None = None) -> dict[str, Any]:
    return _svc.stt_status(lang)


__all__ = [
    "build_capture_command",
    "compact_audio_endpoint",
    "endpoint_audio_policy",
    "endpoint_audio_stt_status",
    "process_endpoint_audio_event",
]
