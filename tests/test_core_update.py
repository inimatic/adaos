from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from pathlib import Path

import pytest

from adaos.services import core_update as core_update_service
from adaos.services.core_update import (
    clear_plan,
    execute_pending_update,
    finalize_runtime_boot_status,
    configured_update_command,
    _run_command_with_bounded_output,
    prepare_pending_update,
    read_last_result,
    read_plan,
    read_status,
    rollback_installed_skill_runtimes,
    write_plan,
    write_status,
)
from adaos.services.core_slots import (
    active_slot,
    activate_slot,
    reconcile_active_slot_marker,
    read_slot_manifest,
    slot_dir,
    write_slot_manifest,
)


def _make_valid_slot(slot: str) -> None:
    root = slot_dir(slot)
    app_entry = root / "repo" / "src" / "adaos" / "apps" / "autostart_runner.py"
    app_entry.parent.mkdir(parents=True, exist_ok=True)
    app_entry.write_text("# test runtime entry\n", encoding="utf-8")
    python_path = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("# test python\n", encoding="utf-8")
    write_slot_manifest(
        slot,
        {
            "slot": slot,
            "repo_dir": str(root / "repo"),
            "venv_dir": str(root / "venv"),
        },
    )


def test_bootstrap_critical_paths_cover_root_api_contracts() -> None:
    from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS

    assert "src/adaos/apps/api/server.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/apps/api/node_api.py" in BOOTSTRAP_CRITICAL_PATHS
    assert "src/adaos/services/bootstrap.py" in BOOTSTRAP_CRITICAL_PATHS


def test_reconcile_active_slot_marker_restores_valid_previous_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    _make_valid_slot("A")
    activate_slot("A")
    activate_slot("B")

    result = reconcile_active_slot_marker()

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["invalid_slot"] == "B"
    assert result["restored_slot"] == "A"
    assert active_slot() == "A"


def test_core_update_plan_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    clear_plan()
    payload = {"target_rev": "rev2026", "expires_at": 9999999999.0}
    write_plan(payload)
    assert read_plan()["target_rev"] == "rev2026"


def test_core_update_command_formats_placeholders(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_CORE_UPDATE_CMD", "echo {target_rev} {target_version} {base_dir}")
    cmd = configured_update_command({"target_rev": "rev2026", "target_version": "1.2.3"})
    assert cmd is not None
    assert "rev2026" in cmd
    assert "1.2.3" in cmd
    assert str(tmp_path) in cmd


def test_manifest_target_match_accepts_build_version() -> None:
    from adaos.services.core_update import _manifest_matches_target_version

    assert _manifest_matches_target_version(
        {"build_version": "0.1.7+44.abc1234", "git_commit": "f" * 40},
        "0.1.7+44.abc1234",
    )


def test_core_update_command_uses_builtin_runner_when_not_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("ADAOS_CORE_UPDATE_CMD", raising=False)
    cmd = configured_update_command({"target_rev": "rev2026", "target_slot": "B", "inactive_slot_dir": str(tmp_path / "slot-b")})
    assert cmd is not None
    assert "adaos.apps.core_update_apply" in cmd
    assert "rev2026" in cmd
    assert '--slot "B"' in cmd
    assert f'--slot-dir "{tmp_path / "slot-b"}"' in cmd


def test_core_update_status_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status({"state": "countdown", "message": "scheduled"})
    assert read_status()["state"] == "countdown"


def test_async_runtime_boot_finalization_keeps_io_off_owner_loop(monkeypatch) -> None:
    owner_thread = threading.get_ident()
    worker_threads: list[int] = []
    publisher_threads: list[int] = []
    finalized = {"state": "validated", "phase": "root_promotion_pending"}

    def _finalize(**_kwargs):
        worker_threads.append(threading.get_ident())
        return finalized

    monkeypatch.setattr(core_update_service, "finalize_runtime_boot_status", _finalize)
    monkeypatch.setattr(
        core_update_service,
        "_publish_status_events",
        lambda payload: publisher_threads.append(threading.get_ident()),
    )

    result = asyncio.run(core_update_service.finalize_runtime_boot_status_async())

    assert result == finalized
    assert worker_threads and worker_threads[0] != owner_thread
    assert publisher_threads == [owner_thread]


def test_core_update_status_keeps_rollout_metadata_across_validate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "plan": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "0.1.0+77.d7d79d5",
                "reason": "infrastate.start_update",
            },
        }
    )

    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "target_slot": "B",
            "manifest": {
                "slot": "B",
                "target_rev": "rev2026",
                "target_version": "0.1.0+77.d7d79d5",
            },
        }
    )

    status = read_status()
    assert status["action"] == "update"
    assert status["target_rev"] == "rev2026"
    assert status["target_version"] == "0.1.0+77.d7d79d5"
    assert status["planned_reason"] == "infrastate.start_update"
    assert read_last_result()["target_version"] == "0.1.0+77.d7d79d5"


