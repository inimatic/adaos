import subprocess
from adaos.services.builder.automation import _preserved_candidate_has_changes, _preserved_candidate_changed_paths


def test_recovery_commit_does_not_erase_candidate_relative_to_materialization(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    def git(*args):
        subprocess.run(['git',*args],cwd=workspace,check=True,capture_output=True)
    git('init');git('config','user.name','Test');git('config','user.email','test@localhost')
    source=workspace/'main.py';source.write_text('baseline\n',encoding='utf-8')
    git('add','main.py');git('commit','-m','materialize')
    assert not _preserved_candidate_has_changes(tmp_path)
    source.write_text('implemented\n',encoding='utf-8')
    git('add','main.py');git('commit','-m','validated result before activation failure')
    assert _preserved_candidate_has_changes(tmp_path)
    assert _preserved_candidate_changed_paths(tmp_path)==['main.py']
