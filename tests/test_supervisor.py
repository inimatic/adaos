from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adaos.apps import supervisor
from adaos.services.core_update import read_plan, read_status, write_plan, write_status


@pytest.fixture(autouse=True)
def _allow_core_update_reactions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_DEV_ALLOW_CORE_UPDATE", "1")


def test_reconcile_update_status_marks_stale_attempt_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor, "rollback_to_previous_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "rollback_installed_skill_runtimes",
        lambda: {"ok": True, "total": 1, "failed_total": 0, "rollback_total": 1, "skills": []},
    )

    monkeypatch.setattr(supervisor.time, "time", lambda: 120.0)
    write_status(
        {
            "state": "restarting",
            "phase": "shutdown",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.update",
        }
    )
    write_plan({"state": "pending_restart", "target_rev": "rev2026", "expires_at": 9999999999.0})
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.update",
            "requested_at": 0.0,
            "transitioned_at": 10.0,
            "updated_at": 10.0,
        }
    )

    monkeypatch.setattr(supervisor.time, "time", lambda: 240.0)
    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "_served_by": "supervisor_fallback",
        }
    )

    assert payload["status"]["state"] == "failed"
    assert payload["status"]["phase"] == "shutdown"
    assert payload["status"]["restored_slot"] == "A"
    assert payload["status"]["rollback"]["ok"] is True
    assert payload["status"]["skill_runtime_rollback"]["rollback_total"] == 1
    assert payload["_served_by"] == "supervisor_timeout_recovery"
    assert read_plan() is None
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["contract_version"] == "1"
    assert attempt["authority"] == "supervisor"
    assert attempt["state"] == "failed"
    assert attempt["last_status"]["state"] == "failed"


def test_timeout_reconciliation_cannot_overwrite_advanced_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor.time, "time", lambda: 240.0)
    write_status(
        {
            "state": "restarting",
            "phase": "shutdown",
            "action": "update",
            "target_version": "target-build",
            "updated_at": 10.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_version": "target-build",
            "requested_at": 1.0,
            "transitioned_at": 10.0,
            "updated_at": 10.0,
        }
    )

    def _advance_during_rollback() -> str:
        write_status(
            {
                "state": "succeeded",
                "phase": "root_promoted",
                "action": "update",
                "target_version": "target-build",
                "updated_at": 230.0,
            }
        )
        return "B"

    monkeypatch.setattr(supervisor, "rollback_to_previous_slot", _advance_during_rollback)
    monkeypatch.setattr(supervisor, "rollback_installed_skill_runtimes", lambda: {})

    payload = supervisor._reconcile_update_status(
        {"ok": True, "status": read_status(), "_served_by": "supervisor_fallback"}
    )

    assert payload["_served_by"] == "supervisor_stale_timeout_write_suppressed"
    assert payload["reconciliation"]["reason"] == "transition_advanced_during_timeout_recovery"
    assert payload["status"]["state"] == "succeeded"
    assert read_status()["phase"] == "root_promoted"


def test_reconciliation_defers_while_transition_guard_is_held(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status({"state": "applying", "phase": "root_promotion", "updated_at": 10.0})
    supervisor._write_update_attempt(
        {"state": "active", "action": "update", "transitioned_at": 10.0, "updated_at": 10.0}
    )

    with supervisor._try_update_transition_guard(operation="test.root_promotion") as acquired:
        assert acquired is True
        payload = supervisor._reconcile_update_status(
            {"ok": True, "status": read_status(), "_served_by": "supervisor_fallback"}
        )

    assert payload["_served_by"] == "supervisor_transition_busy"
    assert payload["reconciliation"] == {
        "deferred": True,
        "retryable": True,
        "reason": "update_transition_guard_busy",
    }
    assert read_status()["state"] == "applying"


def test_timeout_rollback_defers_slot_cleanup_until_runtime_stop_is_confirmed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor, "rollback_to_previous_slot", lambda: "A")
    monkeypatch.setattr(supervisor, "rollback_installed_skill_runtimes", lambda: {})
    monkeypatch.setattr(
        supervisor,
        "remove_inactive_slot",
        lambda *_args, **_kwargs: pytest.fail("live target slot must not be removed by timeout reconciliation"),
    )
    monkeypatch.setattr(supervisor.time, "time", lambda: 120.0)
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_slot": "B",
            "target_rev": "rev2026",
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_slot": "B",
            "target_rev": "rev2026",
            "transitioned_at": 10.0,
        }
    )

    monkeypatch.setattr(supervisor.time, "time", lambda: 240.0)
    payload = supervisor._reconcile_update_status({"ok": True, "status": read_status()})

    cleanup = payload["status"]["slot_cleanup"]
    assert cleanup == {
        "ok": True,
        "removed": False,
        "deferred": True,
        "slot": "B",
        "reason": "runtime_stop_not_confirmed",
    }


def test_update_attempt_read_write_normalizes_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))

    written = supervisor._write_update_attempt(
        {
            "state": "ACTIVE",
            "action": "Update",
            "target_rev": "rev2026",
            "reason": "test.update",
            "requested_at": "100.0",
            "subsequent_transition_request": {"action": "update", "target_rev": "rev2027"},
        }
    )

    loaded = supervisor._read_update_attempt()

    assert written["contract_version"] == "1"
    assert written["authority"] == "supervisor"
    assert written["state"] == "active"
    assert written["action"] == "update"
    assert loaded == written


def test_reconcile_update_status_completes_attempt_on_terminal_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {"state": "succeeded", "phase": "validate", "updated_at": 499.0},
            "_served_by": "runtime",
        }
    )

    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["last_status"]["state"] == "succeeded"


def test_reconcile_update_status_ignores_stale_targetless_terminal_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "target_version": "1111111111111111111111111111111111111111",
            "git_commit": "1111111111111111111111111111111111111111",
            "git_short_commit": "1111111",
        },
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "2222222222222222222222222222222222222222",
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "succeeded",
                "phase": "validate",
                "updated_at": 455.0,
            },
            "_served_by": "runtime",
        }
    )

    assert payload["_served_by"] == "supervisor_stale_terminal_status_ignored"
    assert payload["status"]["state"] == "succeeded"
    assert not payload["status"].get("active_slot_target_mismatch")
    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "active"
    assert attempt.get("completion_reason") is None


def test_reconcile_update_status_rejects_terminal_success_for_wrong_active_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_version": "1111111111111111111111111111111111111111",
            "git_commit": "1111111111111111111111111111111111111111",
            "git_short_commit": "1111111",
        },
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "2222222222222222222222222222222222222222",
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "succeeded",
                "phase": "validate",
                "target_rev": "rev2026",
                "target_version": "2222222222222222222222222222222222222222",
                "updated_at": 499.0,
            },
            "_served_by": "runtime",
        }
    )

    assert payload["status"]["state"] == "failed"
    assert payload["status"]["active_slot_target_mismatch"] is True
    assert payload["_served_by"] == "supervisor_target_mismatch_recovery"
    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "failed"
    assert attempt["completion_reason"] == "active slot target mismatch"
    assert attempt["last_status"]["target_version"] == "2222222222222222222222222222222222222222"


def test_reconcile_update_status_clears_failed_attempt_after_terminal_success(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 700.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "build_version": "0.1.39+1.23592eb",
            "git_commit": "23592eb4b5889c7d880ec1f2ab189ff30e72c03d",
            "git_short_commit": "23592eb",
        },
    )
    supervisor._write_update_attempt(
        {
            "state": "failed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "23592eb4b5889c7d880ec1f2ab189ff30e72c03d",
            "requested_at": 600.0,
            "transitioned_at": 660.0,
            "updated_at": 690.0,
            "completed_at": 690.0,
            "completion_reason": "active slot target mismatch",
            "last_status": {
                "state": "failed",
                "phase": "validate",
                "target_slot": "B",
                "target_version": "23592eb4b5889c7d880ec1f2ab189ff30e72c03d",
                "reason": "active_slot_target_mismatch",
            },
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "succeeded",
                "phase": "validate",
                "target_rev": "rev2026",
                "target_version": "23592eb4b5889c7d880ec1f2ab189ff30e72c03d",
                "target_slot": "A",
                "message": "runtime boot validated on slot A",
                "updated_at": 699.0,
            },
            "_served_by": "runtime",
        }
    )

    assert payload["_served_by"] == "supervisor_failed_attempt_success_reconciled"
    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "terminal core update success reconciled"
    assert attempt["last_status"]["state"] == "succeeded"
    assert attempt["last_status"]["target_slot"] == "A"


def test_reconcile_update_status_restores_completed_update_after_later_runtime_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 720.0)
    target_version = "23592eb4b5889c7d880ec1f2ab189ff30e72c03d"
    manifest = {
        "slot": "B",
        "target_version": target_version,
        "git_commit": target_version,
    }
    monkeypatch.setattr(supervisor, "active_slot_manifest", lambda: dict(manifest))
    terminal_status = {
        "state": "succeeded",
        "phase": "validate",
        "action": "update",
        "target_rev": "rev2026",
        "target_version": target_version,
        "target_slot": "B",
        "message": "runtime boot validated on slot B",
        "updated_at": 690.0,
    }
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target_version,
            "requested_at": 600.0,
            "transitioned_at": 660.0,
            "updated_at": 700.0,
            "completed_at": 700.0,
            "completion_reason": "active slot target already active",
            "last_status": terminal_status,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "failed",
                "phase": "uvicorn.run",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": target_version,
                "message": "autostart runner failed during uvicorn.run",
                "error_type": "InvalidStateError",
                "error": "invalid state",
                "traceback": "asyncio proactor traceback",
                "updated_at": 710.0,
            },
            "runtime": {
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
            },
        }
    )

    assert payload["_served_by"] == "supervisor_post_update_runtime_failure_reconciled"
    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["phase"] == "validate"
    assert payload["status"]["target_slot"] == "B"
    assert payload["status"]["post_update_runtime_failure_reconciled"] is True
    assert payload["status"]["post_update_runtime_failure"]["error_type"] == "InvalidStateError"
    assert payload["attempt"]["state"] == "completed"


def test_reconcile_update_status_clears_failed_target_mismatch_after_slot_switch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 710.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "build_version": "0.1.77+1.4081501",
            "git_commit": "40815011428a3c6aa0ab46c46fb0dc322e998b3f",
            "git_short_commit": "4081501",
        },
    )
    supervisor._write_update_attempt(
        {
            "state": "failed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "40815011428a3c6aa0ab46c46fb0dc322e998b3f",
            "requested_at": 600.0,
            "transitioned_at": 660.0,
            "updated_at": 690.0,
            "completed_at": 690.0,
            "completion_reason": "active slot target mismatch",
            "last_status": {
                "state": "failed",
                "phase": "validate",
                "target_slot": "B",
                "target_version": "40815011428a3c6aa0ab46c46fb0dc322e998b3f",
                "active_slot_target_mismatch": True,
            },
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "failed",
                "phase": "validate",
                "target_rev": "rev2026",
                "target_version": "40815011428a3c6aa0ab46c46fb0dc322e998b3f",
                "target_slot": "B",
                "active_slot_target_mismatch": True,
                "updated_at": 699.0,
            },
            "runtime": {
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
            },
            "_served_by": "runtime",
        }
    )

    assert payload["_served_by"] == "supervisor_failed_target_mismatch_reconciled"
    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["target_slot"] == "A"
    assert payload["status"]["active_slot_target_mismatch"] is False
    assert payload["status"]["active_slot_target_mismatch_reconciled"] is True
    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "active slot target mismatch reconciled"
    assert attempt["last_status"]["state"] == "succeeded"


def test_sidecar_role_falls_back_to_load_config_when_ctx_config_is_missing(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token=None)

    class _Ctx:
        config = None

    monkeypatch.setattr(supervisor, "get_ctx", lambda: _Ctx())
    monkeypatch.setattr(supervisor, "load_config", lambda ctx=None: type("Conf", (), {"role": "hub"})())

    assert manager._sidecar_role() == "hub"


def test_sidecar_repo_root_prefers_shared_dotenv_project_root_over_venv_ctx_repo_root(monkeypatch, tmp_path) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token=None)
    project_root = tmp_path / "adaos"
    project_root.mkdir()
    (project_root / ".env").write_text("ADAOS_TOKEN=test\n", encoding="utf-8")
    (project_root / ".git").mkdir()
    venv_repo_root = tmp_path / "venv" / "lib" / "python3.11"
    (venv_repo_root / "src" / "adaos").mkdir(parents=True)

    class _Paths:
        def repo_root(self):
            return venv_repo_root

    class _Ctx:
        paths = _Paths()

    monkeypatch.setattr(supervisor, "get_ctx", lambda: _Ctx())
    monkeypatch.setenv("ADAOS_SHARED_DOTENV_PATH", str(project_root / ".env"))

    assert manager._sidecar_repo_root() == project_root.resolve()


def test_reconcile_update_status_completes_awaiting_root_restart_attempt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "succeeded",
                "phase": "validate",
                "root_restart_completed_at": 499.0,
                "updated_at": 499.0,
            },
            "_served_by": "runtime",
        }
    )

    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "root restart completed"
    assert attempt["last_status"]["root_restart_completed_at"] == 499.0


def test_reconcile_update_status_clears_stale_subsequent_marker_without_queued_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
            "subsequent_transition": False,
        }
    )
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "root_restart_completed_at": 499.0,
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 400.0,
            "subsequent_transition_action": "update",
            "subsequent_transition_target_rev": "stale-rev",
            "subsequent_transition_target_version": "stale-version",
            "updated_at": 499.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "_served_by": "runtime",
        }
    )

    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["last_status"]["subsequent_transition"] is False
    status = read_status()
    assert status["subsequent_transition"] is False
    assert status["subsequent_transition_requested_at"] is None
    assert "subsequent_transition_target_version" not in status


def test_reconcile_update_status_self_heals_orphaned_subsequent_marker_after_attempt_completed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_version": "current-build",
            "subsequent_transition": False,
            "updated_at": 499.0,
        }
    )
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "target_version": "current-build",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 400.0,
            "updated_at": 499.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {"ok": True, "status": read_status(), "_served_by": "runtime"}
    )

    assert payload["_served_by"] == "supervisor_orphaned_subsequent_recovery"
    assert payload["status"]["subsequent_transition"] is False
    assert read_status()["subsequent_transition"] is False


def test_root_restart_boot_finalize_requires_new_supervisor_generation() -> None:
    runtime = {
        "runtime_state": "ready",
        "listener_running": True,
        "runtime_api_ready": True,
        "active_slot": "B",
    }
    current_generation = {
        "state": "succeeded",
        "phase": "root_promoted",
        "target_slot": "B",
        "root_promotion_supervisor_instance_id": supervisor._SUPERVISOR_INSTANCE_ID,
    }
    next_generation = {
        **current_generation,
        "root_promotion_supervisor_instance_id": "previous-supervisor-instance",
    }

    assert supervisor._runtime_ready_for_boot_status_finalize(current_generation, runtime) is False
    assert supervisor._runtime_ready_for_boot_status_finalize(next_generation, runtime) is True


def test_root_restart_finalize_records_receiving_supervisor_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 7654)
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "target_slot": "B",
            "root_promotion_supervisor_instance_id": "previous-supervisor-instance",
        }
    )
    monkeypatch.setattr(
        supervisor,
        "finalize_runtime_boot_status",
        lambda **_kwargs: {
            "state": "succeeded",
            "phase": "validate",
            "target_slot": "B",
            "root_restart_completed_at": 501.0,
        },
    )

    finalized = supervisor._finalize_runtime_boot_status_from_supervisor()

    assert isinstance(finalized, dict)
    assert finalized["root_restart_completed_by_instance_id"] == supervisor._SUPERVISOR_INSTANCE_ID
    assert finalized["root_restart_completed_by_pid"] == 7654
    assert finalized["root_restart_completed_by_started_at"] == supervisor._SUPERVISOR_INSTANCE_STARTED_AT
    persisted = read_status()
    assert persisted["root_restart_completed_by_instance_id"] == supervisor._SUPERVISOR_INSTANCE_ID
    assert persisted["root_promotion_supervisor_instance_id"] == "previous-supervisor-instance"


def test_reconcile_update_status_keeps_root_promotion_pending_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "b5cbb1d5",
            "requested_at": 100.0,
            "transitioned_at": 110.0,
            "updated_at": 110.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "validated",
                "phase": "root_promotion_pending",
                "target_rev": "rev2026",
                "target_version": "b5cbb1d5",
                "target_slot": "A",
                "updated_at": 110.0,
            },
            "_served_by": "runtime",
        }
    )

    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "active"
    assert not attempt.get("completed_at")
    assert attempt.get("completion_reason") in {None, ""}


def test_observed_update_attempt_exposes_stale_last_status_without_mutating_it() -> None:
    attempt = {
        "state": "active",
        "target_version": "b5cbb1d5",
        "last_status": {
            "state": "countdown",
            "phase": "countdown",
            "target_version": "b5cbb1d5",
            "updated_at": 110.0,
        },
    }
    status = {
        "state": "validated",
        "phase": "root_promotion_pending",
        "action": "update",
        "target_version": "b5cbb1d5",
        "target_slot": "A",
        "updated_at": 500.0,
    }

    observed = supervisor._observed_update_attempt(attempt, status)

    assert isinstance(observed, dict)
    assert observed["last_status"]["phase"] == "countdown"
    assert observed["observed_status"]["phase"] == "root_promotion_pending"
    assert observed["observed_status"]["target_slot"] == "A"
    assert observed["last_status_matches_current"] is False
    assert observed["last_status_updated_at"] == 110.0
    assert "observed_status" not in attempt


def test_reconcile_update_status_marks_stale_awaiting_root_restart_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor, "finalize_runtime_boot_status", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor.time, "time", lambda: 120.0)
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.root_restart",
            "updated_at": 10.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.root_restart",
            "requested_at": 0.0,
            "transitioned_at": 10.0,
            "updated_at": 10.0,
        }
    )

    monkeypatch.setattr(supervisor.time, "time", lambda: 240.0)
    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
                "active_slot": "A",
            },
            "_served_by": "supervisor_fallback",
        }
    )

    assert payload["status"]["state"] == "failed"
    assert payload["status"]["phase"] == "root_restart_timeout"
    assert payload["_served_by"] == "supervisor_timeout_recovery"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "failed"
    assert attempt["completion_reason"] == "root restart timeout"


def test_reconcile_root_restart_timeout_after_runtime_self_heal(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 300.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "git_commit": "target-commit",
            "target_version": "target-commit",
        },
    )
    write_status(
        {
            "state": "failed",
            "phase": "root_restart_timeout",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "target-commit",
            "supervisor_timeout_at": 240.0,
            "updated_at": 240.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "failed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "target-commit",
            "completion_reason": "root restart timeout",
            "completed_at": 240.0,
            "updated_at": 240.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
                "active_slot": "B",
            },
            "_served_by": "supervisor_fallback",
        }
    )

    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["phase"] == "validate"
    assert payload["status"]["root_restart_timeout_reconciled"] is True
    assert payload["_served_by"] == "supervisor_root_restart_timeout_reconciled"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "root restart timeout reconciled after runtime recovery"


def test_reconcile_update_status_self_heals_stale_awaiting_root_restart_when_runtime_can_finalize(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "60")
    monkeypatch.setattr(supervisor.time, "time", lambda: 120.0)
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.root_restart",
            "target_slot": "A",
            "updated_at": 10.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "target_rev": "rev2026",
            "reason": "test.root_restart",
            "requested_at": 0.0,
            "transitioned_at": 10.0,
            "updated_at": 10.0,
        }
    )
    monkeypatch.setattr(
        supervisor,
        "finalize_runtime_boot_status",
        lambda **_kwargs: {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": "rev2026",
            "target_slot": "A",
            "root_restart_completed_at": 119.0,
            "updated_at": 119.0,
        },
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
                "active_slot": "A",
            },
            "_served_by": "supervisor_fallback",
        }
    )

    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["phase"] == "validate"
    assert payload["status"]["root_restart_completed_at"] == 119.0
    assert payload["_served_by"] == "supervisor_runtime_ready_finalize"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "root restart completed"


def test_reconcile_update_status_finalizes_stale_launch_when_runtime_is_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_rev": "rev2026",
            "target_slot": "B",
            "reason": "test.launch",
            "updated_at": 10.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "requested_at": 0.0,
            "transitioned_at": 10.0,
            "updated_at": 11.0,
        }
    )
    monkeypatch.setattr(
        supervisor,
        "finalize_runtime_boot_status",
        lambda **_kwargs: {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": "rev2026",
            "target_slot": "B",
            "validated_at": 120.0,
            "updated_at": 120.0,
        },
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "runtime_state": "ready",
                "runtime_api_ready": True,
                "listener_running": True,
                "active_slot": "B",
            },
            "_served_by": "supervisor_monitor",
        }
    )

    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["phase"] == "validate"
    assert payload["_served_by"] == "supervisor_runtime_ready_finalize"
    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"


def test_reconcile_update_status_does_not_finalize_ready_runtime_for_other_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_rev": "rev2026",
            "target_slot": "B",
            "reason": "test.launch",
            "updated_at": 10.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "requested_at": 0.0,
            "transitioned_at": 10.0,
            "updated_at": 11.0,
        }
    )
    monkeypatch.setattr(
        supervisor,
        "finalize_runtime_boot_status",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not finalize a different active slot")),
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "runtime_state": "ready",
                "runtime_api_ready": True,
                "listener_running": True,
                "active_slot": "A",
            },
            "_served_by": "supervisor_monitor",
        }
    )

    assert payload["status"]["state"] == "restarting"
    assert payload["_served_by"] == "supervisor_monitor"


def test_reconcile_update_status_clears_stale_candidate_prewarm_fields_when_root_restart_completes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "awaiting_restart": True,
            "restart_required": True,
            "candidate_prewarm_state": "starting",
            "candidate_prewarm_message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
            "candidate_prewarm_ready_at": 430.0,
            "requested_at": 450.0,
            "transitioned_at": 460.0,
            "updated_at": 460.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": {
                "state": "succeeded",
                "phase": "validate",
                "root_restart_completed_at": 499.0,
                "updated_at": 499.0,
            },
            "_served_by": "runtime",
        }
    )

    attempt = payload.get("attempt")
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["awaiting_restart"] is False
    assert attempt["restart_required"] is False
    assert attempt["candidate_prewarm_state"] is None
    assert attempt["candidate_prewarm_message"] is None
    assert attempt["candidate_prewarm_ready_at"] is None


def test_last_update_completion_at_ignores_idle_status() -> None:
    assert supervisor._last_update_completion_at({"state": "idle", "updated_at": 123.0}, None) == 0.0


def test_runtime_shutdown_request_timeout_scales_with_drain_window() -> None:
    assert supervisor._runtime_shutdown_request_timeout(drain_timeout_sec=10.0, signal_delay_sec=0.25) >= 12.0


def test_runtime_self_heal_restarts_when_managed_process_does_not_match_active_slot(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._stopping = False
    manager._managed_runtime_cwd = "/slots/A/repo"
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {"state": "restarting", "phase": "launch"})
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "B", "argv": ["/slots/B/venv/bin/python"], "cwd": "/slots/B/repo"},
    )
    monkeypatch.setattr(
        supervisor,
        "_proc_details",
        lambda proc, cwd_hint=None: {
            "managed_pid": 4321,
            "managed_alive": True,
            "managed_cmdline": ["/slots/A/venv/bin/python", "-m", "adaos.apps.autostart_runner"],
            "managed_executable": "/slots/A/venv/bin/python",
            "managed_cwd": "/slots/A/repo",
        },
    )

    decision = manager._runtime_self_heal_decision(now=120.0)

    assert isinstance(decision, dict)
    assert decision["reason"] == "supervisor.runtime.slot_mismatch"
    assert decision["active_slot"] == "B"
    assert decision["managed_executable"] == "/slots/A/venv/bin/python"
    assert decision["expected_managed_executable"] == "/slots/B/venv/bin/python"


def test_hub_root_watchdog_requests_reconnect_when_root_control_is_down(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {
                "hub_root": {
                    "effective_status": "down",
                    "effective_state": "down",
                }
            },
            "hub_root_transport_strategy": {
                "last_event": "failure",
                "last_summary": "watchdog._reading_task",
            },
        },
        now=100.0,
    )

    assert isinstance(decision, dict)
    assert decision["reason"] == "supervisor.hub_root.watchdog_reconnect"
    assert decision["action"] == "runtime_reconnect"
    assert decision["transport_owner"] == "runtime"
    assert decision["root_control_status"] == "down"
    assert decision["last_summary"] == "watchdog._reading_task"


def test_hub_root_watchdog_respects_reconnect_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_RECONNECT_COOLDOWN_SEC", "30")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._hub_root_watchdog_last_reconnect_at = 95.0
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
        },
        now=100.0,
    )

    assert decision is None
    assert manager._hub_root_watchdog_last_state == "cooldown"
    assert "cooldown" in str(manager._hub_root_watchdog_last_reason)


def test_hub_root_watchdog_uses_fresh_root_perspective_probe(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    manager._hub_root_root_probe_last_result = {
        "ok": True,
        "state": "ready",
        "age_sec": 3.0,
        "target_id": "hub:sn_test",
    }

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
        },
        now=100.0,
    )

    assert decision is None
    assert manager._hub_root_watchdog_last_state == "root_perspective_ready"
    assert "fresh hub control report" in str(manager._hub_root_watchdog_last_reason)


def test_hub_root_watchdog_ignores_fresh_root_probe_when_report_route_is_down(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    manager._hub_root_root_probe_last_result = {
        "ok": True,
        "state": "ready",
        "age_sec": 1.0,
        "target_id": "hub:sn_test",
        "root_control_status": "down",
        "route_status": "degraded",
    }

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
        },
        now=100.0,
    )

    assert decision is not None
    assert decision["action"] == "runtime_reconnect"
    assert decision["root_perspective_probe"]["root_control_status"] == "down"
    assert decision["root_perspective_probe"]["route_status"] == "degraded"
    assert manager._hub_root_watchdog_last_state != "root_perspective_ready"


def test_hub_root_root_probe_reads_fresh_control_report(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Config:
        subnet_id = "sn_test"
        zone_id = "eu"

        class root_settings:
            base_url = "https://api.inimatic.com"

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ok": True,
                "reports": [
                    {
                        "event_id": "evt_1",
                        "server_time_utc": "2026-06-17T10:00:05+00:00",
                        "report": {
                            "reported_at": "2026-06-17T10:00:00+00:00",
                            "root_control": {"status": "ready"},
                            "route": {"status": "ready"},
                            "transport": {"assessment_state": "stable"},
                            "runtime": {"runtime_instance_id": "rt-a"},
                        },
                    }
                ],
            }

    requests_seen: list[dict[str, object]] = []

    class _Session:
        trust_env = True

        def get(self, url, **kwargs):
            requests_seen.append({"url": url, **kwargs})
            return _Response()

        def close(self):
            return None

    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "root-token")
    monkeypatch.setattr(supervisor, "load_config", lambda: _Config())
    monkeypatch.setattr(supervisor.requests, "Session", _Session)

    result = manager._probe_hub_root_from_root_once(now=1781690410.0)

    assert result["state"] == "ready"
    assert result["target_id"] == "hub:sn_test"
    assert result["age_sec"] == 5.0
    assert result["root_control_status"] == "ready"
    assert requests_seen[0]["url"] == "https://api.inimatic.com/v1/hubs/control/reports"
    assert requests_seen[0]["params"] == {"hub_id": "sn_test"}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"ok": True}, True),
        ({"ok": True, "readiness": {"state": "starting", "ready": False}}, False),
        ({"ok": True, "readiness": {"state": "ready", "ready": True}}, True),
        ({"ok": True, "readiness": {"state": "failed", "ready": False}}, False),
    ],
)
def test_runtime_api_ready_honors_explicit_boot_readiness(monkeypatch, payload, expected) -> None:
    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(supervisor.requests, "get", lambda *_args, **_kwargs: _Response())

    assert supervisor._runtime_api_ready("http://127.0.0.1:8777", token=None) is expected


