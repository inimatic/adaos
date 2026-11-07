from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Mapping
from adaos.domain.conversation_output import CommunicationContext, OutputRoutes, OutputSelectionReceipt, PublicationReceipt
from adaos.sdk.core.contracts import public_contract

from adaos.services import conversation_context, conversation_store
from adaos.services.yjs.webspace import default_webspace_id


def _output_owner(conversation_id: str) -> dict[str, Any]:
    from adaos.services.agent_context import get_ctx
    from adaos.sdk import access

    access.require("workspace.read")
    record = conversation_store.get_conversation(conversation_id)
    skill = get_ctx().skill_ctx.get()
    if not record or skill is None or record["owner"] != f"skill:{skill.name}":
        raise PermissionError("conversation output requires the owning skill context")
    return record


@public_contract(capabilities=("conversation.output_routes",), permissions=("workspace.read",),
    effects=("core_binding_observation",), errors=("conversation_owner_denied",),
    boundedness={"kind": "bounded_collection", "max_items": 20},
    pagination={"supported": False}, stability="beta", runtime_support={"owner": "conversation_runtime", "min_contract": 1})
def output_routes(conversation_id: str) -> OutputRoutes:
    """Discover Core-admitted routes for this owned chat; discovery grants no send authority."""
    from adaos.services.agent_context import get_ctx
    from adaos.services import durable_delivery

    record = _output_owner(conversation_id)
    durable_delivery.ensure_schema()
    # Admit the existing node pairing only for its verified owner. Applications
    # never receive or provide the Root credential, bot ID or chat ID.
    from adaos.sdk import access
    from adaos.services.personalization_runtime import current_user_id
    actor = access.current_user()
    if actor and actor["id"] == current_user_id():
        from adaos.services.conversation_routes import refresh_paired_route
        refresh_paired_route(conversation_id, f"user:{actor['id']}")
        from adaos.services.conversation_routes import refresh_max_routes
        refresh_max_routes(conversation_id, f"user:{actor['id']}")
    with get_ctx().sql.connect() as con:
        subject = f"{actor['kind']}:{actor['id']}" if actor else None
        rows = con.execute("""SELECT payload_json FROM conversation_reply_routes
            WHERE conversation_id=? AND EXISTS (
                SELECT 1 FROM json_each(payload_json, '$.principal_scope') WHERE value=?)
            ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                priority,updated_at DESC LIMIT 21""", (conversation_id, subject)).fetchall()
    import json
    routes = [json.loads(row[0]) for row in rows]
    truncated = len(routes) > 20
    routes = routes[:20]
    selected = record["policy"].get("output_route_ids", [])
    return {"items": [{"route_id": route["route_id"], "transport": route["transport"],
        "status": route["status"], "expires_at": route.get("expires_at"),
        "selected": route["route_id"] in selected} for route in routes],
        "automatic_external_outputs_default": "off", "conversation_id": conversation_id,
        "policy_revision": int(record["policy"].get("output_policy_revision", 0)), "truncated": truncated}


@public_contract(capabilities=("conversation.output_selection",), permissions=("workspace.read", "workspace.write"),
    effects=("durable_write",), errors=("conversation_owner_denied", "human_response_required", "policy_generation_stale", "route_scope_denied"),
    boundedness={"kind": "single_result", "max_routes": 20}, stability="beta",
    runtime_support={"owner": "conversation_runtime", "min_contract": 1},
    action_closure={"requires": ["accepted_interaction_response", "verified_actor", "exact_choice", "policy_generation_cas"]})
