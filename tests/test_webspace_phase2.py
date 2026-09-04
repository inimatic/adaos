from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import threading
import time
import types
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.agent_context import get_ctx

try:
    import y_py as _real_y_py  # noqa: F401
except Exception:
    if "y_py" not in sys.modules:
        sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
try:
    import ypy_websocket as _real_ypy_websocket  # noqa: F401
except Exception:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.scenario import webspace_runtime as webspace_runtime_module
from adaos.services.scenario.webspace_components.resolution import _apply_component_metadata
from adaos.services.workspaces import (
    ensure_workspace,
    get_workspace,
    set_workspace_current_scenario_overlay,
    set_workspace_installed_overlay,
    set_workspace_manifest,
    set_workspace_pinned_widgets_overlay,
    set_workspace_topbar_overlay,
    set_workspace_page_schema_overlay,
)


def _clear_member_snapshot_task_state() -> None:
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    state.clear_tasks(state.MEMBER_SNAPSHOT, cancel=True)
    state.clear_tasks(state.MEMBER_SNAPSHOT_DELAYED, cancel=True)
    for group in (
        state.MEMBER_SNAPSHOT_LAST_AT,
        state.MEMBER_SNAPSHOT_DIRTY,
        state.MEMBER_SNAPSHOT_STATS,
        state.MEMBER_SNAPSHOT_MATERIAL_FINGERPRINT,
    ):
        state.clear_records(group)


def _clear_scenario_switch_task_state() -> None:
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    state.clear_tasks(state.SCENARIO_SWITCH, cancel=True)
    state.clear_records(state.WEBSPACE_REBUILD_STATUS)


def test_json_fingerprint_normalizes_yjs_integral_float_roundtrip() -> None:
    if not hasattr(_real_y_py, "YDoc"):
        pytest.skip("y_py is unavailable")

    expected = {
        "node_index": 1,
        "layout": {"width": 320, "ratio": 1.25},
        "limits": [0, 2, 4],
    }
    ydoc = _real_y_py.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("data").set(txn, "catalog", expected)

    round_tripped = ydoc.get_map("data").get("catalog")
    assert round_tripped["node_index"] == 1.0
    assert webspace_runtime_module._fingerprint_json_like(round_tripped) == (  # noqa: SLF001
        webspace_runtime_module._fingerprint_json_like(expected)  # noqa: SLF001
    )
    assert webspace_runtime_module._fingerprint_json_like({"ratio": 1.25}) != (  # noqa: SLF001
        webspace_runtime_module._fingerprint_json_like({"ratio": 1.5})  # noqa: SLF001
    )


def test_component_metadata_replaces_stale_publication_projection() -> None:
    projected = _apply_component_metadata(
        {
            "id": "demo-metrics",
            "version": "0.13.25",
            "release_stage": "beta",
            "component_update": {"stage": "beta", "version": "0.13.25"},
            "_adaos": {
                "version": "0.13.25",
                "releaseStage": "beta",
                "componentUpdate": {"stage": "beta", "version": "0.13.25"},
            },
        },
        component_type="skill",
        component_id="demo_metrics_skill",
        version="0.13.26",
        source_authority="workspace",
        component_update={"stage": "stable", "version": "0.13.26"},
    )

    assert projected["version"] == "0.13.26"
    assert projected["release_stage"] == "stable"
    assert projected["_adaos"]["version"] == "0.13.26"
    assert projected["_adaos"]["releaseStage"] == "stable"

    without_notice = _apply_component_metadata(
        projected,
        component_type="skill",
        component_id="demo_metrics_skill",
        version="0.13.26",
        source_authority="workspace",
    )

    assert "release_stage" not in without_notice
    assert "component_update" not in without_notice
    assert "releaseStage" not in without_notice["_adaos"]
    assert "componentUpdate" not in without_notice["_adaos"]


def test_component_metadata_preserves_builder_materialization_stage() -> None:
    projected = _apply_component_metadata(
        {
            "id": "flowboard",
            "_adaos": {
                "releaseStage": "BETA",
                "releaseStageSource": "builder_materialization",
                "materialization": {"stage": "trial", "revision": "0.1.1"},
            },
        },
        component_type="scenario",
        component_id="flowboard",
        version="0.1.10",
        source_authority="trial",
    )

    assert projected["_adaos"]["releaseStage"] == "BETA"
    assert projected["_adaos"]["releaseStageSource"] == "builder_materialization"
    assert projected["_adaos"]["materialization"]["stage"] == "trial"


def test_build_local_desktop_catalog_snapshot_uses_runtime_skill_decls(monkeypatch) -> None:
    captured_modes: list[str] = []

    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        captured_modes.append(mode)
        return [
            {
                "skill": "member_skill",
                "space": "default",
                "apps": [{"id": "member_app", "title": "Member App"}],
                "widgets": [{"id": "member_widget", "title": "Member Widget"}],
                "interface": {
                    "schema": "adaos.ui.skill_interface.v1",
                    "views": {"member.view": {"surface": "modal"}},
                },
            }
        ]

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)

    snapshot = webspace_runtime_module.build_local_desktop_catalog_snapshot(mode="workspace")

    assert captured_modes == ["workspace"]
    assert snapshot["apps"][0]["id"] == "member_app"
    assert snapshot["apps"][0]["node_id"] == "node-1"
    assert snapshot["apps"][0]["node_label"] == "Node 1"
    assert snapshot["widgets"][0]["id"] == "member_widget"
    assert snapshot["interfaces"]["member_skill"]["views"]["member.view"]["surface"] == "modal"
    assert snapshot["interfaces"]["member_skill"]["_adaos"]["originSkill"] == "member_skill"


def test_node_owned_shared_stream_widget_stays_shared(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    snapshot = webspace_runtime_module._local_catalog_decl_entries(
        [
            {
                "skill": "voice_chat_skill",
                "ui_owner": "node",
                "widgets": [
                    {
                        "id": "voice_chat_widget",
                        "dataSource": {
                            "kind": "stream",
                            "receiver": "voice_chat.messages",
                            "transport": "hub",
                            "scope": "shared",
                        },
                    },
                    {
                        "id": "node_stream_widget",
                        "dataSource": {
                            "kind": "stream",
                            "receiver": "node.metrics",
                        },
                    },
                ],
            }
        ]
    )

    shared_ds = snapshot["widgets"][0]["dataSource"]
    node_ds = snapshot["widgets"][1]["dataSource"]
    assert shared_ds["receiver"] == "voice_chat.messages"
    assert shared_ds["scope"] == "shared"
    assert "nodeId" not in shared_ds
    assert node_ds["nodeId"] == "node-1"


def test_build_local_desktop_catalog_snapshot_prefers_live_ydoc_values_over_decl_defaults(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        return [
            {
                "skill": "infrastate_skill",
                "space": "default",
                "apps": [],
                "widgets": [],
                "ydoc_defaults": {
                    "data/infrastate/summary": {
                        "label": "Core update",
                        "value": "idle",
                        "subtitle": "slot --",
                        "description": "No update in progress",
                    }
                },
            }
        ]

    class _Map:
        def __init__(self, data):
            self._data = data

        def get(self, key):
            value = self._data.get(key)
            if isinstance(value, dict):
                return _Map(value)
            return value

        def items(self):
            return self._data.items()

        def items(self):
            return self._data.items()

        def items(self):
            return self._data.items()

        def items(self):
            return self._data.items()

        def items(self):
            return self._data.items()

    class _YDoc:
        def __init__(self, data):
            self._data = data

        def get_map(self, key):
            value = self._data.get(key, {})
            return _Map(value if isinstance(value, dict) else {})

    class _CtxMgr:
        def __enter__(self):
            return _YDoc(
                {
                    "data": {
                        "nodes": {
                            "node-1": {
                                "infrastate": {
                                    "summary": {
                                        "label": "Core update",
                                        "value": "succeeded",
                                        "subtitle": "slot B | 2ac1fa3",
                                        "description": "runtime boot validated on slot B",
                                    }
                                }
                            }
                        }
                    }
                }
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)
    monkeypatch.setattr(webspace_runtime_module, "get_ydoc", lambda webspace_id: _CtxMgr())

    snapshot = webspace_runtime_module.build_local_desktop_catalog_snapshot(mode="workspace")

    assert snapshot["ydoc_defaults"]["data/nodes/node-1/infrastate/summary"] == {
        "label": "Core update",
        "value": "succeeded",
        "subtitle": "slot B | 2ac1fa3",
        "description": "runtime boot validated on slot B",
    }


def test_build_local_desktop_catalog_snapshot_reads_node_scoped_defaults_from_local_unscoped_skill_projection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        return [
            {
                "skill": "infrastate_skill",
                "space": "default",
                "apps": [],
                "widgets": [],
                "ydoc_defaults": {
                    "data/infrastate": {
                        "summary": {
                            "label": "Core update",
                            "value": "idle",
                            "subtitle": "slot --",
                            "description": "No update in progress",
                        },
                        "update_actions": [
                            {
                                "id": "marketplace",
                                "title": "Marketplace",
                            }
                        ],
                    }
                },
            }
        ]

    class _Map:
        def __init__(self, data):
            self._data = data

        def get(self, key):
            value = self._data.get(key)
            if isinstance(value, dict):
                return _Map(value)
            return value

        def items(self):
            return self._data.items()

    class _YDoc:
        def __init__(self, data):
            self._data = data

        def get_map(self, key):
            value = self._data.get(key, {})
            return _Map(value if isinstance(value, dict) else {})

    class _CtxMgr:
        def __enter__(self):
            return _YDoc(
                {
                    "data": {
                        "infrastate": {
                            "summary": {
                                "label": "Core update",
                                "value": "validated",
                                "subtitle": "slot A | c8d43f5",
                                "description": "runtime boot validated on slot A; root promotion pending",
                            },
                            "update_actions": [
                                {
                                    "id": "marketplace",
                                    "title": "Marketplace",
                                },
                                {
                                    "id": "start_update",
                                    "title": "Start update",
                                },
                            ],
                        }
                    }
                }
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)
    monkeypatch.setattr(webspace_runtime_module, "get_ydoc", lambda _webspace_id: _CtxMgr())

    snapshot = webspace_runtime_module.build_local_desktop_catalog_snapshot()

    assert snapshot["ydoc_defaults"]["data/nodes/node-1/infrastate"] == {
        "summary": {
            "label": "Core update",
            "value": "validated",
            "subtitle": "slot A | c8d43f5",
            "description": "runtime boot validated on slot A; root promotion pending",
        },
        "update_actions": [
            {
                "id": "marketplace",
                "title": "Marketplace",
            },
            {
                "id": "start_update",
                "title": "Start update",
            },
        ],
    }


def test_build_local_desktop_catalog_snapshot_prefers_live_room_doc_without_sync_get_ydoc(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        return [
            {
                "skill": "infrastate_skill",
                "space": "default",
                "apps": [],
                "widgets": [],
                "ydoc_defaults": {
                    "data/infrastate": {
                        "summary": {
                            "label": "Core update",
                            "value": "idle",
                            "subtitle": "slot --",
                            "description": "No update in progress",
                        }
                    }
                },
            }
        ]

    class _Map:
        def __init__(self, data):
            self._data = data

        def get(self, key):
            value = self._data.get(key)
            if isinstance(value, dict):
                return _Map(value)
            return value

        def items(self):
            return self._data.items()

    class _YDoc:
        def __init__(self, data):
            self._data = data

        def get_map(self, key):
            value = self._data.get(key, {})
            return _Map(value if isinstance(value, dict) else {})

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_resolve_live_room_ydoc",
        lambda _webspace_id: _YDoc(
            {
                "data": {
                    "infrastate": {
                        "summary": {
                            "label": "Core update",
                            "value": "succeeded",
                            "subtitle": "slot A | c8d43f5",
                            "description": "runtime boot validated on slot A",
                        }
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "get_ydoc",
        lambda _webspace_id: (_ for _ in ()).throw(AssertionError("sync get_ydoc should not run when live room doc exists")),
    )

    snapshot = webspace_runtime_module.build_local_desktop_catalog_snapshot()

    assert snapshot["ydoc_defaults"]["data/nodes/node-1/infrastate"] == {
        "summary": {
            "label": "Core update",
            "value": "succeeded",
            "subtitle": "slot A | c8d43f5",
            "description": "runtime boot validated on slot A",
        }
    }


@pytest.mark.asyncio
async def test_build_local_desktop_catalog_snapshot_async_reads_local_unscoped_skill_projection(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        return [
            {
                "skill": "infrastate_skill",
                "space": "default",
                "apps": [],
                "widgets": [],
                "ydoc_defaults": {
                    "data/infrastate": {
                        "summary": {
                            "label": "Core update",
                            "value": "idle",
                            "subtitle": "slot --",
                            "description": "No update in progress",
                        }
                    }
                },
            }
        ]

    class _Map:
        def __init__(self, data):
            self._data = data

        def get(self, key):
            value = self._data.get(key)
            if isinstance(value, dict):
                return _Map(value)
            return value

        def items(self):
            return self._data.items()

    class _YDoc:
        def __init__(self, data):
            self._data = data

        def get_map(self, key):
            value = self._data.get(key, {})
            return _Map(value if isinstance(value, dict) else {})

    class _AsyncCtxMgr:
        async def __aenter__(self):
            return _YDoc(
                {
                    "data": {
                        "infrastate": {
                            "summary": {
                                "label": "Core update",
                                "value": "succeeded",
                                "subtitle": "slot A | c8d43f5",
                                "description": "runtime boot validated on slot A",
                            }
                        }
                    }
                }
            )

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)
    monkeypatch.setattr(webspace_runtime_module, "_resolve_live_room_ydoc", lambda _webspace_id: None)
    monkeypatch.setattr(webspace_runtime_module, "async_read_ydoc", lambda _webspace_id: _AsyncCtxMgr())

    snapshot = await webspace_runtime_module.build_local_desktop_catalog_snapshot_async()

    assert snapshot["ydoc_defaults"]["data/nodes/node-1/infrastate"] == {
        "summary": {
            "label": "Core update",
            "value": "succeeded",
            "subtitle": "slot A | c8d43f5",
            "description": "runtime boot validated on slot A",
        }
    }


@pytest.mark.asyncio
async def test_build_local_desktop_catalog_snapshot_async_keeps_catalog_when_ydoc_overlay_times_out(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_MEMBER_DESKTOP_CATALOG_YDOC_OVERLAY_TIMEOUT_S", "0.1")
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 1",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="member", node_id="node-1", node_settings=SimpleNamespace(node_names=[])),
    )

    def _fake_collect(self, mode: str = "mixed", *, include_remote: bool = True) -> list[dict[str, object]]:  # noqa: ARG001
        return [
            {
                "skill": "member_skill",
                "space": "default",
                "apps": [{"id": "member_app", "title": "Member App"}],
                "widgets": [{"id": "member_widget", "title": "Member Widget"}],
                "ydoc_defaults": {"data/member/current": {"value": "declared"}},
            }
        ]

    async def _slow_overlay(snapshot: dict[str, object], *, webspace_id: str) -> dict[str, object]:  # noqa: ARG001
        await asyncio.sleep(10)
        return snapshot

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", _fake_collect)
    monkeypatch.setattr(webspace_runtime_module, "_overlay_current_ydoc_defaults_async", _slow_overlay)

    snapshot = await webspace_runtime_module.build_local_desktop_catalog_snapshot_async()

    assert snapshot["apps"][0]["id"] == "member_app"
    assert snapshot["widgets"][0]["id"] == "member_widget"
    assert snapshot["ydoc_defaults"]["data/nodes/node-1/member/current"] == {"value": "declared"}


def test_member_snapshot_changed_rebuilds_shared_workspaces_with_rate_limit(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop", is_dev=False),
            SimpleNamespace(workspace_id="dev-infrascope", is_dev=True),
        ],
    )
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_rebuild_min_interval_s", lambda: 60.0)
    _clear_member_snapshot_task_state()

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        calls.append((webspace_id, action, source_of_truth))
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)
    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_snapshot_changed({"node_id": "member-1"})
        await asyncio.sleep(0)
        await webspace_runtime_module._on_subnet_member_snapshot_changed({"node_id": "member-1"})
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert calls == [("desktop", "subnet_member_snapshot_sync", "member_runtime_snapshot")]


def test_member_snapshot_material_fingerprint_includes_skill_interfaces(monkeypatch) -> None:
    from adaos.services.registry import subnet_directory as directory_module

    catalog = {
        "apps": [{"id": "slideshow"}],
        "widgets": [],
        "registry": {"modals": {"folders": {"schema": {"id": "folders"}}}},
        "interfaces": {
            "slideshow_skill": {
                "views": {"slideshow_skill.folders": {"surfaces": ["modal"]}},
            }
        },
    }

    class _Directory:
        def get_node(self, _node_id: str):
            return {"runtime_projection": {"snapshot": {"desktop_catalog": catalog}}}

    monkeypatch.setattr(directory_module, "get_directory", lambda: _Directory())
    before = webspace_runtime_module._member_snapshot_desktop_material_fingerprint("member-1")
    catalog["interfaces"]["slideshow_skill"]["views"]["slideshow_skill.folders"]["params"] = {
        "folder": {"type": "string"},
    }
    after = webspace_runtime_module._member_snapshot_desktop_material_fingerprint("member-1")

    assert before
    assert after
    assert before != after


def test_member_access_reactivated_forces_rebuild_even_when_material_fingerprint_is_unchanged(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    key = "member-1\0desktop"

    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [SimpleNamespace(workspace_id="desktop", is_dev=False)],
    )
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_rebuild_min_interval_s", lambda: 60.0)
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_desktop_material_fingerprint", lambda _node_id: "same")
    _clear_member_snapshot_task_state()
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    state.put_record(state.MEMBER_SNAPSHOT_LAST_AT, key, time.monotonic())
    state.put_record(state.MEMBER_SNAPSHOT_MATERIAL_FINGERPRINT, key, "same")

    async def _fake_seed(**_kwargs):
        return None

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        calls.append((webspace_id, action, source_of_truth))
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "_seed_member_snapshot_ydoc_defaults", _fake_seed)
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_access_reactivated({"node_id": "member-1"})
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert calls == [("desktop", "subnet_member_snapshot_sync", "member_runtime_snapshot")]


def test_member_snapshot_refreshed_rebuilds_when_remote_catalog_projection_is_missing(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [SimpleNamespace(workspace_id="desktop", is_dev=False)],
    )
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_rebuild_min_interval_s", lambda: 0.0)
    _clear_member_snapshot_task_state()

    async def _missing(**_kwargs):
        return True

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        calls.append((webspace_id, action, source_of_truth))
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "_member_catalog_projection_missing", _missing)
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_snapshot_refreshed({"node_id": "member-1"})
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert calls == [("desktop", "subnet_member_snapshot_sync", "member_runtime_snapshot")]


def test_member_snapshot_refreshed_skips_rebuild_when_remote_catalog_projection_is_present(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [SimpleNamespace(workspace_id="desktop", is_dev=False)],
    )
    _clear_member_snapshot_task_state()

    async def _missing(**_kwargs):
        return False

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        calls.append((webspace_id, action, source_of_truth))
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "_member_catalog_projection_missing", _missing)
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_snapshot_refreshed({"node_id": "member-1"})
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert calls == []


def test_member_catalog_projection_missing_repairs_live_room_when_persisted_catalog_is_present(monkeypatch) -> None:
    node_id = "member-1"
    snapshot = {
        "desktop_catalog": {
            "apps": [{"id": "weather_app"}],
            "widgets": [{"id": "weather"}],
        }
    }
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        get_node=lambda _node_id: {"runtime_projection": {"snapshot": snapshot}}
    )
    monkeypatch.setitem(sys.modules, "adaos.services.registry.subnet_directory", directory_module)

    class _Doc:
        def __init__(self, catalog: dict[str, object]) -> None:
            self.catalog = catalog

        def get_map(self, name: str) -> dict[str, object]:
            assert name == "data"
            return {"catalog": self.catalog}

    class _AsyncDoc:
        async def __aenter__(self) -> _Doc:
            return _Doc(
                {
                    "apps": [{"id": "node:member-1:weather_app"}],
                    "widgets": [{"id": "node:member-1:weather"}],
                }
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    refreshes: list[tuple[str, str]] = []

    async def _refresh(webspace_id: str, *, node_id: str) -> bool:
        refreshes.append((webspace_id, node_id))
        return True

    monkeypatch.setattr(webspace_runtime_module, "_resolve_live_room_ydoc", lambda _webspace_id: _Doc({"apps": [], "widgets": []}))
    monkeypatch.setattr(webspace_runtime_module, "async_read_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(webspace_runtime_module, "_refresh_live_room_for_member_catalog_projection", _refresh)

    async def _exercise() -> bool:
        return await webspace_runtime_module._member_catalog_projection_missing(webspace_id="desktop", node_id=node_id)

    assert asyncio.run(_exercise()) is False
    assert refreshes == [("desktop", node_id)]


def test_member_catalog_projection_missing_rebuilds_when_live_and_persisted_catalogs_are_missing(monkeypatch) -> None:
    node_id = "member-1"
    snapshot = {
        "desktop_catalog": {
            "widgets": [{"id": "weather"}],
        }
    }
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        get_node=lambda _node_id: {"runtime_projection": {"snapshot": snapshot}}
    )
    monkeypatch.setitem(sys.modules, "adaos.services.registry.subnet_directory", directory_module)

    class _Doc:
        def __init__(self, catalog: dict[str, object]) -> None:
            self.catalog = catalog

        def get_map(self, name: str) -> dict[str, object]:
            assert name == "data"
            return {"catalog": self.catalog}

    class _AsyncDoc:
        async def __aenter__(self) -> _Doc:
            return _Doc({"apps": [], "widgets": []})

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    refreshes: list[tuple[str, str]] = []

    async def _refresh(webspace_id: str, *, node_id: str) -> bool:
        refreshes.append((webspace_id, node_id))
        return True

    monkeypatch.setattr(webspace_runtime_module, "_resolve_live_room_ydoc", lambda _webspace_id: _Doc({"apps": [], "widgets": []}))
    monkeypatch.setattr(webspace_runtime_module, "async_read_ydoc", lambda *_args, **_kwargs: _AsyncDoc())
    monkeypatch.setattr(webspace_runtime_module, "_refresh_live_room_for_member_catalog_projection", _refresh)

    async def _exercise() -> bool:
        return await webspace_runtime_module._member_catalog_projection_missing(webspace_id="desktop", node_id=node_id)

    assert asyncio.run(_exercise()) is True
    assert refreshes == []


def test_remote_member_catalog_entries_are_node_scoped_and_auto_installed(monkeypatch) -> None:
    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        list_known_nodes=lambda: [
            {
                "node_id": "member-1",
                "roles": ["member"],
                "node_label": "Node 1",
                "node_compact_label": "N1",
                "runtime_projection": {
                    "snapshot": {
                        "desktop_catalog": {
                            "apps": [{"id": "infrastate_app", "title": "Infra State"}],
                            "widgets": [{"id": "infrastate_widget", "title": "Infra State"}],
                        }
                    }
                },
            }
        ]
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_settings=SimpleNamespace(node_names=[])),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")
    try:
        runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
        decls = runtime._collect_remote_skill_decls()
    finally:
        if previous_directory_module is None:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)
        else:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        importlib.invalidate_caches()

    assert len(decls) == 1
    decl = decls[0]
    assert decl["apps"][0]["id"] == "node:member-1:infrastate_app"
    assert decl["apps"][0]["node_local_id"] == "infrastate_app"
    assert decl["apps"][0]["node_label"] == "Node 1"
    assert decl["widgets"][0]["id"] == "node:member-1:infrastate_widget"
    assert decl["contributions"] == [
        {
            "extensionPoint": "desktop.apps",
            "type": "app",
            "id": "node:member-1:infrastate_app",
            "autoInstall": True,
        },
        {
            "extensionPoint": "desktop.widgets",
            "type": "widget",
            "id": "node:member-1:infrastate_widget",
            "autoInstall": True,
        },
    ]


def test_remote_member_catalog_entries_collapse_existing_node_scope(monkeypatch) -> None:
    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        list_known_nodes=lambda: [
            {
                "node_id": "member-1",
                "roles": ["member"],
                "runtime_projection": {
                    "snapshot": {
                        "desktop_catalog": {
                            "apps": [
                                {
                                    "id": "node:member-1:weather_skill",
                                    "title": "Weather",
                                    "launchModal": "node:old-hub:node:member-1:weather_modal",
                                    "node_local_id": "node:member-1:weather_skill",
                                    "remote_id": "node:member-1:weather_skill",
                                }
                            ],
                            "registry": {
                                "modals": {
                                    "node:member-1:weather_modal": {
                                        "title": "Weather Settings",
                                    }
                                }
                            },
                        }
                    }
                },
            }
        ]
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_settings=SimpleNamespace(node_names=[])),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")
    try:
        runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
        decls = runtime._collect_remote_skill_decls()
    finally:
        if previous_directory_module is None:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)
        else:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        importlib.invalidate_caches()

    assert len(decls) == 1
    app = decls[0]["apps"][0]
    assert app["id"] == "node:member-1:weather_skill"
    assert app["launchModal"] == "node:member-1:weather_modal"
    assert app["node_local_id"] == "weather_skill"
    assert app["remote_id"] == "weather_skill"
    assert "node:member-1:weather_modal" in decls[0]["registry"]["modals"]


