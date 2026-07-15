from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace


def _load_prompt_engineer_module(monkeypatch):
    monkeypatch.setenv("ADAOS_VALIDATE", "1")
    if "y_py" not in sys.modules:
        sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
    if "ypy_websocket" not in sys.modules:
        ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
        sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
        sys.modules["ypy_websocket.ystore"] = ystore_mod
    module_path = Path(__file__).resolve().parents[1] / ".adaos" / "workspace" / "skills" / "prompt_engineer_skill" / "handlers" / "main.py"
    spec = importlib.util.spec_from_file_location("prompt_engineer_skill_phase2_main", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_create_dev_project_accepts_project_alias_payload(monkeypatch) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    captured: list[tuple[str, str | None]] = []

    class _Svc:
        def create_scenario(self, name: str, template: str | None = None):
            captured.append((name, template))
            return SimpleNamespace(name=name, path=Path(f"/tmp/{name}"))

    monkeypatch.setattr(module, "RootDeveloperService", lambda: _Svc())
    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda *args, **kwargs: None)

    result = module.prompt_create_dev_project(
        {
            "project_type": "scenario",
            "project_id": "demo_scenario",
            "template": "default",
        }
    )

    assert captured == [("demo_scenario", "default")]
    assert result["ok"] is True
    assert result["object_type"] == "scenario"
    assert result["object_id"] == "demo_scenario"
    assert result["project_id"] == "demo_scenario"


def test_prompt_create_dev_project_creates_skill(monkeypatch) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    captured: list[tuple[str, str | None]] = []

    class _Svc:
        def create_skill(self, name: str, template: str | None = None):
            captured.append((name, template))
            return SimpleNamespace(name=name, path=Path(f"/tmp/{name}"))

    monkeypatch.setattr(module, "RootDeveloperService", lambda: _Svc())
    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda *args, **kwargs: None)

    result = module.prompt_create_dev_project(
        {
            "object_type": "skill",
            "name": "demo_skill",
        }
    )

    assert captured == [("demo_skill", None)]
    assert result["ok"] is True
    assert result["object_type"] == "skill"
    assert result["object_id"] == "demo_skill"


def test_prompt_create_dev_project_normalizes_selector_like_template_payload(monkeypatch) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    captured: list[tuple[str, str | None]] = []

    class _Svc:
        def create_scenario(self, name: str, template: str | None = None):
            captured.append((name, template))
            return SimpleNamespace(name=name, path=Path(f"/tmp/{name}"))

    monkeypatch.setattr(module, "RootDeveloperService", lambda: _Svc())
    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda *args, **kwargs: None)

    result = module.prompt_create_dev_project(
        {
            "object_type": {"id": "scenario"},
            "name": {"value": "selector_scenario"},
            "template": {"id": "scenario_default", "label": "Default"},
        }
    )

    assert captured == [("selector_scenario", "scenario_default")]
    assert result["ok"] is True
    assert result["object_type"] == "scenario"
    assert result["object_id"] == "selector_scenario"


def test_prompt_create_dev_project_returns_structured_error(monkeypatch) -> None:
    module = _load_prompt_engineer_module(monkeypatch)

    class _Svc:
        def create_scenario(self, name: str, template: str | None = None):  # noqa: ARG002
            raise module.RootServiceError(f"Target already exists: /tmp/{name}")

    monkeypatch.setattr(module, "RootDeveloperService", lambda: _Svc())

    result = module.prompt_create_dev_project(
        {
            "object_type": "scenario",
            "name": "existing_scenario",
        }
    )

    assert result["ok"] is False
    assert "Target already exists" in result["error"]


def test_prompt_payload_helpers_accept_kwargs(monkeypatch, tmp_path: Path) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    skills_root.mkdir()
    scenarios_root.mkdir()
    for name in ("s1", "s2"):
        scen = scenarios_root / name
        scen.mkdir()
        (scen / "scenario.yaml").write_text(f"id: {name}\nversion: 0.1.0\n", encoding="utf-8")

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths()))
    monkeypatch.setattr(module, "_load_state", lambda object_type, object_id: {"workflow_state": "tz"})

    items = module.prompt_list_dev_objects(limit=1)

    assert len(items) == 1
    assert items[0]["object_type"] == "scenario"


