from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=type("YDoc", (), {}))
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

from adaos.services.scenario import manager as scenario_manager


def test_bootstrap_dependencies_reports_structured_lifecycle_results(monkeypatch) -> None:
    calls: list[str] = []
    events: list[Any] = []

    class _FakeSkillManager:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def install(self, name: str) -> None:
            calls.append(f"install:{name}")
            if name == "install_bad":
                raise RuntimeError("install failed")

        def prepare_runtime(self, name: str, run_tests: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}")
            if name == "prepare_bad":
                raise RuntimeError("prepare failed")
            return SimpleNamespace(version=f"{name}-1.0.0", slot="B")

        def activate_for_space(
            self,
            name: str,
            *,
            version: str | None = None,
            slot: str | None = None,
            space: str = "default",
            webspace_id: str = "default",
        ) -> None:
            calls.append(f"activate_for_space:{name}:{version}:{slot}:{space}:{webspace_id}")
            if name == "activate_bad":
                raise RuntimeError("activate failed")

    monkeypatch.setattr(
        scenario_manager,
        "get_ctx",
        lambda: SimpleNamespace(sql=object(), skills_repo=object(), git=object(), paths=object(), caps=object()),
    )
    monkeypatch.setattr(
        scenario_manager,
        "read_manifest",
        lambda scenario_id: {"depends": ["ok_skill", "install_bad", "prepare_bad", "activate_bad"]},
    )
    monkeypatch.setattr(scenario_manager, "SqliteSkillRegistry", lambda sql: object())
    monkeypatch.setattr(scenario_manager, "SkillManager", _FakeSkillManager)

    mgr = scenario_manager.ScenarioManager(
        repo=object(),
        registry=object(),
        git=object(),
        paths=object(),
        bus=SimpleNamespace(publish=lambda evt: events.append(evt)),
        caps=SimpleNamespace(require=lambda *args, **kwargs: None),
    )

    result = mgr.bootstrap_dependencies("demo_scene", webspace_id="desktop")

    assert result["ok"] is False
    assert result["scenario_id"] == "demo_scene"
    assert result["webspace_id"] == "desktop"
    assert result["required"] == ["ok_skill", "install_bad", "prepare_bad", "activate_bad"]
    assert result["succeeded"] == ["ok_skill"]
    assert result["failed"] == ["install_bad", "prepare_bad", "activate_bad"]
    assert [item["name"] for item in result["items"]] == ["ok_skill", "install_bad", "prepare_bad", "activate_bad"]
    assert result["items"][0]["installed"] is True
    assert result["items"][0]["prepared"] is True
    assert result["items"][0]["activated"] is True
    assert result["items"][0]["version"] == "ok_skill-1.0.0"
    assert result["items"][1]["error"] == "RuntimeError: install failed"
    assert result["items"][2]["error"] == "RuntimeError: prepare failed"
    assert result["items"][3]["error"] == "RuntimeError: activate failed"
    assert mgr.last_dependency_bootstrap_result == result
    assert "activate_for_space:ok_skill:ok_skill-1.0.0:B:default:desktop" in calls
    assert events[-1].type == "scenario.dependencies.bootstrapped"
    assert events[-1].payload["failed"] == ["install_bad", "prepare_bad", "activate_bad"]


def test_install_with_deps_blocks_projection_when_required_dependency_fails_in_prod(monkeypatch) -> None:
    sync_calls: list[str] = []
    dep_result = {
        "ok": False,
        "scenario_id": "demo_scene",
        "webspace_id": "desktop",
        "required": ["bad_skill"],
        "items": [{"name": "bad_skill", "ok": False, "error": "prepare failed"}],
        "succeeded": [],
        "failed": ["bad_skill"],
        "error": "RuntimeError: prepare failed",
    }
    mgr = scenario_manager.ScenarioManager(
        repo=object(),
        registry=object(),
        git=object(),
        paths=object(),
        bus=SimpleNamespace(publish=lambda evt: None),
        caps=SimpleNamespace(require=lambda *args, **kwargs: None),
    )
    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.setattr(
        mgr,
        "install",
        lambda name, pin=None: SimpleNamespace(id=SimpleNamespace(value=name), name=name, version="0.1.0", path="/scenarios/demo_scene"),
    )
    monkeypatch.setattr(mgr, "bootstrap_dependencies", lambda scenario_id, webspace_id=None: dict(dep_result))
    monkeypatch.setattr(mgr, "sync_to_yjs", lambda scenario_id, webspace_id=None: sync_calls.append(scenario_id))

    with pytest.raises(scenario_manager.ScenarioDependencyLifecycleError) as excinfo:
        mgr.install_with_deps("demo_scene", webspace_id="desktop")

    assert excinfo.value.result["failed"] == ["bad_skill"]
    assert mgr.last_dependency_bootstrap_result["failed"] == ["bad_skill"]
    assert sync_calls == []


def test_install_with_deps_allows_degraded_projection_in_dev(monkeypatch) -> None:
    sync_calls: list[str] = []
    dep_result = {
        "ok": False,
        "scenario_id": "demo_scene",
        "webspace_id": "desktop",
        "required": ["bad_skill"],
        "items": [{"name": "bad_skill", "ok": False, "error": "prepare failed"}],
        "succeeded": [],
        "failed": ["bad_skill"],
        "error": "RuntimeError: prepare failed",
    }
    mgr = scenario_manager.ScenarioManager(
        repo=object(),
        registry=object(),
        git=object(),
        paths=object(),
        bus=SimpleNamespace(publish=lambda evt: None),
        caps=SimpleNamespace(require=lambda *args, **kwargs: None),
    )
    monkeypatch.setenv("ENV_TYPE", "dev")
    monkeypatch.setattr(
        mgr,
        "install",
        lambda name, pin=None: SimpleNamespace(id=SimpleNamespace(value=name), name=name, version="0.1.0", path="/scenarios/demo_scene"),
    )
    monkeypatch.setattr(mgr, "bootstrap_dependencies", lambda scenario_id, webspace_id=None: dict(dep_result))
    monkeypatch.setattr(mgr, "sync_to_yjs", lambda scenario_id, webspace_id=None: sync_calls.append(scenario_id))

    meta = mgr.install_with_deps("demo_scene", webspace_id="desktop")

    assert getattr(meta.id, "value") == "demo_scene"
    assert mgr.last_dependency_bootstrap_result["failed"] == ["bad_skill"]
    assert sync_calls == ["demo_scene"]