def test_remote_member_catalog_skips_foreign_relay_entries(monkeypatch) -> None:
    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        list_known_nodes=lambda: [
            {
                "node_id": "hub-relay",
                "roles": ["hub"],
                "runtime_projection": {
                    "snapshot": {
                        "desktop_catalog": {
                            "apps": [
                                {
                                    "id": "node:member-1:weather_skill",
                                    "title": "Weather",
                                    "origin": "skill:subnet.member.member-1",
                                }
                            ],
                            "ydoc_defaults": {
                                "data/nodes/member-1/weather/current": {"city": "Berlin"},
                            },
                        }
                    }
                },
            }
        ]
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_settings=SimpleNamespace(node_names=[])),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")
    try:
        runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
        decls = runtime._collect_remote_skill_decls()
    finally:
        if previous_directory_module is None:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)
        else:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        importlib.invalidate_caches()

    assert decls == []


def test_remote_member_catalog_skips_unavailable_scenario_apps_from_capacity_fallback(monkeypatch) -> None:
    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        list_known_nodes=lambda: [
            {
                "node_id": "member-1",
                "roles": ["member"],
                "capacity": {"skills": [{"name": "prompt_engineer_skill"}]},
            }
        ]
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_settings=SimpleNamespace(node_names=[])),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")
    monkeypatch.setattr(
        webspace_runtime_module,
        "_scenario_exists_for_switch",
        lambda scenario_id, *, space: scenario_id != "missing_prompt_scenario",
    )

    def _load_webui(self, skill_name: str, space: str = "default") -> dict[str, object]:  # noqa: ARG001
        return {
            "apps": [
                {
                    "id": "scenario:missing_prompt_scenario",
                    "title": "Prompt IDE",
                    "scenario_id": "missing_prompt_scenario",
                },
                {"id": "prompt_modal_app", "title": "Prompt Modal", "launchModal": "prompt_modal"},
            ],
            "registry": {"modals": {"prompt_modal": {"title": "Prompt Modal"}}},
        }

    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_load_webui", _load_webui)
    try:
        runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
        decls = runtime._collect_remote_skill_decls()
    finally:
        if previous_directory_module is None:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)
        else:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        importlib.invalidate_caches()

    assert len(decls) == 1
    assert [item["id"] for item in decls[0]["apps"]] == ["node:member-1:prompt_modal_app"]
    assert decls[0]["contributions"] == [
        {
            "extensionPoint": "desktop.apps",
            "type": "app",
            "id": "node:member-1:prompt_modal_app",
            "autoInstall": True,
        }
    ]


def test_member_snapshot_change_seeds_ydoc_defaults_without_waiting_for_rebuild(monkeypatch) -> None:
    seeded: dict[str, object] = {}

    class _FakeDataMap:
        def __init__(self) -> None:
            self._data: dict[str, object] = {}

        def to_json(self) -> dict[str, object]:
            return self._data

        def get(self, key: str) -> object:
            return self._data.get(key)

        def set(self, _txn: object, key: str, value: object) -> None:
            self._data[key] = value

    class _FakeDoc:
        def __init__(self) -> None:
            self.data_map = _FakeDataMap()

        def get_map(self, name: str) -> _FakeDataMap:
            assert name == "data"
            return self.data_map

        class _Txn:
            def __enter__(self) -> object:
                return object()

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        def begin_transaction(self) -> "_FakeDoc._Txn":
            return _FakeDoc._Txn()

    fake_doc = _FakeDoc()

    class _AsyncDocCtx:
        async def __aenter__(self) -> _FakeDoc:
            return fake_doc

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _AsyncMetaCtx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        get_node=lambda _node_id: {
            "runtime_projection": {
                "snapshot": {
                    "desktop_catalog": {
                        "ydoc_defaults": {
                            "data/nodes/member-1/weather/current": {"city": "Berlin"},
                            "data/nodes/member-1/infrastate": {"state": "succeeded"},
                        }
                    }
                }
            }
        }
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda *_args, **_kwargs: _AsyncDocCtx())
    monkeypatch.setattr(webspace_runtime_module, "_webspace_runtime_async_write_meta", lambda **_kwargs: _AsyncMetaCtx())
    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [SimpleNamespace(workspace_id="desktop", is_dev=False)],
    )
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_rebuild_min_interval_s", lambda: 60.0)
    _clear_member_snapshot_task_state()

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        seeded["rebuild"] = (webspace_id, action, source_of_truth)
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_snapshot_changed({"node_id": "member-1"})
        await asyncio.sleep(0)

    try:
        asyncio.run(_exercise())
    finally:
        if previous_directory_module is not None:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        else:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)

    nodes_bucket = fake_doc.data_map.to_json().get("nodes")
    assert isinstance(nodes_bucket, dict)
    assert nodes_bucket["member-1"]["weather"]["current"]["city"] == "Berlin"
    assert nodes_bucket["member-1"]["infrastate"]["state"] == "succeeded"
    assert seeded["rebuild"] == ("desktop", "subnet_member_snapshot_sync", "member_runtime_snapshot")


def test_member_snapshot_changed_skips_unchanged_desktop_material(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    previous_directory_module = sys.modules.get("adaos.services.registry.subnet_directory")
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        get_node=lambda _node_id: {
            "runtime_projection": {
                "snapshot": {
                    "desktop_catalog": {
                        "apps": [{"id": "weather"}],
                        "widgets": [],
                        "ydoc_defaults": {"data/nodes/member-1/weather/current": {"city": "Berlin"}},
                    }
                }
            }
        }
    )
    sys.modules["adaos.services.registry.subnet_directory"] = directory_module

    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [SimpleNamespace(workspace_id="desktop", is_dev=False)],
    )
    monkeypatch.setattr(webspace_runtime_module, "_member_snapshot_rebuild_min_interval_s", lambda: 0.0)
    _clear_member_snapshot_task_state()

    async def _fake_seed(**_kwargs) -> None:
        return None

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str, **_kwargs):
        calls.append((webspace_id, action, source_of_truth))
        return {"accepted": True}

    monkeypatch.setattr(webspace_runtime_module, "_seed_member_snapshot_ydoc_defaults", _fake_seed)
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    async def _exercise() -> None:
        await webspace_runtime_module._on_subnet_member_snapshot_changed({"node_id": "member-1"})
        await asyncio.sleep(0)
        await webspace_runtime_module._on_subnet_member_snapshot_changed({"node_id": "member-1"})
        await asyncio.sleep(0)

    try:
        asyncio.run(_exercise())
    finally:
        if previous_directory_module is not None:
            sys.modules["adaos.services.registry.subnet_directory"] = previous_directory_module
        else:
            sys.modules.pop("adaos.services.registry.subnet_directory", None)

    assert calls == [("desktop", "subnet_member_snapshot_sync", "member_runtime_snapshot")]
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    stats = state.get_record(state.MEMBER_SNAPSHOT_STATS, "member-1\0desktop")
    assert stats["skipped_unchanged_total"] == 1


class _FakeTxn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeMap(dict):
    def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
        self[key] = value


class _CountingMap(_FakeMap):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.set_count = 0

    def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
        self.set_count += 1
        super().set(txn, key, value)


class _FakeDoc:
    def __init__(self, state: dict[str, _FakeMap]) -> None:
        self._state = state
        self.transaction_count = 0

    def get_map(self, name: str) -> _FakeMap:
        return self._state.setdefault(name, _FakeMap())

    def begin_transaction(self) -> _FakeTxn:
        self.transaction_count += 1
        return _FakeTxn()


class _FakeAsyncDoc:
    def __init__(self, state: dict[str, _FakeMap]) -> None:
        self._state = state

    async def __aenter__(self) -> _FakeDoc:
        return _FakeDoc(self._state)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def test_ydoc_defaults_create_node_scoped_nested_skill_state() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    fake_state = {
        "data": _FakeMap(
            {
                "nodes": {
                    "member-1": {
                        "weather": {
                            "current": {"city": "Paris"},
                        }
                    }
                }
            }
        )
    }
    fake_doc = _FakeDoc(fake_state)

    runtime._apply_ydoc_defaults_in_txn(
        fake_doc,
        _FakeTxn(),
        [
            {
                "skill": "weather_skill",
                "node_id": "member-1",
                "ydoc_defaults": {
                    "data/weather/current": {"city": "Moscow"},
                    "data/weather/cities": ["Moscow", "Paris"],
                },
            }
        ],
    )

    weather = fake_state["data"]["nodes"]["member-1"]["weather"]
    assert weather["current"] == {"city": "Paris"}
    assert weather["cities"] == ["Moscow", "Paris"]


def test_ydoc_defaults_keep_shared_skill_state_when_ui_owner_is_shared() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    fake_state = {
        "data": _FakeMap({})
    }
    fake_doc = _FakeDoc(fake_state)

    runtime._apply_ydoc_defaults_in_txn(
        fake_doc,
        _FakeTxn(),
        [
            {
                "skill": "demo_metrics_skill",
                "node_id": "member-1",
                "ui_owner": "shared",
                "ydoc_defaults": {
                    "data/demo_metrics/table": {"items": [{"id": "cpu"}]},
                },
            }
        ],
    )

    assert fake_state["data"]["demo_metrics"]["table"] == {"items": [{"id": "cpu"}]}
    assert "nodes" not in fake_state["data"] or "demo_metrics" not in fake_state["data"].get("nodes", {})


def test_describe_webspace_operational_state_exposes_manifest_and_current_scenario(monkeypatch) -> None:
    webspace_id = "phase2-describe"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt Lab",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_runtime")

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_runtime"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(
        webspace_runtime_module,
        "_scenario_exists_for_switch",
        lambda scenario_id, *, space: scenario_id in {"prompt_engineer_scenario", "prompt_engineer_runtime"},
    )

    result = asyncio.run(webspace_runtime_module.describe_webspace_operational_state(webspace_id))

    assert result.webspace_id == webspace_id
    assert result.kind == "dev"
    assert result.source_mode == "dev"
    assert result.stored_home_scenario == "prompt_engineer_scenario"
    assert result.effective_home_scenario == "prompt_engineer_scenario"
    assert result.current_scenario == "prompt_engineer_runtime"
    assert result.to_dict()["current_matches_home"] is False
    assert result.stored_home_scenario_exists is True
    assert result.current_scenario_exists is True
    assert result.degraded is False


def test_describe_webspace_projection_state_reports_active_layer(monkeypatch) -> None:
    webspace_id = "phase4-projection-describe"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Projection Lab",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_scenario")

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    class _Projections:
        def snapshot(self) -> dict[str, object]:
            return {
                "active_scenario_id": "prompt_engineer_scenario",
                "active_space": "workspace",
                "base_rule_count": 2,
                "scenario_rule_count": 1,
            }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace(projections=_Projections()))

    result = asyncio.run(webspace_runtime_module.describe_webspace_projection_state(webspace_id))

    assert result["webspace_id"] == webspace_id
    assert result["target_scenario"] == "prompt_engineer_scenario"
    assert result["target_space"] == "workspace"
    assert result["active_scenario"] == "prompt_engineer_scenario"
    assert result["active_space"] == "workspace"
    assert result["active_matches_target"] is True
    assert result["base_rule_count"] == 2
    assert result["scenario_rule_count"] == 1


def test_describe_webspace_projection_state_detects_space_mismatch(monkeypatch) -> None:
    webspace_id = "phase4-projection-dev-mismatch"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt Lab",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    class _Projections:
        def snapshot(self) -> dict[str, object]:
            return {
                "active_scenario_id": "prompt_engineer_scenario",
                "active_space": "workspace",
                "base_rule_count": 2,
                "scenario_rule_count": 1,
            }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace(projections=_Projections()))

    result = asyncio.run(webspace_runtime_module.describe_webspace_projection_state(webspace_id))

    assert result["target_space"] == "dev"
    assert result["active_space"] == "workspace"
    assert result["active_matches_target"] is False


def test_resolve_webspace_merges_webio_receivers_into_compact_runtime_contract(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    runtime = webspace_runtime_module.WebspaceScenarioRuntime()

    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="default",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={"desktop": {"pageSchema": {"id": "desktop", "layout": {"type": "single", "areas": [{"id": "main"}]}, "widgets": []}}},
            scenario_catalog={"apps": [], "widgets": []},
            scenario_registry={"modals": [], "widgets": []},
            overlay_snapshot={},
            live_state={},
            skill_decls=[
                {
                    "skill": "telemetry_skill",
                    "space": "default",
                    "node_id": "node-1",
                    "widgets": [
                        {
                            "id": "telemetry_widget",
                            "title": "Telemetry",
                            "dataSource": {"kind": "stream", "receiver": "telemetry_feed"},
                        }
                    ],
                    "webio": {
                        "receivers": {
                            "telemetry_feed": {
                                "mode": "append",
                                "collectionKey": "items",
                                "maxItems": 50,
                                "initialState": {"items": []},
                                "snapshotPolicy": "on_subscribe",
                                "ttlMs": 30000,
                                "sequenceField": "seq",
                                "updatedAtField": "updated_at",
                                "budget": {
                                    "maxPayloadBytes": 8192,
                                    "maxPublishHz": 2,
                                    "coalesceMs": 250,
                                    "maxFanout": 8,
                                },
                                "guardVisibility": {
                                    "degradedState": "Telemetry stream paused",
                                    "log": "service.telemetry_skill.runtime.log",
                                    "quarantine": True,
                                    "metric": "webio.stream.telemetry_feed.suppressed",
                                },
                                "route": {
                                    "kind": "stream",
                                    "surface": "widget:telemetry",
                                    "owner": "telemetry_skill",
                                    "firstPaint": "empty telemetry list",
                                    "recovery": "request bounded snapshot on subscribe",
                                    "updateSource": ["telemetry.sampled"],
                                },
                            }
                        }
                    },
                }
            ],
            desktop_scenarios=[],
        )
    )

    assert resolved.webio == {
        "receivers": {
            "telemetry_feed": {
                "id": "telemetry_feed",
                "mode": "append",
                "collectionKey": "items",
                "maxItems": 50,
                "initialState": {"items": []},
                "snapshotPolicy": "on_subscribe",
                "ttlMs": 30000,
                "sequenceField": "seq",
                "updatedAtField": "updated_at",
                "budget": {
                    "maxPayloadBytes": 8192,
                    "maxPublishHz": 2,
                    "coalesceMs": 250,
                    "maxFanout": 8,
                },
                "guardVisibility": {
                    "degradedState": "Telemetry stream paused",
                    "log": "service.telemetry_skill.runtime.log",
                    "quarantine": True,
                    "metric": "webio.stream.telemetry_feed.suppressed",
                },
                "route": {
                    "kind": "stream",
                    "surface": "widget:telemetry",
                    "owner": "telemetry_skill",
                    "firstPaint": "empty telemetry list",
                    "recovery": "request bounded snapshot on subscribe",
                    "updateSource": ["telemetry.sampled"],
                },
                "origin": "skill:telemetry_skill",
            }
        }
    }
    assert resolved.catalog["widgets"][0]["dataSource"]["nodeId"] == "node-1"


def test_resolver_cache_keys_use_precomputed_skill_decls_fingerprint(monkeypatch) -> None:
    def _fake_fingerprint(value):
        if isinstance(value, list) and value and isinstance(value[0], dict) and value[0].get("skill") == "large_skill":
            raise AssertionError("skill declarations should use the precomputed fingerprint")
        return "computed"

    monkeypatch.setattr(webspace_runtime_module, "_fingerprint_json_like", _fake_fingerprint)

    keys = webspace_runtime_module._resolver_cache_keys(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="default",
            scenario_id="web_desktop",
            source_mode="workspace",
            skill_decls=[{"skill": "large_skill", "apps": [{"id": "large"}]}],
            skill_decls_fingerprint="skill-fp-1",
        )
    )

    assert keys["skills"] == "skill-fp-1"


def test_collect_remote_skill_decls_uses_member_desktop_catalog_snapshot(monkeypatch) -> None:
    import adaos.services.registry.subnet_directory as subnet_directory_module

    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_names=["Hub"]),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")

    class _Directory:
        def list_known_nodes(self) -> list[dict[str, object]]:
            return [
                {
                    "node_id": "member-1",
                    "roles": ["member"],
                    "display_index": 2,
                    "accent_index": 5,
                    "runtime_projection": {
                        "node_names": ["Edge One"],
                        "primary_node_name": "Edge One",
                        "snapshot": {
                            "desktop_catalog": {
                                "apps": [
                                    {
                                        "id": "weather_skill",
                                        "title": "Weather",
                                        "launchModal": "weather_modal",
                                        "dataSource": {"kind": "y", "path": "data/weather/current"},
                                    }
                                ],
                                "widgets": [
                                    {
                                        "id": "infrastate",
                                        "title": "Infra State",
                                        "dataSource": {"kind": "stream", "receiver": "infrastate.realtime"},
                                        "actions": [
                                            {
                                                "type": "openModal",
                                                "params": {"modalId": "weather_modal"},
                                            },
                                            {
                                                "type": "callHost",
                                                "target": "skill.event.publish",
                                                "params": {
                                                    "_observe": {
                                                        "kind": "y",
                                                        "path": "data/weather/current",
                                                    }
                                                },
                                            },
                                        ],
                                    },
                                    {
                                        "id": "mediaserver_widget",
                                        "title": "Media Server",
                                        "dataSource": {"kind": "y", "path": "data/media/library"},
                                    },
                                ],
                                "registry": {
                                    "modals": {
                                        "weather_modal": {
                                            "title": "Weather Settings",
                                            "schema": {
                                                "widgets": [
                                                    {
                                                        "type": "selector",
                                                        "source": "data/weather/cities",
                                                    }
                                                ]
                                            },
                                        }
                                    }
                                },
                                "webio": {
                                    "receivers": {
                                        "infrastate.realtime": {
                                            "mode": "replace",
                                            "initialState": {"status": "idle"},
                                        }
                                    }
                                },
                                "ydoc_defaults": {
                                    "data/weather/current": {"city": "Moscow"},
                                    "data/media/library": {"items": []},
                                },
                            }
                        },
                    },
                }
            ]

    monkeypatch.setattr(subnet_directory_module, "get_directory", lambda: _Directory())

    runtime = webspace_runtime_module.WebspaceScenarioRuntime()
    decls = runtime._collect_remote_skill_decls()

    assert len(decls) == 1
    assert decls[0]["skill"] == "subnet.member.member-1"
    assert decls[0]["node_id"] == "member-1"
    assert decls[0]["apps"][0]["node_label"] == "Edge One"
    assert decls[0]["apps"][0]["node_compact_label"] == "N2"
    assert decls[0]["apps"][0]["id"] == "node:member-1:weather_skill"
    assert decls[0]["apps"][0]["launchModal"] == "node:member-1:weather_modal"
    assert decls[0]["apps"][0]["dataSource"]["path"] == "data/nodes/member-1/weather/current"
    assert decls[0]["widgets"][0]["node_label"] == "Edge One"
    assert isinstance(decls[0]["widgets"][0]["node_color"], str) and decls[0]["widgets"][0]["node_color"]
    assert decls[0]["widgets"][0]["id"] == "node:member-1:infrastate"
    assert decls[0]["widgets"][0]["dataSource"]["nodeId"] == "member-1"
    assert decls[0]["widgets"][0]["actions"][0]["params"]["modalId"] == "node:member-1:weather_modal"
    assert decls[0]["widgets"][0]["actions"][1]["params"]["_observe"]["path"] == "data/nodes/member-1/weather/current"
    assert decls[0]["widgets"][1]["id"] == "node:member-1:mediaserver_widget"
    assert decls[0]["widgets"][1]["dataSource"]["path"] == "data/nodes/member-1/media/library"
    assert decls[0]["registry"]["modals"]["node:member-1:weather_modal"]["schema"]["widgets"][0]["source"] == "data/nodes/member-1/weather/cities"
    assert decls[0]["webio"]["receivers"]["infrastate.realtime"]["mode"] == "replace"
    assert "nodeId" not in decls[0]["webio"]["receivers"]["infrastate.realtime"]
    assert decls[0]["ydoc_defaults"]["data/nodes/member-1/weather/current"] == {"city": "Moscow"}
    assert decls[0]["ydoc_defaults"]["data/nodes/member-1/media/library"] == {"items": []}


def test_collect_remote_skill_decls_overrides_member_catalog_label_with_device_inventory_name(monkeypatch) -> None:
    directory_module = types.ModuleType("adaos.services.registry.subnet_directory")
    directory_module.get_directory = lambda: SimpleNamespace(
        list_known_nodes=lambda: [
            {
                "node_id": "member-1",
                "roles": ["member"],
                "display_index": 3,
                "runtime_projection": {
                    "snapshot": {
                        "node_id": "member-1",
                        "desktop_catalog": {
                            "apps": [
                                {
                                    "id": "infrastate_app",
                                    "title": "Infra State",
                                    "node_label": "adaos2",
                                }
                            ],
                            "widgets": [
                                {
                                    "id": "infrastate_widget",
                                    "title": "Infra State",
                                    "node_label": "adaos2",
                                }
                            ],
                            "registry": {
                                "widgets": {
                                    "infrastate_widget": {
                                        "id": "infrastate_widget",
                                        "node_label": "adaos2",
                                    }
                                }
                            },
                        }
                    }
                },
            }
        ]
    )
    device_inventory_module = types.ModuleType("adaos.services.device_inventory")
    device_inventory_module.list_devices = lambda **_kwargs: [
        {
            "identity": {"node_id": "member-1"},
            "policy": {
                "present": True,
                "managed_state": "managed",
                "admission_policy": "allow",
                "effective_name": "Mediapoint",
                "display_name": "Mediapoint",
            },
        }
    ]
    monkeypatch.setitem(sys.modules, "adaos.services.registry.subnet_directory", directory_module)
    monkeypatch.setitem(sys.modules, "adaos.services.device_inventory", device_inventory_module)
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_settings=SimpleNamespace(node_names=[])),
    )
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-1")

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
    decls = runtime._collect_remote_skill_decls()

    assert len(decls) == 1
    assert decls[0]["apps"][0]["node_label"] == "Mediapoint"
    assert decls[0]["widgets"][0]["node_label"] == "Mediapoint"
    assert decls[0]["registry"]["widgets"]["node:member-1:infrastate_widget"]["node_label"] == "Mediapoint"


def test_resolve_webspace_preserves_live_remote_entries_during_projection_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_names=["Hub"]),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Hub",
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "#4E79A7",
        },
    )

    runtime = webspace_runtime_module.WebspaceScenarioRuntime()
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="desktop",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={"desktop": {"pageSchema": {"id": "desktop"}}},
            scenario_catalog={"apps": [{"id": "hub_app", "title": "Hub App"}], "widgets": []},
            scenario_registry={"modals": ["apps_catalog"], "widgets": []},
            overlay_snapshot={},
            live_state={
                "application": {
                    "modals": {
                        "node:member-1:weather_modal": {
                            "title": "Weather Settings",
                        }
                    }
                },
                "catalog": {
                    "apps": [
                        {"id": "node:member-1:weather_skill", "title": "Weather", "node_id": "member-1"},
                    ],
                    "widgets": [
                        {"id": "node:member-1:infrastate", "title": "Infra State", "node_id": "member-1"},
                    ],
                },
                "registry": {
                    "modals": ["apps_catalog", "node:member-1:weather_modal"],
                    "widgets": [],
                },
                "desktop": {},
                "routing": {},
            },
            skill_decls=[],
            desktop_scenarios=[],
        )
    )

    app_ids = [str(item.get("id") or "") for item in resolved.catalog["apps"]]
    widget_ids = [str(item.get("id") or "") for item in resolved.catalog["widgets"]]
    assert "hub_app" in app_ids
    assert "node:member-1:weather_skill" in app_ids
    assert "node:member-1:infrastate" in widget_ids
    assert "node:member-1:weather_modal" in resolved.application["modals"]
    assert "node:member-1:weather_modal" in resolved.registry["modals"]


def test_detached_member_node_ids_include_detached_and_denied_policies(monkeypatch) -> None:
    from adaos.services import device_inventory

    monkeypatch.setattr(
        device_inventory,
        "list_devices",
        lambda *, kind=None, include_detached=False: [
            {
                "identity": {"node_id": "member-detached"},
                "policy": {"managed_state": "detached", "admission_policy": "detached"},
            },
            {
                "identity": {"node_id": "member-denied"},
                "policy": {"managed_state": "denied", "admission_policy": "deny"},
            },
            {
                "identity": {"node_id": "member-active"},
                "policy": {"managed_state": "managed", "admission_policy": "allow"},
            },
        ],
    )

    assert webspace_runtime_module._detached_member_node_ids() == {"member-detached", "member-denied"}


