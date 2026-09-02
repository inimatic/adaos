from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef, WorkspaceLock, WorkspaceSlot
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    DependencyRequirement,
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
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