@pytest.mark.parametrize(
    ("status_code", "payload", "stale", "expected"),
    [
        (200, {"ok": True}, "0", True),
        (200, {"ok": True}, "1", True),
        (200, {"ok": True}, "unavailable", False),
        (503, {"ok": False}, "unavailable", False),
    ],
)
def test_runtime_beacon_ready_requires_usable_bounded_response(
    monkeypatch, status_code, payload, stale, expected
) -> None:
    class _Response:
        headers = {"X-AdaOS-Runtime-Stale": stale}

        def __init__(self):
            self.status_code = status_code

        @staticmethod
        def json():
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(supervisor.requests, "get", lambda *_args, **_kwargs: _Response())

    assert supervisor._runtime_beacon_ready("http://127.0.0.1:8778", token="token") is expected


def test_hub_root_root_probe_accepts_root_items_payload(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Config:
        subnet_id = "sn_test"
        zone_id = "eu"

        class root_settings:
            base_url = "https://api.inimatic.com"

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ok": True,
                "items": [
                    {
                        "hub_id": "sn_test",
                        "report": {
                            "target_id": "hub:sn_test",
                            "root_received_at": "2026-06-17T10:00:05Z",
                            "root_control": {"status": "ready"},
                            "route": {"status": "ready"},
                            "transport": {"assessment_state": "stable"},
                            "runtime_instance_id": "rt-a",
                            "transition_role": "active",
                        },
                    }
                ],
            }

    requests_seen: list[dict[str, object]] = []

    class _Session:
        trust_env = True

        def get(self, url, **kwargs):
            requests_seen.append({"url": url, **kwargs})
            return _Response()

        def close(self):
            return None

    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "root-token")
    monkeypatch.setattr(supervisor, "load_config", lambda: _Config())
    monkeypatch.setattr(supervisor.requests, "Session", _Session)

    result = manager._probe_hub_root_from_root_once(now=1781690410.0)

    assert result["state"] == "ready"
    assert result["target_id"] == "hub:sn_test"
    assert result["lookup_hub_id"] == "sn_test"
    assert result["age_sec"] == 5.0
    assert result["runtime_instance_id"] == "rt-a"
    assert result["transition_role"] == "active"
    assert requests_seen[0]["params"] == {"hub_id": "sn_test"}


def test_hub_root_root_probe_uses_node_role_when_transition_role_is_active(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "active"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    calls: list[dict[str, object]] = []

    def _probe_once(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "state": "ready", "reason": "fresh"}

    monkeypatch.setattr(manager, "_probe_hub_root_from_root_once", _probe_once)

    result = asyncio.run(manager._maybe_probe_hub_root_from_root(force=True))

    assert result == {"ok": True, "state": "ready", "reason": "fresh"}
    assert len(calls) == 1
    assert manager._hub_root_root_probe_last_state == "ready"


def test_hub_root_watchdog_invokes_runtime_reconnect(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_TIMEOUT_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    calls: list[dict[str, object]] = []
    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.5: {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
        },
    )

    def _request(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True}

    monkeypatch.setattr(manager, "_runtime_request_json", _request)

    asyncio.run(manager._maybe_reconnect_hub_root_from_watchdog())

    assert len(calls) == 1
    assert calls[0]["path"] == "/api/node/hub-root/reconnect"
    assert manager._hub_root_watchdog_reconnect_total == 1
    assert manager._hub_root_watchdog_last_result["result"]["ok"] is True
    assert manager._hub_root_watchdog_last_result["verification"]["ok"] is False


def test_supervisor_reliability_probe_uses_nonblocking_configured_deadline(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUPERVISOR_RELIABILITY_PROBE_TIMEOUT_SEC", "4.5")
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    seen: list[float] = []

    def _payload(*, timeout: float = 0.0) -> dict[str, object]:
        seen.append(timeout)
        return {"node": {"role": "hub"}}

    monkeypatch.setattr(manager, "_runtime_reliability_payload", _payload)

    result = asyncio.run(manager._runtime_reliability_payload_async())

    assert result == {"node": {"role": "hub"}}
    assert seen == [4.5]


def test_supervisor_reliability_probe_uses_compact_channel_endpoint(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    calls: list[dict[str, object]] = []

    def _request(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {"runtime": {"node": {"role": "hub"}}}

    monkeypatch.setattr(manager, "_runtime_request_json", _request)

    payload = manager._runtime_reliability_payload(timeout=3.0)

    assert payload == {"node": {"role": "hub"}}
    assert calls == [
        {"path": "/api/node/reliability/supervisor-channel", "timeout": 3.0},
    ]


def test_supervisor_update_gate_uses_compact_runtime_endpoint(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    calls: list[dict[str, object]] = []

    def _request(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {"runtime": {"skill_runtime_migration": {"pending": False}}}

    monkeypatch.setattr(manager, "_runtime_request_json", _request)

    payload = manager._runtime_update_gate_payload(timeout=3.0)

    assert payload == {"skill_runtime_migration": {"pending": False}}
    assert calls == [{"path": "/api/node/reliability/update-gate", "timeout": 3.0}]


def test_hub_root_watchdog_resets_browser_route_when_root_control_is_ready(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_ROUTE_DEGRADED_RESET", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_TIMEOUT_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    calls: list[dict[str, object]] = []
    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.5: {
            "readiness_tree": {
                "root_control": {"status": "ready"},
                "route": {"status": "degraded"},
            },
            "channel_overview": {
                "hub_root": {"effective_status": "ready", "effective_state": "stable"},
                "hub_root_browser": {"effective_status": "degraded", "effective_state": "unstable"},
            },
        },
    )

    def _request(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True}

    monkeypatch.setattr(manager, "_runtime_request_json", _request)

    asyncio.run(manager._maybe_reconnect_hub_root_from_watchdog())

    assert len(calls) == 1
    assert calls[0]["path"] == "/api/node/hub-root/route-reset"
    assert calls[0]["payload"] == {
        "reason": "supervisor_route_watchdog",
        "notify_browser": True,
    }
    assert manager._hub_root_watchdog_last_result["action"] == "runtime_route_reset"
    assert manager._hub_root_watchdog_last_result["decision"]["hub_root_status"] == "ready"
    assert manager._hub_root_watchdog_last_result["decision"]["hub_root_browser_status"] == "degraded"


def test_hub_root_watchdog_resets_route_without_restarting_healthy_sidecar(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_ROUTE_DEGRADED_RESET", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_TIMEOUT_SEC", "0")
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )

    class _Proc:
        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.5: {
            "readiness_tree": {
                "root_control": {"status": "ready"},
                "route": {"status": "degraded"},
            },
            "channel_overview": {
                "hub_root": {"effective_status": "ready", "effective_state": "stable"},
                "hub_root_browser": {"effective_status": "degraded", "effective_state": "flapping"},
            },
            "required_upstream_link": {
                "kind": "hub_root",
                "state": "degraded",
                "current_owner": "sidecar",
                "sidecar_enabled": True,
            },
        },
    )
    requests: list[dict[str, object]] = []
    sidecar_restarts: list[dict[str, object]] = []
    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: requests.append(dict(kwargs)) or {"ok": True},
    )

    async def _restart_sidecar(**kwargs):
        sidecar_restarts.append(dict(kwargs))
        return {"ok": True}

    monkeypatch.setattr(manager, "restart_sidecar", _restart_sidecar)

    asyncio.run(manager._maybe_reconnect_hub_root_from_watchdog())

    assert sidecar_restarts == []
    assert requests[0]["path"] == "/api/node/hub-root/route-reset"
    assert manager._hub_root_watchdog_last_result["action"] == "runtime_route_reset"


def test_hub_root_watchdog_preserves_runtime_route_on_degraded_browser_route_by_default(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    monkeypatch.delenv("ADAOS_SUPERVISOR_HUB_ROOT_ROUTE_DEGRADED_RESET", raising=False)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    calls: list[dict[str, object]] = []
    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.5: {
            "readiness_tree": {
                "root_control": {"status": "ready"},
                "route": {"status": "degraded"},
            },
            "channel_overview": {
                "hub_root": {"effective_status": "ready", "effective_state": "stable"},
                "hub_root_browser": {"effective_status": "degraded", "effective_state": "flapping"},
            },
        },
    )
    monkeypatch.setattr(manager, "_runtime_request_json", lambda **kwargs: calls.append(dict(kwargs)) or {"ok": True})

    asyncio.run(manager._maybe_reconnect_hub_root_from_watchdog())

    assert len(calls) == 1
    assert calls[0]["path"] == "/api/admin/update/reconcile"
    assert calls[0]["payload"]["reason"] == "supervisor.hub_root.periodic_core_update_reconcile"
    assert manager._hub_root_watchdog_last_state == "degraded"
    assert manager._hub_root_watchdog_last_reason == "browser route degraded; preserving active runtime-owned tunnels"
    assert manager._hub_root_watchdog_reconnect_total == 0


def test_periodic_core_update_reconcile_uses_local_runtime_when_hub_root_route_is_down(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SUPERVISOR_PERIODIC_CORE_UPDATE_RECONCILE", raising=False)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: calls.append(dict(kwargs)) or {"ok": True, "needs_update": False},
    )
    runtime = {
        "readiness_tree": {"root_control": {"status": "down"}},
        "channel_overview": {"hub_root": {"effective_status": "down"}},
    }

    result = asyncio.run(manager._maybe_reconcile_hub_core_update_periodic(runtime))

    assert result is not None
    assert result["result"]["ok"] is True
    assert calls[0]["path"] == "/api/admin/update/reconcile"
    assert result["verification"]["state"] == "local_runtime_api_ready"
    assert result["verification"]["source"] == "supervisor.periodic_core_update_reconcile.direct_root_mtls"


def test_hub_root_watchdog_requests_runtime_reconnect_when_sidecar_owns_transport(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_TIMEOUT_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    snapshots = [
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
        },
        {
            "readiness_tree": {
                "root_control": {"status": "ready"},
                "route": {"status": "ready"},
            },
            "channel_overview": {
                "hub_root": {"effective_status": "ready", "effective_state": "stable"},
                "hub_root_browser": {"effective_status": "ready", "effective_state": "stable"},
            },
        },
    ]

    def _runtime_payload(timeout=1.5):
        if len(snapshots) > 1:
            return snapshots.pop(0)
        return snapshots[0]

    sidecar_calls: list[dict[str, object]] = []
    runtime_calls: list[dict[str, object]] = []

    async def _restart_sidecar(**kwargs):
        sidecar_calls.append(dict(kwargs))
        return {"ok": True, "restart": {"ok": True}, "reconnect": {"ok": True}}

    monkeypatch.setattr(manager, "_runtime_reliability_payload", _runtime_payload)
    monkeypatch.setattr(manager, "restart_sidecar", _restart_sidecar)
    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: runtime_calls.append(dict(kwargs)) or {"ok": True},
    )

    asyncio.run(manager._maybe_reconnect_hub_root_from_watchdog())

    assert sidecar_calls == []
    assert runtime_calls[0]["path"] == "/api/node/hub-root/reconnect"
    assert manager._hub_root_watchdog_last_result["action"] == "runtime_reconnect"
    assert manager._hub_root_watchdog_last_result["verification"]["ok"] is True
    assert manager._hub_root_watchdog_last_state == "ready"
    events = supervisor._read_jsonl_tail(supervisor._supervisor_hub_root_watchdog_log_path(), limit=5)
    assert events[-1]["action"] == "runtime_reconnect"
    assert events[-1]["verification"]["ok"] is True


def test_hub_root_watchdog_waits_for_fresh_runtime_recovery_attempt(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
            "hub_root_transport_strategy": {
                "last_event": "attempt",
                "last_attempt_ago_s": 1.0,
                "updated_ago_s": 0.5,
            },
        },
        now=100.0,
    )

    assert decision is None
    assert manager._hub_root_watchdog_last_state == "runtime_recovery_in_progress"
    assert "attempt" in str(manager._hub_root_watchdog_last_reason)


def test_hub_root_watchdog_rearms_stale_runtime_recovery_attempt(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_HUB_ROOT_RECONNECT_COOLDOWN_SEC", "30")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")

    decision = manager._hub_root_watchdog_decision(
        {
            "readiness_tree": {"root_control": {"status": "down"}},
            "channel_overview": {"hub_root": {"effective_status": "down"}},
            "hub_root_transport_strategy": {
                "last_event": "attempt",
                "last_attempt_ago_s": 45.0,
                "updated_ago_s": 45.0,
            },
        },
        now=100.0,
    )

    assert decision is not None
    assert decision["action"] == "runtime_reconnect"


def test_watchdog_payloads_stay_light_when_previous_state_is_recursive(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._hub_root_watchdog_last_result = {
        "requested_at": 1.0,
        "action": "runtime_reconnect",
        "decision": {
            "required_upstream_link": {
                "kind": "hub_root",
                "watchdog": {
                    "recent_events": [{"payload": "x" * 4096}],
                    "last_result": {"decision": {"watchdog": {"recent_events": []}}},
                },
            },
            "channel_before": {"root_control_status": "down", "raw": "y" * 4096},
        },
        "result": {"ok": True, "payload": {"raw": "z" * 4096}},
        "verification": {"ok": False, "channel": {"root_control_status": "down", "raw": "w" * 4096}},
    }

    required_link = manager._required_upstream_link_state_payload(role="hub")
    compact = supervisor._compact_watchdog_last_result(manager._hub_root_watchdog_last_result)

    assert "recent_events" not in required_link["watchdog"]
    assert "watchdog" not in compact["decision"]["required_upstream_link"]
    assert "channel_before" not in compact["decision"]
    assert "raw" not in compact["verification"]["channel"]
    assert "payload" not in compact["result"]


def test_required_upstream_link_uses_node_role_before_transition_role(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "active"
    manager._hub_root_watchdog_last_state = "ready"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(supervisor, "realtime_sidecar_enabled", lambda *, role=None: role == "hub")

    payload = manager._required_upstream_link_state_payload()

    assert payload["kind"] == "hub_root"
    assert payload["role"] == "hub"
    assert payload["sidecar_enabled"] is True
    assert payload["current_owner"] == "sidecar"


def test_required_upstream_link_uses_ready_sidecar_handoff_over_stale_route_degradation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._hub_root_watchdog_last_state = "degraded"
    manager._hub_root_watchdog_last_reason = "browser route degraded; preserving active runtime-owned tunnels"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(supervisor, "realtime_sidecar_enabled", lambda *, role=None: role == "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_sidecar_runtime_payload",
        lambda: {
            "enabled": True,
            "status": "ready",
            "remote_session_state": "ready",
            "transport_ready": True,
            "route_tunnel_contract": {
                "ws": {
                    "current_owner": "sidecar",
                    "listener_ready": True,
                    "handoff_ready": True,
                    "blockers": [],
                },
                "yws": {
                    "current_owner": "sidecar",
                    "listener_ready": True,
                    "handoff_ready": True,
                    "blockers": [],
                },
            },
        },
    )

    payload = manager._required_upstream_link_state_payload(role="hub")

    assert payload["state"] == "ready"
    assert payload["ready"] is True
    assert payload["handoff_state"] == "ready"
    assert payload["handoff_ready"] is True
    assert payload["served_by"] == "supervisor_sidecar"
    assert payload["watchdog"]["last_state"] == "degraded"


def test_required_upstream_link_does_not_hide_failed_sidecar_transport(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._hub_root_watchdog_last_state = "degraded"
    manager._hub_root_watchdog_last_reason = "browser route degraded; preserving active runtime-owned tunnels"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(supervisor, "realtime_sidecar_enabled", lambda *, role=None: role == "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_sidecar_runtime_payload",
        lambda: {
            "enabled": True,
            "status": "degraded",
            "remote_session_state": "down",
            "transport_ready": False,
            "route_tunnel_contract": {
                "ws": {"current_owner": "sidecar", "listener_ready": True, "handoff_ready": True},
                "yws": {"current_owner": "sidecar", "listener_ready": True, "handoff_ready": True},
            },
        },
    )

    payload = manager._required_upstream_link_state_payload(role="hub")

    assert payload["state"] == "degraded"
    assert payload["ready"] is False
    assert payload["handoff_ready"] is False
    assert payload["served_by"] == "supervisor"


def test_read_jsonl_tail_uses_bounded_tail_window(tmp_path) -> None:
    path = tmp_path / "watchdog.jsonl"
    lines = [{"i": i, "payload": "x" * 20} for i in range(10)]
    path.write_text("\n".join(supervisor.json.dumps(item) for item in lines) + "\n", encoding="utf-8")

    tail = supervisor._read_jsonl_tail(path, limit=2, max_bytes=256)

    assert [item["i"] for item in tail] == [8, 9]


def test_member_hub_watchdog_requests_reconnect_when_member_link_is_down(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")

    decision = manager._member_hub_watchdog_decision(
        {
            "node": {"role": "member"},
            "readiness_tree": {
                "route": {"status": "down"},
                "hub_member": {"status": "down"},
            },
            "hub_member_connection_state": {
                "state": "disconnected",
                "assessment": {"state": "degraded", "reason": "member_link_down"},
                "hub": {
                    "connected": False,
                    "hub_url": "https://ru.api.inimatic.com/hubs/sn_demo",
                },
            },
        },
        now=100.0,
    )

    assert isinstance(decision, dict)
    assert decision["reason"] == "supervisor.member_hub.watchdog_reconnect"
    assert decision["action"] == "runtime_reconnect"
    assert decision["transport_owner"] == "runtime"
    assert decision["member_state"] == "disconnected"
    assert decision["continuity_mode"] == "runtime_bound"
    assert decision["handoff_state"] == "unknown"


def test_member_hub_watchdog_accepts_node_connectivity_when_channel_detail_lags(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")

    decision = manager._member_hub_watchdog_decision(
        {
            "node": {
                "role": "member",
                "connected_to_hub": True,
                "connected_to_subnet": True,
            },
            "hub_member_connection_state": {
                "state": "connected",
                "hub": {"connected": False},
            },
        },
        now=100.0,
    )

    assert decision is None
    assert manager._member_hub_watchdog_last_state == "ready"
    assert manager._member_hub_watchdog_last_reason == "member-hub link is connected"


def test_member_hub_watchdog_uses_runtime_required_upstream_link_context(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")

    decision = manager._member_hub_watchdog_decision(
        {
            "node": {"role": "member"},
            "required_upstream_link": {
                "kind": "member_hub",
                "current_owner": "sidecar",
                "planned_owner": "sidecar",
                "continuity_mode": "slot_sticky",
                "handoff_state": "ready",
                "handoff_ready": True,
                "recovery_policy": {
                    "on_runtime_restart": "preserve_sidecar",
                    "while_owner_runtime": "runtime_reconnect",
                    "while_owner_sidecar": "preserve_sidecar",
                },
                "sidecar_enabled": True,
                "blockers": [],
            },
            "readiness_tree": {
                "route": {"status": "down"},
                "hub_member": {"status": "down"},
            },
            "hub_member_connection_state": {
                "state": "disconnected",
                "assessment": {"state": "degraded", "reason": "member_link_down"},
                "hub": {
                    "connected": False,
                    "hub_url": "https://ru.api.inimatic.com/hubs/sn_demo",
                },
            },
        },
        now=100.0,
    )

    assert isinstance(decision, dict)
    assert decision["transport_owner"] == "sidecar"
    assert decision["continuity_mode"] == "slot_sticky"
    assert decision["handoff_state"] == "ready"
    assert decision["handoff_ready"] is True
    assert decision["recovery_policy"]["on_runtime_restart"] == "preserve_sidecar"
    assert decision["required_upstream_link"]["current_owner"] == "sidecar"


def test_member_hub_watchdog_skips_recovery_during_restart_transition(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")

    decision = manager._member_hub_watchdog_decision(
        {
            "node": {"role": "member"},
            "hub_member_connection_state": {
                "state": "restarting",
                "assessment": {"state": "degraded", "reason": "restarting"},
                "hub": {
                    "connected": False,
                    "transition_state": "restarting",
                    "transition_reason": "core update launch",
                },
            },
        },
        now=100.0,
    )

    assert decision is None
    assert manager._member_hub_watchdog_last_state == "restarting"
    assert manager._member_hub_watchdog_last_reason == "core update launch"


def test_member_hub_watchdog_invokes_runtime_reconnect(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMBER_HUB_VERIFY_TIMEOUT_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        @staticmethod
        def poll():
            return None

    calls: list[dict[str, object]] = []
    manager._proc = _Proc()
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.5: {
            "node": {"role": "member"},
            "readiness_tree": {
                "route": {"status": "down"},
                "hub_member": {"status": "down"},
            },
            "hub_member_connection_state": {
                "state": "disconnected",
                "assessment": {"state": "degraded", "reason": "member_link_down"},
                "hub": {
                    "connected": False,
                    "hub_url": "https://ru.api.inimatic.com/hubs/sn_demo",
                },
            },
        },
    )

    def _request(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(manager, "_runtime_request_json", _request)

    asyncio.run(manager._maybe_reconnect_member_hub_from_watchdog())

    assert len(calls) == 1
    assert calls[0]["path"] == "/api/node/member-hub/reconnect"
    assert manager._member_hub_watchdog_reconnect_total == 1
    assert manager._member_hub_watchdog_last_result["result"]["accepted"] is True
    assert manager._member_hub_watchdog_last_result["verification"]["ok"] is False
    events = supervisor._read_jsonl_tail(supervisor._supervisor_member_hub_watchdog_log_path(), limit=5)
    assert events[-1]["action"] == "runtime_reconnect"


def test_required_upstream_link_maintenance_dispatches_to_member_watchdog(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "member"
    calls: list[str] = []

    async def _member() -> None:
        calls.append("member")

    async def _hub() -> None:
        calls.append("hub")

    monkeypatch.setattr(manager, "_maybe_reconnect_member_hub_from_watchdog", _member)
    monkeypatch.setattr(manager, "_maybe_reconnect_hub_root_from_watchdog", _hub)

    asyncio.run(manager._maybe_maintain_required_upstream_link())

    assert calls == ["member"]


def test_required_upstream_link_maintenance_ignores_active_transition_marker_for_member(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "active"
    calls: list[str] = []

    async def _member() -> None:
        calls.append("member")

    async def _hub() -> None:
        calls.append("hub")

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "member")
    monkeypatch.setattr(manager, "_maybe_reconnect_member_hub_from_watchdog", _member)
    monkeypatch.setattr(manager, "_maybe_reconnect_hub_root_from_watchdog", _hub)

    asyncio.run(manager._maybe_maintain_required_upstream_link())

    assert calls == ["member"]


def test_required_upstream_link_snapshot_prefers_runtime_payload(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    payload = manager._required_upstream_link_snapshot(
        runtime={
            "required_upstream_link": {
                "kind": "member_hub",
                "current_owner": "sidecar",
                "handoff_state": "ready",
            }
        },
        role="member",
    )

    assert payload["kind"] == "member_hub"
    assert payload["current_owner"] == "sidecar"
    assert payload["handoff_state"] == "ready"


def test_required_upstream_link_maintenance_dispatches_to_hub_watchdog(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "hub"
    calls: list[str] = []

    async def _member() -> None:
        calls.append("member")

    async def _hub() -> None:
        calls.append("hub")

    monkeypatch.setattr(manager, "_maybe_reconnect_member_hub_from_watchdog", _member)
    monkeypatch.setattr(manager, "_maybe_reconnect_hub_root_from_watchdog", _hub)

    asyncio.run(manager._maybe_maintain_required_upstream_link())

    assert calls == ["hub"]


def test_required_upstream_link_maintenance_throttles_runtime_snapshot_poll(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPSTREAM_WATCHDOG_POLL_INTERVAL_SEC", "10")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_transition_role = "hub"
    clock = {"now": 100.0}
    calls: list[float] = []

    async def _hub() -> None:
        calls.append(clock["now"])

    monkeypatch.setattr(supervisor.time, "time", lambda: clock["now"])
    monkeypatch.setattr(manager, "_maybe_reconnect_hub_root_from_watchdog", _hub)

    asyncio.run(manager._maybe_maintain_required_upstream_link())
    clock["now"] = 101.0
    asyncio.run(manager._maybe_maintain_required_upstream_link())
    clock["now"] = 110.0
    asyncio.run(manager._maybe_maintain_required_upstream_link())

    assert calls == [100.0, 110.0]


def test_active_skill_runtime_migration_uses_cross_process_lease(monkeypatch, tmp_path) -> None:
    from adaos.apps import supervisor as supervisor_module
    from adaos.services.skill import runtime_migration_worker

    ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: tmp_path))
    monkeypatch.setattr(supervisor_module, "current_base_dir", lambda: tmp_path)
    migration_dir = tmp_path / "state" / "skill_runtime_migration"
    migration_dir.mkdir(parents=True)
    (migration_dir / "status.json").write_text(
        json.dumps(
            {
                "state": "running",
                "phase": "migrate",
                "pending": True,
                "operation_id": "skill-migrate-test",
                "worker_pid": 123,
            }
        ),
        encoding="utf-8",
    )
    lease = runtime_migration_worker._try_acquire_global_lease(ctx, operation_id="skill-migrate-test")
    assert lease is not None
    try:
        active = supervisor_module._active_skill_runtime_migration()
        assert active is not None
        assert active["operation_id"] == "skill-migrate-test"
    finally:
        runtime_migration_worker._release_global_lease(lease)

    assert supervisor_module._active_skill_runtime_migration() is None


def test_supervisor_start_update_and_cancel(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "0")
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 123.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is True
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["state"] == "active"
        assert attempt["action"] == "update"
        cancelled = await manager.cancel_update(reason="test.cancel")
        assert cancelled["accepted"] is True
        assert cancelled["status"]["state"] == "cancelled"
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["state"] == "cancelled"

    asyncio.run(_exercise())


def test_supervisor_defers_update_while_skill_migration_is_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "0")
    monkeypatch.setattr(
        supervisor,
        "_active_skill_runtime_migration",
        lambda: {
            "operation_id": "skill-migrate-active",
            "state": "running",
            "phase": "migrate",
            "pending": True,
            "current": {"skill": "slideshow_skill", "stage": "refresh_runtime"},
            "worker_pid": 321,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="a" * 40,
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is False
        assert result["deferred"] is True
        assert result["retryable"] is True
        assert result["reason"] == "skill_runtime_migration_active"
        assert result["migration"]["current"]["skill"] == "slideshow_skill"
        assert manager._update_task is None

    asyncio.run(_exercise())


def test_supervisor_status_exposes_core_skill_workload_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(supervisor, "current_base_dir", lambda: tmp_path)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_runtime_state_payload", lambda **_kwargs: {"ok": True})

    status = manager.status()
    assert status["workload_admission"]["core_update_holds_skill_migration_gate"] is False

    manager._skill_runtime_migration_gate_lease = object()
    status = manager.status()
    assert status["workload_admission"]["core_update_holds_skill_migration_gate"] is True
    assert status["workload_admission"]["skill_migration_lease_path"].endswith("worker.lock")


def test_supervisor_status_reads_compact_in_memory_projection_without_io(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._publish_status_snapshot(
        {
            "ok": True,
            "supervisor_pid": 11,
            "runtime_state": "ready",
            "runtime_api_ready": True,
            "listener_running": True,
            "active_manifest": {
                "slot": "A",
                "git_short_commit": "abc1234",
                "env": {"SHOULD_NOT_BE_EXPOSED": "x" * 1000},
            },
            "bootstrap_update": {
                "required": True,
                "changed_paths": [f"path-{index}" for index in range(100)],
            },
            "sidecar": {
                "enabled": True,
                "role": "hub",
                "process": {
                    "managed_pid": 22,
                    "listener_running": True,
                    "listener_process_relationship": "managed_descendant",
                    "route_tunnel_contract": {"large": "x" * 1000},
                },
                "health": {"last_probe_ok": True, "consecutive_failures": 0},
                "code": {"fingerprint": "next", "active_fingerprint": "current"},
                "restart_policy": {"pending_code_fingerprint": "next"},
                "sync": {"last_sync_changed_paths": ["one.py"]},
                "transition": {
                    "in_progress": True,
                    "transition_id": "sidecar-test",
                    "source": "operator",
                    "reason": "supervisor.sidecar.restart",
                    "started_at": 123.0,
                    "internal_context": "x" * 1000,
                },
            },
        },
        update_attempt={
            "state": "active",
            "action": "update",
            "observed_status": {"large": "x" * 1000},
        },
        reason="test_projection",
    )

    def _unexpected_io(*args, **kwargs):
        raise AssertionError("status read must not collect diagnostics")

    monkeypatch.setattr(manager, "_runtime_state_payload", _unexpected_io)
    monkeypatch.setattr(supervisor, "_read_json", _unexpected_io)
    monkeypatch.setattr(supervisor, "_read_update_attempt", _unexpected_io)
    monkeypatch.setattr(supervisor, "read_core_update_status", _unexpected_io)

    first = manager.status()
    second = manager.status()

    assert first["runtime_state"] == "ready"
    assert first["status_read_model"]["read_only"] is True
    assert first["status_read_model"]["mode"] == "event_projection"
    assert first["status_read_model"]["generation"] == second["status_read_model"]["generation"]
    assert "persisted_state" not in first
    assert "env" not in first["active_manifest"]
    assert len(first["bootstrap_update"]["changed_paths"]) == 16
    assert first["bootstrap_update"]["changed_paths_total"] == 100
    assert first["sidecar"]["process"]["listener_process_relationship"] == "managed_descendant"
    assert "route_tunnel_contract" not in first["sidecar"]["process"]
    assert first["sidecar"]["transition"]["transition_id"] == "sidecar-test"
    assert "internal_context" not in first["sidecar"]["transition"]
    assert "observed_status" not in first["update_attempt"]
    assert len(json.dumps(first)) < 12_000


def test_supervisor_prepare_failure_does_not_request_runtime_shutdown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "0")
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "failed",
            "phase": "prepare",
            "message": "prepare exploded",
            "target_slot": "B",
            "plan": {"target_slot": "B"},
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _unexpected_shutdown(**kwargs):
        raise AssertionError("runtime shutdown must not be requested when prepare fails")

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _unexpected_shutdown)

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is True
        task = manager._update_task
        assert task is not None
        await task
        status = read_status()
        assert status["state"] == "failed"
        assert status["phase"] == "prepare"
        assert status["prepare_lease_revocation"]["ok"] is True
        lease = json.loads(Path(status["prepare_lease_path"]).read_text(encoding="utf-8"))
        assert lease["state"] == "revoked"
        assert lease["revoked_reason"] == "supervisor.prepare_failed"
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["state"] == "failed"

    asyncio.run(_exercise())


def test_supervisor_prepare_emits_progress_heartbeat(monkeypatch, tmp_path) -> None:
    from adaos.apps.supervisor_runtime import update_execution

    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "0")
    monkeypatch.setattr(update_execution, "PREPARE_HEARTBEAT_SEC", 0.01)

    def _slow_prepare(plan):
        time.sleep(0.04)
        return {
            "state": "failed",
            "phase": "prepare",
            "message": "expected test failure",
            "target_slot": "B",
            "plan": {"target_slot": "B"},
        }

    status_writes: list[dict[str, object]] = []
    original_write_status = supervisor.write_core_update_status

    def _record_status(payload):
        status_writes.append(dict(payload))
        return original_write_status(payload)

    monkeypatch.setattr(supervisor, "prepare_pending_update", _slow_prepare)
    monkeypatch.setattr(supervisor, "write_core_update_status", _record_status)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is True
        assert manager._update_task is not None
        await manager._update_task

    asyncio.run(_exercise())

    heartbeats = [payload for payload in status_writes if payload.get("prepare_heartbeat_at")]
    assert heartbeats
    assert heartbeats[-1]["state"] == "preparing"
    assert heartbeats[-1]["phase"] == "prepare"
    assert float(heartbeats[-1]["prepare_elapsed_s"]) > 0.0
    assert "worker active" in str(heartbeats[-1]["message"])


def test_prepare_worker_writes_prepared_restart_plan_and_reenables_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    desired_running_states: list[bool] = []
    activated_slots: list[str] = []
    candidate_calls: list[tuple[str, str | None]] = []
    promote_calls: list[tuple[str, str]] = []
    cutover_order: list[str] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        prewarm_status = read_status()
        assert prewarm_status["state"] == "preparing"
        assert prewarm_status["phase"] == "prewarm"
        assert prewarm_status["candidate_prewarm_state"] == "starting"
        assert prewarm_status["target_slot"] == "B"
        candidate_calls.append(("prewarm", target_slot))
        return {
            "attempted": True,
            "state": "ready",
            "message": "passive candidate runtime is ready on http://127.0.0.1:8778",
            "ready_at": 223.0,
        }

    async def _cleanup_candidate_runtime(*, reason: str, slot: str | None = None):
        candidate_calls.append((reason, slot))
        return {"ok": True, "stopped": True, "slot": slot}

    async def _single_owner_candidate_cutover(
        *, slot: str, reason: str, restore_active_on_failure: bool
    ):
        assert restore_active_on_failure is True
        cutover_order.extend(("retire", "promote"))
        promote_calls.append((slot, reason))
        return {
            "ok": True,
            "active_retirement": {
                "ok": True,
                "stopped": True,
                "invariant": "active_stopped_before_candidate_promotion",
            },
            "invariant": "no_concurrent_active_transport_owners",
        }

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)
    monkeypatch.setattr(manager, "_single_owner_candidate_cutover", _single_owner_candidate_cutover)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: activated_slots.append(str(slot)))
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: desired_running_states.append(bool(manager._desired_running)))

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    plan = read_plan()
    assert isinstance(plan, dict)
    assert plan["state"] == "prepared_restart"
    assert plan["target_slot"] == "B"
    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "launch"
    assert status["candidate_prewarm_state"] == "promoted_to_active"
    assert activated_slots == ["B"]
    assert lifecycle_calls == []
    assert candidate_calls == [("prewarm", "B")]
    assert promote_calls == [("B", "supervisor.fast_cutover")]
    assert cutover_order == ["retire", "promote"]
    assert status["active_retirement"]["invariant"] == "active_stopped_before_candidate_promotion"
    assert desired_running_states[-1] is True


def test_supervisor_update_status_uses_local_authority_while_update_task_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[float] = []

    monkeypatch.setattr(manager._update_state_machine, "task_running", lambda: True)

    def _local_status(*, runtime_api_timeout: float = 0.75):
        calls.append(runtime_api_timeout)
        return {"ok": True, "status": {"state": "preparing"}, "_served_by": "supervisor_fallback"}

    monkeypatch.setattr(manager, "_local_supervisor_update_status_payload", _local_status)
    monkeypatch.setattr(
        supervisor.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("active update status must not round-trip through the runtime"),
    )

    payload = manager.supervisor_update_status()

    assert payload["status"]["state"] == "preparing"
    assert payload["_served_by"] == "supervisor_fallback"
    assert calls == [0.1]


def test_candidate_prewarm_stops_memory_blocked_candidate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    start_calls: list[tuple[str | None, str]] = []
    cleanup_calls: list[tuple[str, str | None, bool]] = []

    async def _start_candidate_runtime(*, slot: str | None = None, reason: str = ""):
        start_calls.append((slot, reason))
        return {"ok": True}

    async def _cleanup_candidate_runtime(
        *,
        reason: str,
        slot: str | None = None,
        graceful: bool = True,
    ):
        cleanup_calls.append((reason, slot, graceful))
        return {"ok": True, "stopped": True, "slot": slot}

    def _status():
        return {
            "candidate_slot": "B",
            "transition_mode": "warm_switch",
            "warm_switch_allowed": True,
            "candidate_managed_alive": True,
            "candidate_managed_pid": 42424,
            "candidate_runtime_api_ready": False,
            "candidate_runtime_url": "http://127.0.0.1:8778",
        }

    guard = {
        "allowed": False,
        "reason": "candidate_rss_threshold",
        "candidate_pid": 42424,
        "candidate_process_rss_bytes": 1800 * 1024 * 1024,
        "candidate_family_rss_bytes": 1800 * 1024 * 1024,
        "available_memory_bytes": 900 * 1024 * 1024,
        "max_candidate_rss_bytes": 1536 * 1024 * 1024,
        "reserve_bytes": 256 * 1024 * 1024,
    }

    monkeypatch.setattr(manager, "start_candidate_runtime", _start_candidate_runtime)
    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)
    monkeypatch.setattr(manager, "status", _status)
    monkeypatch.setattr(manager, "_candidate_memory_guard_snapshot", lambda snapshot=None: guard)

    result = asyncio.run(manager._candidate_prewarm(target_slot="B"))

    assert result["state"] == "memory_blocked"
    assert result["candidate_memory_guard"] == guard
    assert "candidate memory gate blocked warm switch" in result["message"]
    assert start_calls == [("B", "supervisor.candidate.prewarm")]
    assert cleanup_calls == [("supervisor.candidate.memory_blocked", "B", False)]


def test_prepare_worker_rechecks_starting_candidate_before_shutdown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    cleanup_calls: list[tuple[str, str | None]] = []
    promote_calls: list[tuple[str, str]] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": True,
            "state": "starting",
            "message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
        }

    async def _cleanup_candidate_runtime(*, reason: str, slot: str | None = None):
        cleanup_calls.append((reason, slot))
        return {"ok": True, "stopped": True, "slot": slot}

    async def _single_owner_candidate_cutover(
        *, slot: str, reason: str, restore_active_on_failure: bool
    ):
        assert restore_active_on_failure is True
        promote_calls.append((slot, reason))
        return {
            "ok": True,
            "active_retirement": {
                "ok": True,
                "stopped": True,
                "invariant": "active_stopped_before_candidate_promotion",
            },
            "invariant": "no_concurrent_active_transport_owners",
        }

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)
    monkeypatch.setattr(manager, "_single_owner_candidate_cutover", _single_owner_candidate_cutover)
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "candidate_slot": "B",
            "candidate_managed_alive": True,
            "candidate_runtime_api_ready": True,
            "candidate_runtime_url": "http://127.0.0.1:8778",
        },
    )
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: None)
    monkeypatch.setattr(supervisor, "_runtime_beacon_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "launch"
    assert status["candidate_prewarm_state"] == "promoted_to_active"
    assert status["candidate_prewarm_ready_at"]
    assert lifecycle_calls == []
    assert cleanup_calls == []
    assert promote_calls == [("B", "supervisor.fast_cutover")]


def test_warm_switch_candidate_readiness_defaults_cover_slow_startup(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SUPERVISOR_CANDIDATE_READY_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("ADAOS_SUPERVISOR_WARM_SWITCH_MAX_DEFERRALS", raising=False)

    assert supervisor._warm_switch_candidate_ready_timeout_sec() == 60.0
    assert supervisor._warm_switch_max_deferrals() == 1


def test_prepare_worker_defers_when_candidate_is_not_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", raising=False)
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    cleanup_calls: list[tuple[str, str | None]] = []
    activated_slots: list[str] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": True,
            "state": "starting",
            "message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
        }

    async def _refresh_starting_candidate_prewarm(*, target_slot: str | None):
        return {
            "state": "starting",
            "message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
        }

    async def _cleanup_candidate_runtime(*, reason: str, slot: str | None = None):
        cleanup_calls.append((reason, slot))
        return {"ok": True, "stopped": True, "slot": slot}

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_refresh_starting_candidate_prewarm", _refresh_starting_candidate_prewarm)
    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: activated_slots.append(str(slot)))
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    attempt = supervisor._read_update_attempt()
    assert status["state"] == "planned"
    assert status["phase"] == "scheduled"
    assert status["planned_reason"] == "candidate_not_ready"
    assert status["candidate_prewarm_state"] == "deferred_not_ready"
    assert status["candidate_prewarm_deferral_count"] == 1
    assert status["candidate_prewarm_max_deferrals"] == 1
    assert attempt["state"] == "planned"
    assert attempt["candidate_prewarm_deferral_count"] == 1
    assert attempt["candidate_prewarm_max_deferrals"] == 1
    assert lifecycle_calls == []
    assert activated_slots == []
    assert cleanup_calls == [("supervisor.candidate.defer_not_ready", "B")]


def test_prepare_worker_fails_after_candidate_prewarm_deferrals_are_exhausted(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_WARM_SWITCH_MAX_DEFERRALS", "0")
    monkeypatch.delenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", raising=False)
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    cleanup_calls: list[tuple[str, str | None]] = []

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": True,
            "state": "starting",
            "message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
        }

    async def _refresh_starting_candidate_prewarm(*, target_slot: str | None):
        return {
            "state": "starting",
            "message": "passive candidate runtime is still warming on http://127.0.0.1:8778",
        }

    async def _cleanup_candidate_runtime(*, reason: str, slot: str | None = None):
        cleanup_calls.append((reason, slot))
        return {"ok": True, "stopped": True, "slot": slot}

    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_refresh_starting_candidate_prewarm", _refresh_starting_candidate_prewarm)
    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    attempt = supervisor._read_update_attempt()
    assert status["state"] == "failed"
    assert status["phase"] == "prewarm"
    assert status["failure_reason"] == "candidate_not_ready"
    assert status["candidate_prewarm_state"] == "failed_not_ready"
    assert status["candidate_prewarm_deferral_count"] == 1
    assert status["candidate_prewarm_max_deferrals"] == 0
    assert attempt["state"] == "failed"
    assert attempt["completion_reason"] == "candidate_not_ready: automatic warm-switch deferrals exhausted"
    assert cleanup_calls == [("supervisor.candidate.defer_not_ready", "B")]


def test_prepare_worker_uses_cold_transition_when_warm_switch_is_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_WARM_SWITCH_ENABLED", "0")
    monkeypatch.delenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", raising=False)
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    activated_slots: list[str] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": False,
            "state": "skipped",
            "message": "warm switch is disabled",
        }

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: activated_slots.append(str(slot)))
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "launch"
    assert status["target_slot"] == "B"
    assert status["candidate_prewarm_state"] == "skipped"
    assert lifecycle_calls == ["shutdown", "stopped"]
    assert activated_slots == ["B"]


