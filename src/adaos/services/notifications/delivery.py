"""Notification planning over the existing Conversation output intents/receipts."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from adaos.domain.notifications import NotificationError, digest, dump
from adaos.services import durable_delivery
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.policy import evaluate, explain


def _row_record(con, notification_id):
    from adaos.services.notifications.store import NotificationStore
    return NotificationStore.record(con.execute(
        "SELECT * FROM notification_records WHERE notification_id=?", (notification_id,)).fetchone())


def _enqueue(service, con, record, endpoint, *, text=None, window_id=None, members=None):
    """One existing envelope/intent per exact content revision and endpoint."""
    route_row = con.execute("SELECT payload_json FROM conversation_reply_routes WHERE route_id=?",
                            (endpoint["route_id"],)).fetchone()
    if not route_row:
        return None
    route = json.loads(route_row[0])
    identity = [record["notification_id"], record["content_revision"], endpoint["endpoint_id"]]
    if window_id:
        identity = [window_id, members, endpoint["endpoint_id"]]
    intent_id = "notification-output:" + digest(identity)
    if con.execute("SELECT 1 FROM conversation_output_intents WHERE intent_id=?", (intent_id,)).fetchone():
        return intent_id
    data = {"notification_ref": record["notification_id"], "endpoint_id": endpoint["endpoint_id"],
            "notification_revision": record["content_revision"], "expires_at": record["expires_at"],
            "route_digest": endpoint["binding_digest"], "window_id": window_id, "members": members}
    label = f"[{record['subnet_ref'].removeprefix('subnet:')}] {record['author_label']}: "
    envelope = durable_delivery.enqueue_response(route["conversation_id"], "notification",
        text=(text if text is not None else label + record["text"]), data=data,
        reply_route_ids=[route["route_id"]], envelope_id=intent_id, connection=con)
    envelope.update(materialization_status="materialized", materialized_at=durable_delivery._now())
    durable_delivery._save_envelope(con, envelope)
    con.execute("""INSERT INTO conversation_output_intents
        (intent_id,publication_id,envelope_id,route_id,status,next_attempt_at,updated_at)
        VALUES(?,?,?,?,'pending',?,?)""",
        (intent_id, "notification:" + record["notification_id"], intent_id, route["route_id"],
         service.clock(), service.clock()))
    con.execute("INSERT INTO notification_outputs VALUES(?,?,?,?,?,?,?)",
        (intent_id, record["notification_id"], endpoint["endpoint_id"], record["content_revision"],
         record["decision"]["policy_revision"], endpoint["revision"], window_id))
    return intent_id


def enqueue_targets(service, con, record, prefs, endpoints):
    decision = record["decision"]
    for endpoint in endpoints:
        if endpoint["endpoint_id"] not in decision["endpoint_ids"]:
            continue
        if decision["mode"] == "digest":
            interval = prefs["budgets"]["digest_seconds"]
            due = (int(service.clock()) // interval + 1) * interval
            prior = con.execute("""SELECT w.window_id,w.status,m.content_revision FROM notification_digest_windows w
                JOIN notification_digest_members m ON m.window_id=w.window_id
                WHERE w.endpoint_id=? AND m.notification_id=? AND
                (w.status IN ('pending','paused') OR m.content_revision=?)
                ORDER BY w.due_at DESC LIMIT 1""",
                (endpoint["endpoint_id"], record["notification_id"], record["content_revision"])).fetchone()
            if prior and prior["status"] == "enqueued" and prior["content_revision"] == record["content_revision"]:
                continue
            window_id = prior["window_id"] if prior and prior["status"] in {"pending", "paused"} else "notification-digest:" + digest([
                record["principal_ref"], record["subnet_ref"], record["namespace"],
                endpoint["endpoint_id"], due])
            con.execute("INSERT OR IGNORE INTO notification_digest_windows VALUES(?,?,?,?,?,'pending',NULL)",
                (window_id, record["principal_ref"], endpoint["endpoint_id"], record["namespace"], due))
            count = con.execute("SELECT COUNT(*) FROM notification_digest_members WHERE window_id=?", (window_id,)).fetchone()[0]
            if count < 100:
                con.execute("""INSERT INTO notification_digest_members VALUES(?,?,?)
                    ON CONFLICT(window_id,notification_id) DO UPDATE SET content_revision=excluded.content_revision""",
                    (window_id, record["notification_id"], record["content_revision"]))
            else:
                record["decision"].update(reason="digest_capacity", explanation={
                    "en": "More updates remain available in Notification Center",
                    "ru": "Остальные обновления доступны в центре уведомлений"})
            # The canonical private feed retains all notices even when this bounded digest fills.
            continue
        delay = decision.get("escalate_after_seconds")
        if delay and not record.get("escalation_due"):
            record["escalation_due"] = service.clock() + delay
            record["suppress_if_read"] = True
        if record.get("escalation_due", 0) > service.clock():
            record["decision"]["next_eligible_at"] = record["escalation_due"]
            continue
        _enqueue(service, con, record, endpoint)


def live_application_authority(record, ctx) -> bool:
    """Recheck the captured initiating subject's release-bound Application grant."""
    from pathlib import Path
    from adaos.domain.application_access import evaluate_application_access
    from adaos.domain.personalization_access import SubjectRef
    from adaos.services.personalization_runtime import personalization_access_service
    from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
    if record.get("test_only"):
        return False
    store = ApplicationStore(Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()))
    try:
        release = store.get_release(record["application_id"], record["release_digest"])
        grants = store.list_application_access_grants(record["application_id"], subject_ref=record["principal_ref"])
        authority = record.get("producer_binding", {}).get("authority", {})
        selected = store.get_runtime_selection(str(authority.get("webspace_id") or ""), record["application_id"])
        if (selected.release_digest != record["release_digest"] or
            selected.runtime_root_ref != authority.get("runtime_root_ref", "workspace")):
            return False
        actor_kind, _, actor_id = str(authority.get("actor_ref") or "").partition(":")
        if not actor_kind or not actor_id:
            return False
        _, _, user_id = record["principal_ref"].partition(":")
        personalization_access_service(ctx).require_user_private_content_access(
            actor=SubjectRef(actor_kind, actor_id), subject=SubjectRef("user", user_id),
            action="preferences.read.self", resource="notifications", audit_success=False)
        for grant in grants:
            decision = evaluate_application_access(profile=release.permission_profile,
                roles=release.application_roles, grant=grant, permission_id="notifications.send",
                app_capability=authority.get("app_capability", "notifications.send"),
                actor_chain={**authority, "application_id": record["application_id"], "subject_ref": record["principal_ref"]},
                component_capabilities=("notifications.send",))
            if decision.decision == "allow":
                return True
    except (ValueError, KeyError, OSError, PermissionError, ApplicationStoreError):
        pass
    return False


