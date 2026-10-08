from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import hashlib
import json
import sqlite3
import time
import uuid

from adaos.services.agent_context import AgentContext
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader


STATE_SCHEMA = "adaos.runtime_reactivation.state.v1"
RECEIPT_SCHEMA = "adaos.runtime_reactivation.receipt.v1"


class RuntimeReactivationError(ValueError):
    pass


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS runtime_reactivation_state (
        recovery_key TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        source_manifest_digest TEXT NOT NULL,
        expected_version TEXT NOT NULL,
        expected_slot TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt_count INTEGER NOT NULL,
        operation_id TEXT,
        started_at REAL,
        completed_at REAL,
        cooldown_until REAL,
        receipt_json TEXT,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_reactivation_attempts (
        operation_id TEXT PRIMARY KEY,
        recovery_key TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        started_at REAL NOT NULL,
        completed_at REAL,
        receipt_json TEXT,
        UNIQUE(recovery_key, attempt_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_reactivation_attempts_key
    ON runtime_reactivation_attempts(recovery_key, attempt_number DESC)
    """,
)


def _ensure_schema(ctx: AgentContext) -> None:
    with ctx.sql.connect() as con:
        for statement in _SCHEMA:
            con.execute(statement)
        columns = {
            str(row[1])
            for row in con.execute("PRAGMA table_info(runtime_reactivation_state)").fetchall()
        }
        if "source_manifest_digest" not in columns:
            con.execute(
                "ALTER TABLE runtime_reactivation_state "
                "ADD COLUMN source_manifest_digest TEXT NOT NULL DEFAULT ''"
            )
        con.commit()


def _recovery_key(
    skill_id: str,
    package_digest: str,
    source_manifest_digest: str,
) -> str:
    raw = f"{skill_id}\n{package_digest}\n{source_manifest_digest}".encode("utf-8")
    return "reactivation:" + hashlib.sha256(raw).hexdigest()


def _classification_admits(
    classification: Mapping[str, Any],
    *,
    package_digest: str,
    package_manifest_digest: str,
    source_manifest_digest: str,
) -> None:
    value = dict(classification or {})
    if value.get("schema") != "adaos.runtime_compatibility.classification.v1":
        raise RuntimeReactivationError("runtime compatibility classification is required")
    if value.get("evidence_complete") is not True:
        raise RuntimeReactivationError("runtime compatibility evidence is incomplete")
    if value.get("automatic_recovery_eligible") is not True:
        raise RuntimeReactivationError("classification does not admit automatic reactivation")
    if str(value.get("code") or "") not in {"stale_runtime_memory", "unavailable_module"}:
        raise RuntimeReactivationError("classification does not describe a reactivation condition")
    desired = str(value.get("desired_package_digest") or "").strip()
    installed = str(value.get("installed_package_digest") or "").strip()
    if not package_digest or desired != package_digest or installed != package_digest:
        raise RuntimeReactivationError("classification package identity does not match the requested digest")
    desired_manifest = str(value.get("desired_manifest_digest") or "").strip()
    installed_manifest = str(value.get("installed_manifest_digest") or "").strip()
    if desired_manifest or installed_manifest:
        if (
            not package_manifest_digest
            or desired_manifest != package_manifest_digest
            or installed_manifest != package_manifest_digest
        ):
            raise RuntimeReactivationError(
                "classification package manifest identity does not match the requested digest"
            )
    if not source_manifest_digest:
        raise RuntimeReactivationError("runtime source manifest identity is required")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _blocked_receipt(
    *,
    skill_id: str,
    package_digest: str,
    reason: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": False,
        "admitted": False,
        "reason": reason,
        "skill_id": skill_id,
        "package_digest": package_digest,
        "attempt_count": int(state.get("attempt_count") or 0),
        "operation_id": state.get("operation_id"),
        "cooldown_until": state.get("cooldown_until"),
    }


async def reactivate_exact_admitted_package(
    ctx: AgentContext,
    *,
    skill_id: str,
    expected_version: str,
    expected_slot: str,
    expected_package_digest: str,
    expected_package_manifest_digest: str | None = None,
    expected_source_manifest_digest: str | None = None,
    classification: Mapping[str, Any],
    attempt_budget: int = 3,
    cooldown_s: float = 300.0,
    running_lease_s: float = 120.0,
    in_flight_drain_timeout_s: float = 10.0,
    now: float | None = None,
    loader: ImportlibSkillsLoader | None = None,
) -> dict[str, Any]:
    """Run one bounded, identity-fenced skill handler reactivation.

    Admission and terminal receipts are durable. A crashed pre-effect attempt is
    reclaimable only after its lease; failures enter cooldown and an attempt
    budget. The loader performs the generation rollback and post-check.
    """

    skill = str(skill_id or "").strip()
    version = str(expected_version or "").strip()
    slot = str(expected_slot or "").strip().upper()
    package_digest = str(expected_package_digest or "").strip()
    package_manifest_digest = str(
        expected_package_manifest_digest
        or classification.get("desired_manifest_digest")
        or ""
    ).strip()
    source_manifest_digest = str(
        expected_source_manifest_digest or expected_package_digest or ""
    ).strip()
    if (
        not skill
        or not version
        or slot not in {"A", "B"}
        or not package_digest
        or not source_manifest_digest
    ):
        raise RuntimeReactivationError(
            "exact skill, version, slot, package digest, and source manifest digest are required"
        )
    budget = max(1, min(int(attempt_budget), 10))
    cooldown = max(1.0, min(float(cooldown_s), 86400.0))
    running_lease = max(1.0, min(float(running_lease_s), 3600.0))
    drain_timeout = max(0.0, min(float(in_flight_drain_timeout_s), 3600.0))
    _classification_admits(
        classification,
        package_digest=package_digest,
        package_manifest_digest=package_manifest_digest,
        source_manifest_digest=source_manifest_digest,
    )
    _ensure_schema(ctx)
    timestamp = float(time.time() if now is None else now)
    recovery_key = _recovery_key(skill, package_digest, source_manifest_digest)

    with ctx.sql.connect() as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM runtime_reactivation_state WHERE recovery_key=?",
            (recovery_key,),
        ).fetchone()
        state = dict(row) if row else {}
        if state.get("status") == "succeeded":
            receipt = json.loads(str(state.get("receipt_json") or "{}"))
            con.rollback()
            return {**receipt, "duplicate": True}
        if (
            state.get("status") == "running"
            and timestamp - float(state.get("started_at") or 0.0) < running_lease
        ):
            con.rollback()
            return _blocked_receipt(
                skill_id=skill,
                package_digest=package_digest,
                reason="reactivation_in_progress",
                state=state,
            )
        cooldown_until = float(state.get("cooldown_until") or 0.0)
        attempts = int(state.get("attempt_count") or 0)
        if cooldown_until > timestamp:
            con.rollback()
            return _blocked_receipt(
                skill_id=skill,
                package_digest=package_digest,
                reason="reactivation_cooldown",
                state=state,
            )
        if attempts >= budget:
            attempts = 0
        attempt_count = attempts + 1
        attempt_number = int(
            con.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM runtime_reactivation_attempts WHERE recovery_key=?",
                (recovery_key,),
            ).fetchone()[0]
        ) + 1
        operation_id = f"reactivation.{uuid.uuid4().hex}"
        con.execute(
            """
            INSERT INTO runtime_reactivation_state(
                recovery_key, skill_id, package_digest, source_manifest_digest, expected_version,
                expected_slot, status, attempt_count, operation_id, started_at,
                completed_at, cooldown_until, receipt_json, updated_at
            ) VALUES(?,?,?,?,?,?,'running',?,?,?,NULL,NULL,NULL,?)
            ON CONFLICT(recovery_key) DO UPDATE SET
                expected_version=excluded.expected_version,
                expected_slot=excluded.expected_slot,
                source_manifest_digest=excluded.source_manifest_digest,
                status='running', attempt_count=excluded.attempt_count,
                operation_id=excluded.operation_id, started_at=excluded.started_at,
                completed_at=NULL, cooldown_until=NULL, receipt_json=NULL,
                updated_at=excluded.updated_at
            """,
            (
                recovery_key,
                skill,
                package_digest,
                source_manifest_digest,
                version,
                slot,
                attempt_count,
                operation_id,
                timestamp,
                timestamp,
            ),
        )
        con.execute(
            """
            INSERT INTO runtime_reactivation_attempts(
                operation_id, recovery_key, attempt_number, status, started_at
            ) VALUES(?,?,?,'running',?)
            """,
            (operation_id, recovery_key, attempt_number, timestamp),
        )
        con.commit()

    runtime_loader = loader or ImportlibSkillsLoader()
    try:
        reload_receipt = await runtime_loader.reload_skill_handlers(
            ctx.paths.skills_dir(),
            skill,
            expected_version=version,
            expected_slot=slot,
            expected_source_manifest_digest=source_manifest_digest,
            expected_package_digest=package_digest,
            expected_package_manifest_digest=package_manifest_digest,
            drain_timeout_s=drain_timeout,
        )
    except Exception as exc:
        reload_receipt = {
            "ok": False,
            "reason": "reactivation_exception",
            "error": f"{type(exc).__name__}: {exc}"[:1000],
        }
    completed_at = float(time.time() if now is None else now)
    succeeded = bool(reload_receipt.get("ok")) and bool(
        dict(reload_receipt.get("postcheck") or {}).get("ok")
    )
    status = "succeeded" if succeeded else "failed"
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": succeeded,
        "admitted": True,
        "reason": "reactivation_succeeded" if succeeded else str(reload_receipt.get("reason") or "reactivation_failed"),
        "operation_id": operation_id,
        "skill_id": skill,
        "expected_version": version,
        "expected_slot": slot,
        "package_digest": package_digest,
        "package_manifest_digest": package_manifest_digest or None,
        "source_manifest_digest": source_manifest_digest,
        "attempt_count": attempt_count,
        "attempt_number": attempt_number,
        "started_at": timestamp,
        "completed_at": completed_at,
        "reload": dict(reload_receipt),
    }
    next_cooldown = None if succeeded else completed_at + cooldown
    with ctx.sql.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        updated = con.execute(
            """
            UPDATE runtime_reactivation_state SET
                status=?, completed_at=?, cooldown_until=?, receipt_json=?, updated_at=?
            WHERE recovery_key=? AND operation_id=? AND status='running'
            """,
            (status, completed_at, next_cooldown, _json(receipt), completed_at, recovery_key, operation_id),
        ).rowcount
        if updated != 1:
            con.rollback()
            raise RuntimeReactivationError("reactivation operation lost its durable lease")
        con.execute(
            """
            UPDATE runtime_reactivation_attempts SET
                status=?, completed_at=?, receipt_json=?
            WHERE operation_id=? AND status='running'
            """,
            (status, completed_at, _json(receipt), operation_id),
        )
        con.commit()
    return receipt


def list_reactivation_attempts(
    ctx: AgentContext,
    *,
    skill_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return bounded redaction-safe recovery receipts for diagnostics."""

    _ensure_schema(ctx)
    selected_limit = max(1, min(int(limit), 100))
    skill = str(skill_id or "").strip()
    with ctx.sql.connect() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT a.operation_id, a.attempt_number, a.status, a.started_at,
                   a.completed_at, a.receipt_json
            FROM runtime_reactivation_attempts a
            JOIN runtime_reactivation_state s ON s.recovery_key=a.recovery_key
            WHERE s.skill_id=?
            ORDER BY a.started_at DESC LIMIT ?
            """,
            (skill, selected_limit),
        ).fetchall()
    return [
        {
            "operation_id": row["operation_id"],
            "attempt_number": int(row["attempt_number"]),
            "status": row["status"],
            "started_at": float(row["started_at"]),
            "completed_at": float(row["completed_at"]) if row["completed_at"] is not None else None,
            "receipt": json.loads(str(row["receipt_json"] or "{}")),
        }
        for row in rows
    ]


__all__ = [
    "RECEIPT_SCHEMA",
    "RuntimeReactivationError",
    "list_reactivation_attempts",
    "reactivate_exact_admitted_package",
]
