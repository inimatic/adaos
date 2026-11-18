import json
import sys
import pytest

from adaos import builder_worker_bootstrap as bootstrap
from adaos.services.builder.worker_identity import worker_core_identity


def test_cold_builder_imports_start_after_a_truthful_booting_handshake(tmp_path, monkeypatch):
    monkeypatch.setenv('ADAOS_BASE_DIR', str(tmp_path))
    monkeypatch.setenv('ADAOS_BUILDER_WORKER_CORE_IDENTITY', json.dumps(worker_core_identity()))
    original = sys.argv
    calls = []
    def import_worker(module, *, run_name):
        marker = json.loads((tmp_path / 'state/builder/automation_workers/session-1/ready.json').read_text())
        assert marker['status'] == 'booting'  # No execution readiness or task claim is invented.
        assert marker['session_id'] == 'session-1'
        assert marker['pid'] == bootstrap.os.getpid()
        assert marker['core_identity'] == worker_core_identity()
        assert marker['runtime_identity']['python_executable'] == sys.executable
        calls.append((module, run_name, list(sys.argv)))
    monkeypatch.setattr(bootstrap.runpy, 'run_module', import_worker)
    bootstrap.main(['--session-id', 'session-1', '--recover-validated-result'])
    assert calls == [('adaos.services.builder.automation_worker', '__main__',
        ['adaos.services.builder.automation_worker', '--session-id', 'session-1', '--recover-validated-result'])]
    assert sys.argv is original


def test_bootstrap_rejects_a_different_core_before_emitting_liveness(tmp_path, monkeypatch):
    monkeypatch.setenv('ADAOS_BASE_DIR', str(tmp_path))
    monkeypatch.setenv('ADAOS_BUILDER_WORKER_CORE_IDENTITY', json.dumps({'source_root': 'other-core'}))
    monkeypatch.setattr(bootstrap.runpy, 'run_module', lambda *args, **kwargs: pytest.fail('must not import the worker'))
    with pytest.raises(RuntimeError, match='Core identity mismatch'):
        bootstrap.main(['--session-id', 'session-1'])
    assert not (tmp_path / 'state/builder/automation_workers/session-1/ready.json').exists()
