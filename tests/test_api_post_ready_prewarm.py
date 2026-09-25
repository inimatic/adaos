from __future__ import annotations

import asyncio
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