def _set_intent(con, item, status, reason, *, due=None):
    con.execute("""UPDATE conversation_output_intents SET status=?,last_error=?,next_attempt_at=?,
        attempt_count=MAX(0,attempt_count-1),lease_token=NULL,lease_until=NULL,updated_at=?
        WHERE intent_id=? AND lease_token=? AND status='leased'""",
        (status, reason, due or time.time(), time.time(), item["intent_id"], item["lease_token"]))


def gate(item, envelope, route, *, now=None) -> bool:
    """Final local dispatch gate; false means no irreversible adapter call is allowed."""
    from adaos.services.notifications.runtime import NotificationService
    service = NotificationService()
    current = time.time() if now is None else now
    endpoint_id = envelope["payload"]["data"]["endpoint_id"]
    with service.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        record = _row_record(con, envelope["payload"]["data"]["notification_ref"])
        link = con.execute("SELECT * FROM notification_outputs WHERE intent_id=?", (item["intent_id"],)).fetchone()
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
        reason = None
        if not record or not link or not endpoint:
            reason = "notification_source_or_endpoint_missing"
        elif record["content_revision"] != link["content_revision"]:
            reason = "notification_revision_superseded"
        elif endpoint["revision"] != link["endpoint_revision"] or endpoint["binding_digest"] != envelope["payload"]["data"]["route_digest"]:
            reason = "endpoint_binding_changed"
        elif not live_application_authority(record, service.ctx):
            reason = "permission_denied"
        members = envelope["payload"]["data"].get("members") or []
        for member in (members if reason is None else []):
            source = _row_record(con, member["notification_id"])
            if (not source or source["content_revision"] != member["content_revision"]
                or source["source_state"] != "active" or source["expires_at"] <= current
                or not live_application_authority(source, service.ctx)):
                reason = "digest_source_changed"
                break
            member_prefs = service.preferences.read(source["principal_ref"], source["subnet_ref"], connection=con)
            member_plan = evaluate(source, member_prefs, [dict(endpoint)], now=current)
            if not member_plan["alert"] or endpoint_id not in member_plan["endpoint_ids"]:
                reason = "digest_source_changed"
                break
        if reason:
            _set_intent(con, item, "suppressed", reason)
            if reason == "digest_source_changed" and link and link["digest_window_id"]:
                state = "needs_review" if item.get("transport_attempt_id") else "pending"
                con.execute("UPDATE notification_digest_windows SET status=?,due_at=? WHERE window_id=?",
                            (state, current, link["digest_window_id"]))
            con.commit()
            return False
        prefs = service.preferences.read(record["principal_ref"], record["subnet_ref"], connection=con)
        endpoints = service._endpoints(con, record["principal_ref"], record["subnet_ref"])
        decision = evaluate(record, prefs, endpoints, now=current)
        if not decision["alert"] or endpoint_id not in decision["endpoint_ids"]:
            due = decision["next_eligible_at"]
            _set_intent(con, item, "retry_wait" if due else "suppressed", decision["reason"], due=due)
            if due is None:
                record["cancelled_endpoint_ids"] = sorted(set(record.get("cancelled_endpoint_ids", [])) | {endpoint_id})
                service._save(con, record)
            con.commit()
            return False
        caps = json.loads(endpoint["capabilities_json"])
        if route["transport"] == "web" and caps.get("permission") != "granted":
            _set_intent(con, item, "suppressed", "device_permission_not_granted")
            con.commit()
            return False
        sensitivity = ("public", "private", "sensitive")
        if sensitivity.index(record["sensitivity"]) > sensitivity.index(caps.get("maximum_sensitivity", "public")):
            _set_intent(con, item, "suppressed", "endpoint_sensitivity_denied")
            con.commit()
            return False
        existing = con.execute("SELECT 1 FROM notification_dispatch_reservations WHERE intent_id=?", (item["intent_id"],)).fetchone()
        if not existing:
            used = con.execute("""SELECT COUNT(*),MIN(reserved_at) FROM notification_dispatch_reservations
                WHERE principal_ref=? AND reserved_at>?""", (record["principal_ref"], current - 3600)).fetchone()
            producer = con.execute("""SELECT COUNT(*),MIN(reserved_at) FROM notification_dispatch_reservations
                WHERE principal_ref=? AND application_id=? AND reserved_at>?""",
                (record["principal_ref"], record["application_id"], current - 60)).fetchone()
            if used[0] >= prefs["budgets"]["per_hour"] or producer[0] >= prefs["budgets"]["producer_per_minute"]:
                due = max((used[1] or current) + 3600 if used[0] >= prefs["budgets"]["per_hour"] else current,
                          (producer[1] or current) + 60 if producer[0] >= prefs["budgets"]["producer_per_minute"] else current)
                _set_intent(con, item, "retry_wait", "budget", due=due)
                con.commit()
                return False
            con.execute("INSERT INTO notification_dispatch_reservations VALUES(?,?,?,?,?)",
                (item["intent_id"], record["principal_ref"], record["application_id"], endpoint_id, current))
        con.commit()
        return True


