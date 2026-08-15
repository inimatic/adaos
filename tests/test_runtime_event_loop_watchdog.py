from __future__ import annotations

import threading

from adaos.services import incident_registry, reliability
from adaos.services import runtime_event_loop_watchdog as watchdog_module
from adaos.services.skill import subscription_execution


class _UnresponsiveLoop:
    def call_soon_threadsafe(self, _callback) -> None:
        return None


class _ResponsiveLoop:
    def call_soon_threadsafe(self, callback) -> None:
        callback()


class _DelayedLoop:
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    def call_soon_threadsafe(self, callback) -> None:
        threading.Timer(self.delay_s, callback).start()


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


def test_watchdog_preserves_skill_attribution_until_stall_completion(monkeypatch) -> None:
    correlated = threading.Event()
    captured = [
        {
            "handler_key": "demo\0topic\0handler",
            "skill": "demo_skill",
            "topic": "topic",
            "handler": "handler",
        }
    ]
    correlation: dict = {}

    monkeypatch.setattr(watchdog_module, "set_runtime_event_loop_watchdog_state", lambda **_kwargs: {})
    monkeypatch.setattr(watchdog_module, "record_runtime_event_loop_watchdog_probe", lambda **_kwargs: {})
    monkeypatch.setattr(watchdog_module, "record_runtime_event_loop_stall", lambda **_kwargs: {})
    monkeypatch.setattr(watchdog_module, "capture_process_activity_sample", lambda: {})
    monkeypatch.setattr(
        subscription_execution,
        "capture_active_skill_handlers_for_stack",
        lambda _frames: captured,
    )

    def correlate(**kwargs):
        correlation.update(kwargs)
        correlated.set()
        return []

    monkeypatch.setattr(subscription_execution, "correlate_runtime_event_loop_stall", correlate)

    watchdog = watchdog_module.RuntimeEventLoopWatchdog(
        loop=_DelayedLoop(0.06),  # type: ignore[arg-type]
        loop_thread_id=threading.get_ident(),
        config=watchdog_module.RuntimeEventLoopWatchdogConfig(
            interval_sec=0.05,
            threshold_ms=10.0,
            report_interval_sec=1.0,
        ),
    )
    watchdog.start()
    try:
        assert correlated.wait(1.0)
    finally:
        watchdog.stop()

    assert correlation["candidates"] == captured
    assert correlation["stall_ms"] >= 50.0


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
    reliability.record_runtime_event_loop_watchdog_probe(
        elapsed_ms=2104.25,
        stalled=False,
        completed_stall=True,
    )

    snapshot = reliability.runtime_event_loop_watchdog_snapshot()

    assert snapshot["running"] is True
    assert snapshot["status"] == "watching"
    assert snapshot["stall_total"] == 1
    assert snapshot["last_stall_ms"] == 2104.25
    assert snapshot["max_stall_ms"] == 2104.25


def test_watchdog_reliability_preserves_stall_stack_transitions() -> None:
    reliability.reset_reliability_runtime_state()
    initial = [{"filename": "skill_env.py", "lineno": 200, "function": "write_env"}]
    final = [{"filename": "sqlite.py", "lineno": 500, "function": "durable_state_write"}]
    candidate = [{"skill": "demo_skill", "topic": "state.changed", "handler": "handlers.on_state"}]

    reliability.record_runtime_event_loop_watchdog_stall_evidence(
        phase="detected",
        elapsed_ms=250.0,
        stack_frames=initial,
    )
    reliability.record_runtime_event_loop_watchdog_stall_evidence(
        phase="sample",
        elapsed_ms=750.0,
        stack_frames=final,
        skill_candidates=candidate,
    )
    reliability.record_runtime_event_loop_watchdog_stall_evidence(
        phase="completed",
        elapsed_ms=1250.0,
        stack_frames=final,
        skill_candidates=candidate,
    )

    snapshot = reliability.runtime_event_loop_watchdog_snapshot()
    completed = snapshot["last_completed_stall"]
    assert snapshot["current_stall"] is None
    assert completed["duration_ms"] == 1250.0
    assert completed["initial_stack"][-1]["function"] == "write_env"
    assert completed["last_stack"][-1]["function"] == "durable_state_write"
    assert completed["skill_candidates"] == candidate
    assert len(completed["samples"]) == 3
    assert snapshot["recent_stalls"][-1]["sample_total"] == 3


def test_stall_incident_completion_updates_duration_without_double_count(monkeypatch) -> None:
    incident_registry.reset_incident_registry()
    monkeypatch.setattr(incident_registry, "process_activity_history_snapshot", lambda limit=8: {})
    kwargs = {
        "threshold_ms": 250.0,
        "interval_sec": 0.5,
        "stack_frames": [
            {
                "filename": "/root/.adaos/workspace/skills/.runtime/demo_skill/v1/handlers/main.py",
                "lineno": 42,
                "function": "refresh",
            }
        ],
        "loop_thread_id": 11,
        "watchdog_thread_id": 22,
        "process_sample": {},
    }

    incident_registry.record_runtime_event_loop_stall(stall_ms=250.0, **kwargs)
    completed = incident_registry.record_runtime_event_loop_stall(
        stall_ms=2200.0,
        increment_occurrence=False,
        **kwargs,
    )

    assert completed["occurrence_count"] == 1
    assert completed["latest_evidence"]["stall_ms"] == 2200.0
    assert "2200.0 ms" in completed["summary"]


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
