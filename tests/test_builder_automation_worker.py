from __future__ import annotations

import json

from adaos.services.builder import automation as automation_module
from adaos.services.builder.automation_worker import (
    _run_until_settled,
    _worker_root_from_environment,
    _write_worker_handshake,
    _recover_validated_session,
)


def test_worker_lock_allows_same_thread_browser_repair_reentry() -> None:
    assert automation_module._WORKER_LOCK.acquire(blocking=False)
    try:
        assert automation_module._WORKER_LOCK.acquire(blocking=False)
        automation_module._WORKER_LOCK.release()
    finally:
        automation_module._WORKER_LOCK.release()


class _QueuedService:
    def __init__(self) -> None:
        self.status = "queued"
        self.run_calls = 0

    def _find_session_by_id(self, session_id: str):
        return {"session_id": session_id, "status": self.status}

    def _run_worker(self, session_id: str) -> None:
        self.run_calls += 1
        if self.run_calls == 2:
            self.status = "completed"


def test_durable_worker_retries_its_exact_session_while_node_is_busy() -> None:
    service = _QueuedService()
    sleeps: list[float] = []

    result = _run_until_settled(
        service,  # type: ignore[arg-type]
        "automation.skill.research",
        poll_interval_seconds=0.25,
        sleep=sleeps.append,
    )

    assert result["status"] == "completed"
    assert service.run_calls == 2
    assert sleeps == [0.25]


def test_durable_worker_does_not_reexecute_an_assigned_session() -> None:
    service = _QueuedService()
    service.status = "in_progress"
    sleeps: list[float] = []

    def _complete(_: float) -> None:
        sleeps.append(0.25)
        service.status = "completed"

    result = _run_until_settled(
        service,  # type: ignore[arg-type]
        "automation.skill.research",
        poll_interval_seconds=0.25,
        sleep=_complete,
    )

    assert result["status"] == "completed"
    assert service.run_calls == 0
    assert sleeps == [0.25]


def test_worker_publishes_booting_handshake_from_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    worker_root = _worker_root_from_environment("automation.skill.demo")

    assert worker_root == (
        tmp_path.resolve()
        / "state"
        / "builder"
        / "automation_workers"
        / "automation.skill.demo"
    )

    assert worker_root is not None
    _write_worker_handshake(worker_root, "automation.skill.demo", status="booting")

    payload = json.loads((worker_root / "ready.json").read_text(encoding="utf-8"))
    assert payload["status"] == "booting"
    assert payload["session_id"] == "automation.skill.demo"
    from adaos.services.builder.worker_identity import worker_core_identity
    assert payload['core_identity'] == worker_core_identity()


def test_worker_rejects_a_different_core_before_publishing_readiness(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv('ADAOS_BUILDER_WORKER_CORE_IDENTITY', json.dumps({'source_root': 'older-checkout'}))
    with pytest.raises(RuntimeError, match='Core identity mismatch'):
        _write_worker_handshake(tmp_path, 'automation.skill.demo', status='booting')
    assert not (tmp_path / 'ready.json').exists()


def test_launcher_preserves_loaded_core_while_authoring_checkout_is_old(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from adaos.services.builder.worker_identity import worker_core_identity
    state = tmp_path / 'state'
    repo = tmp_path / 'older-authoring-checkout'
    repo.mkdir()
    service = SimpleNamespace(state_dir=state, repo_root=repo)
    root = state / 'builder/automation_workers/automation.test'
    captured = []
    def popen(command, **kwargs):
        captured.append(kwargs)
        (root / 'ready.json').write_text(json.dumps({'session_id': 'automation.test',
            'status': 'booting', 'pid': 123, 'core_identity': worker_core_identity()}))
        return SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(automation_module.subprocess, 'Popen', popen)
    result = automation_module.BuilderAutomationService._launch_worker_process(service, 'automation.test')
    assert result['core_identity'] == worker_core_identity()
    assert captured[0]['cwd'] == str(repo)
    assert captured[0]['env']['PYTHONPATH'].split(automation_module.os.pathsep)[0] == worker_core_identity()['source_root']
    assert result['status'] == 'booting'


def test_launcher_stops_a_worker_with_a_different_loaded_core(tmp_path, monkeypatch):
    import pytest
    from types import SimpleNamespace
    service = SimpleNamespace(state_dir=tmp_path / 'state', repo_root=tmp_path)
    root = service.state_dir / 'builder/automation_workers/automation.test'
    terminated = []
    def popen(command, **kwargs):
        (root / 'ready.json').write_text(json.dumps({'session_id': 'automation.test',
            'status': 'ready', 'core_identity': {'source_root': 'wrong'}}))
        return SimpleNamespace(pid=123, poll=lambda: None, terminate=lambda: terminated.append(True))
    monkeypatch.setattr(automation_module.subprocess, 'Popen', popen)
    with pytest.raises(RuntimeError, match='Core identity mismatch'):
        automation_module.BuilderAutomationService._launch_worker_process(service, 'automation.test')
    assert terminated == [True]
    assert json.loads((root / 'launch.json').read_text())['status'] == 'failed'


def test_recovery_worker_only_calls_the_validated_result_owner():
    calls = []
    class Service:
        def _find_session_by_id(self, identity):
            return {"session_id": identity, "object_type": "scenario", "object_id": "retouch"}
        def recover_validated_result(self, **kwargs):
            calls.append(kwargs)
            return {"session": {"status": "completed", "current_task_id": "task.retained"}}
        def _run_worker(self, *args):
            raise AssertionError("a recovery worker must not run the implementation worker")
    result = _recover_validated_session(Service(), "automation.scenario.retouch")
    assert calls == [{"object_type": "scenario", "object_id": "retouch"}]
    assert result["current_task_id"] == "task.retained"