def test_resolve_webspace_drops_live_remote_entries_for_detached_members(monkeypatch) -> None:
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="hub-1", node_names=["Hub"]),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Hub",
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "#4E79A7",
        },
    )
    monkeypatch.setattr(webspace_runtime_module, "_detached_member_node_ids", lambda: {"member-1"})

    runtime = webspace_runtime_module.WebspaceScenarioRuntime()
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="desktop",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={"desktop": {"pageSchema": {"id": "desktop"}}},
            scenario_catalog={"apps": [{"id": "hub_app", "title": "Hub App"}], "widgets": []},
            scenario_registry={"modals": ["apps_catalog"], "widgets": []},
            overlay_snapshot={},
            live_state={
                "application": {
                    "modals": {
                        "node:member-1:weather_modal": {
                            "title": "Weather Settings",
                        }
                    }
                },
                "catalog": {
                    "apps": [
                        {"id": "node:member-1:weather_skill", "title": "Weather", "node_id": "member-1"},
                    ],
                    "widgets": [
                        {"id": "node:member-1:infrastate", "title": "Infra State", "node_id": "member-1"},
                    ],
                },
                "registry": {
                    "modals": ["apps_catalog", "node:member-1:weather_modal"],
                    "widgets": [],
                },
                "desktop": {},
                "routing": {},
            },
            skill_decls=[],
            desktop_scenarios=[],
        )
    )

    app_ids = [str(item.get("id") or "") for item in resolved.catalog["apps"]]
    widget_ids = [str(item.get("id") or "") for item in resolved.catalog["widgets"]]
    assert "hub_app" in app_ids
    assert "node:member-1:weather_skill" not in app_ids
    assert "node:member-1:infrastate" not in widget_ids
    assert "node:member-1:weather_modal" not in resolved.application["modals"]
    assert "node:member-1:weather_modal" not in resolved.registry["modals"]


def _patch_switch_dependencies(monkeypatch, *, state: dict[str, _FakeMap] | None = None) -> dict[str, _FakeMap]:
    fake_state = state or {"ui": _FakeMap(), "registry": _FakeMap(), "data": _FakeMap()}
    fake_ctx = get_ctx()
    rebuilds: list[str] = []
    workflows: list[tuple[str, str]] = []
    sync_listing_calls: list[bool] = []

    def _record_fake_rebuild(runtime, webspace_id: str, scenario_id: str | None = None):
        scenario = str(scenario_id or "prompt_engineer_scenario")
        rebuilds.append(webspace_id)
        runtime._last_rebuild_timings_ms = {
            "collect_inputs": 1.0,
            "resolve": 2.0,
            "apply_structure": 1.25,
            "apply_interactive": 1.5,
            "apply": 3.0,
            "to_registry_entry": 0.5,
            "total": 6.5,
        }
        runtime._last_rebuild_ydoc_timings_ms = {
            "payload_only": 0.0,
            "total": 6.5,
        }
        runtime._last_resolver_debug = {
            "source": "loader:workspace",
            "legacy_fallback": False,
            "cache_hit": False,
        }
        runtime._last_apply_summary = {
            "branch_count": 6,
            "changed_branches": 3,
            "unchanged_branches": 3,
            "failed_branches": 0,
            "changed_paths": ["ui.application", "data.catalog", "registry.merged"],
            "defaults_failed": False,
            "phases": {
                "structure": {
                    "branch_count": 2,
                    "changed_branches": 2,
                    "unchanged_branches": 0,
                    "failed_branches": 0,
                    "changed_paths": ["ui.application", "registry.merged"],
                },
                "interactive": {
                    "branch_count": 4,
                    "changed_branches": 1,
                    "unchanged_branches": 3,
                    "failed_branches": 0,
                    "changed_paths": ["data.catalog"],
                },
            },
        }
        runtime._last_apply_phase_timings_ms = {
            "structure": 1.25,
            "interactive": 1.5,
        }
        runtime._last_materialized_payload = {
            "ui": {"current_scenario": scenario, "application": {}},
            "data": {"catalog": {"apps": []}, "installed": {}, "desktop": {}, "webio": {}, "routing": {}},
            "registry": {"merged": {}},
            "runtime": {"environment": {"materialization": {"scenario_id": scenario}}},
        }
        return SimpleNamespace(
            webspace_id=webspace_id,
            scenario_id=scenario,
            apps=[{"id": f"app:{scenario}"}],
            widgets=[],
        )

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
        return _record_fake_rebuild(self, webspace_id, kwargs.get("initial_scenario_id"))

    async def _fake_materialize(self, webspace_id: str, **kwargs):  # noqa: ARG002
        return _record_fake_rebuild(self, webspace_id, kwargs.get("scenario_id"))

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):
        workflows.append((scenario_id, webspace_id))
        return None

    async def _fake_sync_listing() -> None:
        sync_listing_calls.append(True)

    async def _fake_live_refresh(webspace_id: str, **kwargs):
        payload = kwargs.get("materialized_payload")
        payload_map = payload if isinstance(payload, dict) else {}
        scenario = str(payload_map.get("scenario_id") or "prompt_engineer_scenario")
        fake_state.setdefault("ui", _FakeMap())["current_scenario"] = scenario
        return {
            "ok": True,
            "webspace_id": webspace_id,
            "materialized_payload_applied": True,
            "materialized_payload": {
                "ready": True,
                "apply_summary": {
                    "branch_count": 6,
                    "changed_branches": 3,
                    "unchanged_branches": 3,
                },
            },
        }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: True)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: {
            "id": scenario_id,
            "ui": {"application": {"desktop": {"pageSchema": {"id": f"page-{scenario_id}"}}}},
            "registry": {"modals": [f"modal:{space}:{scenario_id}"]},
            "catalog": {"apps": [{"id": f"app:{scenario_id}"}]},
            "data": {"status": {"scenario": scenario_id, "space": space}},
        },
    )
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "resolve_materialized_payload_async", _fake_materialize)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(apply_materialized_payload_to_live_room=_fake_live_refresh),
    )
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: fake_ctx)
    fake_state["_meta"] = _FakeMap({"rebuilds": rebuilds, "workflows": workflows, "listing_syncs": sync_listing_calls})
    return fake_state


def test_switch_webspace_scenario_can_persist_home_scenario(monkeypatch) -> None:
    webspace_id = "phase2-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Home",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = _patch_switch_dependencies(monkeypatch)

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            set_home=True,
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "prompt_engineer_scenario"
    assert fake_state["ui"]["current_scenario"] == "prompt_engineer_scenario"
    assert fake_state["_meta"]["rebuilds"] == [webspace_id]
    assert fake_state["_meta"]["workflows"] == []
    assert fake_state["_meta"]["listing_syncs"] == [True]
    assert result["ok"] is True
    assert result["set_home"] is True
    assert result["home_scenario"] == "prompt_engineer_scenario"
    assert result["scenario_switch_mode"] == "pointer_only"
    assert isinstance(result["timings_ms"], dict)
    assert "validate_scenario" in result["timings_ms"]
    assert result["timings_ms"]["defer_switch_pointer"] == 0.0
    assert "write_switch_pointer" not in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]
    assert "wait_rebuild" in result["timings_ms"]
    assert isinstance(result["rebuild_timings_ms"], dict)
    assert "projection_refresh" in result["rebuild_timings_ms"]
    assert "semantic_rebuild" in result["rebuild_timings_ms"]
    assert isinstance(result["semantic_rebuild_timings_ms"], dict)
    assert result["semantic_rebuild_timings_ms"]["resolve"] == 2.0
    assert result["apply_summary"]["changed_branches"] == 3
    assert isinstance(result["phase_timings_ms"], dict)
    assert "time_to_pointer_update" in result["phase_timings_ms"]
    assert "time_to_first_structure" in result["phase_timings_ms"]
    assert "time_to_interactive_focus" in result["phase_timings_ms"]
    assert "time_to_full_hydration" in result["phase_timings_ms"]
    assert result["phase_timings_ms"]["time_to_first_structure"] == result["phase_timings_ms"]["time_to_full_hydration"]
    assert result["phase_timings_ms"]["time_to_interactive_focus"] == result["phase_timings_ms"]["time_to_full_hydration"]


def test_switch_webspace_scenario_defers_locked_secondary_overlay(monkeypatch) -> None:
    webspace_id = "phase2-locked-overlay"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Locked Overlay",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    _patch_switch_dependencies(monkeypatch)
    deferred: list[tuple[str, str, str]] = []

    def _locked_overlay(
        _webspace_id: str,
        _scenario_id: str,
        *,
        busy_timeout_ms: int | None = None,
    ) -> None:
        assert busy_timeout_ms == 200
        raise sqlite3.OperationalError("database is locked")

    def _defer(webspace_id: str, scenario_id: str, *, reason: str):
        deferred.append((webspace_id, scenario_id, reason))
        return {"accepted": True, "pending": True, "reason": reason}

    monkeypatch.setattr(webspace_runtime_module.workspace_index, "set_workspace_current_scenario_overlay", _locked_overlay)
    monkeypatch.setattr(webspace_runtime_module.workspace_index, "defer_workspace_current_scenario_overlay", _defer)

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "media_center",
        )
    )

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["overlay_persistence"]["state"] == "deferred"
    assert result["overlay_persistence"]["pending"] is True
    assert deferred == [(webspace_id, "media_center", "scenario_switch.sqlite_locked")]


def test_switch_webspace_scenario_does_not_defer_non_lock_database_error(monkeypatch) -> None:
    webspace_id = "phase2-overlay-io-error"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Broken Overlay",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    _patch_switch_dependencies(monkeypatch)

    def _broken_overlay(
        _webspace_id: str,
        _scenario_id: str,
        *,
        busy_timeout_ms: int | None = None,
    ) -> None:
        assert busy_timeout_ms == 200
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(webspace_runtime_module.workspace_index, "set_workspace_current_scenario_overlay", _broken_overlay)

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "media_center",
        )
    )

    assert result["ok"] is False
    assert result["accepted"] is False
    assert result["error"] == "scenario_switch_failed"


def test_switch_webspace_scenario_keeps_home_unchanged_by_default_for_dev_webspace(monkeypatch) -> None:
    webspace_id = "phase2-dev-pointer-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt Lab",
        kind="dev",
        source_mode="dev",
        home_scenario="web_desktop",
    )

    fake_state = _patch_switch_dependencies(monkeypatch)

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "web_desktop"
    assert fake_state["ui"]["current_scenario"] == "prompt_engineer_scenario"
    assert fake_state["_meta"]["listing_syncs"] == []
    assert result["set_home"] is False
    assert result["home_scenario"] == "web_desktop"


def test_switch_webspace_scenario_keeps_home_unchanged_for_regular_workspace(monkeypatch) -> None:
    webspace_id = "phase2-workspace-no-auto-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Workspace",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    _patch_switch_dependencies(monkeypatch)

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "web_desktop"
    assert result["set_home"] is False
    assert result["home_scenario"] == "web_desktop"


def test_switch_webspace_scenario_default_pointer_only_can_schedule_background_rebuild(monkeypatch) -> None:
    webspace_id = "phase2-scenario-fast"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Fast",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = _patch_switch_dependencies(
        monkeypatch,
        state={
            "ui": _FakeMap(
                {
                    "current_scenario": "web_desktop",
                    "application": {"desktop": {"pageSchema": {"id": "old-page"}}},
                    "scenarios": {
                        "web_desktop": {"application": {"desktop": {"pageSchema": {"id": "old-cache"}}}}
                    },
                }
            ),
            "registry": _FakeMap(
                {
                    "merged": {"modals": ["old-modal"]},
                    "scenarios": {"web_desktop": {"modals": ["old-cache-modal"]}},
                }
            ),
            "data": _FakeMap(
                {
                    "catalog": {"apps": [{"id": "old-app"}]},
                    "status": {"scenario": "web_desktop"},
                    "scenarios": {"web_desktop": {"catalog": {"apps": [{"id": "old-cache-app"}]}}},
                }
            ),
        },
    )
    scheduled: list[tuple[str, str, str | None]] = []

    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, *, scenario_id, scenario_resolution, switch_mode=None, switch_timings_ms=None, **_kwargs: scheduled.append(
            (webspace_id, scenario_id, scenario_resolution, switch_mode, isinstance(switch_timings_ms, dict))
        ),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["ok"] is True
    assert result["background_rebuild"] is True
    assert result["scenario_switch_mode"] == "pointer_only"
    assert scheduled == [(webspace_id, "prompt_engineer_scenario", "explicit", "pointer_only", True)]
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert fake_state["ui"]["application"]["desktop"]["pageSchema"]["id"] == "old-page"
    assert fake_state["registry"]["merged"]["modals"] == ["old-modal"]
    assert fake_state["data"]["catalog"]["apps"] == [{"id": "old-app"}]
    assert fake_state["data"]["status"] == {"scenario": "web_desktop"}
    assert "prompt_engineer_scenario" not in fake_state["ui"]["scenarios"]
    assert "prompt_engineer_scenario" not in fake_state["registry"]["scenarios"]
    assert "prompt_engineer_scenario" not in fake_state["data"]["scenarios"]
    assert isinstance(result["timings_ms"], dict)
    assert "validate_scenario" in result["timings_ms"]
    assert result["timings_ms"]["defer_switch_pointer"] == 0.0
    assert "write_switch_pointer" not in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]
    assert "materialize_switch_payload" not in result["timings_ms"]
    assert "schedule_background_rebuild" in result["timings_ms"]
    assert isinstance(result["phase_timings_ms"], dict)
    assert "time_to_accept" in result["phase_timings_ms"]
    assert "time_to_pointer_update" not in result["phase_timings_ms"]
    assert "time_to_full_hydration" not in result["phase_timings_ms"]


def test_switch_webspace_scenario_defers_selector_until_atomic_materialization(monkeypatch) -> None:
    webspace_id = "phase2-scenario-atomic-selector"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Atomic Selector",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    fake_state = _patch_switch_dependencies(
        monkeypatch,
        state={
            "ui": _FakeMap({"current_scenario": "web_desktop"}),
            "registry": _FakeMap(),
            "data": _FakeMap(),
        },
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, **_kwargs: scheduled.append(webspace_id),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["accepted"] is True
    assert result["selector_commit_mode"] == "materialization_transaction"
    assert result["timings_ms"]["defer_switch_pointer"] == 0.0
    assert "write_switch_pointer" not in result["timings_ms"]
    assert "time_to_pointer_update" not in result["phase_timings_ms"]
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert scheduled == [webspace_id]


def test_switch_webspace_scenario_compat_env_is_ignored_and_keeps_pointer_only_contract(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SWITCH_COMPAT_CACHE_WRITES", "1")

    webspace_id = "phase2-scenario-compat-rollback"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Compat Rollback",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = _patch_switch_dependencies(
        monkeypatch,
        state={
            "ui": _FakeMap(
                {
                    "current_scenario": "web_desktop",
                    "application": {"desktop": {"pageSchema": {"id": "old-page"}}},
                    "scenarios": {
                        "web_desktop": {"application": {"desktop": {"pageSchema": {"id": "old-cache"}}}}
                    },
                }
            ),
            "registry": _FakeMap(
                {
                    "merged": {"modals": ["old-modal"]},
                    "scenarios": {"web_desktop": {"modals": ["old-cache-modal"]}},
                }
            ),
            "data": _FakeMap(
                {
                    "catalog": {"apps": [{"id": "old-app"}]},
                    "status": {"scenario": "web_desktop"},
                    "scenarios": {"web_desktop": {"catalog": {"apps": [{"id": "old-cache-app"}]}}},
                }
            ),
        },
    )
    scheduled: list[tuple[str, str, str | None]] = []

    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, *, scenario_id, scenario_resolution, switch_mode=None, switch_timings_ms=None, **_kwargs: scheduled.append(
            (webspace_id, scenario_id, scenario_resolution, switch_mode, isinstance(switch_timings_ms, dict))
        ),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["ok"] is True
    assert result["background_rebuild"] is True
    assert result["scenario_switch_mode"] == "pointer_only"
    assert scheduled == [(webspace_id, "prompt_engineer_scenario", "explicit", "pointer_only", True)]
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert fake_state["ui"]["application"]["desktop"]["pageSchema"]["id"] == "old-page"
    assert fake_state["registry"]["merged"]["modals"] == ["old-modal"]
    assert fake_state["data"]["catalog"]["apps"] == [{"id": "old-app"}]
    assert fake_state["data"]["status"] == {"scenario": "web_desktop"}
    assert "prompt_engineer_scenario" not in fake_state["ui"]["scenarios"]
    assert "prompt_engineer_scenario" not in fake_state["registry"]["scenarios"]
    assert "prompt_engineer_scenario" not in fake_state["data"]["scenarios"]
    assert isinstance(result["timings_ms"], dict)
    assert "validate_scenario" in result["timings_ms"]
    assert result["timings_ms"]["defer_switch_pointer"] == 0.0
    assert "write_switch_pointer" not in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]
    assert "materialize_switch_payload" not in result["timings_ms"]
    assert "schedule_background_rebuild" in result["timings_ms"]
    assert isinstance(result["phase_timings_ms"], dict)
    assert "time_to_accept" in result["phase_timings_ms"]
    assert "time_to_full_hydration" not in result["phase_timings_ms"]

def test_switch_webspace_scenario_deprecated_pointer_first_env_keeps_atomic_contract(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_POINTER_SCENARIO_SWITCH", "1")

    webspace_id = "phase-pointer-switch"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Pointer Switch",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = _patch_switch_dependencies(
        monkeypatch,
        state={
            "ui": _FakeMap(
                {
                    "current_scenario": "web_desktop",
                    "application": {"desktop": {"pageSchema": {"id": "old-page"}}},
                    "scenarios": {
                        "web_desktop": {"application": {"desktop": {"pageSchema": {"id": "old-cache"}}}}
                    },
                }
            ),
            "registry": _FakeMap(
                {
                    "merged": {"modals": ["old-modal"]},
                    "scenarios": {"web_desktop": {"modals": ["old-cache-modal"]}},
                }
            ),
            "data": _FakeMap(
                {
                    "catalog": {"apps": [{"id": "old-app"}]},
                    "scenarios": {"web_desktop": {"catalog": {"apps": [{"id": "old-cache-app"}]}}},
                }
            ),
        },
    )
    scheduled: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: True)

    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, *, scenario_id, scenario_resolution, switch_mode=None, switch_timings_ms=None, **_kwargs: scheduled.append(
            (webspace_id, scenario_id, scenario_resolution, switch_mode, isinstance(switch_timings_ms, dict))
        ),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["ok"] is True
    assert result["background_rebuild"] is True
    assert result["scenario_switch_mode"] == "pointer_only"
    assert result["selector_commit_mode"] == "materialization_transaction"
    assert scheduled == [(webspace_id, "prompt_engineer_scenario", "explicit", "pointer_only", True)]
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert fake_state["ui"]["application"]["desktop"]["pageSchema"]["id"] == "old-page"
    assert "prompt_engineer_scenario" not in fake_state["ui"]["scenarios"]
    assert fake_state["registry"]["merged"]["modals"] == ["old-modal"]
    assert "prompt_engineer_scenario" not in fake_state["registry"]["scenarios"]
    assert fake_state["data"]["catalog"]["apps"] == [{"id": "old-app"}]
    assert "prompt_engineer_scenario" not in fake_state["data"]["scenarios"]
    assert isinstance(result["timings_ms"], dict)
    assert "validate_scenario" in result["timings_ms"]
    assert result["timings_ms"]["defer_switch_pointer"] == 0.0
    assert "write_switch_pointer" not in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]
    assert "materialize_switch_payload" not in result["timings_ms"]
    assert isinstance(result["phase_timings_ms"], dict)
    assert "time_to_pointer_update" not in result["phase_timings_ms"]


def test_switch_webspace_scenario_deprecated_pointer_env_does_not_load_content(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_POINTER_SCENARIO_SWITCH", "1")

    webspace_id = "phase-pointer-no-content-load"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Pointer Switch",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "web_desktop"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: True)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: (_ for _ in ()).throw(AssertionError("should not load scenario content")),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "_read_effective_materialization_scenario",
        lambda _webspace_id: (_ for _ in ()).throw(AssertionError("should not probe materialization for a different target scenario")),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, **kwargs: None,
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["accepted"] is True
    assert result["scenario_switch_mode"] == "pointer_only"
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert "validate_scenario" in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]
    assert "read_materialization_scenario_before" not in result["timings_ms"]


def test_switch_webspace_scenario_deprecated_pointer_env_keeps_dev_home_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_POINTER_SCENARIO_SWITCH", "1")

    webspace_id = "phase-pointer-dev-auto-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Pointer Home",
        kind="dev",
        source_mode="dev",
        home_scenario="web_desktop",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "web_desktop"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    sync_listing_calls: list[bool] = []
    scheduled: list[tuple[str, str]] = []

    async def _fake_sync_listing() -> None:
        sync_listing_calls.append(True)

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: True)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, *, scenario_id, **kwargs: scheduled.append((webspace_id, scenario_id)),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "web_desktop"
    assert fake_state["ui"]["current_scenario"] == "web_desktop"
    assert sync_listing_calls == []
    assert scheduled == [(webspace_id, "prompt_engineer_scenario")]
    assert result["set_home"] is False
    assert result["home_scenario"] == "web_desktop"
    assert result["scenario_switch_mode"] == "pointer_only"


def test_switch_webspace_scenario_compat_env_is_ignored_and_does_not_load_content(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SWITCH_COMPAT_CACHE_WRITES", "1")

    webspace_id = "phase2-materialize-validate-order"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Materialize Validate",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "web_desktop"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    calls: list[str] = []

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(
        webspace_runtime_module,
        "_scenario_exists_for_switch",
        lambda scenario_id, *, space: (calls.append(f"validate:{space}:{scenario_id}") or True),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: (_ for _ in ()).throw(AssertionError("should not load scenario content")),
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "_schedule_scenario_switch_rebuild",
        lambda webspace_id, **kwargs: None,
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["accepted"] is True
    assert "validate:workspace:prompt_engineer_scenario" in calls
    assert result["scenario_switch_mode"] == "pointer_only"
    assert "validate_scenario" in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]


def test_switch_webspace_scenario_compat_env_ignored_missing_scenario_fails_without_loading_content(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SWITCH_COMPAT_CACHE_WRITES", "1")

    webspace_id = "phase2-materialize-missing"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Materialize Missing",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "web_desktop"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: False)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: (_ for _ in ()).throw(AssertionError("should not load missing scenario content")),
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "missing_scenario",
            wait_for_rebuild=False,
        )
    )

    assert result["accepted"] is False
    assert result["error"] == "scenario_not_found"
    assert result["scenario_switch_mode"] == "pointer_only"
    assert "validate_scenario" in result["timings_ms"]
    assert "load_scenario" not in result["timings_ms"]


def test_switch_webspace_scenario_same_current_ready_skips_rebuild_and_only_persists_home(monkeypatch) -> None:
    webspace_id = "phase2-same-current-noop"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Same Current",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_scenario")

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    sync_listing_calls: list[bool] = []

    async def _fake_sync_listing() -> None:
        sync_listing_calls.append(True)

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: (_ for _ in ()).throw(AssertionError("should not reload scenario content")),
    )
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "rebuild_webspace_async",
        lambda self, webspace_id: (_ for _ in ()).throw(AssertionError("should not rebuild current scenario")),
    )
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="ready",
        pending=False,
        scenario_id="prompt_engineer_scenario",
        resolver={"source": "loader:workspace", "legacy_fallback": False, "cache_hit": True},
        apply_summary={
            "branch_count": 6,
            "changed_branches": 0,
            "unchanged_branches": 6,
            "failed_branches": 0,
            "changed_paths": [],
            "defaults_failed": False,
        },
        timings_ms={"projection_refresh": 1.5, "semantic_rebuild": 2.5, "total": 4.0},
        semantic_rebuild_timings_ms={"collect_inputs": 0.5, "resolve": 1.0, "apply": 1.5, "total": 3.0},
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            set_home=True,
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "prompt_engineer_scenario"
    assert sync_listing_calls == [True]
    assert result["accepted"] is True
    assert result["switch_skipped"] is True
    assert result["skip_reason"] == "already_current_ready"
    assert result["background_rebuild"] is False
    assert result["apply_summary"]["unchanged_branches"] == 6
    assert result["rebuild_timings_ms"]["total"] == 4.0
    assert "load_scenario" not in result["timings_ms"]
    assert "wait_rebuild" not in result["timings_ms"]


