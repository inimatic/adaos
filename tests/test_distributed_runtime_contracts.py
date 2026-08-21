from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from adaos.domain.distributed_runtime import (
    Dataset,
    DistributedContractError,
    DistributedRoute,
    Partition,
    Replica,
    RouteEndpoint,
    ServiceDefinition,
    ServiceEndpoint,
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
    TopologyPhaseResult,
    TransferRecord,
)


_DIGEST = "sha256:" + "a" * 64
_NOW = "2026-08-19T18:00:00+00:00"
_LATER = "2026-08-19T18:05:00+00:00"
_LATEST = "2026-08-19T18:10:00+00:00"
_SCHEMAS = (
    "distributed.service-definition.v2.schema.json",
    "distributed.service-group.v1.schema.json",
    "distributed.service-instance.v1.schema.json",
    "distributed.topology-lease.v1.schema.json",
    "distributed.dataset.v1.schema.json",
    "distributed.partition.v1.schema.json",
    "distributed.replica.v1.schema.json",
    "distributed.route.v1.schema.json",
    "distributed.topology-operation.v1.schema.json",
    "distributed.transfer.v1.schema.json",
)


def _schema(name: str) -> dict[str, Any]:
    root = Path(__file__).parents[1] / "src" / "adaos" / "abi"
    return json.loads((root / name).read_text(encoding="utf-8"))


def _definition() -> ServiceDefinition:
    return ServiceDefinition(
        definition_id="media-library-agent",
        version="1",
        release_digest=_DIGEST,
        compatible_components=("skill:media_library_agent",),
        provided_contracts=("media.catalog.v1", "media.search.v1"),
        topology_mode="multi_instance",
        protocol_version="1",
        required_capabilities=("media.catalog",),
        adapter_contracts=("adaos.distributed.adapter.v1",),
    )


def _group() -> ServiceGroup:
    return ServiceGroup(
        group_id="media-library-home",
        definition_id="media-library-agent",
        definition_version="1",
        desired_generation=3,
        desired_instances=2,
        authority_policy="singleton_fenced",
        placement={"mode": "selected_nodes", "node_ids": ["node-a", "node-b"]},
        linked_datasets=("media-catalog",),
        route_policy={"max_staleness_seconds": 30},
        desired_revision=4,
        observed_revision=3,
        status="reconciling",
    )


def _membership_lease() -> TopologyLease:
    return TopologyLease(
        lease_id="lease-membership-node-a",
        scope_ref="service_group:media-library-home",
        owner_instance_id="media-agent-node-a",
        kind="membership",
        epoch=0,
        topology_generation=3,
        operation_ref=None,
        issued_at=_NOW,
        renew_by=_LATER,
        valid_until=_LATEST,
    )


def _instance(*, readiness: bool = True, status: str = "ready") -> ServiceInstance:
    return ServiceInstance(
        instance_id="media-agent-node-a",
        group_id="media-library-home",
        node_id="node-a",
        activation_id="activation-media-agent-node-a",
        release_digest=_DIGEST,
        component_ref="skill:media_library_agent",
        runtime_generation=3,
        protocol_version="1",
        topology_generation=3,
        lease_id=_membership_lease().lease_id,
        status=status,
        readiness=readiness,
        health={"status": "passing"},
        pressure={"queue_depth": 2},
        capabilities=("media.catalog",),
        endpoints=(
            ServiceEndpoint(
                endpoint_id="catalog",
                protocol="adaos.skill.v1",
                address_ref="skill://media_library_agent/catalog",
                scopes=("media.read",),
            ),
        ),
        observed_at=_NOW,
    )


def _dataset(profile: str = "external_authority") -> Dataset:
    return Dataset(
        dataset_id="media-files",
        owner_ref="skill:media_library_agent",
        contract="media.files.v1",
        consistency_profile=profile,
        partition_scheme={"kind": "source_root"},
        retention={"on_remove": "retain"},
        data_class="external" if profile == "external_authority" else "derived",
        desired_revision=2,
        observed_revision=1,
        status="ready",
        metadata={"payload_transport": "source_reference"},
    )


