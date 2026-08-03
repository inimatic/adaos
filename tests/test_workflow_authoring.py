from __future__ import annotations

from pathlib import Path

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.builder.governed import builder_change_definition
from adaos.services.workflow_artifacts import validate_workflow_definition_report
from adaos.services.workflow_authoring import (
    WorkflowAuthoringHistoryStore,
    workflow_authoring_context,
)


def test_workflow_authoring_context_exports_exact_abi_and_adapter_catalogue() -> None:
    context = workflow_authoring_context(
        current_definition=builder_change_definition(),
        domain_invariants=[
            {
                "id": "builder.single_active_change",
                "description": "Only one active Builder change may own the current prototype.",
                "severity": "must",
            }
        ],
        examples=[
            {
                "id": "builder.approve",
                "summary": "Approve a prototype and start automation.",
            }
        ],
        generated_at="2026-08-03T00:00:00+00:00",
    )

    schema_names = {item["filename"] for item in context["abi_schemas"]}
    adapter_ids = {
        item["contract"]["adapter_id"] for item in context["adapter_catalog"]
    }

    assert context["schema"] == "adaos.workflow.authoring_context.v1"
    assert context["current_definition_digest"].startswith("sha256:")
    assert context["context_digest"].startswith("sha256:")
    assert "workflow.definition.v1.schema.json" in schema_names
    assert "workflow.transition.v1.schema.json" in schema_names
    assert "workflow.authoring_attempt.v1.schema.json" in schema_names
    assert "builder.codex.run" in adapter_ids
    assert context["role_policy"]["unknown_role_policy"] == "deny"
    assert context["role_policy"]["role_self_assignment"] == "rejected"
    assert context["domain_invariants"][0]["severity"] == "must"


def _repair(index: int) -> dict[str, str]:
    return {
        "repair_id": f"repair:{index}",
        "diagnostics_digest": canonical_payload_digest({"repair": index}),
        "action_summary": f"Repair attempt {index}",
    }


def test_workflow_authoring_history_persists_bounded_attempts_and_repairs(tmp_path: Path) -> None:
    context = workflow_authoring_context(
        generated_at="2026-08-03T00:00:00+00:00",
        context_id="workflow-authoring:test",
    )
    report = validate_workflow_definition_report(
        b'{"schema":"adaos.workflow.definition.v1","states":[]}'
    ).report
    store = WorkflowAuthoringHistoryStore(
        tmp_path / "workflow-authoring-history.json",
        max_attempts=2,
        max_repairs=3,
    )

    for index in range(3):
        store.record_attempt(
            context=context,
            attempt_id=f"attempt:{index}",
            model={
                "provider": "openai",
                "model": "gpt-test",
                "prompt_digest": canonical_payload_digest({"prompt": index}),
                "temperature": 0,
            },
            validation_report=report,
            repair_history=[_repair(item) for item in range(5)],
            recorded_at=f"2026-08-03T00:00:0{index}+00:00",
        )

    records = store.load()

    assert [item["attempt_id"] for item in records] == ["attempt:1", "attempt:2"]
    assert records[-1]["status"] == "validation_failed"
    assert records[-1]["context_digest"] == context["context_digest"]
    assert len(records[-1]["repair_history"]) == 3
    assert records[-1]["repair_history"][0]["repair_id"] == "repair:2"
    assert records[-1]["validation_report_digest"].startswith("sha256:")