def test_prepare_worker_uses_cold_fallback_when_candidate_is_not_ready_if_explicitly_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", "1")
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    activated_slots: list[str] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {"attempted": True, "state": "starting", "message": "candidate still warming"}

    async def _refresh_starting_candidate_prewarm(*, target_slot: str | None):
        return {"state": "starting", "message": "candidate still warming"}

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_refresh_starting_candidate_prewarm", _refresh_starting_candidate_prewarm)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: activated_slots.append(str(slot)))
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "launch"
    assert status["target_slot"] == "B"
    assert lifecycle_calls == ["shutdown", "stopped"]
    assert activated_slots == ["B"]


def test_prepare_worker_restores_active_when_single_owner_candidate_cutover_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", raising=False)
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    cutover_order: list[str] = []

    async def _shutdown(**kwargs):
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": True,
            "state": "ready",
            "message": "passive candidate runtime is ready on http://127.0.0.1:8778",
            "ready_at": 223.0,
        }

    async def _single_owner_candidate_cutover(
        *, slot: str, reason: str, restore_active_on_failure: bool
    ):
        assert slot == "B"
        assert reason == "supervisor.fast_cutover"
        assert restore_active_on_failure is True
        cutover_order.extend(("retire", "promote", "cleanup", "restore"))
        return {
            "ok": False,
            "error": "RuntimeError: candidate reconnect failed",
            "active_retirement": {"ok": True, "stopped": True},
            "candidate_cleanup": {"ok": True, "stopped": True, "slot": "B"},
            "active_restore": {"ok": True, "pid": 4242},
            "invariant": "no_concurrent_active_transport_owners",
        }

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_single_owner_candidate_cutover", _single_owner_candidate_cutover)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: None)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    assert status["state"] == "planned"
    assert status["phase"] == "scheduled"
    assert status["planned_reason"] == "candidate_cutover_failed"
    assert status["candidate_prewarm_state"] == "cutover_deferred"
    assert "candidate reconnect failed" in str(status["candidate_prewarm_message"] or "")
    assert cutover_order == ["retire", "promote", "cleanup", "restore"]
    assert status["candidate_cleanup"]["stopped"] is True
    assert status["active_restore"]["ok"] is True


def test_prepare_worker_uses_cold_fallback_when_candidate_cutover_fails_if_explicitly_enabled(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK", "1")
    monkeypatch.setattr(
        supervisor,
        "prepare_pending_update",
        lambda plan: {
            "state": "prepared",
            "phase": "prepare",
            "target_slot": "B",
            "manifest": {"slot": "B"},
            "plan": {"target_slot": "B"},
            "finished_at": 222.0,
        },
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    lifecycle_calls: list[str] = []
    activated_slots: list[str] = []

    async def _shutdown(**kwargs):
        lifecycle_calls.append("shutdown")
        return {"ok": True}

    async def _ensure_stopped(**kwargs):
        lifecycle_calls.append("stopped")
        return {"ok": True, "forced": False}

    async def _candidate_prewarm(*, target_slot: str | None):
        return {
            "attempted": True,
            "state": "ready",
            "message": "passive candidate runtime is ready on http://127.0.0.1:8778",
            "ready_at": 223.0,
        }

    async def _single_owner_candidate_cutover(
        *, slot: str, reason: str, restore_active_on_failure: bool
    ):
        assert slot == "B"
        assert reason == "supervisor.fast_cutover"
        assert restore_active_on_failure is False
        return {
            "ok": False,
            "error": "RuntimeError: candidate reconnect failed",
            "active_retirement": {"ok": True, "stopped": True},
            "candidate_cleanup": {"ok": True, "stopped": True, "slot": "B"},
            "active_restore": None,
            "invariant": "no_concurrent_active_transport_owners",
        }

    monkeypatch.setattr(manager, "_request_runtime_shutdown", _shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _ensure_stopped)
    monkeypatch.setattr(manager, "_candidate_prewarm", _candidate_prewarm)
    monkeypatch.setattr(manager, "_single_owner_candidate_cutover", _single_owner_candidate_cutover)
    monkeypatch.setattr(supervisor, "activate_slot", lambda slot: activated_slots.append(str(slot)))
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(
        manager._prepare_and_countdown_update_worker(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "launch"
    assert status["target_slot"] == "B"
    assert status["candidate_prewarm_state"] == "cutover_fallback"
    assert "candidate reconnect failed" in str(status["candidate_prewarm_message"] or "")
    assert lifecycle_calls == ["shutdown", "stopped"]
    assert status["candidate_cleanup"]["stopped"] is True
    assert activated_slots == ["B"]


def test_candidate_cutover_holds_lock_until_previous_owner_stops(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 31338

        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

    old_proc = _Proc()
    manager._proc = old_proc
    observed: list[str] = []

    async def _terminate(**kwargs):  # noqa: ANN003
        assert manager._lock.locked()
        assert kwargs["proc"] is old_proc
        observed.append("active_stopped")
        old_proc.running = False

    async def _promote_locked(*, slot: str, reason: str):
        assert manager._lock.locked()
        assert old_proc.poll() is not None
        observed.append("candidate_promoted")
        return {"ok": True, "slot": slot, "reason": reason}

    monkeypatch.setattr(manager, "_terminate_proc_locked", _terminate)
    monkeypatch.setattr(manager, "_promote_candidate_runtime_locked", _promote_locked)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    result = asyncio.run(
        manager._single_owner_candidate_cutover(
            slot="B",
            reason="test.atomic_cutover",
            restore_active_on_failure=True,
        )
    )

    assert result["ok"] is True
    assert result["invariant"] == "no_concurrent_active_transport_owners"
    assert observed == ["active_stopped", "candidate_promoted"]
    assert manager._desired_running is True


def test_candidate_cutover_cleans_partial_candidate_before_restoring_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        next_pid = 32000

        def __init__(self) -> None:
            self.running = True
            self.pid = self.next_pid
            type(self).next_pid += 1

        def poll(self):
            return None if self.running else 0

    old_proc = _Proc()
    candidate_proc = _Proc()
    manager._proc = old_proc
    manager._candidate_proc = candidate_proc
    manager._candidate_slot = "B"
    observed: list[str] = []

    async def _terminate(*, proc, **kwargs):  # noqa: ANN003
        assert manager._lock.locked()
        if proc is old_proc:
            observed.append("active_stopped")
        elif proc is candidate_proc:
            assert old_proc.poll() is not None
            observed.append("candidate_cleaned")
        proc.running = False

    async def _promote_locked(*, slot: str, reason: str):
        assert manager._lock.locked()
        assert old_proc.poll() is not None
        observed.append("candidate_promotion_failed")
        raise RuntimeError("authority confirmation timed out")

    async def _spawn_locked(*, reason: str, **kwargs):  # noqa: ANN003
        assert manager._lock.locked()
        assert candidate_proc.poll() is not None
        observed.append("active_restored")
        manager._proc = _Proc()

    monkeypatch.setattr(manager, "_terminate_proc_locked", _terminate)
    monkeypatch.setattr(manager, "_promote_candidate_runtime_locked", _promote_locked)
    monkeypatch.setattr(manager, "_spawn_runtime_locked", _spawn_locked)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    result = asyncio.run(
        manager._single_owner_candidate_cutover(
            slot="B",
            reason="test.atomic_cutover",
            restore_active_on_failure=True,
        )
    )

    assert result["ok"] is False
    assert "authority confirmation timed out" in result["error"]
    assert result["candidate_cleanup"]["lifecycle_scope"] == "runtime_retire"
    assert result["active_restore"]["ok"] is True
    assert observed == [
        "active_stopped",
        "candidate_promotion_failed",
        "candidate_cleaned",
        "active_restored",
    ]


def test_promote_candidate_runtime_adopts_candidate_process(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _CandidateProc:
        pid = 42424

        @staticmethod
        def poll():
            return None

    manager._candidate_proc = _CandidateProc()
    manager._candidate_slot = "B"
    manager._candidate_runtime_instance_id = "rt-b-c-12345678"
    manager._candidate_transition_role = "candidate"

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "ok": True,
                "accepted": True,
                "reconnect": {
                    "ok": True,
                    "authority": {"required": True, "ready": True},
                },
                "runtime": {
                    "transition_role": "active",
                    "runtime_instance_id": "rt-b-c-12345678",
                    "runtime_port": 8778,
                },
            }

    captured: dict[str, object] = {}

    def _post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Response()

    persisted: list[bool] = []

    monkeypatch.setattr(supervisor.requests, "post", _post)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: persisted.append(True))

    payload = asyncio.run(manager._promote_candidate_runtime(slot="B", reason="test.cutover"))

    assert payload["accepted"] is True
    assert captured["url"] == "http://127.0.0.1:8778/api/admin/runtime/promote-active"
    assert captured["kwargs"]["json"]["reason"] == "test.cutover"
    assert captured["kwargs"]["json"]["reconnect_hub_root"] is True
    assert captured["kwargs"]["timeout"] == 20.0
    assert manager._proc is not None
    assert manager._candidate_proc is None
    assert manager._managed_runtime_instance_id == "rt-b-c-12345678"
    assert manager._managed_transition_role == "active"
    assert persisted


def test_promote_candidate_runtime_rejects_memory_blocked_candidate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _CandidateProc:
        pid = 42424

        @staticmethod
        def poll():
            return None

    manager._candidate_proc = _CandidateProc()
    manager._candidate_slot = "B"
    manager._candidate_runtime_instance_id = "rt-b-c-12345678"
    manager._candidate_transition_role = "candidate"

    post_calls: list[str] = []
    monkeypatch.setattr(supervisor.requests, "post", lambda *args, **kwargs: post_calls.append("post"))
    monkeypatch.setattr(
        manager,
        "_candidate_memory_guard_snapshot",
        lambda snapshot=None: {
            "allowed": False,
            "reason": "candidate_rss_threshold",
            "candidate_pid": 42424,
            "candidate_process_rss_bytes": 1800 * 1024 * 1024,
            "candidate_family_rss_bytes": 1800 * 1024 * 1024,
            "available_memory_bytes": 900 * 1024 * 1024,
            "max_candidate_rss_bytes": 1536 * 1024 * 1024,
            "reserve_bytes": 256 * 1024 * 1024,
        },
    )

    with pytest.raises(RuntimeError, match="candidate memory gate blocked warm switch"):
        asyncio.run(manager._promote_candidate_runtime(slot="B", reason="test.cutover"))

    assert post_calls == []


def test_supervisor_monitor_cleans_idle_candidate_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 9999

        @staticmethod
        def poll():
            return None

    manager._candidate_proc = _Proc()
    manager._candidate_slot = "B"
    manager._candidate_runtime_instance_id = "rt-b-c-12345678"
    manager._candidate_transition_role = "candidate"
    write_status({"state": "idle", "updated_at": 10.0})
    supervisor._write_update_attempt({"state": "completed", "updated_at": 9.0})

    cleanup_calls: list[tuple[str, str | None]] = []

    async def _cleanup_candidate_runtime(*, reason: str, slot: str | None = None):
        cleanup_calls.append((reason, slot))
        manager._candidate_proc = None
        manager._candidate_slot = None
        manager._candidate_runtime_instance_id = None
        manager._candidate_transition_role = None
        return {"ok": True, "stopped": True}

    monkeypatch.setattr(manager, "_cleanup_candidate_runtime", _cleanup_candidate_runtime)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert cleanup_calls == [("supervisor.candidate.idle_cleanup", None)]
    assert manager._candidate_proc is None


def test_supervisor_start_update_schedules_when_min_period_not_elapsed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "completed_at": 450.0,
            "updated_at": 450.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["planned"] is True
    status = read_status()
    assert status["state"] == "planned"
    assert status["planned_reason"] == "minimum_update_period"
    assert status["scheduled_for"] == 750.0
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "planned"
    assert attempt["scheduled_for"] == 750.0


def test_supervisor_start_update_deduplicates_active_slot_before_min_period(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "300")
    monkeypatch.setattr(supervisor, "core_update_reactions_disabled_reason", lambda: "")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    target = "4c1806aa70b040db61199707e0b739b244d7af04"
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_rev": "rev2026",
            "target_version": target,
            "git_commit": target,
            "git_short_commit": target[:7],
        },
    )
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "finished_at": 450.0,
            "updated_at": 450.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "completed_at": 450.0,
            "updated_at": 450.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version=target,
            reason="test.same-active",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["deduplicated"] is True
    assert result["same_target"] is True
    assert result["planned"] is False
    status = read_status()
    assert status["state"] == "succeeded"
    assert status["same_target_deduped_reason"] == "active_slot_same_target"
    assert status["scheduled_for"] is None
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "deduplicated"
    assert supervisor._last_update_completion_at(status, attempt) == 450.0


def test_supervisor_active_slot_dedupe_clears_stale_failed_prepare_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "core_update_reactions_disabled_reason", lambda: "")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    active_target = "0.1.0"
    failed_target = "37f53cc4f1e7aa9806f62717491dc6219ab1ab2b"
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "target_rev": "rev2026",
            "target_version": active_target,
            "git_commit": "6b63485d53247c9993c351f4499a26fb98b44f9b",
            "git_short_commit": "6b63485",
        },
    )
    write_status(
        {
            "state": "failed",
            "phase": "prepare",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": failed_target,
            "message": "core update slot preparation failed",
            "error_type": "RuntimeError",
            "error": "fatal: reference is not a tree",
            "plan": {"target_version": failed_target, "target_slot": "B"},
            "started_at": 420.0,
            "finished_at": 430.0,
            "updated_at": 430.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "failed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": failed_target,
            "reason": "hub.member_follow.update",
            "completed_at": 430.0,
            "completion_reason": "core update slot preparation failed: fatal: reference is not a tree",
            "last_status": read_status(),
            "updated_at": 430.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version=active_target,
            reason="cli.core_update",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["deduplicated"] is True
    status = read_status()
    assert status["state"] == "succeeded"
    assert status["target_version"] == active_target
    assert "error" not in status
    assert "error_type" not in status
    assert "plan" not in status
    assert "finished_at" not in status
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "deduplicated"
    assert attempt.get("completion_reason") is None
    assert attempt.get("completed_at") is None
    assert "error" not in attempt["last_status"]
    assert "plan" not in attempt["last_status"]


def test_reconcile_update_status_recovers_active_attempt_when_target_already_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    target = "259c1e63e4f2e931292287a93e9eb69a42d8d1cd"

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_rev": "rev2026",
            "target_version": target,
            "git_commit": target,
            "git_short_commit": target[:7],
        },
    )
    monkeypatch.setattr(
        supervisor,
        "rollback_to_previous_slot",
        lambda: (_ for _ in ()).throw(AssertionError("matching active target must not roll back")),
    )
    write_status({"state": "idle", "message": "autostart runner boot", "updated_at": 490.0})
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "reason": "github.push:rev2026:259c1e63e4f2",
            "requested_at": 300.0,
            "transitioned_at": 360.0,
            "updated_at": 360.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "runtime": {
                "active_slot": "B",
                "runtime_state": "ready",
                "listener_running": True,
                "runtime_api_ready": True,
            },
            "_served_by": "supervisor_monitor",
        }
    )

    status = payload["status"]
    assert status["state"] == "succeeded"
    assert status["phase"] == "validate"
    assert status["target_slot"] == "B"
    assert status["target_version"] == target
    assert status["stale_active_attempt_recovered"] is True
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "active slot target already active"


