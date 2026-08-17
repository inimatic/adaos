from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .api import SupervisorRoute


def _handler(handlers: Mapping[str, Callable[..., Any]], name: str) -> Callable[..., Any]:
    try:
        return handlers[name]
    except KeyError as exc:  # pragma: no cover - import-time configuration guard
        raise RuntimeError(f"missing supervisor route handler: {name}") from exc


def health_routes(handlers: Mapping[str, Callable[..., Any]]) -> tuple[SupervisorRoute, ...]:
    return (
        SupervisorRoute("/api/ping", _handler(handlers, "ping"), protected=False),
        SupervisorRoute("/api/supervisor/status", _handler(handlers, "supervisor_status")),
    )


def memory_routes(handlers: Mapping[str, Callable[..., Any]]) -> tuple[SupervisorRoute, ...]:
    return (
        SupervisorRoute("/api/supervisor/memory/status", _handler(handlers, "supervisor_memory_status")),
        SupervisorRoute("/api/supervisor/memory/telemetry", _handler(handlers, "supervisor_memory_telemetry")),
        SupervisorRoute(
            "/api/supervisor/public/memory-status",
            _handler(handlers, "supervisor_public_memory_status"),
            protected=False,
        ),
        SupervisorRoute("/api/supervisor/memory/sessions", _handler(handlers, "supervisor_memory_sessions")),
        SupervisorRoute("/api/supervisor/memory/incidents", _handler(handlers, "supervisor_memory_incidents")),
        SupervisorRoute(
            "/api/supervisor/memory/sessions/{session_id}",
            _handler(handlers, "supervisor_memory_session"),
        ),
        SupervisorRoute(
            "/api/supervisor/memory/sessions/{session_id}/artifacts/{artifact_id}",
            _handler(handlers, "supervisor_memory_session_artifact"),
        ),
        SupervisorRoute(
            "/api/supervisor/memory/profile/start",
            _handler(handlers, "supervisor_memory_profile_start"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/memory/profile/{session_id}/stop",
            _handler(handlers, "supervisor_memory_profile_stop"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/memory/profile/{session_id}/retry",
            _handler(handlers, "supervisor_memory_profile_retry"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/memory/publish",
            _handler(handlers, "supervisor_memory_publish"),
            method="POST",
        ),
    )


def runtime_routes(handlers: Mapping[str, Callable[..., Any]]) -> tuple[SupervisorRoute, ...]:
    return (
        SupervisorRoute("/api/supervisor/sidecar/status", _handler(handlers, "supervisor_sidecar_status")),
        SupervisorRoute(
            "/api/supervisor/service/restart",
            _handler(handlers, "supervisor_service_restart"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/runtime/restart",
            _handler(handlers, "supervisor_runtime_restart"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/runtime/candidate/start",
            _handler(handlers, "supervisor_runtime_candidate_start"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/runtime/candidate/stop",
            _handler(handlers, "supervisor_runtime_candidate_stop"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/sidecar/restart",
            _handler(handlers, "supervisor_sidecar_restart"),
            method="POST",
        ),
    )


def update_routes(handlers: Mapping[str, Callable[..., Any]]) -> tuple[SupervisorRoute, ...]:
    return (
        SupervisorRoute("/api/supervisor/update/status", _handler(handlers, "supervisor_update_status")),
        SupervisorRoute(
            "/api/supervisor/public/update-status",
            _handler(handlers, "supervisor_public_update_status"),
            protected=False,
        ),
        SupervisorRoute(
            "/api/supervisor/update/start",
            _handler(handlers, "supervisor_update_start"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/update/cancel",
            _handler(handlers, "supervisor_update_cancel"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/update/defer",
            _handler(handlers, "supervisor_update_defer"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/update/rollback",
            _handler(handlers, "supervisor_update_rollback"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/update/promote-root",
            _handler(handlers, "supervisor_update_promote_root"),
            method="POST",
        ),
        SupervisorRoute(
            "/api/supervisor/update/complete",
            _handler(handlers, "supervisor_update_complete"),
            method="POST",
        ),
    )


def create_supervisor_routes(handlers: Mapping[str, Callable[..., Any]]) -> tuple[SupervisorRoute, ...]:
    return (
        *health_routes(handlers),
        *memory_routes(handlers),
        *runtime_routes(handlers),
        *update_routes(handlers),
    )
