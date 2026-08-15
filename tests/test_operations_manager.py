from __future__ import annotations

import json
import sys
import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.operations.manager import OperationManager
import adaos.services.operations.manager as operations_manager


class _FakeMap(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)

    def set(self, txn, key, value):
        self[key] = value


class _FakeTxn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeYDoc:
    def __init__(self):
        self._maps = {"runtime": _FakeMap()}

    def get_map(self, name: str):
        return self._maps.setdefault(name, _FakeMap())

    def begin_transaction(self):
        return _FakeTxn()


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FakePaths:
    def base_dir(self):
        return "test-base-dir"


class _FakeToastService:
    pushed: list[dict[str, object]] = []

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def push(self, message: str, **kwargs):
        self.pushed.append({"message": message, **kwargs})


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        bus=_FakeBus(),
        paths=_FakePaths(),
        skills_repo=object(),
        sql=object(),
        git=object(),
        caps=object(),
        settings=object(),
        scenarios_repo=object(),
    )


def test_operation_manager_projects_active_operations_to_yjs(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)

    manager = OperationManager(_make_ctx())
    operation = manager.create_operation(
        kind="skill.install",
        target_kind="skill",
        target_id="demo_skill",
        webspace_id="default",
        scope=["global", "skill.install", "skill:demo_skill"],
        message="Accepted skill install",
    )

    manager.update_operation(
        operation.operation_id,
        status="running",
        progress=25,
        message="Installing",
        current_step="skill.install",
    )

    snapshot = manager.snapshot(webspace_id="default")
    assert snapshot["active"]
    current = next(item for item in snapshot["active_items"] if item["target_id"] == "demo_skill")
    assert current["target_id"] == "demo_skill"
    assert current["status"] == "running"

    runtime_map = docs["default"].get_map("runtime")
    operations = runtime_map.get("operations")
    assert isinstance(operations, dict)
    assert current["operation_id"] in (operations.get("by_id") or {})


def test_operation_manager_records_notifications_on_completion(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    _FakeToastService.pushed = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)

    manager = OperationManager(_make_ctx())
    operation = manager.create_operation(
        kind="scenario.install",
        target_kind="scenario",
        target_id="welcome",
        webspace_id="default",
        scope=["global", "scenario.install", "scenario:welcome"],
    )
    manager.update_operation(
        operation.operation_id,
        status="succeeded",
        progress=100,
        message="Installed scenario welcome",
        result={"target_id": "welcome"},
        finished=True,
    )

    snapshot = manager.snapshot(webspace_id="default")
    assert snapshot["notifications"]
    assert snapshot["notifications"][-1]["operation_id"] == operation.operation_id
    assert _FakeToastService.pushed[-1]["message"] == "scenario welcome completed"

    runtime_map = docs["default"].get_map("runtime")
    assert isinstance(runtime_map.get("notifications"), list)


def test_operation_manager_persists_terminal_history_across_restart(monkeypatch, tmp_path: Path) -> None:
    docs: dict[str, _FakeYDoc] = {}

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    state_path = tmp_path / "operations.json"
    manager = OperationManager(_make_ctx(), state_path=state_path)
    operation = manager.create_operation(
        kind="skill.install",
        target_kind="skill",
        target_id="durable_skill",
        webspace_id="desktop",
    )

    manager.update_operation(
        operation.operation_id,
        status="succeeded",
        progress=100,
        result={"version": "1.2.3"},
        finished=True,
    )

    restored = OperationManager(_make_ctx(), state_path=state_path)
    snapshot = restored.snapshot(webspace_id="desktop")

    assert snapshot["by_id"][operation.operation_id]["status"] == "succeeded"
    assert snapshot["by_id"][operation.operation_id]["result"] == {"version": "1.2.3"}
    assert snapshot["notifications"][-1]["operation_id"] == operation.operation_id
    assert snapshot["persistence"]["healthy"] is True