def test_core_update_status_does_not_inherit_metadata_from_another_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "old-target",
            "planned_reason": "minimum_update_period",
            "scheduled_for": 123.0,
            "candidate_prewarm_state": "failed",
        }
    )

    status = write_status(
        {
            "state": "preparing",
            "phase": "prepare",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "new-target",
            "reason": "operator.update",
        }
    )

    assert status["target_version"] == "new-target"
    assert "planned_reason" not in status
    assert "scheduled_for" not in status
    assert "candidate_prewarm_state" not in status


def test_finalize_runtime_boot_status_rejects_active_slot_target_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "target_version": "1111111111111111111111111111111111111111",
            "git_commit": "1111111111111111111111111111111111111111",
            "git_short_commit": "1111111",
        },
    )
    activate_slot("B")
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "2222222222222222222222222222222222222222",
            "target_slot": "B",
        }
    )

    status = finalize_runtime_boot_status()

    assert status["state"] == "failed"
    assert status["phase"] == "validate"
    assert status["active_slot_target_mismatch"] is True
    assert status["target_version"] == "2222222222222222222222222222222222222222"
    assert status["manifest"]["git_short_commit"] == "1111111"
    assert read_last_result()["active_slot_target_mismatch"] is True


def test_finalize_runtime_boot_status_hydrates_install_metrics_from_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "target_version": "0.1.0+1.abc1234",
            "install": {
                "installer": "uv",
                "elapsed_s": 1.234,
            },
            "venv_seed": {
                "seeded": True,
                "source": "active_slot",
                "copy_method": "cp_reflink_auto",
                "copy_elapsed_s": 0.456,
                "elapsed_s": 0.567,
                "repair": {
                    "elapsed_s": 0.111,
                    "repaired_files_total": 7,
                },
            },
        },
    )
    activate_slot("B")
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_version": "0.1.0+1.abc1234",
            "target_slot": "B",
        }
    )

    status = finalize_runtime_boot_status()

    assert status["state"] == "succeeded"
    assert status["phase"] == "validate"
    assert status["install_installer"] == "uv"
    assert status["install_elapsed_s"] == 1.234
    assert status["venv_seeded"] is True
    assert status["venv_seed_source"] == "active_slot"
    assert status["venv_seed_copy_method"] == "cp_reflink_auto"
    assert status["venv_seed_copy_elapsed_s"] == 0.456
    assert status["venv_seed_elapsed_s"] == 0.567
    assert status["venv_seed_repair_elapsed_s"] == 0.111
    assert status["venv_repair_files_total"] == 7
    assert read_last_result()["venv_seed_copy_method"] == "cp_reflink_auto"


def test_finalize_runtime_boot_status_does_not_reject_pending_target_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "A",
        {
            "slot": "A",
            "target_version": "1111111111111111111111111111111111111111",
            "git_commit": "1111111111111111111111111111111111111111",
            "git_short_commit": "1111111",
        },
    )
    activate_slot("A")
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "2222222222222222222222222222222222222222",
            "target_slot": "B",
        }
    )

    status = finalize_runtime_boot_status()

    assert status is None
    current = read_status()
    assert current["state"] == "planned"
    assert current["phase"] == "scheduled"
    assert not current.get("active_slot_target_mismatch")


def test_finalize_runtime_boot_status_requires_explicit_slot_for_target_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "A",
        {
            "slot": "A",
            "target_version": "1111111111111111111111111111111111111111",
            "git_commit": "1111111111111111111111111111111111111111",
            "git_short_commit": "1111111",
        },
    )
    activate_slot("A")
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "2222222222222222222222222222222222222222",
        }
    )

    status = finalize_runtime_boot_status()

    assert status is None
    current = read_status()
    assert current["state"] == "restarting"
    assert current["phase"] == "launch"
    assert not current.get("active_slot_target_mismatch")


