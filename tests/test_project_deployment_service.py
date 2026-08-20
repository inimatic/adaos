from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
)
from adaos.domain.project_deployment import (
    ComponentActivation,
    ComponentPlacementPolicy,
    DeploymentCompatibilityPolicy,
    NodeEndpointRecord,
    NodeInventoryRecord,
    ProjectDeployment,
    RolloutPolicy,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.project_deployment import (
    DeploymentPrincipal,
    ProjectDeploymentConflictError,
    ProjectDeploymentExecutionError,
    ProjectDeploymentExecutor,
    ProjectDeploymentPlanner,
    ProjectDeploymentRuntime,
    ProjectDeploymentStore,
    RetryableDeploymentPhaseError,
    SnapshotNodeInventoryProvider,
    UncertainDeploymentPhaseError,
)


_NOW = "2026-08-19T18:00:00+00:00"
_SOURCE = ArtifactSourceRef(
    forge="github",
    repository="inimatic/adaos-registry",
    revision="0123456789abcdef0123456789abcdef01234567",
    path_scope=("projects/media_center/",),
)
_COMPONENTS = (
    ("skill", "media_center_coordinator", "a"),
    ("skill", "media_library_agent", "b"),
    ("skill", "media_recommendation_worker", "c"),
    ("skill", "media_endpoint_agent", "d"),
    ("scenario", "media_center", "e"),
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _release(
    component_specs: tuple[tuple[str, str, str], ...] = _COMPONENTS,
) -> ReleasePlan:
    packages = tuple(
        ArtifactPackageRef(
            kind=kind,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            version="1.0.0",
            digest=_digest(character),
            manifest_digest=_digest("f"),
            source_ref=_SOURCE,
        )
        for kind, artifact_id, character in component_specs
    )
    release = ProjectRelease(
        project_id="media_center",
        version="1.0.0",
        source_ref=_SOURCE,
        components=packages,
    ).seal()
    return ReleasePlan(
        release=release,
        packages=packages,
        bindings=(),
        reverse_consumers={},
    )


def _endpoint(endpoint_id: str = "display-1") -> NodeEndpointRecord:
    return NodeEndpointRecord(
        endpoint_id=endpoint_id,
        role="display",
        available=True,
        capabilities=("media.playback",),
        capacity={"streams": 1},
    )


def _node(
    node_id: str,
    *,
    trusted: bool = True,
    endpoint: bool = False,
    cpu: int = 4000,
) -> NodeInventoryRecord:
    return NodeInventoryRecord(
        node_id=node_id,
        subnet_id="home",
        trust_state="trusted" if trusted else "pending",
        online=True,
        architecture="x86_64",
        runtime_version="0.1.900",
        capabilities=("media.catalog", "project.activate"),
        protocols={"project_activation": "1"},
        labels={"site": "home"},
        capacity={"cpu_millicores": cpu},
        endpoints=(_endpoint(),) if endpoint else (),
        observed_at=_NOW,
        revision=1,
    )


def _deployment(release_plan: ReleasePlan, *, revision: int = 1) -> ProjectDeployment:
    return ProjectDeployment(
        deployment_id="media-center-home",
        project_ref="project:media_center",
        release_digest=str(release_plan.release.release_digest),
        subnet_id="home",
        revision=revision,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:media_center_coordinator",
                mode="singleton",
                required_capabilities=("project.activate",),
                required_capacity={"cpu_millicores": 500},
            ),
            ComponentPlacementPolicy(
                component_ref="skill:media_library_agent",
                mode="selected_nodes",
                selected_node_ids=("node-b",),
                required_capabilities=("media.catalog",),
                required_capacity={"cpu_millicores": 1000},
            ),
            ComponentPlacementPolicy(
                component_ref="skill:media_recommendation_worker",
                mode="all_matching",
                min_instances=2,
                max_instances=2,
                required_capacity={"cpu_millicores": 500},
            ),
            ComponentPlacementPolicy(
                component_ref="skill:media_endpoint_agent",
                mode="per_endpoint",
                endpoint_role="display",
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
        rollout=RolloutPolicy(
            batch_size=2,
            max_unavailable=1,
            pause_seconds=0,
            stop_on_failure=True,
            rollback_on_failure=True,
        ),
        status="planned",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _principal(plan_approvals: tuple[str, ...]) -> DeploymentPrincipal:
    return DeploymentPrincipal.create(
        actor_ref="user:owner",
        permissions=(
            "project.deployment.apply",
            "project.deployment.reconcile",
            "project.component.install.remote",
            "project.component.drain",
            "project.component.remove",
            "project.data.runtime.delete",
            "project.data.derived.delete",
        ),
        approvals=plan_approvals,
    )


class FakeDeploymentAdapter:
    def __init__(
        self,
        *,
        retry_phase_once: str | None = None,
        uncertain_phase: str | None = None,
    ) -> None:
        self.retry_phase_once = retry_phase_once
        self.uncertain_phase = uncertain_phase
        self.calls: list[dict[str, Any]] = []
        self._retried: set[str] = set()

    def execute_phase(self, **kwargs: Any) -> Mapping[str, Any]:
        phase = str(kwargs["phase"])
        key = str(kwargs["idempotency_key"])
        self.calls.append(
            {
                "phase": phase,
                "key": key,
                "attempt": int(kwargs["attempt"]),
                "component_ref": kwargs["change"].component_ref,
                "node_id": kwargs["node"].node_id,
                "package_digest": getattr(kwargs.get("package"), "digest", None),
            }
        )
        if phase == self.uncertain_phase:
            raise UncertainDeploymentPhaseError(
                "transport disconnected after dispatch",
                details={"reconcile": True},
            )
        if phase == self.retry_phase_once and key not in self._retried:
            self._retried.add(key)
            raise RetryableDeploymentPhaseError("temporary node pressure")
        if phase == "health":
            return {"ready": True, "credential_token": "must-not-leak"}
        return {"witness": f"{phase}:ok"}


class StaticReleaseProvider:
    def __init__(self, release_plan: ReleasePlan) -> None:
        self.release_plan = release_plan

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        assert project_id == self.release_plan.release.project_id
        assert release_digest == self.release_plan.release.release_digest
        return self.release_plan


class StaticInventoryProvider:
    def __init__(self, records: tuple[NodeInventoryRecord, ...]) -> None:
        self.records = records

    def list_nodes(self, subnet_id: str) -> tuple[NodeInventoryRecord, ...]:
        return tuple(item for item in self.records if item.subnet_id == subnet_id)


def test_store_uses_compare_and_switch_and_immutable_revisions(tmp_path: Path) -> None:
    release_plan = _release()
    first = _deployment(release_plan)
    store = ProjectDeploymentStore(state_dir=tmp_path)

    revision_1 = store.save_deployment(
        first,
        expected_revision=0,
        actor_ref="user:owner",
        reason="initial topology",
    )
    with pytest.raises(ProjectDeploymentConflictError, match="expected 0, observed 1"):
        store.save_deployment(
            first,
            expected_revision=0,
            actor_ref="user:owner",
            reason="stale write",
        )

    second = replace(first, revision=2, updated_at="2026-08-19T18:05:00+00:00")
    revision_2 = store.save_deployment(
        second,
        expected_revision=1,
        actor_ref="user:owner",
        reason="advance topology",
    )

    assert store.get_deployment(first.deployment_id) == second
    assert revision_2.previous_desired_digest == revision_1.desired_digest
    records, cursor = store.list_deployments(limit=1)
    assert records == (second,)
    assert cursor is None


def test_planner_resolves_all_placement_modes_and_exact_packages() -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (
        _node("node-a", endpoint=True),
        _node("node-b"),
        _node("node-c", trusted=False),
    )

    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    targets = {(item.component_ref, item.node_id): item for item in plan.changes}

    assert plan.status == "ready"
    assert ("skill:media_center_coordinator", "node-a") in targets
    assert ("scenario:media_center", "node-a") in targets
    assert ("skill:media_library_agent", "node-b") in targets
    assert ("skill:media_endpoint_agent", "node-a") in targets
    recommendation_nodes = {
        node_id
        for (component_ref, node_id) in targets
        if component_ref == "skill:media_recommendation_worker"
    }
    assert recommendation_nodes == {"node-a", "node-b"}
    assert plan.required_approvals == ("remote_install",)
    for change in plan.changes:
        package = next(
            item
            for item in release_plan.release.components
            if item.key == change.component_ref
        )
        assert change.target_package_digest == package.digest


def test_planner_reports_capacity_and_trust_blocks_without_mutation() -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (
        _node("node-a", endpoint=True, cpu=400),
        _node("node-b", trusted=False),
    )

    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )

    assert plan.status == "blocked"
    assert any("capacity_insufficient:cpu_millicores" in item for item in plan.warnings)
    assert any("node_untrusted" in item for item in plan.warnings)


def test_planner_emits_noop_update_and_explicit_remove() -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (_node("node-a", endpoint=True), _node("node-b"))
    initial = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    coordinator_change = next(
        item
        for item in initial.changes
        if item.component_ref == "skill:media_center_coordinator"
    )
    activation = ComponentActivation(
        activation_id="coordinator-current",
        deployment_id=desired.deployment_id,
        component_ref=coordinator_change.component_ref,
        node_id=coordinator_change.node_id,
        release_digest=desired.release_digest,
        package_digest=str(coordinator_change.target_package_digest),
        generation=1,
        status="active",
        created_at=_NOW,
        updated_at=_NOW,
    )
    stale = ComponentActivation(
        activation_id="stale-worker",
        deployment_id=desired.deployment_id,
        component_ref="skill:obsolete_worker",
        node_id="node-b",
        release_digest=desired.release_digest,
        package_digest=_digest("9"),
        generation=1,
        status="active",
        created_at=_NOW,
        updated_at=_NOW,
    )

    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        activations=(activation, stale),
        local_node_id="node-a",
    )

    noop = next(
        item for item in plan.changes if item.component_ref == activation.component_ref
    )
    assert noop.action == "noop"
    assert noop.phases == ("observe",)
    removal = next(
        item for item in plan.changes if item.component_ref == stale.component_ref
    )
    assert removal.action == "remove"
    assert removal.phases == ("cordon", "drain", "deactivate", "remove")
    assert "component_remove" in plan.required_approvals


def test_executor_journals_multi_component_nodes_retries_and_is_idempotent(
    tmp_path: Path,
) -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (_node("node-a", endpoint=True), _node("node-b"))
    store = ProjectDeploymentStore(state_dir=tmp_path)
    store.save_deployment(
        desired,
        expected_revision=0,
        actor_ref="user:owner",
        reason="initial topology",
    )
    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    adapter = FakeDeploymentAdapter(retry_phase_once="verify")
    executor = ProjectDeploymentExecutor(store=store, adapter=adapter)

    operation = executor.execute(
        plan,
        desired=desired,
        release_plan=release_plan,
        inventory=inventory,
        principal=_principal(plan.required_approvals),
        idempotency_key="apply:media-center-home:1",
    )
    call_count = len(adapter.calls)
    duplicate = executor.execute(
        plan,
        desired=desired,
        release_plan=release_plan,
        inventory=inventory,
        principal=_principal(plan.required_approvals),
        idempotency_key="apply:media-center-home:1",
    )

    assert operation.state == "succeeded"
    assert duplicate.operation_id == operation.operation_id
    assert len(adapter.calls) == call_count
    assert any(
        item["attempt"] == 2 for item in adapter.calls if item["phase"] == "verify"
    )
    node_a = next(item for item in operation.node_results if item.node_id == "node-a")
    assert len(node_a.components) >= 3
    activations, cursor = store.list_activations(
        deployment_id=desired.deployment_id, limit=20
    )
    assert cursor is None
    assert len(activations) == len(plan.changes)
    assert all(item.status == "active" for item in activations)
    assert all(
        item.health.get("credential_token") == "<redacted>" for item in activations
    )


def test_executor_does_not_retry_or_rollback_uncertain_state(tmp_path: Path) -> None:
    release_plan = _release((("skill", "media_center_coordinator", "a"),))
    desired = ProjectDeployment(
        deployment_id="media-center-home",
        project_ref="project:media_center",
        release_digest=str(release_plan.release.release_digest),
        subnet_id="home",
        revision=1,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:media_center_coordinator",
                mode="singleton",
            ),
        ),
        compatibility=DeploymentCompatibilityPolicy(
            architectures=("x86_64",),
            required_protocols={"project_activation": "1"},
        ),
        status="planned",
        created_at=_NOW,
        updated_at=_NOW,
    )
    inventory = (_node("node-a"),)
    store = ProjectDeploymentStore(state_dir=tmp_path)
    store.save_deployment(
        desired,
        expected_revision=0,
        actor_ref="user:owner",
        reason="initial topology",
    )
    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    adapter = FakeDeploymentAdapter(uncertain_phase="activate")

    operation = ProjectDeploymentExecutor(store=store, adapter=adapter).execute(
        plan,
        desired=desired,
        release_plan=release_plan,
        inventory=inventory,
        principal=_principal(plan.required_approvals),
        idempotency_key="apply:uncertain:1",
    )

    assert operation.state == "uncertain"
    assert operation.uncertain is True
    assert [item["phase"] for item in adapter.calls].count("activate") == 1
    assert "rollback" not in [item["phase"] for item in adapter.calls]