def _partition() -> Partition:
    return Partition(
        partition_id="media-files:root-a",
        dataset_id="media-files",
        selector={"root_id": "root-a"},
        desired_replicas=1,
        topology_generation=3,
        authority_lease_id="lease-authority-root-a",
        authority_epoch=7,
        checkpoint="scan:9021",
        status="ready",
    )


def _replica(*, content_state: str = "non_empty", lifecycle: str = "ready") -> Replica:
    return Replica(
        replica_id="replica-root-a-node-a",
        partition_id="media-files:root-a",
        instance_id="media-agent-node-a",
        node_id="node-a",
        role="authority",
        lifecycle=lifecycle,
        content_state=content_state,
        authority_epoch=7,
        checkpoint="scan:9021",
        source_ref="file:///mnt/disk1/Music",
        freshness_seconds=2.5,
        item_count=5000,
        byte_count=500_000_000,
        observed_at=_NOW,
    )


def _route() -> DistributedRoute:
    return DistributedRoute(
        route_id="route-media-files-4",
        dataset_id="media-files",
        partition_ids=("media-files:root-a", "media-files:root-b"),
        endpoints=(
            RouteEndpoint(
                endpoint_ref="skill://media_library_agent/catalog",
                replica_id="replica-root-a-node-a",
                partition_id="media-files:root-a",
                role="authority",
                priority=0,
                authority_epoch=7,
                checkpoint="scan:9021",
                freshness_seconds=2.5,
                observed_at=_NOW,
            ),
        ),
        consistency_profile="external_authority",
        topology_generation=3,
        topology_revision=4,
        partial=True,
        unavailable_partitions=("media-files:root-b",),
        fallback="external_source",
        auth_scope="media.read",
        created_at=_NOW,
        expires_at=_LATER,
    )


def _operation() -> TopologyOperation:
    return TopologyOperation(
        operation_id="topology-op-4",
        kind="handoff",
        target_ref="partition:media-files:root-a",
        state="succeeded",
        expected_revision=4,
        authority_epoch=8,
        idempotency_key="handoff:media-files:root-a:8",
        phases=(
            TopologyPhaseResult(
                phase="promote",
                state="succeeded",
                attempt=1,
                idempotency_key="handoff:media-files:root-a:8:promote",
                receipt={"accepted_epoch": 8},
                started_at=_NOW,
                finished_at=_LATER,
            ),
        ),
        created_at=_NOW,
        updated_at=_LATER,
    )


def _transfer() -> TransferRecord:
    return TransferRecord(
        transfer_id="transfer-root-a-8",
        operation_id="topology-op-4",
        partition_id="media-files:root-a",
        source_instance_id="media-agent-node-a",
        target_instance_id="media-agent-node-b",
        authority_epoch=8,
        state="transferring",
        checkpoint="scan:9021",
        manifest_digest=_DIGEST,
        item_count=5000,
        byte_count=500_000_000,
        resume_token_ref="blob:transfer-root-a-8-resume",
        started_at=_NOW,
        updated_at=_LATER,
    )


def test_distributed_contracts_round_trip_and_validate_json_schemas() -> None:
    records = (
        (_SCHEMAS[0], _definition(), ServiceDefinition),
        (_SCHEMAS[1], _group(), ServiceGroup),
        (_SCHEMAS[2], _instance(), ServiceInstance),
        (_SCHEMAS[3], _membership_lease(), TopologyLease),
        (_SCHEMAS[4], _dataset(), Dataset),
        (_SCHEMAS[5], _partition(), Partition),
        (_SCHEMAS[6], _replica(), Replica),
        (_SCHEMAS[7], _route(), DistributedRoute),
        (_SCHEMAS[8], _operation(), TopologyOperation),
        (_SCHEMAS[9], _transfer(), TransferRecord),
    )
    for schema_name, record, record_type in records:
        payload = record.to_dict()
        jsonschema.Draft202012Validator(
            _schema(schema_name), format_checker=jsonschema.FormatChecker()
        ).validate(payload)
        assert record_type.from_mapping(payload) == record


