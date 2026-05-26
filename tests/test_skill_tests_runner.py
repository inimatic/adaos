from __future__ import annotations

import io
import subprocess

from adaos.services.skill import tests_runner as mod


def test_run_suite_does_not_write_completed_process_to_stdout(tmp_path, monkeypatch, capsys) -> None:
    suite_dir = tmp_path / "smoke"
    suite_dir.mkdir()
    (suite_dir / "test_smoke.py").write_text("print('ok')\n", encoding="utf-8")

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="test stdout\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    log = io.StringIO()
    result = mod._run_suite(
        "smoke",
        suite_dir,
        timeout=10,
        log=log,
        interpreter=None,
        env={},
        skill_name=None,
        skill_version=None,
        slot_dir=None,
    )

    captured = capsys.readouterr()
    assert result.status == "passed"
    assert captured.out == ""
    assert "test stdout" in log.getvalue()