def test_executor_rejects_inventory_drift_after_review(tmp_path: Path) -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (_node("node-a", endpoint=True), _node("node-b"))
    store = ProjectDeploymentStore(state_dir=tmp_path)
    store.save_deployment(
        desired,
        expected_revision=0,
        actor_ref="user:owner",
        reason="initial topology",
    )
    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    changed_inventory = (
        replace(
            inventory[0],
            capabilities=(*inventory[0].capabilities, "media.transcode"),
            revision=2,
        ),
        inventory[1],
    )

    with pytest.raises(ProjectDeploymentExecutionError, match="inventory changed"):
        ProjectDeploymentExecutor(
            store=store,
            adapter=FakeDeploymentAdapter(),
        ).execute(
            plan,
            desired=desired,
            release_plan=release_plan,
            inventory=changed_inventory,
            principal=_principal(plan.required_approvals),
            idempotency_key="apply:stale-inventory:1",
        )


def test_executor_accepts_heartbeat_after_review(tmp_path: Path) -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    inventory = (_node("node-a", endpoint=True), _node("node-b"))
    store = ProjectDeploymentStore(state_dir=tmp_path)
    store.save_deployment(
        desired,
        expected_revision=0,
        actor_ref="user:owner",
        reason="initial topology",
    )
    plan = ProjectDeploymentPlanner().plan(
        desired,
        release_plan=release_plan,
        inventory=inventory,
        local_node_id="node-a",
    )
    heartbeat_inventory = (
        replace(
            inventory[0],
            observed_at="2026-08-20T12:00:00+00:00",
            revision=2,
        ),
        inventory[1],
    )

    operation = ProjectDeploymentExecutor(
        store=store,
        adapter=FakeDeploymentAdapter(),
    ).execute(
        plan,
        desired=desired,
        release_plan=release_plan,
        inventory=heartbeat_inventory,
        principal=_principal(plan.required_approvals),
        idempotency_key="apply:heartbeat-inventory:1",
    )

    assert operation.state == "succeeded"
    assert operation.uncertain is False


