import json

import pytest

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline import source_validation as validation
from adaos.services.artifact_pipeline.project_source_sync import capture_project
from adaos.services.skill.tests_runner import TestResult as Result
from test_project_source_sync import source


@pytest.mark.parametrize('asset', ['abi/contract.json', 'templates/form.yaml', 'prompts/task.md',
                                  'sql/schema.sql', 'services/engine.py'])
def test_core_test_identity_covers_runtime_assets_and_their_addition_or_removal(tmp_path, asset):
    core = tmp_path / 'core'
    core.mkdir()
    (core / '__init__.py').write_text('# stable code\n', encoding='utf-8')
    before = validation._core_input_digest(core)
    path = core / asset
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('first runtime input', encoding='utf-8')
    added = validation._core_input_digest(core)
    assert added != before
    path.write_text('changed runtime input', encoding='utf-8')
    assert validation._core_input_digest(core) not in {before, added}
    path.unlink()
    assert validation._core_input_digest(core) == before


def test_core_identity_does_not_traverse_dependencies_or_runtime_caches(tmp_path):
    (tmp_path / 'engine.py').write_text('# qualified\n', encoding='utf-8')
    before = validation._core_input_digest(tmp_path)
    for name in ('integrations', 'node_modules', '__pycache__', '.git'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'foreign.json').write_text('not a Core package input', encoding='utf-8')
    (tmp_path / 'engine.pyc').write_bytes(b'compiled cache')
    assert validation._core_input_digest(tmp_path) == before


def test_editable_core_build_configuration_invalidates_environment_identity(tmp_path, monkeypatch):
    core = tmp_path / 'src/adaos'
    module = core / 'services/artifact_pipeline/source_validation.py'
    module.parent.mkdir(parents=True)
    module.write_text('# same runtime code', encoding='utf-8')
    (tmp_path / 'pyproject.toml').write_text('version = "1.0.0"', encoding='utf-8')
    lock = tmp_path / 'uv.lock'
    lock.write_text('version = 1', encoding='utf-8')
    monkeypatch.setattr(validation, '__file__', str(module))
    monkeypatch.setattr(validation.metadata, 'distributions', lambda: [])
    before = validation.validation_environment()
    lock.write_text('version = 2', encoding='utf-8')
    after = validation.validation_environment()
    assert before['core_digest'] == after['core_digest']
    assert before['core_build_configuration_digest'] != after['core_build_configuration_digest']


def test_repaired_aggregate_cannot_pass_if_environment_changes_while_reusing_packages(source, tmp_path, monkeypatch):
    workspace, _, _ = source
    monkeypatch.setattr(validation, 'validation_environment', lambda: {'fixture': 'same'})
    calls = []
    def run(root, *, log_path, **kwargs):
        calls.append(root.name)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text('verified\n')
        return {'suite': Result('suite', 'passed')}
    monkeypatch.setattr(validation, 'run_tests', run)
    args = dict(workspace=workspace, project_id='demo', snapshot_digest=capture_project(workspace, 'demo').digest,
        state_root=tmp_path / 'validation')
    receipt = validation.validate_restored_project(**args)
    directory = args['state_root'] / 'source-validation' / canonical_payload_digest(receipt['identity']).removeprefix('sha256:')
    (directory / 'receipt.json').write_text('{interrupted')
    environments = iter([{'fixture': 'same'}, {'fixture': 'changed'}])
    monkeypatch.setattr(validation, 'validation_environment', lambda: next(environments))
    calls.clear()
    with pytest.raises(validation.SourceValidationUnavailable, match='aggregate not reusable'):
        validation.validate_restored_project(**args)
    assert not calls


@pytest.mark.parametrize('fault',['missing_package','failed_result','path_escape','damaged_json','source_changed'])
def test_invalid_whole_receipt_cannot_authorize_reuse_but_valid_packages_survive(source,tmp_path,monkeypatch,fault):
    workspace,_,_ = source
    tests = workspace/'projects/demo/tests'
    tests.mkdir()
    (tests/'test_composition.py').write_text('def test_ok(): pass\n')
    monkeypatch.setattr(validation,'validation_environment',lambda:{'fixture':'same-environment'})
    calls=[]
    def run(root,*,log_path,**kwargs):
        calls.append(root.name)
        log_path.parent.mkdir(parents=True,exist_ok=True)
        log_path.write_text('verified tests\n')
        return {'suite':Result('suite','passed')}
    monkeypatch.setattr(validation,'run_tests',run)
    args = dict(workspace=workspace,project_id='demo',snapshot_digest=capture_project(workspace,'demo').digest,
                state_root=tmp_path/'validation')
    receipt=validation.validate_restored_project(**args)
    directory=args['state_root']/'source-validation'/canonical_payload_digest(receipt['identity']).removeprefix('sha256:')
    path=directory/'receipt.json'
    calls.clear()
    if fault == 'damaged_json': path.write_text('{interrupted')
    else:
        if fault == 'missing_package': receipt['packages'].pop()
        elif fault == 'failed_result': receipt['packages'][0]['results'][0]['status']='failed'
        elif fault == 'path_escape': receipt['packages'][0]['log']='../../foreign.log'
        else: receipt['source_unchanged']=False
        receipt['receipt_digest']=canonical_payload_digest({k:v for k,v in receipt.items() if k != 'receipt_digest'})
        path.write_text(json.dumps(receipt))
    restored=validation.validate_restored_project(**args)
    assert restored['reused'] is False
    assert [row['ref'] for row in restored['packages']] == ['project:demo','skill:worker']
    assert all(row['results'][0]['status']=='passed' for row in restored['packages'])
    assert calls == []  # Exact per-package evidence repairs the aggregate autonomously.
    assert validation.validate_restored_project(**args)['reused'] is True


def test_failed_package_record_is_rerun_while_unchanged_package_is_reused(source,tmp_path,monkeypatch):
    workspace,_,_ = source
    monkeypatch.setattr(validation,'validation_environment',lambda:{'fixture':'same'})
    calls=[]
    def run(root,*,log_path,**kwargs):
        calls.append(root.name)
        log_path.parent.mkdir(parents=True,exist_ok=True)
        log_path.write_text('verified\n')
        return {'suite':Result('suite','passed')}
    monkeypatch.setattr(validation,'run_tests',run)
    args=dict(workspace=workspace,project_id='demo',snapshot_digest=capture_project(workspace,'demo').digest,
              state_root=tmp_path/'validation')
    receipt=validation.validate_restored_project(**args)
    directory=args['state_root']/'source-validation'/canonical_payload_digest(receipt['identity']).removeprefix('sha256:')
    row=json.loads((directory/'skill-worker.json').read_text())
    row['results'][0]['status']='failed'
    row['receipt_digest']=canonical_payload_digest({k:v for k,v in row.items() if k != 'receipt_digest'})
    (directory/'skill-worker.json').write_text(json.dumps(row))
    receipt['packages']=[row]
    receipt['receipt_digest']=canonical_payload_digest({k:v for k,v in receipt.items() if k != 'receipt_digest'})
    (directory/'receipt.json').write_text(json.dumps(receipt))
    assert validation.validate_restored_project(**args)['status']=='passed'
    assert calls == ['worker','worker']
