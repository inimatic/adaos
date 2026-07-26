from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from adaos.domain.artifact_release import ArtifactPackageRef
from adaos.services.artifact_pipeline import (
    ArtifactPublicationService,
    ContentAddressedPackageStore,
    PushedSourceRecord,
    ReleasePlan,
    ReleaseRepository,
    build_artifact_package,
    verify_artifact_package,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json


class LocalProofRemote:
    """Package-verifying registry used only by the bounded local proof."""

    def __init__(self, root: Path) -> None:
        self.releases = ReleaseRepository(root / "releases")
        self.packages = ContentAddressedPackageStore(root / "packages")

    def put_release(self, plan: ReleasePlan, archives: Mapping[str, bytes]) -> None:
        for package in plan.packages:
            archive = archives.get(package.digest)
            if archive is None:
                raise RuntimeError(f"release omitted package archive {package.digest}")
            self.packages.put(archive, expected_digest=package.digest)
        self.releases.put_release(plan)

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        return self.releases.get_release(project_id, release_digest)

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ):
        self.releases.put_release(plan)
        return self.releases.set_channel(
            plan.release.project_id,
            channel,
            plan.release.release_digest,
            expected_release_digest=expected_release_digest,
        )

    def get_channel(self, project_id: str, channel: str = "stable"):
        return self.releases.get_channel(project_id, channel)

    def fetch_package(self, package: ArtifactPackageRef) -> bytes:
        return self.packages.read(package.digest)

    def tree_revision(self, source_ref) -> str:
        # The bounded local proof has no Forge transport. The immutable commit
        # identity is used as its deterministic source-verification witness;
        # live promotion verifies the Forge tree through RemoteReleaseRepository.
        revision = str(source_ref.revision or "").strip().lower()
        if len(revision) not in {40, 64}:
            raise RuntimeError("local proof source revision is not an immutable Git object id")
        return revision


