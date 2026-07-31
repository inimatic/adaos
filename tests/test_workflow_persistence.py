from __future__ import annotations

import copy

import pytest

from adaos.services.agent_context import get_ctx
from adaos.services.builder.governed import compiled_builder_change_definition
from adaos.services.governed_workflow import (
    WorkflowResolver,
    migrate_workflow_instance,
    new_instance,
)
from adaos.services import workflow_persistence


def _decision(
    instance_id: str,
    *,
    state: str,
    command: str,
    confirmed: bool = False,
) -> dict[str, object]:
    definition = compiled_builder_change_definition()
    instance = new_instance(definition, instance_id)
    instance["state"] = state
    workflow_persistence.create_instance(instance)
    decision = WorkflowResolver().apply(
        definition,
        instance,
        command,
        input_value={"confirmed": confirmed, "evidence_refs": ["evidence:test"]},
        actor="user:local",
        roles=("registered",),
        expected_generation=0,
        idempotency_key=f"command:{instance_id}",
    )
    assert decision["accepted"] is True
    return decision


def test_atomic_commit_checks_and_idempotent_journal() -> None:
    decision = _decision(
        "change:persistence:checks",
        state="publication_ready",
        command="publish_compatibility",
        confirmed=True,
    )
    binding = {"activity": "builder.publication.publish", "executor": "builder:publisher"}
    with pytest.raises(workflow_persistence.WorkflowPersistenceError, match="permission"):
        workflow_persistence.commit_decision(
            decision,
            idempotency_key="publish:checks",
            permission_granted=False,
            target_digest="sha256:one",
            expected_target_digest="sha256:one",
            approval_required=True,
            approval_witness={"actor": "user:local"},
            effect_binding=binding,
        )
    with pytest.raises(workflow_persistence.WorkflowPersistenceError, match="target digest"):
        workflow_persistence.commit_decision(
            decision,
            idempotency_key="publish:checks",
            permission_granted=True,
            target_digest="sha256:changed",
            expected_target_digest="sha256:expected",
            approval_required=True,
            approval_witness={"actor": "user:local"},
            effect_binding=binding,
        )
    with pytest.raises(workflow_persistence.WorkflowPersistenceError, match="approval witness"):
        workflow_persistence.commit_decision(
            decision,
            idempotency_key="publish:checks",
            permission_granted=True,
            target_digest="sha256:expected",
            expected_target_digest="sha256:expected",
            approval_required=True,
            effect_binding=binding,
        )
    with pytest.raises(workflow_persistence.WorkflowPersistenceError, match="effect binding"):
        workflow_persistence.commit_decision(
            decision,
            idempotency_key="publish:checks",
            permission_granted=True,
            target_digest="sha256:expected",
            expected_target_digest="sha256:expected",
            approval_required=True,
            approval_witness={"actor": "user:local"},
            effect_binding={"activity": "wrong", "executor": "builder"},
        )

    committed = workflow_persistence.commit_decision(
        decision,
        idempotency_key="publish:checks",
        permission_granted=True,
        target_digest="sha256:expected",
        expected_target_digest="sha256:expected",
        approval_required=True,
        approval_witness={"actor": "user:local", "interaction_id": "confirm:1"},
        effect_binding=binding,
    )
    duplicate = workflow_persistence.commit_decision(
        decision,
        idempotency_key="publish:checks",
        permission_granted=True,
        target_digest="sha256:expected",
        expected_target_digest="sha256:expected",
        approval_required=True,
        approval_witness={"actor": "user:local", "interaction_id": "confirm:1"},
        effect_binding=binding,
    )

    assert committed["instance"]["state"] == "published"
    assert duplicate["duplicate"] is True
    assert len(workflow_persistence.list_events("change:persistence:checks")) == 1
    outbox = workflow_persistence.claim_outbox()
    assert len(outbox) == 1
    workflow_persistence.complete_outbox(outbox[0]["outbox_id"], delivered=False)
    retried = workflow_persistence.claim_outbox()
    assert retried[0]["attempt_count"] == 2
    workflow_persistence.complete_outbox(retried[0]["outbox_id"], delivered=True)
    description = workflow_persistence.operator_describe("change:persistence:checks")
    assert description["outbox"] == {"delivered": 1}


_ACTIVITY_CASES = [
    ("automation_ready", "start_automation", "builder.codex.run", False),
    ("verification", "request_prototype_derivation", "builder.prototype.derive", False),
    ("trial_ready", "start_trial", "builder.trial.activate", False),
    ("publication_ready", "begin_publication", "builder.publication.publish", True),
]


@pytest.mark.parametrize(("state", "command", "activity", "confirmed"), _ACTIVITY_CASES)
@pytest.mark.parametrize("crash_boundary", ["before_effect", "after_effect_started"])
def test_crash_boundaries_never_auto_repeat_unknown_effect(
    state: str,
    command: str,
    activity: str,
    confirmed: bool,
    crash_boundary: str,
) -> None:
    instance_id = f"change:crash:{command}:{crash_boundary}"
    decision = _decision(instance_id, state=state, command=command, confirmed=confirmed)
    committed = workflow_persistence.commit_decision(
        decision,
        idempotency_key=f"commit:{instance_id}",
        permission_granted=True,
        approval_required=confirmed,
        approval_witness={"actor": "user:local"} if confirmed else None,
        effect_binding={"activity": activity, "executor": "builder:test", "idempotent": False},
    )
    attempt_id = committed["activity_attempt_id"]
    workflow_persistence.claim_activity(attempt_id)
    if crash_boundary == "after_effect_started":
        workflow_persistence.mark_effect_started(attempt_id)

    recovered = workflow_persistence.recovery_report(instance_id)
    if crash_boundary == "before_effect":
        assert [item["attempt_id"] for item in recovered["safe_resume"]] == [attempt_id]
        assert recovered["reconciliation_required"] == []
    else:
        assert recovered["safe_resume"] == []
        assert [item["attempt_id"] for item in recovered["reconciliation_required"]] == [attempt_id]


