from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from adaos.services.agent_context import get_ctx
from adaos.services.webspace_id import coerce_webspace_id


BUILDER_PROJECT_PREVIEW = "builder_project_preview"
BUILDER_SELF_HOST = "builder_self_host"
BUILDER_RELATION_PURPOSES = {BUILDER_PROJECT_PREVIEW, BUILDER_SELF_HOST}


def _workspace_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("webspace_id is required")
    return coerce_webspace_id(token, fallback=token)


def _builder_scenario_ids() -> set[str]:
    raw = str(os.getenv("ADAOS_BUILDER_SCENARIO_IDS") or "builder,prompt_engineer_scenario")
    return {item.strip() for item in raw.split(",") if item.strip()}


def relation_purpose_for_scenario(scenario_id: Any) -> str:
    token = str(scenario_id or "").strip()
    return BUILDER_SELF_HOST if token in _builder_scenario_ids() else BUILDER_PROJECT_PREVIEW


@dataclass(frozen=True, slots=True)
class WebspaceRelation:
    relation_id: str
    source_webspace_id: str
    target_webspace_id: str
    purpose: str
    generation: int
    created_at: float
    updated_at: float
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "source_webspace_id": self.source_webspace_id,
            "target_webspace_id": self.target_webspace_id,
            "purpose": self.purpose,
            "generation": self.generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


def _decode_metadata(value: Any) -> dict[str, Any]:
    try:
        raw = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _row_to_relation(row: Any) -> WebspaceRelation | None:
    if not row:
        return None
    return WebspaceRelation(
        relation_id=str(row[0]),
        source_webspace_id=str(row[1]),
        target_webspace_id=str(row[2]),
        purpose=str(row[3]),
        generation=int(row[4] or 1),
        created_at=float(row[5] or 0.0),
        updated_at=float(row[6] or 0.0),
        metadata=_decode_metadata(row[7]),
    )


