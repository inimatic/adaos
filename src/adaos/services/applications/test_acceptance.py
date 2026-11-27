"""Execute the frozen Application's whole test plan before publication effects."""
from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any

from adaos.domain.artifact_release import ProjectRelease, canonical_payload_digest
from adaos.services.artifact_pipeline.project_source_sync import capture_project
from adaos.services.artifact_pipeline.source_validation import validate_restored_project
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
from adaos.services.source_policy import source_policy


class CandidateTestSourceUnavailable(ValueError):
    """The immutable release has no retained whole-Project qualification basis."""


class CandidateTestSourceListChanged(ValueError):
    """The retained Candidate belongs to another source list."""


def _restore_candidate_test_source(owner, candidate, release, custody, destination):
    """Read the original whole-Project receipt, never today's DEV or a component ZIP."""
    from adaos.services.root.service import RootDeveloperService

    if (custody.get('commit') != release.source_ref.revision
            or custody.get('repository') != release.source_ref.repository):
        raise ValueError('Candidate source custody differs from its release')
    sync = RootDeveloperService(ctx=owner).project_source_sync()
    for change_id in candidate.change_ids:
        intent_path, _ = sync._paths(sync._scope(candidate.project_id, change_id), custody['operation_id'])
        intent = sync._read_json(intent_path)
        receipt = intent.get('receipt') or {}
        fields = ('repository', 'commit', 'tree', 'snapshot_digest', 'retention_ref', 'operation_id')
        if (intent.get('state') == 'remote_confirmed' and not receipt.get('custody')
                and all(receipt.get(key) == custody.get(key) for key in fields)):
            restored = sync.restore(candidate.project_id, change_id,
                operation_id=custody['operation_id'], destination=destination)
            if (restored.get('commit') != custody['commit']
                    or restored.get('snapshot_digest') != custody['snapshot_digest']):
                raise ValueError('Restored Candidate source differs from its custody')
            return
    raise ValueError('Candidate has no matching confirmed whole-Project source receipt')


@contextmanager
def _candidate_test_source(owner, candidate, release, runtime):
    custody = [dict(item['source_custody']) for item in release.validation_evidence
               if isinstance(item.get('source_custody'), dict)]
    if not custody:
        # Legacy whole-Project Trials still have to match their qualified snapshot.
        if not (runtime.root / 'projects' / candidate.project_id / 'project.yaml').is_file():
            raise CandidateTestSourceUnavailable('Candidate lacks confirmed whole-Project test source')
        yield runtime.root
        return
    if any(item != custody[0] for item in custody):
        raise ValueError('Candidate has conflicting whole-Project source custody')
    staging = Path(owner.paths.state_dir()) / 'artifact_pipeline/test-source-staging'
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='candidate-', dir=staging) as folder:
        workspace = Path(folder)
        _restore_candidate_test_source(owner, candidate, release, custody[0], workspace)
        yield workspace


def qualify_candidate_tests(owner, candidate) -> dict[str, Any]:
    from .trial_runtime import NativeTrialRuntime
    runtime = NativeTrialRuntime.resolve(owner, candidate.candidate_id, candidate.release_digest)
    if runtime.project_id != candidate.project_id:
        raise ValueError("Candidate test Project identity differs")
    state = Path(owner.paths.state_dir())
    activation = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate.candidate_id)
    lock_digest = (activation.get("runtime_binding") or {}).get("workspace_lock_digest")
    if not lock_digest:
        raise ValueError("Candidate tests require an exact WorkspaceLock")
    release_path = runtime.root / ".adaos/releases" / (candidate.release_digest.split(":")[1] + ".json")
    release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8")))
    selected = source_policy(owner.config)
    if release.source_branch != selected["branch"] or not selected.get("confirmed"):
        raise CandidateTestSourceListChanged("Candidate tests belong to another source list")
    for package in runtime.packages:
        runtime.verified_source(package)
    qualified_snapshots = {
        item["source_validation"].get("source_snapshot_digest")
        for item in release.validation_evidence
        if isinstance(item.get("source_validation"), dict)
        and item["source_validation"].get("status") == "passed"
    }
    if not qualified_snapshots:
        raise CandidateTestSourceUnavailable("Candidate lacks a qualified whole-Project test source")
    with _candidate_test_source(owner, candidate, release, runtime) as workspace:
        snapshot = capture_project(workspace, candidate.project_id)
        if qualified_snapshots != {snapshot.digest}:
            raise ValueError("Candidate requires its qualified whole-Project source snapshot")
        tests = validate_restored_project(workspace=workspace, project_id=candidate.project_id,
            snapshot_digest=snapshot.digest, state_root=state / "artifact_pipeline",
            timeout_seconds=600, max_wall_seconds=3600)
        verified = NativeTrialRuntime.resolve(owner, candidate.candidate_id, candidate.release_digest)
        for package in verified.packages:
            verified.verified_source(package)
        current_activation = TrialActivationStore(state / "artifact_pipeline/trial-activations").load(candidate.candidate_id)
        identity_fields = ("runtime_binding", "candidate_ref", "package_refs", "data_mode")
        if (verified.root != runtime.root
                or any(current_activation.get(key) != activation.get(key) for key in identity_fields)
                or source_policy(owner.config) != selected
                or capture_project(workspace, candidate.project_id).digest != snapshot.digest):
            raise ValueError("Candidate or source policy changed during test qualification")
    binding = {"schema": "adaos.application.test_acceptance.v1", "status": "passed",
        "candidate_id": candidate.candidate_id, "project_id": candidate.project_id,
        "package_digest": candidate.package_digest, "release_digest": candidate.release_digest,
        "workspace_lock_digest": lock_digest, "source_policy": selected,
        "source_snapshot_digest": snapshot.digest,
        "test_receipt_digest": tests["receipt_digest"], "test_identity": tests["identity"]}
    binding["receipt_digest"] = canonical_payload_digest(binding)
    atomic_write_json(state / "artifact_pipeline" / "test-acceptance" /
                      f"{candidate.candidate_id}.json", binding)
    return binding


