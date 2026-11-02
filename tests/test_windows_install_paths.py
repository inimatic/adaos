"""Run the installer's path preflight without downloads or installation writes."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer preflight")
INSTALLER = Path(__file__).resolve().parents[1] / "tools/init/windows/init.ps1"


def _preflight(destination: Path, runtime_base: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    env = dict(os.environ, ADAOS_BASE_DIR=str(runtime_base))
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(INSTALLER), "-CheckPathsOnly", "-Dest", str(destination)],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_path_preflight_accepts_source_without_creating_runtime_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    runtime_base = tmp_path / "runtime"

    result = _preflight(source, runtime_base)

    assert result.returncode == 0, result.stderr
    assert "preflight passed" in result.stdout
    assert not runtime_base.exists()
    assert list(source.iterdir()) == [source / "pyproject.toml"]


def test_path_preflight_rejects_long_destination_before_creating_it(tmp_path: Path) -> None:
    destination = tmp_path / ("a" * 85) / ("b" * 85) / ("c" * 85)

    result = _preflight(destination, tmp_path / "runtime")

    assert result.returncode != 0
    assert "installation directory is too long" in result.stderr
    assert "shorter -Dest or ADAOS_BASE_DIR" in result.stderr
    assert not destination.exists()


def test_path_preflight_counts_bundled_name_and_future_candidate_layout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ("module_" + "m" * 85 + ".py")).write_text("# bundled\n", encoding="utf-8")
    drive = Path(tmp_path.anchor)
    runtime_base = drive / ("runtime_" + "r" * 75) / ("state_" + "s" * 75)

    result = _preflight(source, runtime_base)

    assert result.returncode != 0
    assert "too long for the bundled layout" in result.stderr
    assert "adaos-core-b-XXXXXXXX" in result.stderr
    assert not runtime_base.exists()


def test_path_preflight_checks_tracked_files_without_local_cache_noise(tmp_path: Path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    subprocess.run([git, "init", str(source)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(source), "add", "pyproject.toml"], check=True)
    cache = source / "untracked-cache"
    cache.mkdir()
    (cache / ("x" * 100)).write_text("not delivered", encoding="utf-8")

    result = _preflight(source, tmp_path / "runtime")

    assert result.returncode == 0, result.stderr


def test_path_preflight_measures_utf8_tracked_names_as_windows_characters(tmp_path: Path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is unavailable")
    source = tmp_path / "source"
    source.mkdir()
    filename = "界" * 45 + ".py"
    (source / filename).write_text("# Unicode bundled name\n", encoding="utf-8")
    subprocess.run([git, "init", str(source)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(source), "add", "."], check=True)

    result = _preflight(source, tmp_path / "runtime")

    assert result.returncode == 0, result.stderr
