from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

from adaos.apps.api import server
from adaos.services import builder as builder_service
from adaos.services.scenario import webspace_runtime


def test_post_ready_prewarm_delay_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_DELAY_SEC", "-1")
    assert server._post_ready_prewarm_delay_sec() == 0.0

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_DELAY_SEC", "999")
    assert server._post_ready_prewarm_delay_sec() == 30.0

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_DELAY_SEC", "invalid")
    assert server._post_ready_prewarm_delay_sec() == 1.5

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_FIRST_PAINT_MAX_WAIT_SEC", "-1")
    assert server._post_ready_prewarm_first_paint_max_wait_sec() == 0.0

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_FIRST_PAINT_MAX_WAIT_SEC", "999")
    assert server._post_ready_prewarm_first_paint_max_wait_sec() == 300.0


def test_post_ready_prewarm_waits_for_started_room_first_paint(monkeypatch) -> None:
    from adaos.services.yjs import gateway_ws

    checks = iter((False, False, True))
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        gateway_ws,
        "desktop_first_paint_observed",
        lambda: next(checks),
    )
    monkeypatch.setattr(
        server,
        "_post_ready_prewarm_first_paint_max_wait_sec",
        lambda: 30.0,
    )
    monkeypatch.setattr(server.asyncio, "sleep", _sleep)

    result = asyncio.run(
        server._wait_for_first_paint_before_post_ready_prewarm(
            minimum_delay_sec=1.5,
        )
    )

    assert result == "first_paint_observed"
    assert sleeps == [1.5, 0.25, 0.25]


def test_catalog_and_materialization_prewarm_runs_after_readiness(monkeypatch) -> None:
    calls: list[object] = []
    app = SimpleNamespace(state=SimpleNamespace())

    class _Catalog:
        @classmethod
        def from_context(cls):
            calls.append("catalog_context")
            return cls()

        def list_projects(self, *, limit: int):
            calls.append(("catalog", limit))
            return []

    async def _prewarm_sources():
        calls.append("sources")
        return {"modes": {"workspace": {"declarations": 1}}}

    async def _hydrate(sources):
        calls.append(("hydrate", sources))
        return {
            "ok": True,
            "duration_ms": 2.0,
            "phases_ms": {"hydrate_webspaces": 1.0},
            "webspaces": [],
        }

    monkeypatch.setattr(server, "_post_ready_prewarm_delay_sec", lambda: 0.0)
    monkeypatch.setattr(
        server,
        "_post_ready_prewarm_first_paint_max_wait_sec",
        lambda: 0.0,
    )
    monkeypatch.setattr(builder_service, "BuilderProjectCatalogService", _Catalog)
    monkeypatch.setattr(
        webspace_runtime,
        "prewarm_webspace_materialization_sources",
        _prewarm_sources,
    )
    monkeypatch.setattr(
        webspace_runtime,
        "hydrate_webspace_materialization_statuses",
        _hydrate,
    )

    asyncio.run(server._run_post_ready_catalog_and_materialization_prewarm(app))

    assert calls == [
        "catalog_context",
        ("catalog", 5000),
        "sources",
        ("hydrate", {"modes": {"workspace": {"declarations": 1}}}),
    ]
    assert app.state.webspace_materialization_hydration["ok"] is True
    status = app.state.post_ready_catalog_materialization_prewarm
    assert status["state"] == "complete"
    assert set(status["phases_ms"]) == {
        "builder_project_catalog",
        "materialization_sources",
        "materialization_hydration",
    }


def test_catalog_and_materialization_prewarm_skips_for_interactive_browser(
    monkeypatch,
) -> None:
    from adaos.services.yjs import gateway_ws

    calls: list[str] = []
    app = SimpleNamespace(state=SimpleNamespace())

    async def _barrier(*, minimum_delay_sec: float) -> str:
        assert minimum_delay_sec >= 0.0
        return "first_paint_observed"

    monkeypatch.setattr(
        server,
        "_wait_for_first_paint_before_post_ready_prewarm",
        _barrier,
    )
    monkeypatch.setattr(gateway_ws, "active_yws_connection_total", lambda: 2)
    monkeypatch.setattr(
        builder_service.BuilderProjectCatalogService,
        "from_context",
        lambda: calls.append("catalog"),
    )

    asyncio.run(server._run_post_ready_catalog_and_materialization_prewarm(app))

    assert calls == []
    status = app.state.post_ready_catalog_materialization_prewarm
    assert status["state"] == "skipped"
    assert status["skip_reason"] == "interactive_yws_clients_active"
    assert status["active_yws_connections"] == 2


def test_yjs_gc_is_collected_on_owner_after_catalog_worker(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    owner_thread = threading.current_thread().name
    monkeypatch.setitem(sys.modules, "y_py", SimpleNamespace())
    monkeypatch.setattr(server.gc, "isenabled", lambda: True)
    monkeypatch.setattr(
        server.gc,
        "disable",
        lambda: calls.append(("disable", threading.current_thread().name)),
    )
    monkeypatch.setattr(
        server.gc,
        "collect",
        lambda: calls.append(("collect", threading.current_thread().name)) or 0,
    )
    monkeypatch.setattr(
        server.gc,
        "enable",
        lambda: calls.append(("enable", threading.current_thread().name)),
    )

    def _catalog_read() -> str:
        calls.append(("worker", threading.current_thread().name))
        return "ready"

    result = asyncio.run(server._to_thread_without_yjs_cyclic_gc(_catalog_read))

    assert result == "ready"
    assert [name for name, _thread in calls] == [
        "disable",
        "worker",
        "collect",
        "enable",
    ]
    assert calls[0][1] == owner_thread
    assert calls[1][1] != owner_thread
    assert calls[2][1] == owner_thread
    assert calls[3][1] == owner_thread


def test_api_runtime_routes_cyclic_gc_to_yjs_owner_thread(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    owner_thread = threading.current_thread().name
    monkeypatch.setitem(sys.modules, "y_py", SimpleNamespace())
    monkeypatch.setattr(server.gc, "isenabled", lambda: True)
    monkeypatch.setattr(server, "_yjs_owner_gc_interval_sec", lambda: 0.0)
    monkeypatch.setattr(
        server.gc,
        "disable",
        lambda: calls.append(("disable", threading.current_thread().name)),
    )
    monkeypatch.setattr(
        server.gc,
        "collect",
        lambda: calls.append(("collect", threading.current_thread().name)) or 1,
    )
    monkeypatch.setattr(
        server.gc,
        "enable",
        lambda: calls.append(("enable", threading.current_thread().name)),
    )
    app = SimpleNamespace(state=SimpleNamespace())

    async def _exercise() -> None:
        async with server._yjs_owner_gc_runtime(app):
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert calls[0] == ("disable", owner_thread)
    assert calls[-1] == ("enable", owner_thread)
    assert any(name == "collect" for name, _thread in calls)
    assert all(thread == owner_thread for _name, thread in calls)
    assert app.state.yjs_owner_gc["active"] is False
