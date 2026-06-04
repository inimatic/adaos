from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.apps.cli.commands import builder as builder_cli
from adaos.apps.cli.commands import dev as dev_cli
from adaos.services.builder import BuilderWorkspaceService
from adaos.services.root.service import RootDeveloperService


def _service(tmp_path: Path) -> BuilderWorkspaceService:
    workspace = tmp_path / "workspace"
    return BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=workspace,
        skills_root=workspace / "skills",
        scenarios_root=workspace / "scenarios",
        dev_skills_root=tmp_path / "dev" / "test-subnet" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "test-subnet" / "scenarios",
    )


def _write_demo_skill(root: Path, name: str = "demo_skill") -> Path:
    skill_dir = root / "workspace" / "skills" / name
    (skill_dir / "handlers").mkdir(parents=True)
    (skill_dir / "interpreter").mkdir(parents=True)
    (skill_dir / "intents").mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                f"name: {name}",
                'version: "1.0.0"',
                "description: Old description",
                "tools: []",
                "exports: {}",
                "events: {}",
                "data_routes: []",
                "llm_hints:",
                "  aliases: []",
                "nlu_hints:",
                "  examples: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "webui.json").write_text(json.dumps({"catalog": {"apps": []}}), encoding="utf-8")
    (skill_dir / "interpreter" / "intents.yml").write_text("intents: []\n", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "from y_py import YDoc\n\nhistory_cache = []\n\ndef handle(payload=None):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_descriptor_fix_draft_materializes_manifest_webui_and_nlu_files(tmp_path: Path) -> None:
    _write_demo_skill(tmp_path)
    service = _service(tmp_path)

    result = service.create_draft(
        kind="descriptor_fix",
        target_kind="skill",
        artifact_id="demo_skill",
        source_idea="Open the demo dashboard from voice.",
        task_id="btask.demo",
        descriptor_changes={
            "description": "Demo dashboard voice entrypoint.",
            "llm_hints": {"aliases": ["demo dashboard"]},
            "nlu_hints": {"examples": ["open demo dashboard"]},
        },
    )

    draft = result["draft"]
    artifact_root = Path(result["artifact_root"])
    touched = {item["path"] for item in draft["materialization"]["touched"]}
    assert draft["task_id"] == "btask.demo"
    assert {"skill.yaml", "webui.json", "builder.nlu_hints.json", "interpreter/intents.yml"}.issubset(touched)
    assert "Demo dashboard voice entrypoint" in (artifact_root / "skill.yaml").read_text(encoding="utf-8")
    webui = json.loads((artifact_root / "webui.json").read_text(encoding="utf-8"))
    assert webui["nlu"]["nlu_hints"]["examples"] == ["open demo dashboard"]

    preview = service.preview(draft_id=draft["draft_id"])["preview"]
    changed = {item["path"] for item in preview["diff"]["files"]}
    assert "skill.yaml" in changed
    assert preview["blast_radius"]["risk"] == "medium"
    static_codes = {item["code"] for item in preview["static_checks"]["issues"]}
    assert {"static.unsafe_direct_yjs", "static.unbounded_memory"}.issubset(static_codes)
    route_codes = {item["code"] for item in preview["route_plan"]["issues"]}
    assert "route_plan.missing" in route_codes


def test_preview_reports_scenario_dependency_bootstrap(tmp_path: Path) -> None:
    good_skill = tmp_path / "workspace" / "skills" / "good_skill"
    good_skill.mkdir(parents=True)
    service = _service(tmp_path)
    result = service.create_draft(
        kind="scenario",
        artifact_id="demo_scene",
        source_idea="Run a scenario that uses a dependency.",
    )
    artifact_root = Path(result["artifact_root"])
    manifest = json.loads((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    manifest["depends"] = ["good_skill", "missing_skill"]
    (artifact_root / "scenario.json").write_text(json.dumps(manifest), encoding="utf-8")

    preview = service.preview(draft_id=result["draft"]["draft_id"])["preview"]

    bootstrap = preview["scenario_dependency_bootstrap"]
    assert preview["summary"]["schema_ok"] is True
    assert bootstrap["available"] is True
    assert bootstrap["status"] == "blocked"
    assert bootstrap["failed"] == ["missing_skill"]
    assert {item["name"]: item["ok"] for item in bootstrap["items"]} == {
        "good_skill": True,
        "missing_skill": False,
    }


def test_builder_artifacts_live_under_existing_devspace(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.create_draft(
        kind="scenario",
        artifact_id="devspace_scene",
        source_idea="Build a scenario draft in devspace.",
    )

    draft_dir = Path(result["draft_dir"]).resolve()
    artifact_root = Path(result["artifact_root"]).resolve()
    dev_scenarios_root = (tmp_path / "dev" / "test-subnet" / "scenarios").resolve()
    manifest = json.loads((artifact_root / "scenario.json").read_text(encoding="utf-8"))
    assert artifact_root.relative_to(dev_scenarios_root)
    assert manifest["name"] == "devspace_scene"
    assert draft_dir.relative_to((tmp_path / "state" / "builder" / "drafts").resolve())
    assert (artifact_root / "builder.draft.json").exists()
    assert (draft_dir / "builder.draft.json").exists()


def test_builder_cli_accepts_unquoted_multi_word_idea(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(builder_cli.BuilderWorkspaceService, "from_context", classmethod(lambda cls: service))

    result = CliRunner().invoke(
        builder_cli.app,
        ["draft", "demo_scene", "--kind", "scenario", "--idea", "Build", "demo", "scenario", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["draft"]["metadata"]["source_idea"] == "Build demo scenario"
    assert Path(payload["artifact_root"]).resolve().relative_to(
        (tmp_path / "dev" / "test-subnet" / "scenarios").resolve()
    )


def test_builder_cli_create_delegates_to_dev_scenario_workspace(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    class _Svc:
        def create_scenario(self, name: str, template: str | None = None):
            calls.append((name, template))
            return SimpleNamespace(
                kind="scenario",
                name=name,
                owner_id="owner-1",
                path=tmp_path / "dev" / "sn_test" / "scenarios" / name,
                version="0.1.0",
                updated_at="2026-06-04T00:00:00Z",
            )

    monkeypatch.setattr(builder_cli, "_service", lambda: _Svc())

    result = CliRunner().invoke(
        builder_cli.app,
        ["create", "builder_scene", "--kind", "scenario", "--template", "scenario_default", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["kind"] == "scenario"
    assert payload["name"] == "builder_scene"
    assert calls == [("builder_scene", "scenario_default")]


def test_builder_cli_list_and_push_use_existing_dev_service(tmp_path: Path, monkeypatch) -> None:
    class _Svc:
        def list_skills(self):
            return [
                SimpleNamespace(
                    name="builder_skill",
                    path=tmp_path / "dev" / "sn_test" / "skills" / "builder_skill",
                    version="0.2.0",
                    updated_at="2026-06-04T00:00:00Z",
                )
            ]

        def push_skill(self, name: str):
            return SimpleNamespace(
                kind="skill",
                name=name,
                stored_path=f"skills/{name}.zip",
                sha256="abc123",
                bytes_uploaded=42,
                version="0.2.1",
                updated_at="2026-06-04T00:00:01Z",
            )

    monkeypatch.setattr(builder_cli, "_service", lambda: _Svc())
    runner = CliRunner()

    result = runner.invoke(builder_cli.app, ["list", "--kind", "skill", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["name"] == "builder_skill"

    result = runner.invoke(builder_cli.app, ["push", "builder_skill", "--kind", "skill", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["stored_path"] == "skills/builder_skill.zip"
    assert payload["bytes_uploaded"] == 42


def test_builder_cli_validate_scenario_uses_dev_json_loader(tmp_path: Path, monkeypatch) -> None:
    scenario_dir = tmp_path / "dev" / "sn_test" / "scenarios" / "builder_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "builder_scene", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )

    class _Paths:
        def dev_scenarios_dir(self) -> Path:
            return tmp_path / "dev" / "sn_test" / "scenarios"

    monkeypatch.setattr(builder_cli, "get_ctx", lambda: SimpleNamespace(paths=_Paths()))

    result = CliRunner().invoke(
        builder_cli.app,
        ["validate", "builder_scene", "--kind", "scenario", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["scenario_id"] == "builder_scene"


def test_builder_cli_validate_scenario_prefers_dev_service_path(tmp_path: Path, monkeypatch) -> None:
    scenario_dir = tmp_path / "owner-dev" / "scenarios" / "builder_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "builder_scene", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )

    class _Svc:
        def list_scenarios(self):
            return [
                SimpleNamespace(
                    name="builder_scene",
                    path=scenario_dir,
                    version="0.1.0",
                    updated_at=None,
                )
            ]

    class _Paths:
        def dev_scenarios_dir(self) -> Path:
            return tmp_path / "wrong-dev" / "scenarios"

    monkeypatch.setattr(builder_cli, "_service", lambda: _Svc())
    monkeypatch.setattr(builder_cli, "get_ctx", lambda: SimpleNamespace(paths=_Paths()))

    result = CliRunner().invoke(
        builder_cli.app,
        ["validate", "builder_scene", "--kind", "scenario", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["scenario_id"] == "builder_scene"


def test_root_dev_scenario_manifest_update_sets_id_to_artifact_name(tmp_path: Path) -> None:
    target = tmp_path / "scenarios" / "builder_scene"
    target.mkdir(parents=True)
    (target / "scenario.json").write_text(
        json.dumps({"id": "template-id", "name": "Template", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )
    service = object.__new__(RootDeveloperService)

    service._update_manifest(
        "scenarios",
        target,
        "builder_scene",
        "default",
        version_bump_index=1,
        set_prototype=True,
    )

    payload = json.loads((target / "scenario.json").read_text(encoding="utf-8"))
    assert payload["id"] == "builder_scene"
    assert payload["name"] == "builder_scene"


def test_dev_scenario_loader_accepts_builder_json_manifest(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "dev" / "sn_test" / "scenarios" / "json_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "json_scene", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )

    model = dev_cli._load_dev_scenario_model(scenario_dir / "scenario.json")

    assert model.id == "json_scene"


def test_builder_api_exposes_draft_and_preview(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/builder/draft",
        json={
            "kind": "scenario",
            "artifact_id": "api_scene",
            "source_idea": "Build a small API preview scenario.",
        },
    )

    assert response.status_code == 200
    draft: dict[str, Any] = response.json()["draft"]
    assert draft["artifact"]["id"] == "api_scene"

    response = client.post("/api/builder/preview", json={"draft_id": draft["draft_id"]})
    assert response.status_code == 200
    preview = response.json()["preview"]
    assert preview["draft_id"] == draft["draft_id"]
    assert preview["summary"]["changed_files"] >= 1
