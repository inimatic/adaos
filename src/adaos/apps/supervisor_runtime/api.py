from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable, Iterable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from adaos.apps.api.auth import require_token


@dataclass(frozen=True)
class SupervisorRoute:
    path: str
    endpoint: Callable[..., Any]
    method: str = "GET"
    protected: bool = True


class SupervisorApiAdapter:
    """Validate HTTP payloads and delegate to the supervisor application owner."""

    def __init__(self, manager: Callable[[], Any]) -> None:
        self._manager = manager

    @staticmethod
    def _payload_float(payload: dict[str, Any], key: str, default: float) -> float:
        value = payload.get(key)
        return float(default) if key not in payload or value is None or value == "" else float(value)

    @staticmethod
    def _payload_first_float(payload: dict[str, Any], keys: tuple[str, ...], default: float) -> float:
        for key in keys:
            value = payload.get(key)
            if key in payload and value is not None and value != "":
                return float(value)
        return float(default)

    async def ping(self) -> dict[str, Any]:
        return {"ok": True, "ts": time.time(), "service": "adaos-supervisor"}

    async def supervisor_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().status)

    async def supervisor_memory_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().memory_status)

    async def supervisor_memory_telemetry(self, limit: int = 100) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().memory_telemetry, limit=limit)

    async def supervisor_public_memory_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().public_memory_status)

    async def supervisor_memory_sessions(self, limit: int = 100) -> dict[str, Any]:
        manager = self._manager()
        try:
            return manager.memory_sessions(limit=limit)
        except TypeError:
            return manager.memory_sessions()

    async def supervisor_memory_incidents(self, limit: int = 50) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().memory_incidents, limit=limit)

    async def supervisor_memory_session(self, session_id: str) -> dict[str, Any]:
        payload = self._manager().memory_session(session_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="memory profiling session was not found")
        return payload

    async def supervisor_memory_session_artifact(
        self,
        session_id: str,
        artifact_id: str,
        offset: int = 0,
        max_bytes: int = 256 * 1024,
    ) -> dict[str, Any]:
        manager = self._manager()
        if hasattr(manager, "memory_session_artifact_chunk"):
            payload = manager.memory_session_artifact_chunk(
                session_id,
                artifact_id,
                offset=offset,
                max_bytes=max_bytes,
            )
        else:
            payload = manager.memory_session_artifact(session_id, artifact_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="memory profiling artifact was not found")
        return payload

    async def supervisor_memory_profile_start(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        return self._manager().start_memory_profile(
            profile_mode=str(body.get("profile_mode") or "sampled_profile"),
            reason=str(body.get("reason") or "operator.request"),
            trigger_source=str(body.get("trigger_source") or "operator"),
        )

    async def supervisor_memory_profile_stop(
        self,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        return self._manager().stop_memory_profile(
            session_id,
            reason=str(body.get("reason") or "operator.stop"),
        )

    async def supervisor_memory_profile_retry(
        self,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        return self._manager().retry_memory_profile(
            session_id,
            reason=str(body.get("reason") or "operator.retry"),
        )

    async def supervisor_memory_publish(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        return self._manager().publish_memory_profile(
            session_id,
            reason=str(body.get("reason") or "operator.publish"),
        )

    async def supervisor_sidecar_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().sidecar_status)

    async def supervisor_runtime_restart(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        status = await self._manager().restart_runtime(
            reason=str(body.get("reason") or "supervisor.restart")
        )
        return {"ok": True, "runtime": status}

    async def supervisor_runtime_candidate_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = await self._manager().start_candidate_runtime(
            slot=str(payload.get("slot") or "").strip().upper() or None,
            reason=str(payload.get("reason") or "supervisor.candidate.start"),
        )
        return {"ok": True, "runtime": status}

    async def supervisor_runtime_candidate_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = await self._manager().stop_candidate_runtime(
            reason=str(payload.get("reason") or "supervisor.candidate.stop")
        )
        return {"ok": True, "runtime": status}

    async def supervisor_sidecar_restart(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().restart_sidecar(
            reconnect_hub_root=bool(payload.get("reconnect_hub_root", True)),
            allow_active_channel_disruption=bool(payload.get("allow_active_channel_disruption", False)),
        )

    async def supervisor_update_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().supervisor_update_status)

    async def supervisor_public_update_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._manager().public_update_status)

    async def supervisor_update_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().start_update(
            action="update",
            target_rev=str(payload.get("target_rev") or ""),
            target_version=str(payload.get("target_version") or ""),
            reason=str(payload.get("reason") or "core.update"),
            countdown_sec=self._payload_float(payload, "countdown_sec", 60.0),
            drain_timeout_sec=self._payload_float(payload, "drain_timeout_sec", 10.0),
            signal_delay_sec=self._payload_float(payload, "signal_delay_sec", 0.25),
        )

    async def supervisor_update_cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().cancel_update(reason=str(payload.get("reason") or "user.cancelled"))

    async def supervisor_update_defer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().defer_update(
            delay_sec=self._payload_first_float(payload, ("delay_sec", "countdown_sec"), 300.0),
            reason=str(payload.get("reason") or "user.deferred"),
        )

    async def supervisor_update_rollback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().start_update(
            action="rollback",
            target_rev="",
            target_version="",
            reason=str(payload.get("reason") or "core.rollback"),
            countdown_sec=self._payload_float(payload, "countdown_sec", 0.0),
            drain_timeout_sec=self._payload_float(payload, "drain_timeout_sec", 10.0),
            signal_delay_sec=self._payload_float(payload, "signal_delay_sec", 0.25),
        )

    async def supervisor_update_promote_root(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().promote_root(
            reason=str(payload.get("reason") or "core.root_promotion")
        )

    async def supervisor_update_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._manager().complete_update(
            reason=str(payload.get("reason") or "core.update.complete")
        )

    def handlers(self) -> dict[str, Callable[..., Any]]:
        return {
            name: getattr(self, name)
            for name in (
                "ping",
                "supervisor_status",
                "supervisor_memory_status",
                "supervisor_memory_telemetry",
                "supervisor_public_memory_status",
                "supervisor_memory_sessions",
                "supervisor_memory_incidents",
                "supervisor_memory_session",
                "supervisor_memory_session_artifact",
                "supervisor_memory_profile_start",
                "supervisor_memory_profile_stop",
                "supervisor_memory_profile_retry",
                "supervisor_memory_publish",
                "supervisor_sidecar_status",
                "supervisor_runtime_restart",
                "supervisor_runtime_candidate_start",
                "supervisor_runtime_candidate_stop",
                "supervisor_sidecar_restart",
                "supervisor_update_status",
                "supervisor_public_update_status",
                "supervisor_update_start",
                "supervisor_update_cancel",
                "supervisor_update_defer",
                "supervisor_update_rollback",
                "supervisor_update_promote_root",
                "supervisor_update_complete",
            )
        }


def create_supervisor_app(
    *,
    startup: Callable[[], Awaitable[None]],
    shutdown: Callable[[], Awaitable[None]],
    routes: Iterable[SupervisorRoute],
) -> FastAPI:
    """Build the thin HTTP adapter around supervisor application handlers."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await startup()
        try:
            yield
        finally:
            await shutdown()

    app = FastAPI(title="AdaOS Supervisor", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def private_network_access_middleware(request: Request, call_next):
        origin = str(request.headers.get("origin") or "").strip()
        requested_method = str(request.headers.get("access-control-request-method") or "").strip().upper()
        requested_headers = str(request.headers.get("access-control-request-headers") or "").strip()
        requested_private_network = str(
            request.headers.get("access-control-request-private-network") or ""
        ).strip().lower()
        if (
            origin
            and request.method.upper() == "OPTIONS"
            and requested_method
            and requested_private_network == "true"
        ):
            response = Response(status_code=204)
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = requested_method
            response.headers["Access-Control-Allow-Private-Network"] = "true"
            if requested_headers:
                response.headers["Access-Control-Allow-Headers"] = requested_headers
            response.headers["Vary"] = "Origin"
            return response

        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Private-Network"] = "true"
            response.headers["Vary"] = "Origin"
        return response

    for route in routes:
        dependencies = [Depends(require_token)] if route.protected else None
        app.add_api_route(
            route.path,
            route.endpoint,
            methods=[route.method],
            dependencies=dependencies,
        )
    return app