def read_candidate_test_acceptance(owner, candidate) -> dict[str, Any] | None:
    """Inspect the exact current receipt without restoring source or running tests."""
    root = Path(owner.paths.state_dir()) / "artifact_pipeline"
    path = root / "test-acceptance" / f"{candidate.candidate_id}.json"
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
        unsigned = {key: value for key, value in binding.items() if key != "receipt_digest"}
        expected = {"candidate_id": candidate.candidate_id, "project_id": candidate.project_id,
                    "package_digest": candidate.package_digest, "release_digest": candidate.release_digest}
        if (binding.get("schema") != "adaos.application.test_acceptance.v1"
                or binding.get("status") != "passed"
                or binding.get("receipt_digest") != canonical_payload_digest(unsigned)
                or any(binding.get(key) != value for key, value in expected.items())
                or binding.get("source_policy") != source_policy(owner.config)):
            raise ValueError("Candidate test acceptance identity differs")
        identity = binding["test_identity"]
        if identity.get("source_snapshot_digest") != binding["source_snapshot_digest"]:
            raise ValueError("Candidate test source identity differs")
        from adaos.services.artifact_pipeline.source_validation import validation_environment
        if identity.get("environment") != validation_environment():
            raise ValueError("Candidate test environment changed")
        activation = TrialActivationStore(root / "trial-activations").load(candidate.candidate_id)
        if ((activation.get("runtime_binding") or {}).get("workspace_lock_digest")
                != binding.get("workspace_lock_digest")):
            raise ValueError("Candidate test WorkspaceLock changed")
        directory = root / "source-validation" / canonical_payload_digest(identity).removeprefix("sha256:")
        tests = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        if (tests.get("status") != "passed" or tests.get("identity") != identity or not tests.get("packages")
                or tests.get("source_unchanged") is not True
                or tests.get("receipt_digest") != binding["test_receipt_digest"]
                or canonical_payload_digest({key: value for key, value in tests.items() if key != "receipt_digest"}) != tests["receipt_digest"]):
            raise ValueError("Candidate test receipt is missing or changed")
        plan = identity.get("test_plan") or {}
        suites = plan.get("suites") or []
        packages = tests["packages"]
        if (plan.get("complete_suite") is not True or not suites
                or len(suites) != len(set(suites))
                or sorted(suites) != sorted(package["ref"] for package in packages)
                or any(not package.get("results")
                       or any(result.get("status") != "passed" for result in package["results"])
                       for package in packages)):
            raise ValueError("Candidate acceptance does not cover its complete test plan")
        for package in tests["packages"]:
            log = directory / package["log"]
            if not log.resolve().is_relative_to(directory.resolve()) or hashlib.sha256(log.read_bytes()).hexdigest() != package["log_sha256"]:
                raise ValueError("Candidate test evidence changed")
        return binding
    except (OSError, ValueError, KeyError, TypeError):
        return None


def require_candidate_test_acceptance(owner, candidate) -> dict[str, Any]:
    return read_candidate_test_acceptance(owner, candidate) or qualify_candidate_tests(owner, candidate)


def export_candidate_test_proof(owner, candidate, release: ProjectRelease) -> dict:
    """Portable exact-release proof; private logs and environment values stay local."""
    binding = require_candidate_test_acceptance(owner, candidate)
    if (release.release_digest != binding['release_digest'] or release.project_id != binding['project_id']
            or candidate.package_digest not in {item.digest for item in release.components}):
        raise ValueError('Catalog proof differs from the accepted release')
    identity = binding['test_identity']
    key = canonical_payload_digest(identity).removeprefix('sha256:')
    root = Path(owner.paths.state_dir()) / 'artifact_pipeline/source-validation' / key
    tests = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))
    if (tests.get('receipt_digest') != binding['test_receipt_digest']
            or canonical_payload_digest({k:v for k,v in tests.items() if k != 'receipt_digest'}) != tests['receipt_digest']):
        raise ValueError('Catalog test receipt changed during export')
    proof = {'schema':'adaos.application.catalog_test_proof.v1', 'status':'passed',
        'project_id':release.project_id, 'version':release.version, 'release_digest':release.release_digest,
        'source_branch':release.source_branch, 'source_revision':release.source_ref.revision,
        'package_digests':sorted(package.digest for package in release.components),
        'source_snapshot_digest':binding['source_snapshot_digest'],
        'environment_digest':canonical_payload_digest(identity['environment']),
        'candidate_acceptance_digest':binding['receipt_digest'], 'test_receipt_digest':tests['receipt_digest'],
        'test_plan':identity['test_plan'],
        'packages':[{'ref':row['ref'], 'log_sha256':row['log_sha256'],
                     'statuses':[result['status'] for result in row['results']]} for row in tests['packages']]}
    proof['proof_digest'] = canonical_payload_digest(proof)
    return proof