def test_core_update_status_publishes_bus_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    published: list[object] = []

    class _Bus:
        def publish(self, evt) -> None:
            published.append(evt)

    class _Ctx:
        bus = _Bus()

    monkeypatch.setattr("adaos.services.core_update.get_ctx", lambda: _Ctx())
    write_status({"state": "countdown", "message": "scheduled"})
    assert [getattr(evt, "type", "") for evt in published] == [
        "core.update.status",
        "supervisor.update.status.raw",
    ]


def test_execute_pending_update_activates_target_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))

    def _fake_run(command: str):
        write_slot_manifest(
            "B",
            {
                "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
                "version": "2026.1",
            },
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("adaos.services.core_update._run_command_with_bounded_output", _fake_run)
    result = execute_pending_update({"target_rev": "rev2026", "target_slot": "B"})
    assert result["state"] == "succeeded"
    assert active_slot() == "B"
    assert read_slot_manifest("B")["version"] == "2026.1"


def test_execute_pending_update_rolls_back(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest("A", {"argv": ["python", "-m", "adaos.apps.autostart_runner"]})
    write_slot_manifest("B", {"argv": ["python", "-m", "adaos.apps.autostart_runner"]})
    activate_slot("A")
    activate_slot("B")
    monkeypatch.setattr(
        "adaos.services.core_update.rollback_installed_skill_runtimes",
        lambda: {"ok": True, "total": 2, "failed_total": 0, "rollback_total": 2, "skills": []},
    )
    result = execute_pending_update({"action": "rollback"})
    assert result["state"] == "rolled_back"
    assert active_slot() == "A"
    assert result["skill_runtime_rollback"]["rollback_total"] == 2


def test_execute_pending_update_inherits_target_rev_from_active_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest("A", {"argv": ["python", "-m", "adaos.apps.autostart_runner"], "target_rev": "rev2026"})
    activate_slot("A")

    seen: dict[str, str] = {}

    def _fake_run(command: str):
        seen["command"] = command
        write_slot_manifest(
            "B",
            {
                "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
                "target_rev": "rev2026",
            },
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("adaos.services.core_update._run_command_with_bounded_output", _fake_run)
    result = execute_pending_update({"target_version": "0.1.0"})
    assert result["state"] == "succeeded"
    assert "rev2026" in seen["command"]


def test_run_command_with_bounded_output_keeps_only_tail(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_CORE_UPDATE_OUTPUT_TAIL_CHARS", "8")

    class _FakeStream:
        def __init__(self, chunks: list[str]) -> None:
            self._chunks = list(chunks)

        def read(self, _size: int = -1) -> str:
            if not self._chunks:
                return ""
            return self._chunks.pop(0)

        def close(self) -> None:
            return None

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeStream(["abcd", "efgh", "ijkl"])
            self.stderr = _FakeStream(["1234", "5678", "90"])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr("adaos.services.core_update.subprocess.Popen", lambda *args, **kwargs: _FakeProc())

    completed = _run_command_with_bounded_output("echo test")

    assert completed.returncode == 0
    assert completed.stdout == "efghijkl"
    assert completed.stderr == "34567890"


def test_prepare_pending_update_defers_skill_runtime_migration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    captured: dict[str, object] = {}

    def _fake_prepare_slot(**kwargs):
        captured.update(kwargs)
        return {"slot": "B", "argv": ["python", "-m", "adaos.apps.autostart_runner"]}

    monkeypatch.setattr("adaos.apps.core_update_apply.prepare_slot", _fake_prepare_slot)

    result = prepare_pending_update({"target_rev": "rev2026", "target_slot": "B"})

    assert result["state"] == "prepared"
    assert result["target_slot"] == "B"
    assert captured["slot"] == "B"
    assert captured["migrate_skill_runtimes"] is False


def test_rollback_installed_skill_runtimes_marks_expected_skips(monkeypatch) -> None:
    class _Row:
        def __init__(self, name: str, installed: bool = True) -> None:
            self.name = name
            self.installed = installed

    class _Registry:
        def __init__(self, _sql) -> None:
            pass

        def list(self):
            return [_Row("weather_skill"), _Row("voice_skill"), _Row("draft_skill", installed=False)]

    class _Manager:
        def rollback_runtime(self, name: str) -> str:
            if name == "weather_skill":
                return "A"
            raise RuntimeError("no previous slot recorded for rollback")

    class _Ctx:
        sql = object()
        skills_repo = object()
        git = object()
        paths = object()
        bus = None
        caps = object()

    monkeypatch.setattr("adaos.services.core_update.get_ctx", lambda: _Ctx())
    monkeypatch.setattr("adaos.adapters.db.SqliteSkillRegistry", _Registry)
    monkeypatch.setattr("adaos.services.skill.manager.SkillManager", lambda **kwargs: _Manager())

    payload = rollback_installed_skill_runtimes()

    assert payload["ok"] is True
    assert payload["rollback_total"] == 1
    assert payload["skipped_total"] == 1
    assert payload["failed_total"] == 0


def test_finalize_runtime_boot_status_marks_root_promotion_pending(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")
    write_status({"state": "restarting", "phase": "launch", "target_slot": "B"})

    payload = finalize_runtime_boot_status()

    assert payload is not None
    assert payload["state"] == "validated"
    assert payload["phase"] == "root_promotion_pending"
    assert payload["root_promotion_required"] is True
    assert "src/adaos/apps/supervisor.py" in payload["bootstrap_update"]["changed_paths"]
    assert read_last_result()["phase"] == "root_promotion_pending"
    assert read_plan() is None
    assert payload["scheduled_for"] is None
    assert payload["candidate_prewarm_state"] is None


def test_finalize_runtime_boot_status_marks_root_restart_completed_after_root_promoted(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")
    write_plan({"state": "prepared_restart", "action": "update", "target_slot": "B", "expires_at": 9999999999.0})
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "target_slot": "B",
            "candidate_prewarm_state": "starting",
            "candidate_prewarm_message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
            "candidate_prewarm_ready_at": 123.0,
            "root_promotion_required": False,
        }
    )

    runtime_payload = finalize_runtime_boot_status()

    assert runtime_payload is None
    assert read_status()["phase"] == "root_promoted"

    payload = finalize_runtime_boot_status(supervisor_authorized=True)

    assert payload is not None
    assert payload["state"] == "succeeded"
    assert payload["phase"] == "validate"
    assert payload["root_promotion_required"] is False
    assert payload["root_restart_completed_at"] > 0
    assert payload["candidate_prewarm_state"] is None
    assert payload["candidate_prewarm_message"] is None
    assert payload["candidate_prewarm_ready_at"] is None
    assert read_plan() is None


def test_finalize_runtime_boot_status_reopens_root_promotion_when_root_source_still_stale(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos" / "apps" / "cli" / "commands").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "cli" / "commands" / "node.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "cli" / "commands" / "node.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "bootstrap_update": {"required": False, "changed_paths": []},
        },
    )
    activate_slot("B")
    write_status({"state": "succeeded", "phase": "root_promoted", "target_slot": "B"})

    payload = finalize_runtime_boot_status(supervisor_authorized=True)

    assert payload is not None
    assert payload["state"] == "validated"
    assert payload["phase"] == "root_promotion_pending"
    assert payload["root_promotion_required"] is True
    assert "src/adaos/apps/cli/commands/node.py" in payload["bootstrap_update"]["effective_mismatched_paths"]


def test_finalize_runtime_boot_status_clears_candidate_prewarm_after_successful_validate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "bootstrap_update": {"required": False, "changed_paths": []},
        },
    )
    activate_slot("B")
    write_plan({"state": "prepared_restart", "action": "update", "target_slot": "B", "expires_at": 9999999999.0})
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "target_slot": "B",
            "scheduled_for": 123.0,
            "candidate_prewarm_state": "starting",
            "candidate_prewarm_message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
            "candidate_prewarm_ready_at": 124.0,
        }
    )

    payload = finalize_runtime_boot_status()

    assert payload is not None
    assert payload["state"] == "succeeded"
    assert payload["phase"] == "validate"
    assert payload["scheduled_for"] is None
    assert payload["candidate_prewarm_state"] is None
    assert payload["candidate_prewarm_message"] is None
    assert payload["candidate_prewarm_ready_at"] is None
    assert read_plan() is None


