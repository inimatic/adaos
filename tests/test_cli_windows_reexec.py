"""A checkpoint caller must receive its child's exact arguments and outcome."""
import subprocess
import sys

import pytest

from adaos.apps.cli import app


def test_windows_reexec_preserves_quoted_arguments_output_and_failure(monkeypatch, tmp_path, capfd):
    import json
    child = tmp_path / 'child.py'
    child.write_text('import json,sys; print(json.dumps(sys.argv[1:])); print("receipt diagnostic",file=sys.stderr); sys.exit(7)', encoding='utf-8')
    original = subprocess.run
    def run(args, **kwargs):
        assert args[1:3] == ['-m', 'adaos']
        return original([args[0], str(child), *args[3:]], **kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('ADAOS_CLI_REEXECED', '0')
    monkeypatch.setenv('ADAOS_CLI_REEXEC_REASON', '')
    monkeypatch.setattr(sys, 'argv', ['adaos.exe', '--message', 'two words "quoted"', 'a & b'])
    with pytest.raises(SystemExit) as stopped:
        app._reexec_preferred_python('test', python=sys.executable)
    assert stopped.value.code == 7
    output = capfd.readouterr()
    assert json.loads(output.out) == ['--message', 'two words "quoted"', 'a & b']
    assert 'receipt diagnostic' in output.err
