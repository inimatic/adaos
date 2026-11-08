"""Bounded source admission; delivery budgets remain the user's independent policy."""
from __future__ import annotations

from adaos.domain.notifications import NotificationBackpressure, digest

MAX_PER_MINUTE = 300
MAX_PROGRESS_PER_MINUTE = 60
MAX_LIVE_RECORDS = 10000


def reserve(con, binding, category, principal, intent_id, *, now, replacing=False):
    producer = digest([binding.instance_id, binding.namespace, binding.test_only])
    counts = con.execute("""SELECT COUNT(*),MIN(admitted_at) FROM notification_admission_reservations
        WHERE producer_key=? AND admitted_at>?""", (producer, now - 60)).fetchone()
    event_class = category.get("event_class", "completion")
    if counts[0] >= MAX_PER_MINUTE:
        raise NotificationBackpressure("producer_admission_capacity", retry_after_seconds=int(counts[1] + 61 - now))
    if event_class == "progress":
        progress = con.execute("""SELECT COUNT(*),MIN(admitted_at) FROM notification_admission_reservations
            WHERE producer_key=? AND event_class='progress' AND admitted_at>?""", (producer, now - 60)).fetchone()
        if progress[0] >= MAX_PROGRESS_PER_MINUTE:
            raise NotificationBackpressure("progress_admission_capacity", retry_after_seconds=int(progress[1] + 61 - now))
    if not replacing:
        full = con.execute("""SELECT 1 FROM notification_records WHERE principal_ref=? AND source_state='active'
            AND archived=0 AND expires_at>? LIMIT 1 OFFSET ?""", (principal, now, MAX_LIVE_RECORDS - 1)).fetchone()
        if full:
            raise NotificationBackpressure("recipient_history_capacity", retry_after_seconds=60)
    con.execute("INSERT INTO notification_admission_reservations VALUES(?,?,?,?,?)",
                (intent_id, producer, principal, event_class, now))


def retire(service, limit=100):
    with service.store.connect() as con:
        if not con.execute("SELECT 1 FROM notification_admission_reservations WHERE admitted_at<? LIMIT 1", (service.clock()-3600,)).fetchone():
            return 0
        changed = con.execute("""DELETE FROM notification_admission_reservations WHERE intent_id IN
            (SELECT intent_id FROM notification_admission_reservations WHERE admitted_at<? ORDER BY admitted_at LIMIT ?)""",
            (service.clock() - 3600, limit)).rowcount
        con.commit()
        return changed
