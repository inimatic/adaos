"""Bounded outbox dispatcher, independent of HTTP, Router and workflow work."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from typing import Any
from datetime import datetime, timezone

from adaos.services import conversation_publication as publication, durable_delivery
from adaos.services.agent_context import get_ctx, set_ctx

_LOG = logging.getLogger("adaos.conversation.delivery")
_UNSUPPORTED_ROOTS: dict[str, float] = {}


def claim_next(*, now: float | None = None, transport: str | None = None) -> dict[str, Any] | None:
    """Lease one due target. An expired send lease is uncertain, never blind replay."""
    now = time.time() if now is None else now
    lane = "" if transport is None else (
        " AND r.transport NOT IN ('telegram','max','web')" if transport == "__unsupported__" else " AND r.transport=?")
    lane_args = () if transport in (None, "__unsupported__") else (transport,)
    with get_ctx().sql.connect() as con:
        con.row_factory = sqlite3.Row
        # Idle polling must not compete with interactive writes. This read is
        # only a hint: repeat the selection under the write lease below so
        # concurrent dispatchers cannot claim the same intent.
        due = con.execute(f"""SELECT 1 WHERE EXISTS (
            SELECT 1 FROM conversation_output_intents i JOIN conversation_reply_routes r ON r.route_id=i.route_id
            WHERE i.status IN ('pending','retry_wait') AND i.next_attempt_at<=? {lane}
        ) OR EXISTS (
            SELECT 1 FROM conversation_output_intents i JOIN conversation_reply_routes r ON r.route_id=i.route_id
            WHERE i.status IN ('leased','accepted_by_root') AND i.lease_until<? {lane}
        )""", (now, *lane_args, now, *lane_args)).fetchone()
        if not due:
            return None
        con.execute("BEGIN IMMEDIATE")
        con.execute("""UPDATE conversation_output_intents SET status=CASE
            WHEN status='leased' AND transport_attempt_id IS NULL THEN 'retry_wait'
            ELSE 'reconciling' END,
            last_error='worker_lease_expired',updated_at=?
            WHERE status IN ('leased','accepted_by_root') AND lease_until<?""", (now, now))
        row = con.execute(f"""SELECT i.* FROM conversation_output_intents i
            JOIN conversation_reply_routes r ON r.route_id=i.route_id
            WHERE i.status IN ('pending','retry_wait') AND i.next_attempt_at<=?
            {lane} ORDER BY i.next_attempt_at,i.intent_id LIMIT 1""", (now, *lane_args)).fetchone()
        if not row:
            con.commit()
            return None
        token = uuid.uuid4().hex
        con.execute("""UPDATE conversation_output_intents SET status='leased',lease_token=?,
            lease_until=?,attempt_count=attempt_count+1,updated_at=? WHERE intent_id=?""",
            (token, now + 120, now, row["intent_id"]))
        con.commit()
        return {**dict(row), "status": "leased", "lease_token": token,
                "attempt_count": row["attempt_count"] + 1}


def _finish(item: dict[str, Any], status: str, error: str | None = None) -> None:
    now = time.time()
    with get_ctx().sql.connect() as con:
        con.execute("""UPDATE conversation_output_intents SET status=?,last_error=?,updated_at=?,
            next_attempt_at=? WHERE intent_id=? AND lease_token=? AND status='leased'""",
            (status, error, now, now + min(300, 2 ** min(item["attempt_count"], 8)),
             item["intent_id"], item["lease_token"]))
        con.commit()


def apply_receipt(payload: dict[str, Any]) -> bool:
    """Apply exact transport proof; a Root HTTP acknowledgement is not delivery."""
    from adaos.services import telegram_delivery

    value = telegram_delivery.validate_receipt(payload)
    attempt = telegram_delivery.get_attempt(value["delivery_attempt_id"])
    if not attempt or attempt["operation_key"] != value["operation_key"]:
        raise ValueError("delivery receipt identity mismatch")
    if not attempt.get("durable_attempt_id"):
        if value["delivered"] and value["external_message_ids"]:
            telegram_delivery.complete_outbound(attempt["attempt_id"], delivered=True, receipt=value)
        return True  # Unproven legacy sends remain queryable, never guessed from content.
    error = str(value.get("error") or "")
    permanent_rejection = bool(re.search(r"telegram_http_non_ok status=(?:400|401|403|404|405|406|410|413|415|422)\b", error))
    if error.startswith("telegram_rate_limited"):
        # Root retains this exact queued operation and owns the safe retry.
        status = "reconciling"
    elif (value["delivered"] and not value["external_message_ids"]) or (not value["delivered"] and not permanent_rejection):
        # Stop automatic polling/replay, but still admit a later exact positive receipt.
        status = "needs_review"
    else:
        completed = telegram_delivery.complete_outbound(attempt["attempt_id"],
            delivered=value["delivered"], receipt=value, error=value.get("error"))
        durable_delivery.complete_delivery(attempt["durable_attempt_id"],
            delivered=completed["status"] == "delivered", receipt=value, error=value.get("error"))
        status = "delivered" if value["delivered"] else "dead_letter"
    with get_ctx().sql.connect() as con:
        con.execute("""UPDATE conversation_output_intents SET status=?,last_error=?,updated_at=?
            WHERE attempt_id=? AND status!='delivered'""",
            (status, value.get("error"), time.time(), attempt["durable_attempt_id"]))
        con.commit()
    return True


class TelegramAdapter:
    def send(self, envelope: dict, route: dict, attempt: dict) -> dict:
        import requests
        from adaos.services import telegram_delivery
        from adaos.services.router.telegram_projection import _telegram_text_chunks

        ctx = get_ctx()
        destination = route["destination_ref"]
        if route["transport"] != "telegram":
            raise ValueError("unsupported output transport")
        chunks = _telegram_text_chunks(envelope["payload"]["text"] or "", limit=3000)
        has_bot, has_chat = bool(destination.get("bot_id")), bool(destination.get("chat_id"))
        if has_bot != has_chat:
            raise ValueError("output destination requires both frozen bot and chat identities")
        # A discovered pairing route retains its observed address. Its send is
        # explicit, so a changed pairing cannot redirect already admitted text.
        # Hub-only legacy routes keep the address-free pairing contract.
        mode = None if has_bot else "hub_pairing"
        guard = envelope["payload"]["data"].get("relay_guard")
        guarded_client = None
        if guard:
            from adaos.services.max_delivery import root_client
            from adaos.services.notifications.relay import require_root_guard
            guarded_client = root_client()
            require_root_guard(guarded_client)
        transport = telegram_delivery.claim_outbound(attempt["idempotency_key"],
            hub_id=str(destination["hub_id"]), routing_mode=mode,
            bot_id=destination.get("bot_id"), chat_id=destination.get("chat_id"),
            message_count=len(chunks), content_digest="sha256:" + publication._digest(chunks),
            response_envelope_id=envelope["envelope_id"], durable_attempt_id=attempt["attempt_id"])
        with ctx.sql.connect() as con:
            changed = con.execute("""UPDATE conversation_output_intents SET transport_attempt_id=?
                WHERE attempt_id=? AND status='leased' AND lease_token=? AND lease_until>?""",
                (transport["attempt_id"], attempt["attempt_id"], attempt.get("_output_lease_token"), time.time()))
            con.commit()
            if changed.rowcount != 1:
                raise RuntimeError("output dispatch lease is no longer owned")
        if transport["status"] == "delivered":
            return {"delivered": True, "receipt": transport["receipt"]}
        root = getattr(getattr(ctx.config, "root_settings", None), "base_url", None) or ctx.settings.api_base
        protocol = {"operation_key": transport["operation_key"],
            "delivery_attempt_id": transport["attempt_id"],
            "receipt_subject": "tg.receipt." + str(destination["hub_id"]),
            "response_envelope_id": envelope["envelope_id"],
            "durable_attempt_id": attempt["attempt_id"]}
        if guard:
            protocol["notification_guard"] = guard
            result = guarded_client.request("POST", "/v1/hub/communication/notifications/telegram/send", json={
                "hub_id": destination["hub_id"], "bot_id": destination["bot_id"], "chat_id": destination["chat_id"],
                "messages": [{"type": "text", "text": chunk} for chunk in chunks], "_protocol": protocol}, timeout=5)
            return {"accepted": result.get("accepted") is True and result.get("guard_version") == 1}
        response = requests.post(root.rstrip("/") + "/io/tg/send", json={
            "hub_id": destination["hub_id"], "messages": [{"type": "text", "text": chunk} for chunk in chunks],
            **({"chat_id": destination["chat_id"]} if destination.get("chat_id") else {}),
            **({"bot_id": destination["bot_id"]} if destination.get("bot_id") else {}),
            "_protocol": protocol}, timeout=15)
        if response.status_code >= 500:
            return {"uncertain": True, "error": "root_server_error"}
        if response.status_code >= 400:
            return {"retry_safe": response.status_code == 429, "error": f"root_http_{response.status_code}"}
        return {"accepted": True}


def dispatch_once(adapter: Any = None, *, now: float | None = None, transport: str | None = None) -> bool:
    item = claim_next(now=now, transport=transport)
    if item is None:
        return False
    route = durable_delivery.get_reply_route(item["route_id"])
    envelope = durable_delivery.get_envelope(item["envelope_id"])
    if route and route["status"] == "active" and route["channel_context"].get("binding_ref", "").startswith("telegram-pairing:"):
        from adaos.services.conversation_routes import refresh_paired_route
        observed = refresh_paired_route(route["conversation_id"], route["principal_scope"][0])
        route = durable_delivery.get_reply_route(item["route_id"])
        if observed is None and route and route["status"] == "active":
            _finish(item, "retry_wait", "route_binding_unverified")
            return True
    if (not route or route["status"] != "active"
            or durable_delivery._expired(route.get("expires_at"), now=durable_delivery._now())):
        _finish(item, "suppressed", "route_revoked_or_expired")
        return True
    if not envelope or envelope["status"] in {"superseded", "cancelled"}:
        _finish(item, "suppressed", "envelope_not_dispatchable")
        return True
    route_digest = publication._digest({key: route.get(key) for key in
        ("route_id", "conversation_id", "transport", "destination_ref", "principal_scope")})
    if envelope["payload"]["data"].get("route_digest") != route_digest:
        _finish(item, "suppressed", "route_binding_changed")
        return True
    notification = envelope["payload"]["data"].get("notification_ref")
    if notification:
        from adaos.services.notifications.delivery import gate
        if not gate(item, envelope, route, now=now):
            return True
    else:
        source = envelope["payload"]["data"].get("message_id")
        with get_ctx().sql.connect() as con:
            row = con.execute("SELECT m.redaction_state,c.policy_json FROM conversation_messages m "
                "JOIN conversation_conversations c ON c.conversation_id=m.conversation_id WHERE m.message_id=?", (source,)).fetchone()
        if not row or row[0] != "active":
            _finish(item, "suppressed", "source_removed_or_redacted")
            return True
        policy = json.loads(row[1])
        if item["route_id"] not in (policy.get("output_route_ids") or []):
            _finish(item, "suppressed", "route_deselected")
            return True
        from adaos.services.notifications.delivery import gate_conversation_attention
        if not gate_conversation_attention(item, envelope, policy, now=now):
            return True
    if adapter is None and (route["transport"] not in {"telegram", "max", "web"}
                            or (route["transport"] == "web" and not notification)):
        _finish(item, "dead_letter", "unsupported_output_transport")
        return True
    try:
        attempt = durable_delivery.claim_delivery(item["envelope_id"], item["route_id"], presentation_id=item["intent_id"])
        with get_ctx().sql.connect() as con:
            con.execute("UPDATE conversation_output_intents SET attempt_id=? WHERE intent_id=? AND lease_token=?",
                        (attempt["attempt_id"], item["intent_id"], item["lease_token"]))
            con.commit()
        from adaos.services.max_delivery import MaxAdapter
        from adaos.services.notifications.device_delivery import DeviceAdapter
        default = DeviceAdapter() if route["transport"] == "web" and notification else (
            MaxAdapter() if route["transport"] == "max" else TelegramAdapter())
        result = (adapter or default).send(envelope, route,
            {**attempt, "_output_lease_token": item["lease_token"]})
        if result.get("delivered"):
            durable_delivery.complete_delivery(attempt["attempt_id"], delivered=True, receipt=result["receipt"])
            _finish(item, "delivered")
        elif result.get("accepted"):
            if result.get("accepted_state", "accepted_by_root") == "accepted_by_root":
                durable_delivery.record_root_acceptance(attempt["attempt_id"])
            _finish(item, result.get("accepted_state", "accepted_by_root"))
        elif result.get("retry_safe"):
            if item["attempt_count"] >= 6:
                durable_delivery.complete_delivery(attempt["attempt_id"], delivered=False, error=result.get("error"))
            _finish(item, "retry_wait" if item["attempt_count"] < 6 else "dead_letter", result.get("error"))
        else:
            _finish(item, "reconciling" if result.get("uncertain") else "dead_letter", result.get("error"))
    except Exception:
        _LOG.exception("output dispatch outcome unknown intent=%s", item["intent_id"])
        # The production adapter persists its transport identity before any
        # external call. A local failure before that boundary cannot be joined
        # by reconciliation and is safe to retry under the fenced lease.
        with get_ctx().sql.connect() as con:
            recorded = con.execute("SELECT transport_attempt_id FROM conversation_output_intents WHERE intent_id=?",
                                   (item["intent_id"],)).fetchone()
        status = "reconciling" if recorded and recorded[0] else (
            "retry_wait" if item["attempt_count"] < 6 else "dead_letter")
        _finish(item, status, "transport_outcome_unknown" if status == "reconciling" else "local_dispatch_failed")
    return True


def reconcile_once() -> bool:
    """Query one exact stored operation. An absent receipt never authorizes resend."""
    ctx = get_ctx()
    now = time.time()
    with ctx.sql.connect() as con:
        con.row_factory = sqlite3.Row
        due = con.execute("""SELECT 1 FROM conversation_output_intents i
            JOIN telegram_outbound_attempts t ON t.attempt_id=i.transport_attempt_id
            WHERE i.status='reconciling' AND i.next_attempt_at<=? LIMIT 1""", (now,)).fetchone()
        if not due:
            return False
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("""SELECT i.intent_id,t.payload_json FROM conversation_output_intents i
            JOIN telegram_outbound_attempts t
            ON t.attempt_id=i.transport_attempt_id
            WHERE i.status='reconciling' AND i.next_attempt_at<=? LIMIT 1""", (now,)).fetchone()
        if not row:
            con.commit()
            return False
        con.execute("UPDATE conversation_output_intents SET next_attempt_at=? WHERE intent_id=?",
                    (now + 300, row["intent_id"]))
        con.commit()
    attempt = json.loads(row["payload_json"])
    claimed_at = datetime.fromisoformat(attempt["claimed_at"].replace("Z", "+00:00"))
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    if now - claimed_at.timestamp() > 86400:
        with ctx.sql.connect() as con:
            con.execute("""UPDATE conversation_output_intents SET status='needs_review',
                last_error='receipt_reconciliation_deadline_exceeded',updated_at=? WHERE intent_id=?
                AND status='reconciling'""", (now, row["intent_id"]))
            con.commit()
        return True
    try:
        response = query_receipt(attempt)
        if response and response.get("receipt"):
            apply_receipt(response["receipt"])
        elif response:
            # A restored endpoint returning a truthful miss must replace a
            # stale "root_upgrade_required" diagnostic, without authorizing
            # replay or closing the still-unproven operation.
            error = ("receipt_not_found" if response.get("ok") else
                     "receipt_query:" + str(response.get("status") or "unavailable"))
            with ctx.sql.connect() as con:
                con.execute("UPDATE conversation_output_intents SET last_error=? WHERE intent_id=? AND status='reconciling'",
                    (error, row["intent_id"]))
                con.commit()
    except Exception:
        _LOG.debug("exact receipt reconciliation unavailable intent=%s", row["intent_id"], exc_info=True)
    return True


def query_receipt(attempt: dict[str, Any]) -> dict[str, Any] | None:
    """Read one exact operation under the node's existing Hub mTLS identity."""
    from adaos.services.root.client import RootHttpClient, RootHttpError
    ctx = get_ctx()
    if str(attempt.get("hub_id")) != str(getattr(ctx.config, "subnet_id", "")):
        raise PermissionError("receipt belongs to another hub")
    client = RootHttpClient.from_hub_config(ctx.config, fallback_base_url=ctx.settings.api_base)
    if client is None:
        return {"ok": False, "status": "hub_identity_unavailable"}
    base = str(getattr(client, "base_url", ctx.settings.api_base)).rstrip("/")
    if _UNSUPPORTED_ROOTS.get(base, 0) > time.monotonic():
        return {"ok": False, "status": "root_upgrade_required"}
    try:
        response = client.request("POST", "/v1/hub/communication/telegram/receipt/status",
            json={"operation_key": attempt["operation_key"]}, timeout=5)
    except RootHttpError as exc:
        if (exc.status_code == 404 and isinstance(exc.payload, dict)
                and exc.payload.get("ok") is False and "receipt" in exc.payload
                and exc.payload["receipt"] is None):
            return {"schema": "adaos.telegram.receipt_status.v1", "ok": True,
                    "found": False, "receipt": None, "status": "not_found"}
        if exc.status_code in (404, 501):
            if len(_UNSUPPORTED_ROOTS) >= 16:
                _UNSUPPORTED_ROOTS.pop(min(_UNSUPPORTED_ROOTS, key=_UNSUPPORTED_ROOTS.get))
            _UNSUPPORTED_ROOTS[base] = time.monotonic() + 300
            return {"ok": False, "status": "root_upgrade_required"}
        return {"ok": False, "status": "unavailable"}
    if not isinstance(response, dict) or response.get("schema") != "adaos.telegram.receipt_status.v1" or response.get("ok") is not True:
        return {"ok": False, "status": "invalid_receipt_status"}
    if response.get("found") is False and response.get("receipt") is None and response.get("status") == "not_found":
        _UNSUPPORTED_ROOTS.pop(base, None)
        return response
    if response.get("found") is True and response.get("status") == "recorded" and isinstance(response.get("receipt"), dict):
        from adaos.services import telegram_delivery
        try:
            proof = telegram_delivery.validate_receipt(response["receipt"])
        except ValueError:
            return {"ok": False, "status": "invalid_receipt_status"}
        if (proof["operation_key"] == attempt["operation_key"]
                and proof["delivery_attempt_id"] == attempt.get("attempt_id")
                and proof["transport"] == "telegram"):
            _UNSUPPORTED_ROOTS.pop(base, None)
            return {**response, "receipt": proof}
    return {"ok": False, "status": "invalid_receipt_status"}


