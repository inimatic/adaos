from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.builder.browser_feedback import (
    BuilderBrowserFeedbackService,
    browser_feedback_failures,
)
from adaos.services.skill_factory_worker import _browser_feedback_prompt_projection


def test_browser_feedback_scopes_primary_selection_and_layout_diagnostics() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "e2e"
        / "stand"
        / "browser"
        / "builder-candidate-feedback.mjs"
    ).read_text(encoding="utf-8")

    assert 'ada-layout-region[data-region-role="collection"]' in script
    assert "region.closest('.desktop-grid') === grid" in script
    assert "const diagnosticRoot = activeModal || document" in script
    assert "boundedToolFailureDiagnostic" in script
    assert "target.pathname === '/api/tools/call'" in script
    assert "request.postData()" in script
    assert "response.text()" in script
    assert "Promise.allSettled(responseDiagnosticTasks)" in script


def test_browser_feedback_binds_runtime_source_and_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENV_TYPE", "dev")
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "title": "Проверка"}, ensure_ascii=False),
        encoding="utf-8",
    )

    def runner(*args, **kwargs):
        assert kwargs["env"]["ADAOS_E2E_SPACE_KIND"] == "workspace"
        output = Path(kwargs["env"]["ADAOS_E2E_OUTPUT"])
        (output / "wide.png").write_bytes(b"wide")
        (output / "compact.png").write_bytes(b"compact")
        report = {
            "schema": "adaos.builder.browser_feedback.v1",
            "passed": True,
            "samples": [
                {
                    "layout": "wide",
                    "authoritative_data_settled": True,
                    "hard_failures": [],
                    "warnings": [],
                    "diagnostics": {"visible_widget_ids": ["main"]},
                },
                {
                    "layout": "compact",
                    "authoritative_data_settled": True,
                    "hard_failures": [],
                    "warnings": [],
                    "diagnostics": {"visible_widget_ids": ["main"]},
                },
            ],
        }
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    service = BuilderBrowserFeedbackService(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        command_runner=runner,
    )
    monkeypatch.setattr(
        "adaos.services.builder.browser_feedback.resolve_control_base_url",
        lambda **kwargs: "http://127.0.0.1:8777",
    )
    monkeypatch.setattr(
        "adaos.services.builder.browser_feedback.resolve_control_token",
        lambda **kwargs: "local-token",
    )

    receipt = service.evaluate(
        scenario_id="reading_list",
        webspace_id="desktop-dev",
        subnet_id="sn_test",
        task_id="task.1",
        source_path=scenario,
        context_packet_digest="sha256:" + "1" * 64,
        space_kind="workspace",
    )

    assert receipt["ok"] is True
    assert receipt["status"] == "passed"
    assert receipt["space_kind"] == "workspace"
    assert receipt["source"]["digest"].startswith("sha256:")
    assert receipt["report_digest"].startswith("sha256:")
    assert receipt["runtime_contract"]["webui_abi_digest"].startswith("sha256:")
    assert {item["name"] for item in receipt["evidence"]} >= {
        "wide.png",
        "compact.png",
        "report.json",
    }
    assert json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))[
        "source"
    ]["digest"] == receipt["source"]["digest"]