def select_output_routes(conversation_id: str, route_ids: Sequence[str], *, response_id: str) -> OutputSelectionReceipt:
    """Select existing authorized ReplyRoutes for future publications in this chat.

    Only the current verified user can select destinations in their principal
    scope. Bot/chat IDs are never accepted from application arguments.
    """
    import json
    from adaos.sdk import access
    from adaos.services import durable_delivery
    from adaos.services import conversation_publication
    from adaos.services.agent_context import get_ctx

    record = _output_owner(conversation_id)
    conversation_publication.ensure_schema()
    access.require("workspace.write")
    actor = access.current_user()
    if actor is None:
        raise PermissionError("output selection requires a verified user")
    subject = f"{actor['kind']}:{actor['id']}"
    selected = list(dict.fromkeys(route_ids))
    if len(selected) > 20:
        raise ValueError("too many output routes")
    response = conversation_store.get_interaction_response(response_id)
    interaction = conversation_store.get_interaction(response["interaction_id"]) if response else None
    confirmed_routes = (response or {}).get("values", {}).get("route_ids")
    if confirmed_routes is None and interaction and interaction["input_spec"]["kind"] == "choice":
        # Cross-channel buttons/text choices retain their exact frozen meaning
        # in the Interaction, rather than inventing a second confirmation path.
        choices = (interaction.get("metadata") or {}).get("output_route_choices") or {}
        confirmed_routes = choices.get((response or {}).get("values", {}).get("choice"))
    if (not response or not interaction or response["status"] not in {"answered", "accepted"}
            or not response["validation"].get("valid")
            or response["actor_id"] not in {subject, actor["id"]}
            or interaction["conversation_id"] != conversation_id or interaction["owner"] != record["owner"]
            or (interaction.get("metadata") or {}).get("purpose") != "conversation.output_routes"
            or confirmed_routes != selected):
        raise PermissionError("output selection requires the exact accepted human interaction response")
    for route_id in selected:
        route = durable_delivery.get_reply_route(route_id)
        if (not route or route["conversation_id"] != conversation_id or subject not in route["principal_scope"]
                or route["status"] != "active"
                or durable_delivery._expired(route.get("expires_at"), now=durable_delivery._now())):
            raise PermissionError("output route outside the current user conversation scope")
    with get_ctx().sql.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        previous = con.execute("SELECT selection_digest,receipt_json FROM conversation_output_selections WHERE response_id=?",
                               (response_id,)).fetchone()
        selection_digest = conversation_publication._digest([conversation_id, selected])
        if previous:
            if previous[0] != selection_digest:
                raise PermissionError("output selection response already consumed")
            receipt = json.loads(previous[1])
            policy_row = con.execute("SELECT policy_json FROM conversation_conversations WHERE conversation_id=?",
                                     (conversation_id,)).fetchone()
            policy = json.loads(policy_row[0])
            latest = con.execute("""SELECT response_id FROM conversation_output_selections
                WHERE conversation_id=? ORDER BY CAST(json_extract(receipt_json,'$.policy_revision') AS INTEGER) DESC LIMIT 1""",
                (conversation_id,)).fetchone()
            # Older context upserts erased the complete policy. Only a replay
            # of the latest exact verified human receipt can repair that loss.
            # An explicit disable or a newer selection is never overwritten.
            output_keys = {"output_route_ids", "output_policy_revision", "output_selection_response_id"}
            if not output_keys.intersection(policy) and latest and latest[0] == response_id:
                policy.update(output_route_ids=selected,output_policy_revision=receipt["policy_revision"],
                    output_selection_response_id=response_id,output_policy_recovered_from_receipt=response_id)
                con.execute("UPDATE conversation_conversations SET policy_json=? WHERE conversation_id=?",
                            (json.dumps(policy),conversation_id))
            con.commit()
            _close_output_selection_decision(interaction, response_id, receipt)
            return receipt
        row = con.execute("SELECT policy_json FROM conversation_conversations WHERE conversation_id=?",
                          (conversation_id,)).fetchone()
        policy = json.loads(row[0])
        if (interaction.get("metadata") or {}).get("output_policy_revision") != int(policy.get("output_policy_revision", 0)):
            raise PermissionError("output selection policy generation is stale")
        policy.update(output_route_ids=selected, output_policy_revision=int(policy.get("output_policy_revision", 0)) + 1)
        policy["output_selection_response_id"] = response_id
        con.execute("UPDATE conversation_conversations SET policy_json=? WHERE conversation_id=?",
                    (json.dumps(policy), conversation_id))
        receipt = {"conversation_id": conversation_id, "route_ids": selected,
                   "policy_revision": policy["output_policy_revision"]}
        con.execute("INSERT INTO conversation_output_selections VALUES(?,?,?,?)",
                    (response_id, conversation_id, selection_digest, json.dumps(receipt)))
        con.commit()
    _close_output_selection_decision(interaction, response_id, receipt)
    return receipt


