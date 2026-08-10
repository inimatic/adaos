from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import APIRouter, FastAPI

from adaos.apps.api import router_registry
from adaos.apps.api.router_registry import RuntimeRouter
from adaos.apps.api.runtime_lifecycle import RuntimeApplicationLifecycle


@pytest.mark.asyncio
async def test_runtime_application_lifecycle_mounts_then_starts_and_stops() -> None:
    events: list[str] = []
    app = FastAPI()

    def _mount(_app: FastAPI) -> None:
        events.append("routers.mount")

    @asynccontextmanager
    async def _runtime(_app: FastAPI):
        events.append("runtime.start")
        try:
            yield
        finally:
            events.append("runtime.stop")

    lifecycle = RuntimeApplicationLifecycle(
        app,
        runtime_context_factory=_runtime,
        router_mount=_mount,
    )

    await lifecycle.start()
    await lifecycle.start()
    await lifecycle.stop()
    await lifecycle.stop()

    assert events == ["routers.mount", "runtime.start", "runtime.stop"]
    assert lifecycle.started is False


@pytest.mark.asyncio
async def test_runtime_application_lifecycle_leaves_failed_start_stopped() -> None:
    events: list[str] = []
    app = FastAPI()

    @asynccontextmanager
    async def _runtime(_app: FastAPI):
        events.append("runtime.start")
        try:
            raise RuntimeError("boom")
            yield  # pragma: no cover
        finally:
            events.append("runtime.rollback")

    lifecycle = RuntimeApplicationLifecycle(
        app,
        runtime_context_factory=_runtime,
        router_mount=lambda _app: events.append("routers.mount"),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await lifecycle.start()

    assert events == ["routers.mount", "runtime.start", "runtime.rollback"]
    assert lifecycle.started is False


def test_runtime_router_registry_mounts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    router = APIRouter()

    @router.get("/probe")
    async def _probe() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    monkeypatch.setattr(router_registry, "runtime_routers", lambda: (RuntimeRouter(router, "/api"),))

    router_registry.mount_runtime_routers(app)
    route_count = len(app.routes)
    router_registry.mount_runtime_routers(app)

    assert len(app.routes) == route_count
    assert any(getattr(route, "path", None) == "/api/probe" for route in app.routes)
