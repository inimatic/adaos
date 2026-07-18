from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
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
    dev_skills = tmp_path / "dev" / "test-subnet" / "skills"
    dev_scenarios = tmp_path / "dev" / "test-subnet" / "scenarios"

    class _DeveloperService:
        def _create(self, kind: str, name: str, template: str | None):
            package_root = Path(__file__).resolve().parents[1] / "src" / "adaos"
            source = package_root / ("skills_templates" if kind == "skill" else "scenario_templates") / str(template)
            target = (dev_skills if kind == "skill" else dev_scenarios) / name
            shutil.copytree(source, target)
            return SimpleNamespace(path=target, name=name)

        def create_skill(self, name: str, template: str | None = None):
            return self._create("skill", name, template or "skill_default")

        def create_scenario(self, name: str, template: str | None = None):
            return self._create("scenario", name, template or "scenario_default")

    return BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=workspace,
        skills_root=workspace / "skills",
        scenarios_root=workspace / "scenarios",
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        developer_service=_DeveloperService(),
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


def test_builder_skill_default_rewrites_conversation_manifest_refs(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.create_draft(
        kind="skill",
        artifact_id="demo_companion",
        source_idea="A small companion skill.",
        webspace_id="builder-template-ws",
    )

    manifest = yaml.safe_load((Path(result["artifact_root"]) / "skill.yaml").read_text(encoding="utf-8"))
    conversation = manifest["conversation"]
    assert manifest["name"] == "demo_companion"
    assert conversation["dialog_channel"]["id"] == "demo_companion"
    assert conversation["dialog_channel"]["owner"] == "skill:demo_companion"
    assert conversation["dialog_channel"]["default_tool"] == "demo_companion.chat"
    assert conversation["history"]["owner"] == "skill:demo_companion"
    assert conversation["agents"][0]["id"] == "agent:demo_companion:assistant"
    assert conversation["agents"][0]["owner"] == "skill:demo_companion"
    assert manifest["data_routes"][0]["path"] == "node_conversation_store:memory.skill_user.demo_companion"


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


def test_preview_policy_marks_clean_low_risk_preview_auto_apply_eligible(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.create_draft(
        kind="scenario",
        artifact_id="policy_scene",
        source_idea="Show a simple local dashboard.",
    )

    preview = service.preview(
        draft_id=result["draft"]["draft_id"],
        approval_profile="low_risk_auto_apply",
    )["preview"]

    policy = preview["review_policy"]
    assert policy["profile"]["id"] == "low_risk_auto_apply"
    assert policy["mandatory_classes"] == []
    assert policy["auto_apply_eligible"] is True
    assert preview["summary"]["review_decision"] == "auto_apply_eligible"
    assert preview["summary"]["human_review_required"] is False


def test_preview_policy_respects_legacy_draft_review_override(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.create_draft(
        kind="scenario",
        artifact_id="legacy_review_scene",
        source_idea="Show a simple local dashboard.",
    )
    draft = result["draft"]
    draft["metadata"]["human_review_required"] = True
    draft_path = Path(result["draft_dir"]) / "builder.draft.json"
    artifact_draft_path = Path(result["artifact_root"]) / "builder.draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    artifact_draft_path.write_text(json.dumps(draft), encoding="utf-8")

    preview = service.preview(
        draft_id=draft["draft_id"],
        approval_profile="low_risk_auto_apply",
    )["preview"]

    blocks = {item["code"] for item in preview["review_policy"]["policy_blocks"]}
    assert "draft_metadata_requires_review" in blocks
    assert preview["review_policy"]["auto_apply_eligible"] is False
    assert preview["summary"]["review_decision"] == "human_review_required"


def test_preview_policy_blocks_external_io_for_auto_apply(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.create_draft(
        kind="skill",
        artifact_id="external_skill",
        source_idea="Fetch remote data for a dashboard.",
    )
    artifact_root = Path(result["artifact_root"])
    handler = artifact_root / "handlers" / "main.py"
    handler.write_text("import requests\n\ndef handle(payload=None):\n    return requests.get('https://example.com').status_code\n", encoding="utf-8")

    preview = service.preview(
        draft_id=result["draft"]["draft_id"],
        approval_profile="low-risk-auto-apply",
    )["preview"]

    classes = {item["class"] for item in preview["review_policy"]["mandatory_classes"]}
    blocks = {item["code"] for item in preview["review_policy"]["policy_blocks"]}
    assert "external_io" in classes
    assert "mandatory_review_class" in blocks
    assert preview["review_policy"]["auto_apply_eligible"] is False
    assert preview["summary"]["human_review_required"] is True


def test_preview_policy_wires_action_risk_to_approval_gate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.create_draft(
        kind="skill",
        artifact_id="network_action_skill",
        source_idea="Send a webhook when an event happens.",
    )
    skill_yaml = Path(result["artifact_root"]) / "skill.yaml"
    manifest = yaml.safe_load(skill_yaml.read_text(encoding="utf-8")) or {}
    manifest.setdefault("llm_hints", {})["primary_actions"] = [
        {
            "id": "send_webhook",
            "title": "Send webhook",
            "target": "https://example.com/hook",
            "side_effect_class": "network",
        }
    ]
    skill_yaml.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")

    preview = service.preview(
        draft_id=result["draft"]["draft_id"],
        approval_profile="low_risk_auto_apply",
    )["preview"]

    action_risk = preview["review_policy"]["evidence"]["action_risk"]
    classes = {item["class"] for item in preview["review_policy"]["mandatory_classes"]}
    assert action_risk["approval_required"] is True
    assert action_risk["max_risk_class"] == "network"
    assert "network" in classes
    assert preview["review_policy"]["auto_apply_eligible"] is False


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


def test_builder_draft_create_and_checkpoint_delegate_to_core_dev_service(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []
    dev_scenarios = tmp_path / "dev" / "test-subnet" / "scenarios"

    class _CoreDeveloperService:
        def create_scenario(self, name: str, template: str | None = None):
            calls.append(("create", name, template))
            source = Path(__file__).resolve().parents[1] / "src" / "adaos" / "scenario_templates" / str(template)
            target = dev_scenarios / name
            shutil.copytree(source, target)
            return SimpleNamespace(path=target, name=name)

        def push_scenario(self, name: str, *, message: str | None = None):
            calls.append(("push", name, message))
            return SimpleNamespace(
                name=name,
                stored_path=f"scenarios/{name}",
                sha256="sha256-demo",
                bytes_uploaded=123,
                version="0.1.1",
                updated_at="2026-07-18T00:00:00Z",
                commit="forge-commit",
                message=message,
            )

    service = BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=None,
        workspace_root=tmp_path / "workspace",
        skills_root=tmp_path / "workspace" / "skills",
        scenarios_root=tmp_path / "workspace" / "scenarios",
        dev_skills_root=None,
        dev_scenarios_root=None,
        developer_service=_CoreDeveloperService(),
    )

    created = service.create_draft(
        kind="scenario",
        artifact_id="core_created_scenario",
        source_idea="Create a scenario through Builder chat.",
        template_id="builder_scenario",
    )
    checkpoint = service.checkpoint_artifact(
        kind="scenario",
        artifact_id="core_created_scenario",
        message="LLM added the requested form",
    )

    assert Path(created["artifact_root"]) == dev_scenarios / "core_created_scenario"
    assert calls == [
        ("create", "core_created_scenario", "builder_scenario"),
        ("push", "core_created_scenario", "LLM added the requested form"),
    ]
    assert checkpoint["commit"] == "forge-commit"
    assert checkpoint["message"] == "LLM added the requested form"


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

        def push_skill(self, name: str, *, message: str | None = None):
            return SimpleNamespace(
                kind="skill",
                name=name,
                stored_path=f"skills/{name}.zip",
                sha256="abc123",
                bytes_uploaded=42,
                version="0.2.1",
                updated_at="2026-06-04T00:00:01Z",
                commit="forge123",
                message=message,
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


def test_builder_cli_lists_approval_profiles(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(builder_cli.BuilderWorkspaceService, "from_context", classmethod(lambda cls: service))

    result = CliRunner().invoke(builder_cli.app, ["approval-profiles", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {item["id"] for item in payload["profiles"]} >= {
        "manual_only",
        "low_risk_auto_draft",
        "low_risk_auto_apply",
        "restricted_maintenance_repair",
    }


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


def test_root_dev_scenario_manifest_update_keeps_yaml_and_json_versions_aligned(tmp_path: Path) -> None:
    target = tmp_path / "scenarios" / "builder_scene"
    target.mkdir(parents=True)
    (target / "scenario.yaml").write_text(
        "id: builder_scene\nname: builder_scene\nversion: 0.2.0\n",
        encoding="utf-8",
    )
    (target / "scenario.json").write_text(
        json.dumps({"id": "builder_scene", "name": "builder_scene", "version": "0.1.0", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    service = object.__new__(RootDeveloperService)

    metadata = service._update_manifest(
        "scenarios",
        target,
        "builder_scene",
        None,
        version_bump_index=1,
        set_prototype=False,
    )

    yaml_payload = yaml.safe_load((target / "scenario.yaml").read_text(encoding="utf-8"))
    json_payload = json.loads((target / "scenario.json").read_text(encoding="utf-8"))
    assert metadata["version"] == "0.2.0"
    assert yaml_payload["version"] == json_payload["version"] == metadata["version"]
    assert yaml_payload["updated_at"] == json_payload["updated_at"]
    assert json_payload["ui"] == {"application": {}}


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
            "webspace_id": "builder-api-ws",
        },
    )

    assert response.status_code == 200
    draft: dict[str, Any] = response.json()["draft"]
    assert draft["artifact"]["id"] == "api_scene"
    assert draft["links"]["conversation"]["conversation_id"] == "conv.skill.builder_skill.default"
    assert draft["metadata"]["context_packet"]["conversation_id"] == "conv.skill.builder_skill.default"

    profiles_response = client.get("/api/builder/approval-profiles")
    assert profiles_response.status_code == 200
    assert {item["id"] for item in profiles_response.json()["profiles"]} >= {"manual_only", "low_risk_auto_apply"}

    response = client.post(
        "/api/builder/preview",
        json={"draft_id": draft["draft_id"], "approval_profile": "low_risk_auto_apply", "webspace_id": "builder-api-ws"},
    )
    assert response.status_code == 200
    preview = response.json()["preview"]
    assert preview["draft_id"] == draft["draft_id"]
    assert preview["summary"]["changed_files"] >= 1
    assert preview["summary"]["approval_profile"] == "low_risk_auto_apply"
    assert preview["conversation"]["conversation_id"] == "conv.skill.builder_skill.default"
    assert preview["source_refs"]["conversation_id"] == "conv.skill.builder_skill.default"
