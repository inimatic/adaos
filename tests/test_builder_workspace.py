from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.apps.cli.commands import builder as builder_cli
from adaos.services.builder import BuilderWorkspaceService


def _service(tmp_path: Path) -> BuilderWorkspaceService:
    workspace = tmp_path / "workspace"
    return BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        builder_root=tmp_path / "dev" / "builder",
        repo_root=tmp_path,
        workspace_root=workspace,
        skills_root=workspace / "skills",
        scenarios_root=workspace / "scenarios",
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


def test_builder_drafts_live_under_devspace(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.create_draft(
        kind="scenario",
        artifact_id="devspace_scene",
        source_idea="Build a scenario draft in devspace.",
    )

    draft_dir = Path(result["draft_dir"]).resolve()
    dev_builder_root = (tmp_path / "dev" / "builder").resolve()
    assert draft_dir.relative_to(dev_builder_root)
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
    assert Path(payload["draft_dir"]).resolve().relative_to((tmp_path / "dev" / "builder").resolve())


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
