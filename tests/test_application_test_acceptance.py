"""Publication qualifies the complete retained Project, bound to its Candidate."""
from dataclasses import replace
import json
import shutil
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


def test_portable_catalog_proof_keeps_complete_results_without_private_logs(candidate_basis, tmp_path):
    from adaos.services.workspace_catalog_validation import retain_test_proof, validate_test_proof
    owner, candidate, workspace, state, _ = candidate_basis
    release_path = workspace / '.adaos/releases' / (candidate.release_digest.split(':')[1]+'.json')
    release = acceptance.ProjectRelease.from_mapping(json.loads(release_path.read_text()))
    proof = acceptance.export_candidate_test_proof(owner,candidate,release)
    assert proof['test_plan']['suites'] == ['project:demo','skill:worker']
    assert all(row['statuses'] and set(row['statuses']) == {'passed'} for row in proof['packages'])
    serialized = json.dumps(proof)
    assert str(state) not in serialized and 'interpreter' not in serialized and 'stdout' not in serialized
    root = tmp_path/'catalog'
    path = retain_test_proof(root,proof)
    assert validate_test_proof(root,project_id='demo',version=release.version,
        release_digest=release.release_digest,package_digest=candidate.package_digest) == proof
    unchanged = (root/path).read_bytes()
    assert retain_test_proof(root,proof) == path and (root/path).read_bytes() == unchanged


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


@pytest.fixture
def component_only_trial(candidate_basis, monkeypatch):
    """Real Trials contain runtime packages; whole-Project tests live in source custody."""
    owner, candidate, workspace, state, verified = candidate_basis
    source = capture_project(workspace, 'demo')
    release_file = workspace / '.adaos/releases' / (candidate.release_digest.split(':')[1] + '.json')
    release = acceptance.ProjectRelease.from_mapping(json.loads(release_file.read_text()))
    custody = dict(repository=release.source_ref.repository, commit=release.source_ref.revision,
        tree='a' * 40, snapshot_digest=source.digest, retention_ref='refs/heads/source-receipts/exact',
        operation_id='original-checkpoint')
    release = replace(release, release_digest=None, validation_evidence_refs=(), validation_evidence=({
        'source_validation': {'status': 'passed', 'source_snapshot_digest': source.digest},
        'source_custody': custody},)).seal()
    candidate.release_digest = release.release_digest
    candidate.change_ids = ('original-change',)
    (release_file.parent / (release.release_digest.split(':')[1] + '.json')).write_text(json.dumps(release.to_dict()))
    activations = TrialActivationStore(state / 'artifact_pipeline/trial-activations')
    record = activations.load(candidate.candidate_id)
    candidate.candidate_id = 'checkpoint-candidate'
    record['candidate_ref']['candidate_id'] = candidate.candidate_id
    record['candidate_ref']['release_digest'] = candidate.release_digest
    activations.save(record)
    shutil.rmtree(workspace / 'projects/demo')
    shutil.rmtree(workspace / 'skills/worker/tests')

    receipt = dict(custody)
    calls = []
    class Sync:
        def _scope(self, project_id, change_id):
            assert (project_id, change_id) == ('demo', 'original-change')
            return {}
        def _paths(self, scope, operation_id):
            assert operation_id == 'original-checkpoint'
            return state / 'intent.json', state / 'source.zip'
        def _read_json(self, path):
            return {'state': 'remote_confirmed', 'receipt': receipt}
        def restore(self, project_id, change_id, *, operation_id, destination):
            calls.append(destination)
            source.materialize(destination)
            return {'commit': custody['commit'], 'snapshot_digest': source.digest}
    monkeypatch.setattr('adaos.services.root.service.RootDeveloperService',
        lambda **kwargs: SimpleNamespace(project_source_sync=lambda: Sync()))
    return owner, candidate, workspace, state, receipt, calls


def test_publication_restores_exact_whole_project_without_changing_component_only_trial(component_only_trial):
    owner, candidate, runtime, _, _, calls = component_only_trial
    result = acceptance.qualify_candidate_tests(owner, candidate)
    assert result['status'] == 'passed'
    assert result['test_identity']['test_plan']['suites'] == ['project:demo', 'skill:worker']
    assert len(calls) == 1 and not calls[0].exists()
    assert not (runtime / 'projects/demo').exists()
    assert not (runtime / 'skills/worker/tests').exists()