def test_prompt_llm_profile_options_and_selection(monkeypatch, tmp_path: Path) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    scenario_root = scenarios_root / "demo_scenario"
    scenario_root.mkdir(parents=True)
    skills_root.mkdir()

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths(), bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda *args, **kwargs: None)

    import adaos.sdk.llm.llm_client as llm_client

    monkeypatch.setattr(
        llm_client,
        "list_llm_models",
        lambda **_kwargs: {
            "object": "list",
            "data": [
                {"id": "gpt-5", "provider": "openai", "label": "GPT-5", "default": True},
                {"id": "gpt-4.1", "provider": "openai", "label": "GPT-4.1"},
            ],
            "model_profiles": [
                {"id": "gpt-5", "provider": "openai", "label": "GPT-5", "scope": "development", "default": True},
                {"id": "gpt-4.1", "provider": "openai", "label": "GPT-4.1", "scope": "development"},
            ],
        },
    )

    options = module.prompt_llm_model_options({"object_type": "scenario", "object_id": "demo_scenario"})

    assert options["ok"] is True
    assert options["value"] == "gpt-5"
    assert [item["id"] for item in options["options"]] == ["gpt-5", "gpt-4.1"]

    result = module.prompt_set_llm_profile(
        {"object_type": "scenario", "object_id": "demo_scenario", "model": "gpt-4.1"}
    )

    assert result["ok"] is True
    assert result["builder_llm_model"] == "gpt-4.1"
    state = json.loads((scenario_root / "prompt_state.json").read_text(encoding="utf-8"))
    assert state["builder_llm_model"] == "gpt-4.1"
    assert state["llm_profile"]["provider"] == "openai"

    selected = module.prompt_llm_model_options({"object_type": "scenario", "object_id": "demo_scenario"})
    assert selected["value"] == "gpt-4.1"
    assert [item["selected"] for item in selected["options"]] == [False, True]


def test_prompt_llm_profile_options_use_development_fallback_when_root_returns_raw_models(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    scenarios_root.mkdir(parents=True)
    skills_root.mkdir()

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths(), bus=object()))

    import adaos.sdk.llm.llm_client as llm_client

    monkeypatch.setattr(
        llm_client,
        "list_llm_models",
        lambda **_kwargs: {
            "object": "list",
            "data": [
                {"id": "text-embedding-ada-002", "object": "model"},
                {"id": "whisper-1", "object": "model"},
            ],
        },
    )

    options = module.prompt_llm_model_options({"object_type": "scenario", "object_id": "demo_scenario"})

    assert options["ok"] is True
    assert options["source"] == "hub_fallback"
    assert options["value"] == "gpt-5"
    assert [item["id"] for item in options["options"]] == ["gpt-5", "gpt-4.1", "gpt-4o-mini"]
    assert all(item["scope"] == "development" for item in options["options"])


def test_prompt_lists_json_only_dev_scenario(monkeypatch, tmp_path: Path) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    skills_root.mkdir()
    scenario_root = scenarios_root / "demo_scenario"
    scenario_root.mkdir(parents=True)
    (scenario_root / "scenario.json").write_text(
        '{"id":"demo_scenario","name":"Demo JSON","version":"0.2.0","depends":["demo_skill"]}',
        encoding="utf-8",
    )

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths()))

    objects = module.prompt_list_dev_objects()
    assert objects[0]["object_id"] == "demo_scenario"
    assert objects[0]["title"] == "Demo JSON"
    project_objects = module.prompt_list_project_objects({"project_type": "scenario", "project_id": "demo_scenario"})
    assert project_objects[0]["object_id"] == "demo_scenario"
    files = module.prompt_list_project_files({"object_type": "scenario", "object_id": "demo_scenario"})
    assert [item["path"] for item in files] == ["prompt_state.json", "scenario.json", "tz/base_tz.md"]


