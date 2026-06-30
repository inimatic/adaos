from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