def test_backup_restore_preserves_snapshot_and_journal() -> None:
    instance_id = "change:backup:1"
    decision = _decision(instance_id, state="ready", command="plan_prototype_change")
    workflow_persistence.commit_decision(
        decision,
        idempotency_key="commit:backup",
        permission_granted=True,
    )
    backup = workflow_persistence.export_instance(instance_id)

    with get_ctx().sql.connect() as con:
        con.execute("DELETE FROM governed_workflow_journal WHERE instance_id=?", (instance_id,))
        con.execute("DELETE FROM governed_workflow_instances WHERE instance_id=?", (instance_id,))
        con.commit()
    restored = workflow_persistence.restore_instance(backup)

    assert restored == backup["snapshot"]
    assert workflow_persistence.list_events(instance_id) == backup["events"]


def test_cancellation_is_safe_before_effect_and_unknown_after_effect_start() -> None:
    safe = _decision(
        "change:cancel:safe",
        state="automation_ready",
        command="start_automation",
    )
    safe_commit = workflow_persistence.commit_decision(
        safe,
        idempotency_key="cancel:safe",
        permission_granted=True,
        effect_binding={"activity": "builder.codex.run", "executor": "builder:test"},
    )
    safe_attempt = workflow_persistence.claim_activity(safe_commit["activity_attempt_id"])
    assert workflow_persistence.cancel_activity(
        safe_attempt["attempt_id"], reason="user cancelled"
    )["status"] == "cancelled"

    unknown = _decision(
        "change:cancel:unknown",
        state="automation_ready",
        command="start_automation",
    )
    unknown_commit = workflow_persistence.commit_decision(
        unknown,
        idempotency_key="cancel:unknown",
        permission_granted=True,
        effect_binding={"activity": "builder.codex.run", "executor": "builder:test"},
    )
    unknown_attempt = workflow_persistence.claim_activity(unknown_commit["activity_attempt_id"])
    workflow_persistence.mark_effect_started(unknown_attempt["attempt_id"])
    assert workflow_persistence.cancel_activity(
        unknown_attempt["attempt_id"], reason="process stopping"
    )["status"] == "outcome_unknown"
    metrics = workflow_persistence.operational_metrics()
    assert metrics["automatic_unknown_retries"] == 0
    assert metrics["outcome_unknown"] >= 1


def test_stale_snapshot_cannot_overwrite_committed_generation() -> None:
    instance_id = "change:stale:1"
    decision = _decision(instance_id, state="ready", command="plan_prototype_change")
    workflow_persistence.commit_decision(
        decision,
        idempotency_key="commit:stale:first",
        permission_granted=True,
    )
    other = copy.deepcopy(decision)
    other["event_records"][0]["event_id"] = "evt:other"
    other["event_records"][0]["idempotency_key"] = "other"
    with pytest.raises(workflow_persistence.WorkflowPersistenceError, match="stale workflow generation"):
        workflow_persistence.commit_decision(
            other,
            idempotency_key="commit:stale:other",
            permission_granted=True,
        )


def test_definition_migration_updates_durable_index_and_snapshot_atomically() -> None:
    source = compiled_builder_change_definition()
    target_value = copy.deepcopy(source.source)
    target_value["definition_version"] = "1.1.0"
    instance = new_instance(source, "change:persistence:migration")
    workflow_persistence.create_instance(instance)
    migration = {
        "schema": "adaos.workflow.definition_migration.v1",
        "migration_id": "builder_change_storage_1_1",
        "workflow_type": source.workflow_type,
        "from_definition_version": source.definition_version,
        "to_definition_version": "1.1.0",
        "allowed_source_states": [source.initial_state],
        "state_map": {source.initial_state: source.initial_state},
        "context_set": {"definition_migrated": True},
        "context_remove": [],
        "authority": {
            "actors": ["user"],
            "permissions": ["workflow.definition.migrate"],
        },
        "explanation": "Upgrade the pinned Builder workflow definition.",
    }
    decision = migrate_workflow_instance(
        source,
        target_value,
        instance,
        migration,
        actor="user:local",
        permissions=("workflow.definition.migrate",),
        expected_generation=0,
        idempotency_key="migration:persistence:1.1.0",
    )

    workflow_persistence.commit_decision(
        decision,
        idempotency_key="migration:persistence:1.1.0",
        permission_granted=True,
    )

    stored = workflow_persistence.get_instance(instance["instance_id"])
    assert stored is not None
    assert stored["definition_version"] == "1.1.0"
    assert stored["context"]["definition_migrated"] is True
    event = workflow_persistence.list_events(instance["instance_id"])[0]
    assert event["type"] == "workflow.definition.migrated"
