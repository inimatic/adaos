from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.scenario import workflow_runtime as workflow_runtime_module
from adaos.services.scenario.workflow_runtime import ScenarioWorkflowRuntime


class _Paths:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._skills = root / "skills"
        self._scenarios = root / "scenarios"
        self._state = root / "state"
        self._skills.mkdir(parents=True)
        self._scenarios.mkdir(parents=True)
        self._state.mkdir(parents=True)

    def dev_skills_dir(self) -> Path:
        return self._skills

    def dev_scenarios_dir(self) -> Path:
        return self._scenarios

    def state_dir(self) -> Path:
        return self._state


def _runtime(tmp_path: Path) -> tuple[ScenarioWorkflowRuntime, _Paths]:
    paths = _Paths(tmp_path)
    return ScenarioWorkflowRuntime(SimpleNamespace(paths=paths)), paths


def test_prompt_tz_state_projection_reads_prompt_state(tmp_path: Path) -> None:
    runtime, paths = _runtime(tmp_path)
    root = paths.dev_scenarios_dir() / "demo_scenario"
    root.mkdir()
    (root / "prompt_state.json").write_text(
        json.dumps(
            {
                "base_tz": "Base from state",
                "tz_addenda": [{"id": "a1", "text": "Addendum", "created_at": "2026-01-01T00:00:00+00:00"}],
            }
        ),
        encoding="utf-8",
    )

    state = runtime._build_tz_state("scenario", "demo_scenario")

    assert state["object_type"] == "scenario"
    assert state["object_id"] == "demo_scenario"
    assert state["base_tz"] == "Base from state"
    assert state["tz_addenda"][0]["id"] == "a1"


def test_prompt_tz_state_projection_falls_back_to_tz_files(tmp_path: Path) -> None:
    runtime, paths = _runtime(tmp_path)
    root = paths.dev_scenarios_dir() / "demo_scenario"
    addenda = root / "tz" / "addenda"
    addenda.mkdir(parents=True)
    (root / "tz" / "base_tz.md").write_text("Base from file", encoding="utf-8")
    (addenda / "0001.md").write_text("Addendum from file", encoding="utf-8")

    state = runtime._build_tz_state("scenario", "demo_scenario")

    assert state["base_tz"] == "Base from file"
    assert state["tz_addenda"][0]["id"] == "0001"
    assert state["tz_addenda"][0]["text"] == "Addendum from file"


def test_prompt_project_snapshots_include_files_and_tz_state(tmp_path: Path) -> None:
    runtime, paths = _runtime(tmp_path)
    root = paths.dev_scenarios_dir() / "demo_scenario"
    (root / "tz").mkdir(parents=True)
    (root / "scenario.json").write_text("{}", encoding="utf-8")
    (root / "tz" / "base_tz.md").write_text("Base", encoding="utf-8")
    section: dict[str, object] = {}

    runtime._sync_prompt_project_snapshots(section, "scenario", "demo_scenario")

    assert section["tz_state"]["base_tz"] == "Base"  # type: ignore[index]
    assert [item["path"] for item in section["files"]["list"]] == ["scenario.json", "tz/base_tz.md"]  # type: ignore[index]


def test_prompt_workflow_exposes_virtual_automation_state(tmp_path: Path) -> None:
    runtime, _paths = _runtime(tmp_path)

    states = runtime._states_with_automation("prompt_engineer_scenario", {"tz": {"label": "Stage: TZ"}})

    assert states["automation"]["label"] == "Stage: Automation"
    assert states["automation"]["actions"] == []


