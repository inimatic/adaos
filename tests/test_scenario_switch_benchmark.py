from types import SimpleNamespace

from tools import scenario_switch_benchmark as benchmark


def test_ready_noop_does_not_wait_for_a_rebuild_that_was_skipped(monkeypatch):
    monkeypatch.setattr(benchmark, "_materialization", lambda *args, **kwargs: {
        "materialization": {"current_scenario": "applications", "ready": True},
        "rebuild": {"status": "ready", "pending": False, "started_at": 1, "finished_at": 2},
    })
    result = benchmark._wait_ready("http://localhost", "token", webspace="test",
        target="applications", since_epoch=100, timeout_s=1, poll_interval_s=.01,
        allow_existing=True)
    assert result["timeout"] is False
    assert result["polls"] == 1
    assert result["existing_materialization"] is True
    assert result["server_rebuild_ms"] is None
    assert result["server_accept_to_finish_ms"] is None


def test_new_switch_cannot_pass_with_a_stale_rebuild(monkeypatch):
    snapshots = iter([
        {"materialization": {"current_scenario": "applications", "ready": True},
         "rebuild": {"status": "ready", "finished_at": 2}},
        {"materialization": {"current_scenario": "applications", "ready": True},
         "rebuild": {"status": "ready", "started_at": 100, "finished_at": 101}},
    ])
    monkeypatch.setattr(benchmark, "_materialization", lambda *args, **kwargs: next(snapshots))
    result = benchmark._wait_ready("http://localhost", "token", webspace="test",
        target="applications", since_epoch=100, timeout_s=1, poll_interval_s=.01)
    assert result["timeout"] is False
    assert result["polls"] == 2
    assert result["existing_materialization"] is False
    assert result["server_rebuild_ms"] == 1000


def test_noop_still_waits_for_the_requested_ready_projection(monkeypatch):
    snapshots = iter([
        {"materialization": {"current_scenario": "other", "ready": True}},
        {"materialization": {"current_scenario": "applications", "ready": False}},
        {"materialization": {"current_scenario": "applications", "ready": True}},
    ])
    monkeypatch.setattr(benchmark, "_materialization", lambda *args, **kwargs: next(snapshots))
    result = benchmark._wait_ready("http://localhost", "token", webspace="test",
        target="applications", since_epoch=100, timeout_s=1, poll_interval_s=.01,
        allow_existing=True)
    assert result["timeout"] is False
    assert result["polls"] == 3


def test_rejected_navigation_does_not_poll_for_readiness(monkeypatch):
    monkeypatch.setattr(benchmark, "_find_listening_pid", lambda port: None)
    monkeypatch.setattr(benchmark, "_materialization", lambda *args, **kwargs: {})
    monkeypatch.setattr(benchmark, "_request", lambda *args, **kwargs: {
        "ok": False, "accepted": False, "error": "scenario_not_found",
    })
    monkeypatch.setattr(benchmark, "_wait_ready", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("rejected switch must not poll")))
    args = SimpleNamespace(base_url="http://localhost:8777", token="token", webspace="test",
        memory_port=0, memory_pid=0, request_source="test", timeout=1, label="test",
        sequence=["missing"], restore_current=True, cooldown=0, poll_timeout=1, poll_interval=.01)
    report = benchmark.run(args)
    assert report["actions"][0]["ready_wait"]["reason"] == "switch_rejected"


def test_cli_reports_rejected_navigation_as_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark, "run", lambda args: {"actions": [{
        "post": {"ok": False, "accepted": False, "error": "scenario_not_found"},
        "ready_wait": {"skipped": True},
    }]})
    monkeypatch.setattr("sys.argv", ["scenario_switch_benchmark", "--out", str(tmp_path / "report.json")])
    assert benchmark.main() == 1
