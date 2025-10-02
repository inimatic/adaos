"""Self-test runner for skills runtime pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence


@dataclass(slots=True, frozen=True)
class TestResult:
    name: str
    status: str
    detail: str | None = None


_TEST_TIMEOUTS = {
    "smoke": 10,
    "contract": 30,
    "e2e-dryrun": 60,
}


def run_tests(
    root: Path,
    *,
    log_path: Path,
    interpreter: Path | None = None,
    python_paths: Sequence[str] | None = None,
    skill_env_path: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    skill_name: str | None = None,
    skill_version: str | None = None,
    slot_current_dir: Path | None = None,
) -> Dict[str, TestResult]:
    results: Dict[str, TestResult] = {}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    python_paths = list(python_paths or [])
    env_template = os.environ.copy()
    if extra_env:
        env_template.update({k: str(v) for k, v in extra_env.items()})
    if skill_env_path:
        env_template["ADAOS_SKILL_ENV_PATH"] = str(skill_env_path)
    python_entries = []
    if root.exists():
        python_entries.append(str(root))
    for path in python_paths:
        if path:
            python_entries.append(path)
    existing = env_template.get("PYTHONPATH")
    if existing:
        python_entries.extend(existing.split(os.pathsep))
    if python_entries:
        env_template["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(python_entries))
    with log_path.open("w", encoding="utf-8") as log:
        for suite in ("smoke", "contract", "e2e-dryrun"):
            suite_dir = root / "runtime" / "tests" / suite
            if not suite_dir.exists():
                continue
            outcome = _run_suite(
                suite,
                suite_dir,
                timeout=_TEST_TIMEOUTS.get(suite, 30),
                log=log,
                interpreter=interpreter,
                env=env_template,
                skill_name=skill_name,
                skill_version=skill_version,
                slot_dir=slot_current_dir,
            )
            results[suite] = outcome
    return results


def _run_suite(
    name: str,
    suite_dir: Path,
    *,
    timeout: int,
    log,
    interpreter: Path | None,
    env: Mapping[str, str],
    skill_name: str | None,
    skill_version: str | None,
    slot_dir: Path | None,
) -> TestResult:
    commands = list(
        _discover_commands(
            suite_dir,
            interpreter=interpreter,
            skill_name=skill_name,
            skill_version=skill_version,
            slot_dir=slot_dir,
        )
    )
    if not commands:
        return TestResult(name=name, status="skipped", detail="no tests found")

    for command in commands:
        proc = subprocess.run(
            command,
            cwd=suite_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            env=env,
        )
        log.write(f"$ {' '.join(command)}\n")
        log.write(proc.stdout or "")
        log.write("\n")
        if proc.returncode != 0:
            return TestResult(name=name, status="failed", detail=f"command {' '.join(command)} exited {proc.returncode}")
    return TestResult(name=name, status="passed")


def _discover_commands(
    suite_dir: Path,
    *,
    interpreter: Path | None,
    skill_name: str | None,
    skill_version: str | None,
    slot_dir: Path | None,
) -> Iterable[list[str]]:
    for path in sorted(suite_dir.iterdir()):
        if path.is_dir():
            continue
        if path.suffix == ".py":
            runner = str(interpreter or sys.executable)
            if skill_name and slot_dir is not None:
                command = [
                    runner,
                    "-m",
                    "adaos.services.skill.tests_entry",
                    "--skill-name",
                    skill_name,
                    "--skill-version",
                    skill_version or "",
                    "--slot-dir",
                    str(slot_dir),
                    "--",
                    str(path),
                ]
                yield command
            else:
                yield [runner, str(path)]
        elif path.suffix in {".sh", ""} and path.stat().st_mode & 0o111:
            yield [str(path)]