def test_reconcile_update_status_keeps_fresh_launch_status_when_attempt_clock_is_old(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", "180")
    monkeypatch.setattr(supervisor.time, "time", lambda: 550.0)
    monkeypatch.setattr(
        supervisor,
        "rollback_to_previous_slot",
        lambda: (_ for _ in ()).throw(AssertionError("fresh launch status must not roll back")),
    )
    write_status(
        {
            "state": "restarting",
            "phase": "launch",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "target-sha",
            "message": "prepared slot activated; awaiting runtime launch",
            "updated_at": 520.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "target-sha",
            "requested_at": 100.0,
            "transitioned_at": 100.0,
            "updated_at": 100.0,
        }
    )

    payload = supervisor._reconcile_update_status(
        {
            "ok": True,
            "status": read_status(),
            "_served_by": "supervisor_monitor",
        }
    )

    assert payload["status"]["state"] == "restarting"
    assert payload["status"]["phase"] == "launch"
    assert supervisor._read_update_attempt()["state"] == "active"


def test_start_update_recovers_stale_active_attempt_before_new_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "core_update_reactions_disabled_reason", lambda: "")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    active_target = "259c1e63e4f2e931292287a93e9eb69a42d8d1cd"
    next_target = "8c698078b42c2954e5509a4a7b7d0dac6c2f79f1"
    calls: list[dict] = []

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_rev": "rev2026",
            "target_version": active_target,
            "git_commit": active_target,
            "git_short_commit": active_target[:7],
        },
    )
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "active_slot": "B",
            "runtime_state": "ready",
            "listener_running": True,
            "runtime_api_ready": True,
        },
    )
    monkeypatch.setattr(manager, "_transition_continuity_guard_decision", lambda operation: None)
    monkeypatch.setattr(
        manager,
        "_begin_prepare_transition",
        lambda request: calls.append(dict(request)) or {"ok": True, "accepted": True, "_served_by": "supervisor"},
    )
    write_status({"state": "idle", "message": "autostart runner boot", "updated_at": 490.0})
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": active_target,
            "reason": "github.push:rev2026:259c1e63e4f2",
            "requested_at": 300.0,
            "transitioned_at": 360.0,
            "updated_at": 360.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version=next_target,
            reason="github.push:rev2026:8c698078b42c",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
            bypass_min_period=True,
        )
    )

    assert result["accepted"] is True
    assert calls
    assert calls[0]["target_version"] == next_target
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "completed"
    assert attempt["completion_reason"] == "active slot target already active"


def test_supervisor_planned_update_resumes_through_prepare(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    target = "9c7e221b5157c46d84f64e43822357d5cffec4b0"
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(manager, "status", lambda: {})
    monkeypatch.setattr(manager, "_transition_continuity_guard_decision", lambda operation: None)
    monkeypatch.setattr(
        manager,
        "_begin_prepare_transition",
        lambda request: calls.append(("prepare", dict(request))) or {"ok": True},
    )
    monkeypatch.setattr(
        manager,
        "_begin_countdown_transition",
        lambda request, **kwargs: calls.append(("countdown", dict(request))) or {"ok": True},
    )
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "scheduled_for": 450.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "planned",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "scheduled_for": 450.0,
            "updated_at": 400.0,
        }
    )

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert calls == [
        (
            "prepare",
            {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": target,
                "reason": "",
                "countdown_sec": 0.0,
                "drain_timeout_sec": 10.0,
                "signal_delay_sec": 0.25,
                "requested_at": 500.0,
            },
        )
    ]


def test_supervisor_promote_root_refuses_active_slot_target_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    target = "9c7e221b5157c46d84f64e43822357d5cffec4b0"
    active = "2ba8453f42daaa8f89fad848d92a1481bd3a6a4d"

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "target_rev": "rev2026",
            "target_version": active,
            "git_commit": active,
            "git_short_commit": active[:7],
        },
    )
    monkeypatch.setattr(
        supervisor,
        "resolved_root_promotion_requirement",
        lambda manifest: (False, {"required": False, "basis": "test"}),
    )
    write_status(
        {
            "state": "validated",
            "phase": "root_promotion_pending",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": target,
            "updated_at": 400.0,
        }
    )

    result = asyncio.run(manager.promote_root(reason="test.root"))

    assert result["ok"] is False
    status = read_status()
    assert status["state"] == "failed"
    assert status["root_promotion_refused_reason"] == "active_slot_target_mismatch"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "failed"
    assert attempt["completion_reason"] == "active slot target mismatch"


def test_supervisor_active_slot_dedupe_preserves_different_planned_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    active_target = "4c1806aa70b040db61199707e0b739b244d7af04"
    planned_target = "9a9b9c9d00000000000000000000000000000000"
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_rev": "rev2026",
            "target_version": active_target,
            "git_commit": active_target,
            "git_short_commit": active_target[:7],
        },
    )
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": planned_target,
            "reason": "test.future",
            "scheduled_for": 800.0,
            "planned_reason": "minimum_update_period",
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "planned",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": planned_target,
            "reason": "test.future",
            "scheduled_for": 800.0,
            "planned_reason": "minimum_update_period",
            "updated_at": 450.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version=active_target,
            reason="test.same-active-probe",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["deduplicated"] is True
    assert result["same_target"] is True
    assert result["preserved_planned_transition"] is True
    status = read_status()
    assert status["state"] == "planned"
    assert status["target_version"] == planned_target
    assert status["scheduled_for"] == 800.0
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "planned"
    assert attempt["target_version"] == planned_target


def test_supervisor_start_update_refreshes_existing_planned_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "reason": "test.older",
            "scheduled_for": 750.0,
            "planned_reason": "minimum_update_period",
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "planned",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "reason": "test.older",
            "scheduled_for": 750.0,
            "planned_reason": "minimum_update_period",
            "updated_at": 450.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.refresh",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["planned"] is True
    assert result["status"]["scheduled_for"] == 750.0
    assert result["status"]["message"] == "planned core update refreshed while waiting for scheduled window"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "planned"
    assert attempt["target_version"] == "1.2.3"
    assert attempt["scheduled_for"] == 750.0


def test_supervisor_start_update_queues_subsequent_transition_while_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    write_status(
        {
            "state": "countdown",
            "phase": "countdown",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "reason": "test.active",
            "scheduled_for": 530.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "reason": "test.active",
            "scheduled_for": 530.0,
            "updated_at": 500.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.subsequent",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["deferred"] is True
    assert result["subsequent_transition"] is True
    status = read_status()
    assert status["subsequent_transition"] is True
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["subsequent_transition"] is True
    assert attempt["subsequent_transition_request"]["target_version"] == "1.2.3"


def test_supervisor_start_update_deduplicates_same_target_subsequent_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 600.0)
    write_status(
        {
            "state": "countdown",
            "phase": "countdown",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "671903ec01044b16865a366c81bf27f758823595",
            "reason": "test.active",
            "scheduled_for": 630.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "671903e",
            "reason": "test.active",
            "scheduled_for": 630.0,
            "updated_at": 600.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="671903ec01044b16865a366c81bf27f758823595",
            reason="test.same-target",
            countdown_sec=0.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["deduplicated"] is True
    assert result["same_target"] is True
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt.get("subsequent_transition") is not True
    status = read_status()
    assert status["same_target_subsequent_deduped_reason"] == "active_transition_same_target"


def test_supervisor_start_update_rejects_unresolved_subsequent_update_target(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 650.0)
    write_status(
        {
            "state": "countdown",
            "phase": "countdown",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "60ae4fc5401c0a5c3197b9b6e4b416ad51c076be",
            "reason": "github.push:rev2026",
            "scheduled_for": 670.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "60ae4fc5401c0a5c3197b9b6e4b416ad51c076be",
            "reason": "github.push:rev2026",
            "scheduled_for": 670.0,
            "updated_at": 650.0,
        }
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="",
            target_version="0.1.0",
            reason="cli.core_update",
            countdown_sec=60.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "unresolved_subsequent_transition_target"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt.get("subsequent_transition") is not True
    status = read_status()
    assert status["ambiguous_subsequent_transition_reason"] == "unresolved_update_target"


def test_supervisor_monitor_runs_subsequent_transition_once_after_completion(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 800.0)
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "target_rev": "rev2026",
            "updated_at": 799.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 780.0,
            "subsequent_transition_request": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "1.2.3",
                "reason": "test.subsequent",
                "countdown_sec": 15.0,
                "drain_timeout_sec": 10.0,
                "signal_delay_sec": 0.25,
                "requested_at": 780.0,
            },
            "updated_at": 799.0,
        }
    )
    calls: list[dict[str, object]] = []

    async def _capture(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(manager, "start_update", _capture)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert len(calls) == 1
    assert calls[0]["target_version"] == "1.2.3"
    assert calls[0]["bypass_min_period"] is True


def test_update_attempt_phase_write_preserves_concurrently_queued_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "updated_at": 700.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 710.0,
            "subsequent_transition_request": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "1.2.3",
                "reason": "test.concurrent-push",
                "requested_at": 710.0,
            },
            "updated_at": 710.0,
        }
    )

    manager._update_execution_operations().write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.2",
            "updated_at": 720.0,
        }
    )

    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["subsequent_transition"] is True
    assert attempt["subsequent_transition_requested_at"] == 710.0
    assert attempt["subsequent_transition_request"]["target_version"] == "1.2.3"


def test_replacing_update_attempt_consumes_queued_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_version": "1.2.2",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 710.0,
            "subsequent_transition_request": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "1.2.3",
                "requested_at": 710.0,
            },
            "updated_at": 720.0,
        }
    )

    supervisor._replace_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "updated_at": 730.0,
        }
    )

    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["target_version"] == "1.2.3"
    assert attempt["subsequent_transition"] is False
    assert not attempt.get("subsequent_transition_request")


def test_supervisor_monitor_drops_same_target_subsequent_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(supervisor.time, "time", lambda: 900.0)
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "671903ec01044b16865a366c81bf27f758823595",
            "updated_at": 899.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "671903e",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 880.0,
            "subsequent_transition_request": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "671903ec01044b16865a366c81bf27f758823595",
                "reason": "test.same-target",
                "countdown_sec": 0.0,
                "drain_timeout_sec": 10.0,
                "signal_delay_sec": 0.25,
                "requested_at": 880.0,
            },
            "updated_at": 899.0,
        }
    )
    calls: list[dict[str, object]] = []

    async def _capture(**kwargs):
        calls.append(dict(kwargs))
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(manager, "start_update", _capture)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert calls == []
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["subsequent_transition"] is False
    assert not attempt.get("subsequent_transition_request")
    status = read_status()
    assert status["subsequent_transition"] is False
    assert status["same_target_subsequent_deduped_reason"] == "completed_transition_same_target"


def test_supervisor_start_update_queues_subsequent_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "countdown",
            "phase": "countdown",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "scheduled_for": 9999999999.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "requested_at": 1.0,
            "updated_at": 1.0,
        }
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2027",
            target_version="2.0.0",
            reason="test.update.next",
            countdown_sec=45.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is True
        assert result["deferred"] is True
        assert result["subsequent_transition"] is True
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["subsequent_transition"] is True
        assert attempt["subsequent_transition_request"]["target_rev"] == "rev2027"
        status = read_status()
        assert status["subsequent_transition"] is True

    asyncio.run(_exercise())


def test_supervisor_start_update_schedules_planned_update_when_min_period_not_elapsed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", "300")
    monkeypatch.setattr(supervisor.time, "time", lambda: 150.0)
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "finished_at": 100.0,
            "updated_at": 100.0,
        }
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _exercise() -> None:
        result = await manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.4",
            reason="test.update",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
        assert result["accepted"] is True
        assert result["planned"] is True
        status = read_status()
        assert status["state"] == "planned"
        assert status["phase"] == "scheduled"
        assert status["planned_reason"] == "minimum_update_period"
        assert status["scheduled_for"] == 400.0
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["state"] == "planned"
        assert attempt["scheduled_for"] == 400.0

    asyncio.run(_exercise())


def test_supervisor_start_update_defers_when_live_media_guard_blocks_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: {
            "ok": True,
            "runtime": {
                "media_runtime": {
                    "update_guard": {
                        "role": "hub",
                        "live_session_present": True,
                        "observed_live_topology": "member_browser_direct",
                        "hub_runtime_update": "preserve_sidecar",
                        "hub_sidecar_continuity_required": True,
                        "current_support": "planned",
                        "reason": "live media continuity requires independent sidecar ownership",
                    }
                },
                "sidecar_runtime": {
                    "continuity_contract": {
                        "required": True,
                        "enabled": False,
                        "hub_runtime_update": "preserve_sidecar",
                        "current_support": "planned",
                        "reason": "live media continuity requires independent sidecar ownership",
                    }
                },
            },
        },
    )

    result = asyncio.run(
        manager.start_update(
            action="update",
            target_rev="rev2026",
            target_version="1.2.3",
            reason="test.live_media",
            countdown_sec=30.0,
            drain_timeout_sec=10.0,
            signal_delay_sec=0.25,
        )
    )

    assert result["accepted"] is True
    assert result["planned"] is True
    status = read_status()
    assert status["state"] == "planned"
    assert status["planned_reason"] == "live_media_guard"
    assert status["scheduled_for"] == 800.0
    assert status["guard_code"] == "hub_sidecar_continuity_pending"
    assert status["continuity_contract"]["required"] is True
    assert status["live_media_guard"]["observed_live_topology"] == "member_browser_direct"


def test_supervisor_defer_update_reschedules_active_countdown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    write_status(
        {
            "state": "countdown",
            "phase": "countdown",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "countdown_sec": 30.0,
            "scheduled_for": 200.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "countdown_sec": 30.0,
            "drain_timeout_sec": 10.0,
            "signal_delay_sec": 0.25,
            "requested_at": 100.0,
            "updated_at": 100.0,
        }
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _sleep_forever() -> None:
        await asyncio.Future()

    async def _exercise() -> None:
        monkeypatch.setattr(supervisor.time, "time", lambda: 150.0)
        manager._update_task = asyncio.create_task(_sleep_forever())
        try:
            result = await manager.defer_update(delay_sec=300.0, reason="test.defer")
        finally:
            if manager._update_task is not None and not manager._update_task.done():
                manager._update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await manager._update_task
        assert result["accepted"] is True
        assert result["planned"] is True
        status = read_status()
        assert status["state"] == "planned"
        assert status["planned_reason"] == "operator_defer"
        assert status["scheduled_for"] == 450.0
        attempt = supervisor._read_update_attempt()
        assert isinstance(attempt, dict)
        assert attempt["state"] == "planned"
        assert attempt["scheduled_for"] == 450.0

    import contextlib

    asyncio.run(_exercise())


def test_supervisor_monitor_resumes_due_planned_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "countdown_sec": 30.0,
            "drain_timeout_sec": 10.0,
            "signal_delay_sec": 0.25,
            "scheduled_for": 499.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "planned",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "countdown_sec": 30.0,
            "drain_timeout_sec": 10.0,
            "signal_delay_sec": 0.25,
            "scheduled_for": 499.0,
            "updated_at": 490.0,
        }
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[dict] = []

    def _capture(request: dict) -> dict:
        calls.append({"request": dict(request)})
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(manager, "_begin_prepare_transition", _capture)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert calls
    assert calls[0]["request"]["target_rev"] == "rev2026"


def test_supervisor_monitor_waits_for_recovered_channel_after_failed_cutover(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_CUTOVER_RECOVERY_STABLE_SEC", "30")
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    payload = {
        "state": "planned",
        "phase": "scheduled",
        "action": "update",
        "target_rev": "rev2026",
        "target_version": "1.2.3",
        "reason": "test.update",
        "countdown_sec": 0.0,
        "drain_timeout_sec": 10.0,
        "signal_delay_sec": 0.25,
        "scheduled_for": 499.0,
        "planned_reason": "candidate_cutover_failed",
    }
    write_status(payload)
    supervisor._write_update_attempt({**payload, "state": "planned", "updated_at": 490.0})
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[dict] = []

    async def _guard() -> dict:
        return {
            "ready": False,
            "runtime_ready": True,
            "channel_ready": False,
            "role": "hub",
            "channel": {"route_status": "degraded"},
        }

    monkeypatch.setattr(manager, "_candidate_cutover_recovery_guard_snapshot", _guard)
    monkeypatch.setattr(manager, "_begin_prepare_transition", lambda request: calls.append(dict(request)))

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert calls == []
    status = read_status()
    assert status["planned_reason"] == "candidate_cutover_recovery"
    assert status["scheduled_for"] == 510.0
    assert status["cutover_recovery_ready_since"] is None
    assert status["cutover_recovery_guard"]["channel_ready"] is False


def test_supervisor_monitor_resumes_after_cutover_recovery_stability_window(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_CUTOVER_RECOVERY_STABLE_SEC", "30")
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    payload = {
        "state": "planned",
        "phase": "scheduled",
        "action": "update",
        "target_rev": "rev2026",
        "target_version": "1.2.3",
        "reason": "test.update",
        "countdown_sec": 0.0,
        "drain_timeout_sec": 10.0,
        "signal_delay_sec": 0.25,
        "scheduled_for": 499.0,
        "planned_reason": "candidate_cutover_recovery",
        "cutover_recovery_ready_since": 460.0,
    }
    write_status(payload)
    supervisor._write_update_attempt({**payload, "state": "planned", "updated_at": 490.0})
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[dict] = []

    async def _guard() -> dict:
        return {"ready": True, "runtime_ready": True, "channel_ready": True, "role": "hub", "channel": {}}

    async def _continuity(*, operation: str):
        assert operation == "update"
        return None

    monkeypatch.setattr(manager, "_candidate_cutover_recovery_guard_snapshot", _guard)
    monkeypatch.setattr(manager, "_transition_continuity_guard_decision_async", _continuity)
    monkeypatch.setattr(manager, "_begin_prepare_transition", lambda request: calls.append(dict(request)))

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert len(calls) == 1
    assert calls[0]["target_rev"] == "rev2026"


def test_supervisor_monitor_reschedules_due_planned_transition_when_live_media_guard_active(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.live_media",
            "countdown_sec": 30.0,
            "drain_timeout_sec": 10.0,
            "signal_delay_sec": 0.25,
            "scheduled_for": 499.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "planned",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.live_media",
            "countdown_sec": 30.0,
            "drain_timeout_sec": 10.0,
            "signal_delay_sec": 0.25,
            "scheduled_for": 499.0,
            "updated_at": 490.0,
        }
    )
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[dict] = []

    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: {
            "ok": True,
            "runtime": {
                "media_runtime": {
                    "update_guard": {
                        "role": "hub",
                        "live_session_present": True,
                        "observed_live_topology": "hub_webrtc_loopback",
                        "hub_runtime_update": "preserve_sidecar",
                        "hub_sidecar_continuity_required": True,
                        "current_support": "planned",
                        "reason": "hub participates in the active live media path",
                    }
                },
                "sidecar_runtime": {
                    "continuity_contract": {
                        "required": True,
                        "enabled": False,
                        "hub_runtime_update": "preserve_sidecar",
                        "current_support": "planned",
                        "reason": "hub participates in the active live media path",
                    }
                },
            },
        },
    )

    def _capture(request: dict, *, countdown_sec: float | None = None) -> dict:
        calls.append({"request": dict(request), "countdown_sec": countdown_sec})
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(manager, "_begin_countdown_transition", _capture)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert not calls
    status = read_status()
    assert status["state"] == "planned"
    assert status["planned_reason"] == "live_media_guard"
    assert status["scheduled_for"] == 800.0
    assert status["guard_code"] == "hub_sidecar_continuity_pending"


def test_supervisor_runtime_restart_blocks_when_live_media_continuity_is_not_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: {
            "ok": True,
            "runtime": {
                "media_runtime": {
                    "update_guard": {
                        "role": "hub",
                        "live_session_present": True,
                        "observed_live_topology": "member_browser_direct",
                        "hub_runtime_update": "preserve_sidecar",
                        "hub_sidecar_continuity_required": True,
                        "current_support": "planned",
                        "reason": "live media continuity requires independent sidecar ownership",
                    }
                },
                "sidecar_runtime": {
                    "continuity_contract": {
                        "required": True,
                        "enabled": False,
                        "hub_runtime_update": "preserve_sidecar",
                        "current_support": "planned",
                        "reason": "live media continuity requires independent sidecar ownership",
                    }
                },
            },
        },
    )

    with pytest.raises(supervisor.HTTPException) as excinfo:
        asyncio.run(manager.restart_runtime())

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["planned_reason"] == "live_media_guard"
    assert excinfo.value.detail["guard_code"] == "hub_sidecar_continuity_pending"
    assert excinfo.value.detail["continuity_contract"]["required"] is True


def test_supervisor_countdown_worker_writes_plan_and_requests_shutdown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    shutdown_calls: list[dict] = []
    stop_calls: list[dict] = []

    async def _fake_sleep(_value: float) -> None:
        return None

    async def _fake_shutdown(*, reason: str, drain_timeout_sec: float, signal_delay_sec: float) -> dict:
        shutdown_calls.append(
            {
                "reason": reason,
                "drain_timeout_sec": drain_timeout_sec,
                "signal_delay_sec": signal_delay_sec,
            }
        )
        return {"ok": True, "accepted": True}

    async def _fake_ensure_stopped(*, drain_timeout_sec: float, signal_delay_sec: float, reason: str) -> dict:
        stop_calls.append(
            {
                "reason": reason,
                "drain_timeout_sec": drain_timeout_sec,
                "signal_delay_sec": signal_delay_sec,
            }
        )
        return {"ok": True, "forced": False, "reason": reason}

    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(manager, "_request_runtime_shutdown", _fake_shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _fake_ensure_stopped)

    asyncio.run(
        manager._countdown_update_worker(
            action="rollback",
            target_rev="",
            target_version="",
            reason="test.rollback",
            countdown_sec=0.0,
            drain_timeout_sec=5.0,
            signal_delay_sec=0.1,
        )
    )

    plan = read_plan()
    status = read_status()
    assert isinstance(plan, dict)
    assert plan["action"] == "rollback"
    assert status["state"] == "restarting"
    assert status["phase"] == "shutdown"
    assert shutdown_calls and shutdown_calls[0]["reason"] == "test.rollback"
    assert stop_calls and stop_calls[0]["reason"] == "test.rollback"


def test_supervisor_countdown_worker_marks_failed_when_shutdown_request_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _fake_sleep(_value: float) -> None:
        return None

    async def _fake_shutdown(*, reason: str, drain_timeout_sec: float, signal_delay_sec: float) -> dict:
        raise RuntimeError("runtime shutdown API unavailable")

    async def _fake_ensure_stopped(*, drain_timeout_sec: float, signal_delay_sec: float, reason: str) -> dict:
        raise RuntimeError("runtime process did not exit")

    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(manager, "_request_runtime_shutdown", _fake_shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _fake_ensure_stopped)
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "requested_at": 1.0,
            "transitioned_at": 2.0,
            "updated_at": 2.0,
        }
    )

    asyncio.run(
        manager._countdown_update_worker(
            action="update",
            target_rev="HEAD",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=5.0,
            signal_delay_sec=0.1,
        )
    )

    assert read_plan() is None
    status = read_status()
    assert status["state"] == "failed"
    assert status["phase"] == "shutdown"
    assert status["error_type"] == "RuntimeError"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "failed"


