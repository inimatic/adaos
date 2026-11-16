"""Failed/aborted browser evidence must not become completed cleanup work."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from adaos.services.node_maintenance.validation import executions
from adaos.services.storage_hygiene import MANIFEST_NAME, LEASE_NAME


@pytest.mark.parametrize('outcome,status,entry_status', [(0,'passed','complete'),(7,'failed','failed'),('timeout','inconclusive','failed')])
def test_browser_runner_retains_outcome_and_releases_lease(tmp_path, monkeypatch, outcome, status, entry_status):
    source = Path(__file__).resolve().parents[1] / 'e2e/stand/qualify-node-maintenance.py'
    spec = importlib.util.spec_from_file_location('maintenance_e2e_runner', source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    output, state = tmp_path / 'run', tmp_path / 'state'
    def execute(*args, **kwargs):
        assert (output / LEASE_NAME).exists()
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(['node'], 420)
        return subprocess.CompletedProcess(['node'], outcome, stdout=b'qualification receipt', stderr=b'')
    monkeypatch.setattr(runner.subprocess, 'run', execute)
    manifest = {'run_id':'owned-run','suite':{'suite_id':'maintenance'}}
    if outcome == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired):
            runner._execute(tmp_path, output, manifest, {}, state)
    else:
        assert runner._execute(tmp_path, output, manifest, {}, state) == outcome
    assert not (output / LEASE_NAME).exists()
    assert json.loads((output / MANIFEST_NAME).read_text(encoding='utf-8'))['status'] == entry_status
    assert executions(state)[0]['status'] == status
