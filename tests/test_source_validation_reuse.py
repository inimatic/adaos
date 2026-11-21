import json

import pytest

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline import source_validation as validation
from adaos.services.artifact_pipeline.project_source_sync import capture_project
from adaos.services.skill.tests_runner import TestResult as Result
from test_project_source_sync import source


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
