from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    WorkspaceLock,
    WorkspaceSlot,
)
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    DependencyRequirement,
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
from adaos.services.artifact_pipeline import storage as artifact_storage
from adaos.services.builder.source_recovery import BuilderSourceRecoveryService
from adaos.services.builder.workspace import (
    BuilderSourceRecoveryRequired,
    BuilderWorkspaceService,
)


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/example",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("projects/demo/",),
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[BuilderSourceRecoveryService, Path, Path, Path, Path]:
    source_root = tmp_path / "source"
    scenario_source = source_root / "scenarios" / "demo_scene"
    skill_source = source_root / "skills" / "demo_skill"
    scenario_source.mkdir(parents=True)
    skill_source.mkdir(parents=True)
    (scenario_source / "scenario.yaml").write_text(
        "id: demo_scene\nversion: 1.0.0\ntitle: Demo scene\n",
        encoding="utf-8",
    )
    (scenario_source / "webui.json").write_text('{"title":"stable"}\n', encoding="utf-8")
    (skill_source / "skill.yaml").write_text(
        "name: demo_skill\nversion: 2.0.0\n",
        encoding="utf-8",
    )
    (skill_source / "handler.py").write_text("VALUE = 'stable'\n", encoding="utf-8")

    scenario = build_artifact_package(scenario_source, kind="scenario", source_ref=_source())
    skill = build_artifact_package(skill_source, kind="skill", source_ref=_source())
    plan = build_project_release(
        project_id="demo_project",
        version="1.0.0",
        source_ref=_source(),
        components=(scenario.ref,),
        catalog=PackageCatalog((skill.ref,)),
        requirements_by_package={
            scenario.ref.digest: (DependencyRequirement("skill", "demo_skill", "2.0.0"),)
        },
    )

    state_dir = tmp_path / "state"
    artifact_state = state_dir / "artifact_pipeline"
    store = ContentAddressedPackageStore(artifact_state / "packages")
    store.put(scenario.archive_bytes, expected_digest=scenario.ref.digest)
    store.put(skill.archive_bytes, expected_digest=skill.ref.digest)

    release_digest = str(plan.release.release_digest)
    release_path = (
        artifact_state
        / "release-cache"
        / "projects"
        / "demo_project"
        / "releases"
        / f"{release_digest.split(':', 1)[1]}.json"
    )
    _write_json(release_path, {"schema": "adaos.artifact.release_plan.v1", **plan.explain()})

    workspace = tmp_path / "workspace"
    workspace_scenario = workspace / "scenarios" / "demo_scene"
    workspace_skill = workspace / "skills" / "demo_skill"
    shutil.copytree(scenario_source, workspace_scenario)
    shutil.copytree(skill_source, workspace_skill)
    project_root = workspace / "projects" / "demo_project"
    project_root.mkdir(parents=True)
    (project_root / "project.yaml").write_text(
        "schema: adaos.project.v1\nid: demo_project\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-09-02T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="demo_project",
                project_id="demo_project",
                release="demo_project@1.0.0",
                release_digest=release_digest,
            ),
        ),
        components=plan.packages,
        bindings=plan.bindings,
    )
    _write_json(workspace / ".adaos" / "workspace.lock.json", lock.to_dict())

    dev_root = tmp_path / "dev"
    service = BuilderSourceRecoveryService(
        state_dir=state_dir,
        workspace_root=workspace,
        dev_skills_root=dev_root / "skills",
        dev_scenarios_root=dev_root / "scenarios",
        dev_projects_root=dev_root / "projects",
    )
    return service, workspace_scenario, workspace_skill, scenario_source, skill_source


def test_source_recovery_plan_resolves_project_ownership_and_dependency_role(
    tmp_path: Path,
) -> None:
    service, _, _, _, _ = _fixture(tmp_path)

    plan = service.plan(kind="project", artifact_id="demo_project")

    assert plan["status"] == "ready_to_materialize"
    assert plan["safe_to_apply"] is True
    assert plan["requires_review"] is False
    assert plan["plan_digest"].startswith("sha256:")
    assert plan["projects"][0]["project_id"] == "demo_project"
    components = {item["component_ref"]: item for item in plan["components"]}
    assert components["scenario:demo_scene"]["project_bindings"][0]["role"] == "owned"
    assert components["skill:demo_skill"]["project_bindings"][0]["role"] == "dependency"
    assert {item["classification"] for item in components.values()} == {
        "needs_dev_materialization"
    }


