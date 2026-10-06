import json
import os

import pytest

from adaos.services import workspace_registry as registry


@pytest.fixture(autouse=True)
def clear_snapshots():
    registry._REGISTRY_SNAPSHOTS.clear()
    yield
    registry._REGISTRY_SNAPSHOTS.clear()


def write_registry(root, *, version=2, title='One'):
    payload = {'version': version, 'scenarios': [{
        'kind': 'scenario', 'name': 'sample', 'id': 'sample', 'version': '1.0.0',
        'install': {'kind': 'scenario', 'name': 'sample', 'id': 'sample'},
        'catalog': {'title': title},
    }]}
    (root / 'registry.json').write_text(json.dumps(payload), encoding='utf-8')


def test_exact_lookup_reuses_normalization_without_leaking_mutable_values(tmp_path, monkeypatch):
    write_registry(tmp_path)
    calls = []
    original = registry._normalize_registry_payload
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(registry, '_normalize_registry_payload', tracked)
    first = registry.find_workspace_registry_entry(tmp_path, kind='scenarios', name_or_id='sample')
    first['install']['id'] = 'corrupt'
    whole = registry.load_workspace_registry(tmp_path)
    whole['scenarios'][0]['install']['id'] = 'corrupt'
    listed = registry.list_workspace_registry_entries(tmp_path, kind='scenarios')
    listed[0]['install']['id'] = 'corrupt'
    last = registry.find_workspace_registry_entry(tmp_path, kind='scenarios', name_or_id='sample')
    assert last['install']['id'] == 'sample'
    assert len(calls) == 1


def test_same_mtime_same_size_edit_and_invalid_content_are_never_served_stale(tmp_path):
    write_registry(tmp_path)
    before = registry.load_workspace_registry(tmp_path)
    path = tmp_path / 'registry.json'
    stat = path.stat()
    write_registry(tmp_path, title='Two')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    after = registry.load_workspace_registry(tmp_path)
    assert before != after
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(registry.WorkspaceRegistryError):
        registry.load_workspace_registry(tmp_path)


def test_cache_has_entry_and_input_size_limits_and_excludes_filesystem_dependent_v1(tmp_path, monkeypatch):
    for i in range(10):
        write_registry(tmp_path, title=str(i))
        registry.load_workspace_registry(tmp_path)
    assert len(registry._REGISTRY_SNAPSHOTS) == registry._REGISTRY_SNAPSHOT_MAX_ENTRIES
    registry._REGISTRY_SNAPSHOTS.clear()
    monkeypatch.setattr(registry, '_REGISTRY_SNAPSHOT_MAX_BYTES', 1)
    registry.load_workspace_registry(tmp_path)
    assert not registry._REGISTRY_SNAPSHOTS
    monkeypatch.setattr(registry, '_REGISTRY_SNAPSHOT_MAX_BYTES', 512 * 1024)
    write_registry(tmp_path, version=1)
    registry.load_workspace_registry(tmp_path)
    assert not registry._REGISTRY_SNAPSHOTS
