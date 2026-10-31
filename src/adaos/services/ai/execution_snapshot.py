"""Consumer-scoped, read-only attempt-set observations, never turn closure."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone


def _counter(value):
    return type(value) is int and value >= 0


def _observation(row):
    usage = json.loads(row["usage"])
    telemetry = json.loads(row["telemetry"])
    if not isinstance(usage, dict) or not isinstance(telemetry, dict):
        raise ValueError("ai_execution_snapshot_invalid_metadata")
    safe_usage = {
        key: usage[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if _counter(usage.get(key))
    }
    for key, group, field in (
        ("cached_input_tokens", "input_tokens_details", "cached_tokens"),
        ("reasoning_tokens", "output_tokens_details", "reasoning_tokens"),
    ):
        details = usage.get(group)
        if isinstance(details, dict) and _counter(details.get(field)):
            safe_usage[key] = details[field]
    safe = {
        key: telemetry[key]
        for key in ("provider_calls",)
        if _counter(telemetry.get(key))
    }
    wire = telemetry.get("wire")
    if isinstance(wire, dict):
        projected = {}
        for key, pattern in (
            ("model", r"[A-Za-z0-9_./:-]{1,200}"),
            ("body_sha256", r"[a-f0-9]{64}"),
            ("body_digest_basis", r"serialized_utf8"),
            ("output_schema_digest", r"sha256:[a-f0-9]{64}"),
        ):
            if isinstance(wire.get(key), str) and re.fullmatch(pattern, wire[key]):
                projected[key] = wire[key]
        types = wire.get("tool_types")
        if (
            isinstance(types, list)
            and len(types) <= 64
            and all(
                isinstance(value, str) and re.fullmatch(r"[a-z_]{1,64}", value)
                for value in types
            )
        ):
            projected["tool_types"] = types
        if projected:
            safe["wire"] = projected
    if row["status"] not in {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }:
        raise ValueError("ai_execution_snapshot_invalid_status")
    return {
        "job_id": row["id"],
        "request_id": row["request_id"],
        "input_digest": row["digest"],
        "status": row["status"],
        "usage": safe_usage,
        "telemetry": safe,
    }


def execution_scope_snapshot(db, *, principal, consumer, job_id):
    """Read within the caller's DB snapshot; owner authorization is external.

    No source/output/error/tool-result/credential fields are selected. A missing
    trustworthy scope stays one unqualified attempt, not all consumer history.
    The bounded set is complete at this read, not fenced against future submits.
    """
    columns = "id,request_id,digest,status,usage,telemetry"
    anchor = db.execute(
        "SELECT "
        + columns
        + " FROM attempts WHERE id=? AND principal=? AND consumer=?",
        (job_id, principal, consumer),
    ).fetchone()
    if anchor is None:
        raise KeyError("ai_attempt_not_found")
    telemetry = json.loads(anchor["telemetry"])
    scope = telemetry.get("turn_scope") if isinstance(telemetry, dict) else None
    if isinstance(scope, str) and re.fullmatch(r"[a-f0-9]{64}", scope):
        rows = db.execute(
            "SELECT " + columns + " FROM attempts WHERE principal=? AND consumer=? "
            "AND json_extract(telemetry,'$.turn_scope')=? ORDER BY created,id LIMIT 17",
            (principal, consumer, scope),
        ).fetchall()
        if len(rows) > 16:
            raise ValueError("ai_execution_snapshot_capacity_exceeded")
        qualified = True
    else:
        rows, scope, qualified = [anchor], None, False
    if not any(row["id"] == job_id for row in rows):
        raise ValueError("ai_execution_snapshot_anchor_missing")
    return {
        "schema": "adaos.ai.execution_scope_snapshot.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "core.ai.attempts",
        "anchor_job_id": job_id,
        "consumer": consumer,
        "scope": "trusted_invocation_snapshot"
        if qualified
        else "single_unqualified_attempt",
        "turn_scope": scope,
        "observations": [_observation(row) for row in rows],
        "job_set_complete_as_of_read": True,
        "submission_closed": False,
        "whole_turn_accounting_complete": False,
    }
