"""Bounded private-content GC; delivery evidence and opaque replay fences survive.

This is live Core retention, not physical backup erasure or restore admission.
An Application cannot change this horizon or use it to resolve a PA obligation.
"""
from __future__ import annotations

from adaos.domain.notifications import NotificationError, digest

CONTENT_RETENTION_SECONDS = 30 * 86400


def retire_due(service, *, limit=25):
    """Remove expired cards and durably schedule bounded snapshot scrubbing."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError("retention limit must be 1..100")
    cutoff = service.clock() - CONTENT_RETENTION_SECONDS
    with service.store.connect() as con:
        if not con.execute("SELECT 1 FROM notification_records WHERE expires_at<=? LIMIT 1", (cutoff,)).fetchone():
            return 0
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("""SELECT notification_id,source_revision,content_revision,source_state,instance_id,namespace,source_id
            FROM notification_records WHERE expires_at<=? ORDER BY expires_at,notification_id LIMIT ?""",
            (cutoff, limit)).fetchall()
        for row in rows:
            nid, source_revision, revision, state = tuple(row)[:4]
            con.execute("""INSERT INTO notification_record_tombstones VALUES(?,?,?,?,?,?)
                ON CONFLICT(notification_id) DO UPDATE SET source_revision=excluded.source_revision,
                content_revision=excluded.content_revision,source_state=excluded.source_state,retired_at=excluded.retired_at""",
                (nid, source_revision, revision, state, service.clock(), digest([row["instance_id"], row["namespace"], row["source_id"]])))
            con.execute("""INSERT INTO notification_retention_work VALUES(?,?,'outputs','',0,?)
                ON CONFLICT(notification_id) DO UPDATE SET up_to_revision=MAX(up_to_revision,excluded.up_to_revision),
                phase='outputs',after_id='',after_revision=0""", (nid, revision, service.clock()))
            con.execute("DELETE FROM notification_schedule WHERE notification_id=?", (nid,))
            con.execute("DELETE FROM notification_records WHERE notification_id=?", (nid,))
        con.commit()
    return len(rows)


def _scrub_output(con, intent_id):
    # Already submitted attempts retain their factual outcome and exact receipt.
    con.execute("""UPDATE conversation_output_intents SET status='suppressed',last_error='content_retired'
        WHERE intent_id=? AND status IN ('pending','retry_wait','leased','awaiting_endpoint')""", (intent_id,))
    con.execute("""UPDATE conversation_response_outbox
        SET payload_json=json_set(payload_json,'$.payload.text','','$.payload.data',json('{}'))
        WHERE envelope_id=(SELECT envelope_id FROM conversation_output_intents WHERE intent_id=?)""", (intent_id,))
    con.execute("""UPDATE max_outbound_attempts SET request_json=json_set(request_json,'$.messages',
        (SELECT json_group_array(json_object('type','text','text','')) FROM json_each(request_json,'$.messages'))),
        status=CASE WHEN status IN ('held','pending') THEN 'failed' ELSE status END,last_error='content_retired'
        WHERE durable_attempt_id=(SELECT attempt_id FROM conversation_output_intents WHERE intent_id=?)""", (intent_id,))
    con.execute("UPDATE notification_outputs SET relay_grant_digest=NULL WHERE intent_id=?", (intent_id,))
    con.execute("""UPDATE notification_digest_windows SET content_json=NULL
        WHERE window_id=(SELECT digest_window_id FROM notification_outputs WHERE intent_id=?)""", (intent_id,))


def retire_authority_metadata(service, *, limit=25):
    """Expired captured grants/rosters keep only replay hashes, never source access."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError("authority retention limit must be 1..100")
    cutoff = service.clock() - CONTENT_RETENTION_SECONDS
    with service.store.connect() as con:
        grants = con.execute("""SELECT handle_digest FROM notification_publisher_grants
            WHERE status='active' AND expires_at<=? ORDER BY expires_at LIMIT ?""", (cutoff, limit)).fetchall()
        rosters = con.execute("""SELECT snapshot_id FROM notification_audience_snapshots
            WHERE status='active' AND expires_at<=? ORDER BY expires_at LIMIT ?""", (cutoff, limit)).fetchall()
        if not grants and not rosters:
            return 0
        con.execute("BEGIN IMMEDIATE")
        for row in grants:
            con.execute("UPDATE notification_publisher_grants SET binding_json='{}',status='retired' WHERE handle_digest=? AND status='active' AND expires_at<=?",
                        (row[0], cutoff))
        for row in rosters:
            snapshot = con.execute("SELECT * FROM notification_audience_snapshots WHERE snapshot_id=? AND status='active' AND expires_at<=?", (row[0], cutoff)).fetchone()
            if snapshot:
                con.execute("""UPDATE notification_audience_snapshots SET status='retired',recipients_json='[]',
                    source_ref='',source_id='',principal_ref=? WHERE snapshot_id=?""",
                    ("retired:" + digest(snapshot["principal_ref"]), row[0]))
        con.commit()
    return len(grants) + len(rosters)


