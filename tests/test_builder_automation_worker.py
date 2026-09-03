from __future__ import annotations

import json

from adaos.services.builder.automation_worker import (
    _run_until_settled,
    _worker_root_from_environment,
    _write_worker_handshake,
)


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
