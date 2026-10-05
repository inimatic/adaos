"""Append-only session evidence and separately attributed review decisions.

SQLite transactions serialize writers across threads/processes. Sealed session
records cannot change; late feedback and review live in a separate event chain.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .policy import require_enabled

FEEDBACK_LABELS = frozenset({"correct", "partial", "wrong_information", "wrong_action_target",
                           "rephrasing_required", "too_slow", "capability_absent", "correction"})
ANOMALY_CLASSES = frozenset({"nlu_intent_miss", "nlu_entity_miss", "regexp_gap",
    "capability_search_miss", "interaction_contract_gap", "context_projection_miss",
    "mcp_contract_error", "executor_error", "ui_observation_gap", "model_planning_error",
    "missing_capability", "latency_anomaly"})
HYPOTHESIS_STATES = frozenset({"observed", "candidate", "shadow", "active_personal", "active_node",
    "proposed_canonical", "accepted_canonical", "deprecated", "quarantined"})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def new_id(kind: str) -> str:
    return f"companion-{kind}:{uuid4().hex}"


def default_path() -> Path:
    from adaos.services.agent_context import get_ctx

    return Path(get_ctx().paths.root_mcp_state_dir()) / "companion_lab" / "evidence.sqlite3"


class Ledger:
    def __init__(self, path: Path | None = None):
        require_enabled()
        self.path = Path(path) if path is not None else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, manifest TEXT NOT NULL,
                    started REAL NOT NULL, touched REAL NOT NULL, sealed REAL,
                    bundle TEXT, bundle_digest TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_scope ON sessions(scope) WHERE sealed IS NULL;
                CREATE TABLE IF NOT EXISTS evidence (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    session TEXT NOT NULL REFERENCES sessions(id), turn TEXT,
                    kind TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL,
                    payload TEXT NOT NULL, previous_digest TEXT NOT NULL, digest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS evidence_session ON evidence(session,seq);
                CREATE TABLE IF NOT EXISTS reviews (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    session TEXT NOT NULL REFERENCES sessions(id), target TEXT NOT NULL,
                    actor TEXT NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL,
                    payload TEXT NOT NULL, previous_digest TEXT NOT NULL, digest TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, scope: str, identities: Mapping[str, Any], *, actor: str,
              inactivity_s: float = 1800) -> dict[str, Any]:
        require_enabled()
        if not scope or not actor:
            raise ValueError("session_scope_and_actor_required")
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE scope=? AND sealed IS NULL", (scope,)).fetchone()
            if row:
                manifest = json.loads(row["manifest"])
                if manifest["identities"] == dict(identities) and now-row["touched"] < inactivity_s:
                    return manifest
                self._seal(db, row["id"], reason="inactivity" if now-row["touched"] >= inactivity_s else "input_revision_changed")
            manifest = {"schema": "adaos.companion.session_manifest.v1", "session_id": new_id("session"),
                        "scope": scope, "actor": actor, "started_at": now, "identities": dict(identities)}
            db.execute("INSERT INTO sessions(id,scope,manifest,started,touched) VALUES(?,?,?,?,?)",
                       (manifest["session_id"], scope, canonical(manifest), now, now))
            return manifest

    def append(self, session: str, kind: str, payload: Mapping[str, Any], *,
               turn: str | None = None, actor: str = "runtime", record_id: str | None = None) -> dict[str, Any]:
        require_enabled()
        record_id = record_id or new_id(kind)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT sealed FROM sessions WHERE id=?", (session,)).fetchone()
            if state is None:
                raise KeyError("session_not_found")
            if state["sealed"] is not None:
                raise ValueError("session_sealed")
            existing = db.execute("SELECT * FROM evidence WHERE id=?", (record_id,)).fetchone()
            if existing:
                item = self._record(existing)
                if (item["session"], item["turn"], item["kind"], item["actor"], item["payload"]) != (session, turn, kind, actor, dict(payload)):
                    raise ValueError("evidence_idempotency_conflict")
                return item
            previous = db.execute("SELECT digest FROM evidence WHERE session=? ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
            item = {"id": record_id, "session": session, "turn": turn, "kind": kind, "actor": actor,
                    "created": time.time(), "payload": dict(payload), "previous_digest": previous[0] if previous else ""}
            item["digest"] = digest(item)
            db.execute("INSERT INTO evidence(id,session,turn,kind,actor,created,payload,previous_digest,digest) VALUES(?,?,?,?,?,?,?,?,?)",
                       (record_id, session, turn, kind, actor, item["created"], canonical(payload), item["previous_digest"], item["digest"]))
            db.execute("UPDATE sessions SET touched=? WHERE id=?", (item["created"], session))
            return item

    @staticmethod
    def _record(row) -> dict[str, Any]:
        item = dict(row)
        item.pop("seq", None)
        item["payload"] = json.loads(item["payload"])
        return item

    def records(self, session: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [self._record(row) for row in db.execute("SELECT * FROM evidence WHERE session=? ORDER BY seq", (session,))]

    def _seal(self, db, session: str, *, reason: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone()
        if row is None:
            raise KeyError("session_not_found")
        if row["sealed"] is not None:
            return json.loads(row["bundle"])
        records = [self._record(r) for r in db.execute("SELECT * FROM evidence WHERE session=? ORDER BY seq", (session,))]
        self.verify_records(records)
        turns = {r["turn"] for r in records if r["kind"] == "turn"}
        feedback = {r["turn"] for r in records if r["kind"] == "feedback"}
        usage = {key: sum((r["payload"].get("usage") or {}).get(key) or 0 for r in records if r["kind"] == "model_call")
                 for key in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")}
        receipts = {}
        for r in records:
            if r["kind"] in {"receipt", "observation"} and r["payload"].get("action_id"):
                receipts[r["payload"]["action_id"]] = r["payload"]
        bundle = {"schema": "adaos.companion.session_bundle.v1", "session_id": session,
                  "manifest": json.loads(row["manifest"]), "sealed_at": time.time(), "reason": reason,
                  "records": records, "record_count": len(records), "usage_totals": usage,
                  "feedback_coverage": len(turns & feedback)/len(turns) if turns else 0,
                  "incomplete_actions": [r for r in receipts.values() if r.get("status") in {"dispatched", "pending", "queued"}],
                  "privacy": {"state": "raw", "deidentification": "pending"},
                  "export_eligibility": "requires_current_owner_consent", "chain_head": records[-1]["digest"] if records else ""}
        bundle["digest"] = digest(bundle)
        db.execute("UPDATE sessions SET sealed=?,bundle=?,bundle_digest=? WHERE id=?",
                   (bundle["sealed_at"], canonical(bundle), bundle["digest"], session))
        return bundle

    def seal(self, session: str, *, reason: str = "explicit_end") -> dict[str, Any]:
        require_enabled()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._seal(db, session, reason=reason)

    @staticmethod
    def verify_records(records: list[dict[str, Any]]) -> None:
        previous = ""
        for item in records:
            unsigned = {k: v for k, v in item.items() if k != "digest"}
            if item["previous_digest"] != previous or digest(unsigned) != item["digest"]:
                raise ValueError("evidence_integrity_error")
            previous = item["digest"]

    def bundle(self, session: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT bundle,bundle_digest FROM sessions WHERE id=?", (session,)).fetchone()
            if not row or row["bundle"] is None:
                raise ValueError("sealed_session_required")
            bundle = json.loads(row["bundle"])
            if bundle["digest"] != row["bundle_digest"] or digest({k: v for k, v in bundle.items() if k != "digest"}) != bundle["digest"]:
                raise ValueError("bundle_integrity_error")
            self.verify_records(bundle["records"])
            return bundle

    def sessions(self, *, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM sessions WHERE (? IS NULL OR scope=?) ORDER BY started DESC LIMIT ?", (scope, scope, min(max(limit, 1), 100))).fetchall()
            return [{**json.loads(r["manifest"]), "sealed_at": r["sealed"], "bundle_digest": r["bundle_digest"]} for r in rows]

    def feedback(self, session: str, turn: str, *, label: str, correction: str, actor: str) -> dict[str, Any]:
        if label not in FEEDBACK_LABELS or not actor.startswith("user:"):
            raise ValueError("human_feedback_required")
        if not any(r["turn"] == turn and r["kind"] == "turn" for r in self.records(session)):
            raise ValueError("feedback_turn_not_found")
        return self.review(session, turn, kind="feedback", actor=actor, payload={"label": label, "correction": correction})

    def review(self, session: str, target: str, *, kind: str, actor: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        require_enabled()
        if not actor.startswith("user:"):
            raise ValueError("human_review_required")
        if kind not in {"feedback", "hypothesis", "anomaly", "label"}:
            raise ValueError("unknown_review_kind")
        if kind == "hypothesis" and payload.get("status") not in {"shadow", "quarantined", "deprecated", "proposed_canonical"}:
            raise ValueError("canonical_promotion_requires_release_process")
        if kind == "anomaly" and (payload.get("classification") not in ANOMALY_CLASSES or payload.get("decision") not in {"accept", "reject", "quarantine", "backlog"}):
            raise ValueError("invalid_anomaly_review")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                raise KeyError("session_not_found")
            previous = db.execute("SELECT digest FROM reviews WHERE session=? ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
            item = {"id": new_id("review"), "session": session, "target": target, "actor": actor,
                    "kind": kind, "created": time.time(), "payload": dict(payload), "previous_digest": previous[0] if previous else ""}
            item["digest"] = digest(item)
            db.execute("INSERT INTO reviews(id,session,target,actor,kind,created,payload,previous_digest,digest) VALUES(?,?,?,?,?,?,?,?,?)",
                       (item["id"], session, target, actor, kind, item["created"], canonical(payload), item["previous_digest"], item["digest"]))
            return item

    def reviews(self, session: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = [self._record(r) for r in db.execute("SELECT * FROM reviews WHERE session=? ORDER BY seq", (session,))]
        self.verify_records(rows)
        return rows

    def hypothesis(self, session: str, *, turn: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
        if proposal.get("kind") not in {"lexical_alias", "capability_binding", "intent", "entity", "procedure"}:
            raise ValueError("invalid_hypothesis_kind")
        if not proposal.get("surface") or not proposal.get("evidence_refs"):
            raise ValueError("hypothesis_evidence_required")
        records = {r["id"]: r for r in self.records(session)}
        if not all(ref in records for ref in proposal["evidence_refs"]):
            raise ValueError("unknown_evidence_ref")
        body = {"schema": "adaos.companion.learning_hypothesis.v1", "kind": proposal["kind"],
                "surface": proposal["surface"], "normalized": proposal.get("normalized"),
                "binding": proposal.get("binding"), "scope": proposal.get("scope") or {},
                "evidence_refs": list(proposal["evidence_refs"]), "contradictions": [],
                "status": "candidate", "evidence_class": "model_proposal", "memory_layer": "hypothesis"}
        if body["kind"] == "capability_binding" and not body["scope"].get("catalog_digest"):
            raise ValueError("binding_catalog_digest_required")
        return self.append(session, "hypothesis", body, turn=turn, actor="model")

    def workbench(self, session: str) -> dict[str, Any]:
        bundle = self.bundle(session)
        reviews = self.reviews(session)
        cases = []
        for turn in (r for r in bundle["records"] if r["kind"] == "turn"):
            evidence = [r for r in bundle["records"] if r["turn"] == turn["turn"]]
            decisions = [r for r in reviews if r["target"] == turn["turn"]]
            cases.append({"turn_id": turn["turn"], "example_family_id": turn["payload"].get("example_family_id") or turn["turn"],
                          "utterance": turn["payload"].get("text"), "evidence": evidence,
                          "reviews": decisions, "review_required": not bool(decisions),
                          "classification": next((r["payload"].get("classification") for r in reversed(decisions) if r["kind"] == "anomaly"), None)})
        return {"session_id": session, "bundle_digest": bundle["digest"], "cases": cases,
                "anomaly_classes": sorted(ANOMALY_CLASSES), "canonical_mutations": 0}
