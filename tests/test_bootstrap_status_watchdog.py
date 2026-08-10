from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import BootstrapLifecycleCoordinator, BootstrapStatusWatchdogService


pytestmark = pytest.mark.anyio


def _status_service(*, emitted: list[tuple[str, dict]], reported: list[str]) -> BootstrapStatusWatchdogService:
    async def _emit(event_type: str, payload: dict, **kwargs) -> None:
        emitted.append((event_type, dict(payload)))

    def _should_emit(**kwargs):
        fingerprint = (kwargs["payload"].get("state"),)
        return (fingerprint != kwargs["last_fingerprint"], fingerprint)

    return BootstrapStatusWatchdogService(
        config=SimpleNamespace(role="hub"),
        logger=logging.getLogger("test.bootstrap.status_watchdog"),
        control_report_enabled=True,
        control_await_watch_enabled=False,
        control_heartbeat_s=15.0,
        node_status_heartbeat_s=5.0,
        report_control=lambda config: reported.append(config.role),
        node_status_payload=lambda: {"state": "ready"},
        should_emit_node_status=_should_emit,
        emit_event=_emit,
    )


async def test_status_watchdog_reports_control_lifecycle() -> None:
    emitted: list[tuple[str, dict]] = []
    reported: list[str] = []
    service = _status_service(emitted=emitted, reported=reported)

    await service.report_control_lifecycle("sys.ready")

    assert reported == ["hub"]


async def test_status_watchdog_deduplicates_node_status() -> None:
    emitted: list[tuple[str, dict]] = []
    reported: list[str] = []
    service = _status_service(emitted=emitted, reported=reported)

    await service.emit_node_status("boot")
    await service.emit_node_status("heartbeat")

    assert emitted == [("node.status", {"state": "ready", "trigger": "boot"})]
    assert service._suppressed_duplicate_node_status_total == 1


async def test_status_watchdog_builds_node_status_off_event_loop_thread() -> None:
    emitted: list[tuple[str, dict]] = []
    reported: list[str] = []
    service = _status_service(emitted=emitted, reported=reported)
    loop_thread_id = threading.get_ident()
    payload_thread_ids: list[int] = []

    service._node_status_payload = lambda: (
        payload_thread_ids.append(threading.get_ident()) or {"state": "ready"}
    )

    await service.emit_node_status("boot")

    assert payload_thread_ids
    assert payload_thread_ids[0] != loop_thread_id
    assert emitted == [("node.status", {"state": "ready", "trigger": "boot"})]


async def test_status_watchdog_builds_policy_and_registers_heartbeats(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_HUB_CONTROL_REPORT_ENABLED", "1")
    lifecycle = BootstrapLifecycleCoordinator()
    service = BootstrapStatusWatchdogService.from_environment(
        config=SimpleNamespace(role="member"),
        logger=logging.getLogger("test.bootstrap.status_watchdog"),
        report_control=lambda config: None,
        node_status_payload=lambda: {},
        node_status_heartbeat_s=2.0,
        should_emit_node_status=lambda **kwargs: (True, ()),
        emit_event=lambda *args, **kwargs: None,
    )

    service.start_heartbeats(lifecycle)

    assert lifecycle.find_live_task("adaos-control-lifecycle-heartbeat") is not None
    assert lifecycle.find_live_task("adaos-node-status-push-heartbeat") is not None
    await lifecycle.stop()
