from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

from adaos.apps.api import server
from adaos.services import builder as builder_service
from adaos.services.applications import runtime_selection as runtime_selection_service
from adaos.services.applications import store as application_store_module
from adaos.services.scenario import webspace_runtime
from adaos.services.skill import tool_contract as tool_contract_service


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

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_POST_PAINT_GRACE_SEC", "-1")
    assert server._post_ready_prewarm_post_paint_grace_sec() == 0.0

    monkeypatch.setenv("ADAOS_POST_READY_PREWARM_POST_PAINT_GRACE_SEC", "999")
    assert server._post_ready_prewarm_post_paint_grace_sec() == 300.0


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
    monkeypatch.setattr(
        server,
        "_post_ready_prewarm_post_paint_grace_sec",
        lambda: 30.0,
    )
    monkeypatch.setattr(server.asyncio, "sleep", _sleep)

    result = asyncio.run(
        server._wait_for_first_paint_before_post_ready_prewarm(
            minimum_delay_sec=1.5,
        )
    )

    assert result == "first_paint_observed"
    assert sleeps == [1.5, 0.25, 0.25, 30.0]


def test_catalog_and_materialization_prewarm_stays_lazy_without_a_browser(monkeypatch) -> None:
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

    assert calls == []
    status = app.state.post_ready_catalog_materialization_prewarm
    assert status["state"] == "deferred"
    assert status["skip_reason"] == "no_interactive_first_paint"
    assert status["phases_ms"] == {}


def test_catalog_and_materialization_prewarm_does_not_compete_with_interactive_browser(
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
    assert status["state"] == "deferred"
    assert status["skip_reason"] == "interactive_first_paint_observed"
    assert status["active_yws_connections"] == 2
    assert status["phases_ms"] == {}


def test_selected_trial_runtime_prewarm_is_bounded_to_selected_skill_components(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    selection = SimpleNamespace(
        application_id="management",
        webspace_id="desktop",
        release_digest="sha256:release",
        runtime_root_ref="trial:candidate",
    )
    release = SimpleNamespace(
        project_release=SimpleNamespace(
            components=(
                SimpleNamespace(kind="scenario", artifact_id="web_desktop"),
                SimpleNamespace(kind="skill", artifact_id="web_desktop_runtime_skill"),
            )
        )
    )

    class _Store:
        def __init__(self, _root):
            pass

        def list_runtime_selections(self):
            return (selection,)

        def get_release(self, application_id, release_digest):
            assert application_id == "management"
            assert release_digest == "sha256:release"
            return release

    manager = SimpleNamespace(
        run_tool=lambda skill, tool, payload, **options: calls.append(
            (
                "run_tool",
                skill,
                tool,
                str(payload.get("section")),
                str(options.get("bypass_yjs_guard")),
            )
        )
        or {"ok": True}
    )
    runtime = SimpleNamespace(
        ready_manager=lambda skill: calls.append(("ready", skill)) or manager
    )
    monkeypatch.setattr(application_store_module, "ApplicationStore", _Store)
    monkeypatch.setattr(
        runtime_selection_service,
        "selected_trial_exact",
        lambda _ctx, webspace, application, digest, kind, component, classified: (
            calls.append(
                (
                    "select",
                    webspace,
                    application,
                    digest,
                    kind,
                    component,
                    str(classified),
                )
            )
            or runtime
        ),
    )
    monkeypatch.setattr(
        tool_contract_service,
        "declared_skill_webui_owner",
        lambda resolved, *, skill_name, dev: calls.append(
            ("manifest", skill_name, str(dev), str(resolved is manager))
        )
        or "shared",
    )
    monkeypatch.setattr(
        server,
        "_get_ctx",
        lambda: SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: ".tmp/state")),
    )

    result = asyncio.run(server._prewarm_selected_trial_runtimes())

    assert result["ok"] is True
    assert result["selection_total"] == 1
    assert result["warmed_total"] == 1
    assert [call[0] for call in calls] == [
        "select",
        "ready",
        "manifest",
        "run_tool",
        "run_tool",
    ]
    assert [call[3] for call in calls if call[0] == "run_tool"] == [
        "dashboard",
        "node_dashboard",
    ]


def test_selected_yjs_room_prewarm_uses_only_bounded_selected_webspaces(
    monkeypatch,
) -> None:
    from adaos.services.workspaces import index as workspace_index
    from adaos.services.yjs import gateway_ws

    calls: list[str] = []

    class _Server:
        async def get_room(self, webspace_id: str):
            calls.append(webspace_id)
            return SimpleNamespace(ready=True)

    monkeypatch.setattr(gateway_ws, "y_server", _Server())
    monkeypatch.setattr(workspace_index, "list_workspaces", lambda: [])
    source = {
        "warmed": [
            {"webspace_id": "desktop"},
            {"webspace_id": "desktop"},
            {"webspace_id": "desktop-dev"},
            {},
        ]
    }

    result = asyncio.run(server._prewarm_selected_yjs_rooms(source))

    assert calls == ["desktop", "desktop-dev"]
    assert result["ok"] is True
    assert result["warmed_total"] == 2


def test_selected_yjs_room_prewarm_prefers_primary_dev_surface(
    monkeypatch,
    tmp_path,
) -> None:
    from adaos.services.workspaces import index as workspace_index
    from adaos.services.yjs import gateway_ws

    calls: list[str] = []

    class _Server:
        async def get_room(self, webspace_id: str):
            calls.append(webspace_id)
            return SimpleNamespace(ready=True)

    primary = tmp_path / "desktop-dev.ystore"
    other = tmp_path / "desktop-builder-dev.ystore"
    primary.write_bytes(b"primary")
    other.write_bytes(b"other")
    other.touch()
    monkeypatch.setattr(gateway_ws, "y_server", _Server())
    monkeypatch.setattr(
        workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop-builder-dev", is_dev=True, path=str(other)),
            SimpleNamespace(workspace_id="desktop-dev", is_dev=True, path=str(primary)),
        ],
    )

    result = asyncio.run(
        server._prewarm_selected_yjs_rooms({"warmed": [{"webspace_id": "desktop"}]})
    )

    assert calls == ["desktop", "desktop-dev"]
    assert result["warmed_total"] == 2


def test_selected_application_authority_prewarm_uses_exact_release(monkeypatch) -> None:
    from adaos.apps.api import tool_bridge

    calls: list[dict[str, object]] = []

    async def _prewarm(_ctx, **kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, **kwargs}

    monkeypatch.setattr(
        tool_bridge,
        "prewarm_selected_application_tool_authority",
        _prewarm,
    )
    monkeypatch.setattr(server, "_get_ctx", lambda: object())
    source = {
        "warmed": [
            {
                "application_id": "web_desktop",
                "webspace_id": "desktop",
                "release_digest": "sha256:release",
                "skill": "web_desktop_runtime_skill",
            }
        ]
    }

    result = asyncio.run(server._prewarm_selected_application_authorities(source))

    assert result["ok"] is True
    assert result["warmed_total"] == 1
    assert calls[0]["release_digest"] == "sha256:release"
    assert calls[0]["scenario_id"] == "web_desktop"
    assert "get_system_overview" in calls[0]["public_tools"]


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
        lambda *_args: calls.append(("collect", threading.current_thread().name)) or 1,
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


def test_yjs_owner_gc_uses_bounded_generational_schedule() -> None:
    assert [server._yjs_owner_gc_generation(index) for index in range(1, 21)] == [
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        2,
    ]