def test_marketplace_install_action_parses_table_event_and_rejects_remote_target(monkeypatch) -> None:
    submitted: list[dict[str, object]] = []
    ctx = _make_ctx()
    ctx.config = SimpleNamespace(node_id="hub-local")

    def _submit(**kwargs):
        submitted.append(kwargs)
        return {"operation_id": "op-1", "target_id": kwargs["target_id"]}

    monkeypatch.setattr(operations_manager, "submit_install_operation", _submit)

    result = operations_manager.submit_marketplace_install_action(
        {
            "value": {
                "item": {
                    "kind": "scenario",
                    "id": "adaos_drive",
                    "target_node_id": "hub-local",
                    "webspace_id": "desktop",
                }
            }
        },
        initiator_kind="events_ws",
        ctx=ctx,
    )

    assert result["operation_id"] == "op-1"
    assert submitted[0]["target_kind"] == "scenario"
    assert submitted[0]["target_id"] == "adaos_drive"
    assert submitted[0]["webspace_id"] == "desktop"
    assert submitted[0]["initiator"]["target_node_id"] == "hub-local"

    with pytest.raises(ValueError, match="marketplace_install_remote_target_unsupported"):
        operations_manager.submit_marketplace_install_action(
            {
                "value": {
                    "kind": "skill",
                    "id": "adaos_drive",
                    "target_node_id": "member-remote",
                }
            },
            ctx=ctx,
        )


def test_operation_manager_marks_interrupted_work_recoverable_after_restart(monkeypatch, tmp_path: Path) -> None:
    docs: dict[str, _FakeYDoc] = {}

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    state_path = tmp_path / "operations.json"
    manager = OperationManager(_make_ctx(), state_path=state_path)
    operation = manager.create_operation(
        kind="scenario.update",
        target_kind="scenario",
        target_id="desktop",
        webspace_id="desktop",
    )
    manager.update_operation(
        operation.operation_id,
        status="running",
        progress=40,
        current_step="runtime.prepare",
    )

    restored = OperationManager(_make_ctx(), state_path=state_path)
    snapshot = restored.snapshot(webspace_id="desktop")
    recovered = snapshot["by_id"][operation.operation_id]

    assert recovered["status"] == "recoverable"
    assert recovered["error"]["type"] == "RuntimeRestart"
    assert recovered["error"]["retryable"] is True
    assert operation.operation_id not in snapshot["active"]
    assert snapshot["persistence"]["recovered_interrupted_total"] == 1
    assert snapshot["notifications"][-1]["level"] == "warning"

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["webspaces"]["desktop"]["operations"][0]["status"] == "recoverable"


def test_submit_skill_install_operation_prepares_and_activates_runtime(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    calls: list[str] = []
    rebuilds: list[tuple[str, str, str, str | None]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeSkillManager:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def sync(self) -> None:
            calls.append("sync")

        def install(self, name: str, **kwargs):
            calls.append(f"install:{name}")
            return SimpleNamespace(version="1.2.3", path=f"/skills/{name}")

        def prepare_runtime(self, name: str, run_tests: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}")
            return SimpleNamespace(version="1.2.3", slot="B")

        def activate_for_space(self, name: str, *, version: str | None = None, slot: str | None = None, space: str = "default", webspace_id: str = "default"):
            calls.append(f"activate_for_space:{name}:{version}:{slot}:{space}:{webspace_id}")
            return slot or "B"

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager, "SkillManager", _FakeSkillManager)
    monkeypatch.setattr(operations_manager, "SqliteSkillRegistry", lambda sql: object())
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})
    async def _rebuild(webspace_id: str, *, action: str = "rebuild", scenario_id: str | None = None, source_of_truth: str = "workspace"):
        rebuilds.append((webspace_id, action, source_of_truth, scenario_id))
    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)

    ctx = _make_ctx()
    result = operations_manager.submit_install_operation(
        target_kind="skill",
        target_id="demo_skill",
        webspace_id="default",
        ctx=ctx,
    )

    assert result["target_id"] == "demo_skill"
    assert "sync" in calls
    assert "install:demo_skill" in calls
    assert "prepare_runtime:demo_skill:0" in calls
    assert "activate_for_space:demo_skill:1.2.3:B:default:default" in calls
    assert rebuilds == [("default", "skill_install_sync", "skill_runtime", None)]