def test_resolved_root_promotion_detects_stale_operator_cli(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos" / "apps" / "cli" / "commands").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "cli" / "commands" / "node.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "cli" / "commands" / "node.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    required, payload = resolved_root_promotion_requirement(
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {"required": False, "changed_paths": []},
        }
    )

    assert required is True
    assert payload["declared_required"] is False
    assert "src/adaos/apps/cli/commands/node.py" in payload["effective_mismatched_paths"]


def test_resolved_root_promotion_ignores_windows_line_endings(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    relative = Path("src/adaos/apps/supervisor.py")
    (root_dir / relative).write_bytes(b"def main():\r\n    return 0\r\n")
    (slot_repo / relative).write_bytes(b"def main():\n    return 0\n")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    required, payload = resolved_root_promotion_requirement(
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {"required": False, "changed_paths": []},
        }
    )

    assert required is False
    assert relative.as_posix() not in payload["effective_mismatched_paths"]


def test_resolved_root_promotion_does_not_downgrade_newer_clean_root(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "supervisor.py").write_text("newer\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("candidate\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)
    monkeypatch.setattr(
        "adaos.services.core_update._root_checkout_contains_candidate_commit",
        lambda *_args, **_kwargs: (
            True,
            {
                "effective_root_commit_relation": "contains_candidate",
                "effective_root_commit": "b" * 40,
                "effective_candidate_commit": "a" * 40,
            },
        ),
    )

    required, payload = resolved_root_promotion_requirement(
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "git_commit": "a" * 40,
            "bootstrap_update": {"required": True, "changed_paths": ["src/adaos/apps/supervisor.py"]},
        }
    )

    assert required is False
    assert payload["effective_basis"] == "root_checkout_contains_candidate"
    assert payload["effective_mismatched_paths"] == []


