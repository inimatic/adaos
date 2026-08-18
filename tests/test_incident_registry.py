from __future__ import annotations

import os

import pytest

import adaos.services.incident_registry as incidents


def setup_function() -> None:
    incidents.reset_incident_registry()


def test_slow_event_handler_incident_is_attributed_to_skill() -> None:
    for _ in range(2):
        incidents.record_slow_event_handler(
            handler_label="adaos.sdk.data.bus._adapt skill=slideshow_skill topic=webio.stream.snapshot.requested",
            event_type="webio.stream.snapshot.requested",
            duration_s=1.25,
            kind="async",
            threshold_s=0.25,
        )

    snapshot = incidents.incident_registry_snapshot()

    assert snapshot["total"] == 1
    item = snapshot["items"][0]
    assert item["class"] == "slow_event_handler"
    assert item["domain"] == "skill:slideshow_skill"
    assert item["occurrence_count"] == 2
    assert item["active"] is True


def test_event_loop_lag_incident_keeps_skill_and_process_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        incidents,
        "process_activity_history_snapshot",
        lambda limit=8: {"sample_total": 2, "samples": [{"top_activity": [{"pid": 42}]}]},
    )

    recorded = incidents.record_runtime_event_loop_lag(
        lag_ms=41000.0,
        threshold_ms=250.0,
        interval_sec=1.0,
    )

    assert recorded["class"] == "runtime_event_loop_lag"
    assert recorded["severity"] == "degraded"
    evidence = recorded["latest_evidence"]
    assert evidence["lag_ms"] == 41000.0
    assert evidence["process_activity_history"]["sample_total"] == 2
    assert evidence["skill_subscription_execution"]["schema"] == "adaos.skill_subscription_execution.v1"


def test_runtime_timeout_records_redacted_blocking_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        incidents,
        "local_blocking_evidence",
        lambda include_processes=True: {
            "pressure": {"io": {"full": {"avg10": 9.5}}},
            "processes": {"top_rss": [{"pid": 10, "domain": "skill:test_skill"}]},
        },
    )

    incidents.record_runtime_api_timeout(
        source="supervisor.reliability_preflight",
        path="/api/node/reliability",
        timeout_s=1.5,
        exc=TimeoutError("read timeout token=secret-value"),
    )

    item = incidents.incident_registry_snapshot()["items"][0]

    assert item["signal"] == "runtime_api_unavailable"
    assert item["domain"] == "core.runtime"
    evidence = item["latest_evidence"]
    assert evidence["pressure"]["io"]["full"]["avg10"] == 9.5
    assert evidence["processes"]["top_rss"][0]["domain"] == "skill:test_skill"
    assert "secret-value" not in evidence["exception"]


def test_helper_incidents_are_attributed_and_merged() -> None:
    assert incidents.record_yjs_pressure_incident(
        pressure={"owner": "_by_owner/core", "policy_state": "ok", "observed_state": "idle"}
    ) is None

    yjs = incidents.record_yjs_pressure_incident(
        pressure={
            "owner": "_by_owner/infrastate_skill",
            "policy_state": "throttle",
            "observed_state": "pressure",
            "reason": "write_pressure_warning",
            "webspace_id": "desktop",
            "last_path": "/semantic/infrastate",
        }
    )
    first = incidents.record_action_timeout(
        method="adaos_connect.prepare.browser",
        scenario_id="web_desktop",
        webspace_id="desktop",
        route="webrtc_data:events",
        transport="webrtc_data",
        timeout_s=15.0,
    )
    second = incidents.record_action_timeout(
        method="adaos_connect.prepare.browser",
        scenario_id="web_desktop",
        webspace_id="desktop",
        route="ws",
        transport="ws",
        timeout_s=15.0,
    )

    snapshot = incidents.incident_registry_snapshot()
    by_class = {item["class"]: item for item in snapshot["items"]}

    assert yjs is not None
    assert by_class["yjs_pressure"]["domain"] == "skill:infrastate_skill"
    assert by_class["action_timeout"]["domain"] == "skill:adaos_connect"
    assert first["id"] == second["id"]
    assert by_class["action_timeout"]["occurrence_count"] == 2


def test_browser_and_member_helpers_use_expected_domains() -> None:
    browser = incidents.record_browser_transport_fallback(
        channel="sync",
        from_transport="webrtc_data:yjs",
        to_transport="http_root_routed:media",
        reason="rtc_failed",
        device_id="browser-1",
        webspace_id="desktop",
    )
    member = incidents.record_member_link_stale(
        node_id="member-1",
        hostname="codespaces-ee81be",
        last_seen_ago_s=600,
    )

    assert browser["class"] == "browser_transport_fallback"
    assert browser["domain"] == "browser:browser-1"
    assert browser["severity"] == "degraded"
    assert member["domain"] == "member:member-1"
    assert member["severity"] == "degraded"


