from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    ContentAddressedPackageStore,
    PackageCatalog,
    build_artifact_package,
    build_project_release,
)
from tools.verify_artifact_pipeline import LocalProofRemote
from tools.verify_artifact_pipeline_stand import (
    backend_commit_matches,
    release_plan_from_evidence,
    run_external_stand,
)


def _proof_fixture(tmp_path: Path):
    scenario = tmp_path / "source" / "scenarios" / "stand_scenario"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: stand_scenario\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    source_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/adaos-registry",
        revision="1" * 40,
        path_scope=("subnets/stand/nodes/node/scenarios/stand_scenario/",),
    )
    built = build_artifact_package(scenario, kind="scenario", source_ref=source_ref)
    plan = build_project_release(
        project_id="stand_scenario",
        version="1.0.0",
        source_ref=source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
    )
    source_store = ContentAddressedPackageStore(tmp_path / "source-packages")
    source_store.put(built.archive_bytes, expected_digest=built.ref.digest)
    evidence_path = tmp_path / "source-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "release": plan.release.to_dict(),
                "packages": [item.to_dict() for item in plan.packages],
                "bindings": [item.to_dict() for item in plan.bindings],
            }
        ),
        encoding="utf-8",
    )
    return plan, source_store, evidence_path


def test_external_stand_fetches_into_empty_cache_and_workspace(tmp_path: Path) -> None:
    plan, source_store, evidence_path = _proof_fixture(tmp_path)
    stand_root = tmp_path / "stand"

    result = run_external_stand(
        plan=plan,
        source_store=source_store,
        remote=LocalProofRemote(tmp_path / "remote"),
        stand_root=stand_root,
        channel="stand-proof",
        source_evidence=evidence_path,
        backend_health={"version": "test", "commit": "1" * 7, "ready": True},
    )

    assert result["status"] == "passed"
    assert result["remote_channel"]["release_digest"] == plan.release.release_digest
    assert (stand_root / "workspace" / "scenarios" / "stand_scenario").is_dir()
    assert ContentAddressedPackageStore(stand_root / "package-cache").has(
        plan.packages[0].digest
    )
    assert json.loads((stand_root / "evidence.json").read_text(encoding="utf-8"))[
        "status"
    ] == "passed"

    with pytest.raises(FileExistsError, match="clean stand root already exists"):
        run_external_stand(
            plan=plan,
            source_store=source_store,
            remote=LocalProofRemote(tmp_path / "other-remote"),
            stand_root=stand_root,
            channel="stand-proof",
            source_evidence=evidence_path,
            backend_health={"ready": True},
        )


def test_release_plan_is_reconstructed_from_pipeline_evidence(tmp_path: Path) -> None:
    plan, _source_store, evidence_path = _proof_fixture(tmp_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert release_plan_from_evidence(payload) == plan


@pytest.mark.parametrize(
    ("expected", "observed", "matches"),
    [
        ("", "", True),
        ("5570f330", "5570f33", True),
        ("5570f33", "5570f330", True),
        ("5570f330", "", False),
        ("5570f330", "1329ecb", False),
    ],
)
def test_backend_commit_match_is_fail_closed(
    expected: str,
    observed: str,
    matches: bool,
) -> None:
    assert backend_commit_matches(expected, observed) is matches


def test_cli_requires_explicit_publish_before_transport(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "tools" / "verify_artifact_pipeline_stand.py"),
            "--evidence",
            str(tmp_path / "missing.json"),
            "--stand-root",
            str(tmp_path / "stand"),
            "--base-url",
            "https://example.invalid",
            "--ca",
            str(tmp_path / "ca"),
            "--cert",
            str(tmp_path / "cert"),
            "--key",
            str(tmp_path / "key"),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert completed.returncode != 0
    assert "requires explicit --publish" in completed.stderr
    assert not (tmp_path / "stand").exists()