def test_fresh_doc_rebuild_runs_in_bounded_materialization_executor(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def _fake_run_cpu(func, *args, **kwargs):
        calls.append((getattr(func, "__name__", ""), args, kwargs))
        return {
            "entry": webspace_runtime_module.WebUIRegistryEntry(scenario_id="prompt_engineer_scenario"),
            "snapshot_update": b"snapshot",
            "state_vector": b"state-vector",
            "rebuild_timings_ms": {"collect_inputs": 3.0, "resolve": 4.0, "apply": 5.0, "total": 12.0},
            "resolver_debug": {"source": "loader:dev", "cache_hit": False},
            "apply_summary": {"changed_branches": 2, "unchanged_branches": 6},
            "apply_phase_timings_ms": {"structure": 2.0, "interactive": 3.0},
            "ydoc_timings_ms": {
                "seed_initial_scenario": 1.0,
                "in_doc_rebuild": 12.0,
                "encode_snapshot": 2.0,
                "total": 15.0,
            },
        }

    monkeypatch.setattr(webspace_runtime_module, "_run_materialization_cpu", _fake_run_cpu)

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
    entry = asyncio.run(
        runtime.rebuild_webspace_async(
            "desktop-dev",
            fresh_doc=True,
            replace_ystore_snapshot=False,
            initial_scenario_id="prompt_engineer_scenario",
            materialization_identity={"key_hash": "test-key"},
        )
    )

    assert entry.scenario_id == "prompt_engineer_scenario"
    assert calls == [
        (
            "_rebuild_fresh_doc_snapshot_sync",
            ("desktop-dev",),
            {
                "request_id": None,
                "initial_scenario_id": "prompt_engineer_scenario",
                "materialization_identity": {"key_hash": "test-key"},
            },
        )
    ]
    assert runtime._last_rebuild_timings_ms == {
        "collect_inputs": 3.0,
        "resolve": 4.0,
        "apply": 5.0,
        "total": 12.0,
    }
    assert runtime._last_rebuild_ydoc_timings_ms is not None
    assert runtime._last_rebuild_ydoc_timings_ms["in_doc_rebuild"] == 12.0
    assert runtime._last_rebuild_ydoc_timings_ms["encode_snapshot"] == 2.0
    assert runtime._last_apply_summary == {"changed_branches": 2, "unchanged_branches": 6}


def test_materialization_cpu_worker_count_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_MATERIALIZATION_CPU_WORKERS", raising=False)
    assert webspace_runtime_module._materialization_cpu_workers() == 1
    monkeypatch.setenv("ADAOS_MATERIALIZATION_CPU_WORKERS", "0")
    assert webspace_runtime_module._materialization_cpu_workers() == 1
    monkeypatch.setenv("ADAOS_MATERIALIZATION_CPU_WORKERS", "8")
    assert webspace_runtime_module._materialization_cpu_workers() == 4
    monkeypatch.setenv("ADAOS_MATERIALIZATION_CPU_WORKERS", "invalid")
    assert webspace_runtime_module._materialization_cpu_workers() == 1


def test_materialized_payload_apply_replaces_existing_effective_branches() -> None:
    Y = pytest.importorskip("y_py")
    ydoc = Y.YDoc()
    ui_map = ydoc.get_map("ui")
    data_map = ydoc.get_map("data")
    registry_map = ydoc.get_map("registry")
    with ydoc.begin_transaction() as txn:
        ui_map.set(txn, "current_scenario", "web_desktop")
        ui_map.set(
            txn,
            "application",
            {
                "desktop": {"pageSchema": {"id": "old-page"}},
                "modals": {"old_modal": {}},
            },
        )
        data_map.set(txn, "catalog", {"apps": [{"id": "old-app"}], "widgets": []})
        data_map.set(txn, "installed", {"apps": ["old-app"], "widgets": []})
        data_map.set(txn, "desktop", {"pageSchema": {"id": "old-page"}})
        registry_map.set(txn, "merged", {"modals": ["old_modal"], "widgets": []})

    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="desktop-dev",
        scenario_id="prompt_engineer_scenario",
        source_mode="dev",
        application={
            "desktop": {"pageSchema": {"id": "new-page", "widgets": []}},
            "modals": {"apps_catalog": {}, "widgets_catalog": {}},
        },
        catalog={"apps": [{"id": "new-app"}], "widgets": [{"id": "new-widget"}]},
        registry={"modals": ["apps_catalog", "widgets_catalog"], "widgets": ["new-widget"]},
        installed={"apps": ["new-app"], "widgets": ["new-widget"]},
        desktop={"pageSchema": {"id": "new-page", "widgets": []}},
        webio={"receivers": {}},
        routing={"routes": {}},
        skill_decls=[],
    )
    payload = webspace_runtime_module._resolved_outputs_to_materialized_payload(resolved)  # noqa: SLF001

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
    entry = runtime.apply_materialized_payload_to_doc(
        ydoc,
        "desktop-dev",
        payload,
        materialization_identity={"key_hash": "test-key", "key": "test-key"},
    )

    assert entry.scenario_id == "prompt_engineer_scenario"
    assert ui_map.get("current_scenario") == "prompt_engineer_scenario"
    application = ui_map.get("application")
    assert application["desktop"]["pageSchema"]["id"] == "new-page"
    assert "old_modal" not in application["modals"]
    assert data_map.get("catalog")["apps"][0]["id"] == "new-app"
    assert data_map.get("installed")["apps"] == ["new-app"]
    assert registry_map.get("merged")["modals"] == ["apps_catalog", "widgets_catalog"]
    assert runtime._last_rebuild_timings_ms["load_materialized_payload"] >= 0.0
    assert runtime._last_apply_summary["changed_branches"] >= 1
    assert runtime._last_apply_summary["selector_changed"] is True
    assert runtime._last_apply_summary["selector_reasserted"] is True
    assert runtime._last_apply_summary["selector_apply_mode"] == "reasserted"
    assert runtime._last_apply_summary["transaction_total"] == 1
    assert "apply_combined_transaction" in runtime._last_apply_phase_timings_ms


def test_materialized_payload_keeps_only_declarations_needed_for_ydoc_defaults() -> None:
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="desktop-dev",
        scenario_id="web_desktop",
        source_mode="dev",
        application={"desktop": {"pageSchema": {"id": "desktop"}}},
        catalog={"apps": [], "widgets": []},
        registry={},
        installed={"apps": [], "widgets": []},
        desktop={},
        webio={},
        routing={},
        skill_decls=[
            {
                "skill": "weather_skill",
                "node_id": "node-1",
                "ui_owner": "node",
                "apps": [{"id": "large-app", "schema": {"unused": ["x"] * 100}}],
                "widgets": [{"id": "large-widget"}],
                "handlers": {"unused": "module.handler"},
                "ydoc_defaults": {"data/weather/current": {"city": "Moscow"}},
            },
            {
                "skill": "catalog_only_skill",
                "apps": [{"id": "catalog-only"}],
            },
        ],
    )

    payload = webspace_runtime_module._resolved_outputs_to_materialized_payload(resolved)  # noqa: SLF001

    assert payload["skill_decls"] == [
        {
            "skill": "weather_skill",
            "node_id": "node-1",
            "ui_owner": "node",
            "ydoc_defaults": {"data/weather/current": {"city": "Moscow"}},
        }
    ]
    assert "apps" not in payload["skill_decls"][0]
    assert "widgets" not in payload["skill_decls"][0]
    assert "handlers" not in payload["skill_decls"][0]


def test_materialized_worker_cache_round_trips_disk(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE", "1")

    webspace_runtime_module._RUNTIME.cache.clear_materialized_webspaces()  # noqa: SLF001
    identity = {
        "key_hash": "disk-cache-key",
        "key": "disk-cache-key",
        "webspace_id": "desktop-dev",
        "scenario_id": "todo_scenario",
    }
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="desktop-dev",
        scenario_id="todo_scenario",
        source_mode="dev",
        application={"desktop": {"pageSchema": {"id": "page", "widgets": []}}},
        catalog={"apps": [{"id": "todo"}], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": ["todo"], "widgets": []},
        desktop={"pageSchema": {"id": "page", "widgets": []}},
        webio={"receivers": {}},
        routing={"routes": {}},
        skill_decls=[],
    )
    payload = webspace_runtime_module._resolved_outputs_to_materialized_payload(resolved)  # noqa: SLF001
    worker_result = {
        "snapshot_update": b"snapshot-update",
        "state_vector": b"state-vector",
        "materialized_payload": payload,
        "rebuild_timings_ms": {"total": 123.0},
        "resolver_debug": {"source": "test"},
        "ydoc_timings_ms": {"total": 456.0},
    }

    webspace_runtime_module._remember_materialized_worker_result(identity, worker_result)  # noqa: SLF001
    webspace_runtime_module._RUNTIME.cache.clear_materialized_webspaces()  # noqa: SLF001

    cached = webspace_runtime_module._get_cached_materialized_worker_result(identity)  # noqa: SLF001

    assert cached is not None
    assert cached["snapshot_update"] == b"snapshot-update"
    assert cached["state_vector"] == b"state-vector"
    assert cached["materialization_cache"]["hit"] is True
    assert cached["materialization_cache"]["source"] == "disk"
    assert cached["rebuild_timings_ms"]["cached_original_total"] == 123.0

    dropped = webspace_runtime_module._drop_materialized_cache_for_webspace(  # noqa: SLF001
        "desktop-dev",
        scenario_id="todo_scenario",
    )
    assert dropped == {"memory": 1, "disk": 1}
    assert not list(tmp_path.glob("*.json"))


def test_payload_only_materialized_worker_cache_round_trips_without_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE", "1")

    webspace_runtime_module._RUNTIME.cache.clear_materialized_webspaces()  # noqa: SLF001
    identity = {
        "key_hash": "payload-cache-key",
        "key": "payload-cache-key",
        "webspace_id": "desktop-dev",
        "scenario_id": "todo_scenario",
    }
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="desktop-dev",
        scenario_id="todo_scenario",
        source_mode="dev",
        application={"desktop": {"pageSchema": {"id": "page", "widgets": []}}},
        catalog={"apps": [{"id": "todo"}], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": ["todo"], "widgets": []},
        desktop={"pageSchema": {"id": "page", "widgets": []}},
        webio={"receivers": {}},
        routing={"routes": {}},
        skill_decls=[],
    )
    payload = webspace_runtime_module._resolved_outputs_to_materialized_payload(resolved)  # noqa: SLF001
    worker_result = {
        "materialized_payload": payload,
        "rebuild_timings_ms": {"total": 123.0},
        "resolver_debug": {"source": "test"},
        "apply_summary": {"payload_only": True},
        "ydoc_timings_ms": {"total": 456.0},
    }

    webspace_runtime_module._remember_materialized_worker_result(  # noqa: SLF001
        identity,
        worker_result,
        cache_mode="payload_only",
        require_snapshot=False,
    )
    webspace_runtime_module._RUNTIME.cache.clear_materialized_webspaces()  # noqa: SLF001

    cached = webspace_runtime_module._get_cached_materialized_worker_result(  # noqa: SLF001
        identity,
        cache_mode="payload_only",
        require_snapshot=False,
    )
    fresh_doc_cached = webspace_runtime_module._get_cached_materialized_worker_result(identity)  # noqa: SLF001

    assert cached is not None
    assert cached["snapshot_update"] == b""
    assert cached["materialized_payload"]["scenario_id"] == "todo_scenario"
    assert cached["materialization_cache"]["hit"] is True
    assert cached["materialization_cache"]["source"] == "disk"
    assert cached["materialization_cache"]["mode"] == "payload_only"
    assert cached["apply_summary"]["payload_only"] is True
    assert fresh_doc_cached is None

    dropped = webspace_runtime_module._drop_materialized_cache_for_webspace(  # noqa: SLF001
        "desktop-dev",
        scenario_id="todo_scenario",
    )
    assert dropped == {"memory": 1, "disk": 1}


def test_materialization_cache_invalidation_without_scenario_drops_whole_webspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE", "1")

    webspace_runtime_module._RUNTIME.cache.clear_materialized_webspaces()  # noqa: SLF001
    webspace_runtime_module._RUNTIME.cache.put_skill_source_fingerprint(  # noqa: SLF001
        "workspace",
        123.0,
        "stale",
    )
    for scenario_id in ("builder", "web_desktop"):
        identity = {
            "key_hash": f"{scenario_id}-cache-key",
            "key": f"{scenario_id}-cache-key",
            "webspace_id": "desktop",
            "scenario_id": scenario_id,
        }
        resolved = webspace_runtime_module.WebspaceResolverOutputs(
            webspace_id="desktop",
            scenario_id=scenario_id,
            source_mode="workspace",
            application={"desktop": {"pageSchema": {"id": scenario_id, "widgets": []}}},
            catalog={"apps": [], "widgets": []},
            registry={"modals": [], "widgets": []},
            installed={"apps": [], "widgets": []},
            desktop={"pageSchema": {"id": scenario_id, "widgets": []}},
            webio={"receivers": {}},
            routing={"routes": {}},
            skill_decls=[],
        )
        payload = webspace_runtime_module._resolved_outputs_to_materialized_payload(resolved)  # noqa: SLF001
        webspace_runtime_module._remember_materialized_worker_result(  # noqa: SLF001
            identity,
            {
                "snapshot_update": b"snapshot",
                "state_vector": b"state-vector",
                "materialized_payload": payload,
                "rebuild_timings_ms": {"total": 1.0},
                "resolver_debug": {"source": "test"},
                "ydoc_timings_ms": {"total": 1.0},
            },
        )

    result = webspace_runtime_module.invalidate_webspace_materialization_cache(
        "desktop",
        reason="skill_activate:test",
        action="skill_activation_sync",
        source_of_truth="skill_runtime",
    )

    assert result["materialization"]["cache_drop_scope"] == "webspace"
    assert result["materialization"]["cache_dropped"] == {"memory": 2, "disk": 2}
    assert result["materialization"]["disk_cache_drop_deferred"] is False
    assert webspace_runtime_module._RUNTIME.cache.materialized_webspace_count() == 0  # noqa: SLF001
    assert webspace_runtime_module._RUNTIME.cache.get_skill_source_fingerprint("workspace") is None  # noqa: SLF001
    assert not list(tmp_path.glob("*.json"))


def test_skill_runtime_cache_invalidation_defers_disk_scan(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_WEBSPACE_MATERIALIZATION_DISK_CACHE", "1")
    identity = {
        "key_hash": "deferred-cache-key",
        "key": "deferred-cache-key",
        "webspace_id": "desktop-deferred-cache",
        "scenario_id": "web_desktop",
    }
    value = {"identity": identity, "materialized_payload": {"scenario_id": "web_desktop"}}
    webspace_runtime_module._RUNTIME.cache.put_materialized_webspace(  # noqa: SLF001
        "deferred-cache-key",
        value,
        max_entries=8,
        max_bytes=1024,
    )
    assert webspace_runtime_module._RUNTIME.disk_cache.store_record("deferred-cache-key", value) is True  # noqa: SLF001

    result = webspace_runtime_module.invalidate_webspace_materialization_cache(
        "desktop-deferred-cache",
        reason="skill_activate:test",
        action="skill_activation_sync",
        source_of_truth="skill_runtime",
        defer_disk=True,
    )

    assert result["materialization"]["cache_dropped"] == {"memory": 1, "disk": 0}
    assert result["materialization"]["disk_cache_drop_deferred"] is True
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert (
        webspace_runtime_module._drop_materialized_disk_cache_for_webspace(  # noqa: SLF001
            "desktop-deferred-cache"
        )
        == 1
    )
    assert not list(tmp_path.glob("*.json"))


def test_skill_runtime_rebuild_runs_deferred_disk_scan_off_owner_loop(monkeypatch) -> None:
    webspace_id = "desktop-deferred-cache-worker"
    owner_thread_id = threading.get_ident()
    disk_thread_ids: list[int] = []
    rebuilds: list[str] = []

    monkeypatch.setattr(webspace_runtime_module, "_skill_runtime_rebuild_debounce_s", lambda: 0.0)
    monkeypatch.setattr(
        webspace_runtime_module,
        "invalidate_webspace_materialization_cache",
        lambda *_args, **_kwargs: {},
    )

    def _drop_disk(target: str, **_kwargs) -> int:
        assert target == webspace_id
        disk_thread_ids.append(threading.get_ident())
        return 2

    async def _rebuild(target: str, **_kwargs) -> None:
        rebuilds.append(target)

    monkeypatch.setattr(webspace_runtime_module, "_drop_materialized_disk_cache_for_webspace", _drop_disk)
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _rebuild)

    async def _exercise() -> None:
        webspace_runtime_module.schedule_skill_runtime_rebuild(
            webspace_id=webspace_id,
            action="skill_activation_sync",
            source_of_truth="skill_runtime",
            reason="weather_skill",
        )
        task = webspace_runtime_module._RUNTIME.tasks.active_task(  # noqa: SLF001
            webspace_runtime_module._RUNTIME.tasks.SKILL_RUNTIME,  # noqa: SLF001
            webspace_id,
        )
        assert task is not None
        await task

    asyncio.run(_exercise())

    assert disk_thread_ids and disk_thread_ids[0] != owner_thread_id
    assert rebuilds == [webspace_id]


def test_switch_webspace_scenario_same_current_ready_rebuilds_mismatched_materialization(monkeypatch) -> None:
    webspace_id = "phase2-same-current-mismatch"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Same Current Mismatch",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "web_desktop")

    fake_state = _patch_switch_dependencies(
        monkeypatch,
        state={
            "ui": _FakeMap(
                {
                    "current_scenario": "web_desktop",
                    "application": {"desktop": {"pageSchema": {"id": "prompt_ide"}}},
                }
            ),
            "registry": _FakeMap(),
            "data": _FakeMap(),
            "runtime": _FakeMap(
                {
                    "environment": {
                        "materialization": {
                            "scenario_id": "prompt_engineer_scenario",
                            "required_branches": ["ui.application", "data.catalog"],
                        }
                    }
                }
            ),
        },
    )
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="ready",
        pending=False,
        scenario_id="web_desktop",
        resolver={"source": "loader:workspace", "legacy_fallback": False, "cache_hit": True},
        apply_summary={
            "branch_count": 6,
            "changed_branches": 0,
            "unchanged_branches": 6,
            "failed_branches": 0,
            "changed_paths": [],
            "defaults_failed": False,
        },
        timings_ms={"projection_refresh": 1.5, "semantic_rebuild": 2.5, "total": 4.0},
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "web_desktop",
            wait_for_rebuild=True,
        )
    )

    assert result["accepted"] is True
    assert result.get("switch_skipped") is not True
    assert result["background_rebuild"] is False
    assert fake_state["_meta"]["rebuilds"] == [webspace_id]
    assert "read_materialization_scenario_before" in result["timings_ms"]


def test_switch_webspace_scenario_same_current_pending_rebuild_is_deduplicated(monkeypatch) -> None:
    webspace_id = "phase2-same-current-pending"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Same Current Pending",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_scenario")

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    sync_listing_calls: list[bool] = []

    async def _fake_sync_listing() -> None:
        sync_listing_calls.append(True)

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_load_scenario_switch_content",
        lambda scenario_id, *, space: (_ for _ in ()).throw(AssertionError("should not reload scenario content")),
    )
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "rebuild_webspace_async",
        lambda self, webspace_id: (_ for _ in ()).throw(AssertionError("should not rebuild while pending")),
    )
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="running",
        pending=True,
        background=True,
        scenario_id="prompt_engineer_scenario",
        action="scenario_switch_rebuild",
        resolver={"source": "loader:workspace", "legacy_fallback": False, "cache_hit": False},
        apply_summary={
            "branch_count": 6,
            "changed_branches": 1,
            "unchanged_branches": 5,
            "failed_branches": 0,
            "changed_paths": ["ui.application"],
            "defaults_failed": False,
        },
        phase_timings_ms={"time_to_accept": 3.0, "time_to_full_hydration": 12.0},
    )

    result = asyncio.run(
        webspace_runtime_module.switch_webspace_scenario(
            webspace_id,
            "prompt_engineer_scenario",
            set_home=True,
            wait_for_rebuild=False,
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "prompt_engineer_scenario"
    assert sync_listing_calls == [True]
    assert result["accepted"] is True
    assert result["switch_skipped"] is True
    assert result["skip_reason"] == "already_pending_rebuild"
    assert result["background_rebuild"] is True
    assert result["apply_summary"]["changed_branches"] == 1
    assert result["phase_timings_ms"]["time_to_full_hydration"] == 12.0
    assert "load_scenario" not in result["timings_ms"]
    assert "wait_rebuild" not in result["timings_ms"]


def test_background_scenario_switch_rebuild_superseded_request_keeps_newer_status(monkeypatch) -> None:
    webspace_id = "phase2-background-supersede"
    events: dict[str, asyncio.Event] = {}

    async def _fake_complete(
        webspace_id: str,
        *,
        scenario_id: str,
        scenario_resolution: str | None,
        request_id: str | None = None,
        switch_mode: str | None = None,
        switch_timings_ms=None,
    ) -> dict[str, object]:
        gate = events.setdefault(scenario_id, asyncio.Event())
        await gate.wait()
        webspace_runtime_module._set_webspace_rebuild_status_if_current(
            webspace_id,
            request_id,
            status="ready",
            pending=False,
            background=True,
            scenario_id=scenario_id,
            switch_mode=switch_mode,
            finished_at=time.time(),
            phase_timings_ms={"time_to_full_hydration": 5.0},
        )
        return {
            "ok": True,
            "accepted": True,
            "webspace_id": webspace_id,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "request_id": request_id,
            "switch_mode": switch_mode,
            "timings_ms": {"projection_refresh": 1.0, "semantic_rebuild": 2.0, "total": 3.0},
            "switch_timings_ms": switch_timings_ms,
            "semantic_rebuild_timings_ms": {"collect_inputs": 0.5, "resolve": 1.0, "apply": 1.5, "total": 3.5},
            "phase_timings_ms": {"time_to_full_hydration": 5.0},
        }

    monkeypatch.setattr(webspace_runtime_module, "_complete_scenario_switch_rebuild", _fake_complete)
    _clear_scenario_switch_task_state()

    async def _run() -> dict[str, object]:
        webspace_runtime_module._schedule_scenario_switch_rebuild(
            webspace_id,
            scenario_id="scenario_a",
            scenario_resolution="explicit",
            switch_mode="pointer_first",
            switch_timings_ms={"total": 1.0},
        )
        await asyncio.sleep(0)
        first = webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)
        assert first["scenario_id"] == "scenario_a"
        first_request_id = first["request_id"]

        webspace_runtime_module._schedule_scenario_switch_rebuild(
            webspace_id,
            scenario_id="scenario_b",
            scenario_resolution="explicit",
            switch_mode="pointer_first",
            switch_timings_ms={"total": 2.0},
        )
        await asyncio.sleep(0)
        second = webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)
        assert second["scenario_id"] == "scenario_b"
        assert second["request_id"] != first_request_id

        events["scenario_b"].set()
        state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
        task = state.get_task(state.SCENARIO_SWITCH, webspace_id)
        assert task is not None
        await task
        return webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)

    final = asyncio.run(_run())

    assert final["status"] == "ready"
    assert final["scenario_id"] == "scenario_b"
    assert final["switch_mode"] == "pointer_only"
    assert isinstance(final["phase_timings_ms"], dict)
    assert "time_to_full_hydration" in final["phase_timings_ms"]


def test_scenario_switch_rebuild_can_defer_listing_sync(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_INLINE_LISTING_SYNC", "0")
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "0")
    sync_calls: list[bool] = []

    async def _fake_sync_listing() -> None:
        sync_calls.append(True)

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_materialize(self, webspace_id: str, **kwargs):  # noqa: ARG001
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        self._last_materialized_payload = {
            "scenario_id": "prompt_engineer_scenario",
            "application": {"desktop": {"pageSchema": {"id": "page"}}},
            "catalog": {"apps": [], "widgets": []},
            "registry": {},
            "installed": {"apps": [], "widgets": []},
            "desktop": {},
            "webio": {},
            "routing": {},
        }
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):  # noqa: ARG002
        return None

    async def _fake_live_refresh(webspace_id: str, **_kwargs):
        return {"ok": True, "webspace_id": webspace_id}

    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_async",
        _fake_materialize,
    )
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(apply_materialized_payload_to_live_room=_fake_live_refresh),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=lambda _webspace_id: None),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-deferred-listing",
            action="scenario_switch_rebuild",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario_switch",
            reseed_from_scenario=False,
        )
    )

    assert result["accepted"] is True
    assert sync_calls == []
    assert "scenario_switch_sync_listing" not in result["timings_ms"]
    assert result["timings_ms"]["scenario_switch_sync_listing_deferred"] == 0.0


