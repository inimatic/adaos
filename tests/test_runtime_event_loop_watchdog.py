from __future__ import annotations

import threading

from adaos.services import incident_registry, reliability
from adaos.services import runtime_event_loop_watchdog as watchdog_module


class _UnresponsiveLoop:
    def call_soon_threadsafe(self, _callback) -> None:
        return None


class _ResponsiveLoop:
    def call_soon_threadsafe(self, callback) -> None:
        callback()


def test_watchdog_captures_loop_stack_while_probe_is_unacknowledged(monkeypatch) -> None:
    incident_recorded = threading.Event()
    recorded: dict = {}
    states: list[dict] = []

    monkeypatch.setattr(
        watchdog_module,
        "capture_process_activity_sample",
        lambda: {"top_activity": [{"pid": 42, "domain": "skill:test_skill"}]},
    )

    def record_stall(**kwargs):
        recorded.update(kwargs)
        incident_recorded.set()
        return kwargs

    monkeypatch.setattr(watchdog_module, "record_runtime_event_loop_stall", record_stall)
    monkeypatch.setattr(watchdog_module, "set_runtime_event_loop_watchdog_state", lambda **kwargs: states.append(kwargs))
    monkeypatch.setattr(watchdog_module, "record_runtime_event_loop_watchdog_probe", lambda **_kwargs: {})

    watchdog = watchdog_module.RuntimeEventLoopWatchdog(
        loop=_UnresponsiveLoop(),  # type: ignore[arg-type]
        loop_thread_id=threading.get_ident(),
        config=watchdog_module.RuntimeEventLoopWatchdogConfig(
            interval_sec=0.05,
            threshold_ms=10.0,
            report_interval_sec=1.0,
        ),
    )
    watchdog.start()
    try:
        assert incident_recorded.wait(1.0)
    finally:
        watchdog.stop()

    assert recorded["stall_ms"] >= 10.0
    assert recorded["loop_thread_id"] == threading.get_ident()
    assert recorded["stack_frames"]
    assert recorded["process_sample"]["top_activity"][0]["pid"] == 42
    assert states[0]["running"] is True
    assert states[-1]["running"] is False


def test_watchdog_acks_responsive_loop_without_stall(monkeypatch) -> None:
    probe_recorded = threading.Event()
    probes: list[dict] = []

    monkeypatch.setattr(watchdog_module, "set_runtime_event_loop_watchdog_state", lambda **_kwargs: {})
    monkeypatch.setattr(
        watchdog_module,
        "record_runtime_event_loop_watchdog_probe",
        lambda **kwargs: (probes.append(kwargs), probe_recorded.set()),
    )
    monkeypatch.setattr(
        watchdog_module,
        "record_runtime_event_loop_stall",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected stall")),
    )

    watchdog = watchdog_module.RuntimeEventLoopWatchdog(
        loop=_ResponsiveLoop(),  # type: ignore[arg-type]
        loop_thread_id=threading.get_ident(),
        config=watchdog_module.RuntimeEventLoopWatchdogConfig(interval_sec=0.05, threshold_ms=10.0),
    )
    watchdog.start()
    try:
        assert probe_recorded.wait(1.0)
    finally:
        watchdog.stop()

    assert probes[0]["stalled"] is False


def test_watchdog_reliability_signal_tracks_stalls() -> None:
    reliability.reset_reliability_runtime_state()
    reliability.set_runtime_event_loop_watchdog_state(
        running=True,
        watchdog_thread_id=11,
        loop_thread_id=22,
        interval_sec=0.5,
        threshold_ms=250.0,
    )
    reliability.record_runtime_event_loop_watchdog_probe(elapsed_ms=251.5, stalled=True)

    snapshot = reliability.runtime_event_loop_watchdog_snapshot()

    assert snapshot["running"] is True
    assert snapshot["status"] == "stalled"
    assert snapshot["stall_total"] == 1
    assert snapshot["last_stall_ms"] == 251.5


def test_stall_incident_attributes_runtime_skill_stack(monkeypatch) -> None:
    incident_registry.reset_incident_registry()
    monkeypatch.setattr(incident_registry, "process_activity_history_snapshot", lambda limit=8: {"sample_total": 1})

    recorded = incident_registry.record_runtime_event_loop_stall(
        stall_ms=750.0,
        threshold_ms=250.0,
        interval_sec=0.5,
        stack_frames=[
            {
                "filename": "/root/.adaos/workspace/skills/.runtime/downloader_skill/v1/handlers/main.py",
                "lineno": 42,
                "function": "download",
            }
        ],
        loop_thread_id=11,
        watchdog_thread_id=22,
        process_sample={"system_delta": {"network_recv_bytes_delta": 1024}},
    )

    assert recorded["class"] == "runtime_event_loop_stall"
    assert recorded["domain"] == "skill:downloader_skill"
    assert recorded["latest_evidence"]["stack_frames"][0]["function"] == "download"