@pytest.mark.anyio
async def test_successful_execute_hands_brief_to_local_automation(monkeypatch, tmp_path: Path) -> None:
    runtime, _paths = _runtime(tmp_path)
    calls: list[dict[str, object]] = []

    class _Automation:
        @classmethod
        def from_context(cls):
            return cls()

        def start_from_execute(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "session": {"status": "queued"}}

    async def fake_set_state(*args, **kwargs):
        calls.append({"set_state": args[3], **kwargs})

    async def fake_set_status(*args, **kwargs):
        calls.append({"llm_status": args[2], "message": args[3], **kwargs})

    import adaos.services.builder.automation as automation_module

    monkeypatch.setattr(automation_module, "BuilderAutomationService", _Automation)
    monkeypatch.setattr(ScenarioWorkflowRuntime, "set_state", fake_set_state)
    monkeypatch.setattr(ScenarioWorkflowRuntime, "_set_llm_status", fake_set_status)

    await runtime._start_local_automation_from_tz_execute(
        "prompt-dev",
        "prompt_engineer_scenario",
        object_type="scenario",
        object_id="recipes",
        result={"ok": True, "output_text": "Approved implementation brief", "output_path": "tz/ts_draft.md"},
    )

    assert calls[0]["implementation_brief"] == "Approved implementation brief"
    assert calls[1]["set_state"] == "automation"
    assert calls[2]["llm_status"] == "automation"


@pytest.mark.anyio
async def test_refresh_prompt_project_snapshots_updates_selected_topic(monkeypatch, tmp_path: Path) -> None:
    runtime, paths = _runtime(tmp_path)
    root = paths.dev_scenarios_dir() / "prototype_app_4d5758e5"
    (root / "tz").mkdir(parents=True)
    (root / "scenario.json").write_text("{}", encoding="utf-8")
    (root / "tz" / "base_tz.md").write_text("Prototype TZ", encoding="utf-8")

    data = {
        "prompt": {
            "workflow": {
                "state": "tz",
                "object_type": "scenario",
                "object_id": "shopping_list_222d3f0c",
                "topic_id": "prompt-project:scenario:shopping_list_222d3f0c",
                "conversation_id": "conv.skill.builder_skill.default.desktop",
            }
        }
    }

    class _Txn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _DataMap:
        def get(self, key):
            return data.get(key)

        def set(self, txn, key, value):
            data[key] = value

    class _Doc:
        def get_map(self, name):
            assert name == "data"
            return _DataMap()

        def begin_transaction(self):
            return _Txn()

    class _DocContext:
        async def __aenter__(self):
            return _Doc()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(workflow_runtime_module, "async_get_ydoc", lambda webspace_id: _DocContext())

    await runtime.refresh_prompt_project_snapshots(
        "desktop",
        object_type="scenario",
        object_id="prototype_app_4d5758e5",
    )

    workflow = data["prompt"]["workflow"]
    assert workflow["object_id"] == "prototype_app_4d5758e5"
    assert workflow["topic_id"] == "prompt-project:scenario:prototype_app_4d5758e5"
    selection = json.loads((paths.state_dir() / "prompt_ide" / "selection" / "desktop.json").read_text(encoding="utf-8"))
    assert selection["object_id"] == "prototype_app_4d5758e5"
    assert selection["topic_id"] == "prompt-project:scenario:prototype_app_4d5758e5"


@pytest.mark.anyio
async def test_builder_prompt_project_change_skips_superseded_target(monkeypatch) -> None:
    refreshed: list[tuple[str, str, str]] = []

    class _Workbench:
        @classmethod
        def from_context(cls):
            return cls()

        def get_workspace_binding(self, webspace_id):
            assert webspace_id == "desktop"
            return {"runtime_scenario_id": "builder"}

    class _Runtime:
        def __init__(self, ctx):
            self.ctx = ctx

        async def refresh_prompt_project_snapshots(self, webspace_id, *, object_type, object_id):
            refreshed.append((webspace_id, object_type, object_id))

    import adaos.services.builder.workbench as workbench_module

    monkeypatch.setattr(workbench_module, "BuilderWorkbenchService", _Workbench)
    monkeypatch.setattr(workflow_runtime_module, "ScenarioWorkflowRuntime", _Runtime)
    monkeypatch.setattr(workflow_runtime_module, "get_ctx", lambda: object())

    await workflow_runtime_module._on_prompt_project_changed_refresh_workflow(
        {
            "source_webspace_id": "desktop",
            "webspace_id": "desktop",
            "object_type": "scenario",
            "object_id": "stale_prototype",
            "reason": "builder_project_created",
        }
    )

    assert refreshed == []
