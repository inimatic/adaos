import json
import sys

from adaos import builder_worker_bootstrap as bootstrap


def test_cold_builder_imports_start_after_a_truthful_booting_handshake(tmp_path, monkeypatch):
    monkeypatch.setenv('ADAOS_BASE_DIR', str(tmp_path))
    original = sys.argv
    calls = []
    def import_worker(module, *, run_name):
        marker = json.loads((tmp_path / 'state/builder/automation_workers/session-1/ready.json').read_text())
        assert marker['status'] == 'booting'  # No execution readiness or task claim is invented.
        assert marker['session_id'] == 'session-1'
        assert marker['pid'] == bootstrap.os.getpid()
        calls.append((module, run_name, list(sys.argv)))
    monkeypatch.setattr(bootstrap.runpy, 'run_module', import_worker)
    bootstrap.main(['--session-id', 'session-1', '--recover-validated-result'])
    assert calls == [('adaos.services.builder.automation_worker', '__main__',
        ['adaos.services.builder.automation_worker', '--session-id', 'session-1', '--recover-validated-result'])]
    assert sys.argv is original