def test_prompt_file_tree_groups_ui_revisions(monkeypatch, tmp_path: Path) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    scenario_root = scenarios_root / "demo_scenario"
    scenario_root.mkdir(parents=True)
    skills_root.mkdir()
    (scenario_root / "scenario.json").write_text('{"id":"demo_scenario"}', encoding="utf-8")
    revisions = scenario_root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text('{"revision":"001"}', encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths()))

    tree = module.prompt_list_project_file_tree({"object_type": "scenario", "object_id": "demo_scenario"})

    root = tree["root"]
    ui_revisions = next(item for item in root["children"] if item["title"] == "ui_revisions")
    assert [item["title"] for item in ui_revisions["children"]] == ["001.json", "current.txt"]
    assert ui_revisions["children"][0]["editable"] is False


def test_prompt_save_project_file_updates_base_tz_state(monkeypatch, tmp_path: Path) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    skills_root = tmp_path / "skills"
    scenarios_root = tmp_path / "scenarios"
    scenario_root = scenarios_root / "demo_scenario"
    scenario_root.mkdir(parents=True)
    skills_root.mkdir()
    emitted: list[tuple[str, dict, str]] = []

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return skills_root

        def dev_scenarios_dir(self) -> Path:
            return scenarios_root

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(paths=_Paths(), bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda bus, topic, payload, source: emitted.append((topic, payload, source)))

    result = module.prompt_save_project_file(
        {
            "object_type": "scenario",
            "object_id": "demo_scenario",
            "path": "tz/base_tz.md",
            "text": "current prototype specification",
        }
    )

    assert result["ok"] is True
    assert (scenario_root / "tz" / "base_tz.md").read_text(encoding="utf-8") == "current prototype specification"
    state = json.loads((scenario_root / "prompt_state.json").read_text(encoding="utf-8"))
    assert state["base_tz"] == "current prototype specification"
    assert emitted[-1][0] == "prompt.project.changed"


def test_prompt_select_project_emits_builder_preview(monkeypatch) -> None:
    module = _load_prompt_engineer_module(monkeypatch)
    emitted: list[tuple[str, dict, str]] = []
    bindings: list[dict[str, object]] = []

    class _Workbench:
        def set_active_draft(self, **kwargs):
            bindings.append(dict(kwargs))
            return {"ok": True}

        def publish_projection_sync(self, *_args, **_kwargs):
            return {"ok": True}

    builder_pkg = types.ModuleType("adaos.services.builder")
    workbench_module = types.ModuleType("adaos.services.builder.workbench")
    workbench_module.BuilderWorkbenchService = lambda: _Workbench()
    monkeypatch.setitem(sys.modules, "adaos.services.builder", builder_pkg)
    monkeypatch.setitem(sys.modules, "adaos.services.builder.workbench", workbench_module)

    monkeypatch.setattr(module, "_require_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(module, "bus_emit", lambda bus, topic, payload, source: emitted.append((topic, payload, source)))

    result = module.prompt_select_project(
        {
            "object_type": "scenario",
            "object_id": "demo_scenario",
            "_meta": {"webspace_id": "desktop"},
        }
    )

    assert result["ok"] is True
    assert result["object_type"] == "scenario"
    assert result["object_id"] == "demo_scenario"
    assert result["builder_topic_id"] == "prompt-project:scenario:demo_scenario"
    assert emitted[0][0] == "prompt.project.changed"
    assert emitted[1][0] == "builder.preview.selected"
    assert emitted[1][1]["scenario_id"] == "demo_scenario"
    assert emitted[1][1]["source_webspace_id"] == "desktop"
    assert bindings == [
        {
            "source_webspace_id": "desktop",
            "active_draft_id": None,
            "runtime_scenario_id": "demo_scenario",
            "persist_projection": True,
        }
    ]