def test_supervisor_countdown_worker_continues_when_shutdown_request_fails_but_runtime_stops(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    async def _fake_sleep(_value: float) -> None:
        return None

    async def _fake_shutdown(*, reason: str, drain_timeout_sec: float, signal_delay_sec: float) -> dict:
        raise RuntimeError("runtime shutdown API unavailable")

    async def _fake_ensure_stopped(*, drain_timeout_sec: float, signal_delay_sec: float, reason: str) -> dict:
        return {"ok": True, "forced": True, "reason": reason}

    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(manager, "_request_runtime_shutdown", _fake_shutdown)
    monkeypatch.setattr(manager, "_ensure_runtime_stopped_for_update", _fake_ensure_stopped)
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "requested_at": 1.0,
            "transitioned_at": 2.0,
            "updated_at": 2.0,
        }
    )

    asyncio.run(
        manager._countdown_update_worker(
            action="update",
            target_rev="HEAD",
            target_version="1.2.3",
            reason="test.update",
            countdown_sec=0.0,
            drain_timeout_sec=5.0,
            signal_delay_sec=0.1,
        )
    )

    status = read_status()
    assert status["state"] == "restarting"
    assert status["phase"] == "shutdown"
    assert status["forced_shutdown"] is True
    assert status["shutdown_request_error_type"] == "RuntimeError"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "active"


def test_ensure_runtime_stopped_for_update_forces_hung_process(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    timeline = {"now": 0.0}

    class _Proc:
        def __init__(self) -> None:
            self._alive = True
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self):
            return None if self._alive else 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1
            self._alive = False

    proc = _Proc()
    manager._proc = proc

    async def _fake_sleep(value: float) -> None:
        timeline["now"] += max(0.1, float(value))

    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(supervisor.time, "time", lambda: timeline["now"])

    result = asyncio.run(
        manager._ensure_runtime_stopped_for_update(
            drain_timeout_sec=1.0,
            signal_delay_sec=0.1,
            reason="test.hung_shutdown",
        )
    )

    assert result["ok"] is True
    assert result["forced"] is True
    assert proc.terminate_calls >= 1
    assert proc.kill_calls == 1
    assert proc.poll() == 0


def test_ensure_runtime_stopped_retains_plan_while_kernel_exit_is_pending(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token"
    )
    timeline = {"now": 0.0}

    class _Proc:
        def __init__(self) -> None:
            self.terminate_calls = 0
            self.kill_calls = 0

        @staticmethod
        def poll():
            return None

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1

    proc = _Proc()
    manager._proc = proc

    async def _fake_sleep(value: float) -> None:
        timeline["now"] += max(0.5, float(value))

    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(supervisor.time, "time", lambda: timeline["now"])

    result = asyncio.run(
        manager._ensure_runtime_stopped_for_update(
            drain_timeout_sec=1.0,
            signal_delay_sec=0.1,
            reason="test.kernel_io_wait",
        )
    )

    assert result == {
        "ok": False,
        "forced": True,
        "pending_exit": True,
        "reason": "test.kernel_io_wait",
    }
    assert proc.terminate_calls >= 1
    assert proc.kill_calls == 1


def test_terminate_proc_locked_waits_after_process_group_kill(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    timeline = {"now": 0.0}

    class _Proc:
        pid = 42424

        def __init__(self) -> None:
            self.alive = True
            self.terminate_calls = 0
            self.kill_calls = 0

        def poll(self):
            return None if self.alive else 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

    proc = _Proc()
    signals: list[tuple[int, int]] = []

    def _fake_killpg(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig == getattr(supervisor.signal, "SIGKILL", 9):
            proc.alive = False

    async def _fake_sleep(value: float) -> None:
        timeline["now"] += max(0.5, float(value))

    if supervisor.os.name != "nt":
        monkeypatch.setattr(supervisor.os, "killpg", _fake_killpg, raising=False)
    monkeypatch.setattr(supervisor.time, "time", lambda: timeline["now"])
    monkeypatch.setattr(supervisor.asyncio, "sleep", _fake_sleep)

    asyncio.run(manager._terminate_proc_locked(proc=proc, graceful=False, reason="test.process_group_stop"))

    if supervisor.os.name != "nt":
        assert signals[0] == (42424, supervisor.signal.SIGTERM)
        assert signals[-1] == (42424, getattr(supervisor.signal, "SIGKILL", 9))
        assert proc.terminate_calls == 0
        assert proc.kill_calls == 0
    else:
        assert signals == []
        assert proc.terminate_calls >= 1
        assert proc.kill_calls == 1
    assert proc.poll() == 0


def test_runtime_state_payload_reports_listener_and_api_readiness(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": [], "repo_dir": "/slots/B/repo", "venv_dir": "/slots/B/venv"},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)

    payload = manager.status(refresh=True)

    assert payload["active_slot"] == "B"
    assert payload["managed_alive"] is True
    assert payload["listener_running"] is True
    assert payload["runtime_api_ready"] is False
    assert payload["runtime_state"] == "starting"
    assert payload["managed_executable"] == "python"
    assert payload["managed_matches_active_slot"] is True
    assert payload["slot_structure"]["ok"] is True
    assert payload["managed_cmdline"][1:3] == ["-m", "adaos.apps.autostart_runner"]


def test_runtime_state_payload_surfaces_previous_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"active_slot": "B", "previous_slot": "A", "slots": {}},
    )
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": [], "repo_dir": "/slots/B/repo", "venv_dir": "/slots/B/venv"},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)

    payload = manager.status(refresh=True)

    assert payload["active_slot"] == "B"
    assert payload["previous_slot"] == "A"


def test_runtime_state_payload_surfaces_required_upstream_link_for_member(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._managed_transition_role = "member"
    manager._member_hub_watchdog_last_state = "ready"
    manager._member_hub_watchdog_last_reason = "member-hub link is connected"
    manager._member_hub_watchdog_reconnect_total = 3
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"active_slot": "B", "previous_slot": "A", "slots": {}},
    )
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": [], "repo_dir": "/slots/B/repo", "venv_dir": "/slots/B/venv"},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)

    payload = manager.status(refresh=True)

    assert payload["required_upstream_link"]["kind"] == "member_hub"
    assert payload["required_upstream_link"]["owner"] == "supervisor"
    assert payload["required_upstream_link"]["state"] == "ready"
    assert payload["required_upstream_link"]["ready"] is True
    assert payload["required_upstream_link"]["desired_state"] == "connected"
    assert payload["required_upstream_link"]["current_owner"] == "runtime"
    assert payload["required_upstream_link"]["planned_owner"] == "runtime"
    assert payload["required_upstream_link"]["future_owner"] == "sidecar"
    assert payload["required_upstream_link"]["continuity_mode"] == "runtime_bound"
    assert payload["required_upstream_link"]["reconnect_total"] == 3


def test_runtime_state_payload_reports_slot_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["/wrong/python", "-m", "adaos.apps.autostart_runner"]
        cwd = "/wrong"

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["/expected/python", "-m", "adaos.apps.autostart_runner"],
            "cwd": "/expected",
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": False, "issues": ["nested_slot_dir:/slots/A/A"]},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)

    payload = manager.status(refresh=True)

    assert payload["runtime_state"] == "spawned"
    assert payload["managed_matches_active_slot"] is False


def test_runtime_state_payload_reports_verified_adoption_identity_basis(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["/uv/python", "-m", "adaos.apps.autostart_runner"]
        cwd = "/slots/A/repo"

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._managed_runtime_instance_id = "rt-a-a-existing"
    manager._managed_transition_role = "active"
    manager._managed_slot = "A"
    manager._managed_runtime_api_identity_verified = True
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "A", "argv": ["/slots/A/venv/bin/python"], "cwd": "/slots/A/repo"},
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": []},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)

    payload = manager.status(refresh=True)

    assert payload["managed_process_matches_active_slot"] is False
    assert payload["managed_matches_active_slot"] is True
    assert payload["managed_slot_match_basis"] == "runtime_api_identity"


def test_runtime_state_payload_uses_supervisor_recorded_cwd_when_subprocess_hides_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._managed_runtime_cwd = str(tmp_path)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": [], "repo_dir": "/slots/A/repo", "venv_dir": "/slots/A/venv"},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)

    payload = manager.status(refresh=True)

    assert payload["managed_cwd"] == str(tmp_path)
    assert payload["managed_matches_active_slot"] is True


def test_runtime_state_payload_includes_sidecar_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "validate_slot_structure",
        lambda slot: {"slot": slot, "ok": True, "issues": [], "repo_dir": "/slots/A/repo", "venv_dir": "/slots/A/venv"},
    )
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda proc=None: {"listener_running": True, "managed_pid": 45678, "port": 7422},
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)

    payload = manager.status(refresh=True)

    assert payload["sidecar"]["enabled"] is True
    assert payload["sidecar"]["process"]["listener_running"] is True
    assert payload["sidecar"]["process"]["port"] == 7422


def test_supervisor_restart_sidecar_updates_process_and_optionally_reconnects_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._sidecar_proc = "old-proc"

    async def _restart_sidecar(*, proc, role=None):
        assert proc == "old-proc"
        assert role == "hub"
        return "new-proc", {"ok": True, "accepted": True, "reason": "restarted"}

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    sync_calls: list[bool] = []
    monkeypatch.setattr(
        manager,
        "_sync_sidecar_controlled_files_from_validated_slot",
        lambda: sync_calls.append(True) or {"changed": False},
    )
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _restart_sidecar)
    runtime_requests: list[str] = []

    def _runtime_request(**kwargs):
        path = str(kwargs.get("path") or "")
        runtime_requests.append(path)
        if path == "/api/node/reliability/supervisor-channel":
            return {
                "ok": True,
                "runtime": {
                "readiness_tree": {"root_control": {"status": "ready"}},
                "channel_overview": {"hub_root": {"effective_status": "ready"}},
                "channel_diagnostics": {
                    "root_control": {"status": "ready"},
                    "route": {"status": "ready"},
                },
                "sidecar_runtime": {"transport_owner": "sidecar", "transport_ready": True},
                },
            }
        raise AssertionError(f"unexpected forced reconnect: {path}")

    monkeypatch.setattr(manager, "_runtime_request_json", _runtime_request)
    monkeypatch.setattr(manager, "_runtime_sidecar_runtime_payload", lambda: {"transport_owner": "sidecar"})
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda proc=None: {"listener_running": True, "managed_pid": 77777, "proc": proc},
    )
    persisted: list[bool] = []
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: persisted.append(True))

    payload = asyncio.run(manager.restart_sidecar())

    assert manager._sidecar_proc == "new-proc"
    assert payload["restart"]["accepted"] is True
    assert payload["reconnect"]["ok"] is True
    assert payload["reconnect"]["skipped"] is True
    assert "/api/node/hub-root/reconnect" not in runtime_requests
    assert payload["runtime"]["transport_owner"] == "sidecar"
    assert payload["process"]["proc"] == "new-proc"
    assert payload["transition"]["in_progress"] is False
    assert payload["transition"]["source"] == "operator"
    assert payload["transition"]["outcome"] == "completed"
    assert sync_calls == [True]
    assert persisted


def test_sidecar_restart_waits_for_actual_failback_from_direct_transport(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    probes = 0

    def _runtime_request(**kwargs):
        nonlocal probes
        assert kwargs["path"] == "/api/node/reliability/supervisor-channel"
        probes += 1
        owner = "runtime" if probes == 1 else "sidecar"
        selected_server = (
            "wss://ru.api.inimatic.com/nats"
            if owner == "runtime"
            else "nats://127.0.0.1:7422"
        )
        return {
            "ok": True,
            "runtime": {
                "readiness_tree": {"root_control": {"status": "degraded"}},
                "channel_overview": {"hub_root": {"effective_status": "degraded"}},
                "channel_diagnostics": {
                    "root_control": {"status": "down" if owner == "runtime" else "ready"},
                    "route": {"status": "down" if owner == "runtime" else "ready"},
                },
                "sidecar_runtime": {"transport_owner": owner, "transport_ready": False},
                "hub_root_transport_strategy": {"selected_server": selected_server},
            },
        }

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_runtime_request_json", _runtime_request)

    result = asyncio.run(manager._reconnect_hub_root_after_sidecar_restart())

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "hub_root_already_reconnected"
    assert probes >= 3


def test_sidecar_restart_forces_single_failback_when_direct_transport_persists(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    requests: list[str] = []

    forced = False

    def _runtime_request(**kwargs):
        nonlocal forced
        path = str(kwargs.get("path") or "")
        requests.append(path)
        if path == "/api/node/hub-root/reconnect":
            forced = True
            return {"ok": True, "accepted": True}
        if forced:
            return {
                "ok": True,
                "runtime": {
                    "channel_diagnostics": {
                        "root_control": {"status": "ready"},
                        "route": {"status": "ready"},
                    },
                    "sidecar_runtime": {"transport_owner": "sidecar", "transport_ready": True},
                    "hub_root_transport_strategy": {
                        "selected_server": "nats://127.0.0.1:7422"
                    },
                },
            }
        return {
            "ok": True,
            "runtime": {
                "readiness_tree": {"root_control": {"status": "ready"}},
                "channel_overview": {"hub_root": {"effective_status": "ready"}},
                "sidecar_runtime": {"transport_owner": "runtime", "transport_ready": True},
                "hub_root_transport_strategy": {
                    "selected_server": "wss://ru.api.inimatic.com/nats"
                },
            },
        }

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_runtime_request_json", _runtime_request)
    monkeypatch.setattr(supervisor, "_sidecar_recovery_settle_timeout_sec", lambda: 0.01)
    monkeypatch.setattr(supervisor, "_hub_root_watchdog_verify_timeout_sec", lambda: 0.5)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = asyncio.run(manager._reconnect_hub_root_after_sidecar_restart())

    assert result["ok"] is True
    assert result["forced"] is True
    assert result["reason"] == "hub_root_sidecar_failback_required"
    assert result["verification"]["ok"] is True
    assert requests.count("/api/node/hub-root/reconnect") == 1
    assert requests.count("/api/node/reliability/supervisor-channel") >= 1


def test_sidecar_restart_reports_failed_failback_when_forced_reconnect_does_not_converge(monkeypatch) -> None:
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )

    def _runtime_request(**kwargs):
        if kwargs.get("path") == "/api/node/hub-root/reconnect":
            return {"ok": True, "accepted": True}
        return {
            "ok": True,
            "runtime": {
                "channel_diagnostics": {
                    "root_control": {"status": "ready"},
                    "route": {"status": "degraded"},
                },
                "sidecar_runtime": {"transport_owner": "sidecar", "transport_ready": True},
                "hub_root_transport_strategy": {
                    "selected_server": "nats://127.0.0.1:7422"
                },
            },
        }

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_runtime_request_json", _runtime_request)
    monkeypatch.setattr(supervisor, "_sidecar_recovery_settle_timeout_sec", lambda: 0.001)
    monkeypatch.setattr(supervisor, "_hub_root_watchdog_verify_timeout_sec", lambda: 0.001)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = asyncio.run(manager._reconnect_hub_root_after_sidecar_restart())

    assert result["ok"] is False
    assert result["forced"] is True
    assert result["verification"]["state"] == "not_ready"
    assert result["verification"]["channel_state"]["route_status"] == "degraded"
    assert "did not converge" in result["error"]


def test_supervisor_restart_sidecar_propagates_channel_recovery_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(
        runtime_host="127.0.0.1",
        runtime_port=8777,
        token="dev-local-token",
    )
    manager._sidecar_proc = "old-proc"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_active_sidecar_channel_evidence", lambda: None)
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {})
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(manager, "_runtime_sidecar_runtime_payload", lambda: {})
    monkeypatch.setattr(manager, "_sidecar_status_payload", lambda: {"process": {}})

    async def _restart(**_kwargs):
        return "new-proc", {"ok": True, "accepted": True, "reason": "restarted"}

    async def _reconnect():
        return {"ok": False, "error": "channel did not converge"}

    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _restart)
    monkeypatch.setattr(manager, "_reconnect_hub_root_after_sidecar_restart", _reconnect)

    result = asyncio.run(manager.restart_sidecar())

    assert result["ok"] is False
    assert result["process_restarted"] is True
    assert result["channel_recovered"] is False
    assert result["reconnect"]["error"] == "channel did not converge"


def test_supervisor_restart_sidecar_refuses_to_disrupt_active_channel(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._sidecar_proc = "active-proc"
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        manager,
        "_runtime_sidecar_runtime_payload",
        lambda: {
            "status": "ready",
            "active_session": True,
            "transport_ready": True,
            "remote_session_state": "ready",
            "session_id": "rt-active",
        },
    )

    async def _unexpected_restart(**_kwargs):
        raise AssertionError("active channel must require an explicit disruption override")

    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _unexpected_restart)

    with pytest.raises(supervisor.HTTPException) as exc_info:
        asyncio.run(manager.restart_sidecar())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "active_sidecar_channel"
    assert exc_info.value.detail["channel"]["session_id"] == "rt-active"


def test_supervisor_restart_sidecar_rejects_concurrent_lifecycle_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._sidecar_transition_in_progress = True
    manager._sidecar_transition_id = "sidecar-existing"
    manager._sidecar_transition_source = "monitor"
    manager._sidecar_transition_reason = "supervisor.sidecar.unhealthy"
    monkeypatch.setattr(manager, "_active_sidecar_channel_evidence", lambda: None)

    with pytest.raises(supervisor.HTTPException) as exc_info:
        asyncio.run(manager.restart_sidecar())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "sidecar_transition_in_progress"
    assert exc_info.value.detail["transition"]["transition_id"] == "sidecar-existing"


def test_supervisor_restart_sidecar_clears_failed_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_active_sidecar_channel_evidence", lambda: None)
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {})
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    async def _fail_restart(**_kwargs):
        raise RuntimeError("controlled restart failure")

    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _fail_restart)

    with pytest.raises(RuntimeError, match="controlled restart failure"):
        asyncio.run(manager.restart_sidecar())

    transition = manager._sidecar_transition_payload()
    assert transition["in_progress"] is False
    assert transition["source"] == "operator"
    assert transition["outcome"] == "failed"
    assert transition["error"] == "RuntimeError: controlled restart failure"


def test_supervisor_monitor_recovers_scheduler_after_iteration_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[int] = []
    sleeps: list[float] = []

    async def _iteration_loop() -> None:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("transient monitor failure")
        manager._stopping = True

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(manager, "_monitor_iteration_loop", _iteration_loop)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(supervisor.asyncio, "sleep", _sleep)

    asyncio.run(manager.monitor_forever())

    assert calls == [1, 2]
    assert sleeps == [1.0]
    assert manager._monitor_recovery_total == 1
    assert manager._monitor_failure_total == 1
    assert manager._monitor_last_failure == "RuntimeError: transient monitor failure"


def test_supervisor_monitor_failure_boundary_still_self_heals_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    iteration_calls: list[int] = []
    restart_reasons: list[str] = []

    async def _iteration_loop() -> None:
        iteration_calls.append(len(iteration_calls) + 1)
        if len(iteration_calls) == 1:
            raise NameError("auxiliary monitor defect")
        manager._stopping = True

    async def _restart_runtime(*, reason: str) -> dict[str, object]:
        restart_reasons.append(reason)
        return {"ok": True}

    monkeypatch.setattr(manager, "_monitor_iteration_loop", _iteration_loop)
    monkeypatch.setattr(
        manager,
        "_runtime_self_heal_decision",
        lambda: {
            "reason": "supervisor.runtime.api_unready",
            "message": "runtime API stayed unavailable",
        },
    )
    monkeypatch.setattr(manager, "_record_runtime_self_heal_restart", lambda decision: dict(decision))
    monkeypatch.setattr(manager, "restart_runtime", _restart_runtime)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(manager.monitor_forever())

    assert iteration_calls == [1, 2]
    assert restart_reasons == ["supervisor.runtime.api_unready"]
    assert manager._monitor_last_failure == "NameError: auxiliary monitor defect"


def test_safe_evidence_label_is_available_to_monitor_diagnostics() -> None:
    assert supervisor._safe_evidence_label("runtime/API unhealthy") == "runtime_API_unhealthy"


def test_supervisor_monitor_coalesces_stale_sidecar_sync_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def poll(self):
            return None

    manager._sidecar_proc = _RunningProc()
    manager._sidecar_code_fingerprint = "already-restarted"

    async def _no_sleep(_delay):
        return None

    async def _healthy(*_args, **_kwargs):
        return True

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    async def _unexpected_restart(**_kwargs):
        raise AssertionError("a restart already absorbed under the manager lock must be coalesced")

    reconnect_calls: list[bool] = []

    async def _unexpected_reconnect():
        reconnect_calls.append(True)
        return {"ok": True}

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": True})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "already-restarted"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _healthy)
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)
    monkeypatch.setattr(manager, "_reconnect_hub_root_after_sidecar_restart", _unexpected_reconnect)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _unexpected_restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert reconnect_calls == []


def test_supervisor_monitor_does_not_act_on_stale_sidecar_process_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def __init__(self, name: str) -> None:
            self.name = name

        @staticmethod
        def poll():
            return None

    old_proc = _RunningProc("old")
    new_proc = _RunningProc("operator-replacement")
    manager._sidecar_proc = old_proc
    manager._sidecar_code_fingerprint = "current-fingerprint"

    async def _no_sleep(_delay):
        return None

    async def _stale_unhealthy_probe(*_args, **_kwargs):
        manager._process_supervisor.track_sidecar(new_proc)
        manager._sidecar_consecutive_probe_failures = 2
        return False

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    async def _unexpected_restart(**_kwargs):
        raise AssertionError("a stale health snapshot must not replace the current sidecar generation")

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": False})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "current-fingerprint"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _stale_unhealthy_probe)
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _unexpected_restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert manager._sidecar_proc is new_proc
    assert manager._sidecar_transition_in_progress is False


def test_supervisor_monitor_preserves_sidecar_ownership_during_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ExitedProc:
        @staticmethod
        def poll():
            return 0

    sidecar_proc = _ExitedProc()
    manager._sidecar_proc = sidecar_proc
    manager._sidecar_transition_in_progress = True

    async def _no_sleep(_delay):
        return None

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert manager._sidecar_proc is sidecar_proc


def test_supervisor_monitor_applies_sidecar_code_upgrade_after_runtime_is_stable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def __init__(self, name: str) -> None:
            self.name = name

        def poll(self):
            return None

    old_proc = _RunningProc("old")
    new_proc = _RunningProc("new")
    manager._sidecar_proc = old_proc
    manager._sidecar_code_fingerprint = "old-fingerprint"
    manager._sidecar_code_change_pending_fingerprint = "new-fingerprint"
    manager._sidecar_code_change_pending_since = 1.0

    async def _no_sleep(_delay):
        return None

    async def _healthy(*_args, **_kwargs):
        return True

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    restart_calls: list[tuple[object, str | None, str | None]] = []

    async def _restart(*, proc, role=None, repo_root=None):
        restart_calls.append((proc, role, repo_root))
        return new_proc, {"ok": True, "accepted": True, "reason": "restarted"}

    reconnect_calls: list[bool] = []

    async def _reconnect():
        reconnect_calls.append(True)
        return {"ok": True, "skipped": True, "reason": "hub_root_already_reconnected"}

    async def _upgrade_allowed():
        return True, None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": True})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "new-fingerprint"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _healthy)
    monkeypatch.setattr(manager, "_sidecar_code_upgrade_restart_allowed", _upgrade_allowed)
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)
    monkeypatch.setattr(manager, "_reconnect_hub_root_after_sidecar_restart", _reconnect)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert restart_calls and restart_calls[0][0] is old_proc
    assert reconnect_calls == [True]
    assert manager._sidecar_proc is new_proc
    assert manager._sidecar_code_fingerprint == "new-fingerprint"
    assert manager._sidecar_last_restart_reason == "supervisor.sidecar.code_upgrade"
    assert manager._sidecar_restart_policy_state()["code_upgrade_state"] == "current"
    assert manager._sidecar_transition_in_progress is False
    assert manager._sidecar_transition_source == "monitor"
    assert manager._sidecar_transition_outcome == "completed"


def test_supervisor_monitor_waits_to_upgrade_sidecar_during_runtime_transition(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def poll(self):
            return None

    manager._sidecar_proc = _RunningProc()
    manager._sidecar_code_fingerprint = "old-fingerprint"
    manager._sidecar_code_change_pending_fingerprint = "new-fingerprint"
    manager._sidecar_code_change_pending_since = 1.0

    async def _no_sleep(_delay):
        return None

    async def _healthy(*_args, **_kwargs):
        return True

    async def _upgrade_waiting():
        return False, "supervisor.sidecar.code_upgrade_waiting_transition"

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    async def _unexpected_restart(**_kwargs):
        raise AssertionError("sidecar code upgrade must wait for the active runtime transition")

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": False})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "new-fingerprint"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _healthy)
    monkeypatch.setattr(manager, "_sidecar_code_upgrade_restart_allowed", _upgrade_waiting)
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _unexpected_restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert manager._sidecar_last_restart_reason == "supervisor.sidecar.code_upgrade_waiting_transition"
    policy = manager._sidecar_restart_policy_state()
    assert policy["automatic_code_restart"] is True
    assert policy["code_upgrade_state"] == "waiting_for_runtime_stability"


def test_supervisor_monitor_restarts_confirmed_unhealthy_sidecar(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def __init__(self, name: str) -> None:
            self.name = name

        def poll(self):
            return None

    old_proc = _RunningProc("old")
    new_proc = _RunningProc("new")
    manager._sidecar_proc = old_proc
    manager._sidecar_code_fingerprint = "current-fingerprint"

    async def _no_sleep(_delay):
        return None

    async def _unhealthy(*_args, **_kwargs):
        manager._sidecar_consecutive_probe_failures = 2
        return False

    class _StopMonitor(Exception):
        pass

    async def _stop_after_sidecar_reconcile():
        raise _StopMonitor

    restart_calls: list[object] = []

    async def _restart(*, proc, role=None, repo_root=None):
        restart_calls.append((proc, role, repo_root))
        return new_proc, {"ok": True, "accepted": True, "reason": "restarted"}

    reconnect_calls: list[bool] = []

    async def _reconnect():
        reconnect_calls.append(True)
        return {"ok": True}

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": False})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "current-fingerprint"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _unhealthy)
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _stop_after_sidecar_reconcile)
    monkeypatch.setattr(manager, "_reconnect_hub_root_after_sidecar_restart", _reconnect)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert len(restart_calls) == 1
    assert restart_calls[0][:2] == (old_proc, "hub")
    assert restart_calls[0][2]
    assert reconnect_calls == [True]
    assert manager._sidecar_proc is new_proc
    assert manager._sidecar_code_fingerprint == "current-fingerprint"


def test_supervisor_monitor_honors_sidecar_restart_circuit_breaker(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _RunningProc:
        def poll(self):
            return None

    manager._sidecar_proc = _RunningProc()
    manager._proc = _RunningProc()
    manager._sidecar_code_fingerprint = "current-fingerprint"

    async def _no_sleep(_delay):
        return None

    async def _unhealthy(*_args, **_kwargs):
        manager._sidecar_consecutive_probe_failures = 2
        return False

    class _StopMonitor(Exception):
        pass

    continue_calls = 0

    async def _continue_transition():
        nonlocal continue_calls
        continue_calls += 1
        if continue_calls >= 2:
            raise _StopMonitor

    async def _unexpected_restart(**_kwargs):
        raise AssertionError("circuit breaker must suppress the sidecar restart")

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_sync_sidecar_controlled_files_from_validated_slot", lambda: {"changed": False})
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "current-fingerprint"})
    monkeypatch.setattr(manager, "_probe_sidecar_health", _unhealthy)
    monkeypatch.setattr(
        manager,
        "_sidecar_restart_allowed",
        lambda: (False, "supervisor.sidecar.circuit_open"),
    )
    monkeypatch.setattr(manager, "_maybe_resume_or_continue_transition", _continue_transition)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(supervisor, "restart_realtime_sidecar_subprocess", _unexpected_restart)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {"listener_running": True},
    )

    with pytest.raises(_StopMonitor):
        asyncio.run(manager._monitor_iteration_loop())

    assert continue_calls == 2
    assert manager._sidecar_last_restart_reason == "supervisor.sidecar.circuit_open"