def test_channel_incident_keeps_scope_distinct_from_state_transition() -> None:
    recorded = incidents.record_channel_incident(
        channel="route",
        status="no_upstream",
        summary="session frame arrived without an upstream",
        details={"impact_scope": "session", "key_tag": "abc123"},
        previous_status="ready",
    )

    assert recorded["class"] == "channel_incident"
    assert recorded["latest_evidence"]["impact_scope"] == "session"
    assert "session" in recorded["tags"]


def test_yjs_thread_affinity_fault_is_degraded_and_specialized() -> None:
    exc = RuntimeError("y_py::y_map::YMap is unsendbale, but is dropped on another thread!")

    recorded = incidents.record_event_handler_crash(
        handler_label="adaos.sdk.data.bus._adapt skill=infrastate_skill topic=browser.session.changed",
        event_type="browser.session.changed",
        exc=exc,
    )

    snapshot = incidents.incident_registry_snapshot()
    item = snapshot["items"][0]
    assert recorded["class"] == "yjs_thread_affinity_fault"
    assert item["class"] == "yjs_thread_affinity_fault"
    assert item["severity"] == "degraded"
    assert item["domain"] == "core.yjs"
    assert "state-sync" in item["tags"]


def test_yjs_thread_affinity_fault_recognizes_doc_and_transaction_wrappers() -> None:
    assert incidents.is_yjs_thread_affinity_fault(
        RuntimeError("y_py::y_doc::YDoc is unsendbale, but is dropped on another thread!")
    )
    assert incidents.is_yjs_thread_affinity_fault(
        RuntimeError("y_py::y_transaction::YTransaction is unsendbale, but is dropped on another thread!")
    )


def test_process_io_delta_sample_reports_deltas(monkeypatch) -> None:
    rows = iter(
        [
            [
                {
                    "pid": 10,
                    "name": "python",
                    "status": "running",
                    "domain": "skill:test_skill",
                    "cmdline": "handlers/main.py",
                    "read_bytes": 100,
                    "write_bytes": 200,
                }
            ],
            [
                {
                    "pid": 10,
                    "name": "python",
                    "status": "running",
                    "domain": "skill:test_skill",
                    "cmdline": "handlers/main.py",
                    "read_bytes": 125,
                    "write_bytes": 260,
                }
            ],
        ]
    )
    monkeypatch.setattr(incidents, "_process_rows", lambda: next(rows))
    monkeypatch.setattr(incidents.time, "sleep", lambda _seconds: None)

    sample = incidents.process_io_delta_sample(interval_s=0.1)

    top = sample["top_io_delta"][0]
    assert top["pid"] == 10
    assert top["read_delta_bytes"] == 25
    assert top["write_delta_bytes"] == 60
    assert top["domain"] == "skill:test_skill"


def test_process_activity_history_keeps_pre_failure_cpu_io_and_network_deltas(monkeypatch) -> None:
    process_rows = iter(
        [
            [
                {
                    "pid": 12,
                    "name": "curl",
                    "domain": "system.process",
                    "cmdline": "curl https://example.invalid/large.bin",
                    "rss_bytes": 1000,
                    "cpu_time_s": 2.0,
                    "read_bytes": 100,
                    "write_bytes": 50,
                }
            ],
            [
                {
                    "pid": 12,
                    "name": "curl",
                    "domain": "system.process",
                    "cmdline": "curl https://example.invalid/large.bin",
                    "rss_bytes": 1200,
                    "cpu_time_s": 3.0,
                    "read_bytes": 5100,
                    "write_bytes": 70,
                }
            ],
        ]
    )
    system_rows = iter(
        [
            {"network_recv_bytes": 1000, "network_sent_bytes": 200},
            {"network_recv_bytes": 9000, "network_sent_bytes": 500},
        ]
    )
    monkeypatch.setattr(incidents, "_process_rows", lambda: next(process_rows))
    monkeypatch.setattr(incidents, "_system_activity_counters", lambda: next(system_rows))

    incidents.capture_process_activity_sample(ts=100.0)
    second = incidents.capture_process_activity_sample(ts=110.0)
    history = incidents.process_activity_history_snapshot()

    assert history["sample_total"] == 2
    assert second["system_delta"]["network_recv_bytes_delta"] == 8000
    activity = second["top_activity"][0]
    assert activity["pid"] == 12
    assert activity["cpu_percent"] == 10.0
    assert activity["read_delta_bytes"] == 5000


def test_process_activity_history_default_covers_twelve_minutes_at_runtime_sample_rate() -> None:
    incidents.reset_incident_registry()
    for index in range(80):
        incidents._PROCESS_ACTIVITY_HISTORY.append(
            {
                "ts": 100.0 + index * 10.0,
                "interval_s": 10.0,
                "top_activity": [],
                "system_delta": {},
            }
        )

    history = incidents.process_activity_history_snapshot()

    assert history["returned"] >= 73
    assert history["coverage_s"] >= 720.0
    assert history["history_capacity"] >= history["returned"]


