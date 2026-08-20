from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

from adaos.domain.project_deployment import (
    ComponentActivation,
    ComponentPlacementPolicy,
    DataRetentionPolicy,
    DeploymentCompatibilityPolicy,
    DeploymentComponentResult,
    DeploymentNodeResult,
    DeploymentOperation,
    DeploymentPhaseResult,
    DeploymentPlan,
    DeploymentPlanChange,
    DeploymentRevision,
    NodeEndpointRecord,
    NodeInventoryRecord,
    ProjectDeployment,
    ProjectDeploymentContractError,
    RolloutPolicy,
    inventory_revision,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_NOW = "2026-08-19T18:00:00+00:00"
_SCHEMA_NAMES = (
    "node.inventory.v1.schema.json",
    "project.deployment.v1.schema.json",
    "project.deployment-revision.v1.schema.json",
    "project.deployment-plan.v1.schema.json",
    "project.component-activation.v1.schema.json",
    "project.deployment-operation.v1.schema.json",
)


def _schemas() -> dict[str, dict[str, Any]]:
    root = Path(__file__).parents[1] / "src" / "adaos" / "abi"
    return {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in _SCHEMA_NAMES
    }


def _validate(name: str, payload: dict[str, Any]) -> None:
    schemas = _schemas()
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    jsonschema.Draft202012Validator(
        schemas[name],
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    ).validate(payload)


def _endpoint(
    endpoint_id: str = "display-1", role: str = "display"
) -> NodeEndpointRecord:
    return NodeEndpointRecord(
        endpoint_id=endpoint_id,
        role=role,
        available=True,
        capabilities=("media.playback",),
        labels={"room": "living-room"},
        capacity={"streams": 1},
    )


def _node(node_id: str = "node-a", *, endpoint: bool = True) -> NodeInventoryRecord:
    return NodeInventoryRecord(
        node_id=node_id,
        subnet_id="home",
        trust_state="trusted",
        online=True,
        architecture="x86_64",
        runtime_version="0.1.900",
        capabilities=("project.activate", "media.catalog"),
        protocols={"project_activation": "1"},
        labels={"zone": "living-room"},
        capacity={"cpu_millicores": 2000, "memory_bytes": 4_000_000_000},
        endpoints=(_endpoint(),) if endpoint else (),
        observed_at=_NOW,
        revision=3,
    )


def _deployment(*, retention: DataRetentionPolicy | None = None) -> ProjectDeployment:
    return ProjectDeployment(
        deployment_id="media-center-home",
        project_ref="project:media_center",
        release_digest=_DIGEST_A,
        subnet_id="home",
        revision=2,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:media_center_coordinator",
                mode="singleton",
                required_capabilities=("project.activate",),
            ),
            ComponentPlacementPolicy(
                component_ref="skill:media_library_agent",
                mode="selected_nodes",
                selected_node_ids=("node-a",),
                required_capabilities=("media.catalog",),
            ),
            ComponentPlacementPolicy(
                component_ref="scenario:media_center",
                mode="co_located_with",
                co_located_with="skill:media_center_coordinator",
            ),
        ),
        compatibility=DeploymentCompatibilityPolicy(
            architectures=("x86_64",),
            minimum_runtime_version="0.1.850",
            required_protocols={"project_activation": "1"},
        ),
        rollout=RolloutPolicy(batch_size=1, max_unavailable=1, pause_seconds=0),
        retention=retention or DataRetentionPolicy(),
        status="planned",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        plan_id="plan-2",
        deployment_id="media-center-home",
        expected_revision=2,
        release_digest=_DIGEST_A,
        inventory_revision=inventory_revision((_node(),)),
        changes=(
            DeploymentPlanChange(
                action="install",
                component_ref="skill:media_library_agent",
                node_id="node-a",
                target_package_digest=_DIGEST_B,
                reason="missing activation",
                phases=("fetch", "verify", "stage", "activate", "health", "commit"),
            ),
        ),
        required_approvals=("remote_install",),
        created_at=_NOW,
    )


