from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

from adaos.services.skill import tests_runner as mod
from adaos.services.skill import manager as manager_mod


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


def test_run_dev_skill_tests_calls_imported_runner_alias(tmp_path, monkeypatch) -> None:
    dev_dir = tmp_path / "dev" / "sn_test"
    skill_dir = dev_dir / "skills" / "control_skill"
    skill_dir.mkdir(parents=True)
    captured: dict = {}

    class _Paths:
        def dev_skills_dir(self) -> Path:
            return dev_dir / "skills"

        def dev_dir(self) -> Path:
            return dev_dir

        def package_dir(self):
            return None

        def package_path(self) -> Path:
            return tmp_path / "src"

    class _Environment:
        def ensure_base(self) -> None:
            return None

        def data_root(self) -> Path:
            return tmp_path / "runtime-data"

    fake = SimpleNamespace(
        caps=SimpleNamespace(require=lambda *args: None),
        ctx=SimpleNamespace(paths=_Paths()),
        _load_manifest=lambda _path: {"version": "0.1.0", "runtime": {}},
        _runtime_env_dev=lambda _name: _Environment(),
    )

    def _run(skill_source, **kwargs):
        captured.update({"skill_source": skill_source, **kwargs})
        return {"pytest": mod.TestResult(name="pytest", status="passed")}

    monkeypatch.setattr(manager_mod, "run_skill_tests", _run)

    result = manager_mod.SkillManager.run_dev_skill_tests(fake, "control_skill")

    assert result["pytest"].status == "passed"
    assert captured["skill_source"] == skill_dir.resolve()
    assert captured["dev_mode"] is True


def test_runtime_fallback_pytest_uses_bounded_production_budget(
    tmp_path, monkeypatch
) -> None:
    skill_root = tmp_path / "skill"
    (skill_root / "tests").mkdir(parents=True)
    captured = []

    def fake_pytest(**kwargs):
        captured.append(kwargs["timeout"])
        return mod.TestResult(name="pytest", status="passed")

    monkeypatch.setattr(mod, "_run_pytest_suite", fake_pytest)
    monkeypatch.delenv("ADAOS_SKILL_PYTEST_TIMEOUT_SECONDS", raising=False)
    mod.run_tests(skill_root, log_path=tmp_path / "default.log")
    monkeypatch.setenv("ADAOS_SKILL_PYTEST_TIMEOUT_SECONDS", "5000")
    mod.run_tests(skill_root, log_path=tmp_path / "bounded.log")

    assert captured == [600, 900]


def test_dev_packaged_tests_use_explicit_development_budget(
    tmp_path, monkeypatch
) -> None:
    skill_root = tmp_path / "skill"
    (skill_root / "tests").mkdir(parents=True)
    captured = []

    def fake_pytest(**kwargs):
        captured.append(kwargs["timeout"])
        return mod.TestResult(name="pytest", status="passed")

    monkeypatch.setattr(mod, "_run_pytest_suite", fake_pytest)
    mod.run_tests(
        skill_root,
        log_path=tmp_path / "dev.log",
        dev_mode=True,
        timeout_seconds=180,
    )

    assert captured == [180]