def scrub_pending(service, *, limit=50):
    """One resumable phase/page per tick. Never load a recipient's whole history."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError("retention page limit must be 1..100")
    with service.store.connect() as con:
        job = con.execute("""SELECT * FROM notification_retention_work
            ORDER BY created_at,notification_id LIMIT 1""").fetchone()
        if not job:
            return 0
        con.execute("BEGIN IMMEDIATE")
        # Re-read under the write claim; another existing delivery worker may
        # have advanced this page since our cheap read.
        job = con.execute("SELECT * FROM notification_retention_work WHERE notification_id=?", (job["notification_id"],)).fetchone()
        if not job:
            return 0
        nid, revision, phase, after = job["notification_id"], job["up_to_revision"], job["phase"], job["after_id"]
        if phase == "outputs":
            # Immutable membership also finds a non-leader in an old summary
            # after the live digest roster was rebuilt or removed.
            rows = con.execute("""SELECT intent_id AS id,content_revision FROM notification_output_members
                WHERE notification_id=? AND (content_revision,intent_id)>(?,?) AND content_revision<=?
                ORDER BY content_revision,intent_id LIMIT ?""",
                (nid, job["after_revision"], after, revision, limit)).fetchall()
        elif phase == "operations":
            rows = con.execute("""SELECT operation_key AS id FROM notification_user_operations
                WHERE json_extract(receipt_json,'$.notification_id')=? AND operation_key>?
                AND json_extract(receipt_json,'$.content_revision')<=? ORDER BY operation_key LIMIT ?""",
                (nid, after, revision, limit)).fetchall()
        else:
            rows = con.execute("""SELECT window_id AS id FROM notification_digest_members
                WHERE notification_id=? AND window_id>? AND content_revision<=? ORDER BY window_id LIMIT ?""",
                (nid, after, revision, limit)).fetchall()
        for row in rows:
            identifier = row["id"]
            if phase == "outputs":
                _scrub_output(con, identifier)
            elif phase == "operations":
                con.execute("UPDATE notification_user_operations SET receipt_json=? WHERE operation_key=?",
                            ('{"content_retired":true}', identifier))
            else:
                con.execute("UPDATE notification_digest_windows SET content_json=NULL WHERE window_id=?", (identifier,))
                con.execute("DELETE FROM notification_digest_members WHERE window_id=? AND notification_id=? AND content_revision<=?",
                            (identifier, nid, revision))
                con.execute("""UPDATE notification_digest_windows SET status='retired' WHERE window_id=?
                    AND NOT EXISTS(SELECT 1 FROM notification_digest_members WHERE window_id=?)""", (identifier, identifier))
        if len(rows) == limit:
            con.execute("UPDATE notification_retention_work SET after_id=?,after_revision=? WHERE notification_id=?",
                        (rows[-1]["id"], rows[-1]["content_revision"] if phase == "outputs" else 0, nid))
        elif phase == "digests":
            con.execute("DELETE FROM notification_retention_work WHERE notification_id=?", (nid,))
        else:
            following = {"outputs": "operations", "operations": "digests"}[phase]
            con.execute("UPDATE notification_retention_work SET phase=?,after_id='',after_revision=0 WHERE notification_id=?", (following, nid))
        con.commit()
    return max(1, len(rows))
