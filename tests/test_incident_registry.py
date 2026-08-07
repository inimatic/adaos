from __future__ import annotations

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