def test_submit_scenario_install_operation_rebuilds_target_webspace(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    calls: list[str] = []
    rebuilds: list[tuple[str, str, str, str | None]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeScenarioManager:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def sync(self) -> None:
            calls.append("sync")

        def install(self, name: str, *, pin: str | None = None):
            calls.append(f"install:{name}:{pin}")
            return SimpleNamespace(version="0.1.0", path=f"/scenarios/{name}")

        def list_present(self):
            calls.append("list_present")
            return [
                SimpleNamespace(
                    id=SimpleNamespace(value="demo_scene"),
                    name="demo_scene",
                    version="0.2.0",
                    path="/scenarios/demo_scene",
                )
            ]

        def bootstrap_dependencies(self, name: str, *, webspace_id: str | None = None):
            calls.append(f"bootstrap_dependencies:{name}:{webspace_id}")
            return {
                "ok": True,
                "scenario_id": name,
                "webspace_id": webspace_id,
                "required": ["demo_skill"],
                "items": [
                    {
                        "name": "demo_skill",
                        "ok": True,
                        "installed": True,
                        "prepared": True,
                        "activated": True,
                        "version": "1.2.3",
                        "slot": "B",
                    }
                ],
                "succeeded": ["demo_skill"],
                "failed": [],
            }

        def sync_to_yjs(self, name: str, *, webspace_id: str | None = None, emit_event: bool = True):
            calls.append(f"sync_to_yjs:{name}:{webspace_id}:{int(bool(emit_event))}")
            return SimpleNamespace(version="0.1.0", path=f"/scenarios/{name}")

    async def _rebuild(webspace_id: str, *, action: str = "rebuild", scenario_id: str | None = None, source_of_truth: str = "workspace"):
        rebuilds.append((webspace_id, action, source_of_truth, scenario_id))

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager, "ScenarioManager", _FakeScenarioManager)
    monkeypatch.setattr(operations_manager, "SqliteScenarioRegistry", lambda sql: object())
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})
    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)

    ctx = _make_ctx()
    result = operations_manager.submit_install_operation(
        target_kind="scenario",
        target_id="demo_scene",
        webspace_id="default",
        ctx=ctx,
    )

    assert result["target_id"] == "demo_scene"
    assert "sync" in calls
    assert "install:demo_scene:None" in calls
    assert "bootstrap_dependencies:demo_scene:default" in calls
    assert "sync_to_yjs:demo_scene:default:0" in calls
    assert result["result"]["dependency_bootstrap"]["ok"] is True
    assert result["result"]["dependency_bootstrap"]["succeeded"] == ["demo_skill"]
    assert result["result"]["dependency_bootstrap"]["items"][0]["version"] == "1.2.3"
    assert result["result"]["dependency_bootstrap"]["items"][0]["installed"] is True
    assert result["result"]["dependency_bootstrap"]["items"][0]["prepared"] is True
    assert result["result"]["dependency_bootstrap"]["items"][0]["activated"] is True
    assert rebuilds == [("default", "scenario_install_sync", "scenario_projection", "demo_scene")]