def test_runtime_exposes_plan_apply_inspect_drain_and_remove(tmp_path: Path) -> None:
    release_plan = _release((("skill", "media_center_coordinator", "a"),))
    desired = ProjectDeployment(
        deployment_id="media-center-home",
        project_ref="project:media_center",
        release_digest=str(release_plan.release.release_digest),
        subnet_id="home",
        revision=1,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:media_center_coordinator",
                mode="singleton",
            ),
        ),
        compatibility=DeploymentCompatibilityPolicy(
            required_protocols={"project_activation": "1"}
        ),
        status="planned",
        created_at=_NOW,
        updated_at=_NOW,
    )
    inventory = (_node("node-a"),)
    store = ProjectDeploymentStore(state_dir=tmp_path)
    adapter = FakeDeploymentAdapter()
    published: list[Mapping[str, Any]] = []
    runtime = ProjectDeploymentRuntime(
        store=store,
        releases=StaticReleaseProvider(release_plan),
        inventory=StaticInventoryProvider(inventory),
        adapter=adapter,
        local_node_id="node-a",
        projection_publisher=published.append,
    )
    principal = DeploymentPrincipal.create(
        actor_ref="skill:deployment_test",
        permissions=(
            "project.deployment.manage",
            "project.deployment.inspect",
            "project.deployment.apply",
            "project.deployment.reconcile",
            "project.component.drain",
            "project.component.remove",
        ),
        approvals=("component_drain", "component_remove"),
    )

    runtime.define(
        desired,
        expected_revision=0,
        principal=principal,
        reason="runtime fixture",
    )
    plan = runtime.plan(desired.deployment_id, principal=principal)
    applied = runtime.apply(
        str(plan.plan_digest),
        principal=principal,
        idempotency_key="runtime:apply:1",
    )
    inspection = runtime.inspect(desired.deployment_id, principal=principal)
    activation = inspection.activations[0]
    drained = runtime.drain(
        activation.activation_id,
        principal=principal,
        idempotency_key="runtime:drain:1",
    )
    removed = runtime.remove(
        activation.activation_id,
        principal=principal,
        idempotency_key="runtime:remove:1",
    )

    assert applied.state == "succeeded"
    assert inspection.desired == desired
    assert drained.state == "succeeded"
    assert removed.state == "succeeded"
    lifecycle_calls = [
        item
        for item in adapter.calls
        if item["key"].startswith(("runtime:drain:1", "runtime:remove:1"))
    ]
    assert lifecycle_calls
    assert all(item["package_digest"] is None for item in lifecycle_calls)
    assert store.get_activation(activation.activation_id).status == "removed"
    assert published[-1]["schema"] == "adaos.project.deployment_projection.v1"
    assert published[-1]["items"][0]["observed"]["operation_total"] == 3


