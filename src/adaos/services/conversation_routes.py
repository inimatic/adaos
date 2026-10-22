"""Admit existing platform communication bindings into owned ReplyRoutes."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from adaos.services import durable_delivery
from adaos.services.agent_context import get_ctx


def refresh_paired_route(conversation_id: str, principal: str) -> dict[str, Any] | None:
    """Core-only owner admission from the existing Root Telegram pairing service.

    This discovers a destination; it never selects it or sends a message.
    A live binding revision is captured by its exact bot/chat IDs. Revocation
    or a changed pairing therefore cannot redirect already queued content.
    """
    import requests
    ctx = get_ctx()
    hub_id = str(ctx.config.subnet_id or "")
    route_prefix = "paired-telegram:" + hashlib.sha256(
        f"{conversation_id}:{principal}:{hub_id}".encode()).hexdigest()
    durable_delivery.ensure_schema()
    with ctx.sql.connect() as con:
        rows = con.execute("SELECT payload_json FROM conversation_reply_routes WHERE conversation_id=? AND transport='telegram'",
                           (conversation_id,)).fetchall()
    bindings = [json.loads(row[0]) for row in rows]
    bindings = [route for route in bindings if route["route_id"].startswith(route_prefix)]
    existing = next((route for route in bindings if route["status"] == "active"), None)
    observed = (existing or {}).get("channel_context", {}).get("binding_observed_at", 0)
    if time.time() - observed < 30:
        return existing
    root = getattr(getattr(ctx.config, "root_settings", None), "base_url", None) or ctx.settings.api_base
    try:
        response = requests.get(root.rstrip("/") + "/io/tg/pair/link",
                                params={"hub_id": hub_id}, timeout=3)
        value = response.json() if response.status_code in {200, 404} else {}
        link = value.get("link", value)
        if response.status_code == 404 and value.get("error") == "pairing_not_found":
            _observe_bindings(bindings, active_route_id=None)
            return None
        if not value.get("ok") or not link.get("chat_id") or not link.get("bot_id"):
            return None
    except (requests.RequestException, ValueError):
        return None
    # A changed binding is a NEW destination and needs a NEW explicit selection.
    # Never mutate an admitted route's address underneath queued output.
    current = next((route for route in bindings if route["status"] == "active"
        and route["destination_ref"].get("bot_id") == str(link["bot_id"])
        and route["destination_ref"].get("chat_id") == str(link["chat_id"])), None)
    route_id = current["route_id"] if current else route_prefix + ":" + uuid.uuid4().hex
    _observe_bindings(bindings, active_route_id=route_id)
    if current is not None:
        return durable_delivery.get_reply_route(route_id)
    return durable_delivery.create_reply_route(conversation_id, route_id=route_id,
        transport="telegram", principal_scope=[principal],
        destination_ref={"hub_id": hub_id, "chat_id": str(link["chat_id"]),
                         "bot_id": str(link["bot_id"]), "routing_mode": "hub_pairing"},
        channel_context={"binding_ref": "telegram-pairing:" + hub_id, "binding_observed_at": time.time()})


def _observe_bindings(bindings: list[dict], *, active_route_id: str | None) -> None:
    """Persist authoritative pairing changes without changing immutable addresses."""
    with get_ctx().sql.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        for route in bindings:
            route["status"] = "active" if route["route_id"] == active_route_id else "revoked"
            route["channel_context"]["binding_observed_at"] = time.time()
            route["updated_at"] = durable_delivery._now()
            con.execute("UPDATE conversation_reply_routes SET status=?,updated_at=?,payload_json=? WHERE route_id=?",
                (route["status"], route["updated_at"], durable_delivery._dump(route), route["route_id"]))
        con.commit()
