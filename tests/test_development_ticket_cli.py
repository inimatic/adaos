from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from adaos.apps.cli.commands import dev as dev_cmd


def _payload(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_dev_ticket_cli_lifecycle(tmp_path: Path) -> None:
    runner = CliRunner()
    state_arg = ["--state-dir", str(tmp_path)]

    created = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "new",
                "Skill legacy_skill lacks receiver/data-route declaration for legacy.panel",
                "--kind",
                "runtime_compatibility_debt",
                "--target-type",
                "skill",
                "--target-id",
                "legacy_skill",
                "--target-version",
                "1.0.0",
                "--source",
                "codex_review",
                "--severity",
                "high",
                "--blocking",
                "--evidence",
                "review:missing_receiver",
                *state_arg,
                "--json",
            ],
        )
    )
    ticket_id = created["ticket"]["ticket_id"]
    assert created["signal"]["kind"] == "compatibility_finding"
    assert created["ticket"]["status"] == "proposed"
    assert created["ticket"]["target_scope"]["id"] == "legacy_skill"

    listed = _payload(runner.invoke(dev_cmd.ticket_app, ["list", "--target-id", "legacy_skill", *state_arg, "--json"]))
    assert [item["ticket_id"] for item in listed["tickets"]] == [ticket_id]

    handoff = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            ["handoff", ticket_id, "--mode", "autonomous", "--actor", "codex:test", *state_arg, "--json"],
        )
    )
    assert handoff["ticket"]["status"] == "in_builder"
    assert handoff["repair"]["project_id"] == "legacy_skill"
    assert handoff["repair"]["context"]["development_ticket"]["handoff_mode"] == "autonomous"

    resolved = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "resolve",
                ticket_id,
                "--evidence",
                "test:tests/test_development_ticket_cli.py",
                "--evidence",
                "validation:receiver_contract",
                "--version",
                "legacy_skill@1.0.1",
                "--actor",
                "builder:test",
                *state_arg,
                "--json",
            ],
        )
    )
    assert resolved["ticket"]["status"] == "resolved"
    assert resolved["closure"]["resolved_by_version"] == "legacy_skill@1.0.1"

    verified = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "verify",
                ticket_id,
                "--evidence",
                "runtime_guard:receiver_contract_after_fix",
                "--actor",
                "validation:test",
                *state_arg,
                "--json",
            ],
        )
    )
    assert verified["ticket"]["status"] == "verified"

    closed = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            ["close", ticket_id, "--reason", "closed", "--actor", "validation:test", *state_arg, "--json"],
        )
    )
    assert closed["ticket"]["status"] == "closed"

    reopened = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            ["reopen", ticket_id, "--reason", "runtime regression", "--actor", "codex:test", *state_arg, "--json"],
        )
    )
    assert reopened["ticket"]["status"] == "in_progress"


def test_dev_ticket_cli_dedups_and_defers(tmp_path: Path) -> None:
    runner = CliRunner()
    args = [
        "new",
        "Improve modal feedback wording",
        "--kind",
        "feedback",
        "--target-type",
        "scenario",
        "--target-id",
        "daily_dashboard",
        "--state-dir",
        str(tmp_path),
        "--json",
    ]

    first = _payload(runner.invoke(dev_cmd.ticket_app, args))
    duplicate = _payload(runner.invoke(dev_cmd.ticket_app, args))
    ticket_id = first["ticket"]["ticket_id"]

    assert duplicate["ticket_duplicate"] is True
    assert duplicate["ticket"]["ticket_id"] == ticket_id
    assert duplicate["ticket"]["occurrence_count"] == 2

    deferred = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            ["defer", ticket_id, "--reason", "later", "--state-dir", str(tmp_path), "--json"],
        )
    )
    assert deferred["ticket"]["status"] == "deferred"


def test_dev_ticket_cli_core_request_and_sdk_understanding(tmp_path: Path) -> None:
    runner = CliRunner()
    state_arg = ["--state-dir", str(tmp_path)]

    project = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "new",
                "Project repair is blocked by SDK ambiguity",
                "--kind",
                "development_request",
                "--target-type",
                "modal",
                "--target-id",
                "nlu_teacher_modal",
                "--owner-area",
                "project",
                "--component-ref",
                "modal:nlu_teacher_modal",
                *state_arg,
                "--json",
            ],
        )
    )
    project_ticket_id = project["ticket"]["ticket_id"]

    core = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "core-request",
                "Builder needs stable ticket artifact helpers",
                "--component-ref",
                "core:sdk",
                "--desired-contract",
                "Expose Dev Ticket artifact open/read helpers to Codex and Builder.",
                "--impact",
                "blocker",
                "--blocked-ticket-id",
                project_ticket_id,
                "--evidence",
                "trace:builder.artifact.lookup",
                *state_arg,
                "--json",
            ],
        )
    )

    assert core["ticket"]["kind"] == "core_capability_request"
    assert core["ticket"]["owner_area"] == "core"
    assert core["blocked_tickets"][0]["status"] == "waiting_for_core"

    sdk = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            [
                "sdk-understanding",
                "Artifact refs are not clear enough for agent use",
                "--method-ref",
                "dev_ticket.artifact",
                "--kind",
                "sdk_example_gap",
                "--diagnosis",
                "sdk_example_gap",
                "--project-ticket-id",
                project_ticket_id,
                *state_arg,
                "--json",
            ],
        )
    )
    assert sdk["ticket"]["kind"] == "sdk_understanding"
    assert sdk["ticket"]["owner_area"] == "sdk"

    listed = _payload(
        runner.invoke(
            dev_cmd.ticket_app,
            ["list", "--owner-area", "core", "--component-ref", "core:sdk", *state_arg, "--json"],
        )
    )
    assert [item["ticket_id"] for item in listed["tickets"]] == [core["ticket"]["ticket_id"]]