def test_submit_scenario_update_operation_reports_dependency_bootstrap(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    calls: list[str] = []
    rebuilds: list[tuple[str, str, str, str | None]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeScenarioManager:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def sync(self) -> None:
            calls.append("sync")

        def list_present(self):
            calls.append("list_present")
            return [
                SimpleNamespace(
                    id=SimpleNamespace(value="demo_scene"),
                    name="demo_scene",
                    version="0.2.0",
                    path="/scenarios/demo_scene",
                )
            ]

        def bootstrap_dependencies(self, name: str, *, webspace_id: str | None = None):
            calls.append(f"bootstrap_dependencies:{name}:{webspace_id}")
            return {
                "ok": True,
                "scenario_id": name,
                "webspace_id": webspace_id,
                "required": ["demo_skill"],
                "items": [
                    {
                        "name": "demo_skill",
                        "ok": True,
                        "installed": True,
                        "prepared": True,
                        "activated": True,
                        "version": "1.2.4",
                        "slot": "A",
                    }
                ],
                "succeeded": ["demo_skill"],
                "failed": [],
            }

        def sync_to_yjs(self, name: str, *, webspace_id: str | None = None, emit_event: bool = True):
            calls.append(f"sync_to_yjs:{name}:{webspace_id}:{int(bool(emit_event))}")
            return SimpleNamespace(version="0.2.0", path=f"/scenarios/{name}")

    async def _rebuild(webspace_id: str, *, action: str = "rebuild", scenario_id: str | None = None, source_of_truth: str = "workspace"):
        rebuilds.append((webspace_id, action, source_of_truth, scenario_id))

    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager, "ScenarioManager", _FakeScenarioManager)
    monkeypatch.setattr(operations_manager, "SqliteScenarioRegistry", lambda sql: object())
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})
    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)

    ctx = _make_ctx()
    result = operations_manager.submit_update_operation(
        target_kind="scenario",
        target_id="demo_scene",
        webspace_id="default",
        ctx=ctx,
    )

    assert result["kind"] == "scenario.update"
    assert result["target_id"] == "demo_scene"
    assert "sync" in calls
    assert "list_present" in calls
    assert "bootstrap_dependencies:demo_scene:default" in calls
    assert "sync_to_yjs:demo_scene:default:0" in calls
    assert result["result"]["action"] == "update"
    assert result["result"]["version"] == "0.2.0"
    assert result["result"]["dependency_bootstrap"]["ok"] is True
    assert result["result"]["dependency_bootstrap"]["items"][0]["version"] == "1.2.4"
    assert result["result"]["dependency_bootstrap"]["items"][0]["installed"] is True
    assert result["result"]["dependency_bootstrap"]["items"][0]["prepared"] is True
    assert result["result"]["dependency_bootstrap"]["items"][0]["activated"] is True
    assert rebuilds == [("default", "scenario_update_sync", "scenario_projection", "demo_scene")]


def test_submit_scenario_update_operation_blocks_failed_dependencies_in_prod(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    calls: list[str] = []
    rebuilds: list[tuple[str, str, str, str | None]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeScenarioManager:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def sync(self) -> None:
            calls.append("sync")

        def list_present(self):
            calls.append("list_present")
            return [SimpleNamespace(id=SimpleNamespace(value="demo_scene"), version="0.2.0", path="/scenarios/demo_scene")]

        def bootstrap_dependencies(self, name: str, *, webspace_id: str | None = None):
            calls.append(f"bootstrap_dependencies:{name}:{webspace_id}")
            return {
                "ok": False,
                "scenario_id": name,
                "webspace_id": webspace_id,
                "required": ["bad_skill"],
                "items": [{"name": "bad_skill", "ok": False, "error": "prepare failed"}],
                "succeeded": [],
                "failed": ["bad_skill"],
                "error": "RuntimeError: prepare failed",
            }

        def sync_to_yjs(self, name: str, *, webspace_id: str | None = None, emit_event: bool = True):
            calls.append(f"sync_to_yjs:{name}:{webspace_id}:{int(bool(emit_event))}")

    async def _rebuild(webspace_id: str, *, action: str = "rebuild", scenario_id: str | None = None, source_of_truth: str = "workspace"):
        rebuilds.append((webspace_id, action, source_of_truth, scenario_id))

    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager, "ScenarioManager", _FakeScenarioManager)
    monkeypatch.setattr(operations_manager, "SqliteScenarioRegistry", lambda sql: object())
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})
    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)

    ctx = _make_ctx()
    result = operations_manager.submit_update_operation(
        target_kind="scenario",
        target_id="demo_scene",
        webspace_id="default",
        ctx=ctx,
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "ScenarioDependencyLifecycleError"
    assert result["error"]["dependency_bootstrap"]["failed"] == ["bad_skill"]
    assert "bootstrap_dependencies:demo_scene:default" in calls
    assert not any(call.startswith("sync_to_yjs:") for call in calls)
    assert rebuilds == []


def test_submit_install_operation_uses_isolated_subprocess_when_enabled(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    spawned: list[dict[str, object]] = []
    finalized: list[dict[str, str]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"installed", b"")

        async def wait(self):
            return self.returncode

    async def _fake_create_subprocess_exec(*argv, **kwargs):
        spawned.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        return _FakeProc()

    async def _finalize(**kwargs):
        finalized.append(dict(kwargs))
        return {"materialization_ready": True}

    monkeypatch.setenv("ADAOS_TESTING", "0")
    monkeypatch.setenv("ADAOS_OPERATIONS_INSTALL_SUBPROCESS", "1")
    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(operations_manager, "_finalize_subprocess_install", _finalize)
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})

    ctx = _make_ctx()
    result = operations_manager.submit_install_operation(
        target_kind="scenario",
        target_id="demo_scene",
        webspace_id="default",
        ctx=ctx,
    )

    assert result["target_id"] == "demo_scene"
    assert result["status"] == "succeeded"
    assert len(spawned) == 1
    assert spawned[0]["argv"][:4] == [sys.executable, "-m", "adaos", "scenario"]
    assert spawned[0]["argv"][4:] == ["install", "demo_scene"]
    assert spawned[0]["env"]["ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC"] == "1"
    assert finalized == [{"target_kind": "scenario", "target_id": "demo_scene", "webspace_id": "default"}]
    assert result["result"]["finalization"]["materialization_ready"] is True


