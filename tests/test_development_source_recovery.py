from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.development_source_admission import SourceAdmissionError, ownership_inventory
from adaos.services.development_source_recovery import retire_legacy_project
from test_project_source_sync import source


@pytest.fixture
def duplicate(source, tmp_path):
    workspace, remote, sync = source
    legacy = workspace / 'projects/legacy'
    legacy.mkdir()
    definition = yaml.safe_load((workspace / 'projects/demo/project.yaml').read_text(encoding='utf-8'))
    definition['id'] = 'legacy'
    (legacy / 'project.yaml').write_text(yaml.safe_dump(definition), encoding='utf-8')
    (legacy / 'notes.md').write_text('retain this historical note', encoding='utf-8')
    store = SimpleNamespace(list_applications=lambda: [SimpleNamespace(application_id='existing', legacy_project_id='demo')])
    args = dict(sync=sync(), applications=store, canonical_project_id='demo',
                legacy_project_id='legacy', recovery_root=tmp_path / 'recovery')
    return workspace, remote, args


def test_retire_unbound_subset_retains_remote_source_tests_and_original_declaration(duplicate):
    workspace, remote, args = duplicate
    result = retire_legacy_project(**args)
    assert result['status'] == 'completed'
    assert not (workspace / 'projects/legacy').exists()
    assert (Path(result['destination']) / 'notes.md').read_text() == 'retain this historical note'
    assert ownership_inventory(workspace / 'projects')['skill:worker'] == ['demo']
    destination = args['recovery_root'] / 'clean-restore'
    args['sync'].restore('legacy', 'legacy-ownership-recovery', operation_id=result['operation_id'], destination=destination)
    assert (destination / 'skills/worker/tests/test_worker.py').read_text() == 'assert True\n'
    assert (workspace / 'skills/worker/tests/test_worker.py').read_text() == 'assert True\n'
    assert retire_legacy_project(**args)['source_receipt'] == result['source_receipt']
    assert remote.writes == 1


def test_retirement_lost_reply_keeps_both_declarations_until_remote_proof(duplicate):
    workspace, remote, args = duplicate
    def lost():
        raise TimeoutError('lost reply after push')
    remote.after_write = lost
    with pytest.raises(TimeoutError):
        retire_legacy_project(**args)
    assert sorted(ownership_inventory(workspace / 'projects')['skill:worker']) == ['demo', 'legacy']
    assert retire_legacy_project(**args)['status'] == 'completed'
    assert remote.writes == 1


def test_retirement_preserves_later_legacy_work_after_lost_reply(duplicate):
    workspace, remote, args = duplicate
    def lost():
        (workspace / 'projects/legacy/notes.md').write_text('new user work', encoding='utf-8')
        raise TimeoutError('lost reply after push')
    remote.after_write = lost
    with pytest.raises(TimeoutError):
        retire_legacy_project(**args)
    with pytest.raises(SourceAdmissionError, match='newer work'):
        retire_legacy_project(**args)
    assert (workspace / 'projects/legacy/notes.md').read_text() == 'new user work'
    assert remote.writes == 1


def test_retirement_refuses_two_real_applications_before_source_write(duplicate):
    workspace, remote, args = duplicate
    args['applications'].list_applications = lambda: [
        SimpleNamespace(application_id='existing', legacy_project_id='demo'),
        SimpleNamespace(application_id='other', legacy_project_id='legacy')]
    with pytest.raises(SourceAdmissionError, match='Application authority'):
        retire_legacy_project(**args)
    assert (workspace / 'projects/legacy/project.yaml').is_file()
    assert remote.writes == 0


def test_retirement_refuses_distinct_product_closure_before_source_write(duplicate):
    workspace, remote, args = duplicate
    path = workspace / 'projects/legacy/project.yaml'
    definition = yaml.safe_load(path.read_text(encoding='utf-8'))
    definition['components']['owned'].append({'ref': 'skill:another'})
    path.write_text(yaml.safe_dump(definition), encoding='utf-8')
    with pytest.raises(SourceAdmissionError, match='wholly contained'):
        retire_legacy_project(**args)
    assert remote.writes == 0
    assert path.is_file()
