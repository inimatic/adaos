"""Scratch evidence cannot replace or poison a governed source continuation."""
import subprocess
from adaos.services.skill_factory_sources import source_tree_digest
from adaos.services.builder.automation import (
    _preserved_candidate_changed_paths, _preserved_candidate_has_changes,
    _preserved_candidate_is_clean,
)


def test_scratch_only_is_clean_and_real_candidate_survives_without_gitignore(tmp_path):
    run=tmp_path/'run';workspace=run/'workspace';workspace.mkdir(parents=True)
    def git(*args):
        return subprocess.run(['git',*args],cwd=workspace,check=True,capture_output=True)
    git('init');git('config','user.name','test');git('config','user.email','test@example.invalid')
    target=workspace/'implementation.py';target.write_text('original\n')
    git('add','-A');git('commit','-m','baseline')
    digest=source_tree_digest(workspace)
    scratch=workspace/'.tmp'/'native-review';scratch.mkdir(parents=True)
    (scratch/'retouch.sqlite3').write_bytes(b'private diagnostic')
    cache=workspace/'handlers'/'__pycache__';cache.mkdir(parents=True)
    (cache/'layers.cpython-311.pyc').write_bytes(b'generated cache')
    assert source_tree_digest(workspace)==digest
    assert _preserved_candidate_changed_paths(run)==[]
    assert not _preserved_candidate_has_changes(run)
    assert _preserved_candidate_is_clean(run)
    target.write_text('candidate\n')
    new=workspace/'layers.py';new.write_text('new source\n')
    assert source_tree_digest(workspace)!=digest
    assert _preserved_candidate_changed_paths(run)==['implementation.py','layers.py']
    assert _preserved_candidate_has_changes(run)
    assert not _preserved_candidate_is_clean(run)
