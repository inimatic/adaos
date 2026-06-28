from __future__ import annotations

from typing import Any, Mapping
import json
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
        created_at REAL NOT NULL,
        completed_at REAL
    );
    """,
)


_RETENTION_REDACTION_COLUMNS = (
    ("retention_class", "TEXT NOT NULL DEFAULT 'normal'"),
    ("retention_until", "REAL"),
    ("redaction_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("redacted_at", "REAL"),
    ("redaction_reason", "TEXT"),
)
_SCHEMA_COLUMN_MIGRATIONS = {
    "conversation_conversations": _RETENTION_REDACTION_COLUMNS,
    "conversation_messages": _RETENTION_REDACTION_COLUMNS,
    "conversation_memory_items": _RETENTION_REDACTION_COLUMNS,
}
_ENSURED_SQL_IDS: set[int] = set()


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
        return True
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
        con.commit()
    _ENSURED_SQL_IDS.add(token)
    return True


def _normalize_id(value: Any, fallback_prefix: str) -> str:
    token = str(value or "").strip()
    if token:
        return token
    return f"{fallback_prefix}.{uuid.uuid4().hex}"


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
                    message_id, conversation_id, seq, webspace_id, channel_id, owner,
                    actor_id, actor_label, actor_icon, role, text, route_id, ts,
                    request_id, turn_trace_id, idempotency_key, retention_class,
                    retention_until, redaction_state, redacted_at, redaction_reason,
                    payload_json, meta_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    message_id,
                    conversation_id,
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


def list_projection(
    conversation_id: str,
    *,
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
    with _sql().connect() as con:  # type: ignore[union-attr]
        con.row_factory = sqlite3.Row
        total = int(
            con.execute(
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()[0]
            or 0
        )
        if cursor and cursor >= 1:
            older = con.execute(
                """
                SELECT seq FROM conversation_messages
                WHERE conversation_id=? AND seq <= ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (conversation_id, cursor, safe_limit),
            ).fetchall()
            start_seq = min((int(row["seq"]) for row in older), default=cursor)
            rows = con.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE conversation_id=? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (conversation_id, start_seq, safe_max),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT *
                FROM conversation_messages
                WHERE conversation_id=?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (conversation_id, safe_limit),
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
        con.commit()
    return mid


def list_memory(
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
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
    result = []
    for row in rows:
        policy = _json_load(row["policy_json"], {})
        if not isinstance(policy, dict):
            policy = {}
        result.append(
            {
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
        )
    return result


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
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
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
