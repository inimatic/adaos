from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    ArtifactPublicationService,
    PublicationError,
    ReleasePlan,
    ReleaseRepository,
)


class _Remote:
    def __init__(self, root: Path) -> None:
        self.releases = ReleaseRepository(root / "releases")
        self.archives: dict[str, bytes] = {}

    def put_release(self, plan: ReleasePlan, archives: dict[str, bytes]) -> None:
        self.archives.update(archives)
        self.releases.put_release(plan)

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        return self.releases.get_release(project_id, release_digest)

    def set_channel(self, plan: ReleasePlan, channel: str = "stable"):
        self.releases.put_release(plan)
        return self.releases.set_channel(
            plan.release.project_id,
            channel,
            plan.release.release_digest,
        )

    def get_channel(self, project_id: str, channel: str = "stable"):
        return self.releases.get_channel(project_id, channel)

    def fetch_package(self, package: ArtifactPackageRef) -> bytes:
        return self.archives[package.digest]


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("subnets/dev/nodes/node/scenarios/recipes/",),
    )


def _scenario(root: Path) -> Path:
    scenario = root / "recipes"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ntitle: Recipes\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text('{"ui": {}}\n', encoding="utf-8")
    return scenario


def _skill(root: Path) -> Path:
    skill = root / "shopping_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: shopping_skill\nversion: 2.1.0\n",
        encoding="utf-8",
    )
    (skill / "handlers.py").write_text("def run(): return True\n", encoding="utf-8")
    return skill


def test_checkpoint_candidate_isolated_trial_and_stable_promotion(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "primary-marker.txt").write_text("unchanged", encoding="utf-8")
    remote = _Remote(tmp_path / "remote")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=workspace,
        remote=remote,
    )

    pushed = service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        change_ids=("change-create-recipes",),
        validation_evidence={"suite": "scenario-validation", "status": "passed"},
    )

    assert pushed.package.digest == prepared.plan.packages[0].digest
    assert prepared.candidate.status == "trial"
    assert (prepared.trial_workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert not (workspace / "scenarios" / "recipes").exists()
    assert (workspace / "primary-marker.txt").read_text(encoding="utf-8") == "unchanged"

    accepted = service.decide_candidate(
        prepared.candidate.candidate_id,
        accepted=True,
        observations=({"user": "owner", "decision": "looks_good"},),
    )
    result = service.promote(accepted.candidate_id, health_check=lambda lock: True)

    assert result.pointer.release == "recipes@1.0.0"
    assert (workspace / "scenarios" / "recipes" / "scenario.yaml").is_file()
    assert service.subscriptions.load()["recipes"].installed_digest == result.pointer.release_digest
    registry = (workspace / "registry.json").read_text(encoding="utf-8")
    assert '"stable"' in registry


def test_candidate_rejects_dev_changes_after_checkpoint(tmp_path: Path) -> None:
    dev = _scenario(tmp_path / "dev")
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=_Remote(tmp_path / "remote"),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=dev,
        source_ref=_source(),
    )
    (dev / "webui.json").write_text('{"ui": {"changed": true}}\n', encoding="utf-8")

    with pytest.raises(PublicationError, match="changed after"):
        service.prepare_candidate(
            kind="scenario",
            artifact_id="recipes",
            artifact_dir=dev,
            change_ids=("change-after-push",),
            validation_evidence={"status": "passed"},
        )


def test_scenario_candidate_locks_and_materializes_stable_skill_dependency(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    skill_dir = _skill(tmp_path / "dev")
    skill_service = ArtifactPublicationService(
        state_root=tmp_path / "skill-state",
        workspace_root=tmp_path / "skill-workspace",
        remote=remote,
    )
    skill_service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
    )
    skill_candidate = skill_service.prepare_candidate(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        change_ids=("change-skill",),
        validation_evidence={"status": "passed"},
    )
    skill_service.decide_candidate(skill_candidate.candidate.candidate_id, accepted=True)
    skill_service.promote(skill_candidate.candidate.candidate_id)

    scenario_dir = _scenario(tmp_path / "dev")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    service = ArtifactPublicationService(
        state_root=tmp_path / "scenario-state",
        workspace_root=workspace,
        remote=remote,
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("change-recipes",),
        validation_evidence={"status": "passed"},
    )

    assert [(item.kind, item.artifact_id, item.version) for item in prepared.plan.packages] == [
        ("scenario", "recipes", "1.0.0"),
        ("skill", "shopping_skill", "2.1.0"),
    ]
    assert (
        prepared.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()


def test_scenario_candidate_includes_companion_skill_from_same_change_set(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    scenario_dir = _scenario(dev_root / "scenarios")
    skill_dir = _skill(dev_root / "skills")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    change_id = "change-recipe-editor"
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=(change_id,),
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=(change_id,),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=(change_id,),
        validation_evidence={"status": "passed"},
    )

    assert [(item.kind, item.artifact_id, item.version) for item in prepared.plan.packages] == [
        ("scenario", "recipes", "1.0.0"),
        ("skill", "shopping_skill", "2.1.0"),
    ]
    assert prepared.plan.packages[1].source_ref == _source()
    assert (
        prepared.trial_workspace / "skills" / "shopping_skill" / "skill.yaml"
    ).is_file()


def test_scenario_candidate_does_not_mix_unrelated_dev_dependency(
    tmp_path: Path,
) -> None:
    remote = _Remote(tmp_path / "remote")
    dev_root = tmp_path / "dev"
    skill_dir = _skill(dev_root / "skills")
    skill_service = ArtifactPublicationService(
        state_root=tmp_path / "stable-state",
        workspace_root=tmp_path / "stable-workspace",
        remote=remote,
    )
    skill_service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
    )
    stable_candidate = skill_service.prepare_candidate(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        change_ids=("stable-skill",),
        validation_evidence={"status": "passed"},
    )
    skill_service.decide_candidate(stable_candidate.candidate.candidate_id, accepted=True)
    skill_service.promote(stable_candidate.candidate.candidate_id)

    (skill_dir / "skill.yaml").write_text(
        "name: shopping_skill\nversion: 3.0.0\n",
        encoding="utf-8",
    )
    service = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "workspace",
        remote=remote,
    )
    service.record_push(
        kind="skill",
        artifact_id="shopping_skill",
        artifact_dir=skill_dir,
        source_ref=_source(),
        change_ids=("unrelated-change",),
    )
    scenario_dir = _scenario(dev_root / "scenarios")
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.0.0\ndepends:\n  - shopping_skill\n",
        encoding="utf-8",
    )
    service.record_push(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        source_ref=_source(),
        change_ids=("scenario-change",),
    )

    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id="recipes",
        artifact_dir=scenario_dir,
        change_ids=("scenario-change",),
        validation_evidence={"status": "passed"},
    )

    skill_package = next(item for item in prepared.plan.packages if item.kind == "skill")
    assert skill_package.version == "2.1.0"
