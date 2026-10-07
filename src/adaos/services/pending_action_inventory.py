from __future__ import annotations

import hashlib
import json
import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


_ABI_ROOT = Path(__file__).resolve().parents[1] / "abi"
_INVENTORY_PATH = _ABI_ROOT / "pending_action.producer_inventory.v1.json"
_SCHEMA_PATH = _ABI_ROOT / "pending_action.producer_inventory.v1.schema.json"
_ACTIVE_STATUSES = frozenset({"pending", "postponed"})
_TERMINAL_STATUSES = frozenset({"responded", "expired", "cancelled", "superseded"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


@lru_cache(maxsize=1)
def _validated_inventory() -> tuple[dict[str, Any], str]:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    inventory = json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(inventory)
    digest = hashlib.sha256(_canonical_bytes(inventory)).hexdigest()
    return inventory, digest


def load_pending_action_inventory() -> dict[str, Any]:
    """Return the versioned source inventory without exposing cached mutation."""

    inventory, digest = _validated_inventory()
    return {**_clone(inventory), "digest": f"sha256:{digest}"}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(ordered[rank - 1], 3)


def audit_pending_action_projection(
    snapshot: Mapping[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Produce a content-free, reproducible closure audit for one projection."""

    observed_at = float(time.time() if now is None else now)
    inventory = load_pending_action_inventory()
    family_by_kind = {
        str(item.get("kind")): item
        for item in inventory["families"]
        if item.get("kind")
    }
    raw_by_id = snapshot.get("by_id")
    by_id = raw_by_id if isinstance(raw_by_id, Mapping) else {}
    counts = {
        "total": 0,
        "active": 0,
        "terminal": 0,
        "expired_due": 0,
        "unknown_kind": 0,
        "missing_consumer": 0,
    }
    active_ages: list[float] = []
    closure_latencies: list[float] = []
    by_kind: dict[str, dict[str, int]] = {}
    for raw in by_id.values():
        if not isinstance(raw, Mapping):
            continue
        counts["total"] += 1
        kind = str(raw.get("kind") or "<missing>")
        status = str(raw.get("status") or "pending")
        kind_counts = by_kind.setdefault(kind, {"total": 0, "active": 0, "terminal": 0})
        kind_counts["total"] += 1
        family = family_by_kind.get(kind)
        if family is None:
            counts["unknown_kind"] += 1
        elif (family.get("consumer") or {}).get("status") in {"missing", "not_applicable"}:
            counts["missing_consumer"] += 1
        created_at = _number(raw.get("created_at"))
        if status in _ACTIVE_STATUSES:
            counts["active"] += 1
            kind_counts["active"] += 1
            if created_at is not None:
                active_ages.append(max(0.0, observed_at - created_at))
            expires_at = _number(raw.get("expires_at"))
            if expires_at is not None and expires_at <= observed_at:
                counts["expired_due"] += 1
        elif status in _TERMINAL_STATUSES:
            counts["terminal"] += 1
            kind_counts["terminal"] += 1
            finished_at = _number(raw.get("finished_at")) or _number(raw.get("updated_at"))
            if created_at is not None and finished_at is not None and finished_at >= created_at:
                closure_latencies.append(finished_at - created_at)
    ordered_kinds = {key: by_kind[key] for key in sorted(by_kind)}
    return {
        "schema": "adaos.pending_action.baseline_audit.v1",
        "observed_at": observed_at,
        "inventory": {
            "schema": inventory["schema"],
            "version": inventory["inventory_version"],
            "digest": inventory["digest"],
        },
        "counts": counts,
        "active_age_seconds": {
            "p50": _percentile(active_ages, 0.50),
            "p95": _percentile(active_ages, 0.95),
            "max": round(max(active_ages), 3) if active_ages else None,
        },
        "closure_latency_seconds": {
            "p50": _percentile(closure_latencies, 0.50),
            "p95": _percentile(closure_latencies, 0.95),
        },
        "by_kind": ordered_kinds,
        "redaction": "No action ids, text, payloads, actors, or domain references are included.",
    }


def build_pending_action_baseline(
    snapshot: Mapping[str, Any],
    *,
    component_revisions: Mapping[str, str],
    topology: Mapping[str, Any],
    sdk_discovery: Mapping[str, Any],
    sample: Mapping[str, Any],
    now: float | None = None,
) -> dict[str, Any]:
    """Bind a projection audit to the revisions and sampling evidence that produced it."""

    required_components = {"core", "client", "application", "runtime", "sdk", "prompt"}
    missing = sorted(required_components - {str(key) for key in component_revisions})
    if missing:
        raise ValueError(f"missing component revisions: {', '.join(missing)}")
    if int(sample.get("size") or 0) < 1:
        raise ValueError("sample.size must be positive")
    audit = audit_pending_action_projection(snapshot, now=now)
    evidence = {
        "schema": "adaos.pending_action.baseline_evidence.v1",
        "audit": audit,
        "component_revisions": {key: str(component_revisions[key]) for key in sorted(component_revisions)},
        "topology": _clone(topology),
        "sdk_discovery": _clone(sdk_discovery),
        "sample": _clone(sample),
    }
    evidence["digest"] = f"sha256:{hashlib.sha256(_canonical_bytes(evidence)).hexdigest()}"
    return evidence


__all__ = [
    "audit_pending_action_projection",
    "build_pending_action_baseline",
    "load_pending_action_inventory",
]
