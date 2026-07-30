from __future__ import annotations

from typing import Any, Mapping, Sequence
import hashlib
import json
import re
import sqlite3
import time
import uuid

from adaos.services.agent_context import get_ctx


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversation_conversations (
        conversation_id TEXT PRIMARY KEY,
        webspace_id TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'conversation',
        owner TEXT NOT NULL,
        title TEXT,
        active_agent_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        retention_class TEXT NOT NULL DEFAULT 'normal',
        retention_until REAL,
        redaction_state TEXT NOT NULL DEFAULT 'active',
        redacted_at REAL,
        redaction_reason TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        initiator_json TEXT NOT NULL DEFAULT '{}',
        policy_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_dialog_channels (
        webspace_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        label TEXT,
        owner TEXT,
        conversation_id TEXT,
        active_agent_id TEXT,
        default_skill TEXT,
        default_tool TEXT,
        route_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        updated_at REAL NOT NULL,
        policy_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (webspace_id, channel_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_active_dialog_channels (
        webspace_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        conversation_id TEXT,
        active_agent_id TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        updated_at REAL NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_dialog_frames (
        webspace_id TEXT PRIMARY KEY,
        frame_id TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'slot_collection',
        state TEXT NOT NULL DEFAULT 'collecting',
        owner TEXT,
        conversation_id TEXT,
        slots_json TEXT NOT NULL DEFAULT '{}',
        required_slots_json TEXT NOT NULL DEFAULT '[]',
        validation_json TEXT NOT NULL DEFAULT '{}',
        policy_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_agent_registry (
        agent_id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        owner TEXT NOT NULL,
        channel_id TEXT,
        skill_id TEXT,
        kind TEXT NOT NULL DEFAULT 'agent',
        character_id TEXT,
        aliases_json TEXT NOT NULL DEFAULT '[]',
        gender TEXT,
        voice TEXT,
        icon TEXT,
        voice_profile_json TEXT NOT NULL DEFAULT '{}',
        source TEXT NOT NULL DEFAULT 'runtime',
        status TEXT NOT NULL DEFAULT 'active',
        updated_at REAL NOT NULL,
        policy_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_messages (
        message_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        seq INTEGER NOT NULL,
        webspace_id TEXT NOT NULL,
        channel_id TEXT,
        owner TEXT,
        actor_id TEXT,
        actor_label TEXT,
        actor_icon TEXT,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        route_id TEXT,
        ts REAL NOT NULL,
        request_id TEXT,
        turn_trace_id TEXT,
        idempotency_key TEXT,
        retention_class TEXT NOT NULL DEFAULT 'normal',
        retention_until REAL,
        redaction_state TEXT NOT NULL DEFAULT 'active',
        redacted_at REAL,
        redaction_reason TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        UNIQUE (conversation_id, seq),
        UNIQUE (conversation_id, idempotency_key)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_transport_ingress (
        idempotency_key TEXT PRIMARY KEY,
        transport TEXT NOT NULL,
        event_id TEXT,
        payload_digest TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'claimed',
        claimed_at REAL NOT NULL,
        dispatched_at REAL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_threads (
        thread_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        created_by_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_threads_conversation
    ON conversation_threads(conversation_id, updated_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_seq
    ON conversation_messages(conversation_id, seq);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_webspace_channel
    ON conversation_messages(webspace_id, channel_id, seq);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_memory_items (
        memory_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        owner TEXT NOT NULL,
        subject_id TEXT,
        key TEXT,
        text TEXT,
        value_json TEXT NOT NULL DEFAULT '{}',
        confidence REAL,
        consent_state TEXT NOT NULL DEFAULT 'unknown',
        retention_class TEXT NOT NULL DEFAULT 'normal',
        retention_until REAL,
        redaction_state TEXT NOT NULL DEFAULT 'active',
        redacted_at REAL,
        redaction_reason TEXT,
        policy_json TEXT NOT NULL DEFAULT '{}',
        source_ref_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_memory_scope_owner_subject
    ON conversation_memory_items(scope, owner, subject_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_turn_traces (
        turn_trace_id TEXT PRIMARY KEY,
        conversation_id TEXT,
        message_id TEXT,
        webspace_id TEXT NOT NULL,
        channel_id TEXT,
        agent_id TEXT,
        selected_tool TEXT,
        policy_decision_json TEXT NOT NULL DEFAULT '{}',
        renderer_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'started',
        summary TEXT,
        retention_class TEXT NOT NULL DEFAULT 'normal',
        retention_until REAL,
        redaction_state TEXT NOT NULL DEFAULT 'active',
        redacted_at REAL,
        redaction_reason TEXT,
        created_at REAL NOT NULL,
        completed_at REAL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_segments (
        segment_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        start_seq INTEGER NOT NULL,
        end_seq INTEGER NOT NULL,
        message_count INTEGER NOT NULL,
        summary TEXT NOT NULL,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        retention_class TEXT NOT NULL DEFAULT 'normal',
        redaction_state TEXT NOT NULL DEFAULT 'active',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE(conversation_id, thread_id, start_seq, end_seq)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_segments_conversation_range
    ON conversation_segments(conversation_id, thread_id, start_seq, end_seq);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_segment_summary_jobs (
        job_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        segment_size INTEGER NOT NULL DEFAULT 40,
        priority INTEGER NOT NULL DEFAULT 100,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        available_at REAL NOT NULL,
        last_error TEXT,
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        completed_at REAL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_segment_jobs_status
    ON conversation_segment_summary_jobs(status, available_at, priority, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_audit_events (
        audit_event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        action TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'completed',
        conversation_id TEXT,
        actor_owner TEXT,
        actor_id TEXT,
        reason TEXT,
        counts_json TEXT NOT NULL DEFAULT '{}',
        meta_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_audit_conversation_created
    ON conversation_audit_events(conversation_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_development_changes (
        change_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        topic_id TEXT,
        status TEXT NOT NULL DEFAULT 'accepted',
        source_message_ids_json TEXT NOT NULL DEFAULT '[]',
        source_refs_json TEXT NOT NULL DEFAULT '{}',
        artifact_refs_json TEXT NOT NULL DEFAULT '[]',
        revision_refs_json TEXT NOT NULL DEFAULT '[]',
        commit_refs_json TEXT NOT NULL DEFAULT '[]',
        result_message_id TEXT,
        request_id TEXT,
        model TEXT,
        summary TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_development_changes_topic
    ON conversation_development_changes(topic_id, updated_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_development_changes_conversation
    ON conversation_development_changes(conversation_id, thread_id, updated_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_development_runs (
        run_id TEXT PRIMARY KEY,
        change_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        topic_id TEXT,
        activity TEXT NOT NULL,
        executor TEXT NOT NULL,
        purpose TEXT NOT NULL DEFAULT 'iteration',
        adoption_status TEXT NOT NULL DEFAULT 'not_applicable',
        status TEXT NOT NULL DEFAULT 'queued',
        context_packet_digest TEXT,
        environment_ref TEXT,
        input_refs_json TEXT NOT NULL DEFAULT '[]',
        output_refs_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        started_at TEXT,
        completed_at TEXT,
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_development_runs_change
    ON conversation_development_runs(change_id, updated_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_development_runs_topic
    ON conversation_development_runs(conversation_id, topic_id, updated_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_channel_capability_profiles (
        profile_id TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        transport TEXT NOT NULL,
        client TEXT NOT NULL,
        surface TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        fresh_until TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_interactions (
        interaction_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        thread_id TEXT,
        owner TEXT NOT NULL,
        status TEXT NOT NULL,
        generation INTEGER NOT NULL,
        task_ref_json TEXT,
        workflow_ref_json TEXT,
        reply_route_ref_json TEXT,
        expires_at TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_interactions_pending
    ON conversation_interactions(conversation_id, status, updated_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_interaction_presentations (
        presentation_id TEXT PRIMARY KEY,
        interaction_id TEXT NOT NULL,
        interaction_generation INTEGER NOT NULL,
        profile_id TEXT NOT NULL,
        profile_version INTEGER NOT NULL,
        mode TEXT NOT NULL,
        supported INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(interaction_id, interaction_generation, profile_id, profile_version)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_presentations_interaction
    ON conversation_interaction_presentations(interaction_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_interaction_responses (
        response_id TEXT PRIMARY KEY,
        interaction_id TEXT NOT NULL,
        interaction_generation INTEGER NOT NULL,
        actor_id TEXT NOT NULL,
        status TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        supersedes_response_id TEXT,
        payload_digest TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(interaction_id, idempotency_key)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_responses_interaction
    ON conversation_interaction_responses(interaction_id, created_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_intent_proposals (
        proposal_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        source_message_id TEXT NOT NULL,
        disposition TEXT NOT NULL,
        supersedes_proposal_id TEXT,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_conversation_intent_proposals_source
    ON conversation_intent_proposals(conversation_id, source_message_id, created_at);
    """,
)


_RETENTION_REDACTION_COLUMNS = (
    ("retention_class", "TEXT NOT NULL DEFAULT 'normal'"),
    ("retention_until", "REAL"),
    ("redaction_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("redacted_at", "REAL"),
    ("redaction_reason", "TEXT"),
)
_MESSAGE_THREAD_COLUMNS = (("thread_id", "TEXT"),)
_SCHEMA_COLUMN_MIGRATIONS = {
    "conversation_conversations": _RETENTION_REDACTION_COLUMNS,
    "conversation_messages": _MESSAGE_THREAD_COLUMNS + _RETENTION_REDACTION_COLUMNS,
    "conversation_memory_items": _RETENTION_REDACTION_COLUMNS,
    "conversation_turn_traces": _RETENTION_REDACTION_COLUMNS,
    "conversation_development_runs": (
        ("purpose", "TEXT NOT NULL DEFAULT 'iteration'"),
        ("adoption_status", "TEXT NOT NULL DEFAULT 'not_applicable'"),
    ),
}
_ENSURED_SQL_IDS: set[int] = set()
_FTS_UNAVAILABLE_SQL_IDS: set[int] = set()


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "{}"


def _json_load(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _ensure_columns(con: sqlite3.Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    try:
        existing = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return
    for name, ddl in columns:
        if name in existing:
            continue
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError:
            pass


def _ensure_fts(con: sqlite3.Connection) -> bool:
    token = id(con)
    if token in _FTS_UNAVAILABLE_SQL_IDS:
        return False
    try:
        con.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_messages_fts
            USING fts5(
                message_id UNINDEXED,
                conversation_id UNINDEXED,
                thread_id UNINDEXED,
                webspace_id UNINDEXED,
                channel_id UNINDEXED,
                owner UNINDEXED,
                role UNINDEXED,
                text
            )
            """
        )
        con.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_memory_fts
            USING fts5(
                memory_id UNINDEXED,
                scope UNINDEXED,
                owner UNINDEXED,
                subject_id UNINDEXED,
                key UNINDEXED,
                text
            )
            """
        )
        con.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_segments_fts
            USING fts5(
                segment_id UNINDEXED,
                conversation_id UNINDEXED,
                thread_id UNINDEXED,
                summary
            )
            """
        )
        return True
    except sqlite3.Error:
        _FTS_UNAVAILABLE_SQL_IDS.add(token)
        return False


def _sql() -> Any | None:
    try:
        return get_ctx().sql
    except Exception:
        return None


def available() -> bool:
    sql = _sql()
    return bool(sql and hasattr(sql, "connect"))


def ensure_schema(sql: Any | None = None) -> bool:
    sql = sql or _sql()
    if not sql or not hasattr(sql, "connect"):
        return False
    token = id(sql)
    if token in _ENSURED_SQL_IDS:
        try:
            with sql.connect() as con:
                rows = con.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name IN (
                        'conversation_segment_summary_jobs',
                        'conversation_transport_ingress',
                        'conversation_development_runs',
                        'conversation_channel_capability_profiles',
                        'conversation_interactions',
                        'conversation_interaction_presentations',
                        'conversation_interaction_responses',
                        'conversation_intent_proposals'
                    )
                    """
                ).fetchall()
            if {str(row[0]) for row in rows} == {
                "conversation_segment_summary_jobs",
                "conversation_transport_ingress",
                "conversation_development_runs",
                "conversation_channel_capability_profiles",
                "conversation_interactions",
                "conversation_interaction_presentations",
                "conversation_interaction_responses",
                "conversation_intent_proposals",
            }:
                return True
        except sqlite3.Error:
            pass
        _ENSURED_SQL_IDS.discard(token)
    with sql.connect() as con:
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        cur = con.cursor()
        for stmt in _SCHEMA:
            cur.execute(stmt)
        for table, columns in _SCHEMA_COLUMN_MIGRATIONS.items():
            _ensure_columns(con, table, columns)
        _ensure_fts(con)
        con.commit()
    _ENSURED_SQL_IDS.add(token)
    return True


def _normalize_id(value: Any, fallback_prefix: str) -> str:
    token = str(value or "").strip()
    if token:
        return token
    return f"{fallback_prefix}.{uuid.uuid4().hex}"


def claim_transport_ingress(
    *,
    idempotency_key: str,
    transport: str,
    payload: Mapping[str, Any],
    event_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Claim one transport update exactly once before it can mutate dialog state.

    A claimed update is never replayed automatically, including after a process
    interruption. The sender can submit a new transport message to request a
    deliberate retry. A reused key with different content is rejected as a
    conflict instead of being rebound to another command.
    """

    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    channel = str(transport or "").strip().lower()
    if not channel:
        raise ValueError("transport is required")
    normalized = json.dumps(
        dict(payload or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if not ensure_schema():
        return {
            "ok": True,
            "claimed": True,
            "durable": False,
            "idempotency_key": key,
            "payload_digest": digest,
        }
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT * FROM conversation_transport_ingress WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if existing:
            con.rollback()
            row = dict(existing)
            conflict = str(row.get("payload_digest") or "") != digest
            return {
                "ok": not conflict,
                "claimed": False,
                "duplicate": not conflict,
                "conflict": conflict,
                "durable": True,
                **row,
            }
        con.execute(
            """
            INSERT INTO conversation_transport_ingress(
                idempotency_key, transport, event_id, payload_digest, status,
                claimed_at, dispatched_at, meta_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                key,
                channel,
                str(event_id or "").strip() or None,
                digest,
                "claimed",
                now,
                None,
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
    return {
        "ok": True,
        "claimed": True,
        "durable": True,
        "idempotency_key": key,
        "transport": channel,
        "event_id": str(event_id or "").strip() or None,
        "payload_digest": digest,
        "status": "claimed",
        "claimed_at": now,
    }


def mark_transport_ingress_dispatched(idempotency_key: str) -> dict[str, Any] | None:
    key = str(idempotency_key or "").strip()
    if not key or not ensure_schema():
        return None
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute(
            """
            UPDATE conversation_transport_ingress
            SET status='dispatched', dispatched_at=?
            WHERE idempotency_key=?
            """,
            (now, key),
        )
        row = con.execute(
            "SELECT * FROM conversation_transport_ingress WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        con.commit()
    return dict(row) if row else None


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_\u0410-\u042f\u0430-\u044f\u0401\u0451]+", str(query or "").lower())
    if not tokens:
        token = str(query or "").replace('"', " ").strip()
        return f'"{token}"' if token else '""'
    return " OR ".join(f'"{token}"' for token in tokens[:12])


def _message_fts_upsert(
    con: sqlite3.Connection,
    *,
    message_id: str,
    conversation_id: str,
    thread_id: str | None,
    webspace_id: str,
    channel_id: str | None,
    owner: str | None,
    role: str,
    text: str,
) -> None:
    try:
        if not _ensure_fts(con):
            return
        con.execute("DELETE FROM conversation_messages_fts WHERE message_id=?", (message_id,))
        con.execute(
            """
            INSERT INTO conversation_messages_fts(
                message_id, conversation_id, thread_id, webspace_id, channel_id, owner, role, text
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (message_id, conversation_id, thread_id, webspace_id, channel_id, owner, role, text),
        )
    except sqlite3.Error:
        return


def _memory_fts_upsert(
    con: sqlite3.Connection,
    *,
    memory_id: str,
    scope: str,
    owner: str,
    subject_id: str | None,
    key: str | None,
    text: str | None,
) -> None:
    try:
        if not _ensure_fts(con):
            return
        con.execute("DELETE FROM conversation_memory_fts WHERE memory_id=?", (memory_id,))
        con.execute(
            """
            INSERT INTO conversation_memory_fts(memory_id, scope, owner, subject_id, key, text)
            VALUES(?,?,?,?,?,?)
            """,
            (memory_id, scope, owner, subject_id, key, text),
        )
    except sqlite3.Error:
        return


def _segment_fts_upsert(
    con: sqlite3.Connection,
    *,
    segment_id: str,
    conversation_id: str,
    thread_id: str | None,
    summary: str,
) -> None:
    try:
        if not _ensure_fts(con):
            return
        con.execute("DELETE FROM conversation_segments_fts WHERE segment_id=?", (segment_id,))
        con.execute(
            """
            INSERT INTO conversation_segments_fts(segment_id, conversation_id, thread_id, summary)
            VALUES(?,?,?,?)
            """,
            (segment_id, conversation_id, thread_id, summary),
        )
    except sqlite3.Error:
        return


def rebuild_search_indexes() -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.search_index_rebuild.v1", "ok": False, "status": "unavailable"}
    with _sql().connect() as con:  # type: ignore[union-attr]
        if not _ensure_fts(con):
            return {"schema": "adaos.conversation.search_index_rebuild.v1", "ok": False, "status": "fts_unavailable"}
        con.execute("DELETE FROM conversation_messages_fts")
        con.execute("DELETE FROM conversation_memory_fts")
        con.execute("DELETE FROM conversation_segments_fts")
        con.execute(
            """
            INSERT INTO conversation_messages_fts(
                message_id, conversation_id, thread_id, webspace_id, channel_id, owner, role, text
            )
            SELECT message_id, conversation_id, thread_id, webspace_id, channel_id, owner, role, text
            FROM conversation_messages
            WHERE redaction_state!='redacted'
            """
        )
        con.execute(
            """
            INSERT INTO conversation_memory_fts(memory_id, scope, owner, subject_id, key, text)
            SELECT memory_id, scope, owner, subject_id, key, text
            FROM conversation_memory_items
            WHERE redaction_state!='redacted'
            """
        )
        con.execute(
            """
            INSERT INTO conversation_segments_fts(segment_id, conversation_id, thread_id, summary)
            SELECT segment_id, conversation_id, thread_id, summary
            FROM conversation_segments
            WHERE redaction_state!='redacted'
            """
        )
        message_count = int(con.execute("SELECT COUNT(*) FROM conversation_messages_fts").fetchone()[0] or 0)
        memory_count = int(con.execute("SELECT COUNT(*) FROM conversation_memory_fts").fetchone()[0] or 0)
        segment_count = int(con.execute("SELECT COUNT(*) FROM conversation_segments_fts").fetchone()[0] or 0)
        con.commit()
    return {
        "schema": "adaos.conversation.search_index_rebuild.v1",
        "ok": True,
        "status": "rebuilt",
        "counts": {"messages": message_count, "memory": memory_count, "segments": segment_count},
    }


def search_index_health() -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.search_index_health.v1", "status": "unavailable", "fts_available": False}
    with _sql().connect() as con:  # type: ignore[union-attr]
        if not _ensure_fts(con):
            return {"schema": "adaos.conversation.search_index_health.v1", "status": "fts_unavailable", "fts_available": False}
        base_messages = int(con.execute("SELECT COUNT(*) FROM conversation_messages WHERE redaction_state!='redacted'").fetchone()[0] or 0)
        base_memory = int(con.execute("SELECT COUNT(*) FROM conversation_memory_items WHERE redaction_state!='redacted'").fetchone()[0] or 0)
        base_segments = int(con.execute("SELECT COUNT(*) FROM conversation_segments WHERE redaction_state!='redacted'").fetchone()[0] or 0)
        indexed_messages = int(con.execute("SELECT COUNT(*) FROM conversation_messages_fts").fetchone()[0] or 0)
        indexed_memory = int(con.execute("SELECT COUNT(*) FROM conversation_memory_fts").fetchone()[0] or 0)
        indexed_segments = int(con.execute("SELECT COUNT(*) FROM conversation_segments_fts").fetchone()[0] or 0)
    stale = indexed_messages != base_messages or indexed_memory != base_memory or indexed_segments != base_segments
    return {
        "schema": "adaos.conversation.search_index_health.v1",
        "status": "stale" if stale else "ok",
        "fts_available": True,
        "counts": {
            "messages": {"base": base_messages, "indexed": indexed_messages},
            "memory": {"base": base_memory, "indexed": indexed_memory},
            "segments": {"base": base_segments, "indexed": indexed_segments},
        },
    }


def retrieval_health_report(
    conversation_id: str | None = None,
    *,
    thread_id: str | None = None,
) -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.retrieval_health.v1", "status": "unavailable"}
    cid = str(conversation_id or "").strip() or None
    clean_thread = str(thread_id or "").strip() or None
    search_health = search_index_health()
    segment_health = segment_summary_health(cid, thread_id=clean_thread) if cid else None
    job_health = segment_summary_job_health(conversation_id=cid, thread_id=clean_thread) if cid else None
    counts = _retrieval_health_counts(conversation_id=cid, thread_id=clean_thread)
    degraded_reasons: list[str] = []
    if not search_health.get("fts_available"):
        degraded_reasons.append("fts_unavailable")
    elif search_health.get("status") != "ok":
        degraded_reasons.append("search_index_stale")
    if isinstance(segment_health, Mapping) and segment_health.get("status") not in {None, "ok"}:
        degraded_reasons.append(f"segment_summary_{segment_health.get('status')}")
    if isinstance(job_health, Mapping) and job_health.get("status") in {"failed", "blocked"}:
        degraded_reasons.append(f"segment_summary_job_{job_health.get('status')}")
    return {
        "schema": "adaos.conversation.retrieval_health.v1",
        "status": "degraded" if degraded_reasons else "ok",
        "conversation_id": cid,
        "thread_id": clean_thread,
        "counts": counts,
        "search_index": search_health,
        "segment_summary": segment_health,
        "segment_summary_jobs": job_health,
        "degraded_reasons": degraded_reasons,
    }


def enqueue_segment_summary_job(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    segment_size: int = 40,
    priority: int = 100,
    max_attempts: int = 3,
    queue_limit: int = 1000,
    delay_seconds: float = 0.0,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    if not ensure_schema():
        return {"schema": "adaos.conversation.segment_summary_enqueue.v1", "ok": False, "status": "unavailable"}
    clean_thread = str(thread_id or "").strip() or None
    safe_size = max(2, min(int(segment_size or 40), 200))
    safe_priority = max(0, min(int(priority or 100), 1000))
    safe_attempts = max(1, min(int(max_attempts or 3), 20))
    safe_limit = max(1, min(int(queue_limit or 1000), 10000))
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        active_count = int(
            con.execute(
                "SELECT COUNT(*) FROM conversation_segment_summary_jobs WHERE status IN ('queued','running')",
            ).fetchone()[0]
            or 0
        )
        existing = con.execute(
            """
            SELECT *
            FROM conversation_segment_summary_jobs
            WHERE conversation_id=?
              AND ((thread_id IS NULL AND ? IS NULL) OR thread_id=?)
              AND status IN ('queued','running')
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """,
            (cid, clean_thread, clean_thread),
        ).fetchone()
        if existing:
            return {
                "schema": "adaos.conversation.segment_summary_enqueue.v1",
                "ok": True,
                "status": "existing",
                "queue_depth": active_count,
                "job": _row_to_segment_summary_job(existing),
            }
        if active_count >= safe_limit:
            return {
                "schema": "adaos.conversation.segment_summary_enqueue.v1",
                "ok": False,
                "status": "queue_full",
                "queue_depth": active_count,
                "queue_limit": safe_limit,
            }
        job_id = _normalize_id(None, "segment_summary.job")
        con.execute(
            """
            INSERT INTO conversation_segment_summary_jobs(
                job_id, conversation_id, thread_id, status, segment_size, priority,
                attempts, max_attempts, available_at, result_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                cid,
                clean_thread,
                "queued",
                safe_size,
                safe_priority,
                0,
                safe_attempts,
                now + max(0.0, float(delay_seconds or 0.0)),
                "{}",
                now,
                now,
            ),
        )
        row = con.execute("SELECT * FROM conversation_segment_summary_jobs WHERE job_id=?", (job_id,)).fetchone()
        con.commit()
    return {
        "schema": "adaos.conversation.segment_summary_enqueue.v1",
        "ok": True,
        "status": "queued",
        "queue_depth": active_count + 1,
        "job": _row_to_segment_summary_job(row),
    }


def process_segment_summary_jobs(
    *,
    limit: int = 1,
    processor: Any | None = None,
) -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.segment_summary_jobs.process.v1", "ok": False, "status": "unavailable"}
    safe_limit = max(1, min(int(limit or 1), 50))
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT *
            FROM conversation_segment_summary_jobs
            WHERE status='queued' AND available_at<=?
            ORDER BY priority ASC, available_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, safe_limit),
        ).fetchall()
    jobs = [_row_to_segment_summary_job(row) for row in rows]
    completed = 0
    failed = 0
    requeued = 0
    processed: list[dict[str, Any]] = []
    for job in jobs:
        attempts = int(job.get("attempts") or 0) + 1
        _mark_segment_summary_job_running(str(job["job_id"]), attempts=attempts)
        try:
            result = (
                processor(job)
                if callable(processor)
                else rebuild_conversation_segments(
                    str(job["conversation_id"]),
                    thread_id=str(job.get("thread_id") or "").strip() or None,
                    segment_size=int(job.get("segment_size") or 40),
                )
            )
            if not isinstance(result, Mapping):
                result = {"ok": False, "status": "invalid_processor_result", "value": repr(result)}
        except Exception as exc:
            result = {"ok": False, "status": "exception", "error": f"{type(exc).__name__}: {exc}"}
        if bool(result.get("ok")):
            updated = _finish_segment_summary_job(str(job["job_id"]), status="completed", result=dict(result))
            completed += 1
        else:
            terminal = attempts >= int(job.get("max_attempts") or 1)
            updated = _finish_segment_summary_job(
                str(job["job_id"]),
                status="failed" if terminal else "queued",
                result=dict(result),
                last_error=_segment_summary_job_error(result),
                available_at=time.time() + _segment_summary_retry_delay(attempts) if not terminal else None,
            )
            if terminal:
                failed += 1
            else:
                requeued += 1
        if updated:
            processed.append(updated)
    return {
        "schema": "adaos.conversation.segment_summary_jobs.process.v1",
        "ok": True,
        "status": "processed" if processed else "idle",
        "processed_count": len(processed),
        "completed": completed,
        "failed": failed,
        "requeued": requeued,
        "jobs": processed,
    }


def list_segment_summary_jobs(
    *,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    statuses: list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 100,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    cid = str(conversation_id or "").strip()
    if cid:
        where.append("conversation_id=?")
        params.append(cid)
    clean_thread = str(thread_id or "").strip()
    if clean_thread:
        where.append("thread_id=?")
        params.append(clean_thread)
    elif thread_id is not None:
        where.append("thread_id IS NULL")
    clean_statuses = [str(item or "").strip() for item in (statuses or []) if str(item or "").strip()]
    if clean_statuses:
        where.append(f"status IN ({','.join('?' for _ in clean_statuses)})")
        params.extend(clean_statuses)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    order = "ASC" if ascending else "DESC"
    safe_limit = max(1, min(int(limit or 100), 1000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_segment_summary_jobs
            {sql_where}
            ORDER BY updated_at {order}
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_segment_summary_job(row) for row in rows]


def segment_summary_job_health(
    *,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.segment_summary_jobs.health.v1", "status": "unavailable"}
    jobs = list_segment_summary_jobs(conversation_id=conversation_id, thread_id=thread_id, limit=500)
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    latest_error = next((job.get("last_error") for job in jobs if job.get("last_error")), None)
    pending = counts.get("queued", 0) + counts.get("running", 0)
    if counts.get("failed", 0):
        status = "failed"
    elif pending:
        status = "pending"
    else:
        status = "ok"
    return {
        "schema": "adaos.conversation.segment_summary_jobs.health.v1",
        "status": status,
        "conversation_id": str(conversation_id or "").strip() or None,
        "thread_id": str(thread_id or "").strip() or None,
        "counts": counts,
        "pending_count": pending,
        "latest_error": latest_error,
        "latest_job_id": str(jobs[0].get("job_id") or "") if jobs else None,
    }


def _mark_segment_summary_job_running(job_id: str, *, attempts: int) -> None:
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            UPDATE conversation_segment_summary_jobs
            SET status='running', attempts=?, updated_at=?, last_error=NULL
            WHERE job_id=?
            """,
            (attempts, now, job_id),
        )
        con.commit()


def _finish_segment_summary_job(
    job_id: str,
    *,
    status: str,
    result: Mapping[str, Any],
    last_error: str | None = None,
    available_at: float | None = None,
) -> dict[str, Any] | None:
    now = time.time()
    fields = ["status=?", "result_json=?", "last_error=?", "updated_at=?"]
    params: list[Any] = [status, _json_dump(dict(result)), last_error, now]
    if available_at is not None:
        fields.append("available_at=?")
        params.append(float(available_at))
    if status in {"completed", "failed"}:
        fields.append("completed_at=?")
        params.append(now)
    params.append(job_id)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute(
            f"UPDATE conversation_segment_summary_jobs SET {', '.join(fields)} WHERE job_id=?",
            params,
        )
        row = con.execute("SELECT * FROM conversation_segment_summary_jobs WHERE job_id=?", (job_id,)).fetchone()
        con.commit()
    return _row_to_segment_summary_job(row) if row else None


def _segment_summary_retry_delay(attempts: int) -> float:
    return min(300.0, float(2 ** max(0, min(attempts, 8))))


def _segment_summary_job_error(result: Mapping[str, Any]) -> str:
    return str(result.get("error") or result.get("status") or "segment_summary_failed")[:500]


def _retrieval_health_counts(
    *,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, int]:
    where_messages = ["redaction_state!='redacted'"]
    where_segments = ["redaction_state!='redacted'"]
    message_params: list[Any] = []
    segment_params: list[Any] = []
    if conversation_id:
        where_messages.append("conversation_id=?")
        where_segments.append("conversation_id=?")
        message_params.append(conversation_id)
        segment_params.append(conversation_id)
    if thread_id:
        where_messages.append("thread_id=?")
        where_segments.append("thread_id=?")
        message_params.append(thread_id)
        segment_params.append(thread_id)
    with _sql().connect() as con:  # type: ignore[union-attr]
        message_count = int(
            con.execute(
                f"SELECT COUNT(*) FROM conversation_messages WHERE {' AND '.join(where_messages)}",
                message_params,
            ).fetchone()[0]
            or 0
        )
        segment_count = int(
            con.execute(
                f"SELECT COUNT(*) FROM conversation_segments WHERE {' AND '.join(where_segments)}",
                segment_params,
            ).fetchone()[0]
            or 0
        )
        memory_count = int(
            con.execute("SELECT COUNT(*) FROM conversation_memory_items WHERE redaction_state!='redacted'").fetchone()[0]
            or 0
        )
    return {"messages": message_count, "segments": segment_count, "memory": memory_count}


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        try:
            return row[key] if key in row.keys() else default
        except Exception:
            return default
    return row.get(key, default)


def _row_to_message(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    if not isinstance(row, sqlite3.Row):
        keys = [
            "message_id",
            "conversation_id",
            "thread_id",
            "seq",
            "webspace_id",
            "channel_id",
            "owner",
            "actor_id",
            "actor_label",
            "actor_icon",
            "role",
            "text",
            "route_id",
            "ts",
            "request_id",
            "turn_trace_id",
            "payload_json",
            "meta_json",
        ]
        row = dict(zip(keys, row, strict=False))  # type: ignore[assignment]
    payload = _json_load(_row_value(row, "payload_json"), {})
    meta = _json_load(_row_value(row, "meta_json"), {})
    msg = dict(payload) if isinstance(payload, dict) else {}
    msg["id"] = str(_row_value(row, "message_id") or msg.get("id") or "")
    msg["from"] = str(msg.get("from") or _row_value(row, "role") or "")
    msg["text"] = str(_row_value(row, "text") or msg.get("text") or "")
    msg["ts"] = float(_row_value(row, "ts") or msg.get("ts") or 0.0)
    msg["seq"] = int(_row_value(row, "seq") or 0)
    msg["conversation_id"] = str(_row_value(row, "conversation_id") or "")
    if _row_value(row, "thread_id"):
        msg["thread_id"] = str(_row_value(row, "thread_id"))
    msg["dialog_channel_id"] = str(_row_value(row, "channel_id") or "")
    if _row_value(row, "actor_id"):
        msg.setdefault("active_agent_id", str(_row_value(row, "actor_id")))
    if _row_value(row, "actor_label"):
        msg.setdefault("active_agent_label", str(_row_value(row, "actor_label")))
    if _row_value(row, "actor_icon"):
        msg.setdefault("active_agent_icon", str(_row_value(row, "actor_icon")))
    if _row_value(row, "turn_trace_id"):
        msg["turn_trace_id"] = str(_row_value(row, "turn_trace_id"))
    msg["retention_class"] = str(_row_value(row, "retention_class", "normal") or "normal")
    msg["retention_until"] = _row_value(row, "retention_until")
    msg["redaction_state"] = str(_row_value(row, "redaction_state", "active") or "active")
    msg["redacted_at"] = _row_value(row, "redacted_at")
    msg["redaction_reason"] = _row_value(row, "redaction_reason")
    if isinstance(meta, dict) and meta:
        msg["_meta"] = meta
    return msg


def _row_to_segment(row: sqlite3.Row) -> dict[str, Any]:
    refs = _json_load(row["source_refs_json"], [])
    return {
        "id": str(row["segment_id"] or ""),
        "segment_id": str(row["segment_id"] or ""),
        "conversation_id": str(row["conversation_id"] or ""),
        "thread_id": row["thread_id"],
        "start_seq": int(row["start_seq"] or 0),
        "end_seq": int(row["end_seq"] or 0),
        "message_count": int(row["message_count"] or 0),
        "summary": str(row["summary"] or ""),
        "source_refs": refs if isinstance(refs, list) else [],
        "retention_class": str(row["retention_class"] or "normal"),
        "redaction_state": str(row["redaction_state"] or "active"),
        "created_at": float(row["created_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
    }


def _row_to_segment_summary_job(row: sqlite3.Row) -> dict[str, Any]:
    result = _json_load(row["result_json"], {})
    return {
        "schema": "adaos.conversation.segment_summary_job.v1",
        "id": str(row["job_id"] or ""),
        "job_id": str(row["job_id"] or ""),
        "conversation_id": str(row["conversation_id"] or ""),
        "thread_id": row["thread_id"],
        "status": str(row["status"] or "queued"),
        "segment_size": int(row["segment_size"] or 40),
        "priority": int(row["priority"] or 100),
        "attempts": int(row["attempts"] or 0),
        "max_attempts": int(row["max_attempts"] or 3),
        "available_at": float(row["available_at"] or 0.0),
        "last_error": row["last_error"],
        "result": result if isinstance(result, dict) else {},
        "created_at": float(row["created_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
        "completed_at": row["completed_at"],
    }


def _row_to_conversation(row: sqlite3.Row) -> dict[str, Any]:
    initiator = _json_load(row["initiator_json"], {})
    policy = _json_load(row["policy_json"], {})
    meta = _json_load(row["meta_json"], {})
    return {
        "conversation_id": row["conversation_id"],
        "webspace_id": row["webspace_id"],
        "kind": row["kind"],
        "owner": row["owner"],
        "title": row["title"],
        "active_agent_id": row["active_agent_id"],
        "status": row["status"],
        "retention_class": _row_value(row, "retention_class", "normal"),
        "retention_until": _row_value(row, "retention_until"),
        "redaction_state": _row_value(row, "redaction_state", "active"),
        "redacted_at": _row_value(row, "redacted_at"),
        "redaction_reason": _row_value(row, "redaction_reason"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "initiator": initiator if isinstance(initiator, dict) else {},
        "policy": policy if isinstance(policy, dict) else {},
        "meta": meta if isinstance(meta, dict) else {},
    }


def _row_to_agent(row: sqlite3.Row) -> dict[str, Any]:
    aliases = _json_load(row["aliases_json"], [])
    voice_profile = _json_load(row["voice_profile_json"], {})
    meta = _json_load(row["meta_json"], {})
    policy = _json_load(row["policy_json"], {})
    record = {
        "id": str(row["agent_id"] or ""),
        "label": str(row["label"] or ""),
        "owner": str(row["owner"] or ""),
        "channel_id": str(row["channel_id"] or ""),
        "skill": str(row["skill_id"] or ""),
        "kind": str(row["kind"] or "agent"),
        "character_id": str(row["character_id"] or ""),
        "aliases": [str(item) for item in aliases if str(item or "").strip()] if isinstance(aliases, list) else [],
        "gender": str(row["gender"] or ""),
        "voice": str(row["voice"] or ""),
        "icon": str(row["icon"] or ""),
        "voice_profile": voice_profile if isinstance(voice_profile, dict) else {},
        "source": str(row["source"] or ""),
        "policy": policy if isinstance(policy, dict) else {},
    }
    if isinstance(meta, dict):
        record.update({k: v for k, v in meta.items() if k not in record})
    return record


def upsert_conversation(
    *,
    conversation_id: str,
    webspace_id: str,
    owner: str,
    kind: str = "conversation",
    title: str | None = None,
    active_agent_id: str | None = None,
    status: str = "active",
    initiator: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    ts: float | None = None,
) -> bool:
    if not ensure_schema():
        return False
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_conversations(
                conversation_id, webspace_id, kind, owner, title, active_agent_id,
                status, created_at, updated_at, initiator_json, policy_json, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                webspace_id=excluded.webspace_id,
                kind=excluded.kind,
                owner=excluded.owner,
                title=COALESCE(excluded.title, conversation_conversations.title),
                active_agent_id=COALESCE(excluded.active_agent_id, conversation_conversations.active_agent_id),
                status=excluded.status,
                updated_at=excluded.updated_at,
                initiator_json=excluded.initiator_json,
                policy_json=excluded.policy_json,
                meta_json=excluded.meta_json
            """,
            (
                conversation_id,
                webspace_id,
                kind,
                owner,
                title,
                active_agent_id,
                status,
                now,
                now,
                _json_dump(dict(initiator or {})),
                _json_dump(dict(policy or {})),
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
    return True


def get_conversation(conversation_id: str, *, include_redacted: bool = False) -> dict[str, Any] | None:
    cid = str(conversation_id or "").strip()
    if not cid or not ensure_schema():
        return None
    where = ["conversation_id=?"]
    params: list[Any] = [cid]
    if not include_redacted:
        where.append("redaction_state!='redacted'")
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            f"SELECT * FROM conversation_conversations WHERE {' AND '.join(where)}",
            params,
        ).fetchone()
    return _row_to_conversation(row) if row else None


def merge_conversations(*, source_conversation_id: str, target_conversation_id: str) -> dict[str, Any]:
    source_id = str(source_conversation_id or "").strip()
    target_id = str(target_conversation_id or "").strip()
    if not source_id or not target_id:
        raise ValueError("source_conversation_id and target_conversation_id are required")
    if source_id == target_id or not ensure_schema():
        return {"ok": True, "source": source_id, "target": target_id, "messages_moved": 0}
    moved = 0
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        target = con.execute(
            "SELECT conversation_id FROM conversation_conversations WHERE conversation_id=?",
            (target_id,),
        ).fetchone()
        source = con.execute(
            "SELECT * FROM conversation_conversations WHERE conversation_id=?",
            (source_id,),
        ).fetchone()
        if not source:
            return {"ok": True, "source": source_id, "target": target_id, "messages_moved": 0}
        if not target:
            raise ValueError(f"target conversation does not exist: {target_id}")
        try:
            con.execute("BEGIN IMMEDIATE")
            next_seq = int(
                con.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM conversation_messages WHERE conversation_id=?",
                    (target_id,),
                ).fetchone()[0]
                or 0
            )
            sequence_offset = next_seq
            rows = con.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY ts ASC, seq ASC",
                (source_id,),
            ).fetchall()
            for row in rows:
                next_seq += 1
                try:
                    con.execute(
                        "UPDATE conversation_messages SET conversation_id=?, seq=? WHERE message_id=?",
                        (target_id, next_seq, row["message_id"]),
                    )
                except sqlite3.IntegrityError:
                    con.execute(
                        "UPDATE conversation_messages SET conversation_id=?, seq=?, idempotency_key=NULL WHERE message_id=?",
                        (target_id, next_seq, row["message_id"]),
                    )
                _message_fts_upsert(
                    con,
                    message_id=str(row["message_id"]),
                    conversation_id=target_id,
                    thread_id=str(row["thread_id"] or "").strip() or None,
                    webspace_id=str(row["webspace_id"] or ""),
                    channel_id=str(row["channel_id"] or ""),
                    owner=str(row["owner"] or ""),
                    role=str(row["role"] or ""),
                    text=str(row["text"] or ""),
                )
                moved += 1
            con.execute("UPDATE conversation_threads SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_turn_traces SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_dialog_channels SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_active_dialog_channels SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_dialog_frames SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute(
                "UPDATE conversation_segments SET conversation_id=?, start_seq=start_seq+?, end_seq=end_seq+? WHERE conversation_id=?",
                (target_id, sequence_offset, sequence_offset, source_id),
            )
            con.execute("UPDATE conversation_segment_summary_jobs SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_audit_events SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_development_changes SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            con.execute("UPDATE conversation_development_runs SET conversation_id=? WHERE conversation_id=?", (target_id, source_id))
            source_meta = _json_load(source["meta_json"], {})
            source_meta = dict(source_meta) if isinstance(source_meta, Mapping) else {}
            source_meta.update({"merged_into": target_id, "merged_at": time.time()})
            con.execute(
                "UPDATE conversation_conversations SET status='merged', updated_at=?, meta_json=? WHERE conversation_id=?",
                (time.time(), _json_dump(source_meta), source_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
    return {"ok": True, "source": source_id, "target": target_id, "messages_moved": moved}


def merge_conversations_by_prefix(*, prefix: str, target_conversation_id: str) -> dict[str, Any]:
    token = str(prefix or "").strip()
    target_id = str(target_conversation_id or "").strip()
    if not token or not target_id or not ensure_schema():
        return {"ok": True, "target": target_id, "sources": [], "messages_moved": 0}
    with _sql().connect() as con:  # type: ignore[union-attr]
        rows = con.execute(
            "SELECT conversation_id FROM conversation_conversations WHERE conversation_id LIKE ? AND conversation_id!=? AND status!='merged'",
            (f"{token}%", target_id),
        ).fetchall()
    results = [
        merge_conversations(source_conversation_id=str(row[0]), target_conversation_id=target_id)
        for row in rows
    ]
    return {
        "ok": True,
        "target": target_id,
        "sources": [item["source"] for item in results],
        "messages_moved": sum(int(item.get("messages_moved") or 0) for item in results),
    }


def start_thread(
    *,
    conversation_id: str,
    thread_id: str | None = None,
    title: str | None = None,
    created_by: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    status: str = "active",
    ts: float | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    tid = _normalize_id(thread_id, "thread")
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute(
            """
            INSERT INTO conversation_threads(
                thread_id, conversation_id, title, status, created_at,
                updated_at, created_by_json, meta_json
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(thread_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                title=COALESCE(excluded.title, conversation_threads.title),
                status=excluded.status,
                updated_at=excluded.updated_at,
                created_by_json=excluded.created_by_json,
                meta_json=excluded.meta_json
            """,
            (
                tid,
                cid,
                str(title or "").strip() or None,
                str(status or "active").strip() or "active",
                now,
                now,
                _json_dump(dict(created_by or {})),
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM conversation_threads WHERE thread_id=?",
            (tid,),
        ).fetchone()
    if not row:
        return None
    return _row_to_thread(row)


def start_threads_batch(threads: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Upsert conversation threads in one transaction for migration/import paths."""
    if not threads or not ensure_schema():
        return []
    thread_ids: list[str] = []
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            for raw in threads:
                item = dict(raw)
                conversation_id = str(item.get("conversation_id") or "").strip()
                if not conversation_id:
                    raise ValueError("conversation_id is required")
                thread_id = _normalize_id(item.get("thread_id"), "thread")
                now = float(item.get("ts") or time.time())
                con.execute(
                    """
                    INSERT INTO conversation_threads(
                        thread_id, conversation_id, title, status, created_at,
                        updated_at, created_by_json, meta_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        conversation_id=excluded.conversation_id,
                        title=COALESCE(excluded.title, conversation_threads.title),
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        created_by_json=excluded.created_by_json,
                        meta_json=excluded.meta_json
                    """,
                    (
                        thread_id,
                        conversation_id,
                        str(item.get("title") or "").strip() or None,
                        str(item.get("status") or "active").strip() or "active",
                        now,
                        now,
                        _json_dump(dict(item.get("created_by") or {})),
                        _json_dump(dict(item.get("meta") or {})),
                    ),
                )
                thread_ids.append(thread_id)
            con.commit()
        except Exception:
            con.rollback()
            raise
        rows = [
            con.execute("SELECT * FROM conversation_threads WHERE thread_id=?", (thread_id,)).fetchone()
            for thread_id in thread_ids
        ]
    return [_row_to_thread(row) for row in rows if row is not None]


def _row_to_thread(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "thread_id": row["thread_id"],
        "id": row["thread_id"],
        "conversation_id": row["conversation_id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": _json_load(row["created_by_json"], {}),
        "meta": _json_load(row["meta_json"], {}),
    }


def _row_to_development_change(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "schema": "adaos.conversation.development_change.v1",
        "change_id": row["change_id"],
        "conversation_id": row["conversation_id"],
        "thread_id": row["thread_id"],
        "topic_id": row["topic_id"],
        "status": row["status"],
        "source_message_ids": _json_load(row["source_message_ids_json"], []),
        "source_refs": _json_load(row["source_refs_json"], {}),
        "artifact_refs": _json_load(row["artifact_refs_json"], []),
        "revision_refs": _json_load(row["revision_refs_json"], []),
        "commit_refs": _json_load(row["commit_refs_json"], []),
        "result_message_id": row["result_message_id"],
        "request_id": row["request_id"],
        "model": row["model"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "meta": _json_load(row["meta_json"], {}),
    }


def upsert_development_change(
    *,
    change_id: str,
    conversation_id: str,
    thread_id: str | None = None,
    topic_id: str | None = None,
    status: str = "accepted",
    source_message_ids: Sequence[str] | None = None,
    source_refs: Mapping[str, Any] | None = None,
    artifact_refs: Sequence[Mapping[str, Any]] | None = None,
    revision_refs: Sequence[Mapping[str, Any] | str] | None = None,
    commit_refs: Sequence[Mapping[str, Any] | str] | None = None,
    result_message_id: str | None = None,
    request_id: str | None = None,
    model: str | None = None,
    summary: str | None = None,
    meta: Mapping[str, Any] | None = None,
    ts: float | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    cid = str(change_id or "").strip()
    conversation = str(conversation_id or "").strip()
    if not cid or not conversation:
        raise ValueError("change_id and conversation_id are required")
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        existing = con.execute(
            "SELECT * FROM conversation_development_changes WHERE change_id=?",
            (cid,),
        ).fetchone()
        existing_change = _row_to_development_change(existing) if existing else {}

        def _selected_sequence(key: str, incoming: Sequence[Any] | None) -> list[Any]:
            if incoming is not None:
                return list(incoming)
            value = existing_change.get(key)
            return list(value) if isinstance(value, list) else []

        selected_source_ids = [str(item) for item in _selected_sequence("source_message_ids", source_message_ids) if str(item).strip()]
        selected_artifacts = [dict(item) for item in _selected_sequence("artifact_refs", artifact_refs) if isinstance(item, Mapping)]
        selected_revisions = _selected_sequence("revision_refs", revision_refs)
        selected_commits = _selected_sequence("commit_refs", commit_refs)
        selected_source_refs = dict(source_refs) if source_refs is not None else dict(existing_change.get("source_refs") or {})
        selected_meta = dict(existing_change.get("meta") or {})
        if meta is not None:
            selected_meta.update(dict(meta))
        con.execute(
            """
            INSERT INTO conversation_development_changes(
                change_id, conversation_id, thread_id, topic_id, status,
                source_message_ids_json, source_refs_json, artifact_refs_json,
                revision_refs_json, commit_refs_json, result_message_id,
                request_id, model, summary, created_at, updated_at, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(change_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                thread_id=COALESCE(excluded.thread_id, conversation_development_changes.thread_id),
                topic_id=COALESCE(excluded.topic_id, conversation_development_changes.topic_id),
                status=excluded.status,
                source_message_ids_json=excluded.source_message_ids_json,
                source_refs_json=excluded.source_refs_json,
                artifact_refs_json=excluded.artifact_refs_json,
                revision_refs_json=excluded.revision_refs_json,
                commit_refs_json=excluded.commit_refs_json,
                result_message_id=COALESCE(excluded.result_message_id, conversation_development_changes.result_message_id),
                request_id=COALESCE(excluded.request_id, conversation_development_changes.request_id),
                model=COALESCE(excluded.model, conversation_development_changes.model),
                summary=COALESCE(excluded.summary, conversation_development_changes.summary),
                updated_at=excluded.updated_at,
                meta_json=excluded.meta_json
            """,
            (
                cid,
                conversation,
                str(thread_id or existing_change.get("thread_id") or "").strip() or None,
                str(topic_id or existing_change.get("topic_id") or "").strip() or None,
                str(status or existing_change.get("status") or "accepted").strip() or "accepted",
                _json_dump(selected_source_ids),
                _json_dump(selected_source_refs),
                _json_dump(selected_artifacts),
                _json_dump(selected_revisions),
                _json_dump(selected_commits),
                str(result_message_id or "").strip() or None,
                str(request_id or "").strip() or None,
                str(model or "").strip() or None,
                str(summary or "").strip() or None,
                float(existing_change.get("created_at") or now),
                now,
                _json_dump(selected_meta),
            ),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM conversation_development_changes WHERE change_id=?",
            (cid,),
        ).fetchone()
    return _row_to_development_change(row) if row else None


def get_development_change(change_id: str) -> dict[str, Any] | None:
    token = str(change_id or "").strip()
    if not token or not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM conversation_development_changes WHERE change_id=?",
            (token,),
        ).fetchone()
    return _row_to_development_change(row) if row else None


def list_development_changes(
    *,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    artifact_kind: str | None = None,
    artifact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    if str(conversation_id or "").strip():
        where.append("conversation_id=?")
        params.append(str(conversation_id).strip())
    if str(topic_id or "").strip():
        where.append("topic_id=?")
        params.append(str(topic_id).strip())
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    safe_limit = max(1, min(int(limit or 100), 1000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM conversation_development_changes {sql_where} ORDER BY updated_at DESC LIMIT ?",
            [*params, safe_limit],
        ).fetchall()
    changes = [_row_to_development_change(row) for row in rows]
    kind = str(artifact_kind or "").strip().lower().rstrip("s")
    artifact = str(artifact_id or "").strip()
    if kind or artifact:
        changes = [
            item
            for item in changes
            if any(
                (not kind or str(ref.get("kind") or "").strip().lower().rstrip("s") == kind)
                and (not artifact or str(ref.get("id") or ref.get("name") or "").strip() == artifact)
                for ref in item.get("artifact_refs") or []
                if isinstance(ref, Mapping)
            )
        ]
    return changes


_DEVELOPMENT_RUN_STATES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "superseded",
}


def _row_to_development_run(row: sqlite3.Row) -> dict[str, Any]:
    """Project the durable record through the strict Builder Run ABI."""

    return {
        "schema": "adaos.builder.run.v1",
        "run_id": row["run_id"],
        "change_id": row["change_id"],
        "activity": row["activity"],
        "executor": row["executor"],
        "purpose": row["purpose"],
        "adoption_status": row["adoption_status"],
        "status": row["status"],
        "context_packet_digest": row["context_packet_digest"],
        "environment_ref": row["environment_ref"],
        "input_refs": _json_load(row["input_refs_json"], []),
        "output_refs": _json_load(row["output_refs_json"], []),
        "evidence_refs": _json_load(row["evidence_refs_json"], []),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "error": row["error"],
    }


def upsert_development_run(
    *,
    run_id: str,
    change_id: str,
    conversation_id: str,
    activity: str,
    executor: str,
    purpose: str | None = None,
    adoption_status: str | None = None,
    status: str = "queued",
    thread_id: str | None = None,
    topic_id: str | None = None,
    context_packet_digest: str | None = None,
    environment_ref: str | None = None,
    input_refs: Sequence[str] | None = None,
    output_refs: Sequence[str] | None = None,
    evidence_refs: Sequence[str] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error: str | None = None,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Create or advance one execution Run linked to a stable product Change.

    A repeated ``run_id`` updates the same Run. It can never be rebound to a
    different Change or conversation, which prevents a retry/reconnect from
    manufacturing a second product-level Change.
    """

    if not ensure_schema():
        return None
    rid = str(run_id or "").strip()
    cid = str(change_id or "").strip()
    conversation = str(conversation_id or "").strip()
    selected_activity = str(activity or "").strip()
    selected_executor = str(executor or "").strip()
    requested_purpose = str(purpose or "").strip().lower() or None
    requested_adoption = str(adoption_status or "").strip().lower() or None
    selected_status = str(status or "queued").strip().lower()
    if not rid or not cid or not conversation or not selected_activity or not selected_executor:
        raise ValueError("run_id, change_id, conversation_id, activity, and executor are required")
    if selected_status not in _DEVELOPMENT_RUN_STATES:
        raise ValueError(f"invalid Builder Run status: {selected_status}")
    if requested_purpose and requested_purpose not in {"iteration", "experiment", "evaluation", "recovery"}:
        raise ValueError(f"invalid Builder Run purpose: {requested_purpose}")
    if requested_adoption and requested_adoption not in {"not_applicable", "pending", "adopted", "discarded"}:
        raise ValueError(f"invalid Builder Run adoption status: {requested_adoption}")
    digest = str(context_packet_digest or "").strip() or None
    if digest and (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(char not in "0123456789abcdef" for char in digest[7:])
    ):
        raise ValueError("context_packet_digest must use sha256:<64 hex> format")
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        parent = con.execute(
            "SELECT conversation_id FROM conversation_development_changes WHERE change_id=?",
            (cid,),
        ).fetchone()
        if parent is None:
            raise ValueError("Builder Run requires an existing canonical Change")
        if str(parent["conversation_id"]) != conversation:
            raise ValueError("Builder Run conversation does not match its canonical Change")
        existing = con.execute(
            "SELECT * FROM conversation_development_runs WHERE run_id=?",
            (rid,),
        ).fetchone()
        if existing is not None:
            if str(existing["change_id"]) != cid:
                raise ValueError("run_id is already linked to a different Change")
            if str(existing["conversation_id"]) != conversation:
                raise ValueError("run_id is already linked to a different conversation")
            prior_status = str(existing["status"] or "queued").strip().lower()
            if prior_status in {"succeeded", "failed", "cancelled", "superseded"} and selected_status != prior_status:
                raise ValueError("a terminal Builder Run cannot transition to a different status")
        selected_purpose = requested_purpose or (str(existing["purpose"]) if existing is not None else "iteration")
        selected_adoption = requested_adoption or (
            str(existing["adoption_status"]) if existing is not None else "not_applicable"
        )

        def _refs(column: str, incoming: Sequence[str] | None) -> list[str]:
            if incoming is None and existing is not None:
                return [
                    str(item).strip()
                    for item in _json_load(existing[column], [])
                    if str(item).strip()
                ][-100:]
            return list(dict.fromkeys(str(item).strip() for item in incoming or [] if str(item).strip()))[-100:]

        con.execute(
            """
            INSERT INTO conversation_development_runs(
                run_id, change_id, conversation_id, thread_id, topic_id,
                activity, executor, purpose, adoption_status, status, context_packet_digest,
                environment_ref, input_refs_json, output_refs_json,
                evidence_refs_json, started_at, completed_at, error,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
                thread_id=COALESCE(excluded.thread_id, conversation_development_runs.thread_id),
                topic_id=COALESCE(excluded.topic_id, conversation_development_runs.topic_id),
                activity=excluded.activity,
                executor=excluded.executor,
                purpose=excluded.purpose,
                adoption_status=excluded.adoption_status,
                status=excluded.status,
                context_packet_digest=COALESCE(excluded.context_packet_digest, conversation_development_runs.context_packet_digest),
                environment_ref=COALESCE(excluded.environment_ref, conversation_development_runs.environment_ref),
                input_refs_json=excluded.input_refs_json,
                output_refs_json=excluded.output_refs_json,
                evidence_refs_json=excluded.evidence_refs_json,
                started_at=COALESCE(conversation_development_runs.started_at, excluded.started_at),
                completed_at=COALESCE(excluded.completed_at, conversation_development_runs.completed_at),
                error=COALESCE(excluded.error, conversation_development_runs.error),
                updated_at=excluded.updated_at
            """,
            (
                rid,
                cid,
                conversation,
                str(thread_id or "").strip() or None,
                str(topic_id or "").strip() or None,
                selected_activity,
                selected_executor,
                selected_purpose,
                selected_adoption,
                selected_status,
                digest,
                str(environment_ref or "").strip() or None,
                _json_dump(_refs("input_refs_json", input_refs)),
                _json_dump(_refs("output_refs_json", output_refs)),
                _json_dump(_refs("evidence_refs_json", evidence_refs)),
                str(started_at or "").strip() or None,
                str(completed_at or "").strip() or None,
                str(error or "").strip() or None,
                float(existing["created_at"]) if existing is not None else now,
                now,
            ),
        )
        con.commit()
        row = con.execute(
            "SELECT * FROM conversation_development_runs WHERE run_id=?",
            (rid,),
        ).fetchone()
    return _row_to_development_run(row) if row else None


def get_development_run(run_id: str) -> dict[str, Any] | None:
    token = str(run_id or "").strip()
    if not token or not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM conversation_development_runs WHERE run_id=?",
            (token,),
        ).fetchone()
    return _row_to_development_run(row) if row else None


def list_development_runs(
    *,
    change_id: str | None = None,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("change_id", change_id),
        ("conversation_id", conversation_id),
        ("topic_id", topic_id),
    ):
        token = str(value or "").strip()
        if token:
            where.append(f"{column}=?")
            params.append(token)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    safe_limit = max(1, min(int(limit or 100), 1000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT * FROM conversation_development_runs {sql_where} ORDER BY updated_at DESC LIMIT ?",
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_development_run(row) for row in rows]


def save_channel_capability_profile(record: Mapping[str, Any]) -> dict[str, Any] | None:
    value = dict(record or {})
    profile_id = str(value.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("profile_id is required")
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_channel_capability_profiles(
                profile_id, version, transport, client, surface, payload_json,
                fresh_until, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
                version=excluded.version,
                transport=excluded.transport,
                client=excluded.client,
                surface=excluded.surface,
                payload_json=excluded.payload_json,
                fresh_until=excluded.fresh_until,
                updated_at=excluded.updated_at
            WHERE excluded.version >= conversation_channel_capability_profiles.version
            """,
            (
                profile_id,
                int(value.get("version") or 1),
                str(value.get("transport") or ""),
                str(value.get("client") or ""),
                str(value.get("surface") or ""),
                _json_dump(value),
                value.get("fresh_until"),
                str(value.get("updated_at") or ""),
            ),
        )
        con.commit()
    return get_channel_capability_profile(profile_id)


def get_channel_capability_profile(profile_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT payload_json FROM conversation_channel_capability_profiles WHERE profile_id=?",
            (str(profile_id or "").strip(),),
        ).fetchone()
    return _json_load(row["payload_json"], {}) if row else None


def save_interaction(
    record: Mapping[str, Any],
    *,
    expected_generation: int | None = None,
    create_only: bool = False,
) -> dict[str, Any] | None:
    value = dict(record or {})
    interaction_id = str(value.get("interaction_id") or "").strip()
    if not interaction_id:
        raise ValueError("interaction_id is required")
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT generation, payload_json FROM conversation_interactions WHERE interaction_id=?",
            (interaction_id,),
        ).fetchone()
        if existing and create_only:
            con.rollback()
            return _json_load(existing["payload_json"], {})
        if expected_generation is not None:
            actual = int(existing["generation"]) if existing else -1
            if actual != int(expected_generation):
                con.rollback()
                raise ValueError(
                    f"stale interaction generation: expected {expected_generation}, current {actual}"
                )
        con.execute(
            """
            INSERT INTO conversation_interactions(
                interaction_id, conversation_id, thread_id, owner, status,
                generation, task_ref_json, workflow_ref_json,
                reply_route_ref_json, expires_at, payload_json, created_at,
                updated_at, completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(interaction_id) DO UPDATE SET
                conversation_id=excluded.conversation_id,
                thread_id=excluded.thread_id,
                owner=excluded.owner,
                status=excluded.status,
                generation=excluded.generation,
                task_ref_json=excluded.task_ref_json,
                workflow_ref_json=excluded.workflow_ref_json,
                reply_route_ref_json=excluded.reply_route_ref_json,
                expires_at=excluded.expires_at,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at,
                completed_at=excluded.completed_at
            """,
            (
                interaction_id,
                str(value.get("conversation_id") or ""),
                value.get("thread_id"),
                str(value.get("owner") or ""),
                str(value.get("status") or ""),
                int(value.get("generation") or 0),
                _json_dump(value.get("task_ref")) if value.get("task_ref") is not None else None,
                _json_dump(value.get("workflow_ref")) if value.get("workflow_ref") is not None else None,
                _json_dump(value.get("reply_route_ref")) if value.get("reply_route_ref") is not None else None,
                value.get("expires_at"),
                _json_dump(value),
                str(value.get("created_at") or ""),
                str(value.get("updated_at") or ""),
                value.get("completed_at"),
            ),
        )
        con.commit()
    return get_interaction(interaction_id)


def get_interaction(interaction_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT payload_json FROM conversation_interactions WHERE interaction_id=?",
            (str(interaction_id or "").strip(),),
        ).fetchone()
    return _json_load(row["payload_json"], {}) if row else None


def list_interactions(
    *,
    conversation_id: str | None = None,
    statuses: Sequence[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if str(conversation_id or "").strip():
        clauses.append("conversation_id=?")
        params.append(str(conversation_id).strip())
    clean_statuses = [str(item).strip() for item in statuses or [] if str(item).strip()]
    if clean_statuses:
        clauses.append(f"status IN ({','.join('?' for _ in clean_statuses)})")
        params.extend(clean_statuses)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT payload_json FROM conversation_interactions {where} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    return [_json_load(row["payload_json"], {}) for row in rows]


def append_interaction_presentation(record: Mapping[str, Any]) -> dict[str, Any] | None:
    value = dict(record or {})
    presentation_id = str(value.get("presentation_id") or "").strip()
    if not presentation_id:
        raise ValueError("presentation_id is required")
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT payload_json FROM conversation_interaction_presentations WHERE presentation_id=?",
            (presentation_id,),
        ).fetchone()
        if existing:
            con.rollback()
            return _json_load(existing["payload_json"], {})
        con.execute(
            """
            INSERT INTO conversation_interaction_presentations(
                presentation_id, interaction_id, interaction_generation,
                profile_id, profile_version, mode, supported, payload_json,
                created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                presentation_id,
                str(value.get("interaction_id") or ""),
                int(value.get("interaction_generation") or 0),
                str(value.get("profile_id") or ""),
                int(value.get("profile_version") or 1),
                str(value.get("mode") or ""),
                1 if value.get("supported") else 0,
                _json_dump(value),
                str(value.get("created_at") or ""),
            ),
        )
        con.commit()
    return dict(value)


def latest_interaction_presentation(
    interaction_id: str,
    *,
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    clause = "interaction_id=?"
    params: list[Any] = [str(interaction_id or "").strip()]
    if str(profile_id or "").strip():
        clause += " AND profile_id=?"
        params.append(str(profile_id).strip())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            f"SELECT payload_json FROM conversation_interaction_presentations WHERE {clause} ORDER BY created_at DESC LIMIT 1",
            tuple(params),
        ).fetchone()
    return _json_load(row["payload_json"], {}) if row else None


def find_interaction_presentation_by_action_token(token: str) -> dict[str, Any] | None:
    action_token = str(token or "").strip()
    if not action_token or not ensure_schema():
        return None
    # Action tokens are opaque bounded values emitted by the presentation
    # negotiator. The JSON scan is acceptable for the bounded pending set and
    # avoids making a transport callback token an authority-bearing database key.
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT payload_json
            FROM conversation_interaction_presentations
            WHERE payload_json LIKE ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (f"%{action_token}%",),
        ).fetchall()
    for row in rows:
        payload = _json_load(row["payload_json"], {})
        if action_token in dict(payload.get("action_tokens") or {}):
            return payload
    return None


def append_interaction_response(record: Mapping[str, Any]) -> dict[str, Any] | None:
    value = dict(record or {})
    response_id = str(value.get("response_id") or "").strip()
    interaction_id = str(value.get("interaction_id") or "").strip()
    idempotency_key = str(value.get("idempotency_key") or "").strip()
    if not response_id or not interaction_id or not idempotency_key:
        raise ValueError("response_id, interaction_id, and idempotency_key are required")
    if not ensure_schema():
        return None
    normalized = _json_dump(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT payload_digest, payload_json FROM conversation_interaction_responses WHERE interaction_id=? AND idempotency_key=?",
            (interaction_id, idempotency_key),
        ).fetchone()
        if existing:
            con.rollback()
            if str(existing["payload_digest"]) != digest:
                raise ValueError("interaction response idempotency conflict")
            duplicate = _json_load(existing["payload_json"], {})
            duplicate["duplicate"] = True
            return duplicate
        con.execute(
            """
            INSERT INTO conversation_interaction_responses(
                response_id, interaction_id, interaction_generation, actor_id,
                status, idempotency_key, supersedes_response_id,
                payload_digest, payload_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                response_id,
                interaction_id,
                int(value.get("interaction_generation") or 0),
                str(value.get("actor_id") or ""),
                str(value.get("status") or ""),
                idempotency_key,
                value.get("supersedes_response_id"),
                digest,
                normalized,
                str(value.get("created_at") or ""),
            ),
        )
        con.commit()
    return dict(value)


def get_interaction_response_by_idempotency(
    interaction_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT payload_json FROM conversation_interaction_responses WHERE interaction_id=? AND idempotency_key=?",
            (str(interaction_id or "").strip(), str(idempotency_key or "").strip()),
        ).fetchone()
    return _json_load(row["payload_json"], {}) if row else None


def list_interaction_responses(interaction_id: str) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT payload_json FROM conversation_interaction_responses WHERE interaction_id=? ORDER BY created_at, response_id",
            (str(interaction_id or "").strip(),),
        ).fetchall()
    return [_json_load(row["payload_json"], {}) for row in rows]


def save_intent_proposal(record: Mapping[str, Any], *, create_only: bool = False) -> dict[str, Any] | None:
    value = dict(record or {})
    proposal_id = str(value.get("proposal_id") or "").strip()
    if not proposal_id:
        raise ValueError("proposal_id is required")
    if not ensure_schema():
        return None
    payload = _json_dump(value)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT payload_json FROM conversation_intent_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if existing and create_only:
            con.rollback()
            current = _json_load(existing["payload_json"], {})
            if current != value:
                raise ValueError("intent proposal idempotency conflict")
            return current
        con.execute(
            """
            INSERT INTO conversation_intent_proposals(
                proposal_id, conversation_id, source_message_id, disposition,
                supersedes_proposal_id, payload_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(proposal_id) DO UPDATE SET
                disposition=excluded.disposition,
                supersedes_proposal_id=excluded.supersedes_proposal_id,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                proposal_id,
                str(value.get("conversation_id") or ""),
                str(value.get("source_message_id") or ""),
                str(value.get("disposition") or ""),
                value.get("supersedes_proposal_id"),
                payload,
                str(value.get("created_at") or ""),
                str(value.get("updated_at") or ""),
            ),
        )
        con.commit()
    return value


def get_intent_proposal(proposal_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT payload_json FROM conversation_intent_proposals WHERE proposal_id=?",
            (str(proposal_id or "").strip(),),
        ).fetchone()
    return _json_load(row["payload_json"], {}) if row else None


def list_intent_proposals(
    conversation_id: str,
    *,
    source_message_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    query = "SELECT payload_json FROM conversation_intent_proposals WHERE conversation_id=?"
    params: list[Any] = [str(conversation_id or "").strip()]
    if source_message_id:
        query += " AND source_message_id=?"
        params.append(str(source_message_id).strip())
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(1000, int(limit))))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(query, params).fetchall()
    return [_json_load(row["payload_json"], {}) for row in rows]


def upsert_dialog_channel(
    *,
    webspace_id: str,
    channel_id: str,
    label: str | None = None,
    owner: str | None = None,
    conversation_id: str | None = None,
    active_agent_id: str | None = None,
    default_skill: str | None = None,
    default_tool: str | None = None,
    route_id: str | None = None,
    status: str = "active",
    policy: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    ts: float | None = None,
) -> bool:
    if not ensure_schema():
        return False
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_dialog_channels(
                webspace_id, channel_id, label, owner, conversation_id, active_agent_id,
                default_skill, default_tool, route_id, status, updated_at, policy_json, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(webspace_id, channel_id) DO UPDATE SET
                label=COALESCE(excluded.label, conversation_dialog_channels.label),
                owner=COALESCE(excluded.owner, conversation_dialog_channels.owner),
                conversation_id=COALESCE(excluded.conversation_id, conversation_dialog_channels.conversation_id),
                active_agent_id=COALESCE(excluded.active_agent_id, conversation_dialog_channels.active_agent_id),
                default_skill=COALESCE(excluded.default_skill, conversation_dialog_channels.default_skill),
                default_tool=COALESCE(excluded.default_tool, conversation_dialog_channels.default_tool),
                route_id=COALESCE(excluded.route_id, conversation_dialog_channels.route_id),
                status=excluded.status,
                updated_at=excluded.updated_at,
                policy_json=excluded.policy_json,
                meta_json=excluded.meta_json
            """,
            (
                webspace_id,
                channel_id,
                label,
                owner,
                conversation_id,
                active_agent_id,
                default_skill,
                default_tool,
                route_id,
                status,
                now,
                _json_dump(dict(policy or {})),
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
    return True


def get_dialog_channel(webspace_id: str, channel_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT * FROM conversation_dialog_channels
            WHERE webspace_id=? AND channel_id=?
            """,
            (webspace_id, channel_id),
        ).fetchone()
    if not row:
        return None
    return {
        "webspace_id": row["webspace_id"],
        "channel_id": row["channel_id"],
        "id": row["channel_id"],
        "label": row["label"],
        "owner": row["owner"],
        "conversation_id": row["conversation_id"],
        "active_agent_id": row["active_agent_id"],
        "default_skill": row["default_skill"],
        "default_tool": row["default_tool"],
        "route_id": row["route_id"],
        "status": row["status"],
        "policy": _json_load(row["policy_json"], {}),
        "meta": _json_load(row["meta_json"], {}),
    }


def list_dialog_channels(webspace_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where = ["webspace_id=?"]
    params: list[Any] = [webspace_id]
    if not include_inactive:
        where.append("status='active'")
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT * FROM conversation_dialog_channels
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE channel_id
                    WHEN 'general' THEN 0
                    WHEN 'conversational' THEN 1
                    WHEN 'builder' THEN 2
                    ELSE 10
                END,
                channel_id COLLATE NOCASE
            """,
            params,
        ).fetchall()
    return [
        {
            "webspace_id": row["webspace_id"],
            "channel_id": row["channel_id"],
            "id": row["channel_id"],
            "label": row["label"],
            "owner": row["owner"],
            "conversation_id": row["conversation_id"],
            "active_agent_id": row["active_agent_id"],
            "default_skill": row["default_skill"],
            "default_tool": row["default_tool"],
            "route_id": row["route_id"],
            "status": row["status"],
            "policy": _json_load(row["policy_json"], {}),
            "meta": _json_load(row["meta_json"], {}),
        }
        for row in rows
    ]


def upsert_dialog_frame(
    *,
    webspace_id: str,
    frame_id: str,
    kind: str = "slot_collection",
    state: str = "collecting",
    owner: str | None = None,
    conversation_id: str | None = None,
    slots: Mapping[str, Any] | None = None,
    required_slots: list[str] | tuple[str, ...] = (),
    validation: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    updated_at: float | None = None,
) -> bool:
    if not ensure_schema():
        return False
    now = float(updated_at or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_dialog_frames(
                webspace_id, frame_id, kind, state, owner, conversation_id,
                slots_json, required_slots_json, validation_json, policy_json,
                updated_at, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(webspace_id) DO UPDATE SET
                frame_id=excluded.frame_id,
                kind=excluded.kind,
                state=excluded.state,
                owner=excluded.owner,
                conversation_id=excluded.conversation_id,
                slots_json=excluded.slots_json,
                required_slots_json=excluded.required_slots_json,
                validation_json=excluded.validation_json,
                policy_json=excluded.policy_json,
                updated_at=excluded.updated_at,
                meta_json=excluded.meta_json
            """,
            (
                webspace_id,
                frame_id,
                kind,
                state,
                owner,
                conversation_id,
                _json_dump(dict(slots or {})),
                _json_dump([str(item) for item in required_slots if str(item or "").strip()]),
                _json_dump(dict(validation or {})),
                _json_dump(dict(policy or {})),
                now,
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
    return True


def get_dialog_frame(webspace_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM conversation_dialog_frames WHERE webspace_id=?",
            (webspace_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "webspace_id": row["webspace_id"],
        "frame_id": row["frame_id"],
        "kind": row["kind"],
        "state": row["state"],
        "owner": row["owner"],
        "conversation_id": row["conversation_id"],
        "slots": _json_load(row["slots_json"], {}),
        "required_slots": tuple(_json_load(row["required_slots_json"], [])),
        "validation": _json_load(row["validation_json"], {}),
        "policy": _json_load(row["policy_json"], {}),
        "updated_at": row["updated_at"],
        "meta": _json_load(row["meta_json"], {}),
    }


def clear_dialog_frame(webspace_id: str | None = None) -> int:
    if not ensure_schema():
        return 0
    with _sql().connect() as con:  # type: ignore[union-attr]
        if webspace_id:
            cur = con.execute("DELETE FROM conversation_dialog_frames WHERE webspace_id=?", (webspace_id,))
        else:
            cur = con.execute("DELETE FROM conversation_dialog_frames")
        con.commit()
        return int(cur.rowcount or 0)


def set_active_dialog_channel(
    *,
    webspace_id: str,
    channel_id: str,
    conversation_id: str | None = None,
    active_agent_id: str | None = None,
    status: str = "active",
    meta: Mapping[str, Any] | None = None,
    ts: float | None = None,
) -> bool:
    if not ensure_schema():
        return False
    ws = str(webspace_id or "").strip() or "default"
    cid = str(channel_id or "").strip() or "general"
    now = float(ts or time.time())
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_active_dialog_channels(
                webspace_id, channel_id, conversation_id, active_agent_id,
                status, updated_at, meta_json
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(webspace_id) DO UPDATE SET
                channel_id=excluded.channel_id,
                conversation_id=COALESCE(excluded.conversation_id, conversation_active_dialog_channels.conversation_id),
                active_agent_id=COALESCE(excluded.active_agent_id, conversation_active_dialog_channels.active_agent_id),
                status=excluded.status,
                updated_at=excluded.updated_at,
                meta_json=excluded.meta_json
            """,
            (
                ws,
                cid,
                str(conversation_id or "").strip() or None,
                str(active_agent_id or "").strip() or None,
                str(status or "active").strip() or "active",
                now,
                _json_dump(dict(meta or {})),
            ),
        )
        con.commit()
    return True


def get_active_dialog_channel(webspace_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    ws = str(webspace_id or "").strip() or "default"
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT * FROM conversation_active_dialog_channels
            WHERE webspace_id=? AND status='active'
            """,
            (ws,),
        ).fetchone()
    if not row:
        return None
    return {
        "webspace_id": row["webspace_id"],
        "channel_id": row["channel_id"],
        "id": row["channel_id"],
        "conversation_id": row["conversation_id"],
        "active_agent_id": row["active_agent_id"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "meta": _json_load(row["meta_json"], {}),
    }


def latest_dialog_channel_for_webspace(webspace_id: str) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    ws = str(webspace_id or "").strip() or "default"
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT webspace_id, channel_id, conversation_id, owner, actor_id,
                   actor_label, actor_icon, route_id, ts
            FROM conversation_messages
            WHERE webspace_id=? AND COALESCE(channel_id, '') <> ''
            ORDER BY ts DESC, created_at DESC
            LIMIT 1
            """,
            (ws,),
        ).fetchone()
    if not row:
        return None
    return {
        "webspace_id": row["webspace_id"],
        "channel_id": row["channel_id"],
        "id": row["channel_id"],
        "conversation_id": row["conversation_id"],
        "owner": row["owner"],
        "active_agent_id": row["actor_id"],
        "active_agent_label": row["actor_label"],
        "active_agent_icon": row["actor_icon"],
        "route_id": row["route_id"],
        "ts": row["ts"],
        "status": "active",
        "meta": {"source": "latest_message"},
    }


def upsert_agent(record: Mapping[str, Any], *, source: str = "runtime") -> bool:
    if not ensure_schema():
        return False
    agent_id = str(record.get("id") or record.get("agent_id") or "").strip()
    label = str(record.get("label") or record.get("name") or agent_id).strip()
    owner = str(record.get("owner") or "").strip()
    if not agent_id or not label or not owner:
        return False
    aliases = record.get("aliases")
    if not isinstance(aliases, list | tuple):
        aliases = []
    voice_profile = record.get("voice_profile")
    if not isinstance(voice_profile, Mapping):
        voice_profile = {}
    meta = {
        key: value
        for key, value in dict(record).items()
        if key
        not in {
            "id",
            "agent_id",
            "label",
            "name",
            "owner",
            "channel_id",
            "skill",
            "skill_id",
            "kind",
            "character_id",
            "aliases",
            "gender",
            "voice",
            "icon",
            "voice_profile",
            "source",
            "policy",
        }
    }
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_agent_registry(
                agent_id, label, owner, channel_id, skill_id, kind, character_id,
                aliases_json, gender, voice, icon, voice_profile_json, source,
                status, updated_at, policy_json, meta_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(agent_id) DO UPDATE SET
                label=excluded.label,
                owner=excluded.owner,
                channel_id=excluded.channel_id,
                skill_id=excluded.skill_id,
                kind=excluded.kind,
                character_id=excluded.character_id,
                aliases_json=excluded.aliases_json,
                gender=excluded.gender,
                voice=excluded.voice,
                icon=excluded.icon,
                voice_profile_json=excluded.voice_profile_json,
                source=excluded.source,
                status=excluded.status,
                updated_at=excluded.updated_at,
                policy_json=excluded.policy_json,
                meta_json=excluded.meta_json
            """,
            (
                agent_id,
                label,
                owner,
                str(record.get("channel_id") or "").strip() or None,
                str(record.get("skill") or record.get("skill_id") or "").strip() or None,
                str(record.get("kind") or "agent").strip() or "agent",
                str(record.get("character_id") or "").strip() or None,
                _json_dump([str(item).strip() for item in aliases if str(item or "").strip()]),
                str(record.get("gender") or "").strip() or None,
                str(record.get("voice") or "").strip() or None,
                str(record.get("icon") or "").strip() or None,
                _json_dump(dict(voice_profile)),
                str(record.get("source") or source or "runtime").strip() or "runtime",
                "active",
                time.time(),
                _json_dump(dict(record.get("policy") or {})),
                _json_dump(meta),
            ),
        )
        con.commit()
    return True


def seed_agents(records: list[Mapping[str, Any]], *, source: str = "runtime") -> int:
    count = 0
    for record in records:
        try:
            if upsert_agent(record, source=str(record.get("source") or source)):
                count += 1
        except Exception:
            continue
    return count


def list_agents(*, channel_id: str | None = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where = []
    params: list[Any] = []
    if channel_id:
        where.append("channel_id=?")
        params.append(channel_id)
    if not include_inactive:
        where.append("status='active'")
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT * FROM conversation_agent_registry
            {sql_where}
            ORDER BY CASE WHEN channel_id='general' THEN 0 ELSE 1 END, label COLLATE NOCASE
            """,
            params,
        ).fetchall()
    return [_row_to_agent(row) for row in rows]


def append_message(
    *,
    conversation_id: str,
    thread_id: str | None = None,
    webspace_id: str,
    channel_id: str,
    owner: str,
    role: str,
    text: str,
    payload: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    actor_id: str | None = None,
    actor_label: str | None = None,
    actor_icon: str | None = None,
    route_id: str | None = None,
    request_id: str | None = None,
    turn_trace_id: str | None = None,
    idempotency_key: str | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
    ts: float | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    now = float(ts or time.time())
    message_payload = dict(payload or {})
    message_id = _normalize_id(message_payload.get("id"), "m")
    idem = str(idempotency_key or "").strip() or None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            if idem:
                existing = con.execute(
                    """
                    SELECT *
                    FROM conversation_messages
                    WHERE conversation_id=? AND idempotency_key=?
                    """,
                    (conversation_id, idem),
                ).fetchone()
                if existing:
                    con.rollback()
                    return _row_to_message(existing)
            existing_id = con.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE message_id=?
                """,
                (message_id,),
            ).fetchone()
            if existing_id:
                con.rollback()
                return _row_to_message(existing_id)
            max_seq = con.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            seq = int(max_seq or 0) + 1
            con.execute(
                """
                INSERT INTO conversation_messages(
                    message_id, conversation_id, thread_id, seq, webspace_id, channel_id, owner,
                    actor_id, actor_label, actor_icon, role, text, route_id, ts,
                    request_id, turn_trace_id, idempotency_key, retention_class,
                    retention_until, redaction_state, redacted_at, redaction_reason,
                    payload_json, meta_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    message_id,
                    conversation_id,
                    str(thread_id or "").strip() or None,
                    seq,
                    webspace_id,
                    channel_id,
                    owner,
                    actor_id,
                    actor_label,
                    actor_icon,
                    role,
                    text,
                    route_id,
                    now,
                    request_id,
                    turn_trace_id,
                    idem,
                    str(retention_class or "normal").strip() or "normal",
                    retention_until,
                    str(redaction_state or "active").strip() or "active",
                    redacted_at,
                    str(redaction_reason or "").strip() or None,
                    _json_dump(message_payload),
                    _json_dump(dict(meta or {})),
                    now,
                ),
            )
            _message_fts_upsert(
                con,
                message_id=message_id,
                conversation_id=conversation_id,
                thread_id=str(thread_id or "").strip() or None,
                webspace_id=webspace_id,
                channel_id=channel_id,
                owner=owner,
                role=role,
                text=text,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
    message_payload.update(
        {
            "id": message_id,
            "from": role,
            "text": text,
            "ts": now,
            "seq": seq,
            "conversation_id": conversation_id,
            "thread_id": str(thread_id or "").strip() or None,
            "dialog_channel_id": channel_id,
            "retention_class": str(retention_class or "normal").strip() or "normal",
            "retention_until": retention_until,
            "redaction_state": str(redaction_state or "active").strip() or "active",
            "redacted_at": redacted_at,
            "redaction_reason": str(redaction_reason or "").strip() or None,
        }
    )
    if actor_id:
        message_payload.setdefault("active_agent_id", actor_id)
    if actor_label:
        message_payload.setdefault("active_agent_label", actor_label)
    if actor_icon:
        message_payload.setdefault("active_agent_icon", actor_icon)
    if turn_trace_id:
        message_payload["turn_trace_id"] = turn_trace_id
    if meta:
        message_payload["_meta"] = dict(meta)
    return message_payload


def append_messages_batch(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Append idempotent ledger messages with one SQLite write transaction."""
    if not messages or not ensure_schema():
        return []
    rows: list[sqlite3.Row] = []
    next_seq: dict[str, int] = {}
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            for raw in messages:
                item = dict(raw)
                conversation_id = str(item.get("conversation_id") or "").strip()
                webspace_id = str(item.get("webspace_id") or "").strip()
                role = str(item.get("role") or "").strip()
                text = str(item.get("text") or "")
                if not conversation_id or not webspace_id or not role:
                    raise ValueError("conversation_id, webspace_id, and role are required")
                payload = dict(item.get("payload") or {})
                message_id = _normalize_id(payload.get("id"), "m")
                idempotency_key = str(item.get("idempotency_key") or "").strip() or None
                existing = None
                if idempotency_key:
                    existing = con.execute(
                        """
                        SELECT * FROM conversation_messages
                        WHERE conversation_id=? AND idempotency_key=?
                        """,
                        (conversation_id, idempotency_key),
                    ).fetchone()
                if existing is None:
                    existing = con.execute(
                        "SELECT * FROM conversation_messages WHERE message_id=?",
                        (message_id,),
                    ).fetchone()
                if existing is not None:
                    rows.append(existing)
                    continue
                if conversation_id not in next_seq:
                    maximum = con.execute(
                        "SELECT COALESCE(MAX(seq), 0) FROM conversation_messages WHERE conversation_id=?",
                        (conversation_id,),
                    ).fetchone()[0]
                    next_seq[conversation_id] = int(maximum or 0)
                next_seq[conversation_id] += 1
                seq = next_seq[conversation_id]
                now = float(item.get("ts") or time.time())
                thread_id = str(item.get("thread_id") or "").strip() or None
                channel_id = str(item.get("channel_id") or "").strip() or None
                owner = str(item.get("owner") or "").strip() or None
                actor_id = str(item.get("actor_id") or "").strip() or None
                actor_label = str(item.get("actor_label") or "").strip() or None
                actor_icon = str(item.get("actor_icon") or "").strip() or None
                route_id = str(item.get("route_id") or "").strip() or None
                request_id = str(item.get("request_id") or "").strip() or None
                turn_trace_id = str(item.get("turn_trace_id") or "").strip() or None
                retention_class = str(item.get("retention_class") or "normal").strip() or "normal"
                redaction_state = str(item.get("redaction_state") or "active").strip() or "active"
                con.execute(
                    """
                    INSERT INTO conversation_messages(
                        message_id, conversation_id, thread_id, seq, webspace_id, channel_id, owner,
                        actor_id, actor_label, actor_icon, role, text, route_id, ts,
                        request_id, turn_trace_id, idempotency_key, retention_class,
                        retention_until, redaction_state, redacted_at, redaction_reason,
                        payload_json, meta_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        message_id,
                        conversation_id,
                        thread_id,
                        seq,
                        webspace_id,
                        channel_id,
                        owner,
                        actor_id,
                        actor_label,
                        actor_icon,
                        role,
                        text,
                        route_id,
                        now,
                        request_id,
                        turn_trace_id,
                        idempotency_key,
                        retention_class,
                        item.get("retention_until"),
                        redaction_state,
                        item.get("redacted_at"),
                        str(item.get("redaction_reason") or "").strip() or None,
                        _json_dump(payload),
                        _json_dump(dict(item.get("meta") or {})),
                        now,
                    ),
                )
                _message_fts_upsert(
                    con,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    webspace_id=webspace_id,
                    channel_id=channel_id,
                    owner=owner,
                    role=role,
                    text=text,
                )
                inserted = con.execute(
                    "SELECT * FROM conversation_messages WHERE message_id=?",
                    (message_id,),
                ).fetchone()
                if inserted is not None:
                    rows.append(inserted)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return [_row_to_message(row) for row in rows]


def list_projection(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    before_cursor: Any = None,
    limit: int = 8,
    max_items: int = 200,
) -> dict[str, Any]:
    if not ensure_schema():
        return {
            "messages": [],
            "has_more_before": False,
            "before_cursor": "",
            "total_message_count": 0,
        }
    safe_limit = max(1, min(int(limit or 8), 64))
    safe_max = max(safe_limit, min(int(max_items or 200), 500))
    cursor: int | None = None
    try:
        cursor = int(str(before_cursor or "").strip()) if str(before_cursor or "").strip() else None
    except Exception:
        cursor = None
    thread_filter = str(thread_id or "").strip()
    where = "conversation_id=?"
    params_base: list[Any] = [conversation_id]
    if thread_filter:
        where += " AND thread_id=?"
        params_base.append(thread_filter)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        total = int(
            con.execute(
                f"SELECT COUNT(*) FROM conversation_messages WHERE {where}",
                params_base,
            ).fetchone()[0]
            or 0
        )
        if cursor and cursor >= 1:
            older = con.execute(
                """
                SELECT seq FROM conversation_messages
                WHERE {where} AND seq <= ?
                ORDER BY seq DESC
                LIMIT ?
                """.format(where=where),
                [*params_base, cursor, safe_limit],
            ).fetchall()
            start_seq = min((int(row["seq"]) for row in older), default=cursor)
            rows = con.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE {where} AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """.format(where=where),
                [*params_base, start_seq, safe_max],
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE {where}
                ORDER BY seq DESC
                LIMIT ?
                """.format(where=where),
                [*params_base, safe_limit],
            ).fetchall()
            rows = list(reversed(rows))
    messages = [_row_to_message(row) for row in rows]
    min_seq = min((int(item.get("seq") or 0) for item in messages), default=0)
    return {
        "messages": messages,
        "has_more_before": bool(min_seq > 1),
        "before_cursor": str(max(0, min_seq - 1)) if messages else "",
        "total_message_count": total,
    }


def recover_projection_from_store(
    current_projection: Mapping[str, Any] | None,
    *,
    conversation_id: str,
    thread_id: str | None = None,
    limit: int = 8,
    max_items: int = 200,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    current = dict(current_projection or {}) if isinstance(current_projection, Mapping) else {}
    store_projection = list_projection(cid, thread_id=thread_id, limit=limit, max_items=max_items)
    store_messages = [dict(item) for item in store_projection.get("messages") or [] if isinstance(item, Mapping)]
    current_messages = [dict(item) for item in current.get("messages") or [] if isinstance(item, Mapping)]
    reason = _projection_recovery_reason(
        current=current,
        current_messages=current_messages,
        store=store_projection,
        store_messages=store_messages,
        conversation_id=cid,
        thread_id=str(thread_id or "").strip() or None,
    )
    if reason and store_messages:
        recovered = dict(store_projection)
        recovered["conversation_id"] = cid
        if thread_id:
            recovered["thread_id"] = str(thread_id).strip()
        recovered["recovery"] = {
            "schema": "adaos.conversation.projection_recovery.v1",
            "recovered": True,
            "reason": reason,
            "source": "node_store",
            "previous_message_count": len(current_messages),
            "previous_total_message_count": _projection_int(current.get("total_message_count"), len(current_messages)),
            "store_total_message_count": _projection_int(store_projection.get("total_message_count"), len(store_messages)),
        }
        return recovered
    selected = dict(current) if current_messages else dict(store_projection)
    selected["conversation_id"] = cid
    if thread_id:
        selected["thread_id"] = str(thread_id).strip()
    selected.setdefault("messages", current_messages if current_messages else store_messages)
    selected.setdefault("has_more_before", bool(store_projection.get("has_more_before")))
    selected.setdefault("before_cursor", str(store_projection.get("before_cursor") or ""))
    selected.setdefault("total_message_count", _projection_int(store_projection.get("total_message_count"), len(selected["messages"])))
    selected["recovery"] = {
        "schema": "adaos.conversation.projection_recovery.v1",
        "recovered": False,
        "reason": "current_projection_usable" if current_messages else "store_projection_empty",
        "source": "current_projection" if current_messages else "node_store",
        "store_total_message_count": _projection_int(store_projection.get("total_message_count"), len(store_messages)),
    }
    return selected


def _projection_recovery_reason(
    *,
    current: Mapping[str, Any],
    current_messages: list[dict[str, Any]],
    store: Mapping[str, Any],
    store_messages: list[dict[str, Any]],
    conversation_id: str,
    thread_id: str | None,
) -> str:
    if not store_messages:
        return ""
    current_conversation_id = str(current.get("conversation_id") or "").strip()
    if current_conversation_id and current_conversation_id != conversation_id:
        return "conversation_mismatch"
    current_thread_id = str(current.get("thread_id") or current.get("conversation_topic_id") or "").strip()
    if thread_id and current_thread_id and current_thread_id != thread_id:
        return "thread_mismatch"
    if not current_messages:
        return "empty_projection"
    current_total = _projection_int(current.get("total_message_count"), len(current_messages))
    store_total = _projection_int(store.get("total_message_count"), len(store_messages))
    if store_total > current_total:
        return "stale_total"
    if _projection_tail_seq(store_messages) > _projection_tail_seq(current_messages):
        return "stale_tail"
    if _projection_tail_signature(store_messages) != _projection_tail_signature(current_messages):
        return "tail_mismatch"
    return ""


def _projection_tail_seq(messages: list[dict[str, Any]]) -> int:
    return max((_projection_int(item.get("seq"), 0) for item in messages), default=0)


def _projection_tail_signature(messages: list[dict[str, Any]]) -> tuple[tuple[int, str, str, str], ...]:
    tail = messages[-min(len(messages), 16) :]
    return tuple(
        (
            _projection_int(item.get("seq"), 0),
            str(item.get("id") or ""),
            str(item.get("from") or item.get("role") or ""),
            str(item.get("text") or ""),
        )
        for item in tail
    )


def _projection_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback or 0)


def list_messages(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    limit: int = 500,
    ascending: bool = True,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    safe_limit = max(1, min(int(limit or 500), 5000))
    thread_filter = str(thread_id or "").strip()
    where = "conversation_id=?"
    params: list[Any] = [conversation_id]
    if thread_filter:
        where += " AND thread_id=?"
        params.append(thread_filter)
    order = "ASC" if ascending else "DESC"
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_messages
            WHERE {where}
            ORDER BY seq {order}
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_message(row) for row in rows]


def search_messages(
    query: str,
    *,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    owner: str | None = None,
    channel_id: str | None = None,
    limit: int = 50,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    token = str(query or "").strip()
    if not token or not ensure_schema():
        return []
    safe_limit = max(1, min(int(limit or 50), 200))
    filters: list[str] = []
    params: list[Any] = []
    if conversation_id:
        filters.append("m.conversation_id=?")
        params.append(conversation_id)
    if thread_id:
        filters.append("m.thread_id=?")
        params.append(thread_id)
    if owner:
        filters.append("m.owner=?")
        params.append(owner)
    if channel_id:
        filters.append("m.channel_id=?")
        params.append(channel_id)
    if not include_redacted:
        filters.append("m.redaction_state!='redacted'")
    filter_sql = f"AND {' AND '.join(filters)}" if filters else ""
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        if _ensure_fts(con):
            try:
                rows = con.execute(
                    f"""
                    SELECT m.*, bm25(conversation_messages_fts) AS search_rank
                    FROM conversation_messages_fts
                    JOIN conversation_messages m ON m.message_id=conversation_messages_fts.message_id
                    WHERE conversation_messages_fts MATCH ? {filter_sql}
                    ORDER BY search_rank ASC, m.ts DESC
                    LIMIT ?
                    """,
                    [_fts_query(token), *params, safe_limit],
                ).fetchall()
                results = [_row_to_message(row) for row in rows]
                for index, item in enumerate(results):
                    item["search"] = {"backend": "fts", "rank": float(rows[index]["search_rank"] or 0.0)}
                return results
            except sqlite3.Error:
                pass
        like_filters = ["m.text LIKE ?"]
        like_params: list[Any] = [f"%{token}%"]
        if conversation_id:
            like_filters.append("m.conversation_id=?")
            like_params.append(conversation_id)
        if thread_id:
            like_filters.append("m.thread_id=?")
            like_params.append(thread_id)
        if owner:
            like_filters.append("m.owner=?")
            like_params.append(owner)
        if channel_id:
            like_filters.append("m.channel_id=?")
            like_params.append(channel_id)
        if not include_redacted:
            like_filters.append("m.redaction_state!='redacted'")
        rows = con.execute(
            f"""
            SELECT m.*
            FROM conversation_messages m
            WHERE {' AND '.join(like_filters)}
            ORDER BY m.ts DESC
            LIMIT ?
            """,
            [*like_params, safe_limit],
        ).fetchall()
    results = [_row_to_message(row) for row in rows]
    for item in results:
        item["search"] = {"backend": "like", "rank": None}
    return results


def rebuild_conversation_segments(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    segment_size: int = 40,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    if not ensure_schema():
        return {"schema": "adaos.conversation.segment_rebuild.v1", "ok": False, "status": "unavailable"}
    clean_thread = str(thread_id or "").strip() or None
    safe_size = max(2, min(int(segment_size or 40), 200))
    where = "conversation_id=? AND redaction_state!='redacted'"
    params: list[Any] = [cid]
    if clean_thread is not None:
        where += " AND thread_id=?"
        params.append(clean_thread)
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_messages
            WHERE {where}
            ORDER BY seq ASC
            """,
            params,
        ).fetchall()
        if clean_thread is None:
            con.execute("DELETE FROM conversation_segments WHERE conversation_id=? AND thread_id IS NULL", (cid,))
            if _ensure_fts(con):
                con.execute("DELETE FROM conversation_segments_fts WHERE conversation_id=? AND thread_id IS NULL", (cid,))
        else:
            con.execute("DELETE FROM conversation_segments WHERE conversation_id=? AND thread_id=?", (cid, clean_thread))
            if _ensure_fts(con):
                con.execute("DELETE FROM conversation_segments_fts WHERE conversation_id=? AND thread_id=?", (cid, clean_thread))
        count = 0
        for index in range(0, len(rows), safe_size):
            chunk = rows[index : index + safe_size]
            if not chunk:
                continue
            messages = [_row_to_message(row) for row in chunk]
            start_seq = int(messages[0].get("seq") or 0)
            end_seq = int(messages[-1].get("seq") or 0)
            segment_id = f"seg.{cid}.{clean_thread or 'root'}.{start_seq}.{end_seq}"
            summary = _segment_summary_text(messages)
            refs = [
                {
                    "type": "conversation_message",
                    "conversation_id": cid,
                    "message_id": str(item.get("id") or ""),
                    "seq": int(item.get("seq") or 0),
                }
                for item in messages
            ]
            retention_class = "ephemeral" if any(str(item.get("retention_class") or "") == "ephemeral" for item in messages) else "normal"
            con.execute(
                """
                INSERT INTO conversation_segments(
                    segment_id, conversation_id, thread_id, start_seq, end_seq,
                    message_count, summary, source_refs_json, retention_class,
                    redaction_state, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    segment_id,
                    cid,
                    clean_thread,
                    start_seq,
                    end_seq,
                    len(messages),
                    summary,
                    _json_dump(refs),
                    retention_class,
                    "active",
                    now,
                    now,
                ),
            )
            _segment_fts_upsert(
                con,
                segment_id=segment_id,
                conversation_id=cid,
                thread_id=clean_thread,
                summary=summary,
            )
            count += 1
        con.commit()
    return {
        "schema": "adaos.conversation.segment_rebuild.v1",
        "ok": True,
        "status": "rebuilt",
        "conversation_id": cid,
        "thread_id": clean_thread,
        "segment_size": safe_size,
        "message_count": len(rows),
        "segment_count": count,
    }


def compact_conversation_history(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    keep_last_messages: int = 40,
    segment_size: int = 40,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    if not ensure_schema():
        return {"schema": "adaos.conversation.summary_compaction.v1", "ok": False, "status": "unavailable"}
    clean_thread = str(thread_id or "").strip() or None
    safe_keep = max(0, min(int(keep_last_messages or 0), 5000))
    safe_size = max(2, min(int(segment_size or 40), 200))
    where = ["conversation_id=?", "redaction_state!='redacted'"]
    params: list[Any] = [cid]
    if clean_thread is not None:
        where.append("thread_id=?")
        params.append(clean_thread)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        message_rows = con.execute(
            f"""
            SELECT message_id, seq
            FROM conversation_messages
            WHERE {' AND '.join(where)}
            ORDER BY seq ASC
            """,
            params,
        ).fetchall()
    message_count = len(message_rows)
    if message_count == 0:
        return {
            "schema": "adaos.conversation.summary_compaction.v1",
            "ok": True,
            "status": "empty",
            "conversation_id": cid,
            "thread_id": clean_thread,
            "message_count": 0,
            "segment_count": 0,
            "compacted_message_count": 0,
            "raw_tail_count": 0,
            "summary_refs": [],
            "raw_tail_refs": [],
        }
    if message_count <= safe_keep:
        tail_refs = [_message_range_ref(cid, row) for row in message_rows]
        return {
            "schema": "adaos.conversation.summary_compaction.v1",
            "ok": True,
            "status": "up_to_date",
            "conversation_id": cid,
            "thread_id": clean_thread,
            "message_count": message_count,
            "segment_count": 0,
            "compacted_message_count": 0,
            "raw_tail_count": len(tail_refs),
            "summary_refs": [],
            "raw_tail_refs": tail_refs,
        }

    tail_rows = message_rows[-safe_keep:] if safe_keep else []
    tail_start_seq = int(tail_rows[0]["seq"]) if tail_rows else int(message_rows[-1]["seq"]) + 1
    rebuild = rebuild_conversation_segments(cid, thread_id=clean_thread, segment_size=safe_size)
    if not rebuild.get("ok"):
        return {
            "schema": "adaos.conversation.summary_compaction.v1",
            "ok": False,
            "status": str(rebuild.get("status") or "segment_rebuild_failed"),
            "conversation_id": cid,
            "thread_id": clean_thread,
            "rebuild": rebuild,
        }
    segments = sorted(
        (
            item
            for item in list_conversation_segments(cid, thread_id=clean_thread, limit=5000)
            if int(item.get("start_seq") or 0) < tail_start_seq
        ),
        key=lambda item: int(item.get("start_seq") or 0),
    )
    summary_refs = [
        {
            "type": "conversation_segment",
            "segment_id": str(item.get("id") or item.get("segment_id") or ""),
            "conversation_id": cid,
            "thread_id": item.get("thread_id"),
            "start_seq": int(item.get("start_seq") or 0),
            "end_seq": int(item.get("end_seq") or 0),
            "message_count": int(item.get("message_count") or 0),
            "source_refs": list(item.get("source_refs") or []),
        }
        for item in segments
    ]
    tail_refs = [_message_range_ref(cid, row) for row in tail_rows]
    compacted_message_count = len([row for row in message_rows if int(row["seq"]) < tail_start_seq])
    return {
        "schema": "adaos.conversation.summary_compaction.v1",
        "ok": True,
        "status": "compacted",
        "conversation_id": cid,
        "thread_id": clean_thread,
        "message_count": message_count,
        "segment_count": len(summary_refs),
        "compacted_message_count": compacted_message_count,
        "raw_tail_count": len(tail_refs),
        "tail_start_seq": tail_start_seq,
        "summary_refs": summary_refs,
        "raw_tail_refs": tail_refs,
        "rebuild": rebuild,
    }


def _message_range_ref(conversation_id: str, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "conversation_message",
        "conversation_id": conversation_id,
        "message_id": str(_row_value(row, "message_id") or _row_value(row, "id") or ""),
        "seq": int(_row_value(row, "seq") or 0),
    }


def list_conversation_segments(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    limit: int = 20,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    clean_thread = str(thread_id or "").strip()
    where = ["conversation_id=?"]
    params: list[Any] = [conversation_id]
    if clean_thread:
        where.append("thread_id=?")
        params.append(clean_thread)
    if not include_redacted:
        where.append("redaction_state!='redacted'")
    safe_limit = max(1, min(int(limit or 20), 200))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_segments
            WHERE {' AND '.join(where)}
            ORDER BY end_seq DESC
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_segment(row) for row in rows]


def search_conversation_segments(
    query: str,
    *,
    conversation_id: str | None = None,
    thread_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    token = str(query or "").strip()
    if not token or not ensure_schema():
        return []
    safe_limit = max(1, min(int(limit or 20), 200))
    filters: list[str] = ["s.redaction_state!='redacted'"]
    params: list[Any] = []
    if conversation_id:
        filters.append("s.conversation_id=?")
        params.append(conversation_id)
    if thread_id:
        filters.append("s.thread_id=?")
        params.append(thread_id)
    filter_sql = f"AND {' AND '.join(filters)}" if filters else ""
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        if _ensure_fts(con):
            try:
                rows = con.execute(
                    f"""
                    SELECT s.*, bm25(conversation_segments_fts) AS search_rank
                    FROM conversation_segments_fts
                    JOIN conversation_segments s ON s.segment_id=conversation_segments_fts.segment_id
                    WHERE conversation_segments_fts MATCH ? {filter_sql}
                    ORDER BY search_rank ASC, s.end_seq DESC
                    LIMIT ?
                    """,
                    [_fts_query(token), *params, safe_limit],
                ).fetchall()
                results = [_row_to_segment(row) for row in rows]
                for index, item in enumerate(results):
                    item["search"] = {"backend": "fts", "rank": float(rows[index]["search_rank"] or 0.0)}
                return results
            except sqlite3.Error:
                pass
        like_filters = ["summary LIKE ?", "redaction_state!='redacted'"]
        like_params: list[Any] = [f"%{token}%"]
        if conversation_id:
            like_filters.append("conversation_id=?")
            like_params.append(conversation_id)
        if thread_id:
            like_filters.append("thread_id=?")
            like_params.append(thread_id)
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_segments
            WHERE {' AND '.join(like_filters)}
            ORDER BY end_seq DESC
            LIMIT ?
            """,
            [*like_params, safe_limit],
        ).fetchall()
    results = [_row_to_segment(row) for row in rows]
    for item in results:
        item["search"] = {"backend": "like", "rank": None}
    return results


def segment_summary_health(conversation_id: str, *, thread_id: str | None = None) -> dict[str, Any]:
    if not ensure_schema():
        return {"schema": "adaos.conversation.segment_summary_health.v1", "status": "unavailable"}
    clean_thread = str(thread_id or "").strip()
    msg_where = ["conversation_id=?", "redaction_state!='redacted'"]
    msg_params: list[Any] = [conversation_id]
    seg_where = ["conversation_id=?", "redaction_state!='redacted'"]
    seg_params: list[Any] = [conversation_id]
    if clean_thread:
        msg_where.append("thread_id=?")
        msg_params.append(clean_thread)
        seg_where.append("thread_id=?")
        seg_params.append(clean_thread)
    with _sql().connect() as con:  # type: ignore[union-attr]
        message_row = con.execute(
            f"SELECT COUNT(*), COALESCE(MAX(seq), 0) FROM conversation_messages WHERE {' AND '.join(msg_where)}",
            msg_params,
        ).fetchone()
        segment_row = con.execute(
            f"SELECT COUNT(*), COALESCE(MAX(end_seq), 0), COALESCE(SUM(message_count), 0) FROM conversation_segments WHERE {' AND '.join(seg_where)}",
            seg_params,
        ).fetchone()
    message_count = int(message_row[0] or 0)
    latest_seq = int(message_row[1] or 0)
    segment_count = int(segment_row[0] or 0)
    summarized_until_seq = int(segment_row[1] or 0)
    summarized_message_count = int(segment_row[2] or 0)
    if message_count == 0:
        status = "ok"
    elif segment_count == 0:
        status = "missing"
    elif summarized_until_seq < latest_seq or summarized_message_count < message_count:
        status = "stale"
    else:
        status = "ok"
    return {
        "schema": "adaos.conversation.segment_summary_health.v1",
        "status": status,
        "conversation_id": conversation_id,
        "thread_id": clean_thread or None,
        "message_count": message_count,
        "segment_count": segment_count,
        "summarized_message_count": summarized_message_count,
        "summarized_until_seq": summarized_until_seq,
        "latest_seq": latest_seq,
    }


def _segment_summary_text(messages: list[Mapping[str, Any]]) -> str:
    if not messages:
        return ""
    first = str(messages[0].get("text") or "").strip()
    last = str(messages[-1].get("text") or "").strip()
    roles = ", ".join(str(item.get("from") or item.get("role") or "unknown") for item in messages[:6])
    first = first[:180]
    last = last[:180]
    if len(messages) == 1 or first == last:
        return f"{len(messages)} message seq {messages[0].get('seq')}: {first}"
    return f"{len(messages)} messages seq {messages[0].get('seq')}-{messages[-1].get('seq')} ({roles}). First: {first} Last: {last}"


def remember(
    *,
    scope: str,
    owner: str,
    subject_id: str | None = None,
    key: str | None = None,
    text: str | None = None,
    value: Mapping[str, Any] | None = None,
    confidence: float | None = None,
    consent_state: str = "unknown",
    visibility: str | None = None,
    policy: Mapping[str, Any] | None = None,
    source_ref: Mapping[str, Any] | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
    memory_id: str | None = None,
) -> str | None:
    if not ensure_schema():
        return None
    mid = _normalize_id(memory_id, "mem")
    now = time.time()
    stored_policy = dict(policy or {})
    clean_visibility = str(visibility or stored_policy.get("visibility") or "").strip()
    if clean_visibility:
        stored_policy["visibility"] = clean_visibility
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_memory_items(
                memory_id, scope, owner, subject_id, key, text, value_json,
                confidence, consent_state, retention_class, retention_until,
                redaction_state, redacted_at, redaction_reason, policy_json,
                source_ref_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(memory_id) DO UPDATE SET
                scope=excluded.scope,
                owner=excluded.owner,
                subject_id=excluded.subject_id,
                key=excluded.key,
                text=excluded.text,
                value_json=excluded.value_json,
                confidence=excluded.confidence,
                consent_state=excluded.consent_state,
                retention_class=excluded.retention_class,
                retention_until=excluded.retention_until,
                redaction_state=excluded.redaction_state,
                redacted_at=excluded.redacted_at,
                redaction_reason=excluded.redaction_reason,
                policy_json=excluded.policy_json,
                source_ref_json=excluded.source_ref_json,
                updated_at=excluded.updated_at
            """,
            (
                mid,
                scope,
                owner,
                subject_id,
                key,
                text,
                _json_dump(dict(value or {})),
                confidence,
                consent_state,
                str(retention_class or "normal").strip() or "normal",
                retention_until,
                str(redaction_state or "active").strip() or "active",
                redacted_at,
                str(redaction_reason or "").strip() or None,
                _json_dump(stored_policy),
                _json_dump(dict(source_ref or {})),
                now,
                now,
            ),
        )
        _memory_fts_upsert(
            con,
            memory_id=mid,
            scope=scope,
            owner=owner,
            subject_id=subject_id,
            key=key,
            text=text,
        )
        con.commit()
    return mid


def list_memory(
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    if scope:
        where.append("scope=?")
        params.append(scope)
    if owner:
        where.append("owner=?")
        params.append(owner)
    if subject_id:
        where.append("subject_id=?")
        params.append(subject_id)
    if not include_redacted:
        where.append("redaction_state!='redacted'")
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(int(limit or 50), 200)))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT * FROM conversation_memory_items
            {sql_where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_memory(row) for row in rows]


def search_memory(
    query: str,
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    token = str(query or "").strip()
    if not token:
        return list_memory(
            scope=scope,
            owner=owner,
            subject_id=subject_id,
            limit=limit,
            include_redacted=include_redacted,
        )
    if not ensure_schema():
        return []
    safe_limit = max(1, min(int(limit or 50), 200))
    filters: list[str] = []
    params: list[Any] = []
    if scope:
        filters.append("m.scope=?")
        params.append(scope)
    if owner:
        filters.append("m.owner=?")
        params.append(owner)
    if subject_id:
        filters.append("m.subject_id=?")
        params.append(subject_id)
    if not include_redacted:
        filters.append("m.redaction_state!='redacted'")
    filter_sql = f"AND {' AND '.join(filters)}" if filters else ""
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        if _ensure_fts(con):
            try:
                rows = con.execute(
                    f"""
                    SELECT m.*, bm25(conversation_memory_fts) AS search_rank
                    FROM conversation_memory_fts
                    JOIN conversation_memory_items m ON m.memory_id=conversation_memory_fts.memory_id
                    WHERE conversation_memory_fts MATCH ? {filter_sql}
                    ORDER BY search_rank ASC, m.updated_at DESC
                    LIMIT ?
                    """,
                    [_fts_query(token), *params, safe_limit],
                ).fetchall()
                items = [_row_to_memory(row) for row in rows]
                for index, item in enumerate(items):
                    item["search"] = {"backend": "fts", "rank": float(rows[index]["search_rank"] or 0.0)}
                return items
            except sqlite3.Error:
                pass
        where = ["(m.text LIKE ? OR m.key LIKE ?)"]
        like_params: list[Any] = [f"%{token}%", f"%{token}%"]
        if scope:
            where.append("m.scope=?")
            like_params.append(scope)
        if owner:
            where.append("m.owner=?")
            like_params.append(owner)
        if subject_id:
            where.append("m.subject_id=?")
            like_params.append(subject_id)
        if not include_redacted:
            where.append("m.redaction_state!='redacted'")
        rows = con.execute(
            f"""
            SELECT m.* FROM conversation_memory_items m
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE WHEN m.key=? THEN 0 ELSE 1 END,
                m.updated_at DESC
            LIMIT ?
            """,
            [*like_params, token, safe_limit],
        ).fetchall()
    items = [_row_to_memory(row) for row in rows]
    for item in items:
        item["search"] = {"backend": "like", "rank": None}
    return items


def forget_memory(
    *,
    memory_id: str | None = None,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    key: str | None = None,
    reason: str = "user_request",
    hard_delete: bool = False,
) -> int:
    if not ensure_schema():
        return 0
    where: list[str] = []
    params: list[Any] = []
    if memory_id:
        where.append("memory_id=?")
        params.append(memory_id)
    if scope:
        where.append("scope=?")
        params.append(scope)
    if owner:
        where.append("owner=?")
        params.append(owner)
    if subject_id:
        where.append("subject_id=?")
        params.append(subject_id)
    if key:
        where.append("key=?")
        params.append(key)
    if not where:
        raise ValueError("memory_id or scoped selector is required")
    sql_where = " AND ".join(where)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        audit_conversation_id = _memory_selector_conversation_id(con, sql_where, params)
        if hard_delete:
            cur = con.execute(f"DELETE FROM conversation_memory_items WHERE {sql_where}", params)
        else:
            cur = con.execute(
                f"""
                UPDATE conversation_memory_items
                SET redaction_state='redacted',
                    redacted_at=?,
                    redaction_reason=?,
                    updated_at=?
                WHERE {sql_where}
                """,
                [time.time(), str(reason or "user_request").strip() or "user_request", time.time(), *params],
        )
        rowcount = int(cur.rowcount or 0)
        _append_audit_event_with_connection(
            con,
            event_type="conversation.privacy",
            action="hard_delete_memory" if hard_delete else "redact_memory",
            conversation_id=audit_conversation_id,
            status="completed" if rowcount else "not_found",
            reason=str(reason or "user_request").strip() or "user_request",
            counts={"memory": rowcount},
            meta={
                "memory_id": str(memory_id or "").strip() or None,
                "scope": str(scope or "").strip() or None,
                "owner": str(owner or "").strip() or None,
                "subject_id": str(subject_id or "").strip() or None,
                "key": str(key or "").strip() or None,
            },
        )
        return rowcount


def export_memory(
    *,
    memory_id: str | None = None,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    key: str | None = None,
    include_redacted: bool = False,
    limit: int = 5000,
) -> dict[str, Any]:
    if not ensure_schema():
        return {
            "schema": "adaos.conversation.memory_export.v1",
            "ok": False,
            "memory": [],
            "counts": {"memory": 0},
            "error": "conversation_store_unavailable",
        }
    where: list[str] = []
    params: list[Any] = []
    if memory_id:
        where.append("memory_id=?")
        params.append(memory_id)
    if scope:
        where.append("scope=?")
        params.append(scope)
    if owner:
        where.append("owner=?")
        params.append(owner)
    if subject_id:
        where.append("subject_id=?")
        params.append(subject_id)
    if key:
        where.append("key=?")
        params.append(key)
    if not where:
        raise ValueError("memory_id or scoped selector is required")
    if not include_redacted:
        where.append("redaction_state!='redacted'")
    sql_where = " AND ".join(where)
    safe_limit = max(1, min(int(limit or 5000), 5000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_memory_items
            WHERE {sql_where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
        items = [_row_to_memory(row) for row in rows]
        audit_conversation_id = _memory_items_conversation_id(items)
        audit = _append_audit_event_with_connection(
            con,
            event_type="conversation.privacy",
            action="export_memory",
            conversation_id=audit_conversation_id,
            status="completed",
            counts={"memory": len(items)},
            meta={
                "memory_id": str(memory_id or "").strip() or None,
                "scope": str(scope or "").strip() or None,
                "owner": str(owner or "").strip() or None,
                "subject_id": str(subject_id or "").strip() or None,
                "key": str(key or "").strip() or None,
                "include_redacted": bool(include_redacted),
                "limit": safe_limit,
            },
        )
    result = {
        "schema": "adaos.conversation.memory_export.v1",
        "ok": True,
        "memory": items,
        "include_redacted": bool(include_redacted),
        "counts": {"memory": len(items)},
    }
    if audit:
        result["audit_event_id"] = audit["audit_event_id"]
    return result


def _memory_selector_conversation_id(
    con: sqlite3.Connection,
    sql_where: str,
    params: Sequence[Any],
) -> str | None:
    rows = con.execute(
        f"""
        SELECT scope, subject_id
        FROM conversation_memory_items
        WHERE {sql_where}
        LIMIT 20
        """,
        list(params),
    ).fetchall()
    return _memory_items_conversation_id(
        [
            {
                "scope": str(row["scope"] or ""),
                "subject_id": str(row["subject_id"] or ""),
            }
            for row in rows
        ]
    )


def _memory_items_conversation_id(items: Sequence[Mapping[str, Any]]) -> str | None:
    conversation_ids = {
        str(item.get("subject_id") or "").strip()
        for item in items
        if str(item.get("scope") or "").strip() == "conversation"
        and str(item.get("subject_id") or "").strip()
    }
    if len(conversation_ids) == 1:
        return next(iter(conversation_ids))
    return None


def record_memory_consent(
    *,
    scope: str,
    owner: str,
    subject_id: str | None = None,
    consent_state: str,
    actor_owner: str | None = None,
    actor_id: str | None = None,
    reason: str = "user_request",
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    clean_scope = str(scope or "").strip()
    clean_owner = str(owner or "").strip()
    clean_subject = str(subject_id or "").strip() or None
    clean_state = str(consent_state or "").strip()
    if not clean_scope or not clean_owner or not clean_state:
        raise ValueError("scope, owner and consent_state are required")
    where = ["scope=?", "owner=?"]
    params: list[Any] = [clean_scope, clean_owner]
    if clean_subject is not None:
        where.append("subject_id=?")
        params.append(clean_subject)
    now = time.time()
    action = "revoke_memory_consent" if clean_state in {"revoked", "denied"} else "grant_memory_consent"
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT memory_id, policy_json FROM conversation_memory_items WHERE {' AND '.join(where)}",
            params,
        ).fetchall()
        for row in rows:
            stored_policy = _json_load(row["policy_json"], {})
            if not isinstance(stored_policy, dict):
                stored_policy = {}
            stored_policy.setdefault("consent_history", [])
            history = stored_policy["consent_history"] if isinstance(stored_policy["consent_history"], list) else []
            history.append({"state": clean_state, "reason": reason, "ts": now, **dict(policy or {})})
            stored_policy["consent_history"] = history[-20:]
            con.execute(
                """
                UPDATE conversation_memory_items
                SET consent_state=?, policy_json=?, updated_at=?
                WHERE memory_id=?
                """,
                (clean_state, _json_dump(stored_policy), now, row["memory_id"]),
            )
        event = _append_audit_event_with_connection(
            con,
            event_type="conversation.memory.consent.v1",
            action=action,
            actor_owner=actor_owner or owner,
            actor_id=actor_id,
            reason=reason,
            counts={"memory": len(rows)},
            meta={
                "scope": clean_scope,
                "owner": clean_owner,
                "subject_id": clean_subject,
                "consent_state": clean_state,
                "policy": dict(policy or {}),
            },
        )
        con.commit()
    return event


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    policy = _json_load(row["policy_json"], {})
    if not isinstance(policy, dict):
        policy = {}
    return {
        "id": row["memory_id"],
        "scope": row["scope"],
        "owner": row["owner"],
        "subject_id": row["subject_id"],
        "key": row["key"],
        "text": row["text"],
        "value": _json_load(row["value_json"], {}),
        "confidence": row["confidence"],
        "consent_state": row["consent_state"],
        "retention_class": row["retention_class"],
        "retention_until": row["retention_until"],
        "redaction_state": row["redaction_state"],
        "redacted_at": row["redacted_at"],
        "redaction_reason": row["redaction_reason"],
        "visibility": str(policy.get("visibility") or "owner_only"),
        "policy": policy,
        "source_ref": _json_load(row["source_ref_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_turn_trace(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "turn_trace_id": row["turn_trace_id"],
        "conversation_id": row["conversation_id"],
        "message_id": row["message_id"],
        "webspace_id": row["webspace_id"],
        "channel_id": row["channel_id"],
        "agent_id": row["agent_id"],
        "selected_tool": row["selected_tool"],
        "policy_decision": _json_load(row["policy_decision_json"], {}),
        "renderer": _json_load(row["renderer_json"], {}),
        "status": row["status"],
        "summary": row["summary"],
        "retention_class": _row_value(row, "retention_class", "normal"),
        "retention_until": _row_value(row, "retention_until"),
        "redaction_state": _row_value(row, "redaction_state", "active"),
        "redacted_at": _row_value(row, "redacted_at"),
        "redaction_reason": _row_value(row, "redaction_reason"),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


def _row_to_audit_event(row: sqlite3.Row) -> dict[str, Any]:
    counts = _json_load(row["counts_json"], {})
    meta = _json_load(row["meta_json"], {})
    return {
        "audit_event_id": row["audit_event_id"],
        "event_type": row["event_type"],
        "action": row["action"],
        "status": row["status"],
        "conversation_id": row["conversation_id"],
        "actor_owner": row["actor_owner"],
        "actor_id": row["actor_id"],
        "reason": row["reason"],
        "counts": counts if isinstance(counts, dict) else {},
        "meta": meta if isinstance(meta, dict) else {},
        "created_at": row["created_at"],
    }


def start_turn_trace(
    *,
    webspace_id: str,
    conversation_id: str | None,
    channel_id: str | None,
    agent_id: str | None = None,
    selected_tool: str | None = None,
    policy_decision: Mapping[str, Any] | None = None,
    renderer: Mapping[str, Any] | None = None,
    message_id: str | None = None,
    turn_trace_id: str | None = None,
    summary: str | None = None,
) -> str | None:
    if not ensure_schema():
        return None
    trace_id = _normalize_id(turn_trace_id, "trace")
    now = time.time()
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            """
            INSERT INTO conversation_turn_traces(
                turn_trace_id, conversation_id, message_id, webspace_id, channel_id,
                agent_id, selected_tool, policy_decision_json, renderer_json,
                status, summary, created_at, completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(turn_trace_id) DO UPDATE SET
                conversation_id=COALESCE(excluded.conversation_id, conversation_turn_traces.conversation_id),
                message_id=COALESCE(excluded.message_id, conversation_turn_traces.message_id),
                webspace_id=excluded.webspace_id,
                channel_id=COALESCE(excluded.channel_id, conversation_turn_traces.channel_id),
                agent_id=COALESCE(excluded.agent_id, conversation_turn_traces.agent_id),
                selected_tool=COALESCE(excluded.selected_tool, conversation_turn_traces.selected_tool),
                policy_decision_json=excluded.policy_decision_json,
                renderer_json=excluded.renderer_json,
                status=excluded.status,
                summary=COALESCE(excluded.summary, conversation_turn_traces.summary)
            """,
            (
                trace_id,
                conversation_id,
                message_id,
                webspace_id,
                channel_id,
                agent_id,
                selected_tool,
                _json_dump(dict(policy_decision or {})),
                _json_dump(dict(renderer or {})),
                "started",
                summary,
                now,
                None,
            ),
        )
        con.commit()
    return trace_id


def get_turn_trace(turn_trace_id: str) -> dict[str, Any] | None:
    trace_id = str(turn_trace_id or "").strip()
    if not trace_id or not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM conversation_turn_traces WHERE turn_trace_id=?",
            (trace_id,),
        ).fetchone()
    return _row_to_turn_trace(row) if row else None


def finish_turn_trace(
    turn_trace_id: str,
    *,
    status: str = "completed",
    summary: str | None = None,
    renderer: Mapping[str, Any] | None = None,
) -> bool:
    trace_id = str(turn_trace_id or "").strip()
    if not trace_id or not ensure_schema():
        return False
    fields = ["status=?", "completed_at=?"]
    params: list[Any] = [status, time.time()]
    if summary is not None:
        fields.append("summary=?")
        params.append(summary)
    if renderer is not None:
        fields.append("renderer_json=?")
        params.append(_json_dump(dict(renderer)))
    params.append(trace_id)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.execute(
            f"UPDATE conversation_turn_traces SET {', '.join(fields)} WHERE turn_trace_id=?",
            params,
        )
        con.commit()
    return True


def latest_turn_trace(*, webspace_id: str, conversation_id: str | None = None) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    where = ["webspace_id=?"]
    params: list[Any] = [webspace_id]
    if conversation_id:
        where.append("conversation_id=?")
        params.append(conversation_id)
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        row = con.execute(
            f"""
            SELECT * FROM conversation_turn_traces
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    return _row_to_turn_trace(row)


def list_turn_traces(
    *,
    conversation_id: str | None = None,
    webspace_id: str | None = None,
    limit: int = 500,
    ascending: bool = True,
    include_redacted: bool = False,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    if conversation_id:
        where.append("conversation_id=?")
        params.append(conversation_id)
    if webspace_id:
        where.append("webspace_id=?")
        params.append(webspace_id)
    if not include_redacted:
        where.append("redaction_state!='redacted'")
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    order = "ASC" if ascending else "DESC"
    safe_limit = max(1, min(int(limit or 500), 5000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_turn_traces
            {sql_where}
            ORDER BY created_at {order}
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_turn_trace(row) for row in rows]


def append_audit_event(
    *,
    event_type: str,
    action: str,
    conversation_id: str | None = None,
    status: str = "completed",
    actor_owner: str | None = None,
    actor_id: str | None = None,
    reason: str | None = None,
    counts: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    audit_event_id: str | None = None,
) -> dict[str, Any] | None:
    if not ensure_schema():
        return None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        return _append_audit_event_with_connection(
            con,
            event_type=event_type,
            action=action,
            conversation_id=conversation_id,
            status=status,
            actor_owner=actor_owner,
            actor_id=actor_id,
            reason=reason,
            counts=counts,
            meta=meta,
            audit_event_id=audit_event_id,
        )


def _append_audit_event_with_connection(
    con: sqlite3.Connection,
    *,
    event_type: str,
    action: str,
    conversation_id: str | None = None,
    status: str = "completed",
    actor_owner: str | None = None,
    actor_id: str | None = None,
    reason: str | None = None,
    counts: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    audit_event_id: str | None = None,
) -> dict[str, Any]:
    event_id = _normalize_id(audit_event_id, "audit.conversation")
    now = time.time()
    con.execute(
        """
        INSERT INTO conversation_audit_events(
            audit_event_id, event_type, action, status, conversation_id,
            actor_owner, actor_id, reason, counts_json, meta_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            str(event_type or "conversation.audit").strip() or "conversation.audit",
            str(action or "unknown").strip() or "unknown",
            str(status or "completed").strip() or "completed",
            str(conversation_id or "").strip() or None,
            str(actor_owner or "").strip() or None,
            str(actor_id or "").strip() or None,
            str(reason or "").strip() or None,
            _json_dump(dict(counts or {})),
            _json_dump(dict(meta or {})),
            now,
        ),
    )
    con.commit()
    row = con.execute(
        "SELECT * FROM conversation_audit_events WHERE audit_event_id=?",
        (event_id,),
    ).fetchone()
    return _row_to_audit_event(row)


def list_audit_events(
    *,
    conversation_id: str | None = None,
    event_type: str | None = None,
    action: str | None = None,
    limit: int = 500,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    if not ensure_schema():
        return []
    where: list[str] = []
    params: list[Any] = []
    if conversation_id:
        where.append("conversation_id=?")
        params.append(conversation_id)
    if event_type:
        where.append("event_type=?")
        params.append(event_type)
    if action:
        where.append("action=?")
        params.append(action)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    order = "ASC" if ascending else "DESC"
    safe_limit = max(1, min(int(limit or 500), 5000))
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT *
            FROM conversation_audit_events
            {sql_where}
            ORDER BY created_at {order}
            LIMIT ?
            """,
            [*params, safe_limit],
        ).fetchall()
    return [_row_to_audit_event(row) for row in rows]


def export_conversation(
    conversation_id: str,
    *,
    include_redacted: bool = False,
    include_memory: bool = True,
    include_traces: bool = True,
    limit: int = 5000,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    conversation = get_conversation(cid, include_redacted=include_redacted)
    messages = [
        item
        for item in list_messages(cid, limit=limit)
        if include_redacted or str(item.get("redaction_state") or "active") != "redacted"
    ]
    memory: list[dict[str, Any]] = []
    if include_memory:
        memory = list_memory(
            scope="conversation",
            subject_id=cid,
            limit=limit,
            include_redacted=include_redacted,
        )
    traces = (
        list_turn_traces(conversation_id=cid, limit=limit, include_redacted=include_redacted)
        if include_traces
        else []
    )
    result = {
        "schema": "adaos.conversation.export.v1",
        "conversation_id": cid,
        "conversation": conversation,
        "messages": messages,
        "memory": memory,
        "turn_traces": traces,
        "include_redacted": bool(include_redacted),
        "counts": {
            "messages": len(messages),
            "memory": len(memory),
            "turn_traces": len(traces),
        },
    }
    audit = append_audit_event(
        event_type="conversation.privacy",
        action="export_conversation",
        conversation_id=cid,
        status="completed",
        counts=result["counts"],
        meta={
            "include_redacted": bool(include_redacted),
            "include_memory": bool(include_memory),
            "include_traces": bool(include_traces),
            "limit": max(1, min(int(limit or 5000), 5000)),
        },
    )
    if audit:
        result["audit_event_id"] = audit["audit_event_id"]
    return result


def redact_conversation(
    conversation_id: str,
    *,
    reason: str = "user_request",
    hard_delete: bool = False,
    include_memory: bool = True,
    include_traces: bool = True,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")
    if not ensure_schema():
        return {"ok": False, "conversation_id": cid, "error": "conversation_store_unavailable"}
    now = time.time()
    clean_reason = str(reason or "user_request").strip() or "user_request"
    counts: dict[str, int] = {"conversation": 0, "messages": 0, "memory": 0, "turn_traces": 0}
    audit_event_id: str | None = None
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        if hard_delete:
            counts["turn_traces"] = int(
                con.execute("DELETE FROM conversation_turn_traces WHERE conversation_id=?", (cid,)).rowcount or 0
            )
            counts["messages"] = int(
                con.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (cid,)).rowcount or 0
            )
            if include_memory:
                counts["memory"] = int(
                    con.execute(
                        "DELETE FROM conversation_memory_items WHERE scope='conversation' AND subject_id=?",
                        (cid,),
                    ).rowcount
                    or 0
                )
            counts["conversation"] = int(
                con.execute("DELETE FROM conversation_conversations WHERE conversation_id=?", (cid,)).rowcount or 0
            )
        else:
            counts["conversation"] = int(
                con.execute(
                    """
                    UPDATE conversation_conversations
                    SET redaction_state='redacted',
                        redacted_at=?,
                        redaction_reason=?,
                        updated_at=?
                    WHERE conversation_id=?
                    """,
                    (now, clean_reason, now, cid),
                ).rowcount
                or 0
            )
            counts["messages"] = int(
                con.execute(
                    """
                    UPDATE conversation_messages
                    SET redaction_state='redacted',
                        redacted_at=?,
                        redaction_reason=?
                    WHERE conversation_id=?
                    """,
                    (now, clean_reason, cid),
                ).rowcount
                or 0
            )
            if include_memory:
                counts["memory"] = int(
                    con.execute(
                        """
                        UPDATE conversation_memory_items
                        SET redaction_state='redacted',
                            redacted_at=?,
                            redaction_reason=?,
                            updated_at=?
                        WHERE scope='conversation' AND subject_id=?
                        """,
                        (now, clean_reason, now, cid),
                    ).rowcount
                    or 0
                )
            if include_traces:
                counts["turn_traces"] = int(
                    con.execute(
                        """
                        UPDATE conversation_turn_traces
                        SET redaction_state='redacted',
                            redacted_at=?,
                            redaction_reason=?
                        WHERE conversation_id=?
                        """,
                        (now, clean_reason, cid),
                    ).rowcount
                    or 0
                )
        audit = _append_audit_event_with_connection(
            con,
            event_type="conversation.privacy",
            action="hard_delete_conversation" if hard_delete else "redact_conversation",
            conversation_id=cid,
            status="completed",
            reason=clean_reason,
            counts=counts,
            meta={"include_memory": bool(include_memory), "include_traces": bool(include_traces)},
        )
        audit_event_id = str(audit.get("audit_event_id") or "") or None
        con.commit()
    result = {
        "ok": True,
        "conversation_id": cid,
        "hard_delete": bool(hard_delete),
        "redaction_reason": clean_reason,
        "counts": counts,
    }
    if audit_event_id:
        result["audit_event_id"] = audit_event_id
    return result