def test_resolved_root_promotion_detects_stale_build_info_dependency(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "build_info.py").write_text("BUILD_INFO = object()\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "build_info.py").write_text(
        "BUILD_INFO = object()\ndef base_version():\n    return '0.1.0'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    required, payload = resolved_root_promotion_requirement(
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {"required": False, "changed_paths": []},
        }
    )

    assert required is True
    assert payload["declared_required"] is False
    assert "src/adaos/build_info.py" in payload["effective_mismatched_paths"]


def test_resolved_root_promotion_detects_stale_pyproject_version(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    root_dir.mkdir(parents=True, exist_ok=True)
    slot_repo.mkdir(parents=True, exist_ok=True)
    (root_dir / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (slot_repo / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "0.1.217"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    required, payload = resolved_root_promotion_requirement(
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {"required": False, "changed_paths": []},
        }
    )

    assert required is True
    assert payload["declared_required"] is False
    assert "pyproject.toml" in payload["effective_mismatched_paths"]


def test_promote_root_from_slot_replaces_bootstrap_package_atomically(monkeypatch, tmp_path) -> None:
    import adaos.services.core_update as core_update

    promote_root_from_slot = core_update.promote_root_from_slot

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    (root_dir / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (slot_repo / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "supervisor.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("new\n", encoding="utf-8")
    (root_dir / "src" / "adaos" / "services").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "services" / "legacy.py").write_text("legacy\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "services" / "skill").mkdir(parents=True, exist_ok=True)
    (slot_repo / "src" / "adaos" / "services" / "skill" / "declarations.py").write_text(
        "current\n",
        encoding="utf-8",
    )
    (slot_repo / "src" / "adaos" / "services" / "__pycache__").mkdir(parents=True)
    (slot_repo / "src" / "adaos" / "services" / "__pycache__" / "stale.pyc").write_bytes(b"stale")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    original_copy = core_update._copy_path
    staged_while_old_root_was_intact: list[bool] = []

    def _observe_staging(source: Path, target: Path) -> None:
        if source.resolve() == (slot_repo / "src" / "adaos").resolve() and ".adaos-stage-" in target.name:
            staged_while_old_root_was_intact.append(
                (root_dir / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "old\n"
            )
        original_copy(source, target)

    monkeypatch.setattr(core_update, "_copy_path", _observe_staging)

    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")

    payload = promote_root_from_slot()

    assert payload["ok"] is True
    assert payload["required"] is True
    assert payload["restart_required"] is True
    assert payload["transaction_state"] == "committed"
    assert payload["backup_mode"] == "atomic_rename"
    assert payload["cutover_elapsed_ms"] >= 0
    assert payload["preflight"]["ok"] is True
    assert payload["changed_paths"] == ["src/adaos"]
    assert (root_dir / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "new\n"
    assert (
        root_dir / "src" / "adaos" / "services" / "skill" / "declarations.py"
    ).read_text(encoding="utf-8") == "current\n"
    assert not (root_dir / "src" / "adaos" / "services" / "legacy.py").exists()
    assert staged_while_old_root_was_intact == [True]
    assert not (root_dir / "src" / "adaos" / "services" / "__pycache__").exists()
    backup_file = Path(payload["backup_dir"]) / "src" / "adaos" / "apps" / "supervisor.py"
    assert backup_file.read_text(encoding="utf-8") == "old\n"
    backup_legacy = Path(payload["backup_dir"]) / "src" / "adaos" / "services" / "legacy.py"
    assert backup_legacy.read_text(encoding="utf-8") == "legacy\n"


def test_promote_root_from_slot_aborts_before_mutation_when_import_preflight_fails(
    monkeypatch,
    tmp_path,
) -> None:
    import adaos.services.core_update as core_update

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path / "base"))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (root_dir, slot_repo):
        (base / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
        (base / "src" / "adaos" / "__init__.py").write_text("", encoding="utf-8")
    root_supervisor = root_dir / "src" / "adaos" / "apps" / "supervisor.py"
    slot_supervisor = slot_repo / "src" / "adaos" / "apps" / "supervisor.py"
    root_supervisor.write_text("old\n", encoding="utf-8")
    slot_supervisor.write_text("new\n", encoding="utf-8")
    (slot_repo / "pyproject.toml").write_text("[project]\nname='adaos'\nversion='0.0.0'\n", encoding="utf-8")
    monkeypatch.setattr(core_update, "_repo_root", lambda: root_dir)
    monkeypatch.setattr(core_update, "current_control_python", lambda _root: Path(os.sys.executable))
    monkeypatch.setattr(
        core_update.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "missing dependency"),
    )
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")

    with pytest.raises(RuntimeError, match="import preflight failed"):
        core_update.promote_root_from_slot()

    assert root_supervisor.read_text(encoding="utf-8") == "old\n"
    assert not list((tmp_path / "base" / "state" / "root_promotion").glob("*-b"))


def test_write_status_retries_transient_windows_replace_denial(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    real_replace = core_update_service.os.replace
    attempts: list[tuple[Path, Path]] = []
    delays: list[float] = []

    def _replace(source: Path, target: Path) -> None:
        attempts.append((source, target))
        if len(attempts) < 3:
            error = PermissionError("target is temporarily observed")
            error.winerror = 5
            raise error
        real_replace(source, target)

    monkeypatch.setattr(core_update_service.os, "replace", _replace)
    monkeypatch.setattr(core_update_service.time, "sleep", delays.append)

    persisted = write_status({"state": "countdown", "message": "scheduled"})

    assert persisted["state"] == "countdown"
    assert read_status()["state"] == "countdown"
    assert len(attempts) == 3
    assert delays == [0.005, 0.01]


def test_promote_root_from_slot_rolls_back_partial_apply(monkeypatch, tmp_path) -> None:
    import adaos.services.core_update as core_update

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path / "base"))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    relative_paths = ["bootstrap/first.py", "bootstrap/second.py"]
    for base, prefix in ((root_dir, "old"), (slot_repo, "new")):
        (base / "bootstrap").mkdir(parents=True, exist_ok=True)
        for index, rel_path in enumerate(relative_paths, start=1):
            (base / rel_path).write_text(f"{prefix}-{index}\n", encoding="utf-8")
    monkeypatch.setattr(core_update, "_repo_root", lambda: root_dir)
    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {"required": True, "changed_paths": relative_paths},
        },
    )
    activate_slot("B")
    original_replace = core_update._replace_promotion_path
    failing_target = (root_dir / relative_paths[1]).resolve()

    def _replace_with_apply_failure(source: Path, target: Path) -> None:
        if ".adaos-stage-" in source.name and target.resolve() == failing_target:
            raise OSError("simulated apply failure")
        original_replace(source, target)

    monkeypatch.setattr(core_update, "_replace_promotion_path", _replace_with_apply_failure)

    with pytest.raises(RuntimeError, match="was rolled back"):
        core_update.promote_root_from_slot()

    assert (root_dir / relative_paths[0]).read_text(encoding="utf-8") == "old-1\n"
    assert (root_dir / relative_paths[1]).read_text(encoding="utf-8") == "old-2\n"
    metadata_paths = list((tmp_path / "base" / "state" / "root_promotion").glob("*-b/metadata.json"))
    assert len(metadata_paths) == 1
    assert '"transaction_state": "rolled_back"' in metadata_paths[0].read_text(encoding="utf-8")


def test_promote_root_from_slot_copies_pyproject_version_metadata(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import promote_root_from_slot

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    root_dir.mkdir(parents=True, exist_ok=True)
    slot_repo.mkdir(parents=True, exist_ok=True)
    (root_dir / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (slot_repo / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "0.1.217"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["pyproject.toml"],
            },
        },
    )
    activate_slot("B")

    payload = promote_root_from_slot()

    assert payload["ok"] is True
    assert "pyproject.toml" in payload["promoted_paths"]
    assert 'version = "0.1.217"' in (root_dir / "pyproject.toml").read_text(encoding="utf-8")
    backup_file = Path(payload["backup_dir"]) / "pyproject.toml"
    assert 'version = "0.1.0"' in backup_file.read_text(encoding="utf-8")


def test_promote_root_from_slot_prefers_manifest_root_repo_root(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import promote_root_from_slot

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    wrong_root = tmp_path / "wrong-root"
    right_root = tmp_path / "right-root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    for base in (wrong_root, right_root, slot_repo):
        (base / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (wrong_root / "src" / "adaos" / "apps" / "supervisor.py").write_text("wrong\n", encoding="utf-8")
    (right_root / "src" / "adaos" / "apps" / "supervisor.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: wrong_root)

    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "root_repo_root": str(right_root),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")

    payload = promote_root_from_slot()

    assert payload["target_root"] == str(right_root.resolve())
    assert payload["target_root_basis"] == "manifest.root_repo_root"
    assert (right_root / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "new\n"
    assert (wrong_root / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "wrong\n"


def test_promote_root_from_slot_ignores_slot_manifest_root_when_stable_root_exists(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import promote_root_from_slot

    base_dir = tmp_path / "base"
    monkeypatch.setenv("ADAOS_BASE_DIR", str(base_dir))
    stable_root = tmp_path / "adaos"
    manifest_root = base_dir / "state" / "core_slots" / "slots" / "A" / "repo"
    slot_repo = base_dir / "state" / "core_slots" / "slots" / "B" / "repo"
    for base in (stable_root, manifest_root, slot_repo):
        (base / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (stable_root / "src" / "adaos" / "apps" / "supervisor.py").write_text("old-stable\n", encoding="utf-8")
    (manifest_root / "src" / "adaos" / "apps" / "supervisor.py").write_text("old-slot-root\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: stable_root)

    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "root_repo_root": str(manifest_root),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")

    payload = promote_root_from_slot()

    assert payload["target_root"] == str(stable_root.resolve())
    assert payload["target_root_basis"] == "runtime_context.stable_root_over_manifest_slot"
    assert (stable_root / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "new\n"
    assert (manifest_root / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "old-slot-root\n"


def test_resolved_root_promotion_requirement_tracks_current_root_state(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import promote_root_from_slot, resolved_root_promotion_requirement

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    (root_dir / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (slot_repo / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "supervisor.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    manifest = {
        "slot": "B",
        "repo_dir": str(slot_repo),
        "bootstrap_update": {
            "required": True,
            "changed_paths": ["src/adaos/apps/supervisor.py"],
        },
    }
    write_slot_manifest("B", manifest)
    activate_slot("B")

    required_before, details_before = resolved_root_promotion_requirement(manifest)

    assert required_before is True
    assert details_before["effective_required"] is True
    assert details_before["effective_mismatched_paths"] == ["src/adaos/apps/supervisor.py"]

    promote_root_from_slot()

    required_after, details_after = resolved_root_promotion_requirement(manifest)

    assert required_after is False
    assert details_after["effective_required"] is False
    assert details_after["effective_mismatched_paths"] == []


def test_restore_root_from_backup_restores_previous_root_files(monkeypatch, tmp_path) -> None:
    from adaos.services.core_update import promote_root_from_slot, restore_root_from_backup

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    (root_dir / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (slot_repo / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "supervisor.py").write_text("old\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr("adaos.services.core_update._repo_root", lambda: root_dir)

    write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    activate_slot("B")

    promotion = promote_root_from_slot()
    assert (root_dir / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "new\n"

    restored = restore_root_from_backup(backup_dir=str(promotion["backup_dir"]))

    assert restored["ok"] is True
    assert restored["target_root"] == str(root_dir.resolve())
    assert (root_dir / "src" / "adaos" / "apps" / "supervisor.py").read_text(encoding="utf-8") == "old\n"
