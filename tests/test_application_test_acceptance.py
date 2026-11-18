"""Publication qualifies the complete retained Project, bound to its Candidate."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.applications import test_acceptance as acceptance
from adaos.services.applications.trial_runtime import NativeTrialRuntime
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
from adaos.services.artifact_pipeline.project_source_sync import capture_project
from adaos.services.artifact_pipeline import source_validation
from test_application_service import _release
from test_project_source_sync import source


@pytest.fixture
def candidate_basis(source, tmp_path, monkeypatch):
    workspace, _, _ = source
    (workspace / 'skills/worker/tests/test_worker.py').write_text('def test_worker():\n    assert True\n', encoding='utf-8')
    tests = workspace / 'projects/demo/tests'
    tests.mkdir()
    (tests / 'test_composition.py').write_text('def test_new_control_contract():\n    assert 2 + 2 == 4\n', encoding='utf-8')
    snapshot = capture_project(workspace, 'demo')
    release = replace(_release(project_id='demo').project_release, release_digest=None, validation_evidence_refs=(),
        validation_evidence=({'source_validation': {'status': 'passed', 'source_snapshot_digest': snapshot.digest}},)).seal()
    release_path = workspace / '.adaos/releases' / (release.release_digest.split(':')[1] + '.json')
    release_path.parent.mkdir(parents=True)
    release_path.write_text(json.dumps(release.to_dict()), encoding='utf-8')
    state = tmp_path / 'acceptance-state'
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: state), config=SimpleNamespace())
    candidate = SimpleNamespace(candidate_id='frozen-candidate', release_digest=release.release_digest,
        project_id='demo', package_digest=release.components[0].digest)
    verified = []
    runtime = SimpleNamespace(root=workspace, project_id='demo', packages=release.components,
        verified_source=lambda package: verified.append(package.digest))
    monkeypatch.setattr(NativeTrialRuntime, 'resolve', lambda *args: runtime)
    monkeypatch.setattr(acceptance, 'source_policy', lambda _: {'branch': 'main', 'revision': 1, 'confirmed': True})
    monkeypatch.setattr(source_validation, 'validation_environment', lambda: {'environment': 'qualified-local-core'})
    activations = TrialActivationStore(state / 'artifact_pipeline/trial-activations')
    activations.save({'schema': 'adaos.trial.activation.v1', 'status': 'active', 'data_mode': 'empty',
        'candidate_ref': {'candidate_id': candidate.candidate_id, 'release_digest': release.release_digest},
        'runtime_binding': {'authority': 'immutable_candidate', 'kind': 'isolated_trial_workspace',
            'path': str(workspace), 'workspace_lock_digest': 'sha256:' + 'c' * 64},
        'package_refs': [package.to_dict() for package in release.components]})
    return owner, candidate, workspace, state, verified


def test_publication_executes_project_and_owned_component_suites(candidate_basis):
    owner, candidate, _, state, verified = candidate_basis
    receipt = acceptance.qualify_candidate_tests(owner, candidate)
    assert receipt['status'] == 'passed'
    assert receipt['test_identity']['test_plan']['suites'] == ['project:demo', 'skill:worker']
    assert verified == [candidate.package_digest, candidate.package_digest]
    stored = json.loads((state / 'artifact_pipeline/test-acceptance/frozen-candidate.json').read_text())
    assert stored == receipt
    assert acceptance.require_candidate_test_acceptance(owner, candidate) == receipt


def test_tests_added_or_changed_after_candidate_qualification_cannot_publish(candidate_basis):
    owner, candidate, workspace, state, _ = candidate_basis
    (workspace / 'projects/demo/tests/test_composition.py').write_text('def test_changed():\n    assert True\n')
    with pytest.raises(ValueError, match='whole-Project source snapshot'):
        acceptance.qualify_candidate_tests(owner, candidate)
    assert not (state / 'artifact_pipeline/test-acceptance/frozen-candidate.json').exists()


def test_source_change_during_a_passing_run_does_not_create_acceptance(candidate_basis, monkeypatch):
    owner, candidate, workspace, state, _ = candidate_basis
    validate = acceptance.validate_restored_project
    def changed_after_tests(**kwargs):
        result = validate(**kwargs)
        (workspace / 'skills/worker/skill.yaml').write_text('name: worker\nversion: 2.0.0\n')
        return result
    monkeypatch.setattr(acceptance, 'validate_restored_project', changed_after_tests)
    with pytest.raises(ValueError, match='changed during test qualification'):
        acceptance.qualify_candidate_tests(owner, candidate)
    assert not (state / 'artifact_pipeline/test-acceptance/frozen-candidate.json').exists()