def test_skill_lifecycle_rebuild_uses_process_isolated_payload(monkeypatch) -> None:
    materialize_calls: list[dict[str, object]] = []
    direct_rebuild_calls: list[str] = []
    live_refresh_calls: list[dict[str, object]] = []

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_materialize(self, webspace_id: str, **kwargs):
        materialize_calls.append({"webspace_id": webspace_id, **kwargs})
        self._last_rebuild_timings_ms = {"payload_worker": 10.0, "total": 10.0}
        self._last_rebuild_ydoc_timings_ms = {"worker_process": 10.0, "total": 10.0}
        self._last_worker_diagnostics = {"mode": "payload_only"}
        self._last_apply_summary = {"payload_only": True}
        self._last_materialized_payload = {
            "scenario_id": "web_desktop",
            "application": {"desktop": {"pageSchema": {"id": "desktop"}}},
            "catalog": {"apps": [], "widgets": []},
            "registry": {},
            "installed": {"apps": [], "widgets": []},
            "desktop": {},
            "webio": {},
            "routing": {},
        }
        return SimpleNamespace(scenario_id="web_desktop", apps=[], widgets=[])

    async def _unexpected_direct_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        direct_rebuild_calls.append(webspace_id)
        raise AssertionError("skill lifecycle rebuild must not run synchronously in the owner loop")

    async def _fake_live_refresh(webspace_id: str, **kwargs):
        live_refresh_calls.append({"webspace_id": webspace_id, **kwargs})
        return {"ok": True, "webspace_id": webspace_id}

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_async",
        _fake_materialize,
    )
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "rebuild_webspace_async",
        _unexpected_direct_rebuild,
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(apply_materialized_payload_to_live_room=_fake_live_refresh),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-skill-lifecycle-worker",
            action="skill_activation_sync",
            scenario_id="web_desktop",
            scenario_resolution="explicit",
            source_of_truth="skill_runtime",
        )
    )

    assert result["accepted"] is True
    assert direct_rebuild_calls == []
    assert len(materialize_calls) == 1
    assert materialize_calls[0]["isolate_process"] is True
    assert len(live_refresh_calls) == 1
    assert live_refresh_calls[0]["materialized_payload"]["scenario_id"] == "web_desktop"


def test_deferred_webspace_listing_sync_coalesces(monkeypatch) -> None:
    sync_calls: list[str] = []

    async def _fake_sync_listing() -> None:
        sync_calls.append("sync")
        await asyncio.sleep(0)

    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    state.clear_tasks(state.WEBSPACE_LISTING, cancel=True)

    async def _run() -> tuple[dict[str, object], dict[str, object]]:
        first = webspace_runtime_module._schedule_webspace_listing_sync(reason="test")
        second = webspace_runtime_module._schedule_webspace_listing_sync(reason="test")
        task = state.get_task(state.WEBSPACE_LISTING, "listing")
        assert task is not None
        await task
        return first, second

    try:
        first, second = asyncio.run(_run())
    finally:
        state.clear_tasks(state.WEBSPACE_LISTING, cancel=True)

    assert sync_calls == ["sync"]
    assert first["coalesced"] is False
    assert second["coalesced"] is True


def test_phase3_stale_rebuild_request_does_not_apply_effective_branches() -> None:
    webspace_id = "phase3-stale-apply-guard"
    state = webspace_runtime_module._RUNTIME.tasks  # noqa: SLF001
    state.clear_records(state.WEBSPACE_REBUILD_STATUS)
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="running",
        pending=True,
        background=True,
        request_id="req-new",
        action="scenario_switch_rebuild",
        scenario_id="web_desktop",
    )

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
    fake_state = {
        "ui": _CountingMap(),
        "registry": _CountingMap(),
        "data": _CountingMap(),
    }
    fake_doc = _FakeDoc(fake_state)
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id=webspace_id,
        scenario_id="prompt_engineer_scenario",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "prompt"}}},
        catalog={"apps": [{"id": "prompt"}], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": ["prompt"], "widgets": []},
        desktop={"installed": {"apps": ["prompt"], "widgets": []}, "pageSchema": {"id": "prompt"}},
        routing={"routes": {}},
        skill_decls=[],
    )
    inputs = webspace_runtime_module.WebspaceResolverInputs(
        webspace_id=webspace_id,
        scenario_id="prompt_engineer_scenario",
        source_mode="workspace",
        compatibility_cache_presence={
            "scenario_ui_application": False,
            "scenario_registry_entry": False,
            "scenario_catalog": False,
        },
    )

    with pytest.raises(webspace_runtime_module._StaleRebuildRequestError):
        runtime._apply_resolved_state_in_doc(
            fake_doc,
            webspace_id,
            resolved,
            inputs=inputs,
            expected_request_id="req-old",
        )

    assert fake_state["ui"] == {}
    assert fake_state["data"] == {}
    assert fake_state["registry"] == {}
    assert fake_state["ui"].set_count == 0
    assert fake_state["data"].set_count == 0
    assert fake_state["registry"].set_count == 0


def test_rebuild_webspace_async_prefers_live_room_ydoc_session(monkeypatch) -> None:
    captured: dict[str, object] = {}
    fake_state = {
        "ui": _FakeMap(),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    class _CapturedAsyncDoc:
        async def __aenter__(self) -> _FakeDoc:
            return _FakeDoc(fake_state)

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    def _fake_async_get_ydoc(
        webspace_id: str,
        *,
        read_only: bool = False,
        prefer_live_room: bool = False,
        timings=None,
        timing_prefix: str = "",
    ):
        captured["webspace_id"] = webspace_id
        captured["read_only"] = read_only
        captured["prefer_live_room"] = prefer_live_room
        captured["timings_is_dict"] = isinstance(timings, dict)
        captured["timing_prefix"] = timing_prefix
        return _CapturedAsyncDoc()

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", _fake_async_get_ydoc)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "_rebuild_in_doc",
        lambda self, ydoc, webspace_id, expected_request_id=None, materialization_identity=None: {
            "webspace_id": webspace_id,
            "expected_request_id": expected_request_id,
            "doc": ydoc,
        },
    )

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(ctx=SimpleNamespace())
    result = asyncio.run(runtime.rebuild_webspace_async("default", request_id="req-live-room"))

    assert result["webspace_id"] == "default"
    assert result["expected_request_id"] == "req-live-room"
    assert captured == {
        "webspace_id": "default",
        "read_only": False,
        "prefer_live_room": True,
        "timings_is_dict": True,
        "timing_prefix": "",
    }


def test_go_home_webspace_uses_manifest_home_scenario(monkeypatch) -> None:
    webspace_id = "phase2-go-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt Lab",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    captured: list[tuple[str, str, bool]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool = False,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, scenario_id, set_home))
        return {"ok": True, "webspace_id": webspace_id, "scenario_id": scenario_id, "set_home": set_home}

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", lambda scenario_id, *, space: True)

    result = asyncio.run(webspace_runtime_module.go_home_webspace(webspace_id))

    assert captured == [(webspace_id, "prompt_engineer_scenario", False)]
    assert result["scenario_id"] == "prompt_engineer_scenario"
    assert result["action"] == "go_home"
    assert result["source_of_truth"] == "manifest_home_scenario"
    assert result["scenario_resolution"] == "manifest_home"


def test_go_home_webspace_preflight_falls_back_to_web_desktop_when_home_missing(monkeypatch) -> None:
    webspace_id = "phase2-go-home-fallback"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Phase 2 Go Home Fallback",
        kind="workspace",
        source_mode="workspace",
        home_scenario="infrascope",
    )

    captured: list[tuple[str, str, bool]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool = False,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, scenario_id, set_home))
        return {"ok": True, "webspace_id": webspace_id, "scenario_id": scenario_id, "set_home": set_home}

    def _scenario_exists(scenario_id: str, *, space: str) -> bool:  # noqa: ARG001
        return scenario_id == "web_desktop"

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)
    monkeypatch.setattr(webspace_runtime_module, "_scenario_exists_for_switch", _scenario_exists)

    result = asyncio.run(webspace_runtime_module.go_home_webspace(webspace_id))

    assert captured == [(webspace_id, "web_desktop", False)]
    assert result["scenario_id"] == "web_desktop"
    assert result["scenario_resolution"] == "manifest_home_fallback"
    assert result["validation"]["requested_scenario_id"] == "infrascope"
    assert result["validation"]["resolved_scenario_id"] == "web_desktop"
    assert result["validation"]["fallback_applied"] is True


def test_phase3_resolve_rebuild_target_prefers_current_before_manifest_home(monkeypatch) -> None:
    webspace_id = "phase3-resolve-current-first"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Resolve Current First",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_scenario")

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))

    state, scenario_id, scenario_resolution = asyncio.run(
        webspace_runtime_module._resolve_rebuild_scenario_target(webspace_id, None)
    )

    assert state.webspace_id == webspace_id
    assert scenario_id == "prompt_engineer_scenario"
    assert scenario_resolution == "current_scenario"


def test_phase3_reload_target_preserves_manifest_home_before_current(monkeypatch) -> None:
    webspace_id = "phase3-resolve-home-first"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Resolve Home First",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))

    state, scenario_id, scenario_resolution = asyncio.run(
        webspace_runtime_module._resolve_reload_scenario_target(webspace_id, None)
    )

    assert state.webspace_id == webspace_id
    assert scenario_id == "web_desktop"
    assert scenario_resolution == "manifest_home"


def test_sync_webspace_listing_never_opens_non_live_documents(monkeypatch) -> None:
    listing = [{"id": "preview-live", "title": "Preview"}]
    mutated: list[str] = []
    owner_thread_id = threading.get_ident()
    listing_thread_ids: list[int] = []

    def _listing() -> list[dict[str, str]]:
        listing_thread_ids.append(threading.get_ident())
        return listing

    monkeypatch.setattr(webspace_runtime_module, "_webspace_listing", _listing)
    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "workspace_catalog_version",
        lambda: 7,
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "async_get_ydoc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not open YDoc")),
    )

    def _mutate(webspace_id, _mutator, **_kwargs):
        if webspace_id != "preview-live":
            return False
        mutated.append(webspace_id)
        return True

    monkeypatch.setattr(webspace_runtime_module, "mutate_live_room", _mutate)

    result = asyncio.run(
        webspace_runtime_module._sync_webspace_listing(["preview-live", "stale-preview"])
    )

    assert mutated == ["preview-live"]
    assert listing_thread_ids
    assert all(worker_thread_id != owner_thread_id for worker_thread_id in listing_thread_ids)
    assert result["catalog_version"] == 7
    assert result["updated"] == ["preview-live"]
    assert result["skipped_not_live"] == ["stale-preview"]


def test_webspace_listing_includes_local_node_metadata(monkeypatch) -> None:
    webspace_id = "phase2-listing-node-meta"
    ensure_workspace(webspace_id)

    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "member-01")
    monkeypatch.setattr(webspace_runtime_module, "_local_node_label", lambda: "Edge Member")
    monkeypatch.setattr(
        webspace_runtime_module,
        "_local_node_display",
        lambda: {
            "node_label": "Edge Member",
            "node_compact_label": "N1",
            "node_index": 1,
            "node_color": "#F28E2B",
        },
    )
    monkeypatch.setattr(webspace_runtime_module, "_try_read_live_current_scenario", lambda _webspace_id: "web_desktop")

    listing = webspace_runtime_module._webspace_listing()
    item = next(entry for entry in listing if entry["id"] == webspace_id)

    assert item["node_id"] == "member-01"
    assert item["node_label"] == "Edge Member"


def test_webspace_service_set_home_scenario_updates_manifest(monkeypatch) -> None:
    webspace_id = "phase2-set-home-service"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Service Home",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    async def _fake_sync_listing() -> None:
        return None

    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)

    info = asyncio.run(webspace_runtime_module.WebspaceService().set_home_scenario(webspace_id, "prompt_engineer_scenario"))

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "prompt_engineer_scenario"
    assert info is not None
    assert info.home_scenario == "prompt_engineer_scenario"


def test_webspace_service_update_metadata_updates_title_and_home(monkeypatch) -> None:
    webspace_id = "phase2-update-metadata"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Workspace Before",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )

    async def _fake_sync_listing() -> None:
        return None

    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)

    info = asyncio.run(
        webspace_runtime_module.WebspaceService().update_metadata(
            webspace_id,
            title="Workspace After",
            home_scenario="prompt_engineer_scenario",
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.title == "Workspace After"
    assert row.home_scenario == "prompt_engineer_scenario"
    assert info is not None
    assert info.title == "Workspace After"
    assert info.home_scenario == "prompt_engineer_scenario"


def test_ensure_dev_webspace_for_scenario_reuses_existing_dev_space() -> None:
    webspace_id = "phase2-dev-existing"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt IDE",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    result = asyncio.run(webspace_runtime_module.ensure_dev_webspace_for_scenario("prompt_engineer_scenario"))

    assert result["ok"] is True
    assert result["created"] is False
    assert result["webspace_id"] == webspace_id
    assert result["home_scenario"] == "prompt_engineer_scenario"


def test_ensure_dev_webspace_for_scenario_creates_missing_dev_space(monkeypatch) -> None:
    async def _fake_seed(webspace_id: str, scenario_id: str, *, dev=None) -> None:  # noqa: ARG001
        return None

    async def _fake_sync_listing() -> None:
        return None

    monkeypatch.setattr(webspace_runtime_module, "_seed_webspace_from_scenario", _fake_seed)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)

    result = asyncio.run(webspace_runtime_module.ensure_dev_webspace_for_scenario("phase2_fresh_scenario"))

    row = get_workspace(str(result["webspace_id"]))
    assert row is not None
    assert row.is_dev is True
    assert row.home_scenario == "phase2_fresh_scenario"
    assert result["created"] is True
    assert result["kind"] == "dev"
    assert result["source_mode"] == "dev"


def test_desktop_scenario_set_forwards_set_home_flag(monkeypatch) -> None:
    captured: list[tuple[str, str, bool]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool = False,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, scenario_id, set_home, wait_for_rebuild))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)

    asyncio.run(
        webspace_runtime_module._on_desktop_scenario_set(
            {"webspace_id": "phase2-forward", "scenario_id": "prompt_engineer_scenario", "set_home": True}
        )
    )

    assert captured == [("phase2-forward", "prompt_engineer_scenario", True, False)]


def test_desktop_scenario_set_preserves_explicit_false(monkeypatch) -> None:
    captured: list[tuple[str, str, bool | None]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool | None = None,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, scenario_id, set_home, wait_for_rebuild))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)

    asyncio.run(
        webspace_runtime_module._on_desktop_scenario_set(
            {"webspace_id": "phase2-forward", "scenario_id": "prompt_engineer_scenario", "set_home": False}
        )
    )

    assert captured == [("phase2-forward", "prompt_engineer_scenario", False, False)]


def test_desktop_scenario_set_forwards_explicit_wait_for_rebuild(monkeypatch) -> None:
    captured: list[tuple[str, str, bool | None, bool]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        *,
        set_home: bool | None = None,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, scenario_id, set_home, wait_for_rebuild))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)

    asyncio.run(
        webspace_runtime_module._on_desktop_scenario_set(
            {
                "webspace_id": "phase2-forward",
                "scenario_id": "prompt_engineer_scenario",
                "wait_for_rebuild": True,
            }
        )
    )

    assert captured == [("phase2-forward", "prompt_engineer_scenario", None, True)]


def test_webspace_go_home_event_forwards_explicit_wait_for_rebuild(monkeypatch) -> None:
    captured: list[tuple[str, bool]] = []

    async def _fake_go_home(
        webspace_id: str,
        *,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, wait_for_rebuild))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "go_home_webspace", _fake_go_home)

    asyncio.run(
        webspace_runtime_module._on_webspace_go_home(
            {
                "webspace_id": "phase2-home",
                "wait_for_rebuild": True,
            }
        )
    )

    assert captured == [("phase2-home", True)]


def test_webspace_go_home_event_defaults_to_background_rebuild(monkeypatch) -> None:
    captured: list[tuple[str, bool]] = []

    async def _fake_go_home(
        webspace_id: str,
        *,
        wait_for_rebuild: bool = True,
    ) -> dict[str, object]:
        captured.append((webspace_id, wait_for_rebuild))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "go_home_webspace", _fake_go_home)

    asyncio.run(
        webspace_runtime_module._on_webspace_go_home(
            {
                "webspace_id": "phase2-home",
            }
        )
    )

    assert captured == [("phase2-home", False)]


def test_reload_preview_webspaces_for_scenario_project(monkeypatch) -> None:
    scenario_id = "prompt_engineer_scenario"
    preview_a = "dev-prompt-a"
    preview_b = "dev-prompt-b"
    ensure_workspace(preview_a)
    ensure_workspace(preview_b)
    set_workspace_manifest(
        preview_a,
        display_name="DEV: Prompt A",
        kind="dev",
        source_mode="dev",
        home_scenario=scenario_id,
    )
    set_workspace_manifest(
        preview_b,
        display_name="DEV: Prompt B",
        kind="dev",
        source_mode="dev",
        home_scenario="other_scenario",
    )
    from adaos.services.workspaces.relations import BUILDER_PROJECT_PREVIEW, WebspaceRelationshipRegistry

    relationships = WebspaceRelationshipRegistry.from_context()
    relationships.ensure(
        "builder-source-a",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id=scenario_id,
        legacy_target_webspace_id=preview_a,
    )
    relationships.ensure(
        "builder-source-b",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="other_scenario",
        legacy_target_webspace_id=preview_b,
    )
    ensure_workspace("stale-dev-preview")
    set_workspace_manifest(
        "stale-dev-preview",
        display_name="DEV: stale",
        kind="dev",
        source_mode="dev",
        home_scenario=scenario_id,
    )

    captured: list[tuple[str, str, str]] = []

    async def _fake_reload(webspace_id: str, *, scenario_id: str | None = None, action: str = "reload") -> dict[str, object]:
        captured.append((webspace_id, str(scenario_id), action))
        return {"ok": True, "webspace_id": webspace_id, "scenario_id": scenario_id, "action": action}

    monkeypatch.setattr(webspace_runtime_module, "reload_webspace_from_scenario", _fake_reload)

    result = asyncio.run(
        webspace_runtime_module.reload_preview_webspaces_for_project(
            "scenario",
            scenario_id,
            reason="project_meta_updated",
        )
    )

    assert captured == [(preview_a, scenario_id, "reload")]
    assert result["accepted"] is True
    assert result["reloaded_webspaces"] == [preview_a]


def test_reload_preview_webspaces_for_skill_dependency(monkeypatch) -> None:
    preview = "dev-scenario-preview"
    ensure_workspace(preview)
    set_workspace_manifest(
        preview,
        display_name="DEV: Scenario Preview",
        kind="dev",
        source_mode="dev",
        home_scenario="demo_scenario",
    )
    from adaos.services.workspaces.relations import BUILDER_PROJECT_PREVIEW, WebspaceRelationshipRegistry

    WebspaceRelationshipRegistry.from_context().ensure(
        "builder-source-skill",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id="demo_scenario",
        legacy_target_webspace_id=preview,
    )

    async def _fake_reload(webspace_id: str, *, scenario_id: str | None = None, action: str = "reload") -> dict[str, object]:
        return {"ok": True, "webspace_id": webspace_id, "scenario_id": scenario_id, "action": action}

    monkeypatch.setattr(webspace_runtime_module, "reload_webspace_from_scenario", _fake_reload)
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_manifest",
        lambda scenario_id, *, space="workspace": {"depends": ["weather_skill", "skill_alpha"]},
    )

    result = asyncio.run(
        webspace_runtime_module.reload_preview_webspaces_for_project(
            "skill",
            "skill_alpha",
            reason="git_updated",
        )
    )

    assert result["accepted"] is True
    assert result["reloaded_webspaces"] == [preview]


def test_reload_preview_webspace_discovery_runs_off_event_loop(monkeypatch) -> None:
    import threading

    preview = "dev-threaded-preview"
    scenario_id = "threaded_preview_scenario"
    ensure_workspace(preview)
    set_workspace_manifest(
        preview,
        display_name="DEV: threaded",
        kind="dev",
        source_mode="dev",
        home_scenario=scenario_id,
    )
    from adaos.services.builder.workbench import BuilderWorkbenchService
    from adaos.services.workspaces.relations import BUILDER_PROJECT_PREVIEW, WebspaceRelationshipRegistry

    WebspaceRelationshipRegistry.from_context().ensure(
        "builder-source-threaded",
        purpose=BUILDER_PROJECT_PREVIEW,
        scenario_id=scenario_id,
        legacy_target_webspace_id=preview,
    )
    main_thread_id = threading.get_ident()
    discovery_threads: list[int] = []
    original = BuilderWorkbenchService.list_workspace_bindings

    def _tracked_bindings(self):
        discovery_threads.append(threading.get_ident())
        return original(self)

    async def _fake_reload(webspace_id: str, *, scenario_id: str | None = None, action: str = "reload"):
        return {"ok": True, "webspace_id": webspace_id, "scenario_id": scenario_id, "action": action}

    monkeypatch.setattr(BuilderWorkbenchService, "list_workspace_bindings", _tracked_bindings)
    monkeypatch.setattr(webspace_runtime_module, "reload_webspace_from_scenario", _fake_reload)

    result = asyncio.run(
        webspace_runtime_module.reload_preview_webspaces_for_project(
            "scenario",
            scenario_id,
            reason="project_created",
        )
    )

    assert result["reloaded_webspaces"] == [preview]
    assert discovery_threads
    assert all(thread_id != main_thread_id for thread_id in discovery_threads)


def test_materialization_source_prewarm_returns_mode_summary(monkeypatch) -> None:
    class _Runtime:
        _last_skill_decls_fingerprint = ""

        def _collect_skill_decls(self, *, mode: str):
            self._last_skill_decls_fingerprint = f"fingerprint-{mode}"
            return [{"id": mode}]

    fingerprint_modes: list[str] = []
    monkeypatch.setattr(webspace_runtime_module, "WebspaceScenarioRuntime", _Runtime)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_skill_sources_fingerprint_for_materialization",
        lambda mode: fingerprint_modes.append(mode) or f"source-{mode}",
    )

    result = asyncio.run(
        webspace_runtime_module.prewarm_webspace_materialization_sources()
    )

    assert result["ok"] is True
    assert result["modes"]["workspace"]["declarations"] == 1
    assert result["modes"]["dev"]["fingerprint"] == "fingerprint-dev"
    assert fingerprint_modes == ["workspace", "dev"]


def test_startup_materialization_hydrates_every_registered_webspace(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("ADAOS_WEBSPACE_STARTUP_HYDRATION_CONCURRENCY", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_STARTUP_HYDRATION_MODE", "all")
    monkeypatch.setattr(webspace_runtime_module, "default_webspace_id", lambda: "desktop")
    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop-dev"),
            SimpleNamespace(workspace_id="desktop"),
            SimpleNamespace(workspace_id="homepoint"),
            SimpleNamespace(workspace_id="desktop"),
        ],
    )

    async def _fake_rebuild(webspace_id: str, **kwargs) -> dict[str, object]:
        calls.append({"webspace_id": webspace_id, **kwargs})
        ready = webspace_id != "homepoint"
        return {
            "ok": ready,
            "accepted": ready,
            "scenario_id": "web_desktop",
            "error": None if ready else "materialization_failed",
            "materialization": {"ready": ready},
        }

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    result = asyncio.run(webspace_runtime_module.hydrate_webspace_materialization_statuses())

    assert [call["webspace_id"] for call in calls] == ["desktop", "desktop-dev", "homepoint"]
    assert all(call["action"] == "startup_materialization_hydration" for call in calls)
    assert all(call["source_of_truth"] == "startup_runtime" for call in calls)
    assert all(str(call["request_id"]).startswith("startup-materialization:") for call in calls)
    assert result["ok"] is False
    assert result["webspace_total"] == 3
    assert result["ready_total"] == 2
    assert result["failed_total"] == 1


def test_startup_materialization_defers_scenarios_without_opt_in(monkeypatch) -> None:
    rebuilds: list[str] = []
    statuses: list[dict[str, object]] = []
    monkeypatch.delenv("ADAOS_WEBSPACE_STARTUP_HYDRATION_MODE", raising=False)
    monkeypatch.setattr(webspace_runtime_module, "default_webspace_id", lambda: "desktop")
    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop", home_scenario="startup_scene"),
            SimpleNamespace(workspace_id="orphaned-preview", home_scenario="missing_scene"),
            SimpleNamespace(workspace_id="preview", home_scenario="preview_scene"),
        ],
    )

    def _read_manifest(scenario_id: str, **_kwargs) -> dict[str, object]:
        if scenario_id == "missing_scene":
            raise FileNotFoundError(scenario_id)
        return {
            "runtime": {
                "activation": {
                    "startup_allowed": scenario_id == "startup_scene",
                }
            }
        }

    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_manifest",
        _read_manifest,
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "_set_webspace_rebuild_status",
        lambda webspace_id, **kwargs: statuses.append({"webspace_id": webspace_id, **kwargs}),
    )

    async def _fake_rebuild(webspace_id: str, **_kwargs) -> dict[str, object]:
        rebuilds.append(webspace_id)
        return {
            "ok": True,
            "accepted": True,
            "scenario_id": "startup_scene",
            "error": None,
            "materialization": {"ready": True},
        }

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    result = asyncio.run(webspace_runtime_module.hydrate_webspace_materialization_statuses())

    assert rebuilds == ["desktop"]
    assert [item["webspace_id"] for item in statuses] == ["orphaned-preview", "preview"]
    assert all(item["status"] == "deferred" for item in statuses)
    assert result["ok"] is True
    assert result["mode"] == "opt_in"
    assert result["webspace_total"] == 3
    assert result["ready_total"] == 1
    assert result["deferred_total"] == 2
    assert result["failed_total"] == 0