def _operation() -> DeploymentOperation:
    phase = DeploymentPhaseResult(
        phase="verify",
        state="succeeded",
        attempt=1,
        idempotency_key="operation-2:node-a:verify",
        receipt={"package_digest": _DIGEST_B},
        started_at=_NOW,
        finished_at=_NOW,
    )
    return DeploymentOperation(
        operation_id="operation-2",
        deployment_id="media-center-home",
        plan_digest=_plan().to_dict()["plan_digest"],
        kind="apply",
        state="succeeded",
        expected_revision=2,
        idempotency_key="apply:media-center-home:2",
        node_results=(
            DeploymentNodeResult(
                node_id="node-a",
                state="succeeded",
                components=(
                    DeploymentComponentResult(
                        component_ref="skill:media_library_agent",
                        action="install",
                        state="succeeded",
                        phases=(phase,),
                        activation_ref="activation:media-center-home:node-a:agent",
                    ),
                ),
            ),
        ),
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_versioned_contracts_round_trip_and_validate_against_json_schema() -> None:
    deployment = _deployment()
    revision = DeploymentRevision(
        deployment_id=deployment.deployment_id,
        revision=deployment.revision,
        desired=deployment,
        actor_ref="user:owner",
        reason="place catalog agent on storage node",
        created_at=_NOW,
    )
    plan = _plan()
    activation = ComponentActivation(
        activation_id="activation-2-agent-node-a",
        deployment_id=deployment.deployment_id,
        component_ref="skill:media_library_agent",
        node_id="node-a",
        release_digest=_DIGEST_A,
        package_digest=_DIGEST_B,
        generation=2,
        status="active",
        health={"ready": True},
        evidence={"package_verified": True},
        created_at=_NOW,
        updated_at=_NOW,
    )
    records = (
        ("node.inventory.v1.schema.json", _node(), NodeInventoryRecord),
        ("project.deployment.v1.schema.json", deployment, ProjectDeployment),
        ("project.deployment-revision.v1.schema.json", revision, DeploymentRevision),
        ("project.deployment-plan.v1.schema.json", plan, DeploymentPlan),
        (
            "project.component-activation.v1.schema.json",
            activation,
            ComponentActivation,
        ),
        (
            "project.deployment-operation.v1.schema.json",
            _operation(),
            DeploymentOperation,
        ),
    )

    for schema_name, record, record_type in records:
        payload = record.to_dict()
        _validate(schema_name, payload)
        assert record_type.from_mapping(payload) == record


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (NodeInventoryRecord.from_mapping, lambda: _node().to_dict()),
        (ProjectDeployment.from_mapping, lambda: _deployment().to_dict()),
        (
            DeploymentRevision.from_mapping,
            lambda: DeploymentRevision(
                deployment_id="media-center-home",
                revision=2,
                desired=_deployment(),
                actor_ref="user:owner",
                reason="test",
                created_at=_NOW,
            ).to_dict(),
        ),
        (DeploymentPlan.from_mapping, lambda: _plan().to_dict()),
        (DeploymentOperation.from_mapping, lambda: _operation().to_dict()),
    ],
)
def test_contracts_reject_unknown_fields(factory: Any, payload: Any) -> None:
    value = payload()
    value["unexpected"] = True

    with pytest.raises(ProjectDeploymentContractError, match="unsupported fields"):
        factory(value)


def test_revision_and_plan_digests_detect_tampering() -> None:
    deployment = _deployment()
    revision = DeploymentRevision(
        deployment_id=deployment.deployment_id,
        revision=deployment.revision,
        desired=deployment,
        actor_ref="user:owner",
        reason="test",
        created_at=_NOW,
    ).to_dict()
    revision["desired"]["status"] = "active"

    with pytest.raises(
        ProjectDeploymentContractError, match="desired_digest does not match"
    ):
        DeploymentRevision.from_mapping(revision)

    plan = _plan().to_dict()
    plan["changes"][0]["reason"] = "tampered"
    with pytest.raises(
        ProjectDeploymentContractError, match="plan_digest does not match"
    ):
        DeploymentPlan.from_mapping(plan)


def test_placement_and_retention_invariants_fail_closed() -> None:
    with pytest.raises(
        ProjectDeploymentContractError, match="requires selected_node_ids"
    ):
        ComponentPlacementPolicy(
            component_ref="skill:media_library_agent",
            mode="selected_nodes",
        )

    with pytest.raises(ProjectDeploymentContractError, match="declared placement"):
        ProjectDeployment(
            deployment_id="bad",
            project_ref="project:media_center",
            release_digest=_DIGEST_A,
            subnet_id="home",
            revision=1,
            placements=(
                ComponentPlacementPolicy(
                    component_ref="scenario:media_center",
                    mode="co_located_with",
                    co_located_with="skill:missing",
                ),
            ),
        )

    with pytest.raises(ProjectDeploymentContractError, match="external_data retention"):
        DataRetentionPolicy(external_data="delete")


def test_operation_phases_preserve_order_and_uncertain_state_is_explicit() -> None:
    change = DeploymentPlanChange(
        action="install",
        component_ref="skill:media_library_agent",
        node_id="node-a",
        target_package_digest=_DIGEST_A,
        phases=("fetch", "verify", "stage", "fetch", "commit"),
    )
    assert change.phases == ("fetch", "verify", "stage", "commit")

    with pytest.raises(ProjectDeploymentContractError, match="uncertain node results"):
        DeploymentNodeResult(
            node_id="node-a",
            state="succeeded",
            components=(),
            uncertain=True,
        )


def test_node_inventory_revision_is_order_independent_and_strict() -> None:
    node_a = _node("node-a")
    node_b = _node("node-b", endpoint=False)

    assert inventory_revision((node_a, node_b)) == inventory_revision((node_b, node_a))
    heartbeat = replace(
        node_a,
        observed_at="2026-08-20T12:00:00+00:00",
        revision=node_a.revision + 1,
    )
    changed_capabilities = replace(
        heartbeat,
        capabilities=(*heartbeat.capabilities, "media.transcode"),
    )

    assert inventory_revision((node_a,)) == inventory_revision((heartbeat,))
    assert inventory_revision((node_a,)) != inventory_revision(
        (changed_capabilities,)
    )
    assert inventory_revision((node_a,)) != inventory_revision(
        (replace(heartbeat, online=False),)
    )

    with pytest.raises(
        ProjectDeploymentContractError, match="online must be a boolean"
    ):
        NodeInventoryRecord(
            node_id="node-a",
            subnet_id="home",
            trust_state="trusted",
            online="yes",  # type: ignore[arg-type]
            architecture="x86_64",
            runtime_version="0.1.900",
            observed_at=_NOW,
        )