def test_supervisor_adopted_sidecar_keeps_persisted_code_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ExistingProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        @staticmethod
        def poll():
            return None

    async def _ready(**_kwargs):
        return True

    monkeypatch.setattr(supervisor, "_AdoptedProcess", _ExistingProc)
    monkeypatch.setattr(supervisor, "probe_realtime_sidecar_ready", _ready)
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda *_args, **_kwargs: {
            "listener_running": True,
            "listener_pid": 4242,
            "host": "127.0.0.1",
            "port": 7422,
        },
    )
    monkeypatch.setattr(
        supervisor,
        "_read_json",
        lambda _path: {
            "sidecar": {
                "process": {"listener_pid": 4242},
                "code": {
                    "active_fingerprint": "running-generation",
                    "active_updated_at": 123.0,
                },
            }
        },
    )
    monkeypatch.setattr(manager, "_sidecar_code_state", lambda: {"fingerprint": "promoted-generation"})
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)

    asyncio.run(manager._spawn_sidecar_locked())

    assert manager._sidecar_proc is not None
    assert manager._sidecar_proc.pid == 4242
    assert manager._sidecar_code_fingerprint == "running-generation"
    assert manager._sidecar_code_fingerprint_updated_at == 123.0


def test_supervisor_sidecar_health_uses_managed_listener_snapshot_without_tcp_probe(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._sidecar_proc = object()

    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda proc=None, role=None: {
            "listener_running": True,
            "managed_alive": True,
            "listener_matches_managed": True,
            "host": "127.0.0.1",
            "port": 7422,
        },
    )

    async def _unexpected_probe(**kwargs):
        raise AssertionError("managed sidecar health must not open the NATS listener")

    monkeypatch.setattr(supervisor, "probe_realtime_sidecar_ready", _unexpected_probe)

    assert asyncio.run(manager._probe_sidecar_health(force=True)) is True
    assert manager._sidecar_last_probe_ok is True
    assert manager._sidecar_consecutive_probe_failures == 0


def test_supervisor_sidecar_status_does_not_query_runtime_reliability(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_REALTIME_ENABLE", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    def _runtime_reliability_unavailable(**kwargs):
        raise AssertionError("sidecar status must not depend on the runtime reliability API")

    monkeypatch.setattr(manager, "_runtime_reliability_payload", _runtime_reliability_unavailable)
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    route_tunnel_contract = {
        "lifecycle_manager": "supervisor",
        "ws": {
            "current_owner": "sidecar",
            "planned_owner": "sidecar",
            "handoff_ready": True,
            "listener_ready": True,
            "delegation_mode": "local_proxy",
            "blockers": [],
        },
        "yws": {
            "current_owner": "sidecar",
            "planned_owner": "sidecar",
            "handoff_ready": True,
            "listener_ready": True,
            "delegation_mode": "local_proxy",
            "blockers": [],
        },
    }
    diag_path = supervisor.realtime_sidecar_diag_path()
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    diag_path.write_text(
        json.dumps(
                {
                    "ts": time.time(),
                    "active_session": True,
                    "remote_connected_ago_s": 0.2,
                "session_id": "test-session",
                "remote_url": "ws://root.test/ws",
                "enablement_policy": {
                    "role": None,
                    "enabled": True,
                    "default_enabled": False,
                    "explicit": True,
                    "source": "env_override",
                    "env_var": "ADAOS_REALTIME_ENABLE",
                    "env_value": "1",
                    "reason": "ADAOS_REALTIME_ENABLE=1",
                },
                "route_tunnel_contract": route_tunnel_contract,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        supervisor,
        "realtime_sidecar_listener_snapshot",
        lambda proc=None, role=None: {
            "listener_running": True,
            "managed_alive": True,
            "listener_matches_managed": True,
            "host": "127.0.0.1",
            "port": 7422,
            "enablement_policy": {
                "role": "hub",
                "enabled": True,
                "default_enabled": True,
                "explicit": False,
                "source": "role_default",
                "env_var": None,
                "env_value": None,
                "reason": "hub runtimes use sidecar as the default realtime transport",
            },
            "route_tunnel_contract": {
                "lifecycle_manager": "supervisor",
                "ws": {
                    "current_owner": "runtime",
                    "planned_owner": "sidecar",
                    "handoff_ready": False,
                    "listener_ready": False,
                    "blockers": ["stale supervisor-local route snapshot"],
                },
                "yws": {
                    "current_owner": "runtime",
                    "planned_owner": "sidecar",
                    "handoff_ready": False,
                    "listener_ready": False,
                    "blockers": ["stale supervisor-local yws snapshot"],
                },
            },
        },
    )

    payload = manager.sidecar_status()

    assert payload["ok"] is True
    assert payload["runtime"]["status"] == "ready"
    assert payload["runtime"]["control_ready"] == "ready"
    assert payload["runtime"]["route_ready"] == "ready"
    assert payload["runtime"]["sync_ready"] == "ready"
    assert payload["runtime"]["progress"]["state"] == "ready"
    assert payload["runtime"]["continuity_contract"]["current_support"] == "ready"
    assert payload["runtime"]["transport_provenance"]["session_id"] == "test-session"
    assert payload["runtime"]["route_tunnel_contract"]["ws"]["blockers"] == []
    assert payload["runtime"]["route_tunnel_contract"]["yws"]["blockers"] == []
    assert payload["runtime"]["enablement"]["source"] == "role_default"
    assert payload["runtime"]["enablement"]["default_enabled"] is True
    assert payload["process"]["enablement_policy"]["source"] == "role_default"
    assert payload["process"]["route_tunnel_contract"]["ws"]["current_owner"] == "sidecar"
    assert payload["process"]["route_tunnel_contract"]["yws"]["handoff_ready"] is True


def test_runtime_state_payload_surfaces_root_promotion_requirement(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)

    payload = manager.status(refresh=True)

    assert payload["root_promotion_required"] is True
    assert "src/adaos/apps/supervisor.py" in payload["bootstrap_update"]["changed_paths"]


def test_runtime_state_payload_clears_root_promotion_requirement_when_root_matches_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    root_dir = tmp_path / "root"
    slot_repo = tmp_path / "slots" / "B" / "repo"
    (root_dir / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (slot_repo / "src" / "adaos" / "apps").mkdir(parents=True, exist_ok=True)
    (root_dir / "src" / "adaos" / "apps" / "supervisor.py").write_text("same\n", encoding="utf-8")
    (slot_repo / "src" / "adaos" / "apps" / "supervisor.py").write_text("same\n", encoding="utf-8")

    manager._proc = _Proc()
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "repo_dir": str(slot_repo),
            "root_repo_root": str(root_dir),
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)

    payload = manager.status(refresh=True)

    assert payload["root_promotion_required"] is False
    assert payload["bootstrap_update"]["required"] is True
    assert payload["bootstrap_update"]["effective_required"] is False
    assert payload["bootstrap_update"]["effective_mismatched_paths"] == []


def test_runtime_self_heal_decision_restarts_after_listener_loss_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_RUNTIME_STARTUP_GRACE_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_listener_restart_timeout_sec", lambda: 45.0)

    assert manager._runtime_self_heal_decision(now=120.0) is None
    assert manager._runtime_unhealthy_kind == "listener_lost"
    assert manager._runtime_unhealthy_since == 120.0
    assert manager._runtime_self_heal_decision(now=160.0) is None

    payload = manager._runtime_self_heal_decision(now=166.0)

    assert payload is not None
    assert payload["reason"] == "supervisor.runtime.listener_lost"
    assert payload["runtime_port"] == 8778


def test_runtime_self_heal_decision_respects_listener_startup_grace(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_runtime_listener_restart_timeout_sec", lambda: 45.0)
    monkeypatch.setattr(supervisor, "_runtime_listener_startup_grace_sec", lambda: 90.0)

    assert manager._runtime_self_heal_decision(now=120.0) is None
    assert manager._runtime_unhealthy_kind == "listener_lost"
    assert manager._runtime_unhealthy_since == 120.0
    assert manager._runtime_self_heal_decision(now=160.0) is None
    assert manager._runtime_self_heal_decision(now=189.0) is None

    payload = manager._runtime_self_heal_decision(now=191.0)

    assert payload is not None
    assert payload["reason"] == "supervisor.runtime.listener_lost"
    assert payload["runtime_port"] == 8778


def test_runtime_self_heal_decision_restarts_after_api_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        supervisor,
        "_runtime_api_probe",
        lambda *args, **kwargs: {"ready": False, "runtime": {}, "error_type": "Timeout"},
    )
    monkeypatch.setattr(supervisor, "_runtime_api_restart_timeout_sec", lambda: 60.0)

    assert manager._runtime_self_heal_decision(now=110.0) is None
    assert manager._runtime_unhealthy_kind == "api_unready"
    assert manager._runtime_unhealthy_since == 110.0

    payload = manager._runtime_self_heal_decision(now=171.0)

    assert payload is not None
    assert payload["reason"] == "supervisor.runtime.api_unready"
    assert payload["runtime_port"] == 8777


def test_runtime_self_heal_restart_records_compact_evidence(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._managed_runtime_instance_id = "rt-test"
    calls = []

    def _fake_capture(**kwargs):
        calls.append(dict(kwargs))
        return {
            "captured_at": 123.0,
            "reason": kwargs["reason"],
            "stage": kwargs["stage"],
            "pid": 32123,
            "runtime_instance_id": "rt-test",
            "transition_role": "active",
            "evidence_path": str(tmp_path / "evidence.json"),
            "memory": {
                "process_rss_bytes": 100,
                "family_rss_bytes": 200,
                "cgroup_memory_current_bytes": 300,
            },
            "process": {
                "available": True,
                "state": "D (disk sleep)",
                "wchan": "jbd2_log_wait_commit",
                "threads_total": 2,
                "threads_returned": 1,
                "threads": [{"tid": 32123, "state": "D (disk sleep)", "wchan": "jbd2_log_wait_commit"}],
            },
        }

    monkeypatch.setattr(manager, "_capture_runtime_stop_evidence", _fake_capture)
    monkeypatch.setattr(supervisor.time, "time", lambda: 222.0)

    payload = manager._record_runtime_self_heal_restart(
        {
            "reason": "supervisor.runtime.api_unready",
            "message": "runtime API stayed unavailable",
            "runtime_port": 8777,
        }
    )

    assert calls
    assert calls[0]["reason"] == "supervisor.runtime.api_unready"
    assert calls[0]["stage"] == "runtime_self_heal_restart"
    assert calls[0]["decision"]["recorded_at"] == 222.0
    assert payload["recorded_at"] == 222.0
    evidence = payload["pre_restart_evidence"]
    assert evidence["evidence_path"] == str(tmp_path / "evidence.json")
    assert evidence["memory"]["family_rss_bytes"] == 200
    assert evidence["process"]["state"] == "D (disk sleep)"
    assert evidence["process"]["threads"][0]["wchan"] == "jbd2_log_wait_commit"
    status = manager._runtime_self_heal_status_payload()
    assert status["last_decision"]["reason"] == "supervisor.runtime.api_unready"
    assert status["last_evidence"]["process"]["wchan"] == "jbd2_log_wait_commit"


def test_runtime_self_heal_decision_skips_listener_restart_while_update_apply_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._last_start_at = 100.0
    manager._runtime_unhealthy_since = 120.0
    manager._runtime_unhealthy_kind = "listener_lost"

    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {"state": "applying", "phase": "apply"})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: False)

    payload = manager._runtime_self_heal_decision(now=200.0)

    assert payload is None
    assert manager._runtime_unhealthy_since is None
    assert manager._runtime_unhealthy_kind is None


def test_runtime_self_heal_decision_restarts_slot_mismatch_even_when_apply_status_is_stale(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._managed_runtime_cwd = "/slots/B/repo"
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {"state": "applying", "phase": "apply"})
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "A", "argv": ["/slots/A/venv/bin/python"], "cwd": "/slots/A/repo"},
    )
    monkeypatch.setattr(
        supervisor,
        "_proc_details",
        lambda proc, cwd_hint=None: {
            "managed_pid": 4321,
            "managed_alive": True,
            "managed_cmdline": ["/slots/B/venv/bin/python", "-m", "adaos.apps.autostart_runner"],
            "managed_executable": "/slots/B/venv/bin/python",
            "managed_cwd": "/slots/B/repo",
        },
    )

    payload = manager._runtime_self_heal_decision(now=200.0)

    assert isinstance(payload, dict)
    assert payload["reason"] == "supervisor.runtime.slot_mismatch"
    assert payload["active_slot"] == "A"
    assert payload["managed_executable"] == "/slots/B/venv/bin/python"
    assert payload["expected_managed_executable"] == "/slots/A/venv/bin/python"


def test_runtime_self_heal_refreshes_lost_adopted_runtime_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["/uv/python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._managed_runtime_cwd = "/slots/A/repo"
    manager._managed_runtime_instance_id = "rt-a-a-existing"
    manager._managed_transition_role = "active"
    manager._managed_slot = "A"
    manager._managed_runtime_api_identity_verified = False
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {"state": "succeeded", "phase": "validate"})
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "A", "argv": ["/slots/A/venv/bin/python"], "cwd": "/slots/A/repo"},
    )
    monkeypatch.setattr(
        supervisor,
        "_proc_details",
        lambda proc, cwd_hint=None: {
            "managed_pid": 32123,
            "managed_alive": True,
            "managed_cmdline": ["/uv/python", "-m", "adaos.apps.autostart_runner"],
            "managed_executable": "/uv/python",
            "managed_cwd": "/slots/A/repo",
        },
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        supervisor,
        "_runtime_api_probe",
        lambda *args, **kwargs: {
            "ready": True,
            "runtime": {
                "runtime_instance_id": "rt-a-a-existing",
                "transition_role": "active",
                "slot": "A",
            },
            "error_type": None,
        },
    )

    assert manager._runtime_self_heal_decision(now=200.0) is None
    assert manager._managed_runtime_api_identity_verified is True
    assert manager._managed_runtime_api_identity["runtime_instance_id"] == "rt-a-a-existing"


def test_runtime_self_heal_keeps_verified_identity_during_transient_api_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["/uv/python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    manager._desired_running = True
    manager._managed_runtime_cwd = "/slots/A/repo"
    manager._managed_runtime_instance_id = "rt-a-a-existing"
    manager._managed_transition_role = "active"
    manager._managed_slot = "A"
    manager._managed_runtime_api_identity_verified = True
    manager._last_start_at = 100.0

    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {"state": "succeeded", "phase": "validate"})
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "A", "argv": ["/slots/A/venv/bin/python"], "cwd": "/slots/A/repo"},
    )
    monkeypatch.setattr(
        supervisor,
        "_proc_details",
        lambda proc, cwd_hint=None: {
            "managed_pid": 32123,
            "managed_alive": True,
            "managed_cmdline": ["/uv/python", "-m", "adaos.apps.autostart_runner"],
            "managed_executable": "/uv/python",
            "managed_cwd": "/slots/A/repo",
        },
    )
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        supervisor,
        "_runtime_api_probe",
        lambda *args, **kwargs: {"ready": False, "runtime": {}, "error_type": "ReadTimeout"},
    )

    assert manager._runtime_self_heal_decision(now=200.0) is None
    assert manager._runtime_unhealthy_kind == "api_unready"
    assert manager._runtime_unhealthy_since == 200.0
    assert manager._managed_runtime_api_identity_verified is True


def test_runtime_state_payload_surfaces_warm_switch_admission(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    class _Psutil:
        class Process:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def memory_info(self):
                return type("Mem", (), {"rss": 256 * 1024 * 1024})()

        @staticmethod
        def virtual_memory():
            return type("VM", (), {"available": 1024 * 1024 * 1024})()

    manager._proc = _Proc()
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "planned_reason": "minimum_update_period",
        }
    )
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "choose_inactive_slot", lambda: "B")
    monkeypatch.setattr(supervisor, "psutil", _Psutil)

    payload = manager.status(refresh=True)

    assert payload["runtime_port"] == 8777
    assert payload["candidate_slot"] == "B"
    assert payload["candidate_runtime_port"] == 8778
    assert payload["transition_mode"] == "warm_switch"
    assert payload["warm_switch_supported"] is True
    assert payload["warm_switch_allowed"] is True
    assert payload["slot_ports"]["A"] == 8777
    assert payload["slot_ports"]["B"] == 8778


def test_runtime_state_payload_falls_back_to_stop_and_switch_when_memory_is_low(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    class _Psutil:
        class Process:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def memory_info(self):
                return type("Mem", (), {"rss": 256 * 1024 * 1024})()

        @staticmethod
        def virtual_memory():
            return type("VM", (), {"available": 300 * 1024 * 1024})()

    manager._proc = _Proc()
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
        }
    )
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "choose_inactive_slot", lambda: "B")
    monkeypatch.setattr(supervisor, "psutil", _Psutil)

    payload = manager.status(refresh=True)

    assert payload["candidate_slot"] == "B"
    assert payload["transition_mode"] == "stop_and_switch"
    assert payload["warm_switch_allowed"] is False
    assert "insufficient memory" in str(payload["warm_switch_reason"] or "")


def test_runtime_state_payload_uses_process_family_rss_for_warm_switch_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    class _PsChild:
        def __init__(self, pid: int, rss: int) -> None:
            self.pid = pid
            self._rss = rss

        def memory_info(self):
            return type("Mem", (), {"rss": self._rss})()

    class _Psutil:
        class Process:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def memory_info(self):
                if self.pid == 32123:
                    return type("Mem", (), {"rss": 128 * 1024 * 1024})()
                raise AssertionError(f"unexpected pid {self.pid}")

            def children(self, recursive: bool = False):
                assert recursive is True
                return [
                    _PsChild(40001, 256 * 1024 * 1024),
                    _PsChild(40002, 256 * 1024 * 1024),
                ]

        @staticmethod
        def virtual_memory():
            return type("VM", (), {"available": 900 * 1024 * 1024})()

    manager._proc = _Proc()
    write_status(
        {
            "state": "planned",
            "phase": "scheduled",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
        }
    )
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "choose_inactive_slot", lambda: "B")
    monkeypatch.setattr(supervisor, "psutil", _Psutil)

    payload = manager.status(refresh=True)

    assert payload["candidate_slot"] == "B"
    assert payload["warm_switch_allowed"] is False
    assert payload["transition_mode"] == "stop_and_switch"
    assert payload["warm_switch_memory"]["current_process_rss_bytes"] == 128 * 1024 * 1024
    assert payload["warm_switch_memory"]["current_family_rss_bytes"] == 640 * 1024 * 1024
    assert payload["warm_switch_memory"]["current_rss_bytes"] == 640 * 1024 * 1024


def test_validated_candidate_owns_root_promotion_execution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    root_repo = tmp_path / "root"
    root_repo.mkdir()
    slot_root = tmp_path / "state" / "core_slots" / "slots" / "B"
    repo_dir = slot_root / "repo"
    runner = repo_dir / "src" / "adaos" / "apps" / "core_update_root_promote.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("# candidate runner\n", encoding="utf-8")
    candidate_python = slot_root / "venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(supervisor, "current_base_dir", lambda: tmp_path)
    monkeypatch.setattr(supervisor, "slot_dir", lambda slot: slot_root)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "slot": "B", "transaction_state": "committed"}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(supervisor.subprocess, "run", _run)

    payload = supervisor._promote_root_with_validated_candidate(
        slot="B",
        manifest={
            "repo_dir": str(repo_dir),
            "root_repo_root": str(root_repo),
            "argv": [str(candidate_python), "-m", "adaos.apps.autostart_runner"],
        },
        runtime_host="127.0.0.1",
        runtime_port=8777,
    )

    assert payload["execution_owner"] == "validated_candidate"
    assert payload["transaction_state"] == "committed"
    command, kwargs = calls[0]
    assert command[0] == str(candidate_python)
    assert command[1:3] == ["-m", "adaos.apps.core_update_root_promote"]
    assert command[-4:] == ["--runtime-host", "127.0.0.1", "--runtime-port", "8777"]
    assert kwargs["cwd"] == str(repo_dir)
    assert kwargs["env"]["PYTHONPATH"] == str(repo_dir / "src")


def test_validated_candidate_root_promotion_rejects_paths_outside_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    outside_repo = tmp_path / "outside" / "repo"
    outside_python = tmp_path / "outside" / "venv" / "bin" / "python"

    with pytest.raises(RuntimeError, match="do not belong"):
        supervisor._promote_root_with_validated_candidate(
            slot="A",
            manifest={"repo_dir": str(outside_repo), "argv": [str(outside_python)]},
            runtime_host="127.0.0.1",
            runtime_port=8777,
        )


def test_supervisor_promote_root_marks_update_succeeded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_version": "1.2.3",
            "git_commit": "1.2.3",
            "git_short_commit": "1.2.3",
            "repo_dir": str(tmp_path / "slots" / "B" / "repo"),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "_promote_root_with_validated_candidate",
        lambda *, slot, manifest, runtime_host, runtime_port: {
            "ok": True,
            "slot": slot or "B",
            "required": True,
            "changed_paths": ["src/adaos/apps/supervisor.py"],
            "backup_dir": str(tmp_path / "backup"),
            "promoted_paths": ["src/adaos/apps/supervisor.py"],
            "removed_paths": [],
            "restart_required": True,
        },
    )
    supervisor._write_update_attempt({"state": "active", "action": "update", "updated_at": 1.0})
    write_status({"state": "validated", "phase": "root_promotion_pending", "target_slot": "B"})

    payload = asyncio.run(manager.promote_root(reason="test.root_promotion"))

    assert payload["accepted"] is True
    assert payload["status"]["state"] == "succeeded"
    assert payload["status"]["phase"] == "root_promoted"
    assert payload["root_promotion"]["restart_required"] is True
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "awaiting_root_restart"
    assert attempt["last_status"]["phase"] == "root_promoted"


def test_supervisor_root_promotion_does_not_block_event_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "repo_dir": str(tmp_path / "slots" / "B" / "repo"),
            "bootstrap_update": {"required": True, "changed_paths": ["src/adaos/apps/supervisor.py"]},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "resolved_root_promotion_requirement",
        lambda _manifest: (True, {"required": True, "effective_required": True}),
    )

    promotion_started = threading.Event()
    allow_promotion_to_finish = threading.Event()

    def _slow_promotion(**kwargs):
        promotion_started.set()
        if not allow_promotion_to_finish.wait(timeout=5.0):
            raise TimeoutError("test did not release root promotion worker")
        return {"ok": True, "slot": kwargs["slot"], "required": True, "restart_required": True}

    monkeypatch.setattr(supervisor, "_promote_root_with_validated_candidate", _slow_promotion)
    supervisor._write_update_attempt({"state": "active", "action": "update", "updated_at": 1.0})
    write_status({"state": "validated", "phase": "root_promotion_pending", "target_slot": "B"})

    async def _exercise() -> dict[str, object]:
        task = asyncio.create_task(manager.promote_root(reason="test.nonblocking"))
        try:
            for _ in range(500):
                if promotion_started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert promotion_started.is_set()
            assert not task.done()
            assert read_status()["state"] == "applying"
            assert read_status()["phase"] == "root_promotion"
            attempt = supervisor._read_update_attempt()
            assert isinstance(attempt, dict)
            assert attempt["state"] == "active"
            assert attempt["last_status"]["phase"] == "root_promotion"
        finally:
            allow_promotion_to_finish.set()
        return await task

    payload = asyncio.run(_exercise())

    assert payload["accepted"] is True


def test_supervisor_promote_root_preserves_subsequent_transition_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "target_version": "1.2.3",
            "git_commit": "1.2.3",
            "git_short_commit": "1.2.3",
            "repo_dir": str(tmp_path / "slots" / "B" / "repo"),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(
        supervisor,
        "_promote_root_with_validated_candidate",
        lambda *, slot, manifest, runtime_host, runtime_port: {
            "ok": True,
            "slot": slot or "B",
            "required": True,
            "restart_required": True,
            "changed_paths": ["src/adaos/apps/supervisor.py"],
        },
    )
    supervisor._write_update_attempt(
        {
            "state": "active",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "1.2.3",
            "reason": "test.update",
            "subsequent_transition": True,
            "subsequent_transition_requested_at": 410.0,
            "subsequent_transition_request": {
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "1.2.4",
                "reason": "test.subsequent",
            },
            "updated_at": 400.0,
        }
    )
    write_status({"state": "validated", "phase": "root_promotion_pending", "target_slot": "B"})

    payload = asyncio.run(manager.promote_root(reason="test.root_promotion"))

    assert payload["status"]["phase"] == "root_promoted"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "awaiting_root_restart"
    assert attempt["subsequent_transition"] is True
    assert attempt["subsequent_transition_request"]["target_version"] == "1.2.4"


