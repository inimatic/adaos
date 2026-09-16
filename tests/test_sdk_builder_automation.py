from __future__ import annotations

import json

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


def test_foreground_questions_are_not_reported_as_execution_failure():
    class Questions(_Service):
        def projection(self, **kwargs):
            result = super().projection(**kwargs)
            result["automation"].update(status="awaiting_input", waiting_for_input=True)
            return result

    result = automation._foreground_result(Questions(background=False, status="failed"), {"ok": True},
        object_type="scenario", object_id="sample", webspace_id="desktop")
    assert result["ok"] and result["status"] == "automation_awaiting_input"
    assert result["session"]["status"] == "failed"


def test_trial_verification_evidence_uses_sealed_automation_artifacts(
    monkeypatch, tmp_path
) -> None:
    task_id = "task.01TEST"
    output = tmp_path / task_id / "output"
    output.mkdir(parents=True)
    (output / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "commit_hash": "a" * 40,
                "tests": {"status": "passed"},
                "evidence": {
                    "schema": "adaos.skill_factory.task_evidence_manifest.v1",
                    "artifacts": [
                        {
                            "kind": "test_report",
                            "logical_path": ".adaos/tasks/test/test_report.json",
                            "digest": "sha256:" + "b" * 64,
                        },
                        {
                            "kind": "provenance",
                            "logical_path": ".adaos/tasks/test/provenance.json",
                            "digest": "sha256:" + "c" * 64,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (output / "test_report.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "errors": [],
                "checks": [
                    {
                        "kind": "checkpoint_test_contract",
                        "path": "scenarios/roster/tests/test_application_contract.py",
                        "ok": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    service = _Service(background=True)
    service.runs_root = tmp_path
    service.projection = lambda **_kwargs: {
        "automation": {
            "status": "completed",
            "terminal": True,
            "task_id": task_id,
        }
    }
    monkeypatch.setattr(automation, "_service", lambda: service)

    evidence = automation.trial_verification_evidence(
        object_type="scenario",
        object_id="roster",
    )

    assert evidence["status"] == "ready"
    assert evidence["source_commit"] == "a" * 40
    assert evidence["release_scope"] == "trial"
    assert evidence["regression_evidence"] == [
        "suite:checkpoint:.adaos/tasks/test/test_report.json#" + "b" * 64
    ]
    assert evidence["access_matrix_evidence"] == [
        "suite:access-matrix:scenarios/roster/tests/test_application_contract.py"
    ]


def test_trial_verification_evidence_blocks_without_access_matrix_test(
    monkeypatch, tmp_path
) -> None:
    task_id = "task.01TEST"
    output = tmp_path / task_id / "output"
    output.mkdir(parents=True)
    (output / "result.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "commit_hash": "a" * 40,
                "tests": {"status": "passed"},
                "evidence": {
                    "artifacts": [
                        {"kind": "test_report", "logical_path": "test.json", "digest": "sha256:b"},
                        {"kind": "provenance", "logical_path": "provenance.json", "digest": "sha256:c"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (output / "test_report.json").write_text(
        json.dumps({"status": "passed", "errors": [], "checks": []}),
        encoding="utf-8",
    )
    service = _Service(background=True)
    service.runs_root = tmp_path
    service.projection = lambda **_kwargs: {
        "automation": {"status": "completed", "terminal": True, "task_id": task_id}
    }
    monkeypatch.setattr(automation, "_service", lambda: service)

    evidence = automation.trial_verification_evidence(
        object_type="scenario", object_id="roster"
    )

    assert evidence == {
        "ok": False,
        "status": "blocked",
        "reason": "access_matrix_test_missing",
        "task_id": task_id,
    }
