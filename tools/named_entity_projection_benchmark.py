from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from typing import Any

import y_py as Y

from adaos.services import named_entities
from adaos.services.named_entity_projection import _write_payload_to_doc


class _EmptyDeviceInventory:
    def list_devices(self, kind=None) -> list[dict[str, Any]]:
        del kind
        return []


def _record(index: int, *, label_suffix: str = "") -> named_entities.NamedEntityRecord:
    return named_entities.NamedEntityRecord(
        canonical_ref=f"skill:synthetic_{index}",
        kind="skill",
        display_name=f"Synthetic Skill {index}{label_suffix}",
        aliases=(f"synthetic {index}", f"test entity {index}"),
        source="named_entity_projection_benchmark",
    )


def _payload(entity_count: int, *, changed_index: int | None = None, revision: int = 1) -> dict[str, Any]:
    records = [
        _record(index, label_suffix=" updated" if index == changed_index else "")
        for index in range(entity_count)
    ]
    payload = named_entities._compact_registry_payload_from_records(  # noqa: SLF001
        records,
        webspace_id="benchmark",
    )
    summary = dict(payload["summary"])
    summary["registry_revision"] = revision
    payload["summary"] = summary
    return payload


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "max_ms": round(max(ordered), 3),
    }


def _apply(doc: Y.YDoc, payload: dict[str, Any], *, changed_refs: tuple[str, ...] | None) -> tuple[float, int]:
    started = time.perf_counter()
    with doc.begin_transaction() as txn:
        before = txn.state_vector_v1()
        _write_payload_to_doc(doc, txn, payload, changed_refs=changed_refs)
        update = bytes(txn.diff_v1(before) or b"")
    return (time.perf_counter() - started) * 1000.0, 0 if update == b"\x00\x00" else len(update)


def _benchmark_yjs(*, entity_count: int, iterations: int) -> dict[str, Any]:
    initial = _payload(entity_count)
    seed = Y.YDoc()
    _apply(seed, initial, changed_refs=None)
    encoded = Y.encode_state_as_update(seed)
    full_doc = Y.YDoc()
    incremental_doc = Y.YDoc()
    Y.apply_update(full_doc, encoded)
    Y.apply_update(incremental_doc, encoded)

    with incremental_doc.begin_transaction() as txn:
        before = txn.state_vector_v1()
        _write_payload_to_doc(incremental_doc, txn, initial)
        old_unbounded_diff_bytes = len(bytes(txn.diff_v1() or b""))
        no_op_delta = bytes(txn.diff_v1(before) or b"")

    full_ms: list[float] = []
    incremental_ms: list[float] = []
    full_bytes: list[int] = []
    incremental_bytes: list[int] = []
    previous_changed_index: int | None = None
    for iteration in range(iterations):
        changed_index = iteration % entity_count
        current_changed_index = changed_index if iteration % 2 == 0 else None
        payload = _payload(
            entity_count,
            changed_index=current_changed_index,
            revision=iteration + 2,
        )
        full_elapsed, full_update_bytes = _apply(full_doc, payload, changed_refs=None)
        changed_refs = tuple(
            f"skill:synthetic_{index}"
            for index in sorted(
                {
                    index
                    for index in (previous_changed_index, current_changed_index)
                    if index is not None
                }
            )
        )
        incremental_elapsed, incremental_update_bytes = _apply(
            incremental_doc,
            payload,
            changed_refs=changed_refs,
        )
        full_ms.append(full_elapsed)
        incremental_ms.append(incremental_elapsed)
        full_bytes.append(full_update_bytes)
        incremental_bytes.append(incremental_update_bytes)
        previous_changed_index = current_changed_index

    full_json = json.loads(full_doc.get_map("registry").to_json())
    incremental_json = json.loads(incremental_doc.get_map("registry").to_json())
    return {
        "entity_count": entity_count,
        "iterations": iterations,
        "no_op": {
            "old_unbounded_diff_bytes": old_unbounded_diff_bytes,
            "transaction_delta_bytes": 0 if no_op_delta == b"\x00\x00" else len(no_op_delta),
        },
        "full_reconcile": {
            **_percentiles(full_ms),
            "update_bytes_p50": int(statistics.median(full_bytes)),
        },
        "incremental_reconcile": {
            **_percentiles(incremental_ms),
            "update_bytes_p50": int(statistics.median(incremental_bytes)),
        },
        "converged": full_json == incremental_json,
    }


