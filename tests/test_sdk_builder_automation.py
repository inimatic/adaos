from __future__ import annotations

from adaos.sdk.builder import automation


class _Service:
    def __init__(self, *, background: bool, status: str = "completed") -> None:
        self.background = background
        self.status = status

    def projection(self, **_kwargs):
        return {
            "ok": self.status == "completed",
            "session": {"status": self.status, "current_task_id": "task.1"},
            "automation": {"status": self.status, "task_id": "task.1"},
        }


def test_foreground_result_returns_durable_completed_projection() -> None:
    result = automation._foreground_result(
        _Service(background=False),
        {"ok": True, "status": "automation_queued", "automation": {"status": "queued"}},
        object_type="scenario",
        object_id="recipes",
        webspace_id="desktop",
    )

    assert result["ok"] is True
    assert result["status"] == "automation_completed"
    assert result["session"]["current_task_id"] == "task.1"
    assert result["automation"]["status"] == "completed"


def test_foreground_result_surfaces_terminal_failure() -> None:
    result = automation._foreground_result(
        _Service(background=False, status="failed"),
        {"ok": True, "status": "automation_queued"},
        object_type="scenario",
        object_id="recipes",
        webspace_id="desktop",
    )

    assert result["ok"] is False
    assert result["status"] == "automation_failed"


def test_background_result_keeps_queued_acknowledgement() -> None:
    queued = {"ok": True, "status": "automation_queued"}

    assert automation._foreground_result(
        _Service(background=True),
        queued,
        object_type="scenario",
        object_id="recipes",
        webspace_id="desktop",
    ) == queued
