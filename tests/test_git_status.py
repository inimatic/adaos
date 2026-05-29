from __future__ import annotations

import subprocess
from pathlib import Path

from adaos.apps.cli import git_status


def test_run_git_reports_timeout_without_raising(monkeypatch) -> None:
    def _timeout(cmd, *_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd, 10.0, output="partial")

    monkeypatch.setattr(subprocess, "run", _timeout)

    proc = git_status._run_git(Path("."), ["status"], timeout_s=10.0)

    assert proc.returncode == 124
    assert proc.stdout == "partial"
    assert "timed out" in proc.stderr