def test_finalize_subprocess_scenario_install_requires_visible_desktop_projection(monkeypatch) -> None:
    invalidated: list[tuple[str, str]] = []
    rebuilds: list[dict[str, object]] = []

    monkeypatch.setattr(operations_manager, "invalidate_local_capacity_cache", lambda: None)
    monkeypatch.setattr(
        operations_manager.scenarios_loader,
        "invalidate_cache",
        lambda *, scenario_id, space: invalidated.append((scenario_id, space)),
    )
    monkeypatch.setattr(
        operations_manager.scenarios_loader,
        "read_manifest",
        lambda scenario_id, space="workspace": {"id": scenario_id, "type": "desktop"},
    )
    monkeypatch.setattr(operations_manager, "invalidate_webspace_materialization_cache", lambda *args, **kwargs: {})

    async def _rebuild(webspace_id: str, **kwargs):
        rebuilds.append({"webspace_id": webspace_id, **kwargs})
        return {"status": "ready", "materialization": {"ready": True}}

    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)
    monkeypatch.setattr(
        operations_manager,
        "get_webspace_rebuild_materialized_payload",
        lambda _webspace_id: {
            "schema": "adaos.webspace.materialized_payload.v1",
            "catalog": {"apps": [{"id": "scenario:media_center", "scenario_id": "media_center"}]},
            "installed": {"apps": ["scenario:media_center"]},
        },
    )

    result = asyncio.run(
        operations_manager._finalize_subprocess_install(
            target_kind="scenario",
            target_id="media_center",
            webspace_id="desktop",
        )
    )

    assert invalidated == [("media_center", "workspace"), ("media_center", "dev")]
    assert rebuilds == [
        {
            "webspace_id": "desktop",
            "action": "scenario_install_sync",
            "source_of_truth": "scenario_projection",
        }
    ]
    assert result["projection"]["catalog_present"] is True
    assert result["projection"]["installed_present"] is True
    assert result["projection"]["payload_schema"] == "adaos.webspace.materialized_payload.v1"


def test_finalize_subprocess_scenario_install_rejects_missing_projection(monkeypatch) -> None:
    monkeypatch.setattr(operations_manager, "invalidate_local_capacity_cache", lambda: None)
    monkeypatch.setattr(operations_manager.scenarios_loader, "invalidate_cache", lambda **_kwargs: None)
    monkeypatch.setattr(
        operations_manager.scenarios_loader,
        "read_manifest",
        lambda scenario_id, space="workspace": {"id": scenario_id, "type": "desktop"},
    )
    monkeypatch.setattr(operations_manager, "invalidate_webspace_materialization_cache", lambda *args, **kwargs: {})

    async def _rebuild(_webspace_id: str, **_kwargs):
        return {"status": "ready", "materialization": {"ready": True}}

    monkeypatch.setattr(operations_manager, "rebuild_webspace_from_sources", _rebuild)
    monkeypatch.setattr(
        operations_manager,
        "get_webspace_rebuild_materialized_payload",
        lambda _webspace_id: {"catalog": {"apps": []}, "installed": {"apps": []}},
    )

    with pytest.raises(RuntimeError, match="post-install desktop projection is incomplete"):
        asyncio.run(
            operations_manager._finalize_subprocess_install(
                target_kind="scenario",
                target_id="media_center",
                webspace_id="desktop",
            )
        )