def _benchmark_source_admission(
    *,
    entity_count: int,
    burst: int,
    lookup_delay_ms: float,
) -> dict[str, Any]:
    lookup_calls = 0
    lookup_items = [
        {"value": f"synthetic_{index}", "labels": [f"Synthetic Skill {index}"]}
        for index in range(entity_count)
    ]

    def _lookup_provider(*, webspace_id: str) -> dict[str, Any]:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_delay_ms > 0:
            time.sleep(lookup_delay_ms / 1000.0)
        return {"webspace_id": webspace_id, "lookups": {"skill_id": lookup_items}}

    service = named_entities.NamedEntityService(
        device_inventory_service=_EmptyDeviceInventory(),
        lookup_payload_provider=_lookup_provider,
    )
    registry = named_entities.NamedEntityRegistry()
    started = time.perf_counter()
    first = registry.refresh(webspace_id="benchmark", service=service)
    full_refresh_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    second = registry.refresh(
        webspace_id="benchmark",
        service=service,
        dirty_sources=("devices",),
    )
    device_refresh_ms = (time.perf_counter() - started) * 1000.0
    sample_ref = "skill:synthetic_0"
    sample_fingerprint = str(first.records_by_ref[sample_ref]["fingerprint"])
    hit_ms: list[float] = []
    for _ in range(burst):
        started = time.perf_counter()
        if not registry.fingerprints_match(
            {sample_ref: sample_fingerprint},
            webspace_id="benchmark",
        ):
            raise RuntimeError("cached fingerprint unexpectedly missed")
        hit_ms.append((time.perf_counter() - started) * 1000.0)
    return {
        "lookup_delay_ms": lookup_delay_ms,
        "lookup_calls": lookup_calls,
        "full_refresh_ms": round(full_refresh_ms, 3),
        "device_only_refresh_ms": round(device_refresh_ms, 3),
        "unchanged_snapshot_reused": second is first,
        "fingerprint_burst": {"iterations": burst, **_percentiles(hit_ms)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic named-entity registry/Yjs projection benchmark")
    parser.add_argument("--entities", type=int, default=500)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--burst", type=int, default=500)
    parser.add_argument("--lookup-delay-ms", type=float, default=25.0)
    args = parser.parse_args()
    entity_count = max(1, int(args.entities))
    iterations = max(2, int(args.iterations))
    burst = max(1, int(args.burst))

    yjs = _benchmark_yjs(entity_count=entity_count, iterations=iterations)
    admission = _benchmark_source_admission(
        entity_count=entity_count,
        burst=burst,
        lookup_delay_ms=max(0.0, float(args.lookup_delay_ms)),
    )
    incremental_p95 = float(yjs["incremental_reconcile"]["p95_ms"])
    full_p95 = float(yjs["full_reconcile"]["p95_ms"])
    checks = {
        "no_op_update_is_empty": yjs["no_op"]["transaction_delta_bytes"] == 0,
        "incremental_converges": bool(yjs["converged"]),
        "incremental_apply_is_faster": incremental_p95 < full_p95,
        "scoped_refresh_skips_lookups": admission["lookup_calls"] == 1,
        "unchanged_snapshot_reused": bool(admission["unchanged_snapshot_reused"]),
    }
    report = {
        "schema": "adaos.named-entity-projection-benchmark.v1",
        "generated_at": time.time(),
        "parameters": {
            "entities": entity_count,
            "iterations": iterations,
            "burst": burst,
        },
        "yjs": yjs,
        "source_admission": admission,
        "checks": checks,
        "passed": all(checks.values()),
        "report_fingerprint": hashlib.sha256(
            json.dumps({"yjs": yjs, "source_admission": admission}, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
