from __future__ import annotations

from types import SimpleNamespace

from adaos.services import release_validation_autorun as autorun
from adaos.services.root import core_update_sync


COMMIT = "90048f0123456789abcdef0123456789abcdef01"


class _Response:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RELEASE_VALIDATION_AUTORUN", "1")
    monkeypatch.setenv("ADAOS_SELF_BASE_URL", "http://0.0.0.0:8777")
    monkeypatch.setenv("ADAOS_SUPERVISOR_URL", "http://127.0.0.1:8776")
    monkeypatch.setattr(autorun, "active_slot", lambda: "B")
    monkeypatch.setattr(
        autorun,
        "active_slot_manifest",
        lambda: {"slot": "B", "git_commit": COMMIT, "build_version": "0.1.600+1.90048f0"},
    )
    monkeypatch.setattr(autorun, "read_status", lambda: {"state": "succeeded", "phase": "validate", "target_slot": "B"})


def test_autorun_persists_and_reuses_passed_build_report(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch)
    calls = []

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/ping"):
            return _Response({"ok": True, "service": "adaos-runtime", "runtime": {"slot": "B"}})
        return _Response(
            {
                "ok": True,
                "runtime": {
                    "active_slot": "B",
                    "runtime_state": "ready",
                    "listener_running": True,
                    "runtime_api_ready": True,
                },
            }
        )

    conf = SimpleNamespace(node_id="linux-exp-01", subnet_id="linux-exp", role="hub")
    report = autorun.run_autonomous_release_validation(conf, state_dir=tmp_path, request_get=request_get)
    reused = autorun.run_autonomous_release_validation(conf, state_dir=tmp_path, request_get=request_get)

    assert report is not None
    assert report["state"] == "passed"
    assert report["build_identity"] == COMMIT
    assert report["result"]["checks_passed"] == 4
    assert reused is not None and reused["reused"] is True
    assert len(calls) == 2
    latest = autorun.read_autonomous_release_validation_report(state_dir=tmp_path)
    assert latest is not None and latest["report_id"] == report["report_id"]
    assert (tmp_path / "release_validation" / "autonomous" / "reports" / f"{report['report_id']}.json").is_file()


def test_autorun_classifies_local_transport_failure_as_inconclusive(tmp_path, monkeypatch) -> None:
    _configure(monkeypatch)

    def request_get(url, **kwargs):
        del url, kwargs
        raise OSError("listener unavailable")

    report = autorun.run_autonomous_release_validation(
        SimpleNamespace(node_id="linux-exp-01", subnet_id="linux-exp", role="hub"),
        state_dir=tmp_path,
        request_get=request_get,
    )

    assert report is not None
    assert report["state"] == "inconclusive"
    assert report["result"]["checks_inconclusive"] == 2


def test_core_update_report_embeds_latest_autonomous_validation(monkeypatch) -> None:
    validation = {"report_id": "auto-1", "state": "passed", "build_identity": COMMIT}
    monkeypatch.setattr(core_update_sync, "read_autonomous_release_validation_report", lambda: validation)
    monkeypatch.setattr(core_update_sync, "runtime_identity_snapshot", lambda: {})
    monkeypatch.setattr(core_update_sync, "read_status", lambda: {"state": "succeeded"})
    monkeypatch.setattr(core_update_sync, "slot_status", lambda: {"active_slot": "B"})
    monkeypatch.setattr(core_update_sync, "active_slot_manifest", lambda: {"git_commit": COMMIT})

    report = core_update_sync.build_core_update_report(
        SimpleNamespace(node_id="linux-exp-01", subnet_id="linux-exp", role="hub")
    )

    assert report["release_validation"] == validation


def test_core_update_report_does_not_attach_stale_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        core_update_sync,
        "read_autonomous_release_validation_report",
        lambda: {"report_id": "auto-old", "state": "passed", "build_identity": "old-build"},
    )
    monkeypatch.setattr(core_update_sync, "runtime_identity_snapshot", lambda: {})
    monkeypatch.setattr(core_update_sync, "read_status", lambda: {"state": "succeeded"})
    monkeypatch.setattr(core_update_sync, "slot_status", lambda: {"active_slot": "B"})
    monkeypatch.setattr(core_update_sync, "active_slot_manifest", lambda: {"git_commit": COMMIT})

    report = core_update_sync.build_core_update_report(
        SimpleNamespace(node_id="linux-exp-01", subnet_id="linux-exp", role="hub")
    )

    assert "release_validation" not in report
