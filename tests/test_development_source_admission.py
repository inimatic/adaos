from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
import yaml

from adaos.services.development_source_admission import (
    SourceAdmissionError, assert_project_ownership, ownership_inventory, source_write_admission,
)


def test_two_creators_cannot_claim_the_same_component(tmp_path):
    root = tmp_path / 'projects'
    ready = threading.Barrier(2)

    def create(name):
        payload = {'id': name, 'components': {'owned': [{'ref': 'skill:shared'}]}}
        ready.wait()
        try:
            with source_write_admission(root):
                assert_project_ownership(root, payload)
                folder = root / name
                folder.mkdir(parents=True)
                (folder / 'project.yaml').write_text(yaml.safe_dump(payload), encoding='utf-8')
            return 'created'
        except SourceAdmissionError:
            return 'rejected'

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, ['first', 'second']))
    assert sorted(outcomes) == ['created', 'rejected']
    assert len(ownership_inventory(root)['skill:shared']) == 1


def test_ownership_scan_preserves_historical_duplicates_and_rejects_new_claim(tmp_path):
    tmp_path = tmp_path / 'projects'
    tmp_path.mkdir()
    for name in ('old', 'legacy'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'project.yaml').write_text(yaml.safe_dump({
            'id': name, 'components': {'owned': [{'ref': 'scenario:shared'}]}}), encoding='utf-8')
    assert ownership_inventory(tmp_path)['scenario:shared'] == ['legacy', 'old']
    with source_write_admission(tmp_path), pytest.raises(SourceAdmissionError, match='already owned'):
        assert_project_ownership(tmp_path, {'id': 'new', 'components': {'owned': [{'ref': 'scenario:shared'}]}})
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == ['legacy', 'old']


def test_unreadable_ownership_is_not_treated_as_unowned(tmp_path):
    folder = tmp_path / 'broken'
    folder.mkdir()
    (folder / 'project.yaml').write_text('id: broken\ncomponents: unknown\n', encoding='utf-8')
    with pytest.raises(SourceAdmissionError):
        ownership_inventory(tmp_path)