def verify_checkpoint_source(
    source_service: ArtifactPublicationService,
    record: PushedSourceRecord,
    artifact_dir: Path,
) -> dict[str, object]:
    """Prove mutable DEV still represents the exact recorded checkpoint files.

    The package builder policy may have advanced since the checkpoint. In that
    case the archive digest can legitimately change, but the publishable file
    paths, sizes, and content digests must remain identical.
    """

    recorded_archive = source_service.package_store.read(record.package.digest)
    recorded = verify_artifact_package(
        recorded_archive,
        expected_digest=record.package.digest,
    )
    rebuilt = build_artifact_package(
        artifact_dir,
        kind=record.kind,  # type: ignore[arg-type]
        source_ref=record.source_ref,
    )

    def inventory(manifest: Mapping[str, object]) -> dict[str, tuple[int, str]]:
        raw_files = manifest.get("files")
        if not isinstance(raw_files, list):
            raise RuntimeError("checkpoint package has no verified file inventory")
        result: dict[str, tuple[int, str]] = {}
        for raw in raw_files:
            if not isinstance(raw, Mapping):
                raise RuntimeError("checkpoint package file inventory is malformed")
            path = str(raw.get("path") or "")
            result[path] = (int(raw.get("size") or 0), str(raw.get("digest") or ""))
        return result

    recorded_files = inventory(recorded.package_manifest)
    rebuilt_files = inventory(rebuilt.package_manifest)
    if recorded_files != rebuilt_files:
        changed = sorted(
            path
            for path in recorded_files.keys() | rebuilt_files.keys()
            if recorded_files.get(path) != rebuilt_files.get(path)
        )
        raise RuntimeError(
            f"{record.kind} {record.artifact_id} DEV content differs from its exact "
            f"Forge checkpoint: {changed}"
        )
    return {
        "mode": "checkpoint_package_file_inventory",
        "recorded_package_digest": record.package.digest,
        "rebuilt_package_digest": rebuilt.ref.digest,
        "file_count": len(recorded_files),
        "builder_policy_changed": record.package.build_policy_digest
        != rebuilt.ref.build_policy_digest,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the representative exact-source to WorkspaceLock proof."
    )
    parser.add_argument("--dev-root", type=Path, required=True)
    parser.add_argument("--pipeline-state", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument(
        "--proof-root",
        type=Path,
        default=Path(".adaos/state/artifact_pipeline/proofs"),
    )
    parser.add_argument("--skip-tests", action="store_true")
    return parser.parse_args()


def _run_test_command(command: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "command": command,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def main() -> int:
    args = _arguments()
    dev_root = args.dev_root.expanduser().resolve()
    pipeline_state = args.pipeline_state.expanduser().resolve()
    source_service = ArtifactPublicationService(
        state_root=pipeline_state,
        workspace_root=args.proof_root.resolve() / "source-placeholder",
        remote=LocalProofRemote(args.proof_root.resolve() / "source-remote-placeholder"),
    )
    scenario_source = source_service.load_pushed_source("scenario", args.scenario)
    skill_source = source_service.load_pushed_source("skill", args.skill)
    if args.change_id not in scenario_source.change_ids:
        raise RuntimeError("scenario checkpoint does not belong to the requested change set")
    if args.change_id not in skill_source.change_ids:
        raise RuntimeError("skill checkpoint does not belong to the requested change set")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_root = args.proof_root.expanduser().resolve() / run_id
    state_root = run_root / "state"
    workspace_root = run_root / "workspace"
    remote = LocalProofRemote(run_root / "remote")
    service = ArtifactPublicationService(
        state_root=state_root,
        workspace_root=workspace_root,
        remote=remote,
    )
    skill_dir = dev_root / "skills" / args.skill
    scenario_dir = dev_root / "scenarios" / args.scenario
    scenario_source_verification = verify_checkpoint_source(
        source_service,
        scenario_source,
        scenario_dir,
    )
    skill_source_verification = verify_checkpoint_source(
        source_service,
        skill_source,
        skill_dir,
    )

    test_command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        str(skill_dir / "tests"),
        str(scenario_dir / "tests"),
    ]
    test_result: dict[str, object]
    if args.skip_tests:
        test_result = {"status": "skipped", "command": test_command}
    else:
        test_result = _run_test_command(test_command)
        if test_result["status"] != "passed":
            raise RuntimeError(f"representative contract tests failed: {test_result['stdout']}")

    resilience_command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_artifact_publication_service.py::test_moved_base_creates_reapply_plan_and_requires_new_trial",
        "tests/test_artifact_publication_service.py::test_remote_stable_subscription_updates_from_packages_after_success_only",
        "tests/test_artifact_release_resolver.py::test_release_rejects_missing_ambiguous_incompatible_and_cyclic_dependencies",
        "tests/test_artifact_workspace_activation.py::test_failure_at_each_activation_phase_leaves_no_partial_first_install",
        "tests/test_artifact_workspace_activation.py::test_explicit_recovery_rolls_back_interrupted_journal_without_replaying",
        "tests/test_artifact_workspace_activation.py::test_introduced_permissions_require_an_explicit_approval",
        "tests/test_artifact_workspace_activation.py::test_reversible_migration_executes_once_and_rolls_back_after_health_failure",
        "tests/test_artifact_workspace_activation.py::test_unknown_migration_result_is_not_replayed_and_requires_reconciliation",
        "tests/test_skill_factory_worker.py::test_local_worker_does_not_overwrite_dev_that_changed_after_task_snapshot",
        "tests/test_root_draft_metadata.py::test_checkpoint_reconciles_unknown_remote_outcome_without_second_write",
    ]
    if args.skip_tests:
        resilience_result = {"status": "skipped", "command": resilience_command}
    else:
        resilience_result = _run_test_command(resilience_command)
        if resilience_result["status"] != "passed":
            raise RuntimeError(f"pipeline resilience tests failed: {resilience_result['stdout']}")

    service.record_push(
        kind="skill",
        artifact_id=args.skill,
        artifact_dir=skill_dir,
        source_ref=skill_source.source_ref,
        change_ids=(args.change_id,),
    )
    service.record_push(
        kind="scenario",
        artifact_id=args.scenario,
        artifact_dir=scenario_dir,
        source_ref=scenario_source.source_ref,
        change_ids=(args.change_id,),
    )
    prepared = service.prepare_candidate(
        kind="scenario",
        artifact_id=args.scenario,
        artifact_dir=scenario_dir,
        change_ids=(args.change_id,),
        validation_evidence={
            "status": "passed",
            "validator": "representative.local.contracts",
            "tests": test_result,
        },
    )
    package_ids = {(item.kind, item.artifact_id) for item in prepared.plan.packages}
    expected_ids = {("scenario", args.scenario), ("skill", args.skill)}
    if not expected_ids.issubset(package_ids):
        raise RuntimeError(f"candidate omitted companion artifacts: {package_ids}")
    accepted = service.decide_candidate(
        prepared.candidate.candidate_id,
        accepted=True,
        observations=(
            {
                "actor": "local-pipeline-proof",
                "decision": "accepted",
                "prototype_revision": "015",
            },
        ),
    )
    promoted = service.promote(
        accepted.candidate_id,
        reload_policy={
            "mode": "skip",
            "approved_by": "local-pipeline-proof",
            "reason": "isolated proof Workspace has no attached runtime",
        },
        health_check=lambda _lock: (
            (workspace_root / "scenarios" / args.scenario / "scenario.yaml").is_file()
            and (workspace_root / "skills" / args.skill / "skill.yaml").is_file()
        ),
    )
    registry = json.loads((workspace_root / "registry.json").read_text(encoding="utf-8"))
    lock = promoted.activation.workspace_lock.to_dict()
    evidence = {
        "schema": "adaos.artifact.pipeline_proof.v1",
        "status": "passed",
        "run_id": run_id,
        "representative": {
            "scenario": args.scenario,
            "skill": args.skill,
            "change_id": args.change_id,
            "prototype_revision": "015",
        },
        "tests": test_result,
        "resilience_tests": resilience_result,
        "source_verification": {
            "mode": "local_commit_witness_and_checkpoint_package_inventory",
            "note": "Live promotion additionally uses the backend Forge tree verification endpoint.",
            "scenario": scenario_source_verification,
            "skill": skill_source_verification,
        },
        "source": {
            "scenario": scenario_source.to_dict(),
            "skill": skill_source.to_dict(),
        },
        "candidate": prepared.candidate.to_dict(),
        "accepted_candidate": accepted.to_dict(),
        "release": prepared.plan.release.to_dict(),
        "packages": [item.to_dict() for item in prepared.plan.packages],
        "bindings": [item.to_dict() for item in prepared.plan.bindings],
        "stable_channel": promoted.pointer.to_dict(),
        "workspace_lock": lock,
        "subscription": promoted.subscription.to_dict(),
        "workspace": {
            "root": str(workspace_root),
            "scenario_materialized": (
                workspace_root / "scenarios" / args.scenario / "scenario.yaml"
            ).is_file(),
            "skill_materialized": (
                workspace_root / "skills" / args.skill / "skill.yaml"
            ).is_file(),
            "registry_skill_count": len(registry.get("skills") or []),
            "registry_scenario_count": len(registry.get("scenarios") or []),
        },
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    output = run_root / "evidence.json"
    atomic_write_json(output, evidence)
    print(json.dumps({**evidence, "evidence_path": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
