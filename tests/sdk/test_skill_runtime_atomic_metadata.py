import json
from concurrent.futures import ThreadPoolExecutor

from adaos.services.artifact_pipeline import storage
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


def test_metadata_write_retries_only_the_filesystem_switch(tmp_path, monkeypatch):
    path = tmp_path / 'meta.json'
    path.write_text('{"version":"old"}', encoding='utf8')
    original = storage._replace_once
    attempts = []
    def sharing_then_replace(source, target):
        attempts.append(source)
        if len(attempts) == 1:
            raise PermissionError('Windows sharing conflict')
        original(source, target)
    monkeypatch.setattr(storage, '_replace_once', sharing_then_replace)
    monkeypatch.setattr(storage.time, 'sleep', lambda _: None)
    SkillRuntimeEnvironment._write_metadata_path(path, {'version': 'new'})
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert json.loads(path.read_text('utf8')) == {'version': 'new'}
    assert [item for item in tmp_path.iterdir() if item.is_file()] == [path]


def test_concurrent_metadata_writers_never_share_temporary_files(tmp_path):
    path = tmp_path / 'meta.json'
    payloads = [{'writer': n, 'version': str(n), 'history': [n] * 100} for n in range(32)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda payload: SkillRuntimeEnvironment._write_metadata_path(path, payload), payloads))
    assert json.loads(path.read_text('utf8')) in payloads
    assert [item for item in tmp_path.iterdir() if item.is_file()] == [path]
