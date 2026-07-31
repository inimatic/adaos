from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services import conversation_interactions, workflow_persistence
from adaos.services.artifact_pipeline import (
    ActivationError,
    ContentAddressedPackageStore,
    PackageCatalog,
    WorkspaceActivationManager,
    build_artifact_package,
    build_project_release,
)
from adaos.services.builder.governed import builder_change_definition
from adaos.services.governed_workflow import (
    WorkflowResolver,
    migrate_workflow_instance,
    new_instance,
    verified_workflow_principal,
    workflow_ref,
)
from adaos.services.workflow_artifacts import load_manifest_bound_workflow
from adaos.services.workflow_execution import (
    WorkflowExecutorRegistry,
    description_with_executor_readiness,
    execute_invocation,
    prepare_interaction_invocation,
)
from adaos.services.workflow_registry import platform_workflow_adapter_registry


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/guided_checklist/",),
    )


def _transition(
    *,
    transition_id: str,
    command: str,
    source: str,
    target: str,
    roles: list[str],
    risk_class: str,
    side_effect: str,
    definition_version: str,
) -> dict[str, object]:
    template = copy.deepcopy(builder_change_definition()["transitions"][0])
    template.update(
        {
            "transition_id": transition_id,
            "source": source,
            "target": target,
            "trigger": {
                "kind": "command",
                "command": command,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            "context": {
                "target_resolution": "instance",
                "command_context_required": True,
            },
            "authority": {"actors": ["*"], "permissions": [], "roles": roles},
            "guards": [{"id": "always", "params": {}, "reason_code": "checklist_blocked"}],
            "concurrency": {
                "conflict_scope": "guided_checklist",
                "requires_generation": True,
                "idempotency": "required",
            },
            "risk": {
                "class": risk_class,
                "side_effect": side_effect,
                "confirmation": "none",
            },
            "effect": {
                "activity": None,
                "transaction": "atomic",
                "retry": "never",
                "compensation": None,
            },
            "recovery": {
                "timeout_seconds": None,
                "heartbeat_seconds": None,
                "cancellation": "not_applicable",
                "reconciliation": "not_applicable",
            },
            "outcomes": {
                "success": target,
                "failure": source,
                "input_required": source,
                "cancelled": source,
                "unknown": source,
            },
            "evidence": {"required": False, "minimum": 0},
            "approval": {"required": False, "policy_refs": []},
            "async_reply": {"mode": "terminal", "reply_route": "origin"},
            "capability_requirements": {
                "required": [],
                "optional": ["buttons"],
                "fallback": "numbered_text",
            },
            "explanations": {
                "allowed": f"{command} is available for this checklist.",
                "rejected": f"{command} is not available for this checklist.",
                "completed": f"{command} completed for this checklist.",
            },
            "events": {
                "emitted": [f"guided_checklist.{command}.admitted"],
                "outbox": True,
            },
            "observability": {
                "audit_event": "guided_checklist.transition",
                "redaction": "none",
                "metrics": ["guided_checklist_transition_total"],
                "trace": True,
            },
            "migration": {"introduced_in": definition_version, "aliases": []},
        }
    )
    return template


def _definition(version: str, *, open_state: str) -> dict[str, object]:
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": "scenario.guided_checklist",
        "definition_version": version,
        "aggregate_type": "scenario.guided_checklist",
        "initial_state": open_state,
        "states": [
            {"id": open_state, "label": "Open", "terminal": False},
            {"id": "completed", "label": "Completed", "terminal": True},
        ],
        "commands": [
            {
                "id": "inspect_checklist",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "id": "complete_checklist",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ],
        "transitions": [
            _transition(
                transition_id=f"inspect_{open_state}",
                command="inspect_checklist",
                source=open_state,
                target=open_state,
                roles=["guest", "registered"],
                risk_class="read",
                side_effect="none",
                definition_version=version,
            ),
            _transition(
                transition_id=f"complete_{open_state}",
                command="complete_checklist",
                source=open_state,
                target="completed",
                roles=["registered"],
                risk_class="local_reversible",
                side_effect="reversible",
                definition_version=version,
            ),
        ],
        "metadata": {"domain": "guided_checklist", "owner": "scenario"},
    }


def _build(root: Path, *, package_version: str, definition: dict[str, object]):
    scenario = root / f"guided-checklist-{package_version}"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "\n".join(
            [
                "id: guided_checklist",
                f"version: {package_version}",
                "title: Guided checklist",
                "supported_locales:",
                "  - en",
                "  - ru",
                "workflow:",
                "  manifest: workflow.json",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (scenario / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "title": "Guided checklist"}) + "\n",
        encoding="utf-8",
    )
    return build_artifact_package(scenario, kind="scenario", source_ref=_source())


def _plan(built, *, migrations=()):
    return build_project_release(
        project_id="guided_checklist",
        version=built.ref.version,
        source_ref=_source(),
        components=(built.ref,),
        catalog=PackageCatalog(),
        migrations=migrations,
    )


def _activate(manager: WorkspaceActivationManager, plan, **kwargs):
    kwargs.setdefault(
        "reload_policy",
        {
            "mode": "skip",
            "approved_by": "pytest.governed_workflow_artifact_e2e",
            "reason": "isolated test Workspace has no attached runtime",
        },
    )
    if kwargs.get("health_check") is None:
        kwargs.setdefault(
            "health_policy",
            {
                "mode": "skip",
                "approved_by": "pytest.governed_workflow_artifact_e2e",
                "reason": "this activation does not exercise a live runtime",
            },
        )
    return manager.activate(plan, **kwargs)


def _principal(*, authenticated: bool, permissions=()):
    return verified_workflow_principal(
        "user:local" if authenticated else "guest:local",
        authenticated=authenticated,
        issuer="tests.guided_checklist",
        permissions=permissions,
    )


def test_non_builder_artifact_uses_same_package_roles_invocation_migration_and_rollback(
    tmp_path: Path,
) -> None:
    v1_definition = _definition("1.0.0", open_state="open")
    v2_definition = _definition("1.1.0", open_state="open_v2")
    v1 = _build(tmp_path, package_version="1.0.0", definition=v1_definition)
    v2 = _build(tmp_path, package_version="1.1.0", definition=v2_definition)
    store = ContentAddressedPackageStore(tmp_path / "package-store")
    manager = WorkspaceActivationManager(
        workspace_root=tmp_path / "workspace",
        package_store=store,
        state_root=tmp_path / "state",
    )
    store.put(v1.archive_bytes)
    store.put(v2.archive_bytes)
    active = _activate(manager, _plan(v1), idempotency_key="guided-checklist:v1")
    assert active.workspace_lock.components[0].workflow_binding_digest == v1.ref.workflow_binding_digest

    workspace_artifact = tmp_path / "workspace" / "scenarios" / "guided_checklist"
    artifact = load_manifest_bound_workflow(workspace_artifact, manifest_name="scenario.yaml")
    assert artifact is not None
    compiled = artifact.compiled
    instance = new_instance(
        compiled,
        "guided-checklist:interactive",
        context={"target_ref": workflow_ref("aggregate", "guided_checklist")},
    )
    guest_description = WorkflowResolver(require_verified_principal=True).describe(
        compiled,
        instance,
        actor="guest:local",
        principal=_principal(authenticated=False),
    )
    registered_description = WorkflowResolver(require_verified_principal=True).describe(
        compiled,
        instance,
        actor="user:local",
        principal=_principal(authenticated=True),
    )
    assert {item["command"] for item in guest_description["allowed_commands"]} == {
        "inspect_checklist"
    }
    assert {item["command"] for item in registered_description["allowed_commands"]} == {
        "inspect_checklist",
        "complete_checklist",
    }

    adapters = platform_workflow_adapter_registry()
    executable = description_with_executor_readiness(
        registered_description,
        compiled,
        WorkflowExecutorRegistry(adapters),
    )
    interaction = conversation_interactions.interaction_from_workflow_description(
        executable,
        conversation_id="conversation:guided-checklist",
        owner="scenario:guided_checklist",
        interaction_id="interaction:guided-checklist:complete",
        workflow_ref=workflow_ref(
            "workflow",
            instance["instance_id"],
            version=compiled.definition_version,
            generation=instance["generation"],
        ),
        command_context_ref=workflow_ref("command_context", "webspace:guided-checklist"),
    )
    presentation = conversation_interactions.negotiate_presentation(
        interaction,
        conversation_interactions.standard_capability_profile("telegram"),
    )
    action = next(
        item for item in presentation["actions"] if item["command"] == "complete_checklist"
    )
    response = conversation_interactions.submit_action_token(
        action["token"],
        actor_id="user:local",
        idempotency_key="telegram:guided-checklist:complete",
        metadata={"io_type": "telegram"},
    )["response"]
    invoked = execute_invocation(
        prepare_interaction_invocation(response),
        compiled,
        instance,
        principal=_principal(authenticated=True),
        adapters=adapters,
        executors=WorkflowExecutorRegistry(adapters),
    )
    assert invoked["accepted"] is True
    assert invoked["decision"]["after"]["state"] == "completed"

    in_flight = new_instance(compiled, "guided-checklist:in-flight")
    workflow_persistence.create_instance(in_flight)
    checkpoint = workflow_persistence.export_instance(in_flight["instance_id"])
    migration_contract = {
        "schema": "adaos.workflow.definition_migration.v1",
        "migration_id": "guided_checklist_1_1",
        "workflow_type": "scenario.guided_checklist",
        "from_definition_version": "1.0.0",
        "to_definition_version": "1.1.0",
        "allowed_source_states": ["open"],
        "state_map": {"open": "open_v2"},
        "context_set": {"migrated_by": "guided_checklist_1_1"},
        "context_remove": [],
        "authority": {
            "actors": ["user"],
            "permissions": ["workflow.definition.migrate"],
        },
        "explanation": "Move an open checklist to the v1.1 state name.",
    }
    release_migration = {
        "id": "guided-checklist-workflow-1.1.0",
        "workflow_component": "scenario:guided_checklist",
        "from_definition_digest": v1.ref.workflow_lock.digest,
        "to_definition_digest": v2.ref.workflow_lock.digest,
        "rollback": {
            "supported": True,
            "procedure_ref": "guided_checklist.workflow.rollback_1_1_0",
        },
    }

    def migrate(_request):
        decision = migrate_workflow_instance(
            v1_definition,
            v2_definition,
            workflow_persistence.get_instance(in_flight["instance_id"]),
            migration_contract,
            actor="user:local",
            permissions=("workflow.definition.migrate",),
            principal=_principal(
                authenticated=True,
                permissions=("workflow.definition.migrate",),
            ),
            require_verified_principal=True,
            expected_generation=0,
            idempotency_key="guided-checklist:migrate:1.1.0",
        )
        workflow_persistence.commit_decision(
            decision,
            idempotency_key="guided-checklist:migrate:1.1.0",
            permission_granted=True,
        )
        return {"status": "completed", "checkpoint": "guided-checklist:in-flight:v1"}

    def rollback(_request):
        workflow_persistence.rollback_instance(
            checkpoint,
            expected_current_definition_version="1.1.0",
            expected_current_generation=1,
        )
        return {"status": "rolled_back", "checkpoint": "guided-checklist:in-flight:v1"}

    with pytest.raises(ActivationError, match="health check failed"):
        _activate(
            manager,
            _plan(v2, migrations=(release_migration,)),
            idempotency_key="guided-checklist:v2:failed-health",
            migration_executor=migrate,
            migration_rollback=rollback,
            health_check=lambda _lock: False,
        )

    assert manager.load_lock() == active.workspace_lock
    restored_artifact = load_manifest_bound_workflow(
        workspace_artifact,
        manifest_name="scenario.yaml",
    )
    assert restored_artifact is not None
    assert restored_artifact.compiled.definition_version == "1.0.0"
    restored_instance = workflow_persistence.get_instance(in_flight["instance_id"])
    assert restored_instance["definition_version"] == "1.0.0"
    assert restored_instance["state"] == "open"
    assert restored_instance["generation"] == 0