@pytest.mark.parametrize('field', ['repository', 'commit', 'tree', 'snapshot_digest', 'retention_ref', 'operation_id'])
def test_source_receipt_from_another_checkpoint_cannot_qualify_publication(component_only_trial, field):
    owner, candidate, _, state, receipt, calls = component_only_trial
    receipt[field] = 'another-checkpoint'
    with pytest.raises(ValueError, match='no matching confirmed'):
        acceptance.qualify_candidate_tests(owner, candidate)
    assert not calls
    assert not (state / 'artifact_pipeline/test-acceptance/checkpoint-candidate.json').exists()


def test_component_only_trial_without_source_custody_cannot_publish(candidate_basis):
    owner, candidate, workspace, _, _ = candidate_basis
    shutil.rmtree(workspace / 'projects/demo')
    with pytest.raises(ValueError, match='whole-Project test source'):
        acceptance.qualify_candidate_tests(owner, candidate)


def test_changed_environment_requalifies_the_exact_candidate(candidate_basis, monkeypatch):
    owner, candidate, *_ = candidate_basis
    first = acceptance.qualify_candidate_tests(owner, candidate)
    monkeypatch.setattr(source_validation, 'validation_environment',
                        lambda: {'environment': 'different-core-or-dependencies'})
    current = acceptance.require_candidate_test_acceptance(owner, candidate)
    assert current['test_identity']['environment'] != first['test_identity']['environment']
    assert current['release_digest'] == first['release_digest']


def test_changed_trial_lock_invalidates_old_test_acceptance(candidate_basis):
    owner, candidate, _, state, _ = candidate_basis
    first = acceptance.qualify_candidate_tests(owner, candidate)
    activations = TrialActivationStore(state / 'artifact_pipeline/trial-activations')
    activation = activations.load(candidate.candidate_id)
    activation['runtime_binding']['workspace_lock_digest'] = 'sha256:' + 'e' * 64
    activations.save(activation)
    current = acceptance.require_candidate_test_acceptance(owner, candidate)
    assert current['workspace_lock_digest'] != first['workspace_lock_digest']


@pytest.mark.parametrize('fault', ['incomplete_plan', 'missing_package', 'failed_result'])
def test_top_level_pass_cannot_hide_incomplete_test_execution(candidate_basis, monkeypatch, fault):
    owner, candidate, _, state, _ = candidate_basis
    binding = acceptance.qualify_candidate_tests(owner, candidate)
    original_identity = binding['test_identity']
    old_directory = state / 'artifact_pipeline/source-validation' / acceptance.canonical_payload_digest(original_identity)[7:]
    tests = json.loads((old_directory / 'receipt.json').read_text())
    if fault == 'incomplete_plan':
        binding['test_identity']['test_plan']['complete_suite'] = False
        tests['identity'] = binding['test_identity']
    elif fault == 'missing_package':
        tests['packages'].pop()
    else:
        tests['packages'][0]['results'][0]['status'] = 'failed'
    directory = state / 'artifact_pipeline/source-validation' / acceptance.canonical_payload_digest(binding['test_identity'])[7:]
    directory.mkdir(exist_ok=True)
    tests['receipt_digest'] = acceptance.canonical_payload_digest({k:v for k,v in tests.items() if k != 'receipt_digest'})
    (directory / 'receipt.json').write_text(json.dumps(tests))
    binding['test_receipt_digest'] = tests['receipt_digest']
    binding['receipt_digest'] = acceptance.canonical_payload_digest({k:v for k,v in binding.items() if k != 'receipt_digest'})
    (state / 'artifact_pipeline/test-acceptance/frozen-candidate.json').write_text(json.dumps(binding))
    calls = []
    monkeypatch.setattr(acceptance, 'qualify_candidate_tests', lambda *_: calls.append('requalified') or {})
    acceptance.require_candidate_test_acceptance(owner, candidate)
    assert calls == ['requalified']