def test_startup_materialization_always_hydrates_default_webspace(monkeypatch) -> None:
    rebuilds: list[str] = []
    manifest_reads: list[str] = []
    monkeypatch.delenv("ADAOS_WEBSPACE_STARTUP_HYDRATION_MODE", raising=False)
    monkeypatch.setattr(webspace_runtime_module, "default_webspace_id", lambda: "desktop")
    monkeypatch.setattr(
        webspace_runtime_module.workspace_index,
        "list_workspaces",
        lambda: [
            SimpleNamespace(workspace_id="desktop", home_scenario="web_desktop"),
            SimpleNamespace(workspace_id="preview", home_scenario="preview_scene"),
        ],
    )

    def _read_manifest(scenario_id: str, **_kwargs) -> dict[str, object]:
        manifest_reads.append(scenario_id)
        return {"runtime": {"activation": {"startup_allowed": False}}}

    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_manifest",
        _read_manifest,
    )

    async def _fake_rebuild(webspace_id: str, **_kwargs) -> dict[str, object]:
        rebuilds.append(webspace_id)
        return {
            "ok": True,
            "accepted": True,
            "scenario_id": "web_desktop",
            "error": None,
            "materialization": {"ready": True},
        }

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    result = asyncio.run(webspace_runtime_module.hydrate_webspace_materialization_statuses())

    assert rebuilds == ["desktop"]
    assert manifest_reads == ["preview_scene"]
    assert result["ready_total"] == 1
    assert result["deferred_total"] == 1


def test_startup_materialization_uses_isolated_payload_without_live_mutation(monkeypatch) -> None:
    materialize_calls: list[dict[str, object]] = []
    direct_rebuild_calls: list[str] = []

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_materialize(self, webspace_id: str, **kwargs):
        materialize_calls.append({"webspace_id": webspace_id, **kwargs})
        self._last_rebuild_timings_ms = {"payload_worker": 5.0, "total": 5.0}
        self._last_rebuild_ydoc_timings_ms = {"worker_process": 5.0, "total": 5.0}
        self._last_worker_diagnostics = {"mode": "payload_only"}
        self._last_apply_summary = {"payload_only": True}
        self._last_materialized_payload = {
            "scenario_id": "web_desktop",
            "application": {"desktop": {"pageSchema": {"id": "desktop"}}},
            "catalog": {"apps": [], "widgets": []},
            "registry": {},
            "installed": {"apps": [], "widgets": []},
            "desktop": {},
            "webio": {},
            "routing": {},
        }
        return SimpleNamespace(scenario_id="web_desktop", apps=[], widgets=[])

    async def _unexpected_direct_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        direct_rebuild_calls.append(webspace_id)
        raise AssertionError("startup hydration must not mutate the operational YDoc")

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_async",
        _fake_materialize,
    )
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "rebuild_webspace_async",
        _unexpected_direct_rebuild,
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "startup-desktop",
            action="startup_materialization_hydration",
            scenario_id="web_desktop",
            scenario_resolution="manifest_home",
            source_of_truth="startup_runtime",
            request_id="startup-materialization:test",
        )
    )

    assert result["accepted"] is True
    assert result["payload_only_rebuild"] is True
    assert result["live_room_update_requested"] is False
    assert result["live_room_refresh"] is None
    assert direct_rebuild_calls == []
    assert len(materialize_calls) == 1
    assert materialize_calls[0]["isolate_process"] is True


def test_scenarios_synced_routes_through_semantic_rebuild_helper(monkeypatch) -> None:
    captured: list[tuple[str, str | None, str, str]] = []

    async def _fake_rebuild(
        webspace_id: str,
        *,
        action: str = "rebuild",
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
        source_of_truth: str = "current_runtime",
        reseed_from_scenario: bool = False,
        event_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        assert reseed_from_scenario is False
        assert event_payload is None
        captured.append((webspace_id, scenario_id, action, source_of_truth))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    asyncio.run(
        webspace_runtime_module._on_scenarios_synced(
            {"webspace_id": "phase3-bootstrap", "scenario_id": "web_desktop"}
        )
    )

    assert captured == [("phase3-bootstrap", "web_desktop", "scenario_projection_sync", "scenario_projection")]


def test_phase4_collect_resolver_inputs_does_not_refresh_projection_registry(monkeypatch) -> None:
    projection_calls: list[str] = []

    class _Projections:
        def load_from_scenario(self, scenario_id: str) -> int:
            projection_calls.append(scenario_id)
            return 1

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(SimpleNamespace(projections=_Projections()))
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])

    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap({"current_scenario": "web_desktop", "scenarios": {"web_desktop": {"application": {}}}}),
            "data": _FakeMap({"scenarios": {"web_desktop": {"catalog": {}}}}),
            "registry": _FakeMap({"scenarios": {"web_desktop": {}}}),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, "phase4-collect")

    assert inputs.scenario_id == "web_desktop"
    assert projection_calls == []


def test_materialization_cpu_oneshot_stays_on_owner_thread(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_DEV_TOOL_EXECUTION_MODE", "oneshot")
    owner_thread = threading.get_ident()

    observed_thread = asyncio.run(
        webspace_runtime_module._run_materialization_cpu(threading.get_ident)
    )

    assert observed_thread == owner_thread


def test_collect_resolver_inputs_detaches_nested_doc_values_before_worker(monkeypatch) -> None:
    class _DocMap:
        def __init__(self, values: dict[str, object]) -> None:
            self._values = values

        def get(self, key: str):
            return self._values.get(key)

        def items(self):
            return self._values.items()

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])
    monkeypatch.setattr(webspace_runtime_module, "_preserve_live_state_on_rebuild_enabled", lambda: True)
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, space="workspace": {
            "id": scenario_id,
            "ui": {"application": {"desktop": {"pageSchema": {"id": "detached"}}}},
            "catalog": {},
            "registry": {},
        },
    )
    nested_doc_value = _DocMap({"dialog": _DocMap({"title": "Detached"})})
    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap(
                {
                    "current_scenario": "web_desktop",
                    "application": _DocMap({"modals": nested_doc_value}),
                    "scenarios": {},
                }
            ),
            "data": _FakeMap({"scenarios": {}}),
            "registry": _FakeMap({"scenarios": {}}),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, "detached-inputs")

    assert inputs.live_state["application"]["modals"] == {"dialog": {"title": "Detached"}}
    assert not isinstance(inputs.live_state["application"]["modals"], _DocMap)
    assert not isinstance(inputs.live_state["application"]["modals"]["dialog"], _DocMap)


def test_phase_pointer_collect_resolver_inputs_prefers_loader_payload_over_legacy_yjs(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, space="workspace": {
            "id": scenario_id,
            "ui": {"application": {"desktop": {"pageSchema": {"id": f"loader-page:{space}:{scenario_id}"}}}},
            "catalog": {"apps": [{"id": f"loader-app:{space}:{scenario_id}"}]},
            "registry": {"modals": [f"loader-modal:{space}:{scenario_id}"]},
        },
    )

    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap(
                {
                    "current_scenario": "prompt_engineer_scenario",
                    "scenarios": {
                        "prompt_engineer_scenario": {
                            "application": {"desktop": {"pageSchema": {"id": "legacy-page"}}}
                        }
                    },
                }
            ),
            "data": _FakeMap(
                {
                    "scenarios": {
                        "prompt_engineer_scenario": {
                            "catalog": {"apps": [{"id": "legacy-app"}]}
                        }
                    }
                }
            ),
            "registry": _FakeMap(
                {
                    "scenarios": {
                        "prompt_engineer_scenario": {"modals": ["legacy-modal"]}
                    }
                }
            ),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, "phase-pointer-loader")

    assert inputs.scenario_application["desktop"]["pageSchema"]["id"] == "loader-page:workspace:prompt_engineer_scenario"
    assert inputs.scenario_catalog["apps"] == [{"id": "loader-app:workspace:prompt_engineer_scenario"}]
    assert inputs.scenario_registry["modals"] == ["loader-modal:workspace:prompt_engineer_scenario"]
    assert inputs.scenario_source == "loader:workspace"
    assert inputs.legacy_scenario_fallback is False
    assert inputs.metadata["scenario_source"] == "loader:workspace"


def test_phase_pointer_collect_resolver_inputs_falls_back_to_legacy_yjs_when_loader_missing(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])
    monkeypatch.setattr(webspace_runtime_module.scenarios_loader, "read_content", lambda scenario_id, space="workspace": {})

    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap(
                {
                    "current_scenario": "prompt_engineer_scenario",
                    "scenarios": {
                        "node-1": {
                            "prompt_engineer_scenario": {
                                "application": {"desktop": {"pageSchema": {"id": "legacy-page"}}}
                            }
                        }
                    },
                }
            ),
            "data": _FakeMap(
                {
                    "scenarios": {
                        "node-1": {
                            "prompt_engineer_scenario": {
                                "catalog": {"apps": [{"id": "legacy-app"}]}
                            }
                        }
                    }
                }
            ),
            "registry": _FakeMap(
                {
                    "scenarios": {
                        "node-1": {
                            "prompt_engineer_scenario": {"modals": ["legacy-modal"]}
                        }
                    }
                }
            ),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, "phase-pointer-legacy")

    assert inputs.scenario_application["desktop"]["pageSchema"]["id"] == "legacy-page"
    assert inputs.scenario_catalog["apps"] == [{"id": "legacy-app"}]
    assert inputs.scenario_registry["modals"] == ["legacy-modal"]
    assert inputs.scenario_source == "legacy_yjs"
    assert inputs.legacy_scenario_fallback is True
    assert inputs.metadata["legacy_scenario_fallback"] is True


def test_phase_pointer_collect_resolver_inputs_reads_node_scoped_legacy_yjs_when_loader_missing(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])
    monkeypatch.setattr(webspace_runtime_module.scenarios_loader, "read_content", lambda scenario_id, space="workspace": {})

    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap(
                {
                    "current_scenario": "prompt_engineer_scenario",
                    "scenarios": {
                        "hub": {
                            "prompt_engineer_scenario": {
                                "application": {"desktop": {"pageSchema": {"id": "legacy-node-page"}}}
                            }
                        }
                    },
                }
            ),
            "data": _FakeMap(
                {
                    "scenarios": {
                        "hub": {
                            "prompt_engineer_scenario": {
                                "catalog": {"apps": [{"id": "legacy-node-app"}]}
                            }
                        }
                    }
                }
            ),
            "registry": _FakeMap(
                {
                    "scenarios": {
                        "hub": {
                            "prompt_engineer_scenario": {"modals": ["legacy-node-modal"]}
                        }
                    }
                }
            ),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, "phase-pointer-legacy-node-scoped")

    assert inputs.scenario_application["desktop"]["pageSchema"]["id"] == "legacy-node-page"
    assert inputs.scenario_catalog["apps"] == [{"id": "legacy-node-app"}]
    assert inputs.scenario_registry["modals"] == ["legacy-node-modal"]
    assert inputs.scenario_source == "legacy_yjs"
    assert inputs.legacy_scenario_fallback is True


def test_phase5_collect_resolver_inputs_prefers_persistent_overlay(monkeypatch) -> None:
    webspace_id = "phase5-overlay-collect"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Overlay Collect",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_installed_overlay(
        webspace_id,
        {"apps": ["overlay-app"], "widgets": ["overlay-widget"]},
    )
    set_workspace_pinned_widgets_overlay(
        webspace_id,
        [{"id": "infra-status", "type": "visual.metricTile"}],
    )
    set_workspace_topbar_overlay(
        webspace_id,
        [{"id": "home", "label": "Home"}],
    )
    set_workspace_page_schema_overlay(
        webspace_id,
        {"id": "desktop", "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]}, "widgets": []},
    )

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_collect_skill_decls", lambda mode="mixed": [])
    monkeypatch.setattr(runtime, "_list_desktop_scenarios", lambda space="mixed": [])

    fake_doc = _FakeDoc(
        {
            "ui": _FakeMap({"current_scenario": "web_desktop", "scenarios": {"web_desktop": {"application": {}}}}),
            "data": _FakeMap(
                {
                    "installed": {"apps": ["ydoc-app"], "widgets": ["ydoc-widget"]},
                    "scenarios": {"web_desktop": {"catalog": {}}},
                }
            ),
            "registry": _FakeMap({"scenarios": {"web_desktop": {}}}),
        }
    )

    inputs = runtime._collect_resolver_inputs_in_doc(fake_doc, webspace_id)

    assert inputs.overlay_snapshot["installed"]["apps"] == ["overlay-app"]
    assert inputs.overlay_snapshot["installed"]["widgets"] == ["overlay-widget"]
    assert inputs.overlay_snapshot["installed"].get("removedApps") == []
    assert inputs.overlay_snapshot["installed"].get("removedWidgets") == []
    assert inputs.overlay_snapshot["pinnedWidgets"] == [
        {"id": "infra-status", "type": "visual.metricTile"}
    ]
    assert inputs.overlay_snapshot["source"] == "workspace_manifest_overlay"


def test_phase5_resolver_prefers_pinned_widgets_from_overlay_over_scenario_defaults() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase5-pinned-overlay",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={
                "desktop": {
                    "topbar": [],
                    "pinnedWidgets": [{"id": "scenario-pin", "type": "visual.metricTile"}],
                }
            },
            scenario_catalog={"apps": [], "widgets": [{"id": "overlay-pin", "type": "visual.metricTile"}]},
            scenario_registry={},
            overlay_snapshot={
                "installed": {"apps": [], "widgets": []},
                "pinnedWidgets": [{"id": "overlay-pin", "type": "visual.metricTile", "title": "Overlay Pin"}],
            },
            live_state={"desktop": {}, "routing": {}},
            skill_decls=[],
            desktop_scenarios=[],
        )
    )

    pinned = resolved.application["desktop"]["pinnedWidgets"][0]
    assert {key: pinned.get(key) for key in ("id", "type", "title")} == {
        "id": "overlay-pin",
        "type": "visual.metricTile",
        "title": "Overlay Pin",
    }
    assert resolved.desktop["pinnedWidgets"] == resolved.application["desktop"]["pinnedWidgets"]


def test_phase5_resolver_prefers_scenario_page_schema_and_topbar_over_overlay() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase5-layout-overlay",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={
                "desktop": {
                    "topbar": [{"id": "scenario-home", "label": "Home"}],
                    "pageSchema": {
                        "id": "desktop",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [{"id": "scenario-widget", "type": "desktop.widgets", "area": "main"}],
                    },
                }
            },
            scenario_catalog={"apps": [], "widgets": []},
            scenario_registry={},
            overlay_snapshot={
                "installed": {"apps": [], "widgets": []},
                "topbar": [{"id": "stale-home", "label": "Stale"}],
                "pageSchema": {
                    "id": "stale-desktop",
                    "layout": {"type": "single", "areas": []},
                    "widgets": [],
                },
            },
            live_state={"desktop": {}, "routing": {}},
            skill_decls=[],
            desktop_scenarios=[],
        )
    )

    assert resolved.application["desktop"]["topbar"] == [{"id": "scenario-home", "label": "Home"}]
    assert resolved.application["desktop"]["pageSchema"]["id"] == "desktop"
    assert resolved.desktop["topbar"] == [{"id": "scenario-home", "label": "Home"}]
    assert resolved.desktop["pageSchema"]["widgets"][0]["id"] == "scenario-widget"


def test_phase4_semantic_rebuild_refreshes_projection_rules_before_runtime_rebuild(monkeypatch) -> None:
    order: list[str] = []

    async def _fake_refresh(
        ctx,
        webspace_id: str,
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:  # noqa: ARG001
        order.append("refresh")
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "workspace",
            "rules_loaded": 1,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
        order.append("rebuild")
        self._last_rebuild_timings_ms = {
            "collect_inputs": 1.25,
            "resolve": 2.5,
            "apply": 3.75,
            "to_registry_entry": 0.5,
            "total": 8.0,
        }
        self._last_apply_summary = {
            "branch_count": 6,
            "changed_branches": 2,
            "unchanged_branches": 4,
            "failed_branches": 0,
            "changed_paths": ["ui.application", "registry.merged"],
            "defaults_failed": False,
        }
        return SimpleNamespace(scenario_id="web_desktop", apps=[], widgets=[])

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: get_ctx())
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase4-ordered-rebuild",
            action="rebuild",
            scenario_id="web_desktop",
            source_of_truth="scenario_projection",
        )
    )

    assert order == ["refresh", "rebuild"]
    assert result["accepted"] is True
    assert result["projection_refresh"]["rules_loaded"] == 1
    assert isinstance(result["timings_ms"], dict)
    assert "projection_refresh" in result["timings_ms"]
    assert "semantic_rebuild" in result["timings_ms"]
    assert isinstance(result["semantic_rebuild_timings_ms"], dict)
    assert result["semantic_rebuild_timings_ms"]["apply"] == 3.75
    assert result["apply_summary"]["changed_branches"] == 2


def test_builder_revision_apply_invalidates_loader_cache_without_reseed(monkeypatch) -> None:
    invalidations: list[tuple[str, str]] = []
    rebuild_kwargs: list[dict[str, object]] = []
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    webspace_runtime_module._RUNTIME.cache.put_resolved_webspace(
        "poison",
        {"scenario_id": "stale"},
        max_entries=16,
        max_bytes=1024,
    )

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "dev",
            "rules_loaded": 0,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
        assert kwargs.get("initial_scenario_id") == "prompt_engineer_scenario"
        rebuild_kwargs.append(dict(kwargs))
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        self._last_resolver_debug = {"source": "loader:dev", "cache_hit": False}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):  # noqa: ARG002
        return None

    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "0")
    monkeypatch.delenv("ADAOS_BUILDER_REVISION_LIVE_ROOM_UPDATES", raising=False)
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "invalidate_cache",
        lambda *, scenario_id, space: invalidations.append((scenario_id, space)),
    )
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: SimpleNamespace(bus=SimpleNamespace(publish=lambda _event: None)))
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "builder-revision-cache",
            action="builder_revision_apply",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="builder_revision",
            reseed_from_scenario=False,
        )
    )

    assert invalidations == [
        ("prompt_engineer_scenario", "workspace"),
        ("prompt_engineer_scenario", "dev"),
    ]
    assert rebuild_kwargs[-1]["publish_live_room"] is False
    assert rebuild_kwargs[-1]["prefer_live_room"] is False
    assert rebuild_kwargs[-1]["fresh_doc"] is True
    assert rebuild_kwargs[-1]["replace_ystore_snapshot"] is True
    assert result["live_room_update_requested"] is True
    assert result["live_room_publish"] is False
    assert result["accepted"] is True
    assert "invalidate_loader_cache" in result["timings_ms"]
    assert "invalidate_resolver_cache" in result["timings_ms"]
    assert webspace_runtime_module._RUNTIME.cache.resolved_webspace_count() == 0
    assert "project_scenario_payload" not in result["timings_ms"]
    assert "seed_from_scenario" not in result["timings_ms"]


def test_builder_revision_apply_persists_dev_home_without_listing_sync(monkeypatch) -> None:
    webspace_id = "phase2-builder-dev-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Builder Home",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )
    sync_listing_calls: list[bool] = []

    async def _fake_rebuild(*args, **kwargs):  # noqa: ARG001
        return {"ok": True, "accepted": True, "action": "builder_revision_apply"}

    async def _fake_sync_listing() -> None:
        sync_listing_calls.append(True)

    monkeypatch.setattr(
        webspace_runtime_module,
        "_preflight_validated_scenario",
        lambda scenario_id, **kwargs: (scenario_id, kwargs.get("resolution") or "builder_revision", {"ok": True}),
    )
    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_sync_listing)

    result = asyncio.run(
        webspace_runtime_module.apply_builder_revision_materialization(
            webspace_id,
            scenario_id="todo_list_5b9319fa",
            revision="022",
        )
    )

    row = get_workspace(webspace_id)
    assert row is not None
    assert row.home_scenario == "todo_list_5b9319fa"
    assert sync_listing_calls == []
    assert result["accepted"] is True
    assert result["home_scenario"] == "todo_list_5b9319fa"
    assert result["webspace_identity_update"]["attempted"] is True
    assert result["webspace_identity_update"]["changed"] is True
    assert result["webspace_identity_update"]["home_scenario_before"] == "prompt_engineer_scenario"


def test_builder_preview_sources_exact_prototype_and_retained_automation(monkeypatch, tmp_path: Path) -> None:
    scenario_root = tmp_path / "dev" / "scenarios" / "recipes"
    revisions = scenario_root / "ui_revisions"
    revisions.mkdir(parents=True)
    prototype = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"title": "Recipes prototype"}}}},
    }
    (revisions / "002.json").write_text(
        json.dumps({"after_webui": prototype}),
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    automation_dir = state_dir / "builder" / "workflow_snapshots" / "scenario" / "recipes" / "automation"
    automation_dir.mkdir(parents=True)
    automation = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"title": "Recipes automation"}}}},
    }
    (automation_dir / "webui.json").write_text(json.dumps(automation), encoding="utf-8")
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "scenario_root_for_space",
        lambda scenario_id, space: scenario_root,
    )
    monkeypatch.setattr("adaos.services.runtime_paths.current_state_dir", lambda: state_dir)

    prototype_content, prototype_space = webspace_runtime_module._builder_preview_content_override(
        "recipes",
        stage="prototype",
        revision="002",
        label=None,
    )
    automation_content, automation_space = webspace_runtime_module._builder_preview_content_override(
        "recipes",
        stage="automation",
        revision="task.current",
        label=None,
    )

    assert prototype_space == "dev"
    assert prototype_content["ui"]["application"]["desktop"]["pageSchema"]["title"] == "proto:002 Recipes prototype"
    assert prototype_content["ui"]["application"]["desktop"]["pageSchema"]["_adaos"] == {
        "releaseStage": "ALPHA",
        "releaseStageSource": "builder_materialization",
        "materialization": {
            "stage": "prototype",
            "revision": "002",
            "sourceSpace": "dev",
        },
    }
    assert automation_space == "dev"
    assert automation_content["ui"]["application"]["desktop"]["pageSchema"]["title"] == "active: Recipes automation"
    assert automation_content["ui"]["application"]["desktop"]["pageSchema"]["_adaos"]["releaseStage"] == "ALPHA"


def test_builder_publication_preview_reads_workspace_snapshot(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    publication = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"title": "Published recipes"}}}},
    }

    def _read_content(scenario_id: str, *, space: str):
        calls.append((scenario_id, space))
        return publication

    monkeypatch.setattr(webspace_runtime_module.scenarios_loader, "read_content", _read_content)

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "recipes",
        stage="publication",
        revision="0.2.0",
        label=None,
    )

    assert calls == [("recipes", "workspace")]
    assert source_space == "workspace"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["title"] == "public:0.2.0 Published recipes"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["_adaos"]["releaseStage"] == "STABLE"


def test_builder_trial_preview_reads_exact_runtime_activation(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    runtime_workspace = tmp_path / "workspace" / ".runtime" / "trials" / "candidate-1" / "workspace"
    scenario_root = runtime_workspace / "scenarios" / "recipes"
    scenario_root.mkdir(parents=True)
    (scenario_root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.2.0\nui:\n  manifest: webui.json\n",
        encoding="utf-8",
    )
    (scenario_root / "webui.json").write_text(
        json.dumps(
            {
                "schema": "adaos.webui.v1",
                "ui": {
                    "application": {
                        "desktop": {"pageSchema": {"title": "Candidate recipes"}}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    records = state_dir / "artifact_pipeline" / "trial-activations"
    records.mkdir(parents=True)
    (records / "candidate-1.json").write_text(
        json.dumps(
            {
                "schema": "adaos.trial.activation.v1",
                "status": "active",
                "candidate_ref": {"candidate_id": "candidate-1"},
                "release_ref": {"version": "0.2.0"},
                "target": {"scenario_id": "recipes", "webspace_id": "dev1-dev"},
                "runtime_binding": {"path": str(runtime_workspace)},
                "updated_at": "2026-08-06T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("adaos.services.runtime_paths.current_state_dir", lambda: state_dir)

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "recipes",
        stage="trial",
        revision="0.2.0",
        label=None,
    )

    assert source_space == "workspace"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["title"] == (
        "trial:0.2.0 Candidate recipes"
    )
    assert content["ui"]["application"]["desktop"]["pageSchema"]["_adaos"] == {
        "releaseStage": "BETA",
        "releaseStageSource": "builder_materialization",
        "materialization": {
            "stage": "trial",
            "revision": "0.2.0",
            "sourceSpace": "workspace",
        },
    }


def test_builder_publication_preview_reads_verified_installed_package_when_slot_is_inactive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from adaos.domain.artifact_release import (
        ArtifactSourceRef,
        ProjectRelease,
        StableSubscription,
    )
    from adaos.services.artifact_pipeline.channels import SubscriptionStore
    from adaos.services.artifact_pipeline.packages import (
        ContentAddressedPackageStore,
        build_artifact_package,
    )

    scenario_dir = tmp_path / "source" / "recipes"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.2.0\ntitle: Recipes\n",
        encoding="utf-8",
    )
    publication = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {"pageSchema": {"title": "Installed publication"}}
            }
        },
    }
    (scenario_dir / "webui.json").write_text(
        json.dumps(publication),
        encoding="utf-8",
    )
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("subnets/dev/nodes/node/scenarios/recipes/",),
    )
    built = build_artifact_package(scenario_dir, kind="scenario", source_ref=source)
    release = ProjectRelease(
        project_id="recipes",
        version="0.2.0",
        source_ref=source,
        components=(built.ref,),
    ).seal()

    workspace = tmp_path / "workspace"
    metadata = workspace / ".adaos"
    releases = metadata / "releases"
    releases.mkdir(parents=True)
    release_digest = release.release_digest or release.computed_digest()
    (releases / f"{release_digest.split(':', 1)[1]}.json").write_text(
        json.dumps(release.to_dict()),
        encoding="utf-8",
    )
    SubscriptionStore(metadata / "subscriptions.json").save(
        StableSubscription(
            project_id="recipes",
            installed_release="recipes@0.2.0",
            installed_digest=release_digest,
        )
    )
    state_dir = tmp_path / "state"
    ContentAddressedPackageStore(
        state_dir / "artifact_pipeline" / "packages"
    ).put(built.archive_bytes, expected_digest=built.ref.digest)

    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "scenario_root_for_space",
        lambda scenario_id, space: workspace / "scenarios" / scenario_id,
    )
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space: None,
    )
    monkeypatch.setattr("adaos.services.runtime_paths.current_state_dir", lambda: state_dir)

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "recipes",
        stage="publication",
        revision="0.2.0",
        label=None,
    )

    assert source_space == "workspace"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["title"] == (
        "public:0.2.0 Installed publication"
    )