def test_service_definition_v1_remains_readable_during_rolling_upgrade() -> None:
    current = _definition()
    legacy = current.to_dict()
    legacy["schema"] = "adaos.distributed.service_definition.v1"
    legacy.pop("compatible_release_digests")

    jsonschema.Draft202012Validator(
        _schema("distributed.service-definition.v1.schema.json"),
        format_checker=jsonschema.FormatChecker(),
    ).validate(legacy)

    restored = ServiceDefinition.from_mapping(legacy)
    assert restored == current
    assert restored.to_dict()["schema"] == "adaos.distributed.service_definition.v2"


def test_service_definition_bounds_exact_release_overlap() -> None:
    with pytest.raises(
        DistributedContractError,
        match="compatible_release_digests exceeds 8 items",
    ):
        replace(
            _definition(),
            compatible_release_digests=tuple(
                f"sha256:{index:064x}" for index in range(1, 10)
            ),
        )


def test_unknown_fields_fail_closed() -> None:
    payload = _dataset().to_dict()
    payload["domain_partition_hint"] = "must stay in the skill"
    with pytest.raises(DistributedContractError, match="unsupported"):
        Dataset.from_mapping(payload)


def test_membership_lease_does_not_imply_readiness() -> None:
    lease = _membership_lease()
    instance = _instance(readiness=False, status="unavailable")
    assert lease.status == "active"
    assert instance.readiness is False
    assert instance.status == "unavailable"


def test_authority_requires_positive_fencing_epoch() -> None:
    with pytest.raises(DistributedContractError, match="epoch >= 1"):
        TopologyLease(
            lease_id="bad",
            scope_ref="partition:media-files:root-a",
            owner_instance_id="media-agent-node-a",
            kind="authority",
            epoch=0,
            topology_generation=3,
            operation_ref=None,
            issued_at=_NOW,
            renew_by=_LATER,
            valid_until=_LATEST,
        )


def test_replica_empty_and_unavailable_are_distinct() -> None:
    assert _replica(content_state="empty").content_state == "empty"
    unavailable = _replica(content_state="unavailable", lifecycle="unavailable")
    assert unavailable.content_state == "unavailable"
    with pytest.raises(DistributedContractError, match="unavailable content_state"):
        _replica(content_state="empty", lifecycle="unavailable")


def test_dataset_profiles_preserve_payload_ownership_boundary() -> None:
    assert _dataset("external_authority").data_class == "external"
    assert _dataset("derived_projection").data_class == "derived"
    assert _dataset("external_authority").removal_retention == "retain"
    assert _dataset("derived_projection").removal_retention == "retain"
    assert replace(_dataset("derived_projection"), retention={}).removal_retention == (
        "rebuild"
    )
    with pytest.raises(DistributedContractError, match="external data"):
        Dataset(
            dataset_id="bad",
            owner_ref="skill:test",
            contract="test.v1",
            consistency_profile="external_authority",
            partition_scheme={"kind": "key"},
            retention={},
            data_class="derived",
            desired_revision=1,
        )
    with pytest.raises(DistributedContractError, match="must retain"):
        Dataset(
            dataset_id="unsafe-external",
            owner_ref="skill:test",
            contract="test.v1",
            consistency_profile="external_authority",
            partition_scheme={"kind": "key"},
            retention={"on_remove": "delete"},
            data_class="external",
            desired_revision=1,
        )


def test_partial_route_must_name_unavailable_partitions() -> None:
    route = _route()
    assert route.partial is True
    assert route.endpoints[0].freshness_seconds == 2.5
    with pytest.raises(DistributedContractError, match="partial must match"):
        replace(route, partial=False)


def test_transfer_is_a_bounded_descriptor_not_payload() -> None:
    transfer = _transfer()
    assert transfer.resume_token_ref.startswith("blob:")
    assert "payload" not in transfer.to_dict()


def test_phase_receipts_are_bounded() -> None:
    with pytest.raises(DistributedContractError, match="32 KiB"):
        TopologyPhaseResult(
            phase="snapshot",
            state="succeeded",
            attempt=1,
            idempotency_key="snapshot:oversized",
            receipt={"payload": "x" * 33_000},
            started_at=_NOW,
            finished_at=_LATER,
        )
