from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaos.services.artifact_pipeline.storage import atomic_write_bytes
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


CAPSULE_SCHEMA = "adaos.context.capsule.v2"
RELATIONSHIP_SCHEMA = "adaos.context.relationship.v1"
BINDING_SCHEMA = "adaos.context.subject_binding.v1"
PLAN_SCHEMA = "adaos.context.plan.v1"
RECEIPT_SCHEMA = "adaos.agent.context_receipt.v1"
MEMORY_CANDIDATE_SCHEMA = "adaos.context.memory_candidate.v1"
INVALIDATION_SCHEMA = "adaos.context.invalidation.v1"
COMPILED_PACKET_SCHEMA = "adaos.context.compiled_packet.v1"
DELTA_SCHEMA = "adaos.context.delta.v1"

_TRUST_ORDER = {
    "quarantined": 0,
    "untrusted": 1,
    "observed": 2,
    "validated": 3,
    "accepted": 4,
}
_SHARED_KINDS = {"platform", "sdk", "api", "abi", "policy", "domain", "resource"}
_MEMORY_KINDS = {"authoritative", "procedural", "episodic", "working"}


class ContextConflict(ValueError):
    pass


class ContextAccessDenied(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence):
        return []
    result: list[str] = []
    for item in value:
        token = _text(item)
        if token and token not in result:
            result.append(token)
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json(value: Any) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _search_text(*values: Any) -> str:
    return "\n".join(_json(value).lower() for value in values if value is not None)