def gate_conversation_attention(item, envelope, conversation_policy, *, now=None) -> bool:
    """Keep semantic classification frozen; re-evaluate the current dispatch schedule."""
    from adaos.services.attention_schedule import quiet_period
    from adaos.services.conversation_attention import default_attention_policy, plan_attention
    selected = conversation_policy.get("attention_policy") or default_attention_policy()
    timestamp = durable_delivery._now() if now is None else datetime.fromtimestamp(now, timezone.utc).isoformat()
    plan = plan_attention(envelope["category"], policy=selected,
        requested_attention=envelope["attention"], now=timestamp)
    if plan["notify"]:
        return True
    due = None
    if plan["quiet_hours_applied"]:
        _, until = quiet_period(selected["quiet_hours"], now=timestamp)
        due = until.timestamp() if until else None
    with get_ctx().sql.connect() as con:
        _set_intent(con, item, "retry_wait" if due else "suppressed",
                    "quiet_hours" if plan["quiet_hours_applied"] else "attention_policy", due=due)
        con.commit()
    return False


def tick() -> bool:
    """Bounded work on the existing delivery worker; idle polling does no DDL or scans."""
    ctx = get_ctx()
    with ctx.sql.connect() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='notification_schema_version'").fetchone():
            return False
    from adaos.services.notifications.runtime import NotificationService
    service = NotificationService()
    work = service.recheck_preferences()
    work += service.expire_due()
    work += service.schedule_due()
    work += flush_digests(service)
    return bool(work)


