from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adaos.domain.distributed_runtime import (
    ServiceGroup,
    ServiceInstance,
    TopologyLease,
    TopologyOperation,
    TopologyPhaseResult,
)
from adaos.sdk.distributed import OPERATOR_PROJECTION_SCHEMA
from adaos.services.distributed_runtime.projections import build_distributed_projection


def _operation() -> TopologyOperation:
    return TopologyOperation(
        operation_id="topology-safe-projection",
        kind="replicate",
        target_ref="partition:media-catalog:home",
        state="failed",
        expected_revision=7,
        authority_epoch=4,
        idempotency_key="private-idempotency-key",
        phases=(
            TopologyPhaseResult(
                phase="replica-home.snapshot",
                state="failed",
                attempt=2,
                idempotency_key="private-phase-key",
                receipt={
                    "credential_token": "must-not-leak",
                    "source_path": "D:/Private/Movies/movie.mp4",
                    "payload": "x" * 16_384,
                },
                started_at="2026-08-21T12:00:00Z",
                finished_at="2026-08-21T12:00:01Z",
                error_code="snapshot_verification_failed",
            ),
        ),
        created_at="2026-08-21T12:00:00Z",
        updated_at="2026-08-21T12:00:01Z",
    )


def test_operator_projection_is_schema_valid_bounded_and_receipt_free() -> None:
    projection = build_distributed_projection(
        groups=(),
        instances=(),
        leases=(),
        datasets=(),
        partitions=(),
        replicas=(),
        operations=(_operation(),),
        routes=(),
    )

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "abi"
        / "distributed.operator_projection.v2.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(projection)

    assert projection["schema"] == OPERATOR_PROJECTION_SCHEMA
    assert projection["recent_operations"] == [
        {
            "operation_id": "topology-safe-projection",
            "kind": "replicate",
            "target_ref": "partition:media-catalog:home",
            "state": "failed",
            "expected_revision": 7,
            "authority_epoch": 4,
            "phase_count": 1,
            "terminal_phase_count": 1,
            "current_phase": "replica-home.snapshot",
            "current_phase_state": "failed",
            "error_code": "snapshot_verification_failed",
            "created_at": "2026-08-21T12:00:00Z",
            "updated_at": "2026-08-21T12:00:01Z",
        }
    ]
    encoded = json.dumps(projection, ensure_ascii=False)
    assert "must-not-leak" not in encoded
    assert "D:/Private" not in encoded
    assert "private-idempotency" not in encoded
    assert "receipt" not in encoded
    assert len(encoded.encode("utf-8")) < 16_384


def test_operator_projection_derives_ready_group_from_live_instances() -> None:
    now = datetime.now(timezone.utc)
    group = ServiceGroup(
        group_id="media-library-home",
        definition_id="media-library-agent",
        definition_version="1",
        desired_generation=3,
        desired_instances=1,
        authority_policy="none",
        placement={},
        linked_datasets=("media-catalog",),
        route_policy={},
        desired_revision=7,
    )
    lease = TopologyLease(
        lease_id="membership-media-home",
        scope_ref="service_group:media-library-home",
        owner_instance_id="media-home-node-a",
        kind="membership",
        epoch=0,
        topology_generation=3,
        operation_ref=None,
        issued_at=now.isoformat(),
        renew_by=(now + timedelta(seconds=30)).isoformat(),
        valid_until=(now + timedelta(seconds=60)).isoformat(),
    )
    instance = ServiceInstance(
        instance_id="media-home-node-a",
        group_id=group.group_id,
        node_id="node-a",
        activation_id="activation-media-home",
        release_digest=f"sha256:{'a' * 64}",
        component_ref="skill:media_library_agent",
        runtime_generation=3,
        protocol_version="1",
        topology_generation=3,
        lease_id=lease.lease_id,
        status="ready",
        readiness=True,
        health={},
        pressure={},
        capabilities=(),
        endpoints=(),
        observed_at=now.isoformat(),
    )

    projection = build_distributed_projection(
        groups=(group,),
        instances=(instance,),
        leases=(lease,),
        datasets=(),
        partitions=(),
        replicas=(),
        operations=(),
    )

    assert projection["summary"]["ready_groups"] == 1
    assert projection["status"]["groups"] == {"ready": 1}
