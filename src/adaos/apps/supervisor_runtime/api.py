from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from adaos.apps.api.auth import require_token


@dataclass(frozen=True)
class SupervisorRoute:
    path: str
    endpoint: Callable[..., Any]
    method: str = "GET"
    protected: bool = True


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