def test_subnet_snapshot_inventory_requires_explicit_deployment_capabilities() -> None:
    snapshot = {
        "members": [
            {
                "node_id": "node-b",
                "connected": True,
                "node_snapshot": {
                    "node_id": "node-b",
                    "subnet_id": "home",
                    "captured_at": 1_787_165_000.0,
                    "ready": True,
                    "role": "member",
                    "build": {"runtime_version": "0.1.900"},
                    "environment": {"architecture": "aarch64"},
                    "deployment": {
                        "capabilities": ["project.activate", "media.catalog"],
                        "protocols": {"project_activation": "1"},
                        "capacity": {"cpu_millicores": 1500},
                        "labels": {"storage": "music"},
                        "endpoints": [
                            {
                                "endpoint_id": "tv-1",
                                "role": "display",
                                "available": True,
                                "capabilities": ["media.playback"],
                            }
                        ],
                    },
                },
            }
        ]
    }

    records = SnapshotNodeInventoryProvider(lambda: snapshot).list_nodes("home")

    assert len(records) == 1
    assert records[0].trust_state == "trusted"
    assert records[0].architecture == "aarch64"
    assert records[0].capabilities == ("media.catalog", "project.activate")
    assert records[0].capacity["cpu_millicores"] == 1500
    assert records[0].endpoints[0].role == "display"


def test_placement_recommendations_are_bounded_explainable_and_read_only() -> None:
    release_plan = _release()
    desired = _deployment(release_plan)
    placement = next(
        item
        for item in desired.placements
        if item.component_ref == "skill:media_center_coordinator"
    )

    result = ProjectDeploymentPlanner().recommend_nodes(
        desired,
        placement,
        inventory=(
            _node("node-a", cpu=8000),
            _node("node-b", cpu=4000),
            _node("node-untrusted", trusted=False),
        ),
        limit=2,
    )

    assert result["dry_run"] is True
    assert [item["node_id"] for item in result["candidates"]] == [
        "node-a",
        "node-b",
    ]
    assert result["candidates"][0]["headroom"]["cpu_millicores"] == 7500
    assert result["rejected"] == [
        {"node_id": "node-untrusted", "reason": "node_untrusted"}
    ]
