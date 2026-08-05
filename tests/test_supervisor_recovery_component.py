from __future__ import annotations

from dataclasses import replace

from adaos.apps.supervisor_runtime import RuntimeRecoveryFacts, RuntimeRecoveryPolicy


def _facts(**overrides):
    facts = RuntimeRecoveryFacts(
        process_running=True,
        stopping=False,
        desired_running=True,
        update_state="idle",
        update_phase="",
        current_slot="B",
        managed_executable="/slot/B/python",
        managed_cwd="/slot/B",
        expected_executable="/slot/B/python",
        expected_cwd="/slot/B",
        managed_matches_active_slot=True,
        runtime_host="127.0.0.1",
        runtime_port=8778,
        runtime_url="http://127.0.0.1:8778",
        listener_running=False,
        runtime_api_ready=False,
        now=100.0,
        unhealthy_kind=None,
        unhealthy_since=None,
        last_start_at=0.0,
        listener_startup_grace_sec=0.0,
        listener_restart_timeout_sec=45.0,
        api_restart_timeout_sec=60.0,
    )
    return replace(facts, **overrides)


def test_recovery_policy_starts_unhealthy_window() -> None:
    result = RuntimeRecoveryPolicy.evaluate(_facts())

    assert result.unhealthy_kind == "listener_lost"
    assert result.unhealthy_since == 100.0
    assert result.decision is None


def test_recovery_policy_restarts_after_timeout() -> None:
    result = RuntimeRecoveryPolicy.evaluate(
        _facts(now=146.0, unhealthy_kind="listener_lost", unhealthy_since=100.0)
    )

    assert result.decision is not None
    assert result.decision["reason"] == "supervisor.runtime.listener_lost"


def test_recovery_policy_prioritizes_slot_mismatch_over_apply_guard() -> None:
    result = RuntimeRecoveryPolicy.evaluate(
        _facts(
            update_state="applying",
            update_phase="apply",
            managed_matches_active_slot=False,
            expected_executable="/slot/A/python",
        )
    )

    assert result.decision is not None
    assert result.decision["reason"] == "supervisor.runtime.slot_mismatch"
