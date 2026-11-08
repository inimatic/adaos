"""Admit existing Core/device bindings; public endpoint views contain no addresses."""
from __future__ import annotations

import json
import time
from typing import Any

from adaos.domain.notifications import NotificationConflict, NotificationError, digest, dump, token
from adaos.services import durable_delivery
from adaos.services.agent_context import get_ctx
from adaos.services.notifications.store import NotificationStore


class EndpointService:
    def __init__(self, ctx=None, *, clock=time.time):
        self.ctx = ctx or get_ctx()
        self.store = NotificationStore(self.ctx)
        self.ctx = self.store.ctx
        self.clock = clock

    @staticmethod
    def public(row):
        return {"endpoint_id": row["endpoint_id"], "transport": row["transport"], "label": row["label"],
                "revision": row["revision"], "status": row["status"],
                "capabilities": json.loads(row["capabilities_json"]), "observed_at": row["observed_at"],
                "expires_at": row["expires_at"], "health": {"state": "unknown", "reason": "inspect_delivery_evidence"}}

    def admit_route(self, principal_ref: str, subnet_ref: str, route_id: str, *, label: str,
                    capabilities: dict[str, Any] | None = None, expires_at: float | None = None) -> dict:
        """Core-only after binding admission. This neither selects nor sends."""
        token(label, "endpoint label")
        route = durable_delivery.get_reply_route(route_id)
        if not route or route["principal_scope"] != [principal_ref] or route["status"] != "active":
            raise PermissionError("endpoint requires an exact principal-owned active ReplyRoute")
        hub = str(getattr(self.ctx.config, "subnet_id", "") or "")
        if subnet_ref.removeprefix("subnet:") != hub:
            raise PermissionError("endpoint subnet does not match this authority")
        destination = route["destination_ref"]
        if destination.get("hub_id") and str(destination["hub_id"]) != hub:
            raise PermissionError("endpoint binding belongs to another Hub")
        frozen = digest({key: route[key] for key in (
            "route_id", "conversation_id", "transport", "destination_ref", "principal_scope")})
        endpoint_id = "endpoint:" + digest([principal_ref, subnet_ref, route_id, frozen])
        caps = {"presentation": "text", "read_receipt": False, "source_audience": "personal",
                "maximum_sensitivity": "private", **dict(capabilities or {})}
        if route["transport"] in {"telegram", "max"}:
            chat_type = route["channel_context"].get("chat_type") or destination.get("chat_type")
            # Telegram private IDs are positive; unknown MAX destinations must
            # reconnect through a Root that records the provider's chat_type.
            import re
            personal = chat_type in {"private", "dialog"} or (route["transport"] == "telegram" and
                bool(re.fullmatch(r"[1-9][0-9]*", str(destination.get("chat_id") or ""))))
            caps["source_audience"] = "personal" if personal else "unverified"
            if not personal:
                caps["maximum_sensitivity"] = "public"
        if set(caps) - {"presentation", "read_receipt", "source_audience", "maximum_sensitivity", "permission"}:
            raise NotificationError("unknown endpoint capability")
        if caps["maximum_sensitivity"] not in {"public", "private", "sensitive"}:
            raise NotificationError("invalid endpoint sensitivity")
        # Provider/device capabilities are Core observations, never Application arguments.
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""INSERT OR IGNORE INTO notification_endpoints VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (endpoint_id, principal_ref, subnet_ref, route["transport"], route_id, 1, "active",
                 label, dump(caps), self.clock(), expires_at, frozen))
            con.execute("""UPDATE notification_endpoints SET observed_at=?,
                revision=revision+CASE WHEN capabilities_json!=? THEN 1 ELSE 0 END,
                capabilities_json=? WHERE endpoint_id=?""",
                (self.clock(), dump(caps), dump(caps), endpoint_id))
            row = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
            con.commit()
        return self.public(row)

    def list(self, principal_ref: str, subnet_ref: str) -> list[dict]:
        with self.store.connect(read_only=True) as con:
            rows = con.execute("""SELECT * FROM notification_endpoints WHERE principal_ref=? AND subnet_ref=?
                ORDER BY endpoint_id LIMIT 100""", (principal_ref, subnet_ref)).fetchall()
        return [self.public(row) for row in rows]

    def set_status(self, principal_ref: str, endpoint_id: str, *, status: str, expected_revision: int) -> dict:
        if status not in {"active", "paused", "revoked"}:
            raise NotificationError("unsupported endpoint state")
        with self.store.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=? AND principal_ref=?",
                              (endpoint_id, principal_ref)).fetchone()
            if not row:
                raise PermissionError("endpoint is not owned by this user")
            if type(expected_revision) is not int or row["revision"] != expected_revision:
                raise NotificationConflict("endpoint revision conflict")
            if row["status"] == "revoked":
                raise NotificationError("revoked endpoint requires fresh binding admission")
            con.execute("UPDATE notification_endpoints SET status=?,revision=revision+1 WHERE endpoint_id=?",
                        (status, endpoint_id))
            result = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
            if status != "active":
                con.execute("""UPDATE conversation_output_intents SET status='suppressed',last_error='endpoint_paused_or_revoked'
                    WHERE intent_id IN (SELECT intent_id FROM notification_outputs WHERE endpoint_id=?)
                    AND status IN ('pending','retry_wait','leased','awaiting_endpoint')""", (endpoint_id,))
            con.commit()
        return self.public(result)

    def discover_messengers(self, principal_ref: str, subnet_ref: str) -> list[dict]:
        from adaos.services.conversation_routes import refresh_max_routes, refresh_paired_route
        from adaos.services.personalization_runtime import current_user_id
        # Existing Hub-wide pairing belongs to its verified local owner.
        # Other users only see their own separately admitted bindings.
        if principal_ref != "user:" + current_user_id(self.ctx):
            return self.list(principal_ref, subnet_ref)
        conversation_id = "notification-endpoints:" + digest([principal_ref, subnet_ref])
        route = refresh_paired_route(conversation_id, principal_ref)
        if route:
            self.admit_route(principal_ref, subnet_ref, route["route_id"], label="Telegram")
        for route in refresh_max_routes(conversation_id, principal_ref):
            self.admit_route(principal_ref, subnet_ref, route["route_id"], label="MAX")
        return self.list(principal_ref, subnet_ref)

    def reconnect(self, principal_ref: str, subnet_ref: str, endpoint_id: str, *, expected_revision: int,
                  session_ref: str | None = None, permission: str | None = None) -> dict:
        """Explicitly renew a grant only after a fresh observation of this exact binding.

        No address is accepted from the caller. A changed binding is a different
        endpoint and needs explicit subscription. Older records keep their epoch.
        """
        with self.store.connect(read_only=True) as con:
            row = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=? AND principal_ref=? AND subnet_ref=?",
                              (endpoint_id, principal_ref, subnet_ref)).fetchone()
        if not row:
            raise PermissionError("endpoint is not owned by this user")
        if type(expected_revision) is not int or row["revision"] != expected_revision:
            raise NotificationConflict("endpoint revision conflict")
        route = durable_delivery.get_reply_route(row["route_id"])
        if row["transport"] == "web":
            if not route or not session_ref or route["destination_ref"].get("session_ref") != session_ref:
                raise PermissionError("device reconnect requires its original verified session")
            caps = json.loads(row["capabilities_json"])
            renewed = self.admit_device(principal_ref, subnet_ref, session_ref, kind=caps["presentation"],
                                        permission=permission or caps.get("permission", "default"))
            fresh = renewed["endpoint_id"] == endpoint_id
        else:
            from adaos.services.conversation_routes import refresh_max_routes, refresh_paired_route
            from adaos.services.personalization_runtime import current_user_id
            if principal_ref != "user:" + current_user_id(self.ctx):
                raise PermissionError("this binding requires its own verified reconnect admission")
            conversation_id = "notification-endpoints:" + digest([principal_ref, subnet_ref])
            routes = ([refresh_paired_route(conversation_id, principal_ref, force=True)] if row["transport"] == "telegram"
                      else refresh_max_routes(conversation_id, principal_ref))
            matched = next((item for item in routes if item and item["route_id"] == row["route_id"] and item["status"] == "active"), None)
            fresh = matched is not None
            if matched:
                renewed = self.admit_route(principal_ref, subnet_ref, matched["route_id"], label=row["label"])
                fresh = renewed["endpoint_id"] == endpoint_id
        if not fresh:
            raise PermissionError("current verified binding does not match this endpoint")
        with self.store.connect() as con:
            changed = con.execute("UPDATE notification_endpoints SET status='active',revision=revision+1 WHERE endpoint_id=? AND revision=?",
                                  (endpoint_id, expected_revision))
            if changed.rowcount != 1:
                raise NotificationConflict("endpoint observation changed; inspect the current revision before reconnecting")
            result = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
            con.commit()
        return self.public(result)

    def admit_device(self, principal_ref: str, subnet_ref: str, session_ref: str, *,
                     kind: str = "browser", permission: str = "default") -> dict:
        """Core-only verified session binding; registering permission never prompts an OS."""
        if kind not in {"browser", "native"} or permission not in {"default", "granted", "denied"}:
            raise NotificationError("invalid device notification capability")
        token(session_ref, "verified device/session reference")
        route_id = "notification-device:" + digest([principal_ref, subnet_ref, session_ref, kind])
        conversation_id = "notification-device-scope:" + digest([principal_ref, subnet_ref])
        durable_delivery.create_reply_route(conversation_id, route_id=route_id, transport="web",
            principal_scope=[principal_ref], destination_ref={"session_ref": session_ref},
            channel_context={"notification_device": kind})
        return self.admit_route(principal_ref, subnet_ref, route_id, label="Browser" if kind == "browser" else "Device",
            capabilities={"presentation": kind, "permission": permission, "read_receipt": False,
                          "maximum_sensitivity": "public" if kind == "native" else "private"})