def test_process_activity_history_reports_freshness_and_sampling_gaps(monkeypatch) -> None:
    incidents.reset_incident_registry()
    monkeypatch.setattr(incidents, "_now", lambda: 141.0)
    for ts in (100.0, 110.0, 140.0):
        incidents._PROCESS_ACTIVITY_HISTORY.append(
            {"ts": ts, "interval_s": 10.0, "top_activity": [], "system_delta": {}}
        )

    history = incidents.process_activity_history_snapshot()

    assert history["coverage_status"] == "gapped"
    assert history["expected_interval_s"] == 10.0
    assert history["last_sample_age_s"] == 1.0
    assert history["max_gap_s"] == 30.0
    assert history["gap_breach_total"] == 1


def test_process_activity_history_reports_stale_or_empty_evidence(monkeypatch) -> None:
    incidents.reset_incident_registry()
    monkeypatch.setattr(incidents, "_now", lambda: 200.0)

    assert incidents.process_activity_history_snapshot()["coverage_status"] == "empty"

    incidents._PROCESS_ACTIVITY_HISTORY.append(
        {"ts": 100.0, "interval_s": 10.0, "top_activity": [], "system_delta": {}}
    )
    stale = incidents.process_activity_history_snapshot()

    assert stale["coverage_status"] == "stale"
    assert stale["last_sample_age_s"] == 100.0


def test_process_activity_name_filter_keeps_channel_related_and_configured_processes(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_INCIDENT_PROCESS_NAME_HINTS", "custom-worker")

    assert incidents._process_activity_name_relevant("python.exe", pid=123) is True
    assert incidents._process_activity_name_relevant("custom-worker.exe", pid=124) is True
    assert incidents._process_activity_name_relevant("svchost.exe", pid=125) is False
    assert incidents._process_activity_name_relevant("unrelated.exe", pid=os.getpid()) is True


def test_latest_process_activity_sample_reads_history_without_capture(monkeypatch) -> None:
    incidents.reset_incident_registry()
    incidents._PROCESS_ACTIVITY_HISTORY.append({"ts": 123.0, "process_total": 4})
    monkeypatch.setattr(incidents, "_process_rows", lambda: pytest.fail("must not capture processes"))

    assert incidents.latest_process_activity_sample() == {"ts": 123.0, "process_total": 4}


def test_process_activity_attributes_windows_skill_runtime_paths() -> None:
    command = r"C:\Python\python.exe C:\node\.adaos\workspace\skills\.runtime\downloader_skill\handlers\main.py"

    assert incidents._domain_from_cmdline(command) == "skill:downloader_skill"


def test_process_activity_attributes_builder_automation_to_target_object() -> None:
    skill_command = (
        "python -m adaos.services.builder.automation_worker "
        "--session-id automation.skill.weather_skill"
    )
    scenario_command = (
        "python -m adaos.services.builder.automation_worker "
        "--session-id=automation.scenario.mediacenter"
    )

    assert incidents._domain_from_cmdline(skill_command) == "skill:weather_skill"
    assert incidents._domain_from_cmdline(scenario_command) == "scenario:mediacenter"


def test_transport_incident_persists_process_lookback(monkeypatch, tmp_path) -> None:
    target = tmp_path / "transport-incidents.json"
    monkeypatch.setenv("ADAOS_INCIDENT_REGISTRY_PATH", str(target))
    monkeypatch.setattr(incidents, "_process_rows", lambda: [])
    monkeypatch.setattr(incidents, "_system_activity_counters", lambda: {})
    monkeypatch.setattr(
        incidents,
        "local_blocking_evidence",
        lambda include_processes=True: {"pressure": {"cpu": {}}},
    )
    incidents.capture_process_activity_sample(ts=100.0)

    recorded = incidents.record_hub_root_transport_incident(
        event="transient_disconnect",
        server="wss://ru.api.inimatic.com/nats?token=secret-value",
        error="WinError 10054 password=secret-value",
        details={"ran_for_s": 31.0},
    )

    assert recorded["persistence"]["ok"] is True
    assert target.is_file()
    item = incidents.incident_registry_snapshot()["items"][0]
    evidence = item["latest_evidence"]
    assert evidence["process_activity_history"]["sample_total"] == 2
    assert "secret-value" not in str(evidence)


def test_incident_registry_persists_and_loads_snapshot(tmp_path) -> None:
    target = tmp_path / "incidents.json"
    incidents.record_member_link_stale(node_id="member-1", last_seen_ago_s=60)

    persisted = incidents.persist_incident_registry(path=target)
    incidents.reset_incident_registry()
    loaded = incidents.load_incident_registry(path=target, replace=True)
    snapshot = incidents.incident_registry_snapshot()

    assert persisted["ok"] is True
    assert loaded["loaded"] == 1
    assert snapshot["total"] == 1
    assert snapshot["items"][0]["domain"] == "member:member-1"
