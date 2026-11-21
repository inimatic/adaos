import json
import subprocess
import sys
import time

import psutil
import pytest

from adaos.adapters.git.process import run_bounded_git


def test_background_git_cannot_open_interactive_credential_prompts(monkeypatch,tmp_path):
    monkeypatch.setenv('GIT_TERMINAL_PROMPT','1')
    monkeypatch.setenv('GCM_INTERACTIVE','Always')
    code="import os,json; print(json.dumps({k:os.environ[k] for k in ('GIT_TERMINAL_PROMPT','GCM_INTERACTIVE','GCM_GUI_PROMPT')}))"
    result=run_bounded_git([sys.executable,'-c',code],cwd=tmp_path,timeout=5)
    assert result.returncode==0
    assert json.loads(result.stdout)=={'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never','GCM_GUI_PROMPT':'0'}


def test_timeout_stops_transport_grandchildren_that_inherit_captured_pipes(tmp_path):
    marker=tmp_path/'child-pid.txt'
    code=("import subprocess,sys,time,pathlib; "
          "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
          "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)")
    started=time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded_git([sys.executable,'-c',code,str(marker)],cwd=tmp_path,timeout=2)
    assert time.monotonic()-started<9
    pid=int(marker.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_git_timeout_has_no_blind_retry(monkeypatch,tmp_path):
    from adaos.adapters.git import cli_git
    calls=[]
    def timeout(command,**kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command,1)
    monkeypatch.setattr(cli_git,'run_bounded_git',timeout)
    with pytest.raises(cli_git.GitError,match='timed out'):
        cli_git._run_git(['push','origin','main'],cwd=tmp_path)
    assert len(calls)==1