def test_source_recovery_plan_detects_workspace_drift_and_three_way_conflict(
    tmp_path: Path,
) -> None:
    service, workspace_scenario, _, scenario_source, _ = _fixture(tmp_path)
    workspace_file = workspace_scenario / "webui.json"
    workspace_file.write_text('{"title":"workspace edit"}\n', encoding="utf-8")

    review = service.plan(kind="scenario", artifact_id="demo_scene")

    assert review["status"] == "review_required"
    assert review["safe_to_apply"] is False
    assert review["components"][0]["classification"] == "workspace_drift"
    assert review["components"][0]["recommended_action"] == "import_workspace_delta"

    dev_scenario = service.dev_scenarios_root / "demo_scene"
    shutil.copytree(scenario_source, dev_scenario)
    (dev_scenario / "webui.json").write_text('{"title":"dev edit"}\n', encoding="utf-8")
    before_workspace = workspace_file.read_bytes()
    before_dev = (dev_scenario / "webui.json").read_bytes()

    conflict = service.plan(kind="scenario", artifact_id="demo_scene")

    assert conflict["status"] == "blocked"
    assert conflict["components"][0]["classification"] == "three_way_conflict"
    assert conflict["components"][0]["recommended_action"] == "reconcile_three_way"
    assert workspace_file.read_bytes() == before_workspace
    assert (dev_scenario / "webui.json").read_bytes() == before_dev


def test_source_recovery_plan_reports_dev_ahead_without_requiring_review(tmp_path: Path) -> None:
    service, _, _, scenario_source, _ = _fixture(tmp_path)
    dev_scenario = service.dev_scenarios_root / "demo_scene"
    shutil.copytree(scenario_source, dev_scenario)
    (dev_scenario / "webui.json").write_text('{"title":"candidate"}\n', encoding="utf-8")

    plan = service.plan(kind="scenario", artifact_id="demo_scene")

    assert plan["status"] == "source_available"
    assert plan["safe_to_apply"] is False
    assert plan["requires_review"] is False
    assert plan["components"][0]["classification"] == "dev_ahead"
    assert plan["components"][0]["recommended_action"] == "use_existing_dev_source"


def test_workspace_materialization_fails_closed_on_locked_workspace_drift(tmp_path: Path) -> None:
    recovery, workspace_scenario, _, _, _ = _fixture(tmp_path)
    (workspace_scenario / "webui.json").write_text(
        '{"title":"unreviewed workspace edit"}\n',
        encoding="utf-8",
    )
    service = BuilderWorkspaceService(
        state_dir=recovery.state_dir,
        workspace_root=recovery.workspace_root,
        skills_root=recovery.workspace_root / "skills",
        scenarios_root=recovery.workspace_root / "scenarios",
        dev_skills_root=recovery.dev_skills_root,
        dev_scenarios_root=recovery.dev_scenarios_root,
    )

    with pytest.raises(BuilderSourceRecoveryRequired) as captured:
        service.materialize_dev_source(kind="scenario", artifact_id="demo_scene")

    assert captured.value.plan["status"] == "review_required"
    assert captured.value.plan["components"][0]["classification"] == "workspace_drift"
    assert not (recovery.dev_scenarios_root / "demo_scene").exists()


def test_reviewed_recovery_preserves_evidence_materializes_owned_source_and_plans_change(
    tmp_path: Path,
) -> None:
    service, workspace_scenario, _, _, _ = _fixture(tmp_path)
    (workspace_scenario / "webui.json").write_text(
        '{"title":"reviewed workspace edit"}\n',
        encoding="utf-8",
    )
    (service.dev_projects_root / "demo_project").mkdir(parents=True)
    plan = service.plan(kind="project", artifact_id="demo_project")
    lock_before = (service.workspace_root / ".adaos" / "workspace.lock.json").read_bytes()

    receipt = service.apply(
        kind="project",
        artifact_id="demo_project",
        expected_plan_digest=plan["plan_digest"],
        decisions={"scenario:demo_scene": "adopt_workspace"},
        actor="user:test",
    )

    dev_scenario = service.dev_scenarios_root / "demo_scene"
    project_manifest = service.dev_projects_root / "demo_project" / "project.yaml"
    assert receipt["status"] == "applied_to_dev"
    assert receipt["change_id"].startswith("chg_recovery_")
    assert receipt["change_status"] == "planned"
    assert receipt["workspace_lock_digest"] == plan["workspace_lock_digest"]
    assert receipt["project_manifest"]["synthesized"] is True
    assert receipt["decisions"] == {
        "scenario:demo_scene": "adopt_workspace",
        "skill:demo_skill": "read_only",
    }
    assert len(receipt["evidence_refs"]) == 1
    assert receipt["evidence_refs"][0]["source"] == "workspace"
    assert dev_scenario.joinpath("webui.json").read_text(encoding="utf-8") == (
        '{"title":"reviewed workspace edit"}\n'
    )
    assert project_manifest.is_file()
    project = yaml.safe_load(project_manifest.read_text(encoding="utf-8"))
    assert project["components"]["owned"][0]["ref"] == "scenario:demo_scene"
    assert project["components"]["dependencies"][0]["ref"] == "skill:demo_skill"
    assert not (service.dev_skills_root / "demo_skill").exists()
    assert (service.workspace_root / ".adaos" / "workspace.lock.json").read_bytes() == lock_before
    assert (service.dev_projects_root / "demo_project" / "prompt_state.json").is_file()

    repeated = service.apply(
        kind="project",
        artifact_id="demo_project",
        expected_plan_digest=plan["plan_digest"],
        decisions={"scenario:demo_scene": "adopt_workspace"},
        actor="user:test",
    )
    assert repeated["idempotent"] is True
    assert repeated["receipt_digest"] == receipt["receipt_digest"]
    operation = json.loads(
        (
            service.recovery_root
            / "operations"
            / f"{plan['plan_digest'].removeprefix('sha256:')}.json"
        ).read_text(encoding="utf-8")
    )
    assert operation["status"] == "completed"
    assert operation["receipt_digest"] == receipt["receipt_digest"]


