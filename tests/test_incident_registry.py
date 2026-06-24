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