def test_supervisor_promote_root_allows_idle_status_when_root_promotion_is_still_required(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "repo_dir": str(tmp_path / "slots" / "B" / "repo"),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(
        supervisor,
        "resolved_root_promotion_requirement",
        lambda manifest: (
            True,
            {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
                "effective_required": True,
            },
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "_promote_root_with_validated_candidate",
        lambda *, slot, manifest, runtime_host, runtime_port: {
            "ok": True,
            "slot": slot or "B",
            "required": True,
            "changed_paths": ["src/adaos/apps/supervisor.py"],
            "backup_dir": str(tmp_path / "backup"),
            "promoted_paths": ["src/adaos/apps/supervisor.py"],
            "removed_paths": [],
            "restart_required": True,
        },
    )
    write_status({"state": "idle", "message": "autostart runner boot"})

    payload = asyncio.run(manager.promote_root(reason="test.root_promotion"))

    assert payload["accepted"] is True
    assert payload["status"]["phase"] == "root_promoted"
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "awaiting_root_restart"


def test_supervisor_schedule_service_restart_requests_self_exit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "_autostart_self_restart_supported", lambda: True)
    monkeypatch.setattr(supervisor, "_root_restart_delay_sec", lambda: 0.1)
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 4321)
    monkeypatch.setattr(manager, "_refresh_autostart_wrapper", lambda reason: {"ok": True, "reason": reason})

    sleeps: list[float] = []
    kills: list[tuple[int, int]] = []

    monkeypatch.setattr(supervisor.time, "sleep", lambda sec: sleeps.append(sec))
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    payload = manager._schedule_service_restart(reason="test.root_restart")

    thread = manager._service_restart_thread
    assert thread is not None
    thread.join(timeout=1.0)

    assert payload["requested"] is True
    assert payload["mode"] == "self_exit"
    assert payload["wrapper_refresh"] == {"ok": True, "reason": "test.root_restart"}
    assert sleeps == [0.1]
    assert kills == [(4321, supervisor.signal.SIGTERM)]


def test_supervisor_service_restart_defers_wrapper_refresh_until_after_accept(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "_autostart_self_restart_supported", lambda: True)
    monkeypatch.setattr(supervisor, "_root_restart_delay_sec", lambda: 0.1)
    monkeypatch.setattr(supervisor.os, "getpid", lambda: 4321)
    refresh_started = threading.Event()
    refresh_release = threading.Event()
    kills: list[tuple[int, int]] = []

    def _refresh(*, reason: str) -> dict[str, object]:
        refresh_started.set()
        assert refresh_release.wait(timeout=1.0)
        return {"ok": True, "reason": reason}

    monkeypatch.setattr(manager, "_refresh_autostart_wrapper", _refresh)
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: kills.append((pid, sig)))

    payload = manager.restart_service(reason="test.operator_restart")

    assert payload["accepted"] is True
    assert payload["restart"]["wrapper_refresh"]["scheduled"] is True
    assert refresh_started.wait(timeout=0.5)
    assert kills == []
    refresh_release.set()
    thread = manager._service_restart_thread
    assert thread is not None
    thread.join(timeout=1.0)
    assert kills == [(4321, supervisor.signal.SIGTERM)]


def test_supervisor_service_restart_rejects_active_core_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ActiveTask:
        @staticmethod
        def done() -> bool:
            return False

    manager._update_task = _ActiveTask()

    payload = manager.restart_service(reason="test.operator_restart")

    assert payload["ok"] is False
    assert payload["accepted"] is False
    assert payload["reason"] == "core_update_active"


def test_supervisor_restart_keeps_candidate_generated_wrapper(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._service_restart_pending = True
    monkeypatch.setattr(supervisor, "_autostart_self_restart_supported", lambda: True)
    monkeypatch.setattr(
        manager,
        "_refresh_autostart_wrapper",
        lambda reason: (_ for _ in ()).throw(AssertionError("old supervisor must not rewrite candidate wrapper")),
    )
    candidate_refresh = {"ok": True, "wrapper": str(tmp_path / "bin" / "adaos-autostart.sh")}

    payload = manager._schedule_service_restart(
        reason="test.candidate_wrapper",
        candidate_wrapper_refresh=candidate_refresh,
    )

    assert payload["duplicate"] is True
    assert payload["wrapper_refresh"] == candidate_refresh


def test_supervisor_complete_update_promotes_root_and_requests_self_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "repo_dir": str(tmp_path / "slots" / "B" / "repo"),
            "bootstrap_update": {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
            },
        },
    )
    monkeypatch.setattr(
        supervisor,
        "resolved_root_promotion_requirement",
        lambda manifest: (
            True,
            {
                "required": True,
                "changed_paths": ["src/adaos/apps/supervisor.py"],
                "effective_required": True,
            },
        ),
    )
    monkeypatch.setattr(
        supervisor,
        "_promote_root_with_validated_candidate",
        lambda *, slot, manifest, runtime_host, runtime_port: {
            "ok": True,
            "slot": slot or "B",
            "required": True,
            "changed_paths": ["src/adaos/apps/supervisor.py"],
            "backup_dir": str(tmp_path / "backup"),
            "promoted_paths": ["src/adaos/apps/supervisor.py"],
            "removed_paths": [],
            "restart_required": True,
        },
    )
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "root_promotion_required": str(read_status().get("phase") or "").strip().lower() == "root_promotion_pending",
            "active_slot": "B",
            "runtime_state": "ready",
            "runtime_url": "http://127.0.0.1:8778",
            "runtime_port": 8778,
        },
    )

    async def _terminal_migration(*, timeout=None):  # noqa: ANN001, ARG001
        return {
            "skill_runtime_migration": {
                "state": "failed",
                "phase": "complete",
                "pending": False,
                "failed_total": 1,
            }
        }

    monkeypatch.setattr(manager, "_runtime_update_gate_payload_async", _terminal_migration)

    restart_reasons: list[str] = []

    def _schedule_service_restart(
        *, reason: str, candidate_wrapper_refresh: dict[str, object] | None = None
    ) -> dict[str, object]:
        restart_reasons.append(reason)
        return {"ok": True, "requested": True, "mode": "self_exit", "delay_sec": 0.25}

    monkeypatch.setattr(manager, "_schedule_service_restart", _schedule_service_restart)

    supervisor._write_update_attempt({"state": "active", "action": "update", "requested_at": 1.0, "updated_at": 1.0})
    write_status({"state": "validated", "phase": "root_promotion_pending", "action": "update", "target_slot": "B"})

    payload = asyncio.run(manager.complete_update(reason="test.complete"))

    assert payload["accepted"] is True
    assert payload["restart_required"] is True
    assert payload["status"]["phase"] == "root_promoted"
    assert payload["status"]["root_promotion_required"] is False
    assert payload["status"]["restart_mode"] == "self_exit"
    assert payload["restart"]["requested"] is True
    assert payload["runtime"]["root_promotion_required"] is False
    assert restart_reasons == ["test.complete"]
    attempt = supervisor._read_update_attempt()
    assert isinstance(attempt, dict)
    assert attempt["state"] == "awaiting_root_restart"
    assert attempt["restart_mode"] == "self_exit"
    assert attempt["restart_requested_at"] > 0
    assert attempt["root_promotion_supervisor_instance_id"] == supervisor._SUPERVISOR_INSTANCE_ID
    assert attempt["restart_requested_by_instance_id"] == supervisor._SUPERVISOR_INSTANCE_ID
    assert payload["status"]["restart_requested_by_instance_id"] == supervisor._SUPERVISOR_INSTANCE_ID


def test_complete_update_persists_restart_markers_before_arming_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "active_slot": "B",
            "runtime_state": "ready",
            "runtime_url": "http://127.0.0.1:8778",
            "runtime_port": 8778,
        },
    )

    def _interrupt_restart(**_kwargs):
        raise SystemExit("simulated immediate supervisor termination")

    monkeypatch.setattr(manager, "_schedule_service_restart", _interrupt_restart)
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "action": "update",
            "target_slot": "B",
            "target_version": "target-build",
            "root_promotion_required": False,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "target_version": "target-build",
            "awaiting_restart": True,
            "restart_required": True,
            "updated_at": 10.0,
        }
    )

    with pytest.raises(SystemExit, match="simulated immediate supervisor termination"):
        asyncio.run(manager.complete_update(reason="test.interrupted_restart"))

    status = read_status()
    attempt = supervisor._read_update_attempt()
    assert status["state"] == "succeeded"
    assert status["phase"] == "root_promoted"
    assert status["restart_mode"] == "scheduling"
    assert status["restart_requested_at"] > 0
    assert isinstance(attempt, dict)
    assert attempt["state"] == "awaiting_root_restart"
    assert attempt["restart_mode"] == "scheduling"
    assert attempt["restart_requested_at"] > 0


def test_supervisor_complete_update_defers_root_promotion_during_skill_migration(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "root_promotion_required": True,
            "active_slot": "B",
            "runtime_state": "ready",
            "runtime_url": "http://127.0.0.1:8778",
            "runtime_port": 8778,
        },
    )

    async def _pending_migration(*, timeout=None):  # noqa: ANN001, ARG001
        return {
            "skill_runtime_migration": {
                "operation_id": "skill-migrate-test",
                "state": "running",
                "phase": "tests",
                "pending": True,
                "current": {"skill": "media_indexer_skill", "stage": "tests"},
                "completed_total": 3,
                "total": 7,
            }
        }

    async def _unexpected_promote_root(*, reason):  # noqa: ANN001, ARG001
        raise AssertionError("root promotion must wait for skill migration")

    monkeypatch.setattr(manager, "_runtime_update_gate_payload_async", _pending_migration)
    monkeypatch.setattr(manager, "promote_root", _unexpected_promote_root)
    supervisor._write_update_attempt({"state": "active", "action": "update", "updated_at": 1.0})
    write_status({"state": "validated", "phase": "root_promotion_pending", "action": "update"})

    payload = asyncio.run(manager.complete_update(reason="supervisor.auto_update_complete", auto=True))

    assert payload["accepted"] is False
    assert payload["deferred"] is True
    assert payload["retryable"] is True
    assert payload["promotion_gate"]["reason"] == "skill_runtime_migration_pending"
    assert payload["promotion_gate"]["migration"]["operation_id"] == "skill-migrate-test"
    assert payload["restart"]["requested"] is False
    assert read_status()["phase"] == "root_promotion_pending"


def test_supervisor_auto_complete_does_not_repeat_root_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(
        manager,
        "status",
        lambda: {
            "active_slot": "B",
            "runtime_state": "starting",
            "runtime_url": "http://127.0.0.1:8778",
            "runtime_port": 8778,
        },
    )
    restart_reasons: list[str] = []
    monkeypatch.setattr(
        manager,
        "_schedule_service_restart",
        lambda *, reason: restart_reasons.append(reason) or {"ok": True, "requested": True},
    )
    write_status(
        {
            "state": "succeeded",
            "phase": "root_promoted",
            "action": "update",
            "target_slot": "B",
            "restart_requested_at": 431.0,
            "restart_mode": "self_exit",
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "awaiting_root_restart",
            "action": "update",
            "awaiting_restart": True,
            "restart_required": True,
            "restart_requested_at": 431.0,
            "restart_mode": "self_exit",
            "updated_at": 431.0,
        }
    )

    payload = asyncio.run(manager.complete_update(reason="supervisor.auto_update_complete", auto=True))

    assert payload["accepted"] is False
    assert payload["noop"] is True
    assert payload["restart"]["already_requested"] is True
    assert payload["restart"]["restart_requested_at"] == 431.0
    assert restart_reasons == []
    assert supervisor._read_update_attempt()["state"] == "awaiting_root_restart"


def test_supervisor_maybe_resume_auto_completes_root_promotion_pending(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "_autostart_self_restart_supported", lambda: True)
    write_status({"state": "validated", "phase": "root_promotion_pending", "action": "update"})
    supervisor._write_update_attempt({"state": "active", "action": "update", "updated_at": 1.0})

    captured: dict[str, object] = {}

    async def _complete_update(*, reason: str, auto: bool = False) -> dict[str, object]:
        captured["reason"] = reason
        captured["auto"] = auto
        return {"ok": True}

    monkeypatch.setattr(manager, "complete_update", _complete_update)

    asyncio.run(manager._maybe_resume_or_continue_transition())

    assert captured == {"reason": "supervisor.auto_update_complete", "auto": True}


def test_public_update_status_payload_is_browser_safe() -> None:
    payload = supervisor._public_update_status_payload(
        {
            "status": {
                "action": "update",
                "state": "restarting",
                "phase": "shutdown",
                "message": "countdown completed; pending update written",
                "target_rev": "rev2026",
                "target_version": "0.1.0+1.abc",
                "planned_reason": "minimum_update_period",
                "min_update_period_sec": 300.0,
                "scheduled_for": 456.0,
                "subsequent_transition": True,
                "subsequent_transition_requested_at": 400.0,
                "candidate_prewarm_state": "ready",
                "candidate_prewarm_message": "passive candidate runtime is ready on http://127.0.0.1:8778",
                "candidate_prewarm_ready_at": 430.0,
                "restart_mode": "self_exit",
                "restart_requested_at": 431.0,
                "updated_at": 123.0,
                "error": "hidden",
            },
            "runtime": {
                "active_slot": "A",
                "runtime_state": "spawned",
                "runtime_url": "http://127.0.0.1:8777",
                "runtime_port": 8777,
                "runtime_instance_id": "rt-a-a1b2c3d4",
                "transition_role": "active",
                "listener_running": False,
                "runtime_api_ready": False,
                "candidate_slot": "B",
                "candidate_runtime_url": "http://127.0.0.1:8778",
                "candidate_runtime_port": 8778,
                "candidate_runtime_instance_id": "rt-b-c9d8e7f6",
                "candidate_transition_role": "candidate",
                "candidate_listener_running": True,
                "candidate_runtime_api_ready": True,
                "candidate_runtime_state": "ready",
                "transition_mode": "warm_switch",
                "warm_switch_supported": True,
                "warm_switch_allowed": True,
                "warm_switch_reason": "warm switch admitted",
                "slot_ports": {"A": 8777, "B": 8778},
                "required_upstream_link": {
                    "kind": "hub_root",
                    "role": "hub",
                    "owner": "supervisor",
                    "state": "ready",
                    "ready": True,
                    "visible": True,
                    "current_owner": "sidecar",
                    "planned_owner": "sidecar",
                    "continuity_mode": "slot_sticky",
                    "served_by": "supervisor",
                    "watchdog": {"log_path": "hidden"},
                },
                "root_promotion_required": True,
                "bootstrap_update": {"required": True, "changed_paths": ["src/adaos/apps/supervisor.py"]},
                "runtime_self_heal": {
                    "unhealthy_since": None,
                    "unhealthy_kind": None,
                    "last_decision": {
                        "recorded_at": 111.0,
                        "reason": "supervisor.runtime.api_unready",
                        "message": "active runtime stayed api unready for 60s",
                        "runtime_port": 8777,
                        "runtime_url": "http://127.0.0.1:8777",
                        "listener_running": True,
                        "runtime_api_ready": False,
                        "timeout_sec": 60.0,
                        "pre_restart_evidence": {
                            "captured_at": 110.0,
                            "reason": "supervisor.runtime.api_unready",
                            "stage": "runtime_self_heal_restart",
                            "pid": 32123,
                            "runtime_instance_id": "rt-a-a1b2c3d4",
                            "evidence_path": "/root/.adaos/state/supervisor/evidence/self-heal.json",
                            "memory": {"family_rss_bytes": 123456},
                            "process": {
                                "available": True,
                                "state": "D (disk sleep)",
                                "wchan": "jbd2_log_wait_commit",
                                "threads": [{"tid": 32123, "state": "D (disk sleep)", "wchan": "jbd2_log_wait_commit"}],
                            },
                        },
                    },
                    "last_evidence": {"evidence_path": "stale.json"},
                },
                "managed_cmdline": ["hidden"],
            },
            "attempt": {
                "action": "update",
                "state": "awaiting_root_restart",
                "awaiting_restart": True,
                "planned_reason": "minimum_update_period",
                "scheduled_for": 456.0,
                "subsequent_transition": True,
                "subsequent_transition_requested_at": 400.0,
                "candidate_prewarm_state": "ready",
                "candidate_prewarm_message": "passive candidate runtime is ready on http://127.0.0.1:8778",
                "restart_mode": "self_exit",
                "restart_requested_at": 431.0,
                "updated_at": 222.0,
            },
            "_served_by": "supervisor_fallback",
        }
    )

    assert payload["ok"] is True
    assert payload["status"]["action"] == "update"
    assert payload["status"]["state"] == "restarting"
    assert payload["status"]["phase"] == "shutdown"
    assert payload["status"]["planned_reason"] == "minimum_update_period"
    assert payload["status"]["scheduled_for"] == 456.0
    assert payload["status"]["subsequent_transition"] is True
    assert payload["status"]["candidate_prewarm_state"] == "ready"
    assert payload["status"]["candidate_prewarm_ready_at"] == 430.0
    assert payload["status"]["restart_mode"] == "self_exit"
    assert payload["status"]["restart_requested_at"] == 431.0
    assert payload["attempt"]["state"] == "awaiting_root_restart"
    assert payload["attempt"]["contract_version"] == "1"
    assert payload["attempt"]["authority"] == "supervisor"
    assert payload["attempt"]["action"] == "update"
    assert payload["attempt"]["awaiting_restart"] is True
    assert payload["attempt"]["planned_reason"] == "minimum_update_period"
    assert payload["attempt"]["scheduled_for"] == 456.0
    assert payload["attempt"]["subsequent_transition"] is True
    assert payload["attempt"]["candidate_prewarm_state"] == "ready"
    assert payload["attempt"]["restart_mode"] == "self_exit"
    assert payload["attempt"]["restart_requested_at"] == 431.0
    assert payload["runtime"]["active_slot"] == "A"
    assert payload["runtime"]["runtime_instance_id"] == "rt-a-a1b2c3d4"
    assert payload["runtime"]["transition_role"] == "active"
    assert payload["runtime"]["runtime_url"] == "http://127.0.0.1:8777"
    assert payload["runtime"]["candidate_runtime_url"] == "http://127.0.0.1:8778"
    assert payload["runtime"]["candidate_runtime_instance_id"] == "rt-b-c9d8e7f6"
    assert payload["runtime"]["candidate_transition_role"] == "candidate"
    assert payload["runtime"]["candidate_runtime_state"] == "ready"
    assert payload["runtime"]["candidate_runtime_api_ready"] is True
    assert payload["runtime"]["transition_mode"] == "warm_switch"
    assert payload["runtime"]["slot_ports"]["B"] == 8778
    assert payload["runtime"]["required_upstream_link"]["kind"] == "hub_root"
    assert payload["runtime"]["required_upstream_link"]["state"] == "ready"
    assert payload["runtime"]["required_upstream_link"]["current_owner"] == "sidecar"
    assert "watchdog" not in payload["runtime"]["required_upstream_link"]
    assert payload["runtime"]["root_promotion_required"] is True
    assert payload["runtime"]["runtime_self_heal"]["last_decision"]["reason"] == "supervisor.runtime.api_unready"
    assert payload["runtime"]["runtime_self_heal"]["last_evidence"]["pid"] == 32123
    assert payload["runtime"]["runtime_self_heal"]["last_evidence"]["process"]["wchan"] == "jbd2_log_wait_commit"
    assert payload["_served_by"] == "supervisor_fallback"
    assert "managed_cmdline" not in payload["runtime"]
    assert "error" not in payload["status"]


def test_public_update_status_payload_prefers_runtime_root_promotion_flag() -> None:
    payload = supervisor._public_update_status_payload(
        {
            "status": {
                "state": "succeeded",
                "phase": "validate",
            },
            "runtime": {
                "root_promotion_required": False,
                "bootstrap_update": {"required": True, "changed_paths": ["src/adaos/apps/supervisor.py"]},
            },
        }
    )

    assert payload["runtime"]["root_promotion_required"] is False
    assert payload["runtime"]["runtime_self_heal"]["last_decision"] == {}
    assert payload["runtime"]["runtime_self_heal"]["last_evidence"] == {}


def test_public_update_status_endpoint_is_unauthenticated(monkeypatch) -> None:
    class _Manager:
        def public_update_status(self) -> dict:
            return {
                "ok": True,
                "status": {"state": "restarting", "phase": "shutdown"},
                "runtime": {"runtime_state": "spawned"},
            }

    monkeypatch.setattr(supervisor, "_manager", lambda: _Manager())
    client = TestClient(supervisor.app)

    response = client.get("/api/supervisor/public/update-status")

    assert response.status_code == 200
    assert response.json()["status"]["state"] == "restarting"


def test_public_memory_status_endpoint_is_unauthenticated(monkeypatch) -> None:
    class _Manager:
        def public_memory_status(self) -> dict:
            return {
                "ok": True,
                "memory": {
                    "current_profile_mode": "normal",
                    "profile_control_mode": "phase2_supervisor_restart",
                    "sessions_total": 2,
                },
            }

    monkeypatch.setattr(supervisor, "_manager", lambda: _Manager())
    client = TestClient(supervisor.app)

    response = client.get("/api/supervisor/public/memory-status")

    assert response.status_code == 200
    assert response.json()["memory"]["profile_control_mode"] == "phase2_supervisor_restart"


def test_update_start_endpoint_preserves_zero_countdown(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Manager:
        async def start_update(self, **kwargs) -> dict:
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(supervisor, "_manager", lambda: _Manager())
    client = TestClient(supervisor.app)

    response = client.post(
        "/api/supervisor/update/start",
        headers={"X-AdaOS-Token": "dev-local-token"},
        json={"target_rev": "rev2026", "target_version": "abc123", "countdown_sec": 0},
    )

    assert response.status_code == 200
    assert captured["countdown_sec"] == 0.0


def test_update_defer_endpoint_preserves_zero_delay(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Manager:
        async def defer_update(self, **kwargs) -> dict:
            captured.update(kwargs)
            return {"ok": True}

    monkeypatch.setattr(supervisor, "_manager", lambda: _Manager())
    client = TestClient(supervisor.app)

    response = client.post(
        "/api/supervisor/update/defer",
        headers={"X-AdaOS-Token": "dev-local-token"},
        json={"delay_sec": 0, "reason": "test.defer"},
    )

    assert response.status_code == 200
    assert captured["delay_sec"] == 0.0


def test_public_update_status_does_not_probe_runtime_admin_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    monkeypatch.setattr(
        manager,
        "status",
        lambda **kwargs: {
            "ok": True,
            "runtime_api_ready": False,
            "runtime_state": "spawned",
            "active_slot": "A",
        },
    )
    write_status(
        {
            "state": "restarting",
            "phase": "shutdown",
            "action": "update",
            "message": "countdown completed; pending update written",
        }
    )

    def _unexpected_get(*args, **kwargs):
        raise AssertionError("public_update_status must not call runtime admin update endpoint")

    monkeypatch.setattr(supervisor.requests, "get", _unexpected_get)

    payload = manager.public_update_status()

    assert payload["status"]["state"] == "restarting"
    assert payload["status"]["phase"] == "shutdown"
    assert payload["runtime"]["runtime_state"] == "spawned"


def test_public_update_status_reads_runtime_projection_without_probing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._publish_status_snapshot(
        {
            "ok": True,
            "runtime_state": "starting",
            "listener_running": True,
            "runtime_api_ready": False,
            "runtime_url": "http://127.0.0.1:8777",
            "runtime_port": 8777,
            "sidecar": {},
        },
        update_attempt={},
        reason="test_runtime_observation",
    )

    def _unexpected_probe(*args, **kwargs):
        raise AssertionError("public update status must use the supervisor read model")

    monkeypatch.setattr(supervisor, "_listener_running", _unexpected_probe)
    monkeypatch.setattr(supervisor, "_proc_details", _unexpected_probe)
    monkeypatch.setattr(
        supervisor,
        "_runtime_api_ready",
        _unexpected_probe,
    )

    payload = manager.public_update_status()

    assert payload["runtime"]["runtime_state"] == "starting"


def test_public_memory_status_uses_compact_last_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(supervisor, "load_config", lambda: object())
    monkeypatch.setattr(
        supervisor,
        "report_hub_memory_profile",
        lambda conf, session_summary, operations=None, telemetry=None: {
            "ok": True,
            "reported_at": 33.0,
            "_protocol": {"message_id": "root-msg-1", "cursor": 1},
        },
    )

    manager.start_memory_profile(profile_mode="sampled_profile", reason="operator.request")
    session_id = manager.memory_status()["requested_session_id"]
    manager.publish_memory_profile(session_id, reason="operator.publish")

    payload = manager.public_memory_status()

    assert payload["memory"]["profile_control_mode"] == "phase2_supervisor_restart"
    assert payload["memory"]["last_session"]["session_id"] == session_id
    assert payload["memory"]["last_session"]["publish_state"] == "published"
    assert payload["memory"]["auto_profile_min_uptime_sec"] == 300.0


def test_memory_policy_auto_profile_waits_for_min_uptime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._last_start_at = 100.0
    monkeypatch.setattr(manager, "_sidecar_role", lambda: None)

    allowed, reason = manager._memory_policy_auto_profile_guard(now=250.0)

    assert allowed is False
    assert str(reason).startswith("auto_profile_min_uptime:")

    allowed_after_grace, reason_after_grace = manager._memory_policy_auto_profile_guard(now=401.0)

    assert allowed_after_grace is True
    assert reason_after_grace is None


def test_available_memory_bytes_and_total_memory_bytes_read_psutil(monkeypatch) -> None:
    class _Vm:
        available = 123
        total = 456

    monkeypatch.setattr(supervisor, "psutil", SimpleNamespace(virtual_memory=lambda: _Vm()))

    assert supervisor._available_memory_bytes() == 123
    assert supervisor._total_memory_bytes() == 456


def test_memory_policy_auto_profile_is_blocked_while_hub_has_connected_members(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._last_start_at = 100.0
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.0: {
            "node": {"role": "hub"},
            "hub_member_connection_state": {"connected_total": 1},
        },
    )

    allowed, reason = manager._memory_policy_auto_profile_guard(now=401.0)

    assert allowed is False
    assert reason == "subnet_members_connected:1"


def test_memory_policy_auto_profile_fails_closed_when_hub_reliability_is_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "0")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    monkeypatch.setattr(manager, "_sidecar_role", lambda: "hub")
    monkeypatch.setattr(manager, "_runtime_reliability_payload", lambda timeout=1.0: {})

    allowed, reason = manager._memory_policy_auto_profile_guard(now=401.0)

    assert allowed is False
    assert reason == "hub_runtime_reliability_unavailable"


def test_memory_policy_auto_profile_can_ignore_browser_sessions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "0")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_ALLOW_BROWSER_SESSIONS", "1")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    manager._last_start_at = 100.0
    monkeypatch.setattr(supervisor.time, "time", lambda: 401.0)

    from adaos.services import access_links

    monkeypatch.setattr(
        access_links,
        "browser_snapshot",
        lambda: [
            {
                "last_seen_at": 400.0,
                "connection_state": "open",
                "online": True,
            }
        ],
    )
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.0: {"node": {"role": "hub"}, "hub_member_connection_state": {"connected_total": 0}},
    )

    allowed, reason = manager._memory_policy_auto_profile_guard(now=401.0)

    assert allowed is True
    assert reason is None


def test_policy_memory_profile_restart_is_delayed_during_min_uptime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 12345
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._last_start_at = 100.0
    manager._memory_active_session_id = "mem-policy"
    manager._memory_requested_profile_mode = "sampled_profile"
    supervisor.write_memory_session_summary(
        "mem-policy",
        {
            "session_id": "mem-policy",
            "profile_mode": "sampled_profile",
            "session_state": "requested",
            "trigger_source": "policy",
            "trigger_reason": "memory.growth_and_slope_threshold",
            "requested_at": 150.0,
        },
    )
    monkeypatch.setattr(supervisor.time, "time", lambda: 200.0)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    restarts: list[str] = []

    async def _restart_runtime(*, reason: str):
        restarts.append(reason)
        return {"ok": True}

    monkeypatch.setattr(manager, "restart_runtime", _restart_runtime)

    asyncio.run(manager._maybe_apply_memory_profile_mode())

    assert restarts == []
    assert str(manager._memory_auto_profile_last_block_reason).startswith("auto_profile_min_uptime:")