def _close_output_selection_decision(interaction, response_id, receipt) -> None:
    """Publish the observed selection outcome, then retire obsolete choices.

    The selection receipt is already committed and idempotent. Re-entering this
    closure never selects a route again, including after a newer user decision.
    """
    from adaos.services import conversation_interactions

    dispatch = conversation_store.get_interaction_dispatch(response_id=response_id)
    if dispatch and dispatch["status"] == "pending":
        owner = "conversation.output_selection:" + response_id
        dispatch = conversation_store.claim_interaction_dispatch(response_id, lease_owner=owner)
        conversation_store.complete_interaction_dispatch(dispatch["dispatch_id"], lease_owner=owner,
            status="succeeded", outcome={"selection_receipt":receipt})
    current = conversation_store.get_interaction(interaction["interaction_id"])
    if current and current["status"] == "answered":
        current = conversation_interactions.accept_response(current["interaction_id"], response_id,
            expected_generation=current["generation"])
    if current and current["status"] == "accepted":
        conversation_interactions.transition_interaction(current["interaction_id"], "complete",
            expected_generation=current["generation"], reason="output_selection_receipt_committed")
    # Other choices captured from the previous policy generation cannot be
    # applied any more. Retain their audit trail instead of leaving dead cards.
    for old in conversation_store.list_interactions(conversation_id=receipt["conversation_id"],
            statuses=("created","projected","awaiting_input","partially_answered","validation_failed","answered"),limit=1000):
        metadata = old.get("metadata") or {}
        revision = metadata.get("output_policy_revision")
        if (old["interaction_id"] != interaction["interaction_id"]
                and old["owner"] == interaction["owner"]
                and metadata.get("purpose") == "conversation.output_routes"
                and type(revision) is int and revision < receipt["policy_revision"]):
            conversation_interactions.transition_interaction(old["interaction_id"], "supersede",
                expected_generation=old["generation"], reason="output_policy_generation_replaced")
            previous_response = metadata.get("accepted_response_id") or metadata.get("latest_response_id")
            if previous_response:
                conversation_store.reject_interaction_dispatch_if_pending(previous_response,
                    reason_code="output_policy_generation_replaced",
                    now_iso=datetime.now(timezone.utc).isoformat())


@public_contract(capabilities=("conversation.communication_context",), permissions=("workspace.read",),
    boundedness={"kind": "single_result"}, stability="beta", runtime_support={"owner": "conversation_runtime", "min_contract": 1})
def communication_context(*, webspace_id: str | None = None, channel_id: str = "general") -> CommunicationContext:
    """Resolve the persisted active owned chat and verified ingress identity.

    An unavailable pointer never falls back to a global/shared chat. Origin is
    descriptive, not authorization; Core suppresses delivery to the ingress
    Telegram destination. access.invocation contains only request/replay IDs.
    """
    from adaos.sdk import access
    from adaos.sdk.io.context import get_current_meta
    actor = access.current_user()
    invocation = access.invocation() or {}
    pointer = current(webspace_id=webspace_id, channel_id=channel_id)
    conversation_id = (pointer or {}).get("conversation_id")
    base = {"status": "unavailable", "reason": "active_owned_conversation_missing",
        "conversation_id": None, "thread_id": None, "owner": None,
        "request_id": invocation.get("request_id"),
        "verified_user_ref": f"user:{actor['id']}" if actor else None,
        "origin_transport": None}
    if not conversation_id or actor is None:
        return base
    record = _output_owner(conversation_id)
    meta = get_current_meta()
    thread = meta.get("thread_id") if meta.get("conversation_id") == conversation_id else None
    return {**base, "status": "ready", "reason": None,
        "conversation_id": conversation_id, "owner": record["owner"],
        "thread_id": thread or (pointer or {}).get("active_thread_id"),
        "origin_transport": str(meta.get("io_type") or "api")}


@public_contract(capabilities=("conversation.publication",), permissions=("workspace.read", "workspace.write"),
    effects=("durable_write", "conversation_delivery"), errors=("conversation_owner_denied", "publication_idempotency_conflict", "route_scope_denied"),
    boundedness={"kind": "single_result", "max_routes": 20}, stability="beta",
    runtime_support={"owner": "conversation_runtime", "min_contract": 1})
