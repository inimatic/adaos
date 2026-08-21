from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adaos.domain.distributed_runtime import TopologyOperation, TopologyPhaseResult
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
