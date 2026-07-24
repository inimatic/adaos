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