def flush_digests(service, limit=10) -> int:
    if not 1 <= limit <= 10:
        raise NotificationError("digest worker limit must be 1..10")
    with service.store.connect() as con:
        windows = con.execute("""SELECT * FROM notification_digest_windows WHERE status='pending' AND due_at<=?
            ORDER BY due_at LIMIT ?""", (service.clock(), limit)).fetchall()
    for window in windows:
        with service.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (window["endpoint_id"],)).fetchone()
            members = con.execute("SELECT * FROM notification_digest_members WHERE window_id=? ORDER BY notification_id LIMIT 100",
                                  (window["window_id"],)).fetchall()
            eligible, defer_until = [], None
            for member in members:
                record = _row_record(con, member["notification_id"])
                if not record or not live_application_authority(record, service.ctx):
                    continue
                prefs = service.preferences.read(record["principal_ref"], record["subnet_ref"], connection=con)
                decision = evaluate(record, prefs, [dict(endpoint)] if endpoint else [], now=service.clock())
                if decision["next_eligible_at"]:
                    defer_until = max(defer_until or 0, decision["next_eligible_at"])
                if decision["alert"] and endpoint and endpoint["endpoint_id"] in decision["endpoint_ids"]:
                    eligible.append(record)
            if defer_until:
                con.execute("UPDATE notification_digest_windows SET due_at=? WHERE window_id=?", (defer_until, window["window_id"]))
            elif not eligible:
                con.execute("UPDATE notification_digest_windows SET status='empty' WHERE window_id=?", (window["window_id"],))
            else:
                lines, size = [], 0
                for record in eligible:
                    line = f"{record['author_label']}: {record['text']}"[:400]
                    if size + len(line) > 6000:
                        break
                    lines.append(line)
                    size += len(line) + 1
                title = f"Updates ({len(eligible)}) / Сводка ({len(eligible)})"
                text = f"[{eligible[0]['subnet_ref'].removeprefix('subnet:')}] {title}\n" + "\n".join(lines)
                if len(lines) < len(eligible):
                    text += "\nMore in Notification Center / Остальное в центре уведомлений"
                frozen = [{"notification_id": r["notification_id"], "content_revision": r["content_revision"]} for r in eligible]
                summary = {**eligible[0], "expires_at": min(r["expires_at"] for r in eligible)}
                _enqueue(service, con, summary, dict(endpoint), text=text, window_id=window["window_id"], members=frozen)
                con.execute("UPDATE notification_digest_windows SET status='enqueued',content_json=? WHERE window_id=?",
                            (dump(frozen), window["window_id"]))
            con.commit()
    return len(windows)