def publish(
    *, conversation_id: str, publication_key: str, text: str,
    category: str = "notification", thread_id: str | None = None,
    task_ref: Mapping[str, Any] | None = None,
    existing_message_id: str | None = None,
) -> PublicationReceipt:
    """Atomically commit text and per-target outbox work; returns committed, never delivered.

    The key is stable for one semantic output across retries/restart. Route
    selection comes from this chat's policy; transport retry never runs a tool.
    Core invokes this port for admitted service output as well as skill tools.
    existing_message_id attaches delivery to an exact owned, active stored turn;
    its text, thread, author and local actions are preserved without duplication.
    """
    from adaos.services import conversation_publication
    from adaos.services.agent_context import get_ctx

    record, selected_routes = _publication_scope(conversation_id)
    projection_webspace = _publication_projection_webspace(record, thread_id)
    return conversation_publication.publish(publication_key=publication_key,
        conversation_id=conversation_id, owner=record["owner"], webspace_id=record["webspace_id"],
        channel_id=str(record["meta"].get("channel_id") or "general"), text=text,
        category=category, thread_id=thread_id, task_ref=task_ref,
        route_ids=selected_routes, bus=get_ctx().bus,
        existing_message_id=existing_message_id,
        projection_webspace_id=projection_webspace)


def _publication_projection_webspace(record: Mapping[str, Any], thread_id: str | None) -> str | None:
    """Shared history is not a browser address. Derive one local view from ingress.

    No application-supplied destination is accepted. The verified invocation,
    owned channel pointer and original conversation/thread must agree. Background
    workers retain this context; switching a different window cannot redirect it.
    This never selects external ReplyRoutes or rewrites the canonical history.
    """
    if record.get("webspace_id") != "global":
        return None
    from adaos.sdk import access
    from adaos.sdk.io.context import get_current_meta
    meta = get_current_meta()
    source = str(meta.get("source_webspace_id") or meta.get("webspace_id") or "").strip()
    if not source or source == "global" or not access.current_user():
        return None
    if meta.get("conversation_id") != record["conversation_id"]:
        raise PermissionError("publication projection needs its admitted conversation")
    ingress_thread = str(meta.get("thread_id") or meta.get("conversation_topic_id") or meta.get("topic_id") or "").strip()
    if ingress_thread != str(thread_id or "").strip():
        raise PermissionError("publication projection differs from admitted ingress thread")
    channel = str(meta.get("dialog_channel_id") or record.get("meta", {}).get("channel_id") or "general")
    pointer = conversation_store.get_dialog_channel(source, channel)
    if (not pointer or pointer.get("conversation_id") != record["conversation_id"]
            or pointer.get("owner") != record["owner"] or pointer.get("status") != "active"):
        raise PermissionError("publication projection needs its owned active channel")
    return source


def _publication_scope(conversation_id: str) -> tuple[dict[str, Any], list[str]]:
    """Share the exact SDK principal/ingress admission with ordinary chat sends."""
    record = _output_owner(conversation_id)
    from adaos.sdk import access
    access.require("workspace.write")
    from adaos.sdk.io.context import get_current_meta
    from adaos.services import durable_delivery
    origin = get_current_meta()
    actor = access.current_user()
    subject = f"{actor['kind']}:{actor['id']}" if actor else None
    # A chat policy is not authority to publish another principal's output to
    # a private destination. Background service publications use Core's trusted
    # port; SDK calls keep the verified invocation principal boundary.
    selected_routes = [route_id for route_id in record["policy"].get("output_route_ids", [])[:20]
        if subject and subject in (durable_delivery.get_reply_route(route_id) or {}).get("principal_scope", [])]
    if origin.get("io_type") in {"telegram", "tg", "max"}:
        transport = 'telegram' if origin['io_type'] == 'tg' else origin['io_type']
        selected_routes = [route_id for route_id in selected_routes
            if not ((durable_delivery.get_reply_route(route_id) or {}).get('transport') == transport
                and str((durable_delivery.get_reply_route(route_id) or {}).get("destination_ref", {}).get("chat_id") or "")
                    == str(origin.get("chat_id") or ""))]
    return record, selected_routes


def current(*, webspace_id: str | None = None, channel_id: str = "general") -> dict[str, Any] | None:
    """Return the persisted dialog channel pointer for the current node."""
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    return conversation_store.get_dialog_channel(ws, str(channel_id or "general").strip() or "general")