def _like_token(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _loads(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _instant(value: str | None) -> datetime:
    token = _text(value) or _now()
    parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _applies_at(valid_from: str, valid_to: str | None, as_of: str) -> bool:
    instant = _instant(as_of)
    return _instant(valid_from) <= instant and (not valid_to or instant < _instant(valid_to))


def _estimate_tokens(byte_count: int) -> int:
    return max(1, (max(0, int(byte_count)) + 3) // 4)


def _compiled_units(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        *_mappings(packet.get("stable_prefix")),
        *_mappings(packet.get("task_context")),
    ]


def _compiled_unit_key(unit: Mapping[str, Any]) -> str:
    subject_refs = [
        ref
        for ref in _strings(unit.get("subject_refs"))
        if not ref.startswith(("builder-run:", "change:", "dev-ticket:"))
    ]
    if subject_refs:
        return f"{_text(unit.get('kind'))}:{'|'.join(sorted(subject_refs))}"
    return _text(unit.get("ref")) or _digest(unit)


@dataclass(slots=True)
class ContextControlService:
    state_dir: Path | None = None

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir or current_state_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "context_control"

    @property
    def database_path(self) -> Path:
        return self.root / "registry.sqlite3"

    @property
    def artifact_root(self) -> Path:
        return self.root / "artifacts" / "sha256"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS capsules (
                    capsule_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    subject_refs_json TEXT NOT NULL,
                    authority_ref TEXT NOT NULL,
                    trust_class TEXT NOT NULL,
                    tainted INTEGER NOT NULL,
                    sensitivity TEXT NOT NULL,
                    license TEXT NOT NULL,
                    retention_class TEXT NOT NULL,
                    origin_json TEXT NOT NULL,
                    source_digests_json TEXT NOT NULL,
                    policy_ref TEXT,
                    locale TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    recorded_at TEXT NOT NULL,
                    supersedes_refs_json TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    artifact_bytes INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revocation_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_capsules_kind ON capsules(kind);
                CREATE INDEX IF NOT EXISTS idx_capsules_recorded ON capsules(recorded_at);
                CREATE TABLE IF NOT EXISTS capsule_search (
                    capsule_id TEXT PRIMARY KEY REFERENCES capsules(capsule_id) ON DELETE CASCADE,
                    search_text TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    relationship_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL UNIQUE,
                    from_capsule_id TEXT NOT NULL REFERENCES capsules(capsule_id),
                    to_capsule_id TEXT NOT NULL REFERENCES capsules(capsule_id),
                    relation_type TEXT NOT NULL,
                    required INTEGER NOT NULL,
                    propagate_taint INTEGER NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    recorded_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_relationships_from ON relationships(from_capsule_id);

                CREATE TABLE IF NOT EXISTS bindings (
                    binding_id TEXT PRIMARY KEY,
                    subject_ref TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    capsule_id TEXT NOT NULL REFERENCES capsules(capsule_id),
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(subject_ref, purpose, audience, branch)
                );
                CREATE TABLE IF NOT EXISTS binding_events (
                    event_id TEXT PRIMARY KEY,
                    binding_id TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    capsule_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    recorded_at TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_binding_events_lookup
                    ON binding_events(subject_ref, purpose, audience, branch, recorded_at);

                CREATE TABLE IF NOT EXISTS context_plans (
                    plan_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL UNIQUE,
                    resolution_ref TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    run_ref TEXT NOT NULL,
                    plan_ref TEXT NOT NULL,
                    digest TEXT NOT NULL UNIQUE,
                    artifact_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_receipts_run ON context_receipts(run_ref, created_at);

                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    proposal_ref TEXT NOT NULL,
                    proposed_by TEXT NOT NULL,
                    proposed_by_kind TEXT NOT NULL,
                    authority_ref TEXT NOT NULL,
                    trust_class TEXT NOT NULL,
                    tainted INTEGER NOT NULL,
                    validation_refs_json TEXT NOT NULL,
                    qualified_by TEXT,
                    promoted_capsule_id TEXT,
                    supersedes_candidate_ref TEXT,
                    reason TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invalidations (
                    invalidation_id TEXT PRIMARY KEY,
                    subject_ref TEXT NOT NULL,
                    source_digest TEXT,
                    edge_type TEXT,
                    reason TEXT NOT NULL,
                    event_ref TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            missing_search = connection.execute(
                """SELECT capsules.* FROM capsules
                   LEFT JOIN capsule_search USING (capsule_id)
                   WHERE capsule_search.capsule_id IS NULL"""
            ).fetchall()
            for row in missing_search:
                capsule = self._capsule_row(row)
                try:
                    artifact = self.get_artifact(capsule["artifact_ref"])
                except (KeyError, OSError, ValueError, json.JSONDecodeError):
                    artifact = None
                connection.execute(
                    "INSERT OR REPLACE INTO capsule_search VALUES (?, ?)",
                    (capsule["capsule_id"], _search_text(capsule, artifact)),
                )

    def put_artifact(self, value: Any) -> dict[str, Any]:
        payload = _canonical_bytes(value)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        path = self.artifact_root / f"{digest.removeprefix('sha256:')}.json"
        if not path.is_file():
            atomic_write_bytes(path, payload)
        return {
            "ref": f"artifact://context/sha256/{digest.removeprefix('sha256:')}",
            "digest": digest,
            "bytes": len(payload),
        }

    def get_artifact(self, artifact_ref: str) -> Any:
        digest = _text(artifact_ref).rsplit("/", 1)[-1]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise KeyError(f"invalid context artifact ref: {artifact_ref}")
        path = self.artifact_root / f"{digest.lower()}.json"
        if not path.is_file():
            raise KeyError(f"context artifact is unavailable: {artifact_ref}")
        return json.loads(path.read_text(encoding="utf-8"))

    def register_capsule(self, capsule: Mapping[str, Any], *, bind: bool = False) -> dict[str, Any]:
        request = dict(capsule)
        kind = _text(request.get("kind"))
        subject_refs = _strings(request.get("subject_refs"))
        authority_ref = _text(request.get("authority_ref"))
        if not kind or not subject_refs or not authority_ref:
            raise ValueError("context capsule requires kind, subject_refs, and authority_ref")
        trust_class = _text(request.get("trust_class")) or "observed"
        if trust_class not in _TRUST_ORDER:
            raise ValueError(f"unsupported trust_class: {trust_class}")
        origin = _mapping(request.get("origin"))
        tainted = bool(request.get("tainted", trust_class in {"quarantined", "untrusted"}))
        if tainted and trust_class in {"validated", "accepted"} and not origin.get("sanitization_evidence_refs"):
            raise ValueError("tainted capsule cannot be validated or accepted without sanitization evidence")
        valid_from = _text(request.get("valid_from")) or _now()
        recorded_at = _text(request.get("recorded_at")) or _now()
        artifact_value = {
            "schema": "adaos.context.capsule_content.v1",
            "summary": request.get("summary"),
            "index": request.get("index") or [],
            "content": request.get("content"),
            "source_slices": request.get("source_slices") or [],
            "metadata": _mapping(request.get("content_metadata")),
        }
        artifact = self.put_artifact(artifact_value)
        identity = {
            "schema": CAPSULE_SCHEMA,
            "kind": kind,
            "subject_refs": subject_refs,
            "authority_ref": authority_ref,
            "trust_class": trust_class,
            "tainted": tainted,
            "sensitivity": _text(request.get("sensitivity")) or "workspace",
            "license": _text(request.get("license")) or "internal",
            "retention_class": _text(request.get("retention_class")) or "working",
            "origin": origin,
            "source_digests": _mapping(request.get("source_digests")),
            "policy_ref": _text(request.get("policy_ref")) or None,
            "locale": _text(request.get("locale")) or "und",
            "valid_from": valid_from,
            "valid_to": _text(request.get("valid_to")) or None,
            "recorded_at": recorded_at,
            "supersedes_refs": _strings(request.get("supersedes_refs")),
            "artifact_ref": artifact["ref"],
            "artifact_digest": artifact["digest"],
            "metadata": _mapping(request.get("metadata")),
        }
        digest = _digest(identity)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM capsules WHERE digest = ?", (digest,)).fetchone()
            if row is not None:
                result = self._capsule_row(row)
            else:
                capsule_id = f"ctxcap.{new_id()}"
                created_at = _now()
                connection.execute(
                    """INSERT INTO capsules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                    (
                        capsule_id, digest, kind, _json(subject_refs), authority_ref, trust_class,
                        int(tainted), identity["sensitivity"], identity["license"], identity["retention_class"],
                        _json(origin), _json(identity["source_digests"]), identity["policy_ref"], identity["locale"],
                        valid_from, identity["valid_to"], recorded_at, _json(identity["supersedes_refs"]),
                        artifact["ref"], artifact["bytes"], _json(identity["metadata"]), created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM capsules WHERE capsule_id = ?",
                    (capsule_id,),
                ).fetchone()
                result = self._capsule_row(row)
            connection.execute(
                "INSERT OR REPLACE INTO capsule_search VALUES (?, ?)",
                (result["capsule_id"], _search_text(result, artifact_value)),
            )
        if bind:
            bindings = []
            for subject_ref in subject_refs:
                bindings.append(self.bind_subject(
                    subject_ref=subject_ref,
                    capsule_id=result["capsule_id"],
                    purpose=_text(request.get("purpose")) or "*",
                    audience=_text(request.get("audience")) or "*",
                    branch=_text(request.get("branch")) or "main",
                    actor_ref=_text(request.get("actor_ref")) or authority_ref,
                    reason="capsule_registered",
                ))
            result["bindings"] = bindings
        return result

    def _capsule_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": CAPSULE_SCHEMA,
            "capsule_id": row["capsule_id"],
            "digest": row["digest"],
            "kind": row["kind"],
            "subject_refs": _loads(row["subject_refs_json"], []),
            "authority_ref": row["authority_ref"],
            "trust_class": row["trust_class"],
            "tainted": bool(row["tainted"]),
            "sensitivity": row["sensitivity"],
            "license": row["license"],
            "retention_class": row["retention_class"],
            "origin": _loads(row["origin_json"], {}),
            "source_digests": _loads(row["source_digests_json"], {}),
            "policy_ref": row["policy_ref"],
            "locale": row["locale"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "recorded_at": row["recorded_at"],
            "supersedes_refs": _loads(row["supersedes_refs_json"], []),
            "artifact_ref": row["artifact_ref"],
            "artifact_bytes": int(row["artifact_bytes"]),
            "metadata": _loads(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "revoked_at": row["revoked_at"],
            "revocation_reason": row["revocation_reason"],
        }

    def get_capsule(self, capsule_id: str, *, include_content: bool = False) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM capsules WHERE capsule_id = ?", (_text(capsule_id),)).fetchone()
        if row is None:
            raise KeyError(f"context capsule not found: {capsule_id}")
        result = self._capsule_row(row)
        if include_content:
            result["artifact"] = self.get_artifact(result["artifact_ref"])
        return result

    def list_capsules(
        self,
        *,
        subject_ref: str | None = None,
        kind: str | None = None,
        trust_class: str | None = None,
        search: str | None = None,
        include_revoked: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = [] if include_revoked else ["c.revoked_at IS NULL"]
        params: list[Any] = []
        if kind:
            clauses.append("c.kind = ?")
            params.append(_text(kind))
        if trust_class:
            clauses.append("c.trust_class = ?")
            params.append(_text(trust_class))
        token = _text(subject_ref)
        if token:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(c.subject_refs_json) subjects WHERE subjects.value = ?)"
            )
            params.append(token)
        search_terms = [item for item in _text(search).lower().split() if item]
        for term in search_terms:
            clauses.append("LOWER(s.search_text) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_token(term)}%")
        join = " JOIN capsule_search s ON s.capsule_id = c.capsule_id" if search_terms else ""
        query = "SELECT c.* FROM capsules c" + join
        query += " WHERE " + " AND ".join(clauses) if clauses else ""
        query += " ORDER BY c.recorded_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            items = [self._capsule_row(row) for row in connection.execute(query, params).fetchall()]
        return items

    def add_relationship(self, relationship: Mapping[str, Any]) -> dict[str, Any]:
        request = dict(relationship)
        source_id = _text(request.get("from_capsule_id") or request.get("from_ref"))
        target_id = _text(request.get("to_capsule_id") or request.get("to_ref"))
        relation_type = _text(request.get("relation_type") or request.get("type"))
        if not source_id or not target_id or not relation_type:
            raise ValueError("context relationship requires from, to, and relation_type")
        source = self.get_capsule(source_id)
        target = self.get_capsule(target_id)
        required = bool(request.get("required", True))
        propagate_taint = bool(request.get("propagate_taint", True))
        valid_from = _text(request.get("valid_from")) or _now()
        recorded_at = _text(request.get("recorded_at")) or _now()
        identity = {
            "schema": RELATIONSHIP_SCHEMA,
            "from_capsule_id": source["capsule_id"],
            "to_capsule_id": target["capsule_id"],
            "relation_type": relation_type,
            "required": required,
            "propagate_taint": propagate_taint,
            "valid_from": valid_from,
            "valid_to": _text(request.get("valid_to")) or None,
            "recorded_at": recorded_at,
            "metadata": _mapping(request.get("metadata")),
        }
        digest = _digest(identity)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM relationships WHERE digest = ?", (digest,)).fetchone()
            if row is None:
                relationship_id = f"ctxrel.{new_id()}"
                connection.execute(
                    "INSERT INTO relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        relationship_id, digest, source_id, target_id, relation_type,
                        int(required), int(propagate_taint), valid_from, identity["valid_to"],
                        recorded_at, _json(identity["metadata"]),
                    ),
                )
                row = connection.execute("SELECT * FROM relationships WHERE relationship_id = ?", (relationship_id,)).fetchone()
        return self._relationship_row(row)

    def _relationship_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": RELATIONSHIP_SCHEMA,
            "relationship_id": row["relationship_id"],
            "digest": row["digest"],
            "from_capsule_id": row["from_capsule_id"],
            "to_capsule_id": row["to_capsule_id"],
            "relation_type": row["relation_type"],
            "required": bool(row["required"]),
            "propagate_taint": bool(row["propagate_taint"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "recorded_at": row["recorded_at"],
            "metadata": _loads(row["metadata_json"], {}),
            "revoked_at": row["revoked_at"],
        }

    def list_relationships(
        self,
        *,
        from_capsule_id: str | None = None,
        to_capsule_id: str | None = None,
        relation_type: str | None = None,
        include_revoked: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = [] if include_revoked else ["revoked_at IS NULL"]
        params: list[Any] = []
        for column, value in (
            ("from_capsule_id", from_capsule_id),
            ("to_capsule_id", to_capsule_id),
            ("relation_type", relation_type),
        ):
            if _text(value):
                clauses.append(f"{column} = ?")
                params.append(_text(value))
        query = "SELECT * FROM relationships" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            return [self._relationship_row(row) for row in connection.execute(query, params).fetchall()]

    def bind_subject(
        self,
        *,
        subject_ref: str,
        capsule_id: str,
        purpose: str = "*",
        audience: str = "*",
        branch: str = "main",
        expected_revision: int | None = None,
        actor_ref: str = "system",
        reason: str = "updated",
        valid_from: str | None = None,
    ) -> dict[str, Any]:
        subject_ref = _text(subject_ref)
        purpose = _text(purpose) or "*"
        audience = _text(audience) or "*"
        branch = _text(branch) or "main"
        capsule = self.get_capsule(capsule_id)
        if subject_ref not in capsule["subject_refs"]:
            raise ValueError("binding subject must be declared by the capsule")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM bindings WHERE subject_ref = ? AND purpose = ? AND audience = ? AND branch = ?",
                (subject_ref, purpose, audience, branch),
            ).fetchone()
            revision = int(current["revision"]) if current else 0
            if expected_revision is not None and revision != int(expected_revision):
                raise ContextConflict(f"subject binding revision conflict: expected {expected_revision}, current {revision}")
            binding_id = str(current["binding_id"]) if current else f"ctxbind.{new_id()}"
            next_revision = revision + 1
            changed_at = _text(valid_from) or _now()
            if current:
                connection.execute(
                    "UPDATE binding_events SET valid_to = ? WHERE binding_id = ? AND valid_to IS NULL",
                    (changed_at, binding_id),
                )
                connection.execute(
                    "UPDATE bindings SET capsule_id = ?, revision = ?, updated_at = ? WHERE binding_id = ?",
                    (capsule_id, next_revision, changed_at, binding_id),
                )
            else:
                connection.execute(
                    "INSERT INTO bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (binding_id, subject_ref, purpose, audience, branch, capsule_id, next_revision, changed_at),
                )
            event_id = f"ctxbevt.{new_id()}"
            connection.execute(
                "INSERT INTO binding_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    event_id, binding_id, subject_ref, purpose, audience, branch,
                    capsule_id, next_revision, changed_at, _now(), _text(actor_ref) or "system", _text(reason) or "updated",
                ),
            )
        return self.get_binding(subject_ref=subject_ref, purpose=purpose, audience=audience, branch=branch)

    def get_binding(
        self,
        *,
        subject_ref: str,
        purpose: str = "*",
        audience: str = "*",
        branch: str = "main",
        as_of: str | None = None,
    ) -> dict[str, Any]:
        params = (_text(subject_ref), _text(purpose) or "*", _text(audience) or "*", _text(branch) or "main")
        with self._connect() as connection:
            if as_of:
                rows = connection.execute(
                    """SELECT * FROM binding_events
                       WHERE subject_ref = ? AND purpose = ? AND audience = ? AND branch = ?
                       ORDER BY revision DESC""",
                    params,
                ).fetchall()
                instant = _instant(as_of)
                row = next(
                    (
                        candidate
                        for candidate in rows
                        if _applies_at(candidate["valid_from"], candidate["valid_to"], as_of)
                        and _instant(candidate["recorded_at"]) <= instant
                    ),
                    None,
                )
            else:
                row = connection.execute(
                    "SELECT * FROM bindings WHERE subject_ref = ? AND purpose = ? AND audience = ? AND branch = ?",
                    params,
                ).fetchone()
        if row is None:
            raise KeyError(f"context binding not found: {params[0]}")
        return {
            "schema": BINDING_SCHEMA,
            "binding_id": row["binding_id"],
            "subject_ref": row["subject_ref"],
            "purpose": row["purpose"],
            "audience": row["audience"],
            "branch": row["branch"],
            "capsule_id": row["capsule_id"],
            "revision": int(row["revision"]),
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else row["recorded_at"],
        }

    def list_bindings(
        self,
        *,
        subject_ref: str | None = None,
        purpose: str | None = None,
        audience: str | None = None,
        branch: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("subject_ref", subject_ref),
            ("purpose", purpose),
            ("audience", audience),
            ("branch", branch),
        ):
            if _text(value):
                clauses.append(f"{column} = ?")
                params.append(_text(value))
        query = "SELECT * FROM bindings" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "schema": BINDING_SCHEMA,
                "binding_id": row["binding_id"],
                "subject_ref": row["subject_ref"],
                "purpose": row["purpose"],
                "audience": row["audience"],
                "branch": row["branch"],
                "capsule_id": row["capsule_id"],
                "revision": int(row["revision"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def compare_bindings(
        self,
        *,
        subject_ref: str,
        purpose: str = "*",
        audience: str = "*",
        left_branch: str = "main",
        right_branch: str = "main",
    ) -> dict[str, Any]:
        def load(branch: str) -> dict[str, Any] | None:
            try:
                return self.get_binding(
                    subject_ref=subject_ref,
                    purpose=purpose,
                    audience=audience,
                    branch=branch,
                )
            except KeyError:
                return None

        left = load(_text(left_branch) or "main")
        right = load(_text(right_branch) or "main")
        if left is None or right is None:
            status = "missing"
        elif left["capsule_id"] == right["capsule_id"]:
            status = "same"
        else:
            status = "diverged"
        return {
            "schema": "adaos.context.binding_comparison.v1",
            "subject_ref": _text(subject_ref),
            "purpose": _text(purpose) or "*",
            "audience": _text(audience) or "*",
            "left_branch": _text(left_branch) or "main",
            "right_branch": _text(right_branch) or "main",
            "status": status,
            "left": left,
            "right": right,
        }

    def merge_binding(
        self,
        *,
        subject_ref: str,
        source_branch: str,
        target_branch: str = "main",
        purpose: str = "*",
        audience: str = "*",
        base_capsule_id: str | None = None,
        expected_target_revision: int | None = None,
        actor_ref: str = "system",
        reason: str = "branch_merge",
    ) -> dict[str, Any]:
        source = self.get_binding(
            subject_ref=subject_ref,
            purpose=purpose,
            audience=audience,
            branch=source_branch,
        )
        try:
            target = self.get_binding(
                subject_ref=subject_ref,
                purpose=purpose,
                audience=audience,
                branch=target_branch,
            )
        except KeyError:
            target = None
        if target and target["capsule_id"] == source["capsule_id"]:
            return {
                "schema": "adaos.context.binding_merge.v1",
                "status": "noop",
                "source": source,
                "target": target,
            }
        base = _text(base_capsule_id)
        if target and base and target["capsule_id"] != base:
            raise ContextConflict(
                "context branch merge conflict: target binding changed since the declared base"
            )
        if target and expected_target_revision is not None and target["revision"] != int(expected_target_revision):
            raise ContextConflict(
                "context branch merge conflict: target revision changed"
            )
        merged = self.bind_subject(
            subject_ref=subject_ref,
            capsule_id=source["capsule_id"],
            purpose=purpose,
            audience=audience,
            branch=target_branch,
            expected_revision=(
                int(expected_target_revision)
                if expected_target_revision is not None
                else int(target["revision"]) if target else 0
            ),
            actor_ref=actor_ref,
            reason=reason,
        )
        return {
            "schema": "adaos.context.binding_merge.v1",
            "status": "merged",
            "source": source,
            "target": merged,
        }

    def resolve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        query = dict(request)
        subject_refs = _strings(query.get("subject_refs"))
        compatibility_scope = _text(query.get("scope_ref"))
        if not subject_refs and compatibility_scope:
            subject_refs = [compatibility_scope if ":" in compatibility_scope else f"project:{compatibility_scope}"]
        if not subject_refs:
            raise ValueError("context resolution requires subject_refs")
        purpose = _text(query.get("purpose")) or "general"
        audience = _text(query.get("audience")) or "agent"
        branch = _text(query.get("branch")) or "main"
        as_of = _text(query.get("as_of")) or _now()
        policy = _mapping(query.get("policy"))
        allowed_sensitivity = set(_strings(policy.get("allowed_sensitivity") or ["public", "subnet", "workspace"]))
        allowed_licenses = set(_strings(policy.get("allowed_licenses")))
        minimum_trust = _text(policy.get("minimum_trust")) or "untrusted"
        if minimum_trust not in _TRUST_ORDER:
            raise ValueError(f"unsupported minimum_trust: {minimum_trust}")
        allow_tainted = bool(policy.get("allow_tainted", False))

        roots: list[str] = []
        unavailable: list[dict[str, Any]] = []
        for subject_ref in subject_refs:
            if subject_ref.startswith("ctxcap."):
                roots.append(subject_ref)
                continue
            found = None
            for candidate in ((purpose, audience), (purpose, "*"), ("*", audience), ("*", "*")):
                try:
                    found = self.get_binding(
                        subject_ref=subject_ref,
                        purpose=candidate[0],
                        audience=candidate[1],
                        branch=branch,
                        as_of=as_of if query.get("as_of") else None,
                    )
                    break
                except KeyError:
                    continue
            if found:
                roots.append(found["capsule_id"])
            else:
                unavailable.append({"ref": subject_ref, "reason": "binding_not_found"})

        selected: list[dict[str, Any]] = []
        denied: list[dict[str, Any]] = []
        omitted: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        invalidations: list[dict[str, Any]] = []
        seen: set[str] = set()
        queue: deque[tuple[str, bool, list[str], bool]] = deque((root, True, [], False) for root in roots)
        root_projects = {ref for ref in subject_refs if ref.startswith("project:")}
        with self._connect() as connection:
            while queue:
                capsule_id, required, path, inherited_taint = queue.popleft()
                if capsule_id in seen:
                    continue
                seen.add(capsule_id)
                try:
                    capsule = self.get_capsule(capsule_id)
                except KeyError:
                    unavailable.append({"ref": capsule_id, "reason": "capsule_not_found", "required": required})
                    continue
                reason = self._admission_reason(
                    capsule,
                    as_of=as_of,
                    allowed_sensitivity=allowed_sensitivity,
                    allowed_licenses=allowed_licenses,
                    minimum_trust=minimum_trust,
                    allow_tainted=allow_tainted,
                    inherited_taint=inherited_taint,
                    root_projects=root_projects,
                    path=path,
                )
                applicable_invalidations = self._applicable_invalidations(
                    connection,
                    capsule=capsule,
                    as_of=as_of,
                )
                if applicable_invalidations and not reason:
                    reason = "source_invalidated"
                unit = {
                    "ref": capsule_id,
                    "kind": capsule["kind"],
                    "subject_refs": capsule["subject_refs"],
                    "digest": capsule["digest"],
                    "artifact_ref": capsule["artifact_ref"],
                    "bytes": capsule["artifact_bytes"],
                    "estimated_tokens": _estimate_tokens(capsule["artifact_bytes"]),
                    "required": required,
                    "trust_class": capsule["trust_class"],
                    "tainted": bool(capsule["tainted"] or inherited_taint),
                    "sensitivity": capsule["sensitivity"],
                    "path": path,
                    "reason": "subject_binding" if not path else "dependency_closure",
                    "utility": float(capsule["metadata"].get("utility", 1.0)),
                }
                if reason:
                    denial = {**unit, "reason": reason}
                    if applicable_invalidations:
                        denial["invalidation_refs"] = [
                            item["invalidation_id"] for item in applicable_invalidations
                        ]
                        denial["invalidation_reasons"] = [
                            item["reason"] for item in applicable_invalidations
                        ]
                        invalidations.extend(applicable_invalidations)
                    denied.append(denial)
                    continue
                selected.append(unit)
                rows = connection.execute(
                    "SELECT * FROM relationships WHERE from_capsule_id = ? AND revoked_at IS NULL ORDER BY relation_type, relationship_id",
                    (capsule_id,),
                ).fetchall()
                for row in rows:
                    edge = self._relationship_row(row)
                    edge_invalidations = self._applicable_invalidations(
                        connection,
                        capsule=capsule,
                        as_of=as_of,
                        edge_type=edge["relation_type"],
                        recorded_after=edge["recorded_at"],
                    )
                    if edge_invalidations:
                        invalidations.extend(edge_invalidations)
                        omitted.append({
                            "ref": edge["relationship_id"],
                            "reason": "relationship_invalidated",
                            "invalidation_refs": [
                                item["invalidation_id"] for item in edge_invalidations
                            ],
                        })
                        continue
                    if (
                        not _applies_at(edge["valid_from"], edge["valid_to"], as_of)
                        or _instant(edge["recorded_at"]) > _instant(as_of)
                    ):
                        omitted.append({"ref": edge["relationship_id"], "reason": "relationship_not_effective"})
                        continue
                    relationships.append(edge)
                    queue.append((
                        edge["to_capsule_id"],
                        bool(required and edge["required"]),
                        [*path, edge["relationship_id"]],
                        bool(inherited_taint or (unit["tainted"] and edge["propagate_taint"])),
                    ))

        resolution_body = {
            "schema": "adaos.context.resolution.v1",
            "subject_refs": subject_refs,
            "purpose": purpose,
            "audience": audience,
            "branch": branch,
            "as_of": as_of,
            "policy": policy,
            "required": [item for item in selected if item["required"]],
            "candidates": [item for item in selected if not item["required"]],
            "omitted": omitted,
            "denied": denied,
            "unavailable": unavailable,
            "relationships": relationships,
            "invalidations": list({
                item["invalidation_id"]: item for item in invalidations
            }.values()),
            "created_at": _now(),
        }
        artifact = self.put_artifact(resolution_body)
        return {
            **resolution_body,
            "resolution_id": f"ctxres.{new_id()}",
            "resolution_ref": artifact["ref"],
            "digest": artifact["digest"],
            "status": "insufficient" if unavailable or any(item.get("required") for item in denied) else "ready",
        }

    def _admission_reason(
        self,
        capsule: Mapping[str, Any],
        *,
        as_of: str,
        allowed_sensitivity: set[str],
        allowed_licenses: set[str],
        minimum_trust: str,
        allow_tainted: bool,
        inherited_taint: bool,
        root_projects: set[str],
        path: Sequence[str],
    ) -> str | None:
        as_of_instant = _instant(as_of)
        if capsule.get("revoked_at") and _instant(capsule["revoked_at"]) <= as_of_instant:
            return "revoked"
        if not _applies_at(str(capsule["valid_from"]), capsule.get("valid_to"), as_of):
            return "not_effective"
        if _instant(str(capsule["recorded_at"])) > as_of_instant:
            return "not_recorded_as_of"
        if capsule["sensitivity"] not in allowed_sensitivity:
            return "sensitivity_denied"
        if allowed_licenses and capsule["license"] not in allowed_licenses:
            return "license_denied"
        if _TRUST_ORDER[capsule["trust_class"]] < _TRUST_ORDER[minimum_trust]:
            return "trust_below_policy"
        if (capsule["tainted"] or inherited_taint) and not allow_tainted:
            return "tainted"
        capsule_projects = {ref for ref in capsule["subject_refs"] if str(ref).startswith("project:")}
        if path and root_projects and capsule_projects and capsule_projects.isdisjoint(root_projects) and capsule["kind"] not in _SHARED_KINDS:
            return "cross_project_dependency_denied"
        return None

    def _applicable_invalidations(
        self,
        connection: sqlite3.Connection,
        *,
        capsule: Mapping[str, Any],
        as_of: str,
        edge_type: str | None = None,
        recorded_after: str | None = None,
    ) -> list[dict[str, Any]]:
        subject_refs = _strings(capsule.get("subject_refs"))
        if not subject_refs:
            return []
        placeholders = ",".join("?" for _ in subject_refs)
        rows = connection.execute(
            f"""SELECT * FROM invalidations
                WHERE subject_ref IN ({placeholders})
                ORDER BY recorded_at DESC""",
            subject_refs,
        ).fetchall()
        capsule_recorded_at = _instant(_text(recorded_after) or _text(capsule.get("recorded_at")))
        as_of_instant = _instant(as_of)
        source_values = {
            _text(value)
            for value in _mapping(capsule.get("source_digests")).values()
            if _text(value)
        }
        source_values.add(_text(capsule.get("digest")))
        expected_edge = _text(edge_type)
        result: list[dict[str, Any]] = []
        for row in rows:
            if _instant(row["recorded_at"]) > as_of_instant:
                continue
            row_edge = _text(row["edge_type"])
            if expected_edge:
                if row_edge != expected_edge:
                    continue
            elif row_edge:
                continue
            if _instant(row["recorded_at"]) < capsule_recorded_at:
                continue
            current_source_digest = _text(row["source_digest"])
            if current_source_digest and current_source_digest in source_values:
                continue
            result.append(self._invalidation_row(row))
        return result

    def plan(self, request: Mapping[str, Any]) -> dict[str, Any]:
        query = dict(request)
        resolution = query.get("resolution")
        if not isinstance(resolution, Mapping):
            resolution_ref = _text(query.get("resolution_ref"))
            if not resolution_ref:
                raise ValueError("context plan requires resolution or resolution_ref")
            resolution = self.get_artifact(resolution_ref)
        resolution = dict(resolution)
        token_budget = max(0, int(query.get("token_budget") or 0))
        if token_budget <= 0:
            token_budget = 16_000
        required = _mappings(resolution.get("required"))
        candidates = _mappings(resolution.get("candidates"))
        required.sort(key=lambda item: (len(item.get("path") or []), _text(item.get("kind")), _text(item.get("ref"))))
        candidates.sort(key=lambda item: (-float(item.get("utility") or 0), int(item.get("estimated_tokens") or 0), _text(item.get("ref"))))
        required_estimated_tokens = sum(
            max(1, int(item.get("estimated_tokens") or 1)) for item in required
        )
        selected: list[dict[str, Any]] = []
        omitted = _mappings(resolution.get("omitted"))
        used = 0
        insufficient = False
        for unit in [*required, *candidates]:
            cost = max(1, int(unit.get("estimated_tokens") or 1))
            if used + cost <= token_budget:
                selected.append({**unit, "selection_reason": "required" if unit.get("required") else "utility_per_token"})
                used += cost
            else:
                omitted.append({**unit, "reason": "token_budget"})
                if unit.get("required"):
                    insufficient = True
        omitted_required_refs = [
            _text(item.get("ref"))
            for item in omitted
            if item.get("required") and _text(item.get("ref"))
        ]
        canonical = {
            "schema": PLAN_SCHEMA,
            "resolution_ref": _text(query.get("resolution_ref") or resolution.get("resolution_ref")) or f"digest:{_digest(resolution)}",
            "subject_refs": _strings(resolution.get("subject_refs")),
            "purpose": _text(resolution.get("purpose")),
            "audience": _text(resolution.get("audience")),
            "model_profile": _mapping(query.get("model_profile")),
            "policy_ref": _text(query.get("policy_ref")) or None,
            "token_budget": token_budget,
            "estimated_tokens": used,
            "required_estimated_tokens": required_estimated_tokens,
            "omitted_required_refs": omitted_required_refs,
            "selected": selected,
            "omitted": omitted,
            "denied": _mappings(resolution.get("denied")),
            "unavailable": _mappings(resolution.get("unavailable")),
            "cache_partition": _digest({
                "audience": resolution.get("audience"),
                "policy": resolution.get("policy"),
                "model_profile": query.get("model_profile") or {},
            }),
            "created_at": _now(),
            "status": "insufficient" if insufficient or resolution.get("status") == "insufficient" else "ready",
        }
        artifact = self.put_artifact(canonical)
        plan_id = f"ctxplan.{new_id()}"
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM context_plans WHERE digest = ?", (artifact["digest"],)).fetchone()
            if existing:
                plan_id = str(existing["plan_id"])
                artifact_ref = str(existing["artifact_ref"])
            else:
                artifact_ref = artifact["ref"]
                connection.execute(
                    "INSERT INTO context_plans VALUES (?, ?, ?, ?, ?)",
                    (plan_id, artifact["digest"], canonical["resolution_ref"], artifact_ref, canonical["created_at"]),
                )
        return {**canonical, "plan_id": plan_id, "plan_ref": artifact_ref, "digest": artifact["digest"]}

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM context_plans WHERE plan_id = ?", (_text(plan_id),)).fetchone()
        if row is None:
            raise KeyError(f"context plan not found: {plan_id}")
        return {**self.get_artifact(row["artifact_ref"]), "plan_id": row["plan_id"], "plan_ref": row["artifact_ref"], "digest": row["digest"]}

    def list_plans(self, *, subject_ref: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM context_plans ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 2000)),),
            ).fetchall()
        items = [
            {**self.get_artifact(row["artifact_ref"]), "plan_id": row["plan_id"], "plan_ref": row["artifact_ref"], "digest": row["digest"]}
            for row in rows
        ]
        token = _text(subject_ref)
        return [item for item in items if token in item.get("subject_refs", [])] if token else items

    def compile(self, request: Mapping[str, Any]) -> dict[str, Any]:
        query = dict(request)
        plan = query.get("plan")
        if not isinstance(plan, Mapping):
            plan_id = _text(query.get("plan_id"))
            plan_ref = _text(query.get("plan_ref"))
            plan = self.get_plan(plan_id) if plan_id else self.get_artifact(plan_ref)
        plan = dict(plan)
        output_format = _text(query.get("output_format")) or "json"
        if output_format not in {"json", "min_json", "jsonl", "toon"}:
            raise ValueError(f"unsupported context output_format: {output_format}")
        units: list[dict[str, Any]] = []
        for selected in _mappings(plan.get("selected")):
            artifact = self.get_artifact(_text(selected.get("artifact_ref")))
            units.append({
                "ref": selected.get("ref"),
                "kind": selected.get("kind"),
                "subject_refs": _strings(selected.get("subject_refs")),
                "digest": selected.get("digest"),
                "trust_class": selected.get("trust_class"),
                "tainted": selected.get("tainted"),
                "content": artifact,
            })
        stable = [item for item in units if item.get("kind") in _SHARED_KINDS]
        mutable = [item for item in units if item.get("kind") not in _SHARED_KINDS]
        canonical = {
            "schema": COMPILED_PACKET_SCHEMA,
            "plan_id": plan.get("plan_id"),
            "subject_refs": plan.get("subject_refs") or [],
            "purpose": plan.get("purpose"),
            "audience": plan.get("audience"),
            "role_authority": _mapping(query.get("role_authority")),
            "stable_prefix": stable,
            "task_context": mutable,
            "output_contract": _mapping(query.get("output_contract")),
        }
        artifact = self.put_artifact(canonical)
        full_model_text = self._render_compilation_text(
            canonical,
            output_format=output_format,
        )
        model_text = full_model_text
        projection_ref = artifact["ref"]
        projection_digest = artifact["digest"]
        delta_mode = "full"
        delta_summary: dict[str, Any] | None = None
        base_packet_ref = _text(query.get("base_packet_ref"))
        if base_packet_ref:
            base_packet = self.get_artifact(base_packet_ref)
            if not isinstance(base_packet, Mapping):
                raise ValueError("base context packet must be an object")
            delta = self._compiled_packet_delta(
                canonical,
                base_packet,
                base_packet_ref=base_packet_ref,
                target_packet_ref=artifact["ref"],
                target_packet_digest=artifact["digest"],
            )
            delta_artifact = self.put_artifact(delta)
            delta_text = self._render_compilation_text(
                delta,
                output_format=output_format,
            )
            delta_summary = {
                "changed": len(delta["changed"]),
                "removed": len(delta["removed"]),
                "unchanged": len(delta["unchanged"]),
                "delta_ref": delta_artifact["ref"],
                "delta_digest": delta_artifact["digest"],
                "full_bytes": len(full_model_text.encode("utf-8")),
                "delta_bytes": len(delta_text.encode("utf-8")),
            }
            if delta_summary["delta_bytes"] < delta_summary["full_bytes"]:
                model_text = delta_text
                projection_ref = delta_artifact["ref"]
                projection_digest = delta_artifact["digest"]
                delta_mode = "delta"
            delta_summary["saved_bytes"] = max(
                0,
                delta_summary["full_bytes"] - len(model_text.encode("utf-8")),
            )
        layer_usage = [
            {
                "layer": name,
                "unit_count": len(items),
                "unique_bytes": len(_canonical_bytes(items)),
                "estimated_tokens": _estimate_tokens(len(_canonical_bytes(items))),
            }
            for name, items in (("stable_prefix", stable), ("task_context", mutable))
        ]
        layer_usage.append(
            {
                "layer": "model_projection",
                "unit_count": len(units),
                "unique_bytes": len(model_text.encode("utf-8")),
                "estimated_tokens": _estimate_tokens(len(model_text.encode("utf-8"))),
                "delta_mode": delta_mode,
            }
        )
        return {
            "schema": "adaos.context.compilation.v1",
            "canonical_format": "json",
            "model_text_format": output_format,
            "packet_ref": artifact["ref"],
            "packet_digest": artifact["digest"],
            "model_projection_ref": projection_ref,
            "model_projection_digest": projection_digest,
            "delta_mode": delta_mode,
            "base_packet_ref": base_packet_ref or None,
            "delta": delta_summary,
            "stable_prefix_digest": _digest(stable),
            "model_text": model_text,
            "bytes": len(model_text.encode("utf-8")),
            "token_estimate": _estimate_tokens(len(model_text.encode("utf-8"))),
            "full_token_estimate": _estimate_tokens(len(full_model_text.encode("utf-8"))),
            "layer_usage": layer_usage,
            "selected_refs": [item.get("ref") for item in units],
            "cache_partition": plan.get("cache_partition"),
        }

    @staticmethod
    def _compiled_packet_delta(
        current: Mapping[str, Any],
        base: Mapping[str, Any],
        *,
        base_packet_ref: str,
        target_packet_ref: str,
        target_packet_digest: str,
    ) -> dict[str, Any]:
        if _text(base.get("schema")) != COMPILED_PACKET_SCHEMA:
            raise ValueError("base context packet has an unsupported schema")
        for field in ("purpose", "audience"):
            if _text(base.get(field)) != _text(current.get(field)):
                raise ValueError(f"base context packet {field} does not match")
        base_projects = {
            ref for ref in _strings(base.get("subject_refs")) if ref.startswith("project:")
        }
        current_projects = {
            ref for ref in _strings(current.get("subject_refs")) if ref.startswith("project:")
        }
        if base_projects and current_projects and base_projects != current_projects:
            raise ContextAccessDenied("base context packet belongs to another project")

        base_units = {_compiled_unit_key(item): item for item in _compiled_units(base)}
        current_units = {_compiled_unit_key(item): item for item in _compiled_units(current)}
        changed = [
            {"logical_ref": key, **item}
            for key, item in current_units.items()
            if key not in base_units
            or _text(base_units[key].get("digest")) != _text(item.get("digest"))
        ]
        removed = [
            {
                "logical_ref": key,
                "ref": item.get("ref"),
                "digest": item.get("digest"),
            }
            for key, item in base_units.items()
            if key not in current_units
        ]
        unchanged = [
            {
                "logical_ref": key,
                "ref": item.get("ref"),
                "digest": item.get("digest"),
            }
            for key, item in current_units.items()
            if key in base_units
            and _text(base_units[key].get("digest")) == _text(item.get("digest"))
        ]
        replacement_sections = {
            field: current.get(field)
            for field in ("role_authority", "output_contract")
            if current.get(field) != base.get(field)
        }
        return {
            "schema": DELTA_SCHEMA,
            "base_packet_ref": base_packet_ref,
            "base_packet_digest": _digest(base),
            "target_packet_ref": target_packet_ref,
            "target_packet_digest": target_packet_digest,
            "subject_refs": _strings(current.get("subject_refs")),
            "purpose": _text(current.get("purpose")),
            "audience": _text(current.get("audience")),
            "changed": changed,
            "removed": removed,
            "unchanged": unchanged,
            "replacement_sections": replacement_sections,
        }

    def _render_compilation_text(
        self,
        payload: Mapping[str, Any],
        *,
        output_format: str,
    ) -> str:
        if output_format == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if output_format == "min_json":
            return _json(payload)
        if _text(payload.get("schema")) == DELTA_SCHEMA:
            delta_head = {
                key: payload.get(key)
                for key in (
                    "schema",
                    "base_packet_ref",
                    "base_packet_digest",
                    "target_packet_ref",
                    "target_packet_digest",
                    "purpose",
                    "audience",
                    "removed",
                    "unchanged",
                    "replacement_sections",
                )
            }
            if output_format == "jsonl":
                return "\n".join(
                    [_json(delta_head), *[_json(item) for item in _mappings(payload.get("changed"))]]
                )
            return _json(delta_head) + "\n" + self._toon(_mappings(payload.get("changed")))
        units = _compiled_units(payload)
        if output_format == "jsonl":
            return "\n".join(_json(item) for item in units)
        return self._toon(units)

    @staticmethod
    def _toon(units: Sequence[Mapping[str, Any]]) -> str:
        header = "ref\tkind\ttrust\ttainted\tsummary"
        rows = [header]
        for unit in units:
            content = _mapping(unit.get("content"))
            summary = str(content.get("summary") or "").replace("\t", " ").replace("\n", " ")
            rows.append("\t".join((
                _text(unit.get("ref")), _text(unit.get("kind")), _text(unit.get("trust_class")),
                "1" if unit.get("tainted") else "0", summary,
            )))
        return "\n".join(rows)

    def record_receipt(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        request = dict(receipt)
        run_ref = _text(request.get("run_ref"))
        plan_ref = _text(request.get("plan_ref") or request.get("plan_id"))
        if not run_ref or not plan_ref:
            raise ValueError("context receipt requires run_ref and plan_ref")
        usage = _mapping(request.get("usage"))
        provider_input = int(usage.get("provider_input_tokens") or usage.get("input_tokens") or 0)
        cached = int(usage.get("cached_input_tokens") or 0)
        output = int(usage.get("output_tokens") or 0)
        canonical = {
            "schema": RECEIPT_SCHEMA,
            "run_ref": run_ref,
            "plan_ref": plan_ref,
            "subject_refs": _strings(request.get("subject_refs")),
            "purpose": _text(request.get("purpose")),
            "audience": _text(request.get("audience")),
            "selected_refs": _strings(request.get("selected_refs")),
            "omitted": _mappings(request.get("omitted")),
            "denied": _mappings(request.get("denied")),
            "unavailable": _mappings(request.get("unavailable")),
            "context_misses": _mappings(request.get("context_misses")),
            "layer_usage": _mappings(request.get("layer_usage")),
            "usage": {
                **usage,
                "provider_input_tokens": provider_input,
                "cached_input_tokens": cached,
                "fresh_input_tokens": max(0, provider_input - cached),
                "output_tokens": output,
                "fresh_plus_output": max(0, provider_input - cached) + output,
                "cache_ratio": round(cached / provider_input, 6) if provider_input else 0.0,
            },
            "tool_boundary_count": max(0, int(request.get("tool_boundary_count") or 0)),
            "source_slice_coverage": request.get("source_slice_coverage"),
            "execution_route": _text(request.get("execution_route")) or "unknown",
            "validation": _mapping(request.get("validation")),
            "evidence_refs": _mappings(request.get("evidence_refs")),
            "latency_ms": max(0, int(request.get("latency_ms") or 0)),
            "created_at": _text(request.get("created_at")) or _now(),
        }
        artifact = self.put_artifact(canonical)
        receipt_id = f"ctxreceipt.{new_id()}"
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM context_receipts WHERE digest = ?", (artifact["digest"],)).fetchone()
            if existing:
                receipt_id = str(existing["receipt_id"])
                artifact_ref = str(existing["artifact_ref"])
            else:
                artifact_ref = artifact["ref"]
                connection.execute(
                    "INSERT INTO context_receipts VALUES (?, ?, ?, ?, ?, ?)",
                    (receipt_id, run_ref, plan_ref, artifact["digest"], artifact_ref, canonical["created_at"]),
                )
        return {**canonical, "receipt_id": receipt_id, "receipt_ref": artifact_ref, "digest": artifact["digest"]}

    def list_receipts(self, *, run_ref: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM context_receipts"
        params: list[Any] = []
        if run_ref:
            query += " WHERE run_ref = ?"
            params.append(_text(run_ref))
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {**self.get_artifact(row["artifact_ref"]), "receipt_id": row["receipt_id"], "receipt_ref": row["artifact_ref"], "digest": row["digest"]}
            for row in rows
        ]

    def propose_memory(self, request: Mapping[str, Any]) -> dict[str, Any]:
        proposal = dict(request)
        kind = _text(proposal.get("kind"))
        if kind not in _MEMORY_KINDS:
            raise ValueError(f"unsupported memory kind: {kind}")
        source_refs = _strings(proposal.get("source_refs"))
        if not source_refs:
            raise ValueError("memory candidate requires source_refs")
        artifact = self.put_artifact({
            "schema": "adaos.context.memory_proposal.v1",
            "summary": proposal.get("summary"),
            "content": proposal.get("content"),
            "source_refs": source_refs,
        })
        now = _now()
        candidate_id = f"ctxmem.{new_id()}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO memory_candidates (
                       candidate_id, kind, status, source_refs_json, proposal_ref,
                       proposed_by, proposed_by_kind, authority_ref, trust_class,
                       tainted, validation_refs_json, qualified_by,
                       promoted_capsule_id, supersedes_candidate_ref, reason,
                       revision, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)""",
                (
                    candidate_id, kind, "proposed", _json(source_refs), artifact["ref"],
                    _text(proposal.get("proposed_by")) or "agent", _text(proposal.get("proposed_by_kind")) or "llm",
                    _text(proposal.get("authority_ref")) or "unassigned", _text(proposal.get("trust_class")) or "observed",
                    int(bool(proposal.get("tainted"))), _json([]), _text(proposal.get("supersedes_candidate_ref")) or None,
                    _text(proposal.get("reason")), 1, now, now,
                ),
            )
        return self.get_memory_candidate(candidate_id)

    def get_memory_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (_text(candidate_id),)).fetchone()
        if row is None:
            raise KeyError(f"memory candidate not found: {candidate_id}")
        return {
            "schema": MEMORY_CANDIDATE_SCHEMA,
            "candidate_id": row["candidate_id"], "kind": row["kind"], "status": row["status"],
            "source_refs": _loads(row["source_refs_json"], []), "proposal_ref": row["proposal_ref"],
            "proposed_by": row["proposed_by"], "proposed_by_kind": row["proposed_by_kind"],
            "authority_ref": row["authority_ref"], "trust_class": row["trust_class"], "tainted": bool(row["tainted"]),
            "validation_refs": _loads(row["validation_refs_json"], []), "qualified_by": row["qualified_by"],
            "promoted_capsule_id": row["promoted_capsule_id"], "supersedes_candidate_ref": row["supersedes_candidate_ref"],
            "reason": row["reason"], "revision": int(row["revision"]), "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_memory_candidates(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT candidate_id FROM memory_candidates"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(_text(status))
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            ids = [row["candidate_id"] for row in connection.execute(query, params).fetchall()]
        return [self.get_memory_candidate(item) for item in ids]

    def qualify_memory(
        self,
        candidate_id: str,
        *,
        validation_refs: Sequence[str],
        qualified_by: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        candidate = self.get_memory_candidate(candidate_id)
        refs = _strings(validation_refs)
        if not refs:
            raise ValueError("memory qualification requires validation_refs")
        if candidate["proposed_by_kind"] == "llm" and _text(qualified_by) == candidate["proposed_by"]:
            raise ContextAccessDenied("an LLM memory proposer cannot qualify its own candidate")
        if expected_revision is not None and candidate["revision"] != int(expected_revision):
            raise ContextConflict("memory candidate revision conflict")
        if candidate["status"] not in {"proposed", "rejected"}:
            raise ContextConflict(f"memory candidate cannot be qualified from {candidate['status']}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE memory_candidates SET status = 'qualified', validation_refs_json = ?, qualified_by = ?, revision = ?, updated_at = ? WHERE candidate_id = ?",
                (_json(refs), _text(qualified_by), candidate["revision"] + 1, _now(), candidate_id),
            )
        return self.get_memory_candidate(candidate_id)

    def promote_memory(
        self,
        candidate_id: str,
        *,
        actor_ref: str,
        subject_refs: Sequence[str],
        bind: bool = True,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        candidate = self.get_memory_candidate(candidate_id)
        if candidate["status"] != "qualified":
            raise ContextConflict("memory candidate must be qualified before promotion")
        if expected_revision is not None and candidate["revision"] != int(expected_revision):
            raise ContextConflict("memory candidate revision conflict")
        if _text(actor_ref) == candidate["proposed_by"]:
            raise ContextAccessDenied("memory proposer cannot promote its own candidate")
        if candidate["tainted"] and not any("sanit" in ref.lower() for ref in candidate["validation_refs"]):
            raise ContextAccessDenied("tainted memory requires sanitization validation")
        proposal = self.get_artifact(candidate["proposal_ref"])
        capsule = self.register_capsule({
            "kind": f"{candidate['kind']}_memory",
            "subject_refs": list(subject_refs),
            "authority_ref": candidate["authority_ref"],
            "trust_class": "accepted" if candidate["kind"] in {"authoritative", "procedural"} else "validated",
            "tainted": False,
            "sensitivity": "workspace",
            "retention_class": "accepted_memory" if candidate["kind"] != "working" else "working",
            "origin": {"memory_candidate_ref": candidate_id, "validation_refs": candidate["validation_refs"], "sanitization_evidence_refs": candidate["validation_refs"] if candidate["tainted"] else []},
            "summary": proposal.get("summary"),
            "content": proposal.get("content"),
            "metadata": {"memory_kind": candidate["kind"], "source_refs": candidate["source_refs"]},
            "bind": bind,
            "actor_ref": actor_ref,
        }, bind=bind)
        with self._connect() as connection:
            connection.execute(
                "UPDATE memory_candidates SET status = 'promoted', promoted_capsule_id = ?, revision = ?, updated_at = ? WHERE candidate_id = ?",
                (capsule["capsule_id"], candidate["revision"] + 1, _now(), candidate_id),
            )
        return {"candidate": self.get_memory_candidate(candidate_id), "capsule": capsule}

    def revoke_capsule(self, capsule_id: str, *, actor_ref: str, reason: str) -> dict[str, Any]:
        if not _text(reason):
            raise ValueError("capsule revocation requires a reason")
        capsule = self.get_capsule(capsule_id)
        if capsule.get("revoked_at"):
            return capsule
        with self._connect() as connection:
            connection.execute(
                "UPDATE capsules SET revoked_at = ?, revocation_reason = ? WHERE capsule_id = ?",
                (_now(), _text(reason), capsule_id),
            )
        return self.get_capsule(capsule_id)

    def rollback_memory(
        self,
        candidate_id: str,
        *,
        actor_ref: str,
        reason: str,
        restore_capsule_id: str | None = None,
    ) -> dict[str, Any]:
        candidate = self.get_memory_candidate(candidate_id)
        if candidate["status"] != "promoted" or not candidate["promoted_capsule_id"]:
            raise ContextConflict("only promoted memory can be rolled back")
        revoked = self.revoke_capsule(candidate["promoted_capsule_id"], actor_ref=actor_ref, reason=reason)
        restored: list[dict[str, Any]] = []
        if restore_capsule_id:
            restore = self.get_capsule(restore_capsule_id)
            for subject_ref in restore["subject_refs"]:
                restored.append(self.bind_subject(
                    subject_ref=subject_ref,
                    capsule_id=restore_capsule_id,
                    actor_ref=actor_ref,
                    reason=f"memory_rollback:{candidate_id}",
                ))
        with self._connect() as connection:
            connection.execute(
                "UPDATE memory_candidates SET status = 'rolled_back', reason = ?, revision = ?, updated_at = ? WHERE candidate_id = ?",
                (_text(reason), candidate["revision"] + 1, _now(), candidate_id),
            )
        return {"candidate": self.get_memory_candidate(candidate_id), "revoked_capsule": revoked, "restored_bindings": restored}

    def invalidate(
        self,
        *,
        subject_ref: str,
        reason: str,
        event_ref: str,
        source_digest: str | None = None,
        edge_type: str | None = None,
    ) -> dict[str, Any]:
        if not _text(subject_ref) or not _text(reason) or not _text(event_ref):
            raise ValueError("context invalidation requires subject_ref, reason, and event_ref")
        payload = {
            "schema": INVALIDATION_SCHEMA,
            "invalidation_id": f"ctxinv.{new_id()}",
            "subject_ref": _text(subject_ref),
            "source_digest": _text(source_digest) or None,
            "edge_type": _text(edge_type) or None,
            "reason": _text(reason),
            "event_ref": _text(event_ref),
            "recorded_at": _now(),
        }
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM invalidations
                   WHERE subject_ref = ? AND event_ref = ?
                     AND COALESCE(source_digest, '') = ?
                     AND COALESCE(edge_type, '') = ?""",
                (
                    payload["subject_ref"],
                    payload["event_ref"],
                    payload["source_digest"] or "",
                    payload["edge_type"] or "",
                ),
            ).fetchone()
            if existing is not None:
                return self._invalidation_row(existing)
            connection.execute(
                "INSERT INTO invalidations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["invalidation_id"],
                    payload["subject_ref"],
                    payload["source_digest"],
                    payload["edge_type"],
                    payload["reason"],
                    payload["event_ref"],
                    payload["recorded_at"],
                ),
            )
        return payload

    @staticmethod
    def _invalidation_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": INVALIDATION_SCHEMA,
            "invalidation_id": row["invalidation_id"],
            "subject_ref": row["subject_ref"],
            "source_digest": row["source_digest"],
            "edge_type": row["edge_type"],
            "reason": row["reason"],
            "event_ref": row["event_ref"],
            "recorded_at": row["recorded_at"],
        }

    def list_invalidations(
        self,
        *,
        subject_ref: str | None = None,
        event_ref: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("subject_ref", subject_ref), ("event_ref", event_ref)):
            if _text(value):
                clauses.append(f"{column} = ?")
                params.append(_text(value))
        query = "SELECT * FROM invalidations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            return [
                self._invalidation_row(row)
                for row in connection.execute(query, params).fetchall()
            ]

    def inspect(self, run_ref: str) -> dict[str, Any]:
        receipts = self.list_receipts(run_ref=run_ref)
        return {
            "schema": "adaos.context.inspection.v1",
            "run_ref": _text(run_ref),
            "receipts": receipts,
            "receipt_count": len(receipts),
            "usage": {
                "provider_input_tokens": sum(int(_mapping(item.get("usage")).get("provider_input_tokens") or 0) for item in receipts),
                "cached_input_tokens": sum(int(_mapping(item.get("usage")).get("cached_input_tokens") or 0) for item in receipts),
                "output_tokens": sum(int(_mapping(item.get("usage")).get("output_tokens") or 0) for item in receipts),
                "fresh_plus_output": sum(int(_mapping(item.get("usage")).get("fresh_plus_output") or 0) for item in receipts),
            },
        }


__all__ = [
    "BINDING_SCHEMA",
    "CAPSULE_SCHEMA",
    "ContextAccessDenied",
    "ContextConflict",
    "ContextControlService",
    "INVALIDATION_SCHEMA",
    "MEMORY_CANDIDATE_SCHEMA",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "RELATIONSHIP_SCHEMA",
]
