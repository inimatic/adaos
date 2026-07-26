from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import ArtifactPublicationService
from tools.verify_artifact_pipeline import LocalProofRemote, verify_checkpoint_source


def test_verifier_rejects_dev_content_changed_after_checkpoint(tmp_path: Path) -> None:
    skill_id = "proof_skill"
    skill = tmp_path / "dev" / "skills" / skill_id
    skill.mkdir(parents=True)
    manifest = skill / "skill.yaml"
    manifest.write_text(
        f"id: {skill_id}\nname: {skill_id}\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    publication = ArtifactPublicationService(
        state_root=tmp_path / "pipeline-state",
        workspace_root=tmp_path / "source-workspace",
        remote=LocalProofRemote(tmp_path / "source-remote"),
    )
    record = publication.record_push(
        kind="skill",
        artifact_id=skill_id,
        artifact_dir=skill,
        source_ref=ArtifactSourceRef(
            forge="test",
            repository="test/proof",
            revision="1" * 40,
            path_scope=(f"skills/{skill_id}/",),
        ),
    )
    manifest.write_text(
        f"id: {skill_id}\nname: {skill_id}\nversion: 1.0.1\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="DEV content differs"):
        verify_checkpoint_source(publication, record, skill)


def test_verifier_uses_current_promotion_contract(tmp_path: Path) -> None:
    dev_root = tmp_path / "dev"
    scenario_id = "proof_scenario"
    skill_id = "proof_skill"
    scenario = dev_root / "scenarios" / scenario_id
    skill = dev_root / "skills" / skill_id
    scenario.mkdir(parents=True)
    skill.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        f"id: {scenario_id}\nversion: 1.0.0\ndepends:\n  - {skill_id}\n",
        encoding="utf-8",
    )
    (skill / "skill.yaml").write_text(
        f"id: {skill_id}\nname: {skill_id}\nversion: 1.0.0\n",
        encoding="utf-8",
    )

    state_root = tmp_path / "pipeline-state"
    publication = ArtifactPublicationService(
        state_root=state_root,
        workspace_root=tmp_path / "source-workspace",
        remote=LocalProofRemote(tmp_path / "source-remote"),
    )
    change_id = "change-proof-contract"
    publication.record_push(
        kind="skill",
        artifact_id=skill_id,
        artifact_dir=skill,
        source_ref=ArtifactSourceRef(
            forge="test",
            repository="test/proof",
            revision="1" * 40,
            path_scope=(f"skills/{skill_id}/",),
        ),
        change_ids=(change_id,),
    )
    publication.record_push(
        kind="scenario",
        artifact_id=scenario_id,
        artifact_dir=scenario,
        source_ref=ArtifactSourceRef(
            forge="test",
            repository="test/proof",
            revision="2" * 40,
            path_scope=(f"scenarios/{scenario_id}/",),
        ),
        change_ids=(change_id,),
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools" / "verify_artifact_pipeline.py"),
            "--dev-root",
            str(dev_root),
            "--pipeline-state",
            str(state_root),
            "--scenario",
            scenario_id,
            "--skill",
            skill_id,
            "--change-id",
            change_id,
            "--proof-root",
            str(tmp_path / "proofs"),
            "--skip-tests",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["status"] == "passed"
    assert evidence["workspace"]["scenario_materialized"] is True
    assert evidence["workspace"]["skill_materialized"] is True
    assert evidence["stable_channel"]["release_digest"] == evidence["release"][
        "release_digest"
    ]
