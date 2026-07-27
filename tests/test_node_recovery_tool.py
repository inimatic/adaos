from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "recover-node-update.sh"


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="requires a POSIX shell")
def test_node_recovery_dry_run_selects_verified_control_without_writes(tmp_path: Path) -> None:
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        text=True,
    ).strip()
    if not branch:
        pytest.skip("checkout has no named branch")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    base_dir = tmp_path / "adaos"
    slots = base_dir / "state" / "core_slots"
    manifest_dir = slots / "slots" / "A"
    manifest_dir.mkdir(parents=True)
    (slots / "active").write_text("A\n", encoding="utf-8")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "git_commit": "0" * 40,
                "build_version": "0.0.0",
                "repo_url": str(ROOT),
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "ADAOS_BASE_DIR": str(base_dir),
            "ADAOS_ROOT_REPO_ROOT": str(ROOT),
            "ADAOS_SHARED_DOTENV_PATH": str(tmp_path / "missing.env"),
            "ADAOS_RECOVERY_CONTROL_REPO": str(ROOT),
            "ADAOS_RECOVERY_CONTROL_PYTHON": sys.executable,
            "ADAOS_RECOVERY_REPO_URL": str(ROOT),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--target-rev",
            branch,
            "--target-version",
            commit,
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "dry-run preflight passed" in result.stdout
    assert "control=explicit" in result.stdout
    assert not (base_dir / "state" / "node_recovery").exists()


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="requires a POSIX shell")
def test_node_recovery_rejects_unpinned_target_before_state_changes(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["ADAOS_BASE_DIR"] = str(tmp_path / "adaos")
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--target-rev",
            "rev2026",
            "--target-version",
            "latest",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "exact 40-character commit SHA" in result.stderr
    assert not (tmp_path / "adaos" / "state").exists()