def test_policy_memory_profile_restart_is_blocked_while_member_link_is_connected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 22334
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._last_start_at = 100.0
    manager._memory_active_session_id = "mem-member"
    manager._memory_requested_profile_mode = "sampled_profile"
    supervisor.write_memory_session_summary(
        "mem-member",
        {
            "session_id": "mem-member",
            "profile_mode": "sampled_profile",
            "session_state": "requested",
            "trigger_source": "policy",
            "trigger_reason": "memory.growth_and_slope_threshold",
            "requested_at": 150.0,
        },
    )
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.0: {
            "node": {"role": "member", "connected_to_hub": True},
            "hub_member_connection_state": {"hub": {"connected": True}},
        },
    )
    restarts: list[str] = []

    async def _restart_runtime(*, reason: str):
        restarts.append(reason)
        return {"ok": True}

    monkeypatch.setattr(manager, "restart_runtime", _restart_runtime)

    asyncio.run(manager._maybe_apply_memory_profile_mode())

    assert restarts == []
    assert manager._memory_auto_profile_last_block_reason == "member_hub_connected"


def test_policy_memory_profile_restart_is_blocked_by_connected_to_subnet_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_AUTO_PROFILE_MIN_UPTIME_SEC", "300")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 22334
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._last_start_at = 100.0
    manager._memory_active_session_id = "mem-member"
    manager._memory_requested_profile_mode = "sampled_profile"
    supervisor.write_memory_session_summary(
        "mem-member",
        {
            "session_id": "mem-member",
            "profile_mode": "sampled_profile",
            "session_state": "requested",
            "trigger_source": "policy",
            "trigger_reason": "memory.growth_and_slope_threshold",
            "requested_at": 150.0,
        },
    )
    monkeypatch.setattr(supervisor.time, "time", lambda: 500.0)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.0: {
            "node": {"role": "member", "connected_to_subnet": True},
            "hub_member_connection_state": {"hub": {"connected": False}},
        },
    )
    restarts: list[str] = []

    async def _restart_runtime(*, reason: str):
        restarts.append(reason)
        return {"ok": True}

    monkeypatch.setattr(manager, "restart_runtime", _restart_runtime)

    asyncio.run(manager._maybe_apply_memory_profile_mode())

    assert restarts == []
    assert manager._memory_auto_profile_last_block_reason == "member_hub_connected"


def test_critical_memory_restart_is_allowed_while_live_subnet_is_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_PERCENT", "5")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_DURATION_SEC", "20")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 9988
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._desired_running = True
    manager._stopping = False
    manager._memory_last_available_bytes = 32 * 1024 * 1024
    monkeypatch.setattr(supervisor, "_total_memory_bytes", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(
        supervisor,
        "_process_family_rss_bytes",
        lambda pid: (100 * 1024 * 1024, 300 * 1024 * 1024),
    )
    monkeypatch.setattr(supervisor, "_system_process_memory_snapshot", lambda pid: {"available": True})
    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {})
    monkeypatch.setattr(supervisor, "_read_update_attempt", lambda: {})
    monkeypatch.setattr(
        manager,
        "_runtime_reliability_payload",
        lambda timeout=1.0: {
            "node": {"role": "hub"},
            "hub_member_connection_state": {"connected_total": 2},
        },
    )

    first = manager._memory_critical_restart_decision(now=100.0)
    second = manager._memory_critical_restart_decision(now=121.0)

    assert first is None
    assert second is not None
    assert second["reason"] == "supervisor.memory.critical_pressure"
    assert second["action"] == "restart_runtime"
    assert second["pressure_owner"] == "runtime_family"
    assert second["subnet_live"] is True
    assert second["subnet_reason"] == "subnet_members_connected:2"


def test_critical_external_memory_pressure_preserves_runtime_and_records_attribution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_PERCENT", "5")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_DURATION_SEC", "20")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 9988
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._desired_running = True
    manager._stopping = False
    manager._memory_last_available_bytes = 32 * 1024 * 1024
    manager._memory_baseline_family_rss_bytes = 120 * 1024 * 1024
    manager._memory_last_growth_bytes = 8 * 1024 * 1024
    monkeypatch.setattr(supervisor, "_total_memory_bytes", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(
        supervisor,
        "_process_family_rss_bytes",
        lambda pid: (64 * 1024 * 1024, 128 * 1024 * 1024),
    )
    monkeypatch.setattr(
        supervisor,
        "_system_process_memory_snapshot",
        lambda pid: {
            "available": True,
            "top_external_by_rss": [{"pid": 777, "name": "node", "rss_bytes": 700 * 1024 * 1024}],
        },
    )
    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {})
    monkeypatch.setattr(supervisor, "_read_update_attempt", lambda: {})

    assert manager._memory_critical_pressure_decision(now=100.0) is None
    decision = manager._memory_critical_pressure_decision(now=121.0)

    assert decision is not None
    assert decision["reason"] == "supervisor.memory.external_pressure"
    assert decision["action"] == "observe_external_pressure"
    assert decision["pressure_owner"] == "external_or_system"
    assert decision["attribution"]["family_rss_bytes"] == 128 * 1024 * 1024
    assert decision["system_process_snapshot"]["top_external_by_rss"][0]["name"] == "node"
    assert manager._memory_critical_restart_decision(now=122.0) is None


def test_critical_skill_memory_pressure_selects_targeted_quarantine(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_PERCENT", "5")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_DURATION_SEC", "20")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 9988
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._memory_last_available_bytes = 32 * 1024 * 1024
    monkeypatch.setattr(supervisor, "_total_memory_bytes", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(
        supervisor,
        "_process_family_rss_bytes",
        lambda pid: (64 * 1024 * 1024, 128 * 1024 * 1024),
    )
    monkeypatch.setattr(
        supervisor,
        "_system_process_memory_snapshot",
        lambda pid: {
            "available": True,
            "skill_runtime_totals": [
                {
                    "skill_runtime": "runaway_skill",
                    "rss_bytes": 300 * 1024 * 1024,
                    "process_total": 1,
                    "pids": [777],
                }
            ],
        },
    )
    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {})
    monkeypatch.setattr(supervisor, "_read_update_attempt", lambda: {})

    assert manager._memory_critical_pressure_decision(now=100.0) is None
    decision = manager._memory_critical_pressure_decision(now=121.0)

    assert decision is not None
    assert decision["reason"] == "supervisor.memory.skill_pressure"
    assert decision["action"] == "quarantine_skill_runtime"
    assert decision["pressure_owner"] == "skill_runtime"
    assert decision["attribution"]["skill_target"]["skill_runtime"] == "runaway_skill"
    assert manager._memory_critical_restart_decision(now=122.0) is None


def test_skill_memory_pressure_quarantine_uses_runtime_lifecycle_api(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        manager,
        "_runtime_request_json",
        lambda **kwargs: calls.append(dict(kwargs)) or {"ok": True, "stopped": True},
    )
    decision = {
        "reason": "supervisor.memory.skill_pressure",
        "restart_cooldown_sec": 120.0,
        "available_memory_bytes": 64 * 1024 * 1024,
        "available_memory_percent": 4.0,
        "critical_for_sec": 21.0,
        "attribution": {
            "skill_indicators": ["skill_rss_threshold"],
            "skill_target": {
                "skill_runtime": "runaway skill",
                "rss_bytes": 3 * 1024 * 1024 * 1024,
                "process_total": 2,
                "pids": [777, 778],
            },
        },
    }

    result = asyncio.run(manager._quarantine_skill_memory_pressure(decision))

    assert result == {"ok": True, "stopped": True}
    assert calls[0]["path"] == "/api/services/runaway%20skill/resource-pressure"
    assert calls[0]["method"] == "POST"
    assert calls[0]["payload"]["pressure"]["observed_pids"] == [777, 778]


def test_critical_memory_restart_runs_once_per_continuous_pressure_episode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_PERCENT", "5")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_AVAILABLE_BYTES", "64")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_DURATION_SEC", "20")
    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_CRITICAL_RESTART_COOLDOWN_SEC", "30")
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 9988
        args = ["python", "-m", "adaos.apps.autostart_runner"]

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()  # type: ignore[assignment]
    manager._desired_running = True
    manager._stopping = False
    manager._memory_last_available_bytes = 32 * 1024 * 1024
    monkeypatch.setattr(supervisor, "_total_memory_bytes", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(
        supervisor,
        "_process_family_rss_bytes",
        lambda pid: (100 * 1024 * 1024, 300 * 1024 * 1024),
    )
    monkeypatch.setattr(supervisor, "_system_process_memory_snapshot", lambda pid: {"available": True})
    monkeypatch.setattr(supervisor, "read_core_update_status", lambda: {})
    monkeypatch.setattr(supervisor, "_read_update_attempt", lambda: {})

    assert manager._memory_critical_restart_decision(now=100.0) is None
    assert manager._memory_critical_restart_decision(now=121.0) is not None
    manager._memory_critical_restart_last_at = 121.0
    assert manager._memory_critical_restart_decision(now=300.0) is None

    manager._memory_last_available_bytes = 128 * 1024 * 1024
    assert manager._memory_critical_restart_decision(now=301.0) is None
    manager._memory_last_available_bytes = 32 * 1024 * 1024
    assert manager._memory_critical_restart_decision(now=400.0) is None
    assert manager._memory_critical_restart_decision(now=421.0) is not None


def test_spawn_runtime_locked_prefers_active_slot_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    captured: dict[str, object] = {}

    class _Proc:
        pid = 4242

        @staticmethod
        def poll():
            return None

    def _fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["/slot/python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
            "cwd": "/slot/repo",
            "env": {"PYTHONPATH": "/slot/repo/src"},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"slots": {"A": {"path": "/slots/A"}}},
    )
    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)

    asyncio.run(manager._spawn_runtime_locked(reason="test.spawn"))

    assert captured["args"][0] == "/slot/python"
    assert captured["kwargs"]["cwd"] == "/slot/repo"
    assert captured["kwargs"]["env"]["PYTHONPATH"] == "/slot/repo/src"
    assert captured["kwargs"]["env"]["ADAOS_ACTIVE_CORE_SLOT"] == "A"
    assert captured["kwargs"]["env"]["ADAOS_RUNTIME_TRANSITION_ROLE"] == "active"
    assert captured["kwargs"]["env"]["ADAOS_RUNTIME_PORT"] == "8777"
    assert str(captured["kwargs"]["env"]["ADAOS_RUNTIME_INSTANCE_ID"]).startswith("rt-a-a-")
    assert manager.status()["managed_start_reason"] == "test.spawn"


def test_spawn_runtime_locked_uses_slot_specific_port_for_slot_b(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    captured: dict[str, object] = {}

    class _Proc:
        pid = 4343

        @staticmethod
        def poll():
            return None

    def _fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["/slot/python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
            "cwd": "/slot/repo",
            "env": {"PYTHONPATH": "/slot/repo/src"},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"slots": {"B": {"path": "/slots/B"}}},
    )
    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)

    asyncio.run(manager._spawn_runtime_locked())

    assert captured["args"][-1] == "8778"
    assert captured["kwargs"]["env"]["ADAOS_RUNTIME_PORT"] == "8778"
    assert str(captured["kwargs"]["env"]["ADAOS_RUNTIME_INSTANCE_ID"]).startswith("rt-b-a-")


def test_spawn_candidate_runtime_locked_uses_candidate_role_and_skips_pending_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    captured: dict[str, object] = {}

    class _Proc:
        pid = 5151

        @staticmethod
        def poll():
            return None

    def _fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "read_slot_manifest",
        lambda slot: {
            "slot": slot,
            "argv": ["/slot/python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
            "cwd": f"/slots/{slot}/repo",
            "env": {"PYTHONPATH": f"/slots/{slot}/repo/src"},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"slots": {"B": {"path": "/slots/B"}}},
    )
    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)

    asyncio.run(manager._spawn_candidate_runtime_locked(slot="B", reason="test.candidate"))

    assert captured["args"][-1] == "8778"
    assert captured["kwargs"]["cwd"] == "/slots/B/repo"
    assert captured["kwargs"]["env"]["ADAOS_ACTIVE_CORE_SLOT"] == "B"
    assert captured["kwargs"]["env"]["ADAOS_RUNTIME_TRANSITION_ROLE"] == "candidate"
    assert captured["kwargs"]["env"]["ADAOS_RUNTIME_PORT"] == "8778"
    assert captured["kwargs"]["env"]["ADAOS_SKIP_PENDING_CORE_UPDATE"] == "1"
    assert str(captured["kwargs"]["env"]["ADAOS_RUNTIME_INSTANCE_ID"]).startswith("rt-b-c-")
    assert manager.status()["candidate_start_reason"] == "test.candidate"


def test_restart_runtime_records_last_stop_and_start_reason(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    captured: dict[str, object] = {}

    class _CurrentProc:
        pid = 6060

        @staticmethod
        def poll():
            return None

    class _SpawnedProc:
        pid = 6161

        @staticmethod
        def poll():
            return None

    async def _fake_terminate_proc_locked(
        *, proc=None, base_url=None, graceful: bool, reason: str, lifecycle_scope: str = "subnet"
    ) -> None:
        captured["terminate"] = {
            "proc": proc,
            "base_url": base_url,
            "graceful": graceful,
            "reason": reason,
            "lifecycle_scope": lifecycle_scope,
        }
        manager._proc = None

    def _fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _SpawnedProc()

    manager._proc = _CurrentProc()
    monkeypatch.setattr(manager, "_transition_continuity_guard_decision", lambda operation: None)
    monkeypatch.setattr(manager, "_terminate_proc_locked", _fake_terminate_proc_locked)
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["/slot/python", "-m", "adaos.apps.autostart_runner", "--host", "{host}", "--port", "{port}"],
            "cwd": "/slot/repo",
            "env": {"PYTHONPATH": "/slot/repo/src"},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "core_slot_status",
        lambda: {"slots": {"A": {"path": "/slots/A"}}},
    )
    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)

    payload = asyncio.run(manager.restart_runtime(reason="test.restart"))

    assert captured["terminate"]["reason"] == "test.restart"
    assert captured["args"][0] == "/slot/python"
    assert payload["managed_start_reason"] == "test.restart"
    assert payload["last_stop_reason"] == "test.restart"
    assert payload["restart_count"] == 1


def test_retired_runtime_stop_uses_runtime_lifecycle_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    captured: list[dict[str, object]] = []

    async def _terminate(**kwargs) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(manager, "_terminate_proc_locked", _terminate)

    async def _run() -> None:
        task = manager._schedule_retired_runtime_stop(
            proc="old-runtime",
            base_url="http://127.0.0.1:8777",
            reason="supervisor.fast_cutover.old_active_stop",
        )
        await task

    asyncio.run(_run())

    assert captured == [
        {
            "proc": "old-runtime",
            "base_url": "http://127.0.0.1:8777",
            "graceful": True,
            "reason": "supervisor.fast_cutover.old_active_stop",
            "lifecycle_scope": "runtime_retire",
        }
    ]


def test_supervisor_adopts_slot_matched_listener_before_runtime_api_is_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ExistingProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.args = ["/slots/B/venv/bin/python", "-m", "adaos.apps.autostart_runner", "--port", "8778"]
            self.cwd = "/slots/B/repo"
            self._created_at = 123.0

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "B", "argv": ["/slots/B/venv/bin/python"], "cwd": "/slots/B/repo"},
    )
    monkeypatch.setattr(supervisor, "_listener_owner_pid", lambda host, port: 4242)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_AdoptedProcess", _ExistingProc)
    monkeypatch.setattr(
        supervisor,
        "_read_json",
        lambda _path: {
            "managed_pid": 4242,
            "runtime_instance_id": "rt-b-c-existing",
            "transition_role": "active",
            "managed_slot": "B",
        },
    )
    monkeypatch.setattr(manager, "slot_runtime_port", lambda slot=None: 8778)
    monkeypatch.setattr(manager, "slot_runtime_base_url", lambda slot=None: "http://127.0.0.1:8778")
    monkeypatch.setattr(manager, "_reset_memory_baseline_scope", lambda **_kwargs: None)

    adopted = manager._adopt_active_runtime_listener(reason="supervisor.start")

    assert adopted is True
    assert manager._proc is not None
    assert manager._proc.pid == 4242
    assert manager._managed_runtime_instance_id == "rt-b-c-existing"
    assert manager._managed_start_reason == "supervisor.start"


def test_supervisor_verifies_inherited_runtime_with_lightweight_ping(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ExistingProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.args = ["/uv/python", "-m", "adaos.apps.autostart_runner", "--port", "8777"]
            self.cwd = "/slots/A/repo"
            self._created_at = 123.0

        @staticmethod
        def poll():
            return None

    class _Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "runtime": {
                    "runtime_instance_id": "rt-a-a-existing",
                    "transition_role": "active",
                    "slot": "A",
                }
            }

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    requested: list[str] = []

    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "A", "argv": ["/slots/A/venv/bin/python"], "cwd": "/slots/A/repo"},
    )
    monkeypatch.setattr(supervisor, "_listener_owner_pid", lambda host, port: 4242)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_AdoptedProcess", _ExistingProc)
    monkeypatch.setattr(
        supervisor.requests,
        "get",
        lambda url, **kwargs: requested.append(url) or _Response(),
    )
    monkeypatch.setattr(manager, "_reset_memory_baseline_scope", lambda **_kwargs: None)

    adopted = manager._adopt_active_runtime_listener(reason="supervisor.start")

    assert adopted is True
    assert requested == ["http://127.0.0.1:8777/api/ping"]
    assert manager._managed_runtime_instance_id == "rt-a-a-existing"
    assert manager._managed_runtime_api_identity_verified is True


def test_supervisor_refuses_pre_ready_listener_from_wrong_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _WrongSlotProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.args = ["/slots/A/venv/bin/python", "-m", "adaos.apps.autostart_runner", "--port", "8778"]
            self.cwd = "/slots/A/repo"
            self._created_at = 123.0

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {"slot": "B", "argv": ["/slots/B/venv/bin/python"], "cwd": "/slots/B/repo"},
    )
    monkeypatch.setattr(supervisor, "_listener_owner_pid", lambda host, port: 4343)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(supervisor, "_AdoptedProcess", _WrongSlotProc)
    monkeypatch.setattr(manager, "slot_runtime_port", lambda slot=None: 8778)
    monkeypatch.setattr(manager, "slot_runtime_base_url", lambda slot=None: "http://127.0.0.1:8778")

    assert manager._adopt_active_runtime_listener(reason="supervisor.start") is False
    assert manager._proc is None


def test_supervisor_schedules_retired_runtime_cleanup_across_self_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    captured: dict[str, object] = {}

    class _RetiredProc:
        @staticmethod
        def poll():
            return None

    class _CleanupProc:
        pid = 4545

    manager._retired_runtime_procs[4444] = _RetiredProc()

    def _popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _CleanupProc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", _popen)

    result = manager._schedule_retired_runtime_cleanup()

    assert result == {"ok": True, "scheduled": True, "cleanup_pid": 4545, "pids": [4444]}
    assert "pids = [4444]" in captured["args"][2]
    if supervisor.os.name == "nt":
        assert "os.killpg" not in captured["args"][2]


def test_windows_handoff_reaper_waits_for_replacement_supervisor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    captured: dict[str, object] = {}

    class _Proc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    class _ReaperProc:
        pid = 4546

    manager._proc = _Proc(4444)
    manager._sidecar_proc = _Proc(4445)

    def _popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _ReaperProc()

    monkeypatch.setattr(supervisor.subprocess, "Popen", _popen)
    monkeypatch.setattr(supervisor.os, "name", "nt")

    result = manager._schedule_managed_handoff_reaper()

    assert result["scheduled"] is True
    assert result["pids"] == [4444, 4445]
    code = captured["args"][2]
    assert "socket.create_connection" in code
    assert "systemctl" not in code


def test_stop_candidate_runtime_persists_last_stop_reason_after_candidate_clears(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    captured: dict[str, object] = {}

    class _CandidateProc:
        pid = 7171

        @staticmethod
        def poll():
            return None

    async def _fake_terminate_proc_locked(
        *, proc=None, base_url=None, graceful: bool, reason: str, lifecycle_scope: str = "subnet"
    ) -> None:
        captured["terminate"] = {
            "proc": proc,
            "base_url": base_url,
            "graceful": graceful,
            "reason": reason,
            "lifecycle_scope": lifecycle_scope,
        }

    manager._candidate_proc = _CandidateProc()
    manager._candidate_slot = "B"
    manager._candidate_runtime_instance_id = "rt-b-c-test"
    manager._candidate_transition_role = "candidate"
    monkeypatch.setattr(manager, "_terminate_proc_locked", _fake_terminate_proc_locked)

    payload = asyncio.run(manager.stop_candidate_runtime(reason="test.candidate.stop"))

    assert captured["terminate"]["reason"] == "test.candidate.stop"
    assert captured["terminate"]["lifecycle_scope"] == "runtime_retire"
    assert payload["candidate_slot"] is None
    assert payload["candidate_last_stop_reason"] == "test.candidate.stop"


def test_absent_candidate_never_falls_back_to_active_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    calls: list[object] = []

    class _ActiveProc:
        pid = 8181

        @staticmethod
        def poll():
            return None

    async def _terminate(**kwargs):
        calls.append(kwargs)

    manager._proc = _ActiveProc()
    manager._candidate_proc = None
    monkeypatch.setattr(manager, "_terminate_proc_locked", _terminate)

    asyncio.run(
        manager._terminate_candidate_proc_locked(
            graceful=True,
            reason="supervisor.shutdown.candidate",
        )
    )

    assert calls == []
    assert manager._proc is not None


def test_supervisor_self_restart_preserves_ready_children(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    stopped: list[str] = []

    class _Proc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        @staticmethod
        def poll():
            return None

    async def _stop(*, reason: str):
        stopped.append(reason)

    async def _stop_sidecar(*, reason: str):
        stopped.append(reason)
        return {"ok": True}

    manager._proc = _Proc(9191)
    manager._sidecar_proc = _Proc(9292)
    manager._service_restart_pending = True
    monkeypatch.setattr(manager, "stop", _stop)
    monkeypatch.setattr(manager, "stop_sidecar", _stop_sidecar)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(manager, "_schedule_managed_handoff_reaper", lambda: {"ok": True, "scheduled": True})

    asyncio.run(manager.close())

    assert stopped == []
    assert manager._proc is not None
    assert manager._sidecar_proc is not None


def test_managed_systemd_shutdown_stops_children_without_internal_restart_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ADAOS_AUTOSTART_MANAGED", "1")
    monkeypatch.setattr(supervisor, "_autostart_self_restart_supported", lambda: True)
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")
    stopped: list[str] = []
    reaper_calls: list[bool] = []

    class _Proc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        @staticmethod
        def poll():
            return None

    async def _stop(*, reason: str):
        stopped.append(reason)

    async def _stop_sidecar(*, reason: str):
        stopped.append(reason)
        return {"ok": True}

    manager._proc = _Proc(9391)
    manager._sidecar_proc = _Proc(9392)
    manager._service_restart_pending = False
    monkeypatch.setattr(manager, "stop", _stop)
    monkeypatch.setattr(manager, "stop_sidecar", _stop_sidecar)
    monkeypatch.setattr(manager, "_persist_runtime_state", lambda: None)
    monkeypatch.setattr(
        manager,
        "_schedule_managed_handoff_reaper",
        lambda: reaper_calls.append(True) or {"ok": True, "scheduled": True},
    )

    asyncio.run(manager.close())

    assert stopped == ["supervisor.shutdown", "supervisor.shutdown.sidecar"]
    assert reaper_calls == []
    assert manager._proc is not None
    assert manager._sidecar_proc is not None


def test_runtime_state_payload_surfaces_candidate_runtime_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _ActiveProc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8777"]
        cwd = str(tmp_path / "active")

        @staticmethod
        def poll():
            return None

    class _CandidateProc:
        pid = 32124
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8778"]
        cwd = str(tmp_path / "candidate")

        @staticmethod
        def poll():
            return None

    manager._proc = _ActiveProc()
    manager._candidate_proc = _CandidateProc()
    manager._candidate_slot = "B"
    manager._candidate_runtime_instance_id = "rt-b-c-12345678"
    manager._candidate_transition_role = "candidate"
    monkeypatch.setattr(supervisor, "active_slot", lambda: "A")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path / "active"),
        },
    )
    monkeypatch.setattr(
        supervisor,
        "read_slot_manifest",
        lambda slot: {
            "slot": slot,
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path / "candidate"),
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(
        supervisor,
        "_listener_running",
        lambda host, port, **kwargs: int(port) in {8777, 8778},
    )
    monkeypatch.setattr(
        supervisor,
        "_runtime_api_ready",
        lambda base_url, **kwargs: base_url.endswith(":8777") or base_url.endswith(":8778"),
    )

    payload = manager.status(refresh=True)

    assert payload["candidate_slot"] == "B"
    assert payload["candidate_runtime_port"] == 8778
    assert payload["candidate_runtime_instance_id"] == "rt-b-c-12345678"
    assert payload["candidate_transition_role"] == "candidate"
    assert payload["candidate_runtime_state"] == "ready"
    assert payload["candidate_runtime_api_ready"] is True


def test_runtime_state_payload_hides_candidate_after_root_restart_completion(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    manager = supervisor.SupervisorManager(runtime_host="127.0.0.1", runtime_port=8777, token="dev-local-token")

    class _Proc:
        pid = 32123
        args = ["python", "-m", "adaos.apps.autostart_runner", "--host", "127.0.0.1", "--port", "8778"]
        cwd = str(tmp_path)

        @staticmethod
        def poll():
            return None

    manager._proc = _Proc()
    write_status(
        {
            "state": "succeeded",
            "phase": "validate",
            "action": "update",
            "target_slot": "B",
            "root_restart_completed_at": 499.0,
        }
    )
    supervisor._write_update_attempt(
        {
            "state": "completed",
            "action": "update",
            "target_slot": "B",
            "updated_at": 499.0,
        }
    )
    monkeypatch.setattr(supervisor, "active_slot", lambda: "B")
    monkeypatch.setattr(
        supervisor,
        "active_slot_manifest",
        lambda: {
            "slot": "B",
            "argv": ["python", "-m", "adaos.apps.autostart_runner"],
            "cwd": str(tmp_path),
        },
    )
    monkeypatch.setattr(supervisor, "validate_slot_structure", lambda slot: {"slot": slot, "ok": True, "issues": []})
    monkeypatch.setattr(supervisor, "_listener_running", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "_runtime_api_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(supervisor, "choose_inactive_slot", lambda: "A")

    payload = manager.status(refresh=True)

    assert payload["candidate_slot"] is None
    assert payload["candidate_runtime_url"] is None
    assert payload["candidate_runtime_state"] is None
    assert payload["candidate_transition_role"] is None
