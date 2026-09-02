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


def _write_dev_skill(service: BuilderWorkspaceService, name: str) -> Path:
    skill_dir = Path(service.dev_skills_root) / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "version": "0.1.0",
                "description": "Standalone DEV skill",
                "tools": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return skill_dir


def test_ensure_owning_dev_project_adopts_standalone_component_once(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_dev_skill(service, "subscription_status_skill")

    created = service.ensure_owning_dev_project(
        kind="skill",
        artifact_id="subscription_status_skill",
        actor="builder:test",
    )
    repeated = service.ensure_owning_dev_project(
        kind="skill",
        artifact_id="subscription_status_skill",
        actor="builder:test",
    )

    assert created["status"] == "created"
    assert created["project_id"] == "subscription_status"
    assert created["project_ref"] == "project:subscription_status"
    manifest = yaml.safe_load(Path(created["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["components"]["owned"] == [
        {
            "ref": "skill:subscription_status_skill",
            "role": "primary",
            "exposure": "application",
            "lifecycle": "bound",
            "relations": ["uses"],
        }
    ]
    assert manifest["publication"]["stage"] == "alpha"
    assert repeated["status"] == "source_available"
    assert repeated["created"] is False
    assert repeated["project_id"] == created["project_id"]
    assert len(list(Path(service.dev_skills_root).parent.joinpath("projects").iterdir())) == 1


def test_ensure_owning_dev_project_requires_workspace_owner_materialization(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _write_dev_skill(service, "subscription_status_skill")
    project_root = tmp_path / "workspace" / "projects" / "subscriptions"
    project_root.mkdir(parents=True)
    (project_root / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "adaos.project.v1",
                "id": "subscriptions",
                "version": "1.0.0",
                "components": {
                    "owned": [
                        {"ref": "skill:subscription_status_skill", "role": "primary"}
                    ],
                    "dependencies": [],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = service.ensure_owning_dev_project(
        kind="skill",
        artifact_id="subscription_status_skill",
    )

    assert result["status"] == "needs_materialization"
    assert result["project_id"] == "subscriptions"
    assert not Path(service.dev_skills_root).parent.joinpath("projects").exists()


def test_materialize_dev_source_copies_workspace_project_owned_slice(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "scenarios" / "demo_scene"
    skill_dir = workspace / "skills" / "demo_skill"
    project_dir = workspace / "projects" / "demo_project"
    scenario_dir.mkdir(parents=True)
    skill_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo_scene",
                "version": "1.0.0",
                "depends": ["demo_skill"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (scenario_dir / "scenario.json").write_text(json.dumps({"schema": "adaos.webui.v1"}), encoding="utf-8")
    (skill_dir / "skill.yaml").write_text(
        yaml.safe_dump({"name": "demo_skill", "version": "1.0.0", "tools": []}, sort_keys=False),
        encoding="utf-8",
    )
    (skill_dir / "handlers").mkdir()
    (skill_dir / "handlers" / "main.py").write_text("def handle():\n    return {'ok': True}\n", encoding="utf-8")
    (project_dir / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "adaos.project.v1",
                "id": "demo_project",
                "version": "1.0.0",
                "components": {
                    "owned": [
                        {"ref": "scenario:demo_scene", "role": "primary"},
                        {"ref": "skill:demo_skill", "role": "implementation"},
                    ],
                    "dependencies": [],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    service.dev_scenarios_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(scenario_dir, service.dev_scenarios_root / "demo_scene")

    before = service.development_source_status(kind="scenario", artifact_id="demo_scene")
    result = service.materialize_dev_source(kind="scenario", artifact_id="demo_scene")
    after = service.development_source_status(kind="scenario", artifact_id="demo_scene")

    assert before["status"] == "needs_materialization"
    assert before["project_id"] == "demo_project"
    assert before["reason"] == "owning_project_not_in_devspace"
    assert before["orphaned_dev_source_path"] == str(service.dev_scenarios_root / "demo_scene")
    assert result["ok"] is True
    assert result["project_id"] == "demo_project"
    assert {f"{item['kind']}:{item['name']}" for item in result["components"]} == {
        "project:demo_project",
        "scenario:demo_scene",
        "skill:demo_skill",
    }
    assert after["status"] == "source_available"
    assert (service.dev_scenarios_root / "demo_scene" / "scenario.yaml").is_file()
    assert (service.dev_skills_root / "demo_skill" / "handlers" / "main.py").is_file()
    assert (service.dev_scenarios_root.parent / "projects" / "demo_project" / "project.yaml").is_file()


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


def test_route_plan_rejects_uncausal_tool_backed_browser_read(tmp_path: Path) -> None:
    skill_dir = _write_demo_skill(tmp_path)
    manifest_path = skill_dir / "skill.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["tools"] = [
        {
            "name": "get_status",
            "entry": "handlers.main:get_status",
            "input_schema": {"type": "object"},
        }
    ]
    manifest["data_routes"] = [
        {
            "surface": "widget:demo.status",
            "route": "tool/details",
            "first_paint": "stable skeleton",
            "recovery": "explicit retry",
            "budget": {"max_payload_bytes": 4096, "snapshot_policy": "on_subscribe"},
            "guard_visibility": "unavailable status",
        }
    ]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    report = _service(tmp_path)._route_plan_report("skill", skill_dir)

    codes = {item["code"] for item in report["issues"]}
    assert report["ok"] is False
    assert {
        "route_plan.tool_missing",
        "route_plan.read_policy_missing",
        "route_plan.tool_snapshot_policy",
    }.issubset(codes)


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
    manifest = yaml.safe_load((artifact_root / "scenario.yaml").read_text(encoding="utf-8"))
    manifest["depends"] = ["good_skill", "missing_skill"]
    (artifact_root / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

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


def test_preview_policy_blocks_network_io_for_auto_apply(tmp_path: Path) -> None:
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
    assert "network" in classes
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
    manifest = yaml.safe_load((artifact_root / "scenario.yaml").read_text(encoding="utf-8"))
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


def test_builder_cli_validate_scenario_uses_dev_yaml_loader(tmp_path: Path, monkeypatch) -> None:
    scenario_dir = tmp_path / "dev" / "sn_test" / "scenarios" / "builder_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        yaml.safe_dump({"id": "builder_scene", "version": "0.1.0", "steps": []}, sort_keys=False),
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
    (scenario_dir / "scenario.yaml").write_text(
        yaml.safe_dump({"id": "builder_scene", "version": "0.1.0", "steps": []}, sort_keys=False),
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
    (target / "scenario.yaml").write_text(
        yaml.safe_dump({"id": "template-id", "name": "Template", "version": "0.1.0", "steps": []}, sort_keys=False),
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

    payload = yaml.safe_load((target / "scenario.yaml").read_text(encoding="utf-8"))
    assert payload["id"] == "builder_scene"
    assert payload["name"] == "builder_scene"


def test_root_dev_scenario_manifest_update_only_updates_scenario_yaml(tmp_path: Path) -> None:
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
    assert metadata["version"] == "0.3.0"
    assert yaml_payload["version"] == metadata["version"]
    assert yaml_payload["updated_at"] == metadata["updated_at"]
    json_payload = json.loads((target / "scenario.json").read_text(encoding="utf-8"))
    assert json_payload == {"id": "builder_scene", "name": "builder_scene", "version": "0.1.0", "ui": {"application": {}}}


def test_root_dev_list_artifacts_skips_dot_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "dev"
    skill_dir = workspace / "skills" / "builder_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.2.0\nupdated_at: '2026-06-04T00:00:00Z'\n",
        encoding="utf-8",
    )
    (workspace / "skills" / ".runtime" / "builder_skill").mkdir(parents=True)

    service = object.__new__(RootDeveloperService)
    service._prepare_workspace = lambda _cfg, owner: workspace

    items = service._list_artifacts(SimpleNamespace(owner_id="owner-1"), "skills")

    assert [item.name for item in items] == ["builder_skill"]


def test_root_dev_skill_manifest_update_keeps_conversational_version_atomic(tmp_path: Path) -> None:
    target = tmp_path / "skills" / "builder_skill"
    conversational = target / "conversational"
    conversational.mkdir(parents=True)
    (target / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.3.40\nconversational:\n  manifest: conversational/manifest.yaml\n",
        encoding="utf-8",
    )
    (conversational / "manifest.yaml").write_text(
        "schema: adaos.conversational.package_manifest.v1\npackage_id: builder_skill\npackage_kind: skill\nversion: 0.3.40\n",
        encoding="utf-8",
    )
    service = object.__new__(RootDeveloperService)

    metadata = service._update_manifest(
        "skills",
        target,
        "builder_skill",
        None,
        version_bump_index=2,
        set_prototype=False,
    )

    skill_manifest = yaml.safe_load((target / "skill.yaml").read_text(encoding="utf-8"))
    package_manifest = yaml.safe_load(
        (conversational / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert metadata["version"] == "0.3.41"
    assert skill_manifest["version"] == package_manifest["version"] == "0.3.41"


def test_root_dev_scenario_create_rewrites_the_complete_default_template(tmp_path: Path) -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "scenario_templates"
        / "scenario_default"
    )
    service = object.__new__(RootDeveloperService)
    service._load_config = lambda: object()
    service._owner_workspace = lambda _cfg: ("owner-1", tmp_path / "dev")
    service._resolve_template = lambda _kind, _template: (template, "default")

    result = service._create_artifact("scenarios", "recipe_book", template=None)

    root = Path(result.path)
    manifest = yaml.safe_load((root / "scenario.yaml").read_text(encoding="utf-8"))
    content = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    draft = json.loads((root / "builder.draft.json").read_text(encoding="utf-8"))
    page = webui["ui"]["application"]["desktop"]["pageSchema"]

    assert manifest["id"] == manifest["name"] == "recipe_book"
    assert manifest["version"] == "0.1.0"
    assert manifest["ui"] == {"manifest": "webui.json"}
    assert content["id"] == content["name"] == "recipe_book"
    assert content["version"] == manifest["version"]
    assert content["updated_at"] == manifest["updated_at"]
    assert content["ui"] == webui["ui"]
    assert page["id"] == "recipe_book"
    assert page["title"] == "Recipe Book"
    assert [item["id"] for item in page["widgets"]] == ["builder-empty-canvas"]
    assert draft["artifact"]["id"] == "recipe_book"


def test_dev_scenario_loader_rejects_builder_json_manifest(tmp_path: Path) -> None:
    scenario_dir = tmp_path / "dev" / "sn_test" / "scenarios" / "json_scene"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.json").write_text(
        json.dumps({"id": "json_scene", "version": "0.1.0", "steps": []}),
        encoding="utf-8",
    )

    try:
        dev_cli._load_dev_scenario_model(scenario_dir / "scenario.json")
    except FileNotFoundError as exc:
        assert "scenario.yaml" in str(exc)
    else:
        raise AssertionError("scenario.json must not be accepted as a declaration")


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


def test_builder_api_exposes_trial_decision() -> None:
    calls: list[dict[str, Any]] = []

    class _Automation:
        def decide_aprobation(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "decision": kwargs["decision"], "candidate_id": "candidate.api"}

    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_automation_service] = lambda: _Automation()
    client = TestClient(app)

    response = client.post(
        "/api/builder/trial/decision",
        json={
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "decision": "revise",
            "actor": "user:owner",
            "reason": "The label is still unclear",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["candidate_id"] == "candidate.api"
    assert calls == [
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "decision": "revise",
            "actor": "user:owner",
            "reason": "The label is still unclear",
        }
    ]


def test_builder_api_recovers_validated_result_in_node_context() -> None:
    calls: list[dict[str, Any]] = []

    class _Automation:
        def recover_validated_result(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "recovered": True, "worker": {"model_started": False}}

    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_automation_service] = lambda: _Automation()
    client = TestClient(app)

    response = client.post(
        "/api/builder/automation/recover-validated",
        json={"object_type": "skill", "object_id": "demo_metrics_skill"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["worker"]["model_started"] is False
    assert calls == [
        {"object_type": "skill", "object_id": "demo_metrics_skill"}
    ]


def test_builder_api_reconciles_checkpoint_without_codex() -> None:
    calls: list[dict[str, Any]] = []

    class _Automation:
        def reconcile_checkpoint(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "reconciled": True, "model_started": False}

    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_automation_service] = lambda: _Automation()
    client = TestClient(app)

    response = client.post(
        "/api/builder/automation/reconcile-checkpoint",
        json={"object_type": "scenario", "object_id": "taiga_ui_demo_scenario"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["model_started"] is False
    assert calls == [
        {"object_type": "scenario", "object_id": "taiga_ui_demo_scenario"}
    ]
