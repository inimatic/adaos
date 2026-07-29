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


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="requires a POSIX shell")
def test_node_recovery_finalizes_root_restart_exactly_once(tmp_path: Path) -> None:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not branch:
        pytest.skip("checkout has no named branch")

    base_dir = tmp_path / "adaos"
    slots = base_dir / "state" / "core_slots"
    manifest_dir = slots / "slots" / "A"
    manifest_dir.mkdir(parents=True)
    (slots / "active").write_text("A\n", encoding="utf-8")
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"git_commit": commit, "build_version": "test", "repo_url": str(ROOT)}),
        encoding="utf-8",
    )
    update_dir = base_dir / "state" / "core_update"
    update_dir.mkdir(parents=True)
    (update_dir / "status.json").write_text(
        json.dumps(
            {
                "state": "succeeded",
                "phase": "root_promoted",
                "target_version": commit,
            }
        ),
        encoding="utf-8",
    )
    operation_dir = base_dir / "state" / "node_recovery" / commit
    operation_dir.mkdir(parents=True)
    (operation_dir / "intent.env").write_text("schema=adaos.node-recovery-intent.v1\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    restart_log = tmp_path / "restart.log"
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        "if [ \"${1:-}\" = show ]; then\n"
        "  if [ -s \"${RESTART_LOG}\" ]; then echo 222; else echo 111; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1:-}\" = restart ] && [ \"${2:-}\" = adaos ]; then\n"
        "  echo restart >> \"${RESTART_LOG}\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "ADAOS_BASE_DIR": str(base_dir),
            "ADAOS_ROOT_REPO_ROOT": str(ROOT),
            "ADAOS_SHARED_DOTENV_PATH": str(tmp_path / "missing.env"),
            "ADAOS_RECOVERY_CONTROL_REPO": str(ROOT),
            "ADAOS_RECOVERY_CONTROL_PYTHON": sys.executable,
            "ADAOS_RECOVERY_ROOT_PYTHON": sys.executable,
            "ADAOS_RECOVERY_REPO_URL": str(ROOT),
            "PATH": os.pathsep.join((str(fake_bin), env.get("PATH", ""))),
            "RESTART_LOG": str(restart_log),
        }
    )
    argv = [
        "bash",
        str(SCRIPT),
        "--target-rev",
        branch,
        "--target-version",
        commit,
        "--finalize-root-restart",
    ]

    first = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    second = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, check=False)

    assert first.returncode == 0, first.stderr
    assert "exactly one guarded root-control restart" in first.stdout
    assert second.returncode == 0, second.stderr
    assert "will not be dispatched again" in second.stdout
    assert restart_log.read_text(encoding="utf-8").splitlines() == ["restart"]
    receipt = (operation_dir / "root-restart.env").read_text(encoding="utf-8")
    assert "state=dispatched" in receipt
    assert "before_pid=111" in receipt
    assert "after_pid=222" in receipt