def runtime_snapshot() -> dict[str, Any]:
    """Delivery pressure is a separate health domain from API and first sync."""
    try:
        with get_ctx().sql.connect() as con:
            rows = con.execute("SELECT status,COUNT(*) FROM conversation_output_intents GROUP BY status").fetchall()
            oldest = con.execute("SELECT MIN(updated_at) FROM conversation_output_intents "
                "WHERE status IN ('pending','retry_wait','queued_for_relay','reconciling','accepted_by_root')").fetchone()[0]
        counts = dict(rows)
        return {"available": True, "owner": "conversation_delivery", "states": counts,
            "oldest_pending_age_s": round(time.time() - oldest, 3) if oldest else 0,
            "state": "degraded" if any(counts.get(state) for state in ("reconciling", "dead_letter", "needs_review")) else "ready",
            "blocks_api_or_yjs_readiness": False}
    except sqlite3.OperationalError:
        return {"available": False, "reason": "output_store_not_initialized", "blocks_api_or_yjs_readiness": False}


class DeliveryWorker:
    """Fixed provider lanes on the existing outbox; slow I/O cannot block a peer."""
    def __init__(self, bus: Any):
        self.ctx = get_ctx()
        self.bus = bus
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.lanes: list[threading.Thread] = []

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        from adaos.services.max_delivery import MaxWorker
        self.max_worker = MaxWorker(self.bus)
        self.max_worker.start()
        publication.ensure_schema()
        def provider_lane(transport):
            set_ctx(self.ctx)
            while not self.stop_event.is_set():
                try:
                    worked = dispatch_once(transport=transport)
                except Exception:
                    _LOG.exception("delivery lane failed transport=%s", transport)
                    worked = False
                if not worked:
                    self.stop_event.wait(2)
        for transport in ("telegram", "max", "web"):
            lane = threading.Thread(target=provider_lane, args=(transport,),
                                    name="conversation-delivery-" + transport, daemon=True)
            self.lanes.append(lane)
            lane.start()
        def run():
            set_ctx(self.ctx)
            publication.ensure_schema()
            while not self.stop_event.is_set():
                try:
                    from adaos.services.notifications.delivery import tick
                    worked = tick()
                    worked = dispatch_once(transport="__unsupported__") or worked
                    if not worked:
                        reconcile_once()
                except Exception:
                    _LOG.exception("delivery worker iteration failed")
                    worked = False
                if not worked:
                    self.wake.wait(2)
                    self.wake.clear()
        self.thread = threading.Thread(target=run, name="conversation-delivery", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if hasattr(self, 'max_worker'):
            self.max_worker.stop()
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=0.1)
        for lane in self.lanes:
            lane.join(timeout=0.1)