def list_published_agents(
    *, channel_id: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """Return a bounded authorized projection of agents published by installed skills."""

    from adaos.sdk import access
    from adaos.services.router import dialog_registry

    access.require("workspace.read")
    bounded_limit = max(1, min(int(limit), 200))
    channel = str(channel_id or "").strip()
    records = dialog_registry.published_agent_records()
    if channel:
        records = [
            item
            for item in records
            if str(item.get("channel_id") or "").strip() == channel
        ]
    items: list[dict[str, Any]] = []
    for record in records[:bounded_limit]:
        agent_id = str(record.get("id") or "").strip()
        label = str(record.get("label") or agent_id).strip()
        if not agent_id or not label:
            continue
        items.append(
            {
                "id": agent_id,
                "label": label,
                "owner": str(record.get("owner") or "").strip() or None,
                "channel_id": str(record.get("channel_id") or "").strip() or None,
                "skill": str(record.get("skill") or "").strip() or None,
                "kind": str(record.get("kind") or "agent").strip() or "agent",
                "icon": str(record.get("icon") or "").strip() or None,
                "source": str(record.get("source") or "conversation_agent_registry").strip(),
            }
        )
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "ok": True,
        "schema": "adaos.sdk.conversation.published_agents.v1",
        "items": items,
        "count": len(items),
        "total": len(records),
        "truncated": len(records) > bounded_limit,
        "observed_at": observed_at,
        "freshness": "current",
        "source": "conversation_agent_registry",
        "invalidation_tags": ["conversation.agents"],
    }