def test_builder_prototype_preview_synthesizes_an_empty_canvas_for_legacy_default_scenarios(monkeypatch) -> None:
    legacy_content = {
        "id": "template-id",
        "version": "0.1.0",
        "name": "New Scenario",
        "steps": [],
    }
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space: legacy_content,
    )
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_manifest",
        lambda scenario_id, *, space: {"id": scenario_id, "name": "Recipe Book"},
    )

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "test01_recipes",
        stage="prototype",
        revision=None,
        label=None,
    )

    page = content["ui"]["application"]["desktop"]["pageSchema"]
    assert source_space == "dev"
    assert page["id"] == "test01_recipes"
    assert page["title"] == "proto:current Recipe Book"
    assert [item["id"] for item in page["widgets"]] == ["builder-empty-canvas"]
    assert page["meta"]["builder"]["compatibility_fallback"] is True
    assert "ui" not in legacy_content


def test_builder_prototype_preview_repairs_an_existing_zero_widget_empty_canvas(monkeypatch) -> None:
    empty_canvas = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "template-id",
                        "title": "New Scenario",
                        "layout": {
                            "type": "single",
                            "pattern": "stack",
                            "areas": [{"id": "main", "role": "main"}],
                        },
                        "widgets": [],
                        "meta": {"builder": {"empty_canvas": True}},
                    }
                }
            }
        },
    }
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space: empty_canvas,
    )

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "test02_recipes",
        stage="prototype",
        revision=None,
        label="proto: test02_recipes · current",
    )

    page = content["ui"]["application"]["desktop"]["pageSchema"]
    assert source_space == "dev"
    assert page["id"] == "test02_recipes"
    assert page["title"] == "proto: test02_recipes · current"
    assert [item["id"] for item in page["widgets"]] == ["builder-empty-canvas"]
    assert page["meta"]["builder"]["placeholder_injected"] is True
    assert empty_canvas["ui"]["application"]["desktop"]["pageSchema"]["widgets"] == []


def test_legacy_automation_preview_falls_back_to_current_dev_descriptor(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    current = {
        "schema": "adaos.webui.v1",
        "ui": {"application": {"desktop": {"pageSchema": {"title": "Legacy automation"}}}},
    }
    monkeypatch.setattr("adaos.services.runtime_paths.current_state_dir", lambda: state_dir)
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space: current,
    )

    content, source_space = webspace_runtime_module._builder_preview_content_override(
        "legacy-recipes",
        stage="automation",
        revision="current",
        label=None,
    )

    assert source_space == "dev"
    assert content["ui"]["application"]["desktop"]["pageSchema"]["title"] == "active: Legacy automation"


def test_builder_revision_apply_skips_superseded_source_binding(monkeypatch) -> None:
    webspace_id = "phase2-builder-superseded-dev"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Superseded Builder",
        kind="dev",
        source_mode="dev",
        home_scenario="current_builder",
    )
    rebuild_calls: list[str] = []

    class _Workbench:
        @classmethod
        def from_context(cls):
            return cls()

        def get_workspace_binding(self, source_webspace_id):
            assert source_webspace_id == "desktop"
            return {
                "source_webspace_id": "desktop",
                "dev_webspace_id": webspace_id,
                "runtime_scenario_id": "current_builder",
            }

    import adaos.services.builder.workbench as workbench_module

    monkeypatch.setattr(workbench_module, "BuilderWorkbenchService", _Workbench)
    monkeypatch.setattr(
        webspace_runtime_module,
        "rebuild_webspace_from_sources",
        lambda *args, **kwargs: rebuild_calls.append(str(kwargs.get("scenario_id"))),
    )

    result = asyncio.run(
        webspace_runtime_module.apply_builder_revision_materialization(
            webspace_id,
            scenario_id="stale_prototype",
            revision="002",
            event_payload={"source_webspace_id": "desktop"},
        )
    )

    assert result == {
        "ok": True,
        "accepted": False,
        "skipped": "superseded_builder_target",
        "action": "builder_revision_apply",
        "source_webspace_id": "desktop",
        "webspace_id": webspace_id,
        "scenario_id": "stale_prototype",
        "desired_scenario_id": "current_builder",
        "revision": "002",
    }
    assert rebuild_calls == []


def test_phase4_projection_refresh_uses_dev_space_for_dev_webspace(monkeypatch) -> None:
    webspace_id = "phase4-dev-refresh"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Prompt Lab",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    captured: list[tuple[str, str]] = []

    class _Projections:
        def load_from_scenario(self, scenario_id: str, *, space: str = "workspace") -> int:
            captured.append((scenario_id, space))
            return 2

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))

    result = asyncio.run(
        webspace_runtime_module._refresh_projection_rules_for_rebuild(
            SimpleNamespace(projections=_Projections()),
            webspace_id,
        )
    )

    assert captured == [("prompt_engineer_scenario", "dev")]
    assert result["space"] == "dev"
    assert result["rules_loaded"] == 2


def test_phase4_rebuild_from_sources_succeeds_without_materialized_yjs_scenario_payload(monkeypatch) -> None:
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    webspace_id = "phase4-loader-rebuild"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Loader Rebuild",
        kind="workspace",
        source_mode="workspace",
        home_scenario="prompt_engineer_scenario",
    )

    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }

    async def _fake_refresh(
        ctx,
        webspace_id: str,
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:  # noqa: ARG001
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "workspace",
            "rules_loaded": 0,
        }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", lambda self, mode="mixed": [])
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_list_desktop_scenarios", lambda self, space: [])
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space="workspace": {
            "id": scenario_id,
            "ui": {"application": {"desktop": {"pageSchema": {"id": "loader-page"}}}},
            "registry": {"modals": ["loader-modal"], "widgets": []},
            "catalog": {"apps": [{"id": "loader-app", "title": "Loader App"}], "widgets": []},
            "data": {"routing": {"routes": {"home": "/loader"}}},
        }
        if scenario_id == "prompt_engineer_scenario" and space == "workspace"
        else {},
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            webspace_id,
            action="rebuild",
            source_of_truth="scenario_projection",
        )
    )

    assert result["accepted"] is True
    assert result["resolver"]["source"] == "loader:workspace"
    assert result["resolver"]["legacy_fallback"] is False
    assert fake_state["ui"]["application"]["desktop"]["pageSchema"]["id"] == "loader-page"
    assert fake_state["data"]["catalog"]["apps"][0]["id"] == "loader-app"
    assert "scenarios" not in fake_state["ui"]
    assert "scenarios" not in fake_state["data"]
    assert "scenarios" not in fake_state["registry"]


def test_phase3_resolver_outputs_are_explicit_and_reusable(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "node-0")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Node 0",
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "",
        },
    )
    monkeypatch.setattr(
        webspace_runtime_module,
        "load_config",
        lambda: SimpleNamespace(role="hub", node_id="node-0", node_settings=SimpleNamespace(node_names=[])),
    )
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase3-explicit-resolver",
            scenario_id="prompt_engineer_scenario",
            source_mode="dev",
            scenario_application={
                "id": "prompt-root",
                "version": "0.8.0",
                "modals": {"scenario_modal": {"title": "Scenario"}},
                "desktop": {
                    "pageSchema": {
                        "widgets": [{"id": "desktop-widgets", "type": "desktop.widgets", "area": "main"}]
                    }
                },
            },
            scenario_catalog={
                "apps": [{"id": "scenario-app", "title": "Scenario App"}],
                "widgets": [{"id": "scenario-widget", "title": "Scenario Widget"}],
            },
            scenario_registry={"modals": ["scenario_modal"], "widgets": ["scenario_widget"]},
            overlay_snapshot={"installed": {"apps": ["scenario-app"], "widgets": []}},
            live_state={"desktop": {"installed": {}}, "routing": {}},
            skill_decls=[
                {
                    "skill": "prompt_skill",
                    "space": "dev",
                    "version": "0.4.2",
                    "source_authority": "dev",
                    "component_update": {
                        "notice_id": "update-1",
                        "stage": "alpha",
                        "summary": "Try the prompt controls.",
                    },
                    "apps": [{"id": "skill-app", "title": "Skill App"}],
                    "widgets": [{"id": "skill-widget", "title": "Skill Widget"}],
                    "registry": {
                        "modals": {"skill_modal": {"title": "Skill Modal"}},
                        "widgets": ["skill_widget"],
                    },
                    "contributions": [
                        {
                            "extensionPoint": "desktop.apps",
                            "type": "app",
                            "id": "skill-app",
                            "autoInstall": True,
                        }
                    ],
                    "ydoc_defaults": {"data/prompt": {"status": "idle"}},
                }
            ],
            desktop_scenarios=[("other_scenario", "Other Scenario")],
        )
    )

    assert resolved.scenario_id == "prompt_engineer_scenario"
    assert [item["id"] for item in resolved.catalog["apps"]] == [
        "scenario-app",
        "scenario:other_scenario",
        "skill-app",
    ]
    assert [item["node_label"] for item in resolved.catalog["apps"]] == [
        "Node 0",
        "Node 0",
        "Node 0",
    ]
    assert [item["id"] for item in resolved.catalog["widgets"]] == ["scenario-widget", "skill-widget"]
    assert [item["node_label"] for item in resolved.catalog["widgets"]] == ["Node 0", "Node 0"]
    assert resolved.registry["modals"] == [
        "scenario_modal",
        "skill_modal",
        "apps_catalog",
        "widgets_catalog",
        "scenario_switcher",
    ]
    assert resolved.registry["widgets"] == ["scenario_widget", "skill_widget"]
    assert resolved.installed["apps"] == ["scenario-app", "scenario:other_scenario", "skill-app"]
    assert resolved.application["modals"]["scenario_modal"]["title"] == "Scenario"
    assert resolved.application["modals"]["skill_modal"]["title"] == "Skill Modal"
    assert resolved.application["desktop"]["pageSchema"]["_adaos"]["version"] == "0.8.0"
    modal_metadata = resolved.application["modals"]["skill_modal"]["_adaos"]
    assert modal_metadata["component"] == {"type": "skill", "id": "prompt_skill"}
    assert modal_metadata["version"] == "0.4.2"
    assert modal_metadata["sourceAuthority"] == "dev"
    assert modal_metadata["componentUpdate"] == {
        "notice_id": "update-1",
        "stage": "alpha",
        "summary": "Try the prompt controls.",
    }
    assert modal_metadata["releaseStage"] == "alpha"
    assert next(item for item in resolved.catalog["apps"] if item["id"] == "skill-app")["release_stage"] == "alpha"
    assert resolved.application["modals"]["apps_catalog"]["load"]["focus"] == "off_focus"
    assert resolved.application["modals"]["apps_catalog"]["schema"]["load"]["data"] == "deferred"
    assert resolved.application["modals"]["widgets_catalog"]["schema"]["widgets"][0]["load"]["offFocusReadyState"] == "hydrating"
    assert resolved.desktop["installed"]["apps"] == ["scenario-app", "scenario:other_scenario", "skill-app"]
    assert resolved.routing["routes"] == {}


def test_phase3_resolver_attaches_webui_contract_diagnostics(monkeypatch) -> None:
    captured: list[object] = []

    def _capture_contract_issues(issues, *, webspace_id=None, source="webui_contract") -> None:  # noqa: ANN001
        captured.extend(issues)

    monkeypatch.setattr(webspace_runtime_module, "log_webui_contract_issues", _capture_contract_issues)

    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase3-webui-contract-diagnostics",
            scenario_id="prompt_engineer_scenario",
            source_mode="workspace",
            scenario_application={"desktop": {"pageSchema": {"id": "contract-page"}}},
            scenario_catalog={"apps": [], "widgets": []},
            scenario_registry={"modals": [], "widgets": []},
            overlay_snapshot={"installed": {"apps": [], "widgets": []}},
            live_state={"desktop": {"installed": {}}, "routing": {}},
            skill_decls=[
                {
                    "skill": "demo_skill",
                    "space": "default",
                    "interface": {
                        "schema": "adaos.ui.skill_interface.v1",
                        "defaultView": "demo.note.edit",
                        "views": {
                            "demo.note.edit": {
                                "surfaces": ["modal"],
                                "params": {
                                    "note_id": {"type": "string", "required": True},
                                },
                            }
                        },
                    },
                    "registry": {
                        "modals": {
                            "demo_modal": {
                                "implements": ["demo.note.edit"],
                                "schema": {
                                    "id": "demo_modal",
                                    "interface": {
                                        "schema": "adaos.ui.modal.interface.v1",
                                        "defaultRoute": "note.edit",
                                        "routes": {
                                            "note.edit": {
                                                "view": "demo.note.edit",
                                                "params": {},
                                                "state": {"selectedNoteId": "$params.note_id"},
                                            }
                                        },
                                    },
                                    "widgets": [],
                                },
                            }
                        }
                    },
                }
            ],
            desktop_scenarios=[],
        )
    )

    diagnostics = resolved.application["diagnostics"]["webui_contract"]
    codes = {item["code"] for item in diagnostics["issues"]}

    assert diagnostics["status"] == "invalid"
    assert diagnostics["error_count"] >= 2
    assert "webui.modal.route_missing_view_param" in codes
    assert "webui.modal.state_unknown_param" in codes
    assert {issue.code for issue in captured} >= {
        "webui.modal.route_missing_view_param",
        "webui.modal.state_unknown_param",
    }


def test_phase3_resolver_merges_version_skewed_skill_interface_views(monkeypatch) -> None:
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub-node")
    monkeypatch.setattr(
        webspace_runtime_module,
        "node_display_from_config",
        lambda _conf: {
            "node_label": "Hub",
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "",
        },
    )
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase3-version-skewed-interface",
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_application={"desktop": {"pageSchema": {"id": "desktop-page"}}},
            scenario_catalog={"apps": [], "widgets": []},
            scenario_registry={"modals": [], "widgets": []},
            overlay_snapshot={"installed": {"apps": [], "widgets": []}},
            live_state={"desktop": {"installed": {}}, "routing": {}},
            skill_decls=[
                {
                    "skill": "mediaserver",
                    "space": "default",
                    "interface": {
                        "schema": "adaos.ui.skill_interface.v1",
                        "defaultView": "mediaserver.diagnostics",
                        "views": {
                            "mediaserver.diagnostics": {"title": "Diagnostics", "surfaces": ["modal"]},
                            "mediaserver.shared": {"title": "Local contract", "surfaces": ["modal"]},
                        },
                        "transitions": [
                            {
                                "from": "mediaserver.diagnostics",
                                "on": "inspect",
                                "to": "mediaserver.shared",
                                "params": {"authority": "local"},
                            }
                        ],
                    },
                },
                {
                    "skill": "subnet.member.member-1",
                    "space": "default",
                    "node_id": "member-1",
                    "interfaces": {
                        "mediaserver": {
                            "schema": "adaos.ui.skill_interface.v1",
                            "defaultView": "mediaserver.mediaserver_modal",
                            "views": {
                                "mediaserver.mediaserver_modal": {"title": "Legacy player", "surfaces": ["modal"]},
                                "mediaserver.shared": {"title": "Remote contract", "surfaces": ["modal"]},
                            },
                            "transitions": [
                                {
                                    "from": "mediaserver.diagnostics",
                                    "on": "inspect",
                                    "to": "mediaserver.shared",
                                    "params": {"authority": "remote"},
                                },
                                {
                                    "from": "mediaserver.shared",
                                    "on": "play",
                                    "to": "mediaserver.mediaserver_modal",
                                },
                            ],
                        }
                    },
                    "registry": {
                        "modals": {
                            "mediaserver_modal": {
                                "implements": ["mediaserver.mediaserver_modal"],
                                "schema": {
                                    "id": "mediaserver_modal",
                                    "interface": {
                                        "schema": "adaos.ui.modal.interface.v1",
                                        "defaultRoute": "library",
                                        "routes": {
                                            "library": {
                                                "view": "mediaserver.mediaserver_modal",
                                                "params": {},
                                            }
                                        },
                                    },
                                    "widgets": [],
                                },
                            }
                        },
                        "widgets": {},
                    },
                },
            ],
            desktop_scenarios=[],
        )
    )

    interface = resolved.application["interfaces"]["mediaserver"]
    assert interface["defaultView"] == "mediaserver.diagnostics"
    assert set(interface["views"]) == {
        "mediaserver.diagnostics",
        "mediaserver.mediaserver_modal",
        "mediaserver.shared",
    }
    assert interface["views"]["mediaserver.shared"]["title"] == "Local contract"
    assert len(interface["transitions"]) == 2
    diagnostic_codes = {
        item["code"]
        for item in resolved.application.get("diagnostics", {}).get("webui_contract", {}).get("issues", [])
    }
    assert "webui.modal.implements_unknown_view" not in diagnostic_codes
    assert "webui.modal.route_unknown_view" not in diagnostic_codes


def test_phase5_resolver_cache_reuses_same_inputs_without_leaking_mutations() -> None:
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    inputs = webspace_runtime_module.WebspaceResolverInputs(
        webspace_id="phase5-resolver-cache",
        scenario_id="prompt_engineer_scenario",
        source_mode="workspace",
        scenario_application={"desktop": {"pageSchema": {"id": "cached-page"}}},
        scenario_catalog={"apps": [{"id": "cached-app", "title": "Cached App"}], "widgets": []},
        scenario_registry={"modals": [], "widgets": []},
        overlay_snapshot={"installed": {"apps": [], "widgets": []}},
        live_state={"desktop": {"installed": {}}, "routing": {}},
        skill_decls=[],
        desktop_scenarios=[],
        scenario_source="loader:workspace",
        legacy_scenario_fallback=False,
    )

    first = runtime.resolve_webspace(inputs)
    first_debug = dict(runtime._last_resolver_debug or {})
    first.catalog["apps"].append({"id": "mutated-app"})

    second = runtime.resolve_webspace(inputs)
    second_debug = dict(runtime._last_resolver_debug or {})

    assert first_debug["cache_hit"] is False
    assert second_debug["cache_hit"] is True
    assert second_debug["source"] == "loader:workspace"
    assert second_debug["legacy_fallback"] is False
    assert set(second_debug["cache_keys"].keys()) >= {"scenario", "skills", "overlay"}
    assert [item["id"] for item in second.catalog["apps"]] == ["cached-app"]


def test_resolver_reuses_scenario_core_across_webspaces_without_overlay_leakage() -> None:
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    common = {
        "scenario_id": "shared-generated-scenario",
        "source_mode": "dev",
        "scenario_application": {"desktop": {"pageSchema": {"id": "shared-page"}}},
        "scenario_catalog": {"apps": [{"id": "shared-app"}], "widgets": []},
        "scenario_registry": {"modals": [], "widgets": []},
        "live_state": {},
        "skill_decls": [],
        "skill_decls_fingerprint": "skills-v1",
        "desktop_scenarios": [],
        "scenario_source": "loader:dev:shared-v1",
    }

    first = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="preview-a",
            overlay_snapshot={"installed": {"apps": ["only-a"], "widgets": []}},
            **common,
        )
    )
    second = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="preview-b",
            overlay_snapshot={"installed": {"apps": ["only-b"], "widgets": []}},
            **common,
        )
    )
    second_debug = dict(runtime._last_resolver_debug or {})
    runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="preview-c",
            overlay_snapshot={"installed": {"apps": ["only-c"], "widgets": []}},
            metadata={
                "materialization": {
                    "identity": {
                        "user_id": "operator",
                        "roles_hash": "admin-role-hash",
                        "policy_fingerprint": "policy-v2",
                    }
                }
            },
            **common,
        )
    )
    third_debug = dict(runtime._last_resolver_debug or {})

    assert second_debug["cache_hit"] is False
    assert second_debug["core_cache_hit"] is True
    assert third_debug["core_cache_hit"] is False
    assert first.webspace_id == "preview-a"
    assert second.webspace_id == "preview-b"
    assert first.installed["apps"] == ["only-a"]
    assert second.installed["apps"] == ["only-b"]
    second.application["desktop"]["pageSchema"]["id"] = "mutated"
    assert first.application["desktop"]["pageSchema"]["id"] == "shared-page"


def test_phase5_apply_summary_reports_changed_and_unchanged_top_level_branches() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    fake_state = {
        "ui": _CountingMap(),
        "registry": _CountingMap(),
        "data": _CountingMap(),
    }
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="phase5-apply-summary",
        scenario_id="prompt_engineer_scenario",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "apply-page"}}},
        catalog={"apps": [{"id": "apply-app", "title": "Apply App"}], "widgets": []},
        registry={"modals": ["apply-modal"], "widgets": []},
        installed={"apps": ["apply-app"], "widgets": []},
        desktop={"installed": {"apps": ["apply-app"], "widgets": []}},
        routing={"routes": {"home": "/apply"}},
        skill_decls=[],
    )
    fake_doc = _FakeDoc(fake_state)

    runtime._apply_resolved_state_in_doc(fake_doc, "phase5-apply-summary", resolved)
    first_summary = dict(runtime._last_apply_summary or {})

    runtime._apply_resolved_state_in_doc(fake_doc, "phase5-apply-summary", resolved)
    second_summary = dict(runtime._last_apply_summary or {})

    assert first_summary["changed_branches"] == 8
    assert first_summary["unchanged_branches"] == 0
    assert first_summary["changed_paths"] == [
        "ui.application",
        "registry.merged",
        "runtime.environment",
        "data.catalog",
        "data.installed",
        "data.desktop",
        "data.webio",
        "data.routing",
    ]
    assert first_summary["phases"]["structure"]["changed_paths"] == [
        "ui.application",
        "registry.merged",
        "runtime.environment",
    ]
    assert first_summary["phases"]["interactive"]["changed_paths"] == [
        "data.catalog",
        "data.installed",
        "data.desktop",
        "data.webio",
        "data.routing",
    ]
    assert second_summary["changed_branches"] == 0
    assert second_summary["unchanged_branches"] == 8
    assert second_summary["failed_branches"] == 0
    assert second_summary["transaction_total"] == 2
    assert second_summary["changed_paths"] == []
    assert second_summary["phases"]["structure"]["unchanged_branches"] == 3
    assert second_summary["phases"]["interactive"]["unchanged_branches"] == 5
    assert second_summary["branch_apply_modes"]["ui.application"] == "fingerprint_unchanged"
    assert second_summary["branch_apply_modes"]["data.catalog"] == "fingerprint_unchanged"
    assert "total" in second_summary["branch_timings_ms"]["ui.application"]
    assert "branch_timings_ms" in second_summary["phases"]["structure"]
    assert "branch_apply_modes" in second_summary["phases"]["interactive"]
    assert runtime._last_apply_phase_timings_ms is not None
    assert "apply_structure" in runtime._last_apply_phase_timings_ms
    assert "apply_interactive" in runtime._last_apply_phase_timings_ms
    assert fake_state["ui"].set_count == 2
    assert fake_state["data"].set_count == 5
    assert fake_state["registry"].set_count == 2
    assert fake_doc.transaction_count == 4
    assert "runtime_meta" in fake_state["registry"]


