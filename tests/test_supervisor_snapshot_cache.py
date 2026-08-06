from __future__ import annotations

import sys
from types import SimpleNamespace

from adaos.services import reliability


def _supervisor_payload() -> dict[str, object]:
    return {
        "status": {"state": "idle"},
        "attempt": {},
        "runtime": {
            "required_upstream_link": {
                "kind": "hub_root",
                "state": "ready",
                "ready": True,
                "transport_state": "ready",
                "transition_state": "ready",
                "blockers": [],
            }
        },
        "_served_by": "supervisor",
    }


def test_supervisor_snapshot_reuses_fresh_projection(monkeypatch) -> None:
    reliability.reset_reliability_runtime_state()
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    calls = {"get": 0}

    class _Response:
        status_code = 200

        def json(self):
            return _supervisor_payload()

    class _Session:
        trust_env = True

        def get(self, *args, **kwargs):
            calls["get"] += 1
            return _Response()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=lambda: _Session()))

    refreshed = reliability.supervisor_transition_runtime_snapshot(timeout_sec=0.2)
    cached = reliability.supervisor_transition_runtime_snapshot(timeout_sec=0.2)

    assert refreshed["available"] is True
    assert refreshed["_cache"]["state"] == "refresh"
    assert cached["available"] is True
    assert cached["_cache"]["state"] == "hit"
    assert cached["required_upstream_link"]["state"] == "ready"
    assert calls["get"] == 1


def test_supervisor_snapshot_uses_marked_stale_value_after_transient_failure(monkeypatch) -> None:
    reliability.reset_reliability_runtime_state()
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")
    monkeypatch.setenv("ADAOS_SUPERVISOR_PORT", "8776")
    monkeypatch.setenv("ADAOS_SUPERVISOR_SNAPSHOT_CACHE_TTL_SEC", "0.5")
    monkeypatch.setenv("ADAOS_SUPERVISOR_SNAPSHOT_STALE_MAX_SEC", "30")
    clock = {"now": 100.0}
    monkeypatch.setattr(reliability.time, "monotonic", lambda: clock["now"])
    calls = {"get": 0}

    class _Response:
        status_code = 200

        def json(self):
            return _supervisor_payload()

    class _Session:
        trust_env = True

        def get(self, *args, **kwargs):
            calls["get"] += 1
            if calls["get"] > 1:
                raise TimeoutError("supervisor briefly busy")
            return _Response()

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=lambda: _Session()))

    refreshed = reliability.supervisor_transition_runtime_snapshot(timeout_sec=0.2)
    clock["now"] = 102.0
    stale = reliability.supervisor_transition_runtime_snapshot(timeout_sec=0.2)

    assert refreshed["available"] is True
    assert stale["available"] is True
    assert stale["_cache"]["state"] == "stale"
    assert stale["_cache"]["stale"] is True
    assert "supervisor briefly busy" in stale["_cache"]["refresh_error"]
    assert stale["required_upstream_link"]["state"] == "ready"
    assert calls["get"] >= 2
