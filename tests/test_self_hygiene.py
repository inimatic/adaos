from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

import adaos.services.self_hygiene as self_hygiene


def test_apply_retention_policy_writes_linux_policy_files(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    logs_dir = base_dir / "logs"
    etc_dir = tmp_path / "etc"
    systemd_dir = tmp_path / "systemd"

    monkeypatch.setattr(self_hygiene.platform, "system", lambda: "Linux")
    monkeypatch.delenv("ADAOS_TESTING", raising=False)

    payload = self_hygiene.apply_retention_policy(
        base_dir=base_dir,
        logs_dir=logs_dir,
        system_etc_dir=etc_dir,
        systemd_dir=systemd_dir,
        enable_timer=True,
    )

    assert payload["ok"] is True
    assert (etc_dir / "systemd" / "journald.conf.d" / "adaos-retention.conf").exists()
    assert (etc_dir / "tmpfiles.d" / "adaos.conf").exists()
    assert (etc_dir / "logrotate.d" / "adaos").exists()
    assert (systemd_dir / "adaos-hygiene.service").exists()
    assert (systemd_dir / "adaos-hygiene.timer").exists()
    assert str(base_dir.resolve()) in (systemd_dir / "adaos-hygiene.service").read_text(encoding="utf-8")
    state = json.loads((base_dir / "state" / "self_hygiene" / "retention-policy.json").read_text(encoding="utf-8"))
    assert state["base_dir"] == str(base_dir.resolve())


def test_apply_retention_policy_is_safe_on_windows(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(self_hygiene.platform, "system", lambda: "Windows")

    payload = self_hygiene.apply_retention_policy(base_dir=tmp_path / "base")

    assert payload["ok"] is True
    assert payload["os_policy"]["skipped"] is True
    assert payload["os_policy"]["reason"] == "windows_local_state_only"
    assert (tmp_path / "base" / "state" / "self_hygiene" / "retention-policy.json").exists()


def test_run_hygiene_cleans_old_adaos_and_pip_tmp(monkeypatch, tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_tmp = base_dir / "tmp"
    global_tmp = tmp_path / "tmp"
    old_adaos = base_tmp / "old.tmp"
    recent_adaos = base_tmp / "recent.tmp"
    old_pip = global_tmp / "pip-unpack-old"
    old_large_tmp = global_tmp / "tmp-large-file"
    old_adaos.parent.mkdir(parents=True, exist_ok=True)
    global_tmp.mkdir(parents=True, exist_ok=True)
    old_adaos.write_text("old", encoding="utf-8")
    recent_adaos.write_text("recent", encoding="utf-8")
    old_pip.mkdir()
    (old_pip / "wheel.whl").write_text("wheel", encoding="utf-8")
    with old_large_tmp.open("wb") as handle:
        handle.truncate(101 * self_hygiene.MiB)

    now = 2_000_000_000.0
    old_time = now - 10_000.0
    recent_time = now - 10.0
    for path in (old_adaos, old_pip, old_large_tmp):
        os.utime(path, (old_time, old_time))
    os.utime(recent_adaos, (recent_time, recent_time))

    monkeypatch.setattr(self_hygiene.platform, "system", lambda: "Linux")
    monkeypatch.setattr(self_hygiene, "_clean_pip_cache", lambda **_kwargs: {"ok": True, "commands": []})

    payload = self_hygiene.run_hygiene(
        base_dir=base_dir,
        trigger="test",
        include_pip_cache=False,
        global_tmp_roots=[global_tmp],
        tmp_min_age_seconds=3600.0,
        now=now,
    )

    assert payload["ok"] is True
    assert old_adaos.exists() is False
    assert recent_adaos.exists() is True
    assert old_pip.exists() is False
    assert old_large_tmp.exists() is False


def test_run_hygiene_reports_unmanaged_backup_without_deleting(tmp_path: Path) -> None:
    backup_root = tmp_path / "external-bak"
    old_snapshot = backup_root / "2026-05-01"
    old_snapshot.mkdir(parents=True)
    (old_snapshot / "data.txt").write_text("keep", encoding="utf-8")

    payload = self_hygiene.run_hygiene(
        base_dir=tmp_path / "base",
        trigger="test",
        include_pip_cache=False,
        include_global_tmp=False,
        backup_roots=[backup_root],
        now=2_000_000_000.0,
    )

    backups = payload["actions"]["managed_backups"]
    assert backups[0]["managed"] is False
    assert backups[0]["reason"] == "missing_adaos_managed_backup_marker"
    assert old_snapshot.exists() is True


def test_run_hygiene_cleans_marked_managed_backup(tmp_path: Path) -> None:
    backup_root = tmp_path / "managed-bak"
    old_snapshot = backup_root / "2026-05-01"
    new_snapshot = backup_root / "2026-05-22"
    old_snapshot.mkdir(parents=True)
    new_snapshot.mkdir(parents=True)
    (backup_root / ".adaos-managed-backup").write_text("", encoding="utf-8")
    now = 2_000_000_000.0
    os.utime(old_snapshot, (now - 20 * 86400.0, now - 20 * 86400.0))
    os.utime(new_snapshot, (now - 60.0, now - 60.0))

    payload = self_hygiene.run_hygiene(
        base_dir=tmp_path / "base",
        trigger="test",
        include_pip_cache=False,
        include_global_tmp=False,
        backup_roots=[backup_root],
        policy={"managed_backup_keep_days": 7, "managed_backup_keep_latest": 1},
        now=now,
    )

    backups = payload["actions"]["managed_backups"]
    assert backups[0]["managed"] is True
    assert backups[0]["removed_total"] == 1
    assert old_snapshot.exists() is False
    assert new_snapshot.exists() is True


def test_maintenance_status_cli_returns_json(cli_app, tmp_base_dir) -> None:
    result = CliRunner().invoke(cli_app, ["maintenance", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["base_dir"] == str(tmp_base_dir.resolve())
    assert "disk" in payload
