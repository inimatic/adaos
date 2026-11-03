import hashlib
import subprocess
from pathlib import Path

from adaos.services import skill_factory_worker as worker_module
from adaos.services.skill_factory_worker import SubprocessCodexExecutor


def test_local_reference_freezes_new_core_dependency_but_not_app_or_private_files(tmp_path, monkeypatch):
    repo = tmp_path / 'core'
    sdk = repo / 'src/adaos/sdk'
    sdk.mkdir(parents=True)
    entry = sdk / 'entry.py'
    entry.write_text('BASE = True\n', encoding='utf-8')
    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git('init')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    git('add', '-A')
    git('commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    entry.write_text('from adaos.domain.new_type import VALUE\n', encoding='utf-8')
    dependency = repo / 'src/adaos/domain/new_type.py'
    dependency.parent.mkdir()
    dependency.write_text('VALUE = 1\n', encoding='utf-8')
    private = repo / 'src/adaos/skills_templates/app/private.py'
    private.parent.mkdir(parents=True)
    private.write_text('PRIVATE = True\n', encoding='utf-8')
    monkeypatch.setattr(worker_module, '__file__', str(repo / 'src/adaos/services/skill_factory_worker.py'))
    target = tmp_path / 'reference'
    calls = []
    original_git = worker_module._git
    def bounded_git(args, **kwargs):
        calls.append(args)
        return original_git(args, **kwargs)
    monkeypatch.setattr(worker_module, '_git', bounded_git)
    entries = SubprocessCodexExecutor(repo_root=repo)._materialize_local_core_delta(target, commit)
    assert '--ignore-submodules=all' in calls[0]
    for args in calls:
        paths = args[args.index('--') + 1:]
        assert set(paths) == {f'src/adaos/{part}' for part in ('sdk', 'services', 'domain', 'adapters', 'apps', 'abi')}
    assert {row['path'] for row in entries} == {'src/adaos/sdk/entry.py', 'src/adaos/domain/new_type.py'}
    assert not (target / 'src/adaos/skills_templates').exists()
    for row in entries:
        assert hashlib.sha256((target / row['path']).read_bytes()).hexdigest() == row['sha256']
    assert (target / 'src/adaos/domain/new_type.py').read_bytes() == dependency.read_bytes()