def test_operation_manager_cancels_only_governed_subprocess_work(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    process_state = {"started": False, "terminated": False}
    process_started = asyncio.Event()

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _BlockingProc:
        returncode = None

        async def communicate(self):
            process_state["started"] = True
            process_started.set()
            await asyncio.Event().wait()

        def terminate(self):
            process_state["terminated"] = True
            self.returncode = -15

        def kill(self):
            process_state["terminated"] = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def _fake_create_subprocess_exec(*argv, **kwargs):
        return _BlockingProc()

    monkeypatch.setenv("ADAOS_TESTING", "0")
    monkeypatch.setenv("ADAOS_OPERATIONS_INSTALL_SUBPROCESS", "1")
    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})

    async def _exercise() -> None:
        ctx = _make_ctx()
        accepted = operations_manager.submit_install_operation(
            target_kind="scenario",
            target_id="cancel_scene",
            webspace_id="default",
            ctx=ctx,
        )
        manager = operations_manager.get_operation_manager(ctx)
        await asyncio.wait_for(process_started.wait(), timeout=1.0)
        assert process_state["started"] is True
        assert manager.operation(accepted["operation_id"])["can_cancel"] is True

        cancelling = manager.cancel_operation(accepted["operation_id"])
        assert cancelling["status"] == "cancelling"
        for _ in range(20):
            if manager.operation(accepted["operation_id"])["status"] == "cancelled":
                break
            await asyncio.sleep(0)

        cancelled = manager.operation(accepted["operation_id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["can_cancel"] is False
        assert cancelled["can_retry"] is True
        assert process_state["terminated"] is True

    asyncio.run(_exercise())


def test_retry_operation_is_idempotent_per_source_attempt(monkeypatch) -> None:
    docs: dict[str, _FakeYDoc] = {}
    spawned: list[list[str]] = []

    @contextmanager
    def _get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"installed", b"")

        async def wait(self):
            return self.returncode

    async def _fake_create_subprocess_exec(*argv, **kwargs):
        spawned.append(list(argv))
        return _FakeProc()

    async def _finalize(**_kwargs):
        return {"materialization_ready": True}

    monkeypatch.setenv("ADAOS_TESTING", "0")
    monkeypatch.setenv("ADAOS_OPERATIONS_INSTALL_SUBPROCESS", "1")
    monkeypatch.setattr(operations_manager, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(operations_manager, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(operations_manager, "WebToastService", _FakeToastService)
    monkeypatch.setattr(operations_manager.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(operations_manager, "_finalize_subprocess_install", _finalize)
    monkeypatch.setattr(operations_manager, "_MANAGERS", {})

    ctx = _make_ctx()
    manager = operations_manager.get_operation_manager(ctx)
    source = manager.create_operation(
        kind="scenario.install",
        target_kind="scenario",
        target_id="retry_scene",
        webspace_id="default",
    )
    manager.update_operation(
        source.operation_id,
        status="failed",
        error={"type": "NetworkError", "message": "temporary failure", "retryable": True},
        finished=True,
    )

    first = operations_manager.retry_operation(source.operation_id, ctx=ctx)
    second = operations_manager.retry_operation(source.operation_id, ctx=ctx)

    assert first["operation_id"] == second["operation_id"]
    assert first["retry_of"] == source.operation_id
    assert first["attempt"] == 2
    assert first["status"] == "succeeded"
    assert len(spawned) == 1
    assert manager.operation(source.operation_id)["can_retry"] is False