class WebspaceRelationshipRegistry:
    """Persistent, explicit Builder host-to-preview topology."""

    def __init__(self, sql: Any | None = None) -> None:
        self.sql = sql or get_ctx().sql

    @classmethod
    def from_context(cls) -> "WebspaceRelationshipRegistry":
        return cls()

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS webspace_relations(
                relation_id TEXT PRIMARY KEY,
                source_webspace_id TEXT NOT NULL UNIQUE,
                target_webspace_id TEXT NOT NULL UNIQUE,
                purpose TEXT NOT NULL,
                generation INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_webspace_relations_purpose ON webspace_relations(purpose)"
        )

    def get_outgoing(self, source_webspace_id: Any) -> WebspaceRelation | None:
        source = _workspace_id(source_webspace_id)
        with self.sql.connect() as con:
            self._ensure_schema(con)
            row = con.execute(
                "SELECT relation_id, source_webspace_id, target_webspace_id, purpose, generation, "
                "created_at, updated_at, metadata_json FROM webspace_relations WHERE source_webspace_id=?",
                (source,),
            ).fetchone()
        return _row_to_relation(row)

    def get_incoming(self, target_webspace_id: Any) -> WebspaceRelation | None:
        target = _workspace_id(target_webspace_id)
        with self.sql.connect() as con:
            self._ensure_schema(con)
            row = con.execute(
                "SELECT relation_id, source_webspace_id, target_webspace_id, purpose, generation, "
                "created_at, updated_at, metadata_json FROM webspace_relations WHERE target_webspace_id=?",
                (target,),
            ).fetchone()
        return _row_to_relation(row)

    def list(self, *, purpose: str | None = None) -> list[WebspaceRelation]:
        with self.sql.connect() as con:
            self._ensure_schema(con)
            if purpose:
                rows = con.execute(
                    "SELECT relation_id, source_webspace_id, target_webspace_id, purpose, generation, "
                    "created_at, updated_at, metadata_json FROM webspace_relations WHERE purpose=? ORDER BY created_at",
                    (str(purpose),),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT relation_id, source_webspace_id, target_webspace_id, purpose, generation, "
                    "created_at, updated_at, metadata_json FROM webspace_relations ORDER BY created_at"
                ).fetchall()
        return [relation for row in rows if (relation := _row_to_relation(row)) is not None]

    def remove_outgoing(self, source_webspace_id: Any) -> WebspaceRelation | None:
        source = _workspace_id(source_webspace_id)
        existing = self.get_outgoing(source)
        if existing is None:
            return None
        with self.sql.connect() as con:
            self._ensure_schema(con)
            con.execute("DELETE FROM webspace_relations WHERE source_webspace_id=?", (source,))
            con.commit()
        return existing

    def resolve_builder_host(self, webspace_id: Any) -> str:
        """Resolve compatibility calls without allowing arbitrary preview nesting."""

        current = _workspace_id(webspace_id)
        incoming = self.get_incoming(current)
        if incoming is None:
            return current
        if incoming.purpose == BUILDER_SELF_HOST:
            return current
        return incoming.source_webspace_id

    def claim_builder_self_host(self, webspace_id: Any, *, scenario_id: Any) -> str:
        """Promote the current Builder preview into the single allowed host level.

        A Builder rendered inside an ordinary preview owns its own child Preview.
        The browser supplies the current scenario explicitly; ids are never parsed
        to infer that the current surface is Builder.
        """

        current = _workspace_id(webspace_id)
        scenario = str(scenario_id or "").strip()
        if scenario not in _builder_scenario_ids():
            raise ValueError("builder self-host claim requires a Builder scenario")
        incoming = self.get_incoming(current)
        if incoming is None or incoming.purpose == BUILDER_SELF_HOST:
            return current
        self.ensure(
            incoming.source_webspace_id,
            purpose=BUILDER_SELF_HOST,
            scenario_id=scenario,
            legacy_target_webspace_id=current,
            metadata={"claimed_by": "builder_active_surface"},
        )
        return current

    def allocate_preview_webspace_id(self) -> str:
        for _ in range(32):
            candidate = f"preview-{secrets.token_hex(6)}"
            if self.get_incoming(candidate) is None and self.get_outgoing(candidate) is None:
                return candidate
        raise RuntimeError("failed to allocate preview webspace id")

    def ensure(
        self,
        source_webspace_id: Any,
        *,
        purpose: str,
        scenario_id: str | None = None,
        legacy_target_webspace_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[WebspaceRelation, bool]:
        source = _workspace_id(source_webspace_id)
        purpose_token = str(purpose or "").strip()
        if purpose_token not in BUILDER_RELATION_PURPOSES:
            raise ValueError(f"unsupported webspace relation purpose: {purpose_token!r}")
        if purpose_token == BUILDER_SELF_HOST and str(scenario_id or "").strip() not in _builder_scenario_ids():
            raise ValueError("builder_self_host requires a Builder scenario")

        incoming = self.get_incoming(source)
        if incoming is not None:
            if incoming.purpose != BUILDER_SELF_HOST:
                raise ValueError("ordinary preview webspaces cannot own a child preview")
            if self.get_incoming(incoming.source_webspace_id) is not None:
                raise ValueError("Builder self-host preview depth is limited to one")

        existing = self.get_outgoing(source)
        target = (
            existing.target_webspace_id
            if existing is not None
            else _workspace_id(legacy_target_webspace_id)
            if str(legacy_target_webspace_id or "").strip()
            else _workspace_id(f"{source}-dev")
            if incoming is not None and incoming.purpose == BUILDER_SELF_HOST
            else self.allocate_preview_webspace_id()
        )
        if target == source:
            raise ValueError("webspace relation cannot target itself")
        target_incoming = self.get_incoming(target)
        if target_incoming is not None and target_incoming.source_webspace_id != source:
            raise ValueError("preview webspace is already paired with another Builder host")
        target_outgoing = self.get_outgoing(target)
        if purpose_token != BUILDER_SELF_HOST and target_outgoing is not None:
            # Selecting a non-Builder project demotes the previous self-host.
            # Detach only topology; the child workspace itself remains intact.
            self.remove_outgoing(target)

        next_metadata = dict(existing.metadata) if existing is not None else {}
        next_metadata.update(dict(metadata or {}))
        if scenario_id:
            next_metadata["scenario_id"] = str(scenario_id).strip()
        now = time.time()
        if existing is not None and existing.purpose == purpose_token and existing.metadata == next_metadata:
            return existing, False

        relation_id = existing.relation_id if existing is not None else f"rel-{secrets.token_hex(8)}"
        generation = int(existing.generation if existing is not None else 0) + 1
        created_at = existing.created_at if existing is not None else now
        encoded_metadata = json.dumps(next_metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with self.sql.connect() as con:
            self._ensure_schema(con)
            con.execute(
                """
                INSERT INTO webspace_relations(
                    relation_id, source_webspace_id, target_webspace_id, purpose,
                    generation, created_at, updated_at, metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(source_webspace_id) DO UPDATE SET
                    target_webspace_id=excluded.target_webspace_id,
                    purpose=excluded.purpose,
                    generation=excluded.generation,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    relation_id,
                    source,
                    target,
                    purpose_token,
                    generation,
                    created_at,
                    now,
                    encoded_metadata,
                ),
            )
            con.commit()
        relation = self.get_outgoing(source)
        if relation is None:
            raise RuntimeError("failed to persist webspace relation")
        return relation, existing is None


__all__ = [
    "BUILDER_PROJECT_PREVIEW",
    "BUILDER_RELATION_PURPOSES",
    "BUILDER_SELF_HOST",
    "WebspaceRelation",
    "WebspaceRelationshipRegistry",
    "relation_purpose_for_scenario",
]