def test_browser_feedback_rejects_success_without_authoritative_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENV_TYPE", "dev")
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "title": "Preview"}),
        encoding="utf-8",
    )

    def runner(*args, **kwargs):
        output = Path(kwargs["env"]["ADAOS_E2E_OUTPUT"])
        report = {
            "schema": "adaos.builder.browser_feedback.v1",
            "passed": True,
            "samples": [
                {"layout": "wide", "hard_failures": [], "warnings": []},
                {"layout": "compact", "hard_failures": [], "warnings": []},
            ],
        }
        (output / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    service = BuilderBrowserFeedbackService(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        command_runner=runner,
    )
    monkeypatch.setattr(
        "adaos.services.builder.browser_feedback.resolve_control_base_url",
        lambda **kwargs: "http://127.0.0.1:8777",
    )
    monkeypatch.setattr(
        "adaos.services.builder.browser_feedback.resolve_control_token",
        lambda **kwargs: "local-token",
    )

    receipt = service.evaluate(
        scenario_id="reading_list",
        webspace_id="desktop-dev",
        subnet_id="sn_test",
        task_id="task.1",
        source_path=scenario,
        context_packet_digest="sha256:" + "1" * 64,
    )

    assert receipt["ok"] is False
    assert receipt["status"] == "failed"
    assert receipt["report"]["failure"] == "authoritative_data_not_settled"


def test_browser_feedback_rejects_unknown_space_kind(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENV_TYPE", "dev")
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "webui.json").write_text("{}", encoding="utf-8")
    service = BuilderBrowserFeedbackService(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
    )

    try:
        service.evaluate(
            scenario_id="reading_list",
            webspace_id="desktop",
            subnet_id="sn_test",
            task_id="task.1",
            source_path=scenario,
            space_kind="trial",
        )
    except ValueError as exc:
        assert str(exc) == "browser feedback space_kind must be development or workspace"
    else:
        raise AssertionError("invalid space_kind must be rejected")


def test_browser_feedback_projection_is_bounded_and_human_readable() -> None:
    receipt = {
        "schema": "adaos.builder.browser_feedback_receipt.v1",
        "status": "failed",
        "attempt": 1,
        "scenario_id": "inspection",
        "report": {
            "samples": [
                {
                    "layout": "compact",
                    "hard_failures": ["Русский текст не помещается"],
                    "warnings": ["missing label"],
                    "diagnostics": {
                        "document_width": 480,
                        "viewport_width": 390,
                        "visible_widget_ids": [f"widget-{index}" for index in range(300)],
                    },
                }
            ]
        },
        "evidence": [
            {"name": "compact.png", "path": "C:/evidence/compact.png", "sha256": "sha256:x"}
        ],
    }

    projection = _browser_feedback_prompt_projection(receipt)

    assert projection is not None
    assert projection["samples"][0]["hard_failures"] == [
        "Русский текст не помещается"
    ]
    assert len(projection["samples"][0]["diagnostics"]["visible_widget_ids"]) == 160
    assert browser_feedback_failures(receipt) == [
        "compact: Русский текст не помещается"
    ]


def test_browser_feedback_is_skipped_outside_dev(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_TYPE", "prod")
    service = BuilderBrowserFeedbackService(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
    )

    receipt = service.evaluate(
        scenario_id="app",
        webspace_id="desktop-dev",
        subnet_id="sn_test",
        task_id="task.1",
        source_path=tmp_path / "missing",
    )

    assert receipt == {
        "schema": "adaos.builder.browser_feedback_receipt.v1",
        "ok": True,
        "status": "skipped",
        "reason": "browser_feedback_is_dev_only",
        "env_type": "prod",
    }


def test_failed_browser_feedback_queues_one_bounded_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADAOS_BUILDER_BROWSER_REPAIR_ATTEMPTS", "1")
    service = BuilderAutomationService(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        background=False,
    )
    saved: list[dict] = []
    submitted: list[dict] = []

    class Workflow:
        def transition(self, *args, **kwargs):
            return {"ok": True}

    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: Workflow())
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "submit_turn",
        lambda self, **kwargs: submitted.append(dict(kwargs)) or {"ok": True},
    )
    current = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.reading_list",
        "object_type": "scenario",
        "object_id": "reading_list",
        "iteration": 2,
        "current_task_id": "task.2",
        "change_id": "change.2",
    }
    receipt = {
        "status": "failed",
        "receipt_path": "C:/evidence/receipt.json",
        "receipt_digest": "sha256:" + "2" * 64,
        "report": {
            "samples": [
                {
                    "layout": "compact",
                    "hard_failures": ["Page overflows horizontally"],
                }
            ]
        },
        "evidence": [{"path": "C:/evidence/compact.png"}],
    }

    assert service._queue_browser_feedback_repair(current, {}, receipt) is True
    assert saved[-1]["status"] == "failed"
    assert saved[-1]["browser_feedback_repair_count"] == 1
    assert submitted[-1]["expected_iteration"] == 2
    assert "compact: Page overflows horizontally" in submitted[-1]["text"]
    assert service._queue_browser_feedback_repair(current, {}, receipt) is False
