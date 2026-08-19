from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
)
from adaos.domain.distributed_runtime import (
    Dataset,
    Partition,
    Replica,
    ServiceDefinition,
    ServiceEndpoint,
    ServiceGroup,
    ServiceInstance,
    TransferRecord,
)
from adaos.domain.project_deployment import (
    ComponentActivation,
    ComponentPlacementPolicy,
    NodeInventoryRecord,
    ProjectDeployment,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.distributed_runtime import (
    BoundedTransferController,
    DistributedPrincipal,
    DistributedRuntime,
    DistributedRuntimeError,
    DistributedRuntimeStore,
    RetryableTopologyPhaseError,
    StaleAuthorityEpochError,
    TransferChunk,
    UncertainTopologyPhaseError,
)
from adaos.services.project_deployment import ProjectDeploymentStore


_NOW = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
_DIGEST = "sha256:" + "a" * 64
_SOURCE = ArtifactSourceRef(
    forge="github",
    repository="inimatic/adaos-registry",
    revision="0123456789abcdef0123456789abcdef01234567",
    path_scope=("projects/media_center/",),
)


class Clock:
    def __init__(self) -> None:
        self.value = _NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _release() -> ReleasePlan:
    package = ArtifactPackageRef(
        kind="skill",
        artifact_id="media_library_agent",
        version="1.0.0",
        digest="sha256:" + "b" * 64,
        manifest_digest="sha256:" + "c" * 64,
        source_ref=_SOURCE,
    )
    release = ProjectRelease(
        project_id="media_center",
        version="1.0.0",
        source_ref=_SOURCE,
        components=(package,),
    ).seal()
    return ReleasePlan(
        release=release,
        packages=(package,),
        bindings=(),
        reverse_consumers={},
    )


class ReleaseProvider:
    def __init__(self, release: ReleasePlan) -> None:
        self.release = release

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        assert project_id == "media_center"
        assert release_digest == self.release.release.release_digest
        return self.release


class InventoryProvider:
    def __init__(self, nodes: tuple[NodeInventoryRecord, ...]) -> None:
        self.nodes = nodes

    def list_nodes(self, subnet_id: str) -> tuple[NodeInventoryRecord, ...]:
        return tuple(item for item in self.nodes if item.subnet_id == subnet_id)


def _node(node_id: str, *, protocol: str = "1") -> NodeInventoryRecord:
    return NodeInventoryRecord(
        node_id=node_id,
        subnet_id="home",
        trust_state="trusted",
        online=True,
        architecture="x86_64",
        runtime_version="0.1.900",
        capabilities=("media.catalog", "project.activate"),
        protocols={"project_activation": "1", "distributed_runtime": protocol},
        labels={"failure_domain": node_id},
        capacity={"cpu_millicores": 4000, "storage_bytes": 10_000_000_000},
        observed_at=_NOW.isoformat(),
        revision=1,
    )


def _principal() -> DistributedPrincipal:
    permissions = {
        "distributed.service.manage",
        "distributed.service.register",
        "distributed.service.renew",
        "distributed.service.reconcile",
        "distributed.service.drain",
        "distributed.topology.manage",
        "distributed.topology.inspect",
        "distributed.topology.plan",
        "distributed.topology.apply",
        "distributed.replica.observe",
        "distributed.route.grant",
        "distributed.authority.handoff",
        "distributed.replica.remove",
        "distributed.data.delete",
        "scope:media.read",
    }
    return DistributedPrincipal.create(
        actor_ref="skill:media_center_coordinator",
        permissions=permissions,
        approvals=("authority_handoff", "replica_remove", "replica_data_delete"),
    )


def _deployment(release: ReleasePlan) -> ProjectDeployment:
    return ProjectDeployment(
        deployment_id="media-center-home",
        project_ref="project:media_center",
        release_digest=str(release.release.release_digest),
        subnet_id="home",
        revision=1,
        placements=(
            ComponentPlacementPolicy(
                component_ref="skill:media_library_agent",
                mode="selected_nodes",
                selected_node_ids=("node-a", "node-b"),
            ),
        ),
        status="active",
        created_at=_NOW.isoformat(),
        updated_at=_NOW.isoformat(),
    )


def _activation(
    release: ReleasePlan, node_id: str, generation: int = 1
) -> ComponentActivation:
    package = release.packages[0]
    return ComponentActivation(
        activation_id=f"activation-agent-{node_id}",
        deployment_id="media-center-home",
        component_ref="skill:media_library_agent",
        node_id=node_id,
        release_digest=str(release.release.release_digest),
        package_digest=package.digest,
        generation=generation,
        status="active",
        health={"ready": True},
        evidence={"package_verified": True},
        created_at=_NOW.isoformat(),
        updated_at=_NOW.isoformat(),
    )


def _instance(release: ReleasePlan, node_id: str) -> ServiceInstance:
    return ServiceInstance(
        instance_id=f"media-agent-{node_id}",
        group_id="media-library-home",
        node_id=node_id,
        activation_id=f"activation-agent-{node_id}",
        release_digest=str(release.release.release_digest),
        component_ref="skill:media_library_agent",
        runtime_generation=1,
        protocol_version="1",
        topology_generation=1,
        lease_id="registration-request",
        status="ready",
        readiness=True,
        health={"status": "passing"},
        pressure={"queue_depth": 0},
        capabilities=("media.catalog",),
        endpoints=(
            ServiceEndpoint(
                endpoint_id="catalog",
                protocol="adaos.skill.v1",
                address_ref=f"skill://{node_id}/media_library_agent/catalog",
                scopes=("media.read",),
            ),
        ),
        observed_at=_NOW.isoformat(),
    )


def _runtime(tmp_path: Path) -> tuple[DistributedRuntime, Clock, ReleasePlan]:
    release = _release()
    deployment_store = ProjectDeploymentStore(state_dir=tmp_path)
    deployment_store.save_deployment(
        _deployment(release),
        expected_revision=0,
        actor_ref="user:owner",
        reason="test",
    )
    for node_id in ("node-a", "node-b"):
        deployment_store.put_activation(_activation(release, node_id))
    clock = Clock()
    runtime = DistributedRuntime(
        store=DistributedRuntimeStore(state_dir=tmp_path),
        deployment_store=deployment_store,
        releases=ReleaseProvider(release),
        inventory=InventoryProvider((_node("node-a"), _node("node-b"))),
        clock=clock,
    )
    runtime.define_service(
        ServiceDefinition(
            definition_id="media-library-agent",
            version="1",
            release_digest=str(release.release.release_digest),
            compatible_components=("skill:media_library_agent",),
            provided_contracts=("media.catalog.v1",),
            topology_mode="multi_instance",
            protocol_version="1",
            required_capabilities=("media.catalog",),
            adapter_contracts=("adaos.distributed.adapter.v1",),
        ),
        principal=_principal(),
    )
    runtime.define_group(
        ServiceGroup(
            group_id="media-library-home",
            definition_id="media-library-agent",
            definition_version="1",
            desired_generation=1,
            desired_instances=2,
            authority_policy="singleton_fenced",
            placement={
                "max_instances_per_node": 1,
                "anti_affinity_label": "failure_domain",
            },
            linked_datasets=("media-files", "media-catalog"),
            route_policy={"max_staleness_seconds": 30},
            desired_revision=1,
        ),
        expected_revision=0,
        principal=_principal(),
    )
    return runtime, clock, release


def _register_both(runtime: DistributedRuntime, release: ReleasePlan) -> None:
    for node_id in ("node-a", "node-b"):
        runtime.register_instance(
            _instance(release, node_id),
            expected_revision=0,
            principal=_principal(),
        )


def _external_topology(runtime: DistributedRuntime) -> None:
    runtime.define_dataset(
        Dataset(
            dataset_id="media-files",
            owner_ref="skill:media_library_agent",
            contract="media.files.v1",
            consistency_profile="external_authority",
            partition_scheme={"kind": "source_root"},
            retention={"on_remove": "retain"},
            data_class="external",
            desired_revision=1,
        ),
        expected_revision=0,
        principal=_principal(),
    )
    runtime.put_partition(
        Partition(
            partition_id="media-files:root-a",
            dataset_id="media-files",
            selector={"root_id": "root-a"},
            desired_replicas=1,
            topology_generation=1,
            authority_lease_id=None,
            authority_epoch=0,
            checkpoint="scan:10",
            status="ready",
        ),
        expected_revision=0,
        principal=_principal(),
    )


def test_registration_is_bound_to_activation_release_protocol_and_capacity(
    tmp_path: Path,
) -> None:
    runtime, _, release = _runtime(tmp_path)
    registered = runtime.register_instance(
        _instance(release, "node-a"),
        expected_revision=0,
        principal=_principal(),
    )
    assert registered.lease_id != "registration-request"
    assert runtime.store.get_lease(registered.lease_id).epoch == 0

    incompatible_runtime, _, incompatible_release = _runtime(tmp_path / "bad")
    incompatible_runtime.inventory = InventoryProvider((_node("node-a", protocol="2"),))
    with pytest.raises(DistributedRuntimeError, match="protocol_mismatch"):
        incompatible_runtime.register_instance(
            _instance(incompatible_release, "node-a"),
            expected_revision=0,
            principal=_principal(),
        )


def test_membership_expiry_is_independent_from_last_health(
    tmp_path: Path,
) -> None:
    runtime, clock, release = _runtime(tmp_path)
    registered = runtime.register_instance(
        _instance(release, "node-a"),
        expected_revision=0,
        lease_seconds=30,
        principal=_principal(),
    )
    assert registered.health["status"] == "passing"
    clock.advance(31)
    expired = runtime.expire_leases(principal=_principal())
    observed = runtime.store.get_instance(registered.instance_id)
    assert registered.lease_id in expired
    assert observed.status == "expired"
    assert observed.readiness is False
    assert observed.health["status"] == "passing"


def test_fenced_handoff_rejects_old_owner_and_routes_partial_topology(
    tmp_path: Path,
) -> None:
    runtime, _, release = _runtime(tmp_path)
    _register_both(runtime, release)
    _external_topology(runtime)
    lease_a = runtime.handoff_authority(
        "media-files:root-a",
        "media-agent-node-a",
        expected_partition_revision=1,
        expected_epoch=0,
        operation_id="initial-authority",
        principal=_principal(),
    )
    runtime.observe_replica(
        Replica(
            replica_id="root-a-node-a",
            partition_id="media-files:root-a",
            instance_id="media-agent-node-a",
            node_id="node-a",
            role="authority",
            lifecycle="ready",
            content_state="non_empty",
            authority_epoch=lease_a.epoch,
            checkpoint="scan:10",
            source_ref="file:///mnt/disk1/Music",
            freshness_seconds=1,
            item_count=10,
            byte_count=1000,
            observed_at=_NOW.isoformat(),
        ),
        expected_revision=0,
        principal=_principal(),
    )
    runtime.observe_replica(
        Replica(
            replica_id="root-a-node-b",
            partition_id="media-files:root-a",
            instance_id="media-agent-node-b",
            node_id="node-b",
            role="follower",
            lifecycle="ready",
            content_state="non_empty",
            authority_epoch=lease_a.epoch,
            checkpoint="scan:10",
            source_ref="file:///mnt/disk1/Music",
            freshness_seconds=2,
            item_count=10,
            byte_count=1000,
            observed_at=_NOW.isoformat(),
        ),
        expected_revision=0,
        principal=_principal(),
    )
    lease_b = runtime.handoff_authority(
        "media-files:root-a",
        "media-agent-node-b",
        expected_partition_revision=2,
        expected_epoch=1,
        operation_id="handoff-to-b",
        principal=_principal(),
    )
    with pytest.raises(StaleAuthorityEpochError):
        runtime.assert_authority(
            scope_ref="partition:media-files:root-a",
            instance_id="media-agent-node-a",
            epoch=lease_a.epoch,
        )
    runtime.observe_replica(
        replace(
            runtime.store.get_replica("root-a-node-b"),
            role="authority",
            authority_epoch=lease_b.epoch,
            revision=2,
        ),
        expected_revision=1,
        principal=_principal(),
    )
    route = runtime.resolve_route(
        "media-files",
        ("media-files:root-a", "media-files:missing"),
        auth_scope="media.read",
        allow_partial=True,
        principal=_principal(),
    )
    assert route.partial is True
    assert route.unavailable_partitions == ("media-files:missing",)
    assert [item.replica_id for item in route.endpoints] == ["root-a-node-b"]


class FakeTopologyAdapter:
    def __init__(
        self, *, retry: str | None = None, uncertain: str | None = None
    ) -> None:
        self.retry = retry
        self.uncertain = uncertain
        self.calls: list[tuple[str, int, str]] = []

    def _call(self, context) -> Mapping[str, Any]:
        self.calls.append((context.phase, context.attempt, context.idempotency_key))
        if context.phase == self.uncertain:
            raise UncertainTopologyPhaseError("dispatch_outcome_unknown")
        if context.phase == self.retry and context.attempt == 1:
            raise RetryableTopologyPhaseError("temporary_pressure")
        return {
            "checkpoint": "catalog:10",
            "credential_token": "must-not-leak",
        }

    inspect = reserve = prepare = snapshot = stream_deltas = catch_up = _call
    verify = activate_read = promote = demote = drain = remove = route = release = _call


def _derived_topology(runtime: DistributedRuntime) -> None:
    runtime.define_dataset(
        Dataset(
            dataset_id="media-catalog",
            owner_ref="skill:media_library_agent",
            contract="media.catalog.v1",
            consistency_profile="derived_projection",
            partition_scheme={"kind": "source_root"},
            retention={"on_remove": "rebuild"},
            data_class="derived",
            desired_revision=1,
        ),
        expected_revision=0,
        principal=_principal(),
    )
    runtime.put_partition(
        Partition(
            partition_id="media-catalog:root-a",
            dataset_id="media-catalog",
            selector={"root_id": "root-a"},
            desired_replicas=1,
            topology_generation=1,
            authority_lease_id=None,
            authority_epoch=0,
            checkpoint="catalog:10",
            status="ready",
        ),
        expected_revision=0,
        principal=_principal(),
    )


def test_topology_operation_retries_known_failure_and_redacts_receipt(
    tmp_path: Path,
) -> None:
    runtime, _, _ = _runtime(tmp_path)
    _derived_topology(runtime)
    adapter = FakeTopologyAdapter(retry="snapshot")
    runtime.topology_adapter = adapter
    plan = runtime.plan_replica_change(
        "media-catalog:root-a",
        action="rebuild",
        source_instance_id="media-agent-node-a",
        target_instance_id="media-agent-node-b",
        replica_role="derived",
        principal=_principal(),
    )
    operation = runtime.apply_topology_plan(
        str(plan.plan_digest),
        idempotency_key="rebuild-catalog-10",
        principal=_principal(),
    )
    assert operation.state == "succeeded"
    snapshot_calls = [item for item in adapter.calls if item[0] == "snapshot"]
    assert [item[1] for item in snapshot_calls] == [1, 2]
    assert all(
        phase.receipt.get("credential_token") == "[redacted]"
        for phase in operation.phases
    )
    repeated = runtime.apply_topology_plan(
        str(plan.plan_digest),
        idempotency_key="rebuild-catalog-10",
        principal=_principal(),
    )
    assert repeated == operation


def test_uncertain_topology_phase_is_not_retried(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    _derived_topology(runtime)
    adapter = FakeTopologyAdapter(uncertain="verify")
    runtime.topology_adapter = adapter
    plan = runtime.plan_replica_change(
        "media-catalog:root-a",
        action="repair",
        source_instance_id="media-agent-node-a",
        target_instance_id="media-agent-node-b",
        replica_role="derived",
        principal=_principal(),
    )
    operation = runtime.apply_topology_plan(
        str(plan.plan_digest),
        idempotency_key="repair-catalog-10",
        principal=_principal(),
    )
    assert operation.state == "uncertain"
    assert len([item for item in adapter.calls if item[0] == "verify"]) == 1
    assert operation.phases[-1].state == "uncertain"


class ChunkSource:
    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.calls = 0

    def authorize(self, *, auth_scope: str, operation_id: str) -> bool:
        return auth_scope == "replica.transfer" and bool(operation_id)

    def read(
        self,
        *,
        checkpoint: str | None,
        max_bytes: int,
        cancelled: Callable[[], bool],
    ) -> TransferChunk:
        assert max_bytes <= 1024
        self.calls += 1
        eof = self.calls == 2
        return TransferChunk(
            payload=b"abc",
            checkpoint=f"chunk:{self.calls}",
            eof=eof,
            content_witness=self.digest if eof else None,
        )


def test_bounded_transfer_resumes_and_requires_content_witness(tmp_path: Path) -> None:
    store = DistributedRuntimeStore(state_dir=tmp_path)
    transfer = TransferRecord(
        transfer_id="catalog-transfer",
        operation_id="catalog-rebuild",
        partition_id="media-catalog:root-a",
        source_instance_id="node-a-agent",
        target_instance_id="node-b-agent",
        authority_epoch=1,
        state="preparing",
        checkpoint=None,
        manifest_digest=_DIGEST,
        item_count=0,
        byte_count=0,
        resume_token_ref=None,
        started_at=_NOW.isoformat(),
        updated_at=_NOW.isoformat(),
    )
    store.put_transfer(transfer)
    controller = BoundedTransferController(store=store, max_chunk_bytes=1024)
    source = ChunkSource(_DIGEST)
    paused = controller.pump(
        transfer.transfer_id,
        source=source,
        auth_scope="replica.transfer",
        max_chunks=1,
    )
    assert paused.state == "transferring"
    assert paused.checkpoint == "chunk:1"
    complete = controller.pump(
        transfer.transfer_id,
        source=source,
        auth_scope="replica.transfer",
        max_chunks=1,
    )
    assert complete.state == "complete"
    assert complete.byte_count == 6


def test_non_media_document_fixture_uses_same_opaque_partition_contract() -> None:
    dataset = Dataset(
        dataset_id="research-documents",
        owner_ref="skill:document_index_agent",
        contract="documents.index.v2",
        consistency_profile="derived_projection",
        partition_scheme={"kind": "collection_prefix", "version": "2"},
        retention={"on_remove": "rebuild"},
        data_class="derived",
        desired_revision=1,
    )
    partition = Partition(
        partition_id="research-documents:papers-a-f",
        dataset_id=dataset.dataset_id,
        selector={"collection": "papers", "prefix": ["a", "f"]},
        desired_replicas=2,
        topology_generation=1,
        authority_lease_id=None,
        authority_epoch=0,
        checkpoint="embedding-model:v4:offset:800",
        status="ready",
    )
    assert dataset.partition_scheme["kind"] == "collection_prefix"
    assert partition.selector["prefix"] == ["a", "f"]


def test_rebalance_plan_is_bounded_costed_and_does_not_mutate_replicas(
    tmp_path: Path,
) -> None:
    runtime, _clock, release = _runtime(tmp_path)
    _register_both(runtime, release)
    runtime.define_dataset(
        Dataset(
            dataset_id="media-catalog",
            owner_ref="skill:media_center_coordinator",
            contract="media.catalog.v1",
            consistency_profile="derived_projection",
            partition_scheme={"kind": "root"},
            retention={"on_remove": "rebuild"},
            data_class="derived",
            desired_revision=1,
        ),
        expected_revision=0,
        principal=_principal(),
    )
    runtime.put_partition(
        Partition(
            partition_id="media-catalog:root-a",
            dataset_id="media-catalog",
            selector={"root_id": "root-a", "estimated_bytes": 50_000_000},
            desired_replicas=2,
            topology_generation=1,
            authority_lease_id=None,
            authority_epoch=0,
            checkpoint="catalog:10",
            status="degraded",
        ),
        expected_revision=0,
        principal=_principal(),
    )
    runtime.observe_replica(
        Replica(
            replica_id="catalog-root-a-node-a",
            partition_id="media-catalog:root-a",
            instance_id="media-agent-node-a",
            node_id="node-a",
            role="derived",
            lifecycle="ready",
            content_state="non_empty",
            authority_epoch=0,
            checkpoint="catalog:10",
            source_ref="media-files:root-a",
            freshness_seconds=1,
            item_count=100,
            byte_count=50_000_000,
            observed_at=_NOW.isoformat(),
        ),
        expected_revision=0,
        principal=_principal(),
    )

    before = runtime.inspect(principal=_principal()).replicas
    result = runtime.plan_rebalance(
        "media-catalog",
        max_steps=4,
        max_parallel=99,
        throughput_bytes_per_second=10_000_000,
        principal=_principal(),
    )
    after = runtime.inspect(principal=_principal()).replicas

    assert result["status"] == "ready"
    assert result["dry_run"] is True
    assert result["estimates"] == {
        "bytes": 50_000_000,
        "temporary_bytes": 50_000_000,
        "seconds": 2,
        "throughput_bytes_per_second": 10_000_000,
        "max_parallel": 4,
    }
    step = result["plan"]["steps"][0]
    assert step["target_instance_id"] == "media-agent-node-b"
    assert step["adapter_options"]["expected_partition_revision"] == 1
    assert before == after