def test_source_recovery_tree_switch_retries_transient_windows_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "component"
    staged = tmp_path / ".component.staged"
    target.mkdir()
    staged.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    (staged / "value.txt").write_text("new", encoding="utf-8")
    replace_once = artifact_storage._replace_once
    failed = False

    def transient_replace(source: Path, destination: Path) -> None:
        nonlocal failed
        if Path(source) == staged and not failed:
            failed = True
            error = PermissionError("directory handle is settling")
            error.winerror = 5
            raise error
        replace_once(source, destination)

    monkeypatch.setattr(artifact_storage, "_replace_once", transient_replace)

    backup = BuilderSourceRecoveryService._switch_tree(staged, target)

    assert failed is True
    assert (target / "value.txt").read_text(encoding="utf-8") == "new"
    assert backup is not None
    assert (backup / "value.txt").read_text(encoding="utf-8") == "old"


def test_reviewed_recovery_rejects_stale_digest_and_missing_conflict_decision(
    tmp_path: Path,
) -> None:
    service, workspace_scenario, _, scenario_source, _ = _fixture(tmp_path)
    (workspace_scenario / "webui.json").write_text('{"title":"workspace"}\n', encoding="utf-8")
    dev_scenario = service.dev_scenarios_root / "demo_scene"
    shutil.copytree(scenario_source, dev_scenario)
    (dev_scenario / "webui.json").write_text('{"title":"dev"}\n', encoding="utf-8")
    plan = service.plan(kind="project", artifact_id="demo_project")

    with pytest.raises(ValueError, match="unknown components"):
        service.apply(
            kind="project",
            artifact_id="demo_project",
            expected_plan_digest=plan["plan_digest"],
            decisions={"scenario:not_in_plan": "keep_dev"},
        )

    with pytest.raises(ValueError, match="reviewed decision is required"):
        service.apply(
            kind="project",
            artifact_id="demo_project",
            expected_plan_digest=plan["plan_digest"],
        )

    (dev_scenario / "webui.json").write_text('{"title":"newer dev"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="plan changed"):
        service.apply(
            kind="project",
            artifact_id="demo_project",
            expected_plan_digest=plan["plan_digest"],
            decisions={"scenario:demo_scene": "keep_dev"},
        )


def test_builder_source_recovery_api_exposes_reviewed_plan_and_apply(tmp_path: Path) -> None:
    recovery, workspace_scenario, _, _, _ = _fixture(tmp_path)
    (workspace_scenario / "webui.json").write_text(
        '{"title":"api reviewed"}\n',
        encoding="utf-8",
    )
    service = BuilderWorkspaceService(
        state_dir=recovery.state_dir,
        workspace_root=recovery.workspace_root,
        skills_root=recovery.workspace_root / "skills",
        scenarios_root=recovery.workspace_root / "scenarios",
        dev_skills_root=recovery.dev_skills_root,
        dev_scenarios_root=recovery.dev_scenarios_root,
    )
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/builder/source-recovery/plan",
        params={"kind": "project", "artifact_id": "demo_project"},
    )

    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    assert plan["status"] == "review_required"
    response = client.post(
        "/api/builder/source-recovery/apply",
        json={
            "kind": "project",
            "artifact_id": "demo_project",
            "expected_plan_digest": plan["plan_digest"],
            "decisions": {"scenario:demo_scene": "adopt_workspace"},
            "actor": "user:api-test",
        },
    )

    assert response.status_code == 200, response.text
    receipt = response.json()["receipt"]
    assert receipt["status"] == "applied_to_dev"
    assert receipt["actor"] == "user:api-test"
    assert receipt["change_status"] == "planned"


def test_builder_source_recovery_sdk_uses_workspace_facade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery, _, _, _, _ = _fixture(tmp_path)
    service = BuilderWorkspaceService(
        state_dir=recovery.state_dir,
        workspace_root=recovery.workspace_root,
        skills_root=recovery.workspace_root / "skills",
        scenarios_root=recovery.workspace_root / "scenarios",
        dev_skills_root=recovery.dev_skills_root,
        dev_scenarios_root=recovery.dev_scenarios_root,
    )
    monkeypatch.setattr(
        BuilderWorkspaceService,
        "from_context",
        classmethod(lambda cls: service),
    )
    from adaos.sdk.builder import source_recovery

    plan = source_recovery.plan(kind="project", artifact_id="demo_project")
    receipt = source_recovery.apply(
        kind="project",
        artifact_id="demo_project",
        expected_plan_digest=plan["plan_digest"],
        actor="builder:sdk-test",
    )

    assert receipt["status"] == "applied_to_dev"
    assert receipt["decisions"]["scenario:demo_scene"] == "reset_to_locked"
    assert receipt["decisions"]["skill:demo_skill"] == "read_only"
