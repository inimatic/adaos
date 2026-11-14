import importlib.util
import json
from pathlib import Path
import subprocess


def test_source_identity_accounts_for_candidate_overlay_and_ignores_unrelated_dirty_work(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('bootstrap_builder_metadata', Path(__file__).resolve().parents[1] / 'tools/build_bootstrap_release.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    checkout = tmp_path / 'producer'
    source = checkout / 'src/adaos'
    source.mkdir(parents=True)
    (source / 'owned.py').write_text('admitted = 1\n')
    (source / 'other.py').write_text('unrelated = 1\n')
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    subprocess.run(['git', '-C', str(checkout), 'add', 'src'], check=True)
    subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=Metadata Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'source fixture'], check=True)
    monkeypatch.setattr(builder, 'ROOT', checkout)
    monkeypatch.setattr(builder, 'OVERLAY', ('src/adaos/owned.py',))

    def snapshot(name):
        target = tmp_path / name
        revision = builder.snapshot(target, candidate=True)
        record = json.loads((target / 'bootstrap-source.json').read_text())
        assert record['revision'] == revision
        assert record['candidate'] is True
        assert len(record['sha256']) == 64
        return record

    first = snapshot('first')
    (source / 'other.py').write_text('unrelated = 2\n')
    assert snapshot('unrelated-change') == first
    (source / 'owned.py').write_text('admitted = 2\n')
    changed = snapshot('admitted-change')
    assert changed['revision'] == first['revision']
    assert changed['sha256'] != first['sha256']