def test_phase5_apply_trusts_previous_materialized_fingerprint_without_live_branch_hash(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_apply_ydoc_defaults_in_txn", lambda ydoc, txn, skill_decls: None)

    live_webio_sentinel = object()
    original_fingerprint = webspace_runtime_module._fingerprint_json_like

    def _fingerprint(value):
        if value is live_webio_sentinel:
            raise AssertionError("trusted fast path should not fingerprint live data.webio")
        return original_fingerprint(value)

    monkeypatch.setattr(webspace_runtime_module, "_fingerprint_json_like", _fingerprint)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_trust_previous_materialized_branch_fingerprints_enabled",
        lambda: True,
    )

    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="phase5-trusted-fingerprint",
        scenario_id="prompt_engineer_scenario",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "next"}}},
        catalog={"apps": [], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": [], "widgets": []},
        desktop={"installed": {"apps": [], "widgets": []}},
        webio={"receivers": {"voice_chat.messages": {"mode": "append"}}},
        routing={"routes": {}},
        skill_decls=[],
    )
    previous = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id=resolved.webspace_id,
        scenario_id="web_desktop",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "prev"}}},
        catalog={"apps": [], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": [], "widgets": []},
        desktop={"installed": {"apps": [], "widgets": []}},
        webio=resolved.webio,
        routing={"routes": {}},
        skill_decls=[],
    )
    branch_fingerprints = {
        "ui.application": "app-next",
        "data.catalog": "catalog-next",
        "data.installed": "installed-next",
        "data.desktop": "desktop-next",
        "data.webio": "webio-same",
        "data.routing": "routing-next",
        "registry.merged": "registry-next",
    }
    fake_state = {
        "ui": _CountingMap({"application": previous.application, "current_scenario": previous.scenario_id}),
        "registry": _CountingMap(
            {
                "merged": previous.registry,
                "runtime_meta": {
                    webspace_runtime_module._RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY: {
                        "data.webio": "webio-same",
                    }
                },
            }
        ),
        "data": _CountingMap(
            {
                "catalog": previous.catalog,
                "installed": previous.installed,
                "desktop": previous.desktop,
                "webio": live_webio_sentinel,
                "routing": previous.routing,
            }
        ),
        "runtime": _CountingMap({"environment": webspace_runtime_module.runtime_environment_payload()}),
    }

    runtime._apply_resolved_state_in_doc(
        _FakeDoc(fake_state),
        "phase5-trusted-fingerprint",
        resolved,
        previous_resolved=previous,
        resolved_branch_fingerprints_override=branch_fingerprints,
        previous_branch_fingerprints_override={"data.webio": "webio-same"},
    )

    summary = runtime._last_apply_summary or {}
    assert summary["branch_apply_modes"]["data.webio"] == "trusted_previous_fingerprint_unchanged"
    assert summary["trusted_fingerprint_unchanged_paths"] == ["data.webio"]
    assert summary["branch_timings_ms"]["data.webio"]["presence_check"] >= 0.0


def test_scenario_switch_verifies_and_repairs_stale_branch_behind_matching_fingerprint(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_apply_ydoc_defaults_in_txn", lambda ydoc, txn, skill_decls: None)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_trust_previous_materialized_branch_fingerprints_enabled",
        lambda: True,
    )

    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="scenario-switch-verification",
        scenario_id="web_desktop",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "desktop", "title": "Desktop"}}},
        catalog={"apps": [], "widgets": []},
        registry={"modals": [], "widgets": []},
        installed={"apps": [], "widgets": []},
        desktop={"installed": {"apps": [], "widgets": []}},
        webio={"receivers": {}},
        routing={"routes": {}},
        skill_decls=[],
    )
    fingerprints = webspace_runtime_module._resolved_output_branch_fingerprints(resolved)
    fake_state = {
        "ui": _CountingMap(
            {
                "current_scenario": "web_desktop",
                # Reproduces the observed split state: the selector and stored
                # fingerprint already name home, while the effective UI branch
                # still contains the previous scenario.
                "application": {"desktop": {"pageSchema": {"id": "research", "title": "Research Workbench"}}},
            }
        ),
        "registry": _CountingMap(
            {
                "merged": resolved.registry,
                "runtime_meta": {
                    webspace_runtime_module._RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY: dict(fingerprints),
                },
            }
        ),
        "data": _CountingMap(
            {
                "catalog": resolved.catalog,
                "installed": resolved.installed,
                "desktop": resolved.desktop,
                "webio": resolved.webio,
                "routing": resolved.routing,
            }
        ),
        "runtime": _CountingMap({"environment": webspace_runtime_module.runtime_environment_payload()}),
    }

    runtime._apply_resolved_state_in_doc(
        _FakeDoc(fake_state),
        resolved.webspace_id,
        resolved,
        previous_resolved=resolved,
        resolved_branch_fingerprints_override=fingerprints,
        previous_branch_fingerprints_override=fingerprints,
        force_selector_write=True,
        verify_branch_fingerprints=True,
    )

    assert fake_state["ui"].get("application")["desktop"]["pageSchema"]["id"] == "desktop"
    summary = runtime._last_apply_summary or {}
    assert summary["verified_branch_fingerprints"] is True
    assert "ui.application" in summary["stale_fingerprint_paths"]
    assert summary["branch_apply_modes"]["ui.application"] == "changed:replace"


def test_phase5_apply_trusts_previous_materialized_fingerprint_for_patch_base(monkeypatch) -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    monkeypatch.setattr(runtime, "_apply_ydoc_defaults_in_txn", lambda ydoc, txn, skill_decls: None)

    live_catalog_sentinel = object()
    original_fingerprint = webspace_runtime_module._fingerprint_json_like

    def _fingerprint(value):
        if value is live_catalog_sentinel:
            raise AssertionError("trusted patch base should not fingerprint live data.catalog")
        return original_fingerprint(value)

    monkeypatch.setattr(webspace_runtime_module, "_fingerprint_json_like", _fingerprint)
    monkeypatch.setattr(
        webspace_runtime_module,
        "_trust_previous_materialized_branch_fingerprints_enabled",
        lambda: True,
    )

    previous_catalog = {"apps": [{"id": "old"}], "widgets": []}
    next_catalog = {"apps": [{"id": "new"}], "widgets": []}
    previous_catalog_fingerprint = original_fingerprint(previous_catalog)
    next_catalog_fingerprint = original_fingerprint(next_catalog)
    resolved = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id="phase5-trusted-patch-base",
        scenario_id="builder",
        source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "next"}}},
        catalog=next_catalog,
        registry={"modals": [], "widgets": []},
        installed={"apps": [], "widgets": []},
        desktop={"installed": {"apps": [], "widgets": []}},
        webio={"receivers": {}},
        routing={"routes": {}},
        skill_decls=[],
    )
    previous = webspace_runtime_module.WebspaceResolverOutputs(
        webspace_id=resolved.webspace_id,
        scenario_id="web_desktop",
        source_mode="workspace",
        application=resolved.application,
        catalog=previous_catalog,
        registry=resolved.registry,
        installed=resolved.installed,
        desktop=resolved.desktop,
        webio=resolved.webio,
        routing=resolved.routing,
        skill_decls=[],
    )
    fake_state = {
        "ui": _CountingMap({"application": previous.application, "current_scenario": previous.scenario_id}),
        "registry": _CountingMap(
            {
                "merged": previous.registry,
                "runtime_meta": {
                    webspace_runtime_module._RUNTIME_META_EFFECTIVE_BRANCH_FINGERPRINTS_KEY: {
                        "data.catalog": previous_catalog_fingerprint,
                    }
                },
            }
        ),
        "data": _CountingMap(
            {
                "catalog": live_catalog_sentinel,
                "installed": previous.installed,
                "desktop": previous.desktop,
                "webio": previous.webio,
                "routing": previous.routing,
            }
        ),
        "runtime": _CountingMap({"environment": webspace_runtime_module.runtime_environment_payload()}),
    }

    runtime._apply_resolved_state_in_doc(
        _FakeDoc(fake_state),
        "phase5-trusted-patch-base",
        resolved,
        previous_resolved=previous,
        resolved_branch_fingerprints_override={"data.catalog": next_catalog_fingerprint},
        previous_branch_fingerprints_override={"data.catalog": previous_catalog_fingerprint},
    )

    summary = runtime._last_apply_summary or {}
    assert summary["trusted_previous_fingerprint_patch_paths"] == ["data.catalog"]
    assert summary["branch_timings_ms"]["data.catalog"]["previous_fingerprint_trusted"] >= 0.0
    assert "previous_actual_fingerprint" not in summary["branch_timings_ms"]["data.catalog"]
    assert fake_state["data"].get("catalog") == next_catalog


def test_phase5_derive_phase_timings_uses_semantic_phase_breakdown() -> None:
    phase_timings = webspace_runtime_module._derive_phase_timings(
        switch_timings_ms={
            "describe_state_before": 0.5,
            "resolve_manifest_policy": 0.5,
            "validate_scenario": 1.0,
            "write_switch_pointer": 1.5,
            "total": 4.0,
        },
        rebuild_timings_ms={
            "projection_refresh": 2.0,
            "workflow_sync": 1.0,
            "event_emit": 1.0,
            "total": 10.0,
        },
        semantic_rebuild_timings_ms={
            "collect_inputs": 1.0,
            "resolve": 1.0,
            "apply_structure": 1.0,
            "apply_interactive": 2.0,
            "apply": 3.0,
            "to_registry_entry": 0.5,
            "total": 6.0,
        },
        switch_mode="pointer_only",
    )

    assert phase_timings is not None
    assert phase_timings["time_to_pointer_update"] == 3.5
    assert phase_timings["time_to_first_structure"] == 9.0
    assert phase_timings["time_to_interactive_focus"] == 11.0
    assert phase_timings["time_to_full_hydration"] == 12.0


def test_phase5_atomic_commit_reports_selector_visible_at_full_hydration() -> None:
    phase_timings = webspace_runtime_module._derive_phase_timings(
        switch_timings_ms={
            "describe_state_before": 0.5,
            "resolve_manifest_policy": 0.5,
            "validate_scenario": 1.0,
            "defer_switch_pointer": 0.0,
            "total": 4.0,
        },
        rebuild_timings_ms={"projection_refresh": 2.0, "total": 10.0},
        semantic_rebuild_timings_ms={
            "collect_inputs": 1.0,
            "resolve": 1.0,
            "apply_structure": 1.0,
            "apply_interactive": 2.0,
            "total": 6.0,
        },
        switch_mode="pointer_only",
    )

    assert phase_timings is not None
    assert phase_timings["time_to_accept"] == 4.0
    assert phase_timings["time_to_pointer_update"] == 12.0
    assert phase_timings["time_to_first_structure"] == 12.0
    assert phase_timings["time_to_interactive_focus"] == 12.0
    assert phase_timings["time_to_full_hydration"] == 12.0


def test_phase5_resolver_omits_catalog_modals_without_desktop_library_capability() -> None:
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(
        webspace_runtime_module.WebspaceResolverInputs(
            webspace_id="phase5-no-library",
            scenario_id="prompt_engineer_scenario",
            source_mode="workspace",
            scenario_application={"id": "prompt-root", "modals": {"scenario_modal": {"title": "Scenario"}}},
            scenario_catalog={"apps": [{"id": "scenario-app", "title": "Scenario App"}]},
            scenario_registry={"modals": ["scenario_modal"], "widgets": []},
            overlay_snapshot={"installed": {"apps": [], "widgets": []}},
            live_state={"desktop": {"installed": {}}, "routing": {}},
            skill_decls=[],
            desktop_scenarios=[],
        )
    )

    assert resolved.registry["modals"] == ["scenario_modal", "scenario_switcher"]
    assert "apps_catalog" not in (resolved.application.get("modals") or {})
    assert "widgets_catalog" not in (resolved.application.get("modals") or {})


def test_skill_activated_event_can_defer_webspace_rebuild(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    async def _fake_rebuild(webspace_id: str, *, action: str = "rebuild", source_of_truth: str = "workspace", **kwargs):  # noqa: ARG001
        calls.append((webspace_id, action, source_of_truth))
        return None

    monkeypatch.setattr(webspace_runtime_module, "rebuild_webspace_from_sources", _fake_rebuild)

    asyncio.run(
        webspace_runtime_module._on_skill_activated(
            {
                "skill_name": "weather_skill",
                "webspace_id": "default",
                "defer_webspace_rebuild": True,
            }
        )
    )

    assert calls == []


def test_phase4_rebuild_status_exposes_legacy_resolver_fallback(monkeypatch) -> None:
    webspace_runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    webspace_id = "phase4-legacy-fallback"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Legacy Fallback",
        kind="workspace",
        source_mode="workspace",
        home_scenario="prompt_engineer_scenario",
    )

    fake_state = {
        "ui": _FakeMap(
            {
                "current_scenario": "prompt_engineer_scenario",
                "scenarios": {
                    "hub": {
                        "prompt_engineer_scenario": {"application": {"desktop": {"pageSchema": {"id": "legacy-page"}}}}
                    }
                },
            }
        ),
        "registry": _FakeMap(
            {
                "scenarios": {
                    "hub": {
                        "prompt_engineer_scenario": {"modals": ["legacy-modal"], "widgets": []}
                    }
                }
            }
        ),
        "data": _FakeMap(
            {
                "scenarios": {
                    "hub": {
                        "prompt_engineer_scenario": {"catalog": {"apps": [{"id": "legacy-app"}], "widgets": []}}
                    }
                }
            }
        ),
    }

    async def _fake_refresh(
        ctx,
        webspace_id: str,
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:  # noqa: ARG001
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "workspace",
            "rules_loaded": 0,
        }

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_collect_skill_decls", lambda self, mode="mixed": [])
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "_list_desktop_scenarios", lambda self, space: [])
    monkeypatch.setattr(
        webspace_runtime_module.scenarios_loader,
        "read_content",
        lambda scenario_id, *, space="workspace": {},
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            webspace_id,
            action="rebuild",
            source_of_truth="scenario_projection",
        )
    )
    status = webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)

    assert result["accepted"] is True
    assert result["resolver"]["source"] == "legacy_yjs"
    assert result["resolver"]["legacy_fallback"] is True
    assert status["resolver"]["source"] == "legacy_yjs"
    assert status["resolver"]["legacy_fallback"] is True


def test_rebuild_status_exposes_live_room_refresh_fields() -> None:
    webspace_id = "status-live-room-refresh"
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="ready",
        pending=False,
        live_room_update_requested=True,
        live_room_publish=False,
        live_room_refresh={"ok": True, "room_repaired": True},
    )

    status = webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)

    assert status["live_room_update_requested"] is True
    assert status["live_room_publish"] is False
    assert status["live_room_refresh"] == {"ok": True, "room_repaired": True}


def test_restore_webspace_from_snapshot_reconciles_runtime(monkeypatch) -> None:
    fake_state = {
        "ui": _FakeMap({"current_scenario": "restored_prompt_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    rebuilds: list[str] = []
    workflows: list[tuple[str, str]] = []
    emitted: list[tuple[str, dict[str, object], str]] = []

    class _Bus:
        def publish(self, _event) -> None:
            return None

    fake_ctx = SimpleNamespace(bus=_Bus())

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
        rebuilds.append(webspace_id)
        return SimpleNamespace(scenario_id="restored_prompt_scenario", apps=[{"id": "app-1"}], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):
        workflows.append((scenario_id, webspace_id))
        return None

    async def _fake_restore_ystore(_webspace_id: str) -> dict[str, object]:
        return {"ok": True, "accepted": True, "snapshot_path": "state/ystores/default.snapshot"}

    async def _fake_reset_live_room(_webspace_id: str, close_reason: str = "webspace_restore") -> dict[str, object]:
        return {"accepted": True, "close_reason": close_reason}

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: fake_ctx)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setattr(
        webspace_runtime_module,
        "emit",
        lambda bus, topic, payload, source: emitted.append((topic, dict(payload), source)),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(reset_live_webspace_room=_fake_reset_live_room),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(restore_ystore_for_webspace=_fake_restore_ystore),
    )

    result = asyncio.run(webspace_runtime_module.restore_webspace_from_snapshot("phase3-restore"))

    assert rebuilds == ["phase3-restore"]
    assert workflows == [("restored_prompt_scenario", "phase3-restore")]
    assert emitted == [
        (
            "desktop.webspace.restored",
            {
                "webspace_id": "phase3-restore",
                "action": "restore",
                "scenario_id": "restored_prompt_scenario",
                "snapshot_path": "state/ystores/default.snapshot",
                "_event_type": "desktop.webspace.restored",
            },
            "scenario.webspace_runtime",
        )
    ]
    assert result["accepted"] is True
    assert result["scenario_id"] == "restored_prompt_scenario"
    assert result["scenario_resolution"] == "current_scenario"
    assert result["source_of_truth"] == "snapshot"
    assert result["projection_refresh"]["scenario_id"] == "restored_prompt_scenario"
    assert result["projection_refresh"]["scenario_resolution"] == "current_scenario"


def test_phase3_reload_and_reset_rebuild_sync_workflow_for_target_scenario(monkeypatch) -> None:
    class _Bus:
        def publish(self, _event) -> None:
            return None

    for action in ("reload", "reset"):
        fake_state = {
            "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
            "registry": _FakeMap(),
            "data": _FakeMap(),
        }
        rebuilds: list[str] = []
        workflows: list[tuple[str, str]] = []
        emitted: list[tuple[str, dict[str, object], str]] = []
        fake_ctx = SimpleNamespace(bus=_Bus())

        async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
            rebuilds.append(webspace_id)
            self._last_rebuild_timings_ms = {
                "collect_inputs": 1.0,
                "resolve": 2.0,
                "apply": 3.0,
                "to_registry_entry": 0.5,
                "total": 6.5,
            }
            self._last_apply_summary = {
                "branch_count": 6,
                "changed_branches": 2,
                "unchanged_branches": 4,
                "failed_branches": 0,
                "changed_paths": ["ui.application", "registry.merged"],
                "defaults_failed": False,
            }
            return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[{"id": "app-1"}], widgets=[])

        async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):
            workflows.append((scenario_id, webspace_id))
            return None

        async def _fake_refresh(ctx, webspace_id: str, *, scenario_id: str | None = None, scenario_resolution: str | None = None):
            return {
                "attempted": True,
                "scenario_id": scenario_id,
                "scenario_resolution": scenario_resolution,
                "space": "workspace",
                "rules_loaded": 1,
                "source": "scenario_manifest",
            }

        monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
        monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: fake_ctx)
        monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
        monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
        monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
        monkeypatch.setattr(
            webspace_runtime_module,
            "emit",
            lambda bus, topic, payload, source: emitted.append((topic, dict(payload), source)),
        )

        result = asyncio.run(
            webspace_runtime_module.rebuild_webspace_from_sources(
                f"phase3-{action}-workflow-sync",
                action=action,
                scenario_id="prompt_engineer_scenario",
                scenario_resolution="explicit",
                source_of_truth="scenario",
            )
        )

        assert rebuilds == [f"phase3-{action}-workflow-sync"]
        assert workflows == [("prompt_engineer_scenario", f"phase3-{action}-workflow-sync")]
        assert emitted == [
            (
                "desktop.webspace.reloaded",
                {
                    "webspace_id": f"phase3-{action}-workflow-sync",
                    "action": action,
                    "scenario_id": "prompt_engineer_scenario",
                    "_event_type": "desktop.webspace.reloaded",
                },
                "scenario.webspace_runtime",
            )
        ]
        assert result["accepted"] is True
        assert result["scenario_resolution"] == "explicit"
        assert result["projection_refresh"]["scenario_resolution"] == "explicit"
        assert "workflow_sync" in result["timings_ms"]


def test_phase3_reload_reuses_live_runtime_without_reset(monkeypatch) -> None:
    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    fake_ctx = SimpleNamespace(bus=SimpleNamespace(publish=lambda _event: None))
    project_calls: list[tuple[str, str, bool]] = []
    seed_calls: list[tuple[str, str]] = []
    reset_calls: list[tuple[str, str]] = []
    rebuilds: list[str] = []
    listing_syncs: list[str] = []

    async def _fake_project(
        webspace_id: str,
        scenario_id: str,
        *,
        dev: bool | None = None,  # noqa: ARG001
        emit_event: bool = True,
    ) -> None:
        project_calls.append((webspace_id, scenario_id, emit_event))

    async def _fake_seed(
        webspace_id: str,
        scenario_id: str,
        *,
        dev: bool | None = None,  # noqa: ARG001
    ) -> None:
        seed_calls.append((webspace_id, scenario_id))

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "workspace",
            "rules_loaded": 1,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG002
        rebuilds.append(webspace_id)
        self._last_rebuild_timings_ms = {"total": 1.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_listing() -> None:
        listing_syncs.append("default")

    async def _fake_reset_live_room(
        _webspace_id: str,
        close_reason: str = "webspace_reset",
        persist_ystore_snapshot: bool = True,
    ) -> dict[str, object]:
        reset_calls.append(("room", close_reason))
        return {"accepted": True}

    def _fake_reset_ystore(_webspace_id: str) -> None:
        reset_calls.append(("ystore", "reset"))

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: fake_ctx)
    monkeypatch.setattr(webspace_runtime_module, "_project_webspace_from_scenario", _fake_project)
    monkeypatch.setattr(webspace_runtime_module, "_seed_webspace_from_scenario_with_options", _fake_seed)
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_listing)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(reset_live_webspace_room=_fake_reset_live_room),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=_fake_reset_ystore),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase3-soft-reload",
            action="reload",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario",
            reseed_from_scenario=True,
        )
    )

    assert project_calls == [("phase3-soft-reload", "prompt_engineer_scenario", False)]
    assert seed_calls == []
    assert reset_calls == []
    assert rebuilds == ["phase3-soft-reload"]
    assert listing_syncs == ["default"]
    assert result["accepted"] is True
    assert "project_scenario_payload" in result["timings_ms"]
    assert "reset_runtime_state" not in result["timings_ms"]


def test_phase3_reset_keeps_hard_runtime_reset(monkeypatch) -> None:
    fake_state = {
        "ui": _FakeMap({"current_scenario": "prompt_engineer_scenario"}),
        "registry": _FakeMap(),
        "data": _FakeMap(),
    }
    fake_ctx = SimpleNamespace(bus=SimpleNamespace(publish=lambda _event: None))
    project_calls: list[tuple[str, str, bool]] = []
    seed_calls: list[tuple[str, str]] = []
    reset_calls: list[tuple[str, str, bool | None]] = []
    emitted: list[tuple[str, dict[str, object], str]] = []

    async def _fake_project(
        webspace_id: str,
        scenario_id: str,
        *,
        dev: bool | None = None,  # noqa: ARG001
        emit_event: bool = True,
    ) -> None:
        project_calls.append((webspace_id, scenario_id, emit_event))

    async def _fake_seed(
        webspace_id: str,
        scenario_id: str,
        *,
        dev: bool | None = None,  # noqa: ARG001
    ) -> None:
        seed_calls.append((webspace_id, scenario_id))

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
            "space": "workspace",
            "rules_loaded": 1,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001, ARG002
        self._last_rebuild_timings_ms = {"total": 1.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_listing() -> None:
        return None

    async def _fake_reset_live_room(
        _webspace_id: str,
        close_reason: str = "webspace_reset",
        persist_ystore_snapshot: bool = True,
    ) -> dict[str, object]:
        reset_calls.append(("room", close_reason, persist_ystore_snapshot))
        return {"accepted": True}

    async def _fake_reset_ystore(_webspace_id: str) -> None:
        reset_calls.append(("ystore", "reset", None))

    monkeypatch.setattr(webspace_runtime_module, "async_get_ydoc", lambda _webspace_id: _FakeAsyncDoc(fake_state))
    monkeypatch.setattr(webspace_runtime_module, "get_ctx", lambda: fake_ctx)
    monkeypatch.setattr(webspace_runtime_module, "_project_webspace_from_scenario", _fake_project)
    monkeypatch.setattr(webspace_runtime_module, "_seed_webspace_from_scenario_with_options", _fake_seed)
    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module, "_sync_webspace_listing", _fake_listing)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(
        webspace_runtime_module,
        "emit",
        lambda bus, topic, payload, source: emitted.append((topic, dict(payload), source)),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(reset_live_webspace_room=_fake_reset_live_room),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace_async=_fake_reset_ystore),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase3-hard-reset",
            action="reset",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario",
            reseed_from_scenario=True,
            event_payload={"recreate_room": True, "_meta": {"cmd_id": "cmd-hard-reload"}},
        )
    )

    assert project_calls == []
    assert seed_calls == [("phase3-hard-reset", "prompt_engineer_scenario")]
    assert reset_calls == [("room", "webspace_reset", False), ("ystore", "reset", None)]
    assert emitted == [
        (
            "desktop.webspace.reloaded",
            {
                "webspace_id": "phase3-hard-reset",
                "action": "reset",
                "scenario_id": "prompt_engineer_scenario",
                "_meta": {"cmd_id": "cmd-hard-reload"},
                "_event_type": "desktop.webspace.reloaded",
            },
            "scenario.webspace_runtime",
        )
    ]
    assert result["accepted"] is True
    assert "reset_runtime_state" in result["timings_ms"]
    assert "seed_from_scenario" in result["timings_ms"]
