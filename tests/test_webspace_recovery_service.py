from __future__ import annotations

import asyncio

from adaos.services.scenario.webspace_components import WebspaceRecoveryCoordinator


def test_recovery_coordinator_deduplicates_command_identity() -> None:
    coordinator = WebspaceRecoveryCoordinator(clock=lambda: 100.0)
    trace = {"cmd_id": "cmd-1", "gateway_command_fingerprint": "fp-1"}

    first = coordinator.begin(
        webspace_id="desktop",
        action="reload",
        scenario_id="home",
        command_trace=trace,
        previous_state={},
        command_ttl_s=30.0,
        duplicate_window_s=1.5,
        pending_stale_after_s=120.0,
    )
    second = coordinator.begin(
        webspace_id="desktop",
        action="reload",
        scenario_id="home",
        command_trace=trace,
        previous_state={},
        command_ttl_s=30.0,
        duplicate_window_s=1.5,
        pending_stale_after_s=120.0,
    )

    assert first.deduplicated is False
    assert second.duplicate_reason == "duplicate_recovery_command"
    assert second.fingerprint == "fp-1"


def test_recovery_coordinator_supersedes_stale_pending_request() -> None:
    coordinator = WebspaceRecoveryCoordinator(clock=lambda: 200.0)
    previous = {
        "action": "reload",
        "scenario_id": "home",
        "status": "running",
        "pending": True,
        "updated_at": 10.0,
        "recovery_fingerprint": coordinator.request_fingerprint(
            webspace_id="desktop",
            action="reload",
            scenario_id="home",
        ),
    }

    decision = coordinator.begin(
        webspace_id="desktop",
        action="reload",
        scenario_id="home",
        command_trace={},
        previous_state=previous,
        command_ttl_s=30.0,
        duplicate_window_s=1.5,
        pending_stale_after_s=120.0,
    )

    assert decision.previous_pending_stale is True
    assert decision.deduplicated is False


def test_recovery_coordinator_restores_then_reconciles() -> None:
    coordinator = WebspaceRecoveryCoordinator()
    calls: list[str] = []

    async def restore_store(webspace_id: str):
        calls.append(f"restore:{webspace_id}")
        return {"accepted": True, "snapshot_path": "snapshot.bin"}

    async def reset_room(webspace_id: str):
        calls.append(f"reset:{webspace_id}")
        return {"reset": True}

    async def read_current(webspace_id: str):
        calls.append(f"read:{webspace_id}")
        return "home"

    def persist_current(webspace_id: str, scenario_id: str):
        calls.append(f"persist:{webspace_id}:{scenario_id}")

    async def rebuild(webspace_id: str, restore_result):
        calls.append(f"rebuild:{webspace_id}:{restore_result['snapshot_path']}")
        return {"accepted": True, "reconciled": True}

    result = asyncio.run(
        coordinator.restore_snapshot(
            webspace_id="desktop",
            restore_store=restore_store,
            reset_room=reset_room,
            read_current_scenario=read_current,
            persist_current_scenario=persist_current,
            rebuild=rebuild,
        )
    )

    assert calls == [
        "restore:desktop",
        "reset:desktop",
        "read:desktop",
        "persist:desktop:home",
        "rebuild:desktop:snapshot.bin",
    ]
    assert result["action"] == "restore"
    assert result["reconciled"] is True