def open(
    *,
    conversation_id: str,
    owner: str,
    webspace_id: str | None = None,
    channel_id: str | None = None,
    title: str | None = None,
    active_agent_id: str | None = None,
    policy: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a node-local conversation and optional channel pointer."""
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id=ws,
        owner=owner,
        title=title,
        active_agent_id=active_agent_id,
        policy=policy,
        meta=meta,
    )
    if channel_id:
        conversation_store.upsert_dialog_channel(
            webspace_id=ws,
            channel_id=channel_id,
            label=title or channel_id,
            owner=owner,
            conversation_id=conversation_id,
            active_agent_id=active_agent_id,
            policy=policy,
            meta=meta,
        )
    return {
        "conversation_id": conversation_id,
        "webspace_id": ws,
        "owner": owner,
        "channel_id": channel_id,
        "active_agent_id": active_agent_id,
    }


def append(
    *,
    conversation_id: str,
    text: str,
    role: str,
    thread_id: str | None = None,
    webspace_id: str | None = None,
    channel_id: str = "general",
    owner: str = "core",
    actor_id: str | None = None,
    actor_label: str | None = None,
    payload: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
) -> dict[str, Any] | None:
    ws = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
    return conversation_store.append_message(
        conversation_id=conversation_id,
        thread_id=thread_id,
        webspace_id=ws,
        channel_id=channel_id,
        owner=owner,
        role=role,
        text=text,
        actor_id=actor_id,
        actor_label=actor_label,
        payload=payload,
        meta=meta,
        retention_class=retention_class,
        retention_until=retention_until,
        redaction_state=redaction_state,
        redacted_at=redacted_at,
        redaction_reason=redaction_reason,
    )


def get(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    before_cursor: Any = None,
    limit: int = 50,
) -> dict[str, Any]:
    return conversation_store.list_projection(
        conversation_id,
        thread_id=thread_id,
        before_cursor=before_cursor,
        limit=limit,
        max_items=max(limit, 200),
    )


def export(
    conversation_id: str,
    *,
    include_redacted: bool = False,
    include_memory: bool = True,
    include_traces: bool = True,
    limit: int = 5000,
) -> dict[str, Any]:
    """Return a privacy/audit aware conversation export bundle."""
    return conversation_store.export_conversation(
        conversation_id,
        include_redacted=include_redacted,
        include_memory=include_memory,
        include_traces=include_traces,
        limit=limit,
    )


def redact(
    conversation_id: str,
    *,
    reason: str = "user_request",
    include_memory: bool = True,
    include_traces: bool = True,
) -> dict[str, Any]:
    """Soft-redact a conversation bundle and record a durable audit event."""
    return conversation_store.redact_conversation(
        conversation_id,
        reason=reason,
        hard_delete=False,
        include_memory=include_memory,
        include_traces=include_traces,
    )


def delete(
    conversation_id: str,
    *,
    reason: str = "user_request",
    include_memory: bool = True,
    include_traces: bool = True,
) -> dict[str, Any]:
    """Hard-delete a conversation bundle and record a durable audit event."""
    return conversation_store.redact_conversation(
        conversation_id,
        reason=reason,
        hard_delete=True,
        include_memory=include_memory,
        include_traces=include_traces,
    )


def start_thread(
    conversation_id: str,
    *,
    thread_id: str | None = None,
    title: str | None = None,
    created_by: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    return conversation_store.start_thread(
        conversation_id=conversation_id,
        thread_id=thread_id,
        title=title,
        created_by=created_by,
        meta=meta,
    )


def context(
    conversation_id: str,
    *,
    requester_owner: str,
    channel_id: str | None = None,
    thread_id: str | None = None,
    topic_ref: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    memory_owner: str | None = None,
    include_global_user: bool = True,
    allow_cross_owner_memory: bool = False,
    budgets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a budgeted conversation context packet for LLM/runtime calls."""
    return conversation_context.build_context_packet(
        conversation_id=conversation_id,
        requester_owner=requester_owner,
        channel_id=channel_id,
        thread_id=thread_id,
        topic_ref=topic_ref,
        agent_id=agent_id,
        memory_owner=memory_owner,
        include_global_user=include_global_user,
        allow_cross_owner_memory=allow_cross_owner_memory,
        budgets=budgets,
    )


def ensure_builder_topic(
    *,
    webspace_id: str | None = None,
    active_draft_id: str | None = None,
    scenario_id: str | None = None,
    dev_webspace_id: str | None = None,
    project_id: str | None = None,
    title: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure the canonical Builder conversation and one project topic."""

    from adaos.services.conversation_links import ensure_builder_topic as _ensure

    return dict(
        _ensure(
            webspace_id,
            active_draft_id=active_draft_id,
            scenario_id=scenario_id,
            dev_webspace_id=dev_webspace_id,
            project_id=project_id,
            title=title,
            meta=meta,
        )
        or {}
    )


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
) -> dict[str, Any] | None:
    """Create or update the durable join for one Builder artifact change."""

    return conversation_store.upsert_development_change(
        change_id=change_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        topic_id=topic_id,
        status=status,
        source_message_ids=source_message_ids,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
        revision_refs=revision_refs,
        commit_refs=commit_refs,
        result_message_id=result_message_id,
        request_id=request_id,
        model=model,
        summary=summary,
        meta=meta,
    )


def get_development_change(change_id: str) -> dict[str, Any] | None:
    """Return one Builder Change aggregate by stable change id."""

    return conversation_store.get_development_change(change_id)


def list_development_changes(
    *,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    artifact_kind: str | None = None,
    artifact_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List bounded Builder Change aggregates."""

    return conversation_store.list_development_changes(
        conversation_id=conversation_id,
        topic_id=topic_id,
        artifact_kind=artifact_kind,
        artifact_id=artifact_id,
        limit=limit,
    )


def redact_messages(
    message_ids: Sequence[str],
    *,
    reason: str = "user_request",
) -> dict[str, Any]:
    """Soft-redact selected messages and retain the surrounding history."""
    return conversation_store.redact_messages(message_ids, reason=reason)


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
) -> dict[str, Any] | None:
    """Create or advance one Builder Run linked to a canonical Change."""

    return conversation_store.upsert_development_run(
        run_id=run_id,
        change_id=change_id,
        conversation_id=conversation_id,
        activity=activity,
        executor=executor,
        purpose=purpose,
        adoption_status=adoption_status,
        status=status,
        thread_id=thread_id,
        topic_id=topic_id,
        context_packet_digest=context_packet_digest,
        environment_ref=environment_ref,
        input_refs=input_refs,
        output_refs=output_refs,
        evidence_refs=evidence_refs,
        started_at=started_at,
        completed_at=completed_at,
        error=error,
    )


def get_development_run(run_id: str) -> dict[str, Any] | None:
    """Return one strict `adaos.builder.run.v1` projection."""

    return conversation_store.get_development_run(run_id)


def list_development_runs(
    *,
    change_id: str | None = None,
    conversation_id: str | None = None,
    topic_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List bounded Builder Runs without synthesizing extra Changes."""

    return conversation_store.list_development_runs(
        change_id=change_id,
        conversation_id=conversation_id,
        topic_id=topic_id,
        limit=limit,
    )


def classify_action_risk(action: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """Classify one proposed action through the conversation safety policy."""

    from adaos.services.conversation_safety import classify_action_risk as _classify

    return dict(_classify(action) or {})


__all__ = [
    "communication_context",
    "publish",
    "output_routes",
    "select_output_routes",
    "append",
    "classify_action_risk",
    "context",
    "current",
    "delete",
    "ensure_builder_topic",
    "export",
    "get",
    "get_development_change",
    "get_development_run",
    "list_development_changes",
    "list_development_runs",
    "list_published_agents",
    "open",
    "redact",
    "start_thread",
    "upsert_development_change",
    "upsert_development_run",
]
