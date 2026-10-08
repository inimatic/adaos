from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.domain.development_budget import execution_billable_token_limit
from adaos.sdk.core.decorators import subscribe
from adaos.domain.development_escalations import (
    CORE_IMPACT_CLASSES,
    normalize_development_escalations,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


DEVELOPMENT_SIGNAL_SCHEMA = "adaos.development_signal.v1"
DEV_TICKET_SCHEMA = "adaos.dev_ticket.v1"
STATE_SCHEMA = "adaos.development_tickets.state.v1"
COMPATIBILITY_PENDING_ACTION_KIND = "development_ticket.runtime_compatibility.review"
COMPATIBILITY_RESPONSE_TOPIC = "development_tickets.compatibility.response"
CORE_CAPABILITY_PENDING_ACTION_KIND = "development_ticket.core_capability.available"
CORE_CAPABILITY_RESPONSE_TOPIC = "development_tickets.core_capability.response"
BUILDER_CLARIFICATION_PENDING_ACTION_KIND = "development_ticket.builder.clarification"
BUILDER_CLARIFICATION_RESPONSE_TOPIC = "development_tickets.builder.clarification.response"
PUBLICATION_PERMISSION_PENDING_ACTION_KIND = (
    "development_ticket.publication_permission.review"
)
PUBLICATION_PERMISSION_RESPONSE_TOPIC = (
    "development_tickets.publication_permission.response"
)
DEV_TICKET_LIFECYCLE_EVENT_SCHEMA = "adaos.dev_ticket.lifecycle_event.v1"
ACTIVE_SIGNAL_STATES = {
    "captured",
    "classified",
    "needs_clarification",
    "triaged",
    "deferred",
    "teacher_candidate",
    "repair_created",
    "issue_created",
    "in_progress",
}
ACTIVE_TICKET_STATES = {
    "captured",
    "proposed",
    "accepted",
    "deferred",
    "waiting_for_user",
    "waiting_for_core",
    "ready_for_builder",
    "claimed",
    "in_progress",
    "in_builder",
    "resolved",
    "verified",
}
TERMINAL_TICKET_STATES = {"closed", "superseded", "stale"}
TICKET_PRIORITIES = {"must", "should", "could", "deferred"}
TICKET_STATUS_GROUPS = {
    "open": ACTIVE_TICKET_STATES,
    "active": ACTIVE_TICKET_STATES,
    "triage": {"captured", "proposed", "accepted", "waiting_for_user", "ready_for_builder"},
    "waiting": {"deferred", "waiting_for_user", "waiting_for_core"},
    "waiting_for_core": {"waiting_for_core"},
    "work": {"claimed", "in_progress", "in_builder"},
    "review": {"resolved", "verified"},
    "terminal": TERMINAL_TICKET_STATES,
    "closed": TERMINAL_TICKET_STATES,
}
SDK_UNDERSTANDING_SIGNAL_KINDS = {
    "sdk_unclear_definition",
    "sdk_application_failure",
    "sdk_observability_gap",
    "sdk_example_gap",
    "sdk_policy_boundary",
    "sdk_generalization_pressure",
    "builder_rejection_learning",
}
TICKET_RELATION_KINDS = {
    "blocks",
    "blocked_by",
    "related",
    "duplicate_of",
    "supersedes",
    "caused_by",
}

DEFAULT_AUTONOMOUS_REPAIR_BUDGET = {
    "schema": "adaos.builder.execution_budget.v1",
    "source": "development_ticket.default",
    "max_tokens": 45000,
    "token_budget_metric": "fresh_plus_output",
    "max_billable_tokens": 360000,
    "max_wall_seconds": 1200,
}
INVERSE_TICKET_RELATION = {
    "blocks": "blocked_by",
    "blocked_by": "blocks",
    "duplicate_of": "supersedes",
    "supersedes": "duplicate_of",
}
RECEIVER_COMPATIBILITY_REASONS = {
    "stream_receiver_policy_missing",
    "stream_receiver_not_declared",
}
_LOCK = threading.RLock()
_STATE_READ_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_log = logging.getLogger("adaos.development_tickets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def project_development_ticket_summary(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded list projection; use get/show for ticket detail."""

    return {
        key: ticket.get(key)
        for key in (
            "schema",
            "ticket_id",
            "revision",
            "kind",
            "status",
            "status_group",
            "summary",
            "severity",
            "priority",
            "blocking",
            "owner_area",
            "component_ref",
            "web_component",
            "owner_scope",
            "origin_scope",
            "target_scope",
            "occurrence_count",
            "source",
            "created_at",
            "updated_at",
        )
        if ticket.get(key) not in (None, "")
    }


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _sequence_of_mappings(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not value:
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fingerprint(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _normalized_failure_text(value: Any) -> str:
    text = _text(value).lower()
    text = re.sub(
        r"\b(?:candidate|task|session|run|operation)\.[a-z0-9_.-]+\b",
        "<execution-ref>",
        text,
    )
    text = re.sub(r"sha256:[a-f0-9]{32,}", "sha256:<digest>", text)
    return re.sub(r"\s+", " ", text).strip()[:1200]


def _target_identity(target_scope: Mapping[str, Any]) -> str:
    target = _mapping(target_scope)
    target_type = _text(target.get("type")) or "unknown"
    target_id = _text(target.get("id") or target.get("name")) or "unknown"
    version = _text(target.get("version"))
    digest = _text(target.get("digest"))
    return ":".join(item for item in (target_type, target_id, version, digest) if item)


def _project_id_from_target(target_scope: Mapping[str, Any]) -> str:
    target = _mapping(target_scope)
    token = _text(target.get("id") or target.get("name"))
    return token or _text(target.get("type")) or "unknown"


def ticket_status_group(status: str) -> str:
    token = _text(status)
    if token in {"deferred", "waiting_for_core"}:
        return "waiting"
    if token in {"claimed", "in_progress", "in_builder"}:
        return "work"
    if token in {"resolved", "verified"}:
        return "review"
    if token in TERMINAL_TICKET_STATES:
        return "closed"
    return "triage"


def _owner_area_from_scope(target_scope: Mapping[str, Any] | None, metadata: Mapping[str, Any] | None = None) -> str:
    target = _mapping(target_scope)
    meta = _mapping(metadata)
    explicit = _text(meta.get("owner_area") or target.get("owner_area")).lower()
    if explicit:
        return explicit
    target_type = _text(target.get("type")).lower()
    if target_type in {
        "project",
        "skill",
        "scenario",
        "sdk",
        "api",
        "core",
        "builder",
        "runtime",
        "nlu",
        "webui",
        "component",
        "modal",
        "user",
    }:
        if target_type in {"webui", "component", "modal"}:
            return "project"
        return target_type
    if _text(target.get("project_ref") or target.get("project_id")):
        return "project"
    if _text(target.get("skill_ref") or target.get("skill_id")):
        return "skill"
    if _text(target.get("scenario_ref") or target.get("scenario_id")):
        return "scenario"
    return "workspace"


def _component_ref_from_scopes(
    target_scope: Mapping[str, Any] | None,
    origin_scope: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    target = _mapping(target_scope)
    origin = _mapping(origin_scope)
    meta = _mapping(metadata)
    for source in (meta, target, origin):
        web_component = _mapping(source.get("web_component"))
        token = _text(web_component.get("ref") or web_component.get("id"))
        if token:
            return token
        for key in ("component_ref", "ref", "canonical_ref", "target_ref", "modal_ref", "skill_ref", "scenario_ref", "project_ref"):
            token = _text(source.get(key))
            if token:
                return token
    target_type = _text(target.get("type") or origin.get("type"))
    target_id = _text(target.get("id") or target.get("name") or origin.get("id") or origin.get("name"))
    if target_type and target_id:
        return f"{target_type}:{target_id}"
    return ""


def _normalize_relation_ref(ref: Mapping[str, Any]) -> dict[str, Any] | None:
    relation = _text(ref.get("relation") or ref.get("type")).lower()
    if relation == "dev_ticket":
        relation = _text(ref.get("relation")).lower()
    relation = relation or "related"
    if relation not in TICKET_RELATION_KINDS:
        relation = "related"
    ticket_id = _text(ref.get("ticket_id") or ref.get("id"))
    target_ref = _text(ref.get("target_ref") or ref.get("ref"))
    if not target_ref and ticket_id:
        target_ref = f"dticket:{ticket_id}"
    if not ticket_id and target_ref.startswith("dticket:"):
        ticket_id = target_ref.split(":", 1)[1].strip()
    if not target_ref:
        return None
    item = {**dict(ref), "type": relation, "relation": relation, "target_ref": target_ref}
    if ticket_id:
        item["ticket_id"] = ticket_id
    return item


def _normalize_relation_refs(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for group in groups:
        for raw in group or []:
            if not isinstance(raw, Mapping):
                continue
            item = _normalize_relation_ref(raw)
            if item:
                refs.append(item)
    return _merge_refs([], refs)


def _normalized_ticket_view(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Build a shallow normalized view suitable for filtering and sorting."""

    out = dict(ticket)
    try:
        out["revision"] = max(1, int(out.get("revision") or 1))
    except (TypeError, ValueError):
        out["revision"] = 1
    out["status_group"] = ticket_status_group(_text(out.get("status")))
    priority = _text(out.get("priority")).lower()
    out["priority"] = priority if priority in TICKET_PRIORITIES else "should"
    out["owner_area"] = _text(out.get("owner_area")) or _owner_area_from_scope(
        _mapping(out.get("target_scope")),
        _mapping(out.get("metadata")),
    )
    out["component_ref"] = _text(out.get("component_ref")) or _component_ref_from_scopes(
        _mapping(out.get("target_scope")),
        _mapping(out.get("origin_scope")),
        _mapping(out.get("metadata")),
    )
    web_component = _web_component_from_scopes(
        _mapping(out.get("web_component")),
        _mapping(out.get("target_scope")),
        _mapping(out.get("origin_scope")),
        _mapping(out.get("metadata")),
    )
    if web_component:
        out["web_component"] = web_component
    out["relation_refs"] = _normalize_relation_refs(
        _sequence_of_mappings(out.get("relation_refs") or []),
        _sequence_of_mappings(out.get("related_refs") or []),
    )
    return out


def _normalized_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    return _clone(_normalized_ticket_view(ticket))


def _ref_tail(value: Any, prefix: str) -> str:
    token = _text(value)
    wanted = f"{prefix}:"
    return token[len(wanted):].strip() if token.startswith(wanted) else ""


def _development_source_target(target_scope: Mapping[str, Any]) -> dict[str, str | None]:
    target = _mapping(target_scope)
    target_type = _text(target.get("type")).lower().rstrip("s") or "unknown"
    target_id = _text(target.get("id") or target.get("name"))
    project_id = _text(target.get("project_id") or _ref_tail(target.get("project_ref"), "project")) or None
    if target_type in {"project", "scenario", "skill"} and target_id:
        return {"type": target_type, "id": target_id, "project_id": project_id}
    for key, object_type in (
        ("scenario_ref", "scenario"),
        ("skill_ref", "skill"),
        ("scenario_id", "scenario"),
        ("skill_id", "skill"),
        ("project_ref", "project"),
        ("project_id", "project"),
    ):
        value = target.get(key)
        ref_value = _ref_tail(value, object_type) if str(value or "").startswith(f"{object_type}:") else _text(value)
        if ref_value:
            return {"type": object_type, "id": ref_value, "project_id": project_id}
    return {"type": target_type, "id": target_id or None, "project_id": project_id}


def _source_materialization_options(
    *,
    source: str,
    target_type: str,
    target_id: str | None,
    project_id: str | None = None,
    source_path: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "needs_materialization",
        "source": source or "unknown",
        "target_type": target_type,
        "target_id": target_id or None,
        "project_id": project_id or None,
        "source_path": source_path or None,
        "options": [
            "materialize_dev_source",
            "create_local_fork",
            "defer",
        ],
        "default_option": "materialize_dev_source",
    }
    if extra:
        payload.update(dict(extra))
    return payload


def _source_materialization_ref(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    materialization = _mapping(value)
    if not materialization:
        return None
    ref = {
        key: materialization.get(key)
        for key in (
            "schema",
            "fork_id",
            "strategy",
            "status",
            "component_ref",
            "project_id",
            "project_ref",
            "source_digest",
            "idempotent",
        )
        if materialization.get(key) not in (None, "", [], {})
    }
    components = _sequence_of_mappings(materialization.get("components") or [])
    if components:
        ref["component_count"] = len(components)
        ref["components"] = [
            {
                key: component.get(key)
                for key in ("kind", "name", "status", "source_digest")
                if component.get(key) not in (None, "", [], {})
            }
            for component in components
        ]
    recovery_plan = _mapping(materialization.get("source_recovery_plan"))
    if recovery_plan:
        ref["source_recovery_plan"] = {
            key: recovery_plan.get(key)
            for key in ("status", "plan_digest", "workspace_lock_digest")
            if recovery_plan.get(key) not in (None, "", [], {})
        }
    return ref or None


def development_source_options(target_scope: Mapping[str, Any]) -> dict[str, Any]:
    target = _mapping(target_scope)
    source = _text(target.get("source")).lower()
    resolved = _development_source_target(target)
    target_type = str(resolved.get("type") or "unknown")
    target_id = _text(resolved.get("id"))
    project_id = _text(resolved.get("project_id")) or None
    if target_type in {"project", "scenario", "skill"} and target_id:
        try:
            from adaos.services.builder.workspace import BuilderWorkspaceService

            actual = BuilderWorkspaceService.from_context().development_source_status(
                kind=target_type,
                artifact_id=target_id,
                project_id=project_id,
            )
            declared_dev = source in {"dev", "local", "source"}
            if actual and (
                not declared_dev
                or _text(actual.get("status")) == "source_available"
                or bool(_text(actual.get("dev_source_path") or actual.get("source_path")))
            ):
                if source:
                    actual = {**actual, "declared_source": source}
                return actual
        except Exception:
            pass
    if source in {"dev", "local", "source"}:
        return {
            "status": "source_available",
            "source": source or "dev",
            "target_type": target_type,
            "target_id": target_id or None,
            "project_id": project_id,
            "options": ["use_existing_dev_source"],
            "default_option": "use_existing_dev_source",
        }
    return _source_materialization_options(
        source=source or "unknown",
        target_type=target_type,
        target_id=target_id or None,
        project_id=project_id,
    )


def _automation_target_from_ticket(ticket: Mapping[str, Any]) -> dict[str, str]:
    target = _mapping(ticket.get("target_scope"))
    meta = _mapping(ticket.get("metadata"))
    owner_area = _text(ticket.get("owner_area") or _owner_area_from_scope(target, meta)).lower()
    component = _text(ticket.get("component_ref") or _component_ref_from_scopes(target, _mapping(ticket.get("origin_scope")), meta))
    if owner_area in {"core", "api", "runtime", "sdk"} or component.startswith(("core:", "api:", "runtime:", "sdk:")):
        raise ValueError("Dev Ticket is owned by core/API/SDK/runtime and cannot be repaired by project Builder automation")
    repair_hints = _bounded_repair_hints(ticket)
    qualified_type = _text(repair_hints.get("target_object_type")).lower()
    qualified_id = _text(repair_hints.get("target_object_id"))
    target_type = _text(target.get("type")).lower().rstrip("s")
    target_id = _text(target.get("id") or target.get("name"))
    if target_type in {"application", "project"} and target_id:
        from adaos.services.builder.workspace import BuilderWorkspaceService

        preferred = f"{qualified_type}:{qualified_id}" if qualified_type in {"skill", "scenario"} and qualified_id else None
        return BuilderWorkspaceService.from_context().resolve_project_repair_target(target_id, preferred_ref=preferred)
    if qualified_type in {"skill", "scenario"} and qualified_id:
        return {"object_type": qualified_type, "object_id": qualified_id}
    if target_type in {"skill", "scenario"} and target_id:
        return {"object_type": target_type, "object_id": target_id}
    for key, object_type in (
        ("scenario_ref", "scenario"),
        ("skill_ref", "skill"),
        ("scenario_id", "scenario"),
        ("skill_id", "skill"),
    ):
        value = target.get(key) or meta.get(key)
        if object_type in {"scenario", "skill"}:
            ref_value = _ref_tail(value, object_type) if str(value or "").startswith(f"{object_type}:") else _text(value)
            if ref_value:
                return {"object_type": object_type, "object_id": ref_value}
    if owner_area in {"scenario", "skill"}:
        candidate = _ref_tail(component, owner_area)
        if candidate:
            return {"object_type": owner_area, "object_id": candidate}
    for key in ("project_ref", "project_id"):
        value = target.get(key) or meta.get(key)
        if _ref_tail(value, "scenario"):
            return {"object_type": "scenario", "object_id": _ref_tail(value, "scenario")}
    raise ValueError("Dev Ticket target must resolve to a skill or scenario before autonomous repair")


def _trusted_publication_gate_lineage(
    ticket: Mapping[str, Any],
    *,
    target: Mapping[str, str],
) -> dict[str, Any]:
    """Project a failed Builder task only from an authoritative gate ticket."""

    metadata = _mapping(ticket.get("metadata"))
    if (
        _text(ticket.get("source")) != "builder_publication_gate"
        or _text(metadata.get("producer")) != "builder_publication_gate"
    ):
        return {}
    object_type = _text(target.get("object_type")).lower().rstrip("s")
    object_id = _text(target.get("object_id"))
    component_ref = f"{object_type}:{object_id}"
    if _text(ticket.get("component_ref")) != component_ref:
        return {}
    task_id = _text(metadata.get("task_id"))
    if not task_id:
        return {}
    matching_evidence = next(
        (
            item
            for item in _sequence_of_mappings(ticket.get("evidence_refs") or [])
            if _text(item.get("task_id")) == task_id
            and _text(item.get("status")).lower() == "failed"
            and _text(item.get("gate")).lower()
            in {"test", "tests", "validation", "consumer_acceptance"}
        ),
        None,
    )
    if matching_evidence is None:
        return {}
    related_ticket_ids = list(
        dict.fromkeys(
            [
                *[
                    _text(item)
                    for item in metadata.get("related_ticket_ids") or []
                    if _text(item)
                ],
                *[
                    _text(item.get("ticket_id"))
                    for item in _sequence_of_mappings(ticket.get("relation_refs") or [])
                    if _text(item.get("ticket_id"))
                ],
            ]
        )
    )
    if not related_ticket_ids:
        return {}
    return {
        "development_ticket_source": "builder_publication_gate",
        "development_ticket_gate_parent_task_id": task_id,
        "development_ticket_gate_parent_ticket_ids": related_ticket_ids,
        "development_ticket_gate": _text(matching_evidence.get("gate")).lower(),
    }


def _development_source_scope(
    ticket: Mapping[str, Any],
    target: Mapping[str, str],
) -> dict[str, Any]:
    scope = _mapping(ticket.get("target_scope"))
    resolved = {
        **scope,
        "type": _text(target.get("object_type")),
        "id": _text(target.get("object_id")),
    }
    metadata = _mapping(ticket.get("metadata"))
    for key in ("project_id", "project_ref"):
        if _text(target.get(key)):
            resolved[key] = target[key]
        if not _text(resolved.get(key)) and _text(metadata.get(key)):
            resolved[key] = metadata[key]
    return resolved


def _project_id_for_materialization(ticket: Mapping[str, Any], development_source: Mapping[str, Any]) -> str | None:
    target = _mapping(ticket.get("target_scope"))
    meta = _mapping(ticket.get("metadata"))
    for source in (development_source, target, meta):
        token = _text(source.get("project_id") or _ref_tail(source.get("project_ref"), "project"))
        if token:
            return token
    return None


def _project_identity_from_ticket(ticket: Mapping[str, Any]) -> dict[str, str]:
    target = _mapping(ticket.get("target_scope"))
    metadata = _mapping(ticket.get("metadata"))
    if _text(target.get("type")).lower() in {"application", "project"} and _text(target.get("id")):
        project_id = _text(target["id"])
        return {"project_ref": f"project:{project_id}", "project_id": project_id}
    for source in (target, metadata):
        project_ref = _text(source.get("project_ref"))
        project_id = _text(source.get("project_id"))
        if project_ref.startswith("project:"):
            project_id = _ref_tail(project_ref, "project") or project_id
        elif project_ref and ":" not in project_ref:
            project_id = project_id or project_ref
            project_ref = f"project:{project_ref}"
        elif project_ref:
            project_ref = ""
        if project_id:
            project_ref = project_ref or f"project:{project_id}"
            return {"project_ref": project_ref, "project_id": project_id}
    return {}


def _project_identity_for_package(tickets: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    identities = [_project_identity_from_ticket(ticket) for ticket in tickets]
    known = [identity for identity in identities if identity]
    if known and len(known) != len(identities):
        raise ValueError("Builder package project scope is incomplete")
    project_refs = {_text(identity.get("project_ref")) for identity in known}
    if len(project_refs) > 1:
        raise ValueError("Builder package tickets must belong to one project")
    return dict(known[0]) if known else {}


def _automation_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    projection = payload.get("automation") if isinstance(payload.get("automation"), Mapping) else payload
    return dict(projection) if isinstance(projection, Mapping) else {}


def _automation_session(payload: Mapping[str, Any]) -> dict[str, Any]:
    session = payload.get("session") if isinstance(payload.get("session"), Mapping) else {}
    return dict(session) if isinstance(session, Mapping) else {}


def _automation_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    session = _automation_session(payload)
    task = session.get("task") if isinstance(session.get("task"), Mapping) else payload.get("task")
    return dict(task) if isinstance(task, Mapping) else {}


def _automation_development_escalations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    session = _automation_session(payload)
    task = _automation_task(payload)
    result = task.get("result") if isinstance(task.get("result"), Mapping) else session.get("last_result")
    result = dict(result) if isinstance(result, Mapping) else {}
    items = result.get("development_escalations")
    if not items:
        return []
    return normalize_development_escalations(
        {"schema": "adaos.development_escalations.v1", "items": items}
    )


def _automation_correlation(payload: Mapping[str, Any]) -> dict[str, Any]:
    projection = _automation_projection(payload)
    session = _automation_session(payload)
    task = _automation_task(payload)
    realize_request = (
        task.get("realize_request")
        if isinstance(task.get("realize_request"), Mapping)
        else {}
    )
    fallback_sources = [
        projection.get("links") if isinstance(projection.get("links"), Mapping) else {},
        session.get("links") if isinstance(session.get("links"), Mapping) else {},
    ]
    task_links = (
        realize_request.get("links")
        if isinstance(realize_request.get("links"), Mapping)
        else {}
    )
    sources = [*fallback_sources, task_links]
    links: dict[str, Any] = {}
    for source in sources:
        links.update(dict(source))
    correlation_sources = [task_links] if task_links else fallback_sources
    ticket_ids: list[str] = []
    for source in correlation_sources:
        ticket_ids.extend(
            _text(item)
            for item in source.get("development_ticket_ids") or []
            if _text(item)
        )
        ticket_id = _text(source.get("development_ticket_id"))
        if ticket_id:
            ticket_ids.append(ticket_id)
    links["development_ticket_ids"] = list(dict.fromkeys(ticket_ids))
    return links


def _automation_matches_work(
    payload: Mapping[str, Any],
    *,
    ticket_ids: Sequence[str],
    repair_id: str,
) -> tuple[bool, dict[str, Any]]:
    correlation = _automation_correlation(payload)
    observed_tickets = {
        _text(item)
        for item in correlation.get("development_ticket_ids") or []
        if _text(item)
    }
    expected_tickets = {_text(item) for item in ticket_ids if _text(item)}
    observed_repair = _text(
        correlation.get("builder_repair_id") or correlation.get("repair_id")
    )
    expected_repair = _text(repair_id)
    matched = bool(
        expected_tickets
        and expected_tickets <= observed_tickets
        and expected_repair
        and observed_repair == expected_repair
    )
    return matched, {
        "expected_ticket_ids": sorted(expected_tickets),
        "observed_ticket_ids": sorted(observed_tickets),
        "expected_repair_id": expected_repair or None,
        "observed_repair_id": observed_repair or None,
    }


def _automation_evidence_refs(
    payload: Mapping[str, Any],
    *,
    repair_id: str,
    allowed_task_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    projection = _automation_projection(payload)
    session = _automation_session(payload)
    task = _automation_task(payload)
    task_id = _text(projection.get("task_id") or session.get("current_task_id") or task.get("task_id"))
    session_id = _text(projection.get("session_id") or session.get("session_id"))
    status = _text(projection.get("status") or session.get("status") or task.get("status"))
    refs: list[dict[str, Any]] = []
    if session_id:
        refs.append({"type": "builder_automation", "id": session_id, "task_id": task_id or None, "status": status or None, "repair_id": repair_id})
    if task_id:
        refs.append({"type": "skill_factory_task", "id": task_id, "status": _text(task.get("status")) or status or None, "repair_id": repair_id})
    if _text(projection.get("change_id")):
        refs.append({"type": "builder_change", "id": _text(projection.get("change_id")), "status": status or None})
    evidence = projection.get("evidence") if isinstance(projection.get("evidence"), Mapping) else {}
    for key, ref_type in (("result_path", "file"), ("events_path", "trace"), ("stderr_path", "trace")):
        path = _text(evidence.get(key))
        if path:
            refs.append(
                {
                    "type": ref_type,
                    "id": _portable_local_reference(path),
                    "source": "builder_automation",
                    "status": status or None,
                }
            )
    result = task.get("result") if isinstance(task.get("result"), Mapping) else session.get("last_result")
    result = dict(result) if isinstance(result, Mapping) else {}
    tests = result.get("tests") if isinstance(result.get("tests"), Mapping) else {}
    if _text(tests.get("report")):
        refs.append(
            {
                "type": "test",
                "id": _portable_local_reference(tests.get("report")),
                "status": _text(tests.get("status")) or "unknown",
            }
        )
    readiness = session.get("completion_readiness") if isinstance(session.get("completion_readiness"), Mapping) else {}
    if readiness:
        refs.append(
            {
                "type": "validation",
                "id": f"builder_completion:{task_id or session_id or repair_id}",
                "status": "passed" if readiness.get("ok") else "failed",
                "repair_id": repair_id,
            }
        )
    usage_receipts = [
        dict(item)
        for item in session.get("codex_usage_history") or []
        if isinstance(item, Mapping)
    ]
    current_usage = session.get("codex_usage_accounting")
    if isinstance(current_usage, Mapping):
        current_receipt = dict(current_usage)
        if task_id:
            current_receipt.setdefault("task_id", task_id)
        usage_receipts.append(current_receipt)
    allowed = {_text(item) for item in allowed_task_ids if _text(item)}
    if allowed:
        usage_receipts = [
            usage
            for usage in usage_receipts
            if _text(usage.get("task_id")) in allowed
        ]
    for usage in usage_receipts:
        usage_id = _text(usage.get("root_event_id") or usage.get("idempotency_key"))
        if usage_id:
            refs.append(
                {
                    "type": "codex_usage",
                    "id": usage_id,
                    "task_id": _text(usage.get("task_id")) or None,
                    "status": usage.get("status"),
                    "accuracy": usage.get("accuracy"),
                    "total_tokens": usage.get("total_tokens"),
                    "repair_id": repair_id,
                }
            )
    return _merge_refs([], refs)


def _repair_automation_task_ids(ticket: Mapping[str, Any], repair_id: str) -> list[str]:
    return sorted(
        {
            _text(ref.get("automation_task_id"))
            for ref in _sequence_of_mappings(ticket.get("builder_refs") or [])
            if _text(ref.get("repair_id")) == _text(repair_id)
            and _text(ref.get("automation_task_id"))
        }
    )


def _automation_has_validation_evidence(payload: Mapping[str, Any]) -> bool:
    session = _automation_session(payload)
    readiness = session.get("completion_readiness") if isinstance(session.get("completion_readiness"), Mapping) else {}
    if readiness:
        return bool(readiness.get("ok"))
    task = _automation_task(payload)
    result = task.get("result") if isinstance(task.get("result"), Mapping) else session.get("last_result")
    result = dict(result) if isinstance(result, Mapping) else {}
    tests = result.get("tests") if isinstance(result.get("tests"), Mapping) else {}
    return _text(task.get("status")) == "completed" and _text(tests.get("status")) == "passed"


_REPAIR_HINT_PROFILES = {
    "project_batch",
    "surgical_ui",
    "surgical_data",
    "resource_crud",
    "subnet_data_integration",
}
_REPAIR_HINT_CONCEPTS = {"ui", "data", "crud", "subnet", "validation"}
_PROMPT_FACT_ENUMS = {
    "surface_kinds": {
        "ui", "page", "modal", "widget", "panel", "view", "scenario",
        "background", "conversation", "resource",
    },
    "operation_kinds": {
        "read", "create", "update", "delete", "command", "transition",
        "subscribe", "handoff", "validate",
    },
    "data_planes": {
        "yjs", "stream", "tool_details", "skill_local", "resource_provider",
        "conversation",
    },
    "effects": {
        "read_only", "local_write", "external_io", "device_control",
        "destructive", "publication", "llm",
    },
}
_PROMPT_FACT_FLAGS = {
    "requires_i18n",
    "requires_access",
    "requires_conversation",
    "requires_lifecycle",
}
_STRUCTURED_EDIT_SCHEMA = "adaos.builder.structured_edit_set.v1"
_STRUCTURED_EDIT_OPS = {
    "replace_text",
    "json_add",
    "json_remove",
    "json_replace",
    "json_move",
}


def _normalize_structured_edits(
    value: Any,
    *,
    target_files: Sequence[str],
    strict: bool = False,
) -> dict[str, Any]:
    try:
        raw = _mapping(value)
        if not raw:
            return {}
        if _text(raw.get("schema")) != _STRUCTURED_EDIT_SCHEMA:
            raise ValueError(f"structured_edits.schema must be {_STRUCTURED_EDIT_SCHEMA}")
        if set(raw) != {"schema", "operations"}:
            raise ValueError("structured_edits contains unsupported fields")
        operations = raw.get("operations")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 24:
            raise ValueError("structured_edits requires 1..24 operations")
        allowed_files = {_text(item).replace("\\", "/").strip("/") for item in target_files}
        normalized: list[dict[str, Any]] = []
        for index, candidate in enumerate(operations):
            if not isinstance(candidate, Mapping):
                raise ValueError(f"structured edit {index} must be an object")
            item = dict(candidate)
            op = _text(item.get("op")).lower()
            if op not in _STRUCTURED_EDIT_OPS:
                raise ValueError(f"unsupported structured edit operation: {op or '<missing>'}")
            path = _text(item.get("path")).replace("\\", "/").strip("/")
            if not path or ":" in path or ".." in path.split("/") or path not in allowed_files:
                raise ValueError(f"structured edit path is outside target_files: {path or '<missing>'}")
            edit: dict[str, Any] = {"op": op, "path": path}
            operation_id = _text(item.get("id"))
            if operation_id:
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", operation_id):
                    raise ValueError(f"structured edit {index} id is invalid")
                edit["id"] = operation_id
            if op == "replace_text":
                allowed_keys = {"id", "op", "path", "old", "new", "expected_count"}
                if set(item) - allowed_keys:
                    raise ValueError(f"structured edit {index} contains unsupported fields")
                old = item.get("old")
                new = item.get("new")
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    raise ValueError("replace_text requires non-empty old and string new")
                expected_count = int(item.get("expected_count") or 1)
                if not 1 <= expected_count <= 20:
                    raise ValueError("replace_text expected_count must be 1..20")
                edit.update({"old": old, "new": new, "expected_count": expected_count})
            else:
                allowed_keys = {"id", "op", "path", "pointer", "from_pointer", "value", "expected"}
                if set(item) - allowed_keys:
                    raise ValueError(f"structured edit {index} contains unsupported fields")
                pointer = item.get("pointer")
                if not isinstance(pointer, str) or not pointer.startswith("/"):
                    raise ValueError(f"structured edit {index} requires an RFC 6901 pointer")
                edit["pointer"] = pointer
                if op in {"json_replace", "json_remove", "json_move"}:
                    if "expected" not in item:
                        raise ValueError(f"{op} requires an expected precondition")
                    edit["expected"] = json.loads(json.dumps(item["expected"], ensure_ascii=False))
                if op in {"json_add", "json_replace"}:
                    if "value" not in item:
                        raise ValueError(f"{op} requires value")
                    edit["value"] = json.loads(json.dumps(item["value"], ensure_ascii=False))
                if op == "json_move":
                    from_pointer = item.get("from_pointer")
                    if not isinstance(from_pointer, str) or not from_pointer.startswith("/"):
                        raise ValueError("json_move requires from_pointer")
                    edit["from_pointer"] = from_pointer
            normalized.append(edit)
        result = {"schema": _STRUCTURED_EDIT_SCHEMA, "operations": normalized}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
            raise ValueError("structured_edits exceeds 64 KiB")
        return result
    except (TypeError, ValueError, json.JSONDecodeError):
        if strict:
            raise
        return {}


def _normalize_contract_closure(
    value: Any,
    *,
    target_files: Sequence[str],
) -> dict[str, Any]:
    raw = _mapping(value)
    if _text(raw.get("kind")) != "skill_public_tool_graph":
        return {}
    allowed = set(target_files)
    required_paths: list[str] = []
    for value in raw.get("required_paths") or []:
        path = _text(value).replace("\\", "/").strip("/")
        if not path or path not in allowed or path in required_paths:
            continue
        required_paths.append(path)
    if not required_paths:
        return {}
    return {
        "kind": "skill_public_tool_graph",
        "required_paths": required_paths,
        "reason": _text(raw.get("reason"))[:500]
        or "qualified repair crosses the public skill tool graph",
    }


def _parse_language_qualification_output(value: Any) -> dict[str, Any]:
    text = _text(value)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("language qualification output must be one JSON object")
    return dict(parsed)


def _language_qualification_usage_receipt(
    response: Mapping[str, Any],
    *,
    request_id: str,
    status: str,
) -> dict[str, Any]:
    usage: Mapping[str, Any] = {}
    for candidate in (
        response.get("usage"),
        _mapping(response.get("response")).get("usage"),
        _mapping(response.get("result")).get("usage"),
        _mapping(response.get("error")).get("usage"),
        _mapping(response.get("_protocol")).get("usage"),
    ):
        if isinstance(candidate, Mapping) and candidate:
            usage = candidate
            break
    input_details = _mapping(usage.get("input_tokens_details"))
    output_details = _mapping(usage.get("output_tokens_details"))

    def count(*values: Any) -> int | None:
        for raw in values:
            if raw is None:
                continue
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                continue
        return None

    input_tokens = count(usage.get("input_tokens"), usage.get("prompt_tokens"))
    output_tokens = count(usage.get("output_tokens"), usage.get("completion_tokens"))
    total_tokens = count(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    response_id = _text(
        response.get("id")
        or response.get("job_id")
        or _mapping(response.get("response")).get("id")
    )
    return {
        "schema": "adaos.builder.language_qualification_usage.v1",
        "request_id": request_id,
        "root_response_id": response_id or None,
        "status": status,
        "accounting_scope": "builder_qualification_llm",
        "source_of_truth": "root_llm_response",
        "input_tokens": input_tokens,
        "cached_input_tokens": count(
            input_details.get("cached_tokens"), usage.get("cached_input_tokens")
        ),
        "output_tokens": output_tokens,
        "reasoning_tokens": count(
            output_details.get("reasoning_tokens"), usage.get("reasoning_tokens")
        ),
        "total_tokens": total_tokens,
        "accuracy": "provider_reported" if usage else "unavailable",
    }


def _language_qualification_messages(
    ticket: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, str]]:
    entries = [
        {
            "workspace_path": _text(item.get("workspace_path")),
            "role": _text(item.get("role")),
            "semantic_refs": [
                _text(value) for value in item.get("semantic_refs") or [] if _text(value)
            ][:12],
        }
        for item in _sequence_of_mappings(
            _mapping(candidate.get("source_index")).get("entries") or []
        )[:24]
    ]
    payload = {
        "ticket": {
            "summary": _text(ticket.get("summary"))[:1000],
            "component_ref": _text(ticket.get("component_ref")) or None,
            "target_scope": {
                key: value
                for key, value in _mapping(ticket.get("target_scope")).items()
                if key
                in {
                    "type",
                    "id",
                    "surface",
                    "modal_id",
                    "skill_id",
                    "scenario_id",
                }
            },
            "clarification_responses": [
                {
                    "question": _text(item.get("question"))[:500],
                    "answer": _text(item.get("answer"))[:2000],
                }
                for item in _sequence_of_mappings(
                    _mapping(ticket.get("metadata")).get("clarification_responses")
                    or []
                )[-2:]
                if _text(item.get("answer"))
            ],
        },
        "candidate_source_refs": entries,
        "prompt_facts_contract": {
            "required_keys": [
                "schema",
                "concepts",
                "surface_kinds",
                "operation_kinds",
                "data_planes",
                "effects",
                "requires_i18n",
                "requires_access",
                "requires_human_decision",
                "requires_conversation",
                "requires_lifecycle",
            ],
            "schema": "adaos.builder.prompt_facts.v1",
            "surface_kinds": [
                "ui",
                "page",
                "modal",
                "widget",
                "panel",
                "view",
                "scenario",
                "background",
                "conversation",
                "resource",
            ],
            "operation_kinds": [
                "read",
                "create",
                "update",
                "delete",
                "command",
                "transition",
                "subscribe",
                "handoff",
                "validate",
            ],
            "data_planes": [
                "yjs",
                "stream",
                "tool_details",
                "skill_local",
                "resource_provider",
                "conversation",
            ],
            "effects": [
                "read_only",
                "local_write",
                "external_io",
                "device_control",
                "destructive",
                "publication",
                "llm",
                "human_decision",
            ],
            "boolean_keys": [
                "requires_i18n",
                "requires_access",
                "requires_human_decision",
                "requires_conversation",
                "requires_lifecycle",
            ],
        },
        "output_example": {
            "schema": "adaos.builder.language_qualification_proposal.v1",
            "concepts": ["ui"],
            "prompt_facts": {
                "schema": "adaos.builder.prompt_facts.v1",
                "concepts": ["ui"],
                "surface_kinds": ["ui"],
                "operation_kinds": ["read"],
                "data_planes": [],
                "effects": ["read_only"],
                "requires_i18n": False,
                "requires_access": False,
                "requires_human_decision": False,
                "requires_conversation": False,
                "requires_lifecycle": False,
            },
            "candidate_paths": [entries[0]["workspace_path"]] if entries else [],
            "confidence": 0.5,
            "clarification_question": "One bounded question, or null",
            "rationale": "Short classification rationale",
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "Classify one AdaOS Dev Ticket without designing or implementing a solution. "
                "Select at most four exact workspace_path values only from candidate_source_refs. "
                "Use one or more concepts from ui,data,crud,subnet,validation. "
                "Return one JSON object with schema, concepts, prompt_facts, candidate_paths, confidence "
                "from 0 to 1, clarification_question, and a short rationale. prompt_facts must use only "
                "the exact keys and enums in prompt_facts_contract; do not use shorter aliases such as "
                "surface, operations, i18n, access, conversation, or lifecycle. Its schema field must be "
                "adaos.builder.prompt_facts.v1, its concepts/surface_kinds/operation_kinds/data_planes/effects "
                "fields must be arrays, and all requires_* fields must be booleans. "
                "Follow output_example's nesting exactly: the top-level schema is the language qualification "
                "proposal schema, while the prompt-facts schema and semantic arrays stay inside prompt_facts. "
                "output_example demonstrates shape only; select paths and values from the actual ticket. "
                "When the admitted source or public contract is insufficient, ambiguous, conflicting, or "
                "needlessly expensive, add up to four development_feedback observations. Omit that optional "
                "array for a routine qualification with no product feedback. Feedback is advisory and cannot "
                "expand source or execution authority. "
                "If the target is ambiguous, use low confidence and ask one bounded clarification question. "
                "Treat clarification_responses as authoritative user input and do not ask again for information "
                "already answered there. Do not force a choice between requirements that the ticket or answers "
                "state conjunctively; keep all acceptance points when they remain inside the same bounded "
                "component repair. Never invent a path or SDK capability."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _language_qualification_request_id(
    ticket: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> str:
    source_index = _mapping(candidate.get("source_index"))
    digest = hashlib.sha256(
        json.dumps(
            {
                "ticket_id": _text(ticket.get("ticket_id")),
                "ticket_revision": int(ticket.get("revision") or 1),
                "summary": _text(ticket.get("summary")),
                "source_index": source_index,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"builder.language_qualification.{digest[:40]}"


def _language_qualification_candidate_projection(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: _clone(candidate.get(key))
        for key in (
            "schema",
            "status",
            "ready",
            "confidence",
            "recommended_next",
            "reason",
            "clarification_question",
            "concepts",
            "candidate_files",
            "invalid_candidate_paths",
            "builder_repair",
        )
        if candidate.get(key) not in (None, "", [], {})
    }


def _bounded_repair_hints(ticket: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(_mapping(ticket.get("metadata")).get("builder_repair"))
    if not raw:
        return {}
    profile = _text(raw.get("profile")).lower()
    if profile not in _REPAIR_HINT_PROFILES:
        profile = ""
    concepts = list(
        dict.fromkeys(
            _text(value).lower()
            for value in raw.get("concepts") or []
            if _text(value).lower() in _REPAIR_HINT_CONCEPTS
        )
    )
    raw_prompt_facts = _mapping(raw.get("prompt_facts"))
    prompt_facts: dict[str, Any] = {
        "schema": "adaos.builder.prompt_facts.v1",
        "concepts": list(
            dict.fromkeys(
                _text(value).lower()[:80]
                for value in raw_prompt_facts.get("concepts") or concepts
                if _text(value)
            )
        )[:12],
    }
    for key, allowed in _PROMPT_FACT_ENUMS.items():
        prompt_facts[key] = list(
            dict.fromkeys(
                _text(value).lower()
                for value in raw_prompt_facts.get(key) or []
                if _text(value).lower() in allowed
            )
        )
    for key in _PROMPT_FACT_FLAGS:
        prompt_facts[key] = raw_prompt_facts.get(key) is True
    target_files: list[str] = []
    for value in raw.get("target_files") or []:
        path = _text(value).replace("\\", "/").strip("/")
        if not path or ":" in path or ".." in path.split("/"):
            continue
        if path not in target_files:
            target_files.append(path)
        if len(target_files) >= 12:
            break
    target_refs = [_text(value)[:300] for value in raw.get("target_refs") or [] if _text(value)][:20]
    acceptance_checks = [
        _text(value)[:500]
        for value in raw.get("acceptance_checks") or []
        if _text(value)
    ][:12]
    target_object_type = _text(raw.get("target_object_type")).lower()
    target_object_id = _text(raw.get("target_object_id"))
    if target_object_type not in {"skill", "scenario"}:
        target_object_type = ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", target_object_id):
        target_object_id = ""
    try:
        max_changed_files = max(1, min(12, int(raw.get("max_changed_files") or len(target_files) or 1)))
    except (TypeError, ValueError):
        max_changed_files = max(1, len(target_files))
    hints: dict[str, Any] = {
        "profile": profile or None,
        "concepts": concepts,
        "prompt_facts": prompt_facts,
        "change_summary": _text(raw.get("change_summary"))[:1000] or None,
        "target_files": target_files,
        "target_refs": target_refs,
        "acceptance_checks": acceptance_checks,
        "max_changed_files": max_changed_files,
        "requires_root_mcp": raw.get("requires_root_mcp") is True,
        "target_object_type": target_object_type or None,
        "target_object_id": target_object_id or None,
    }
    if raw.get("validation_only") is True:
        hints["validation_only"] = True
    source_preconditions: list[dict[str, Any]] = []
    allowed_files = set(target_files)
    for value in raw.get("source_preconditions") or []:
        if not isinstance(value, Mapping):
            continue
        path = _text(value.get("path")).replace("\\", "/").strip("/")
        digest = _text(value.get("sha256")).lower()
        if path not in allowed_files or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            continue
        try:
            size = max(0, int(value.get("size") or 0))
        except (TypeError, ValueError):
            continue
        source_preconditions.append({"path": path, "sha256": digest, "size": size})
    if source_preconditions:
        hints["source_preconditions"] = source_preconditions[:12]
    contract_closure = _normalize_contract_closure(
        raw.get("contract_closure"),
        target_files=target_files,
    )
    if contract_closure:
        hints["contract_closure"] = contract_closure
    structured_edits = _normalize_structured_edits(
        raw.get("structured_edits"),
        target_files=target_files,
    )
    if structured_edits:
        hints["structured_edits"] = structured_edits
    return {key: value for key, value in hints.items() if value not in (None, "", [])}


def _autonomous_repair_qualification(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Admit a bounded repair before any Builder or model work is created."""

    hints = _bounded_repair_hints(ticket)
    structured = _mapping(hints.get("structured_edits"))
    validation_only = hints.get("validation_only") is True
    contract_closure = _mapping(hints.get("contract_closure"))
    missing: list[str] = []
    if not _text(hints.get("profile")):
        missing.append("profile")
    if not hints.get("target_files"):
        missing.append("target_files")
    if not hints.get("target_refs") and not structured:
        missing.append("target_refs")
    if not hints.get("acceptance_checks"):
        missing.append("acceptance_checks")
    if validation_only:
        target_files = set(hints.get("target_files") or [])
        precondition_files = {
            _text(item.get("path"))
            for item in hints.get("source_preconditions") or []
            if isinstance(item, Mapping)
        }
        if not target_files or precondition_files != target_files:
            missing.append("validation_source_preconditions")
        if hints.get("requires_root_mcp") is True:
            missing.append("validation_only_root_mcp")
    if contract_closure:
        required_paths = set(contract_closure.get("required_paths") or [])
        target_files = set(hints.get("target_files") or [])
        precondition_files = {
            _text(item.get("path"))
            for item in hints.get("source_preconditions") or []
            if isinstance(item, Mapping)
        }
        if not required_paths or not required_paths.issubset(target_files):
            missing.append("contract_closure_paths")
        if not required_paths.issubset(precondition_files):
            missing.append("contract_closure_source_preconditions")
    route = (
        "validation_only"
        if validation_only and not missing
        else "structured_edits"
        if structured
        else "bounded_patch_agent"
        if not missing
        else "qualification"
    )
    ready = not missing
    qualification = {
        "schema": "adaos.builder.autonomous_repair_qualification.v1",
        "status": "ready" if ready else "qualification_required",
        "ready": ready,
        "execution_route": route,
        "model_call_expected": ready and route not in {"structured_edits", "validation_only"},
        "expected_model_tokens": 0 if route in {"structured_edits", "validation_only"} else None,
        "missing_fields": missing,
        "profile": hints.get("profile"),
        "concepts": list(hints.get("concepts") or []),
        "prompt_facts": _mapping(hints.get("prompt_facts")),
        "target_files": list(hints.get("target_files") or []),
        "target_refs": list(hints.get("target_refs") or []),
        "acceptance_checks": list(hints.get("acceptance_checks") or []),
        "source_preconditions": list(hints.get("source_preconditions") or []),
        "requires_root_mcp": hints.get("requires_root_mcp") is True,
        "validation_only": validation_only,
        "structured_operation_count": len(structured.get("operations") or []),
        "contract_closure": contract_closure or None,
        "reason": (
            "qualified exact repair envelope is ready"
            if ready
            else "Builder must qualify exact source and acceptance before autonomous model spend"
        ),
    }
    qualification["estimated_budget"] = (
        _autonomous_repair_budget(qualification, None) if ready else None
    )
    return qualification


def _builder_package_execution_route(
    *,
    target: Mapping[str, Any],
    target_files: Sequence[str],
    qualifications: Sequence[Mapping[str, Any]],
    repair_hints: Mapping[str, Any] | None = None,
) -> str:
    """Select the cheapest safe Builder stage for one qualified package."""

    hints = _mapping(repair_hints)
    declared = _text(hints.get("execution_route"))
    if declared:
        return declared
    routes = {
        _text(item.get("execution_route"))
        for item in qualifications
        if _text(item.get("execution_route"))
    }
    object_type = _text(target.get("object_type")).lower().rstrip("s")
    object_id = _text(target.get("object_id"))
    normalized_files = [
        _text(path).replace("\\", "/").strip("/")
        for path in target_files
        if _text(path)
    ]
    scenario_root = f"scenarios/{object_id}/" if object_id else ""
    scenario_webui = f"{scenario_root}webui.json" if scenario_root else ""
    scenario_semantic = (
        f"{scenario_root}semantic.webui.json" if scenario_root else ""
    )
    prototype_owned = bool(
        object_type == "scenario"
        and scenario_root
        and normalized_files
        and all(path.startswith(scenario_root) for path in normalized_files)
        and bool({scenario_webui, scenario_semantic} & set(normalized_files))
        and any("ui" in set(item.get("concepts") or []) for item in qualifications)
    )
    prototype_safe = bool(
        prototype_owned
        and not hints.get("structured_edits")
        and not hints.get("contract_closure")
        and hints.get("requires_root_mcp") is not True
        and all(item.get("requires_root_mcp") is not True for item in qualifications)
    )
    if prototype_safe:
        return "prototype_first"
    if routes == {"structured_edits"}:
        return "structured_edits"
    if routes == {"validation_only"}:
        return "validation_only"
    return "bounded_patch_agent"


def _prototype_ready_for_package(
    workflow_state: str,
    prototype_link: Mapping[str, Any] | None,
    *,
    accepted_revision: str | None = None,
) -> bool:
    """Return true only for an accepted Prototype linked to this package."""

    link = _mapping(prototype_link)
    if _text(workflow_state) != "automation_ready" or not link:
        return False
    revision = _text(accepted_revision)
    return not revision or _text(link.get("revision")) == revision


def _automation_session_matches_prototype(
    session: Mapping[str, Any],
    accepted_revision: str | None,
) -> bool:
    """Return whether a failed session can safely resume the accepted UI head."""

    expected = _text(accepted_revision)
    if not expected:
        return True
    acceptance = _mapping(session.get("prototype_acceptance"))
    accepted = _text(acceptance.get("revision"))
    source = _text(session.get("source_prototype_version"))
    if source.lower().startswith("ui "):
        source = source[3:].strip()
    return accepted == expected and source == expected


def _builder_skill_manager() -> Any:
    from adaos.adapters.db import SqliteSkillRegistry
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill.manager import SkillManager

    ctx = get_ctx()
    return SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )


def _builder_prototype_meta(
    *,
    object_id: str,
    project_id: str,
    package_id: str,
    repair_id: str,
    ticket_ids: Sequence[str],
    webspace_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    topic_id = f"prompt-project:scenario:{object_id}"
    return {
        "action_source": "api_tool_call",
        # Prototype generation currently executes inside a one-shot skill
        # worker.  A daemon thread started by that worker is not a durable job
        # owner: the process may exit after returning the submit receipt while
        # Root LLM is still generating, leaving the package permanently
        # ``llm_pending``.  Keep package-driven Prototype work synchronous
        # until its observer is owned by a durable Core worker.
        "builder_llm_async": False,
        "request_origin_id": "development_tickets",
        "request_origin_label": "Dev Tickets",
        "message_id": f"m.{package_id}.prototype.request",
        "conversation_id": conversation_id,
        "webspace_id": webspace_id,
        "source_webspace_id": webspace_id,
        "thread_id": topic_id,
        "topic_id": topic_id,
        "force_builder_project_topic": True,
        "builder_topic": {
            "schema": "adaos.conversation.topic_ref.v1",
            "thread_id": topic_id,
            "topic_id": topic_id,
            "topic_kind": "builder_scenario",
            "scenario_id": object_id,
            "project_id": project_id or object_id,
            "webspace_id": webspace_id,
            "source_webspace_id": webspace_id,
        },
        "development_ticket_ids": list(ticket_ids),
        "builder_package_id": package_id,
        "builder_repair_id": repair_id,
    }


def _default_builder_prototype_submitter(
    *,
    instruction: str,
    object_id: str,
    project_id: str,
    package_id: str,
    repair_id: str,
    ticket_ids: Sequence[str],
    webspace_id: str,
    conversation_id: str,
) -> Mapping[str, Any]:
    meta = _builder_prototype_meta(
        object_id=object_id,
        project_id=project_id,
        package_id=package_id,
        repair_id=repair_id,
        ticket_ids=ticket_ids,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
    )
    def reconcile_workflow_for_revision(
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Keep governed and compatibility workflow heads aligned around ticket revisions."""

        from adaos.sdk.builder import workflow as builder_workflow

        try:
            state = builder_workflow.get_state("scenario", object_id)
        except AttributeError as exc:
            # Lightweight SDK/unit-test contexts may intentionally omit the
            # Builder workspace paths. The production bootstrap always owns
            # them; do not turn a submitter contract test into workflow I/O.
            if "dev_skills_dir" in str(exc) or "dev_scenarios_dir" in str(exc):
                return None
            raise
        active_phase = _text(state.get("active_phase"))
        automation_status = _text(_mapping(state.get("automation")).get("status"))
        governed_state = _text(_mapping(state.get("governed")).get("state"))
        if (
            active_phase == "automation"
            and automation_status == "failed"
            and governed_state == "prototype_editing"
        ):
            transitioned = builder_workflow.transition(
                "scenario",
                object_id,
                "prototype_acceptance_invalidated",
                actor="builder.prototype_intake",
                reason="Dev Ticket feedback requalified failed Automation as Prototype work.",
                metadata={
                    "reason": "automation_development_feedback_requalification",
                    "builder_package_id": package_id,
                    "builder_repair_id": repair_id,
                    "development_ticket_ids": list(ticket_ids),
                },
                expected_generation=int(state.get("generation") or 0),
            )
            state = _mapping(transitioned.get("workflow"))

        revision_result = _mapping(result)
        revision_ref = _mapping(revision_result.get("ui_revision"))
        revision = _text(revision_ref.get("revision"))
        if not revision or _text(_mapping(state.get("prototype")).get("head_revision")) == revision:
            return state
        if _text(state.get("active_phase")) != "prototype":
            raise RuntimeError(
                "Builder produced a Prototype revision while the workflow compatibility head remained outside Prototype"
            )
        revision_document: dict[str, Any] = {}
        revision_path = _text(revision_ref.get("path"))
        if revision_path:
            path = Path(revision_path)
            if path.is_file():
                revision_document = _mapping(json.loads(path.read_text(encoding="utf-8")))
        patch = _mapping(revision_document.get("patch"))
        prototype_resource = _mapping(patch.get("prototype_resource"))
        recorded = builder_workflow.transition(
            "scenario",
            object_id,
            "prototype_revision_recorded",
            actor="builder.prototype",
            metadata={
                "object_type": "scenario",
                "revision": revision,
                "change_id": _text(revision_document.get("change_id") or patch.get("change_id")) or None,
                "webui_digest": _text(prototype_resource.get("webui_digest")) or None,
                "prototype_acceptance_required": True,
                "builder_package_id": package_id,
                "builder_repair_id": repair_id,
                "development_ticket_ids": list(ticket_ids),
            },
            expected_generation=int(state.get("generation") or 0),
        )
        return _mapping(recorded.get("workflow"))

    # Package intake may have already moved the governed Change back to
    # Prototype after Automation reported a contract conflict. Align the
    # compatibility projection before the Builder writes a new revision.
    reconcile_workflow_for_revision()
    result = _builder_skill_manager().run_tool(
        "builder_skill",
        "update_current_scenario",
        {
            "instruction": instruction,
            "scenario_id": object_id,
            "webspace_id": webspace_id,
            "auto_apply": True,
            "conversation_context": {
                "schema": "adaos.context.packet.v1",
                "conversation_id": conversation_id,
                "thread_id": meta["thread_id"],
                "topic_id": meta["topic_id"],
                "messages": [],
                "segments": [],
                "memory": [],
                "diagnostics": {
                    "fallbacks": [
                        *(f"development_ticket:{ticket_id}" for ticket_id in ticket_ids),
                        f"builder_package:{package_id}",
                        f"builder_repair:{repair_id}",
                    ]
                },
            },
            "_meta": meta,
        },
        # The Root job itself is durable, but the one-shot Builder worker must
        # stay alive through validation, bounded repair and atomic artifact
        # persistence.  This bound matches the Builder job ceiling rather
        # than the short submit-only timeout used by interactive async calls.
        timeout=900,
    )
    if isinstance(result, Mapping) and result.get("ok") is True:
        reconcile_workflow_for_revision(result)
    return result


def _default_builder_prototype_status_reader(
    *,
    session_id: str,
    object_id: str,
    project_id: str,
    package_id: str,
    repair_id: str,
    ticket_ids: Sequence[str],
    webspace_id: str,
    conversation_id: str,
) -> Mapping[str, Any]:
    meta = _builder_prototype_meta(
        object_id=object_id,
        project_id=project_id,
        package_id=package_id,
        repair_id=repair_id,
        ticket_ids=ticket_ids,
        webspace_id=webspace_id,
        conversation_id=conversation_id,
    )
    return _builder_skill_manager().run_tool(
        "builder_skill",
        "get_session",
        {
            "session_id": session_id,
            "webspace_id": webspace_id,
            "_meta": meta,
        },
        timeout=30,
    )


def _autonomous_repair_budget(
    qualification: Mapping[str, Any],
    requested: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(requested, Mapping):
        budget = dict(requested)
        budget.setdefault("token_budget_metric", "fresh_plus_output")
        budget.setdefault("source", "development_ticket.requested")
        return budget
    profile = _text(qualification.get("profile"))
    route = _text(qualification.get("execution_route"))
    if route in {"structured_edits", "validation_only"}:
        max_tokens, max_wall_seconds = 8_000, 600
        source = f"development_ticket.{route}"
    elif profile in {"surgical_ui", "surgical_data", "resource_crud"}:
        max_tokens, max_wall_seconds = 24_000, 900
        source = f"development_ticket.{profile}"
    elif profile == "subnet_data_integration":
        max_tokens, max_wall_seconds = 40_000, 1200
        source = "development_ticket.subnet_data_integration"
    else:
        return dict(DEFAULT_AUTONOMOUS_REPAIR_BUDGET)
    return {
        "schema": "adaos.builder.execution_budget.v1",
        "source": source,
        "max_tokens": max_tokens,
        "token_budget_metric": "fresh_plus_output",
        "max_billable_tokens": execution_billable_token_limit(
            {
                "max_tokens": max_tokens,
                "token_budget_metric": "fresh_plus_output",
            }
        ),
        "max_wall_seconds": max_wall_seconds,
    }


def _validate_repair_source_preconditions(
    qualification: Mapping[str, Any],
    *,
    development_source: Mapping[str, Any],
    target: Mapping[str, str],
) -> dict[str, Any]:
    preconditions = _sequence_of_mappings(qualification.get("source_preconditions") or [])
    if not preconditions:
        return {
            "schema": "adaos.builder.source_precondition_validation.v1",
            "status": "not_applicable",
            "ok": True,
            "checks": [],
        }
    source_root_text = _text(
        development_source.get("dev_source_path") or development_source.get("source_path")
    )
    if not source_root_text:
        return {
            "schema": "adaos.builder.source_precondition_validation.v1",
            "status": "unavailable",
            "ok": False,
            "reason": "qualified source preconditions require an authoritative DEV source path",
            "checks": [],
        }
    source_root = Path(source_root_text).expanduser().resolve()
    collection = "skills" if _text(target.get("object_type")) == "skill" else "scenarios"
    prefix = f"{collection}/{_text(target.get('object_id'))}/"
    workspace_root = source_root
    if (
        source_root.name == _text(target.get("object_id"))
        and source_root.parent.name == collection
    ):
        # A DEV source locator addresses the primary artifact directory.  A
        # governed package may also name exact companion-artifact files from
        # the same DEV workspace (for example scenario + owned skill).  Resolve
        # those paths from the shared workspace root without admitting globs or
        # weakening the digest precondition.
        workspace_root = source_root.parent.parent.resolve()
    checks: list[dict[str, Any]] = []
    for item in preconditions:
        workspace_path = _text(item.get("path")).replace("\\", "/").strip("/")
        relative_path = workspace_path[len(prefix):] if workspace_path.startswith(prefix) else workspace_path
        candidates = [source_root / relative_path, workspace_root / workspace_path]
        source_path = next((path.resolve() for path in candidates if path.is_file()), None)
        if source_path is None or not (
            source_path == workspace_root or workspace_root in source_path.parents
        ):
            checks.append({"path": workspace_path, "status": "missing"})
            continue
        raw = source_path.read_bytes()
        actual_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        expected_digest = _text(item.get("sha256")).lower()
        expected_size = int(item.get("size") or 0)
        matched = actual_digest == expected_digest and len(raw) == expected_size
        checks.append(
            {
                "path": workspace_path,
                "status": "matched" if matched else "changed",
                "expected_sha256": expected_digest,
                "actual_sha256": actual_digest,
                "expected_size": expected_size,
                "actual_size": len(raw),
            }
        )
    ok = len(checks) == len(preconditions) and all(item.get("status") == "matched" for item in checks)
    return {
        "schema": "adaos.builder.source_precondition_validation.v1",
        "status": "passed" if ok else "source_changed",
        "ok": ok,
        "reason": None if ok else "qualified DEV source changed; repair must be requalified",
        "checks": checks,
    }


def _autonomous_repair_brief(ticket: Mapping[str, Any], repair: Mapping[str, Any], *, target: Mapping[str, str]) -> str:
    policy = _mapping(ticket.get("policy"))
    policy.setdefault("publication_required", True)
    input_refs: list[dict[str, Any]] = []
    for raw in reversed(
        [
            *_sequence_of_mappings(ticket.get("artifact_refs") or []),
            *_sequence_of_mappings(ticket.get("evidence_refs") or []),
        ]
    ):
        ref_type = _text(raw.get("type"))
        ref_status = _text(raw.get("status")).lower()
        if ref_type not in {"screenshot", "runtime_guard", "trace", "file", "validation"}:
            continue
        if ref_type in {"trace", "file", "validation"} and ref_status in {
            "passed",
            "completed",
            "reported",
        }:
            continue
        compact = {
            key: raw.get(key)
            for key in (
                "type",
                "id",
                "path",
                "digest",
                "status",
                "code",
                "message",
                "receiver",
                "topic",
            )
            if raw.get(key) not in (None, "", [], {})
        }
        if compact and compact not in input_refs:
            input_refs.append(compact)
        if len(input_refs) >= 8:
            break
    relation_refs = [
        {
            key: ref.get(key)
            for key in ("relation", "ticket_id", "target_ref", "status")
            if ref.get(key) not in (None, "", [], {})
        }
        for ref in _sequence_of_mappings(ticket.get("relation_refs") or [])[:12]
    ]
    payload = {
        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
        "execution_mode": "surgical_dev_ticket_repair",
        "ticket_id": _text(ticket.get("ticket_id")),
        "ticket_revision": ticket.get("revision"),
        "repair_id": _text(repair.get("repair_id")),
        "kind": _text(ticket.get("kind")),
        "summary": _text(ticket.get("summary")),
        "target": target,
        "target_scope": _mapping(ticket.get("target_scope")),
        "owner_area": _text(ticket.get("owner_area")),
        "component_ref": _text(ticket.get("component_ref")),
        "policy": policy,
        "relation_refs": [ref for ref in relation_refs if ref],
        "input_evidence_refs": list(reversed(input_refs)),
        "repair_hints": _bounded_repair_hints(ticket),
        "diff_policy": {
            "scope": "minimal",
            "allowed": [
                "Small, directly relevant source changes required by this ticket.",
                "Focused tests or validation fixtures that prove the repair.",
                "Small additive manifest changes when the ticket explicitly targets declarative UI/resource metadata.",
            ],
            "blocked_without_explicit_admission": [
                "Broad rewrites, regeneration, minification or collapse of declarative manifests.",
                "Large deletions in scenario.json, webui.json, scenario.yaml or skill.yaml.",
                "Drive-by refactors unrelated to the ticket summary.",
            ],
        },
        "guardrails": [
            "Use only public AdaOS SDK/API surfaces available to the project.",
            "Do not modify AdaOS core/runtime from project Builder automation.",
            "If the defect requires core/API/SDK changes, create or link a core capability Dev Ticket instead of patching a symptom.",
            "Keep the diff surgical: touch only files needed to satisfy this ticket and its evidence.",
            "Do not collapse, regenerate, minify or delete large declarative manifests; preserve existing structure unless the ticket explicitly requires a manifest rewrite.",
            "If a small project-scope repair is not possible, stop with a blocker explanation and propose/link the required core/API/SDK Dev Ticket.",
        ],
        "acceptance": [
            "The ticket summary is satisfied.",
            "Relevant validation passes and is recorded as evidence.",
            "Unrelated project behavior remains valid.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _autonomous_package_brief(
    tickets: Sequence[Mapping[str, Any]],
    repair: Mapping[str, Any],
    *,
    target: Mapping[str, str],
) -> str:
    context = _mapping(repair.get("context"))
    package = _mapping(context.get("package"))
    ticket_items = []
    for ticket in tickets:
        ticket_id = _text(ticket.get("ticket_id"))
        artifact_refs = [
            {
                key: ref.get(key)
                for key in (
                    "type",
                    "artifact_id",
                    "uri",
                    "filename",
                    "content_type",
                    "sha256",
                )
                if ref.get(key) not in (None, "", [], {})
            }
            for ref in _sequence_of_mappings(ticket.get("artifact_refs") or [])[:4]
        ]
        ticket_items.append({
            "ticket_id": _text(ticket.get("ticket_id")),
            "revision": int(ticket.get("revision") or 1),
            "kind": _text(ticket.get("kind")),
            "summary": _text(ticket.get("summary")),
            "component_ref": _text(ticket.get("component_ref")) or None,
            "acceptance_checks": _bounded_repair_hints(ticket).get("acceptance_checks") or [],
            "artifact_refs": [ref for ref in artifact_refs if ref],
            "detail_source": {
                "server": "adaos_task_root",
                "tool": "get_dev_ticket",
                "arguments": {"ticket_id": ticket_id},
            },
        })
    repair_hints = _mapping(package.get("repair_hints"))
    if _text(repair_hints.get("execution_route")) == "prototype_first":
        repair_hints = {
            **repair_hints,
            # File count controls cost and review size; it is not authority.
            # Accepted Prototype realization may deterministically expand into
            # several owned resource declarations.  Preserve that useful work
            # and report the overrun instead of asking the model to refuse it.
            "max_changed_files_policy": "advisory_optimization_target",
        }
    payload = {
        "schema": "adaos.dev_ticket.autonomous_repair_package_brief.v1",
        "execution_mode": "surgical_dev_ticket_repair",
        "package_id": _text(repair.get("package_id") or package.get("package_id")),
        "ticket_id": ticket_items[0]["ticket_id"] if ticket_items else None,
        "ticket_ids": [item["ticket_id"] for item in ticket_items],
        "repair_id": _text(repair.get("repair_id")),
        "summary": _text(repair.get("summary")),
        "target": target,
        "issues": ticket_items,
        "policy": {
            "publication_required": True,
            "one_release_for_package": True,
            "individual_ticket_evidence_required": True,
            "stop_on_core_or_sdk_boundary": True,
            "functional_beta_deferred_capabilities": {
                "allowed": True,
                "requirements": [
                    "The unavailable production effect is represented by a typed capability blocker.",
                    "No unavailable production effect is claimed or invoked.",
                    "A high-confidence missing capability is promoted to a linked Core Dev Ticket.",
                    "The remaining local/read-only beta behavior passes deterministic validation.",
                ],
            },
        },
        "repair_hints": repair_hints,
        "guardrails": [
            "Use only public AdaOS SDK/API surfaces available to the project.",
            "Do not modify AdaOS core/runtime from project Builder automation.",
            "Implement all package issues in one bounded project change and one release.",
            "Do not close an issue that lacks its own validation evidence.",
            "Stop only the unavailable effect and create a linked Core capability request. For functional beta, retain the validated candidate with typed unavailability instead of blocking unrelated local/read-only behavior.",
            "Treat numeric file/token/cost budgets as optimization targets: report overruns, but do not discard an otherwise authorized useful candidate.",
        ],
        "acceptance": [
            "Every included ticket has a satisfied acceptance check or an explicit blocker.",
            "Focused validation passes for the combined change.",
            "Changed files stay inside the exact package envelope.",
            "The candidate is exposed through the workspace runtime trial overlay.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _merge_refs(current: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*current, *incoming]:
        item = dict(raw)
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[-100:]


def _with_pending_action_ref_status(
    refs: Sequence[Mapping[str, Any]],
    *,
    action_id: str | None,
    status: str,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    token = _text(action_id)
    updated: list[dict[str, Any]] = []
    for raw in refs:
        item = dict(raw)
        if token and _text(item.get("id")) == token:
            item["status"] = _text(status)
            item["resolved_at"] = _now()
            if _text(reason):
                item["resolution_reason"] = _text(reason)[:1000]
        updated.append(item)
    return updated[-100:]


_HOST_ABSOLUTE_PATH_RE = re.compile(r"^(?:[a-zA-Z]:/|/)")
_STRUCTURED_PATH_KEYS = {
    "id",
    "path",
    "ref",
    "report",
    "result_path",
    "events_path",
    "stderr_path",
    "local_run_dir",
    "validated_run_dir",
}


def _is_host_absolute_reference(value: Any) -> bool:
    token = _text(value).replace("\\", "/")
    if token.startswith(("/api/", "/hub/")):
        return False
    return bool(_HOST_ABSOLUTE_PATH_RE.match(token))


def _portable_local_reference(value: Any) -> str:
    token = _text(value).replace("\\", "/")
    if not _is_host_absolute_reference(token):
        return token
    marker = "/skill_factory/local_runs/"
    marker_at = token.lower().find(marker)
    if marker_at >= 0:
        tail = token[marker_at + len(marker):].strip("/")
        parts = tail.split("/") if tail else []
        task_id = parts[0] if parts else "unknown"
        filename = parts[-1] if parts else "artifact"
        if filename in {"result.json", "test_report.json", "changed_files.txt", "provenance.json"}:
            return f".adaos/tasks/{task_id}/{filename}"
        if filename == "codex-live.jsonl":
            return f"builder-diagnostic:{task_id}:events"
        if filename.endswith(".stderr.log") or filename == "stderr.log":
            return f"builder-diagnostic:{task_id}:stderr"
        return f"skill-factory-run:{task_id}/{filename}"
    filename = token.rstrip("/").rsplit("/", 1)[-1] or "artifact"
    return f"node-local:{filename}"


def _portable_structured_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        portable: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if isinstance(raw_value, str) and key in _STRUCTURED_PATH_KEYS:
                normalized = raw_value.replace("\\", "/")
                if _is_host_absolute_reference(normalized):
                    logical = _portable_local_reference(raw_value)
                    if key == "path":
                        portable.setdefault("logical_path", logical)
                        continue
                    replacement_key = {
                        "result_path": "result_ref",
                        "events_path": "events_ref",
                        "stderr_path": "stderr_ref",
                        "local_run_dir": "local_run_ref",
                        "validated_run_dir": "validated_run_ref",
                    }.get(key, key)
                    portable[replacement_key] = logical
                    continue
            portable[key] = _portable_structured_paths(raw_value)
        return portable
    if isinstance(value, list):
        return [_portable_structured_paths(item) for item in value]
    return value


def _merge_ids(current: Sequence[Any], incoming: Sequence[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in [*current, *incoming]:
        item = _text(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[-100:]


def _event_payload(evt: Any) -> dict[str, Any]:
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(evt, Mapping):
        return dict(evt)
    return {}


def _collect_scope_tokens(tokens: set[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        scope_type = _text(value.get("type") or value.get("kind"))
        scope_id = _text(value.get("id") or value.get("name"))
        if scope_id:
            tokens.add(scope_id)
            if scope_type:
                tokens.add(f"{scope_type}:{scope_id}")
        for key in (
            "ref",
            "canonical_ref",
            "target_ref",
            "project_ref",
            "scenario_ref",
            "skill_ref",
            "modal_ref",
            "component_ref",
            "project_id",
            "scenario_id",
            "skill_id",
            "modal_id",
            "component_id",
            "surface",
        ):
            _collect_scope_tokens(tokens, value.get(key))
        for key in (
            "component_refs",
            "components",
            "target_refs",
            "affected_refs",
            "scope_refs",
            "related_refs",
        ):
            _collect_scope_tokens(tokens, value.get(key))
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_scope_tokens(tokens, item)
        return
    token = _text(value)
    if not token or token == ":" or "$" in token:
        return
    tokens.add(token)
    if ":" in token:
        tail = token.rsplit(":", 1)[-1].strip()
        if tail:
            tokens.add(tail)


def _ticket_scope_tokens(ticket: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    _collect_scope_tokens(tokens, ticket.get("owner_area"))
    _collect_scope_tokens(tokens, ticket.get("component_ref"))
    _collect_scope_tokens(tokens, ticket.get("web_component"))
    _collect_scope_tokens(tokens, ticket.get("target_scope"))
    _collect_scope_tokens(tokens, ticket.get("relation_refs"))
    _collect_scope_tokens(tokens, ticket.get("related_refs"))
    _collect_scope_tokens(tokens, _mapping(ticket.get("metadata")).get("invocation_context"))
    return tokens


def _ticket_owner_tokens(ticket: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    _collect_scope_tokens(tokens, ticket.get("owner_scope"))
    metadata = _mapping(ticket.get("metadata"))
    _collect_scope_tokens(tokens, metadata.get("claimed_by"))
    return tokens


def _ticket_search_text(ticket: Mapping[str, Any]) -> str:
    chunks = [
        _text(ticket.get("ticket_id")),
        _text(ticket.get("kind")),
        _text(ticket.get("status")),
        _text(ticket.get("owner_area")),
        _text(ticket.get("component_ref")),
        json.dumps(ticket.get("web_component") or {}, ensure_ascii=False, sort_keys=True, default=str),
        _text(ticket.get("summary")),
        _text(ticket.get("source")),
        json.dumps(ticket.get("target_scope") or {}, ensure_ascii=False, sort_keys=True, default=str),
        json.dumps(ticket.get("metadata") or {}, ensure_ascii=False, sort_keys=True, default=str),
        json.dumps(ticket.get("relation_refs") or ticket.get("related_refs") or [], ensure_ascii=False, sort_keys=True, default=str),
        json.dumps(ticket.get("comments") or [], ensure_ascii=False, sort_keys=True, default=str),
    ]
    return "\n".join(chunks).lower()


def _artifact_id_from_ref(ref: Mapping[str, Any]) -> str:
    direct = _text(ref.get("artifact_id"))
    if direct:
        return direct
    uri = _text(ref.get("uri"))
    if uri.startswith("dev-ticket-artifact:"):
        return uri.split(":", 1)[1].strip()
    return ""


def _web_component_from_scopes(
    web_component: Mapping[str, Any] | None,
    target_scope: Mapping[str, Any] | None = None,
    origin_scope: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the stable UI element identity carried by a Dev Ticket.

    Older prototype-review tickets stored these fields directly in target_scope;
    keep projecting those records while new clients use the explicit entity.
    """

    target = _mapping(target_scope)
    origin = _mapping(origin_scope)
    meta = _mapping(metadata)
    candidates = [
        _mapping(web_component),
        _mapping(target.get("web_component")),
        _mapping(origin.get("web_component")),
        _mapping(meta.get("web_component")),
    ]
    selected = next((item for item in candidates if item), {})
    ref = _text(selected.get("ref") or selected.get("id"))
    if not ref:
        legacy_ref = _text(target.get("component_ref"))
        if legacy_ref.startswith(("widget:", "field:")):
            ref = legacy_ref
            selected = {
                "ref": ref,
                "kind": target.get("component_kind"),
                "type": target.get("component_type"),
                "label": target.get("component_label"),
            }
    if not ref or not ref.startswith(("widget:", "field:")):
        return {}
    kind = _text(selected.get("kind")) or ref.split(":", 1)[0]
    component_type = _text(selected.get("type")) or kind
    label = _text(selected.get("label") or selected.get("name")) or ref
    normalized = {
        **selected,
        "ref": ref,
        "kind": kind,
        "type": component_type,
        "label": label,
    }
    if ref.startswith("widget:") and not _text(normalized.get("widget_id")):
        normalized["widget_id"] = ref.split(":", 1)[1]
    if ref.startswith("field:"):
        parts = ref.split(":", 2)
        if len(parts) == 3:
            normalized.setdefault("widget_id", parts[1])
            normalized.setdefault("field_id", parts[2])
    return {key: value for key, value in normalized.items() if value not in (None, "")}


_EXTERNAL_ISSUE_POLICY_MODES = {
    "none",
    "link_only",
    "draft_export",
    "private_repo_issue",
    "public_upstream_issue",
    "mirror_status",
}
_EXTERNAL_ISSUE_SAFE_EVIDENCE_TYPES = {"commit", "release", "test", "version", "project_release"}
_EXTERNAL_ISSUE_UNSAFE_EVIDENCE_TYPES = {
    "audio",
    "dom",
    "file",
    "log",
    "nlu_example",
    "runtime_guard",
    "screenshot",
    "state_snapshot",
    "trace",
    "voice_transcript",
}
_CORE_IMPACT_PRIORITY = {
    "blocker": 100,
    "security_governance": 90,
    "policy_boundary": 80,
    "compatibility_debt": 70,
    "lifecycle_gap": 65,
    "contract_gap": 60,
    "observability_gap": 50,
    "generalization": 40,
    "speed": 30,
}


def _redact_external_issue_text(value: Any) -> tuple[str, list[str]]:
    text = _text(value)
    findings: list[str] = []
    substitutions = (
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "[redacted credential]", "credential"),
        (
            r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            "credential",
        ),
        (r"(?i)https?://[^\s/@]+:[^\s/@]+@", "https://[redacted]@", "credential_url"),
        (r"(?i)\b[A-Z]:\\(?:[^\s<>\"']+\\)*[^\s<>\"']*", "[local path]", "local_path"),
        (r"(?i)(?:/home/|/Users/|/root/|/var/lib/adaos/|\.adaos/)[^\s<>\"']*", "[local path]", "local_path"),
    )
    for pattern, replacement, finding in substitutions:
        updated, count = re.subn(pattern, replacement, text)
        if count:
            findings.append(finding)
            text = updated
    return text, sorted(set(findings))


def _safe_external_evidence(refs: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    safe: list[dict[str, Any]] = []
    excluded: list[str] = []
    for ref in refs:
        evidence_type = _text(ref.get("type") or ref.get("kind")).lower()
        if evidence_type in _EXTERNAL_ISSUE_UNSAFE_EVIDENCE_TYPES or evidence_type not in _EXTERNAL_ISSUE_SAFE_EVIDENCE_TYPES:
            excluded.append(evidence_type or "unknown")
            continue
        item: dict[str, Any] = {"type": evidence_type}
        for key in ("id", "version", "digest", "status", "name"):
            if ref.get(key) in (None, ""):
                continue
            cleaned, findings = _redact_external_issue_text(ref.get(key))
            if findings:
                excluded.extend(findings)
            item[key] = cleaned
        safe.append(item)
    return _merge_refs([], safe), sorted(set(excluded))


@dataclass(slots=True)
class DevelopmentTicketService:
    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "development_tickets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".state.lock"

    def capture_signal(
        self,
        *,
        kind: str,
        summary: str,
        owner_scope: Mapping[str, Any] | None = None,
        origin_scope: Mapping[str, Any] | None = None,
        target_scope: Mapping[str, Any] | None = None,
        severity: str = "medium",
        blocking: bool = False,
        source: str = "runtime",
        dedup_key: str | None = None,
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        conversation_ref: Mapping[str, Any] | None = None,
        nlu_teacher_ref: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        classification_confidence: float | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
        web_component: Mapping[str, Any] | None = None,
        relation_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        signal_kind = _text(kind)
        text = _text(summary)
        if not signal_kind or not text:
            raise ValueError("kind and summary are required")
        owner = _mapping(owner_scope) or {"type": "workspace", "id": "local"}
        origin = _mapping(origin_scope) or {"type": "runtime"}
        target = _mapping(target_scope) or {"type": "unknown"}
        meta = _mapping(metadata)
        area = _text(owner_area) or _owner_area_from_scope(target, meta)
        component = _text(component_ref) or _component_ref_from_scopes(target, origin, meta)
        web_element = _web_component_from_scopes(web_component, target, origin, meta)
        component = component or _text(web_element.get("ref"))
        key = _text(dedup_key) or _fingerprint("dsig", signal_kind, text.lower(), _target_identity(target), metadata or {})
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for signal in state["signals"].values():
                if signal.get("dedup_key") == key and signal.get("status") in ACTIVE_SIGNAL_STATES:
                    signal["occurrence_count"] = int(signal.get("occurrence_count") or 1) + 1
                    signal["artifact_refs"] = _merge_refs(signal.get("artifact_refs") or [], artifact_refs)
                    signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], evidence_refs)
                    signal["relation_refs"] = _normalize_relation_refs(
                        _sequence_of_mappings(signal.get("relation_refs") or []),
                        relation_refs,
                    )
                    signal["owner_area"] = _text(signal.get("owner_area")) or area
                    signal["component_ref"] = _text(signal.get("component_ref")) or component
                    if web_element and not _mapping(signal.get("web_component")):
                        signal["web_component"] = web_element
                    signal["updated_at"] = _now()
                    self._append_history(
                        signal,
                        {
                            "kind": "duplicate_occurrence",
                            "source": _text(source) or "runtime",
                            "recorded_at": signal["updated_at"],
                        },
                    )
                    self._validate_signal(signal)
                    self._write(state)
                    return {"ok": True, "duplicate": True, "signal": _clone(signal)}
            now = _now()
            signal_id = f"dsig.{new_id()}"
            signal = {
                "schema": DEVELOPMENT_SIGNAL_SCHEMA,
                "signal_id": signal_id,
                "kind": signal_kind,
                "status": "captured",
                "summary": text,
                "severity": _text(severity) or "medium",
                "blocking": bool(blocking),
                "owner_area": area,
                "component_ref": component,
                **({"web_component": web_element} if web_element else {}),
                "classification_confidence": float(classification_confidence if classification_confidence is not None else 1.0),
                "owner_scope": owner,
                "origin_scope": origin,
                "target_scope": target,
                "dedup_key": key,
                "occurrence_count": 1,
                "source": _text(source) or "runtime",
                "artifact_refs": _merge_refs([], artifact_refs),
                "evidence_refs": _merge_refs([], evidence_refs),
                "conversation_ref": _mapping(conversation_ref),
                "nlu_teacher_ref": _mapping(nlu_teacher_ref),
                "builder_ref": {},
                "issue_ref": {},
                "relation_refs": _normalize_relation_refs(relation_refs),
                "policy": _mapping(policy),
                "metadata": meta,
                "history": [{"kind": "captured", "recorded_at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self._validate_signal(signal)
            state["signals"][signal_id] = signal
            self._write(state)
            return {"ok": True, "duplicate": False, "signal": _clone(signal)}

    def replay_create_ticket_request(self, request_id: str | None) -> dict[str, Any] | None:
        """Return the durable result of an already-applied create command."""
        command_id = _text(request_id)
        if not command_id:
            return None
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            receipt = _mapping(state.get("command_receipts", {}).get(command_id))
            signal = state["signals"].get(_text(receipt.get("signal_id")))
            ticket = state["tickets"].get(_text(receipt.get("ticket_id")))
            if not isinstance(signal, Mapping) or not isinstance(ticket, Mapping):
                return None
            return {
                "signal": _clone(signal),
                "ticket": _normalized_ticket(ticket),
            }

    def record_create_ticket_request(
        self,
        request_id: str | None,
        *,
        signal_id: str,
        ticket_id: str,
    ) -> None:
        """Persist an idempotency receipt after the ticket transaction succeeds."""
        command_id = _text(request_id)
        signal_ref = _text(signal_id)
        ticket_ref = _text(ticket_id)
        if not command_id or not signal_ref or not ticket_ref:
            return
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            receipts = dict(state.get("command_receipts") or {})
            receipts[command_id] = {
                "kind": "development_ticket.create",
                "signal_id": signal_ref,
                "ticket_id": ticket_ref,
                "recorded_at": _now(),
            }
            # Receipts only bridge transport retries. Keep the newest bounded
            # set so an abandoned browser cannot grow the canonical state.
            if len(receipts) > 2048:
                ordered = sorted(
                    receipts.items(),
                    key=lambda item: _text(_mapping(item[1]).get("recorded_at")),
                    reverse=True,
                )
                receipts = dict(ordered[:2048])
            state["command_receipts"] = receipts
            self._write(state)

    def ensure_ticket_for_signal(
        self,
        signal: Mapping[str, Any],
        *,
        kind: str,
        summary: str | None = None,
        status: str = "captured",
        priority: str | None = None,
        source: str | None = None,
        dedup_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
        web_component: Mapping[str, Any] | None = None,
        relation_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        signal_id = _text(signal.get("signal_id"))
        if not signal_id:
            raise ValueError("signal_id is required")
        ticket_kind = _text(kind)
        text = _text(summary or signal.get("summary"))
        if not ticket_kind or not text:
            raise ValueError("kind and summary are required")
        target = _mapping(signal.get("target_scope"))
        meta = {
            **_mapping(signal.get("metadata")),
            **_mapping(metadata),
        }
        area = _text(owner_area) or _text(signal.get("owner_area")) or _owner_area_from_scope(target, meta)
        component = _text(component_ref) or _text(signal.get("component_ref")) or _component_ref_from_scopes(
            target,
            _mapping(signal.get("origin_scope")),
            meta,
        )
        web_element = _web_component_from_scopes(
            web_component or _mapping(signal.get("web_component")),
            target,
            _mapping(signal.get("origin_scope")),
            meta,
        )
        component = component or _text(web_element.get("ref"))
        key = _text(dedup_key) or _fingerprint("dticket", ticket_kind, signal.get("dedup_key"), _target_identity(target))
        priority_token = _text(priority).lower()
        if priority_token and priority_token not in TICKET_PRIORITIES:
            raise ValueError(f"unsupported Dev Ticket priority: {priority_token}")
        if not priority_token:
            severity_token = _text(signal.get("severity")).lower()
            priority_token = "must" if bool(signal.get("blocking")) or severity_token in {"high", "critical"} else "should"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            if signal_id not in state["signals"]:
                state["signals"][signal_id] = _clone(signal)
            for ticket in state["tickets"].values():
                if ticket.get("dedup_key") == key and ticket.get("status") in ACTIVE_TICKET_STATES:
                    ticket["occurrence_count"] = int(ticket.get("occurrence_count") or 1) + 1
                    ticket["signal_ids"] = _merge_ids(ticket.get("signal_ids") or [], [signal_id])
                    ticket["evidence_refs"] = _merge_refs(
                        ticket.get("evidence_refs") or [],
                        _sequence_of_mappings(signal.get("evidence_refs") or []),
                    )
                    ticket["artifact_refs"] = _merge_refs(
                        ticket.get("artifact_refs") or [],
                        _sequence_of_mappings(signal.get("artifact_refs") or []),
                    )
                    ticket["relation_refs"] = _normalize_relation_refs(
                        _sequence_of_mappings(ticket.get("relation_refs") or []),
                        _sequence_of_mappings(signal.get("relation_refs") or []),
                        relation_refs,
                    )
                    ticket["owner_area"] = _text(ticket.get("owner_area")) or area
                    ticket["component_ref"] = _text(ticket.get("component_ref")) or component
                    if _text(ticket.get("priority")).lower() not in TICKET_PRIORITIES:
                        ticket["priority"] = priority_token
                    if web_element and not _mapping(ticket.get("web_component")):
                        ticket["web_component"] = web_element
                    ticket["metadata"] = {
                        **_mapping(ticket.get("metadata")),
                        **meta,
                    }
                    ticket["updated_at"] = _now()
                    self._append_history(
                        ticket,
                        {
                            "kind": "duplicate_signal",
                            "signal_id": signal_id,
                            "recorded_at": ticket["updated_at"],
                        },
                    )
                    self._validate_ticket(ticket)
                    self._write(state)
                    return {"ok": True, "duplicate": True, "ticket": _normalized_ticket(ticket)}
            now = _now()
            ticket_id = f"dticket.{new_id()}"
            ticket = {
                "schema": DEV_TICKET_SCHEMA,
                "ticket_id": ticket_id,
                "revision": 1,
                "kind": ticket_kind,
                "status": _text(status) or "captured",
                "priority": priority_token,
                "summary": text,
                "severity": _text(signal.get("severity")) or "medium",
                "blocking": bool(signal.get("blocking")),
                "owner_area": area,
                "component_ref": component,
                **({"web_component": web_element} if web_element else {}),
                "owner_scope": _mapping(signal.get("owner_scope")),
                "origin_scope": _mapping(signal.get("origin_scope")),
                "target_scope": target,
                "signal_ids": [signal_id],
                "dedup_key": key,
                "occurrence_count": 1,
                "source": _text(source or signal.get("source")) or "runtime",
                "evidence_refs": _merge_refs([], _sequence_of_mappings(signal.get("evidence_refs") or [])),
                "artifact_refs": _merge_refs([], _sequence_of_mappings(signal.get("artifact_refs") or [])),
                "pending_action_refs": [],
                "builder_refs": [],
                "external_refs": [],
                "relation_refs": _normalize_relation_refs(
                    _sequence_of_mappings(signal.get("relation_refs") or []),
                    relation_refs,
                ),
                "policy": _mapping(policy) or _mapping(signal.get("policy")),
                "metadata": meta,
                "history": [{"kind": "created", "signal_id": signal_id, "recorded_at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self._validate_ticket(ticket)
            state["tickets"][ticket_id] = ticket
            self._write(state)
            return {"ok": True, "duplicate": False, "ticket": _normalized_ticket(ticket)}

    def report_compatibility_finding(
        self,
        *,
        code: str,
        summary: str,
        target_scope: Mapping[str, Any],
        owner_scope: Mapping[str, Any] | None = None,
        origin_scope: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any] | None = None,
        severity: str = "high",
        blocking: bool = True,
        run_policy: str = "block",
        design_time_fixable: bool = True,
        autonomous_repair_eligible: bool = True,
        source: str = "runtime_guard",
        dedup_key: str | None = None,
        publish_pending_action: bool = False,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        reason_code = _text(code)
        target = _mapping(target_scope)
        details = _mapping(context)
        key = _text(dedup_key) or _fingerprint("compat", reason_code, _target_identity(target), details.get("receiver"), details.get("route"), details.get("projection_slot"))
        policy = {
            "blocking": bool(blocking),
            "run_policy": _text(run_policy) or "block",
            "design_time_fixable": bool(design_time_fixable),
            "autonomous_repair_eligible": bool(autonomous_repair_eligible),
        }
        signal_result = self.capture_signal(
            kind="compatibility_finding",
            summary=summary,
            owner_scope=owner_scope,
            origin_scope=origin_scope or {"type": "runtime", "surface": "compatibility_guard"},
            target_scope=target,
            severity=severity,
            blocking=blocking,
            source=source,
            dedup_key=key,
            artifact_refs=artifact_refs,
            evidence_refs=evidence_refs,
            policy=policy,
            metadata={"code": reason_code, "context": details},
        )
        ticket_result = self.ensure_ticket_for_signal(
            signal_result["signal"],
            kind="runtime_compatibility_debt",
            status="accepted" if blocking else "captured",
            source=source,
            dedup_key=key,
            metadata={"code": reason_code, "context": details},
            policy=policy,
        )
        ticket = ticket_result["ticket"]
        pending_action = None
        pending_action_published = False
        if publish_pending_action and (blocking or _text(run_policy) in {"block", "degrade"}):
            pa_result = self.publish_compatibility_pending_action(
                ticket["ticket_id"],
                ctx=ctx,
                webspace_id=webspace_id,
            )
            pending_action = pa_result.get("pending_action")
            pending_action_published = bool(pa_result.get("published"))
            ticket = pa_result.get("ticket") or ticket
        return {
            "ok": True,
            "signal": signal_result["signal"],
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket": ticket,
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
            "pending_action": pending_action,
            "pending_action_published": pending_action_published,
        }

    def report_stream_receiver_compatibility_finding(
        self,
        *,
        skill_id: str,
        admission: Mapping[str, Any],
        compatibility_snapshot: Mapping[str, Any] | None = None,
        topic: str = "",
        event_type: str = "",
        owner_scope: Mapping[str, Any] | None = None,
        target_scope: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        publish_pending_action: bool = False,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        reason = _text(admission.get("reason"))
        if reason not in RECEIVER_COMPATIBILITY_REASONS:
            return {"ok": True, "reported": False, "reason": reason or "not_receiver_compatibility"}
        skill = _text(skill_id)
        if not skill:
            raise ValueError("skill_id is required")
        from adaos.services.runtime_compatibility import (
            classify_runtime_compatibility,
            collect_skill_runtime_compatibility_snapshot,
        )

        snapshot = (
            dict(compatibility_snapshot)
            if isinstance(compatibility_snapshot, Mapping)
            else collect_skill_runtime_compatibility_snapshot(
                skill,
                admission=admission,
                ctx=ctx,
            )
        )
        qualification = classify_runtime_compatibility(snapshot)
        receiver = _text(admission.get("receiver"))
        missing_policy = reason == "stream_receiver_policy_missing"
        summary = (
            f"Skill {skill} has no stream receiver policy. Declare only the streams owned by this skill."
            if missing_policy else
            f"Skill {skill} lacks receiver/data-route declaration" + (f" for {receiver}" if receiver else "")
        )
        context = {
            "code": f"compat.{reason}",
            "reason": reason,
            "receiver": receiver or None,
            "receiver_patterns": list(admission.get("receiver_patterns") or [])[:12],
            "topic": _text(topic) or None,
            "event_type": _text(event_type) or _text(topic) or None,
            "qualification": qualification,
            "compatibility_snapshot": snapshot,
            "remediation": (
                "Inspect the skill handlers and declare their owned stream receivers. "
                "The observed broadcast receiver is evidence of the missing policy, not proof of ownership. "
                "Do not add foreign receivers or a wildcard to silence this finding."
                if missing_policy else "Verify the receiver ownership and data-route contract before retrying."
            ),
        }
        target = _mapping(target_scope) or {"type": "skill", "id": skill, "source": "installed"}
        reported = self.report_compatibility_finding(
                code=f"compat.{reason}",
                summary=summary,
                target_scope=target,
                owner_scope=owner_scope,
                origin_scope={"type": "runtime", "surface": "stream_receiver_admission", "id": skill},
                evidence_refs=[
                    *evidence_refs,
                    {
                        "type": "runtime_guard",
                        "code": f"compat.{reason}",
                        "receiver": receiver or None,
                        "topic": _text(topic) or None,
                    },
                ],
                artifact_refs=artifact_refs,
                context=context,
                severity="high" if reason == "stream_receiver_not_declared" else "medium",
                blocking=reason == "stream_receiver_not_declared",
                run_policy="block" if reason == "stream_receiver_not_declared" else "degrade",
                design_time_fixable=True,
                autonomous_repair_eligible=bool(
                    qualification.get("automatic_recovery_eligible")
                ),
                source="runtime_guard",
                # An absent manifest policy is one defect, not one defect for
                # every broadcast stream seen by an ungoverned subscriber.
                dedup_key=_fingerprint("compat.receiver", skill, reason, "policy" if missing_policy else receiver or _text(topic)),
                # A receiver guard observation is diagnostic evidence, not a
                # user decision. Exact update/reactivation surfaces are owned
                # by their lifecycle commands and must not be approximated by
                # this legacy Builder-repair card.
                publish_pending_action=False,
                ctx=ctx,
                webspace_id=webspace_id,
            )
        reconciled = self.reconcile_compatibility_pending_actions(
            reported["ticket"]["ticket_id"],
            qualification=qualification,
            ctx=ctx,
            webspace_id=webspace_id,
        )
        return {
            **reported,
            "ticket": reconciled["ticket"],
            "reported": True,
            "qualification": qualification,
            "pending_action_suppressed": bool(publish_pending_action),
            "pending_action_reconciliation": {
                "cancelled": reconciled["cancelled"],
                "failures": reconciled["failures"],
            },
        }

    def reconcile_compatibility_pending_actions(
        self,
        ticket_id: str,
        *,
        qualification: Mapping[str, Any],
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel obsolete legacy compatibility cards after requalification.

        Runtime reactivation, exact package update, and scoped Builder repair
        have different authorities. The former generic compatibility card
        cannot safely represent any of them and must not survive a new
        classifier result as an actionable request.
        """

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        refs = _sequence_of_mappings(ticket.get("pending_action_refs") or [])
        active = [
            ref
            for ref in refs
            if ref.get("kind") == COMPATIBILITY_PENDING_ACTION_KIND
            and _text(ref.get("status") or "pending") in {"pending", "postponed"}
            and _text(ref.get("id"))
        ]
        if not active:
            return {"ticket": ticket, "cancelled": [], "failures": []}
        from adaos.services import pending_actions

        code = _text(qualification.get("code")) or "requalified"
        reason = f"compatibility_requalified:{code}"
        updated_refs = [dict(ref) for ref in refs]
        cancelled: list[str] = []
        failures: list[dict[str, str]] = []
        for ref in active:
            action_id = _text(ref.get("id"))
            try:
                pending_actions.cancel_pending_action(
                    action_id,
                    reason=reason,
                    ctx=ctx,
                    webspace_id=webspace_id,
                    actor={"type": "system", "system_id": "runtime_compatibility"},
                )
            except Exception as exc:
                failures.append(
                    {
                        "pending_action_id": action_id,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue
            updated_refs = _with_pending_action_ref_status(
                updated_refs,
                action_id=action_id,
                status="cancelled",
                reason=reason,
            )
            cancelled.append(action_id)
        if not cancelled:
            return {"ticket": ticket, "cancelled": [], "failures": failures}
        status = _text(ticket.get("status"))
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=updated_refs,
            status="accepted" if status == "waiting_for_user" else status,
            history_item={
                "kind": "compatibility_pending_actions_reconciled",
                "pending_action_ids": cancelled,
                "qualification_code": code,
                "recommended_action": _text(qualification.get("recommended_action")) or None,
                "reason": reason,
            },
        )
        return {"ticket": updated, "cancelled": cancelled, "failures": failures}

    async def reconcile_legacy_compatibility_pending_action_cohort(
        self,
        *,
        ctx: Any = None,
        webspace_id: str | None = None,
        limit: int = 100,
        apply: bool = False,
        execute_recovery: bool = True,
        create_builder_handoff: bool = True,
        snapshot_collector: Callable[..., Mapping[str, Any]] | None = None,
        classifier: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        repair_service: BuilderRepairService | None = None,
        exact_update_publisher: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        """Requalify and retire bounded cohorts of legacy compatibility cards.

        A missing receiver policy used to be deduplicated by the receiver that
        happened to expose it. That produced several user decisions for one
        application defect. The current contract treats those broadcasts as
        evidence, classifies the exact runtime identity once per cohort, and
        terminally reconciles every generic card before choosing a typed
        recovery surface.

        ``apply=False`` is a read-only audit. Startup callers must opt into
        mutation explicitly. Both the scan and the returned evidence are
        bounded so an old state file cannot make restart work unbounded.
        """

        from adaos.services.runtime_compatibility import (
            classify_runtime_compatibility,
            collect_skill_runtime_compatibility_snapshot,
        )

        bounded_limit = max(1, min(int(limit), 100))
        scan_limit = min(400, max(bounded_limit + 1, bounded_limit * 4))
        recent = self.list_tickets(
            status_group="open",
            kind="runtime_compatibility_debt",
            limit=scan_limit,
        )
        candidates: list[dict[str, Any]] = []
        for ticket in reversed(recent):
            refs = _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if not any(
                ref.get("kind") == COMPATIBILITY_PENDING_ACTION_KIND
                and _text(ref.get("status") or "pending") in {"pending", "postponed"}
                and _text(ref.get("id"))
                for ref in refs
            ):
                continue
            context = _mapping(_mapping(ticket.get("metadata")).get("context"))
            reason = _text(context.get("reason"))
            target = _mapping(ticket.get("target_scope"))
            skill_id = _text(target.get("id"))
            if reason not in RECEIVER_COMPATIBILITY_REASONS or not skill_id:
                continue
            candidates.append(ticket)
            if len(candidates) >= bounded_limit:
                break

        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for ticket in candidates:
            context = _mapping(_mapping(ticket.get("metadata")).get("context"))
            reason = _text(context.get("reason"))
            skill_id = _text(_mapping(ticket.get("target_scope")).get("id"))
            receiver_key = (
                "policy"
                if reason == "stream_receiver_policy_missing"
                else _text(context.get("receiver")) or "unknown"
            )
            grouped.setdefault((skill_id, reason, receiver_key), []).append(ticket)

        collect = snapshot_collector or collect_skill_runtime_compatibility_snapshot
        classify = classifier or classify_runtime_compatibility
        group_results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for group_key in sorted(grouped):
            skill_id, reason, receiver_key = group_key
            tickets = grouped[group_key]
            expected_dedup = _fingerprint(
                "compat.receiver",
                skill_id,
                reason,
                receiver_key,
            )
            canonical = max(
                tickets,
                key=lambda item: (
                    _text(item.get("dedup_key")) == expected_dedup,
                    _text(item.get("created_at")),
                    _text(item.get("ticket_id")),
                ),
            )
            canonical_context = _mapping(
                _mapping(canonical.get("metadata")).get("context")
            )
            admission = {
                "reason": reason,
                "receiver": _text(canonical_context.get("receiver")) or None,
                "receiver_patterns": list(
                    canonical_context.get("receiver_patterns") or ()
                )[:12],
                "allowed": False,
            }
            try:
                snapshot = dict(
                    collect(
                        skill_id,
                        admission=admission,
                        builder_work=_sequence_of_mappings(
                            canonical.get("builder_refs") or []
                        ),
                        ctx=ctx,
                    )
                )
                qualification = dict(classify(snapshot))
            except Exception as exc:
                errors.append(
                    {
                        "skill_id": skill_id,
                        "reason": reason,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue

            cohort_digest = _fingerprint(
                "compat.reconciliation",
                sorted(_text(item.get("ticket_id")) for item in tickets),
                snapshot,
                qualification,
            )
            result: dict[str, Any] = {
                "skill_id": skill_id,
                "reason": reason,
                "receiver_key": receiver_key,
                "canonical_ticket_id": canonical["ticket_id"],
                "ticket_ids": sorted(
                    _text(item.get("ticket_id")) for item in tickets
                ),
                "cohort_digest": cohort_digest,
                "qualification": qualification,
                "applied": False,
                "cancelled_pending_action_ids": [],
                "superseded_ticket_ids": [],
                "outcome": "audit_only",
            }
            group_results.append(result)
            if not apply:
                continue

            reconciliation_failures: list[dict[str, str]] = []
            for item in tickets:
                ticket_id = _text(item.get("ticket_id"))
                current = self.get_ticket(ticket_id)
                if not current:
                    reconciliation_failures.append(
                        {"ticket_id": ticket_id, "error": "ticket_not_found"}
                    )
                    continue
                metadata = _mapping(current.get("metadata"))
                context = _mapping(metadata.get("context"))
                context["compatibility_snapshot"] = snapshot
                context["qualification"] = qualification
                metadata["context"] = context
                metadata["compatibility_reconciliation"] = {
                    "schema": "adaos.runtime_compatibility.reconciliation.v1",
                    "cohort_digest": cohort_digest,
                    "canonical_ticket_id": canonical["ticket_id"],
                    "recorded_at": _now(),
                }
                self._update_ticket(
                    ticket_id,
                    metadata=metadata,
                    history_item={
                        "kind": "compatibility_requalified",
                        "qualification_code": _text(qualification.get("code")),
                        "cohort_digest": cohort_digest,
                        "canonical_ticket_id": canonical["ticket_id"],
                    },
                )
                reconciled = self.reconcile_compatibility_pending_actions(
                    ticket_id,
                    qualification=qualification,
                    ctx=ctx,
                    webspace_id=webspace_id,
                )
                result["cancelled_pending_action_ids"].extend(
                    reconciled["cancelled"]
                )
                reconciliation_failures.extend(
                    {
                        "ticket_id": ticket_id,
                        **dict(failure),
                    }
                    for failure in reconciled["failures"]
                )

            if reconciliation_failures:
                result["failures"] = reconciliation_failures
                result["outcome"] = "pending_action_reconciliation_failed"
                errors.extend(reconciliation_failures)
                continue

            for item in tickets:
                ticket_id = _text(item.get("ticket_id"))
                if ticket_id == canonical["ticket_id"]:
                    continue
                current = self.get_ticket(ticket_id)
                if not current or _text(current.get("status")) in TERMINAL_TICKET_STATES:
                    continue
                self.duplicate_ticket(
                    ticket_id,
                    duplicate_of=canonical["ticket_id"],
                    actor="runtime_compatibility",
                    expected_revision=int(current.get("revision") or 0),
                )
                result["superseded_ticket_ids"].append(ticket_id)

            canonical_ticket = self.get_ticket(canonical["ticket_id"])
            if not canonical_ticket:
                result["outcome"] = "canonical_ticket_not_found"
                errors.append(
                    {
                        "ticket_id": canonical["ticket_id"],
                        "error": "canonical_ticket_not_found",
                    }
                )
                continue

            code = _text(qualification.get("code"))
            if qualification.get("automatic_recovery_eligible") is True and execute_recovery:
                recovery = await self.execute_qualified_runtime_recovery(
                    canonical_ticket["ticket_id"],
                    skill_id=skill_id,
                    qualification=qualification,
                    compatibility_snapshot=snapshot,
                    ctx=ctx,
                )
                result["recovery"] = recovery
                result["outcome"] = (
                    "automatic_recovery_verified"
                    if recovery.get("ok") is True and recovery.get("executed") is True
                    else _text(recovery.get("reason")) or "automatic_recovery_failed"
                )
            elif (
                code in {"application_declaration_defect", "application_source_drift"}
                and qualification.get("evidence_complete") is True
                and create_builder_handoff
            ):
                active_builder = [
                    ref
                    for ref in _sequence_of_mappings(
                        canonical_ticket.get("builder_refs") or []
                    )
                    if _text(ref.get("status")).lower()
                    not in {"closed", "cancelled", "failed", "rejected", "superseded", "verified"}
                ]
                if active_builder:
                    result["outcome"] = "existing_builder_work"
                    result["builder_ref"] = active_builder[0]
                else:
                    handoff = self.handoff_ticket(
                        canonical_ticket["ticket_id"],
                        mode="interactive",
                        repair_service=repair_service,
                        actor="runtime_compatibility",
                    )
                    result["outcome"] = "scoped_builder_repair_created"
                    result["builder_ref"] = handoff["repair"]
            elif code == "eligible_exact_update" and (
                exact_update_publisher is not None or ctx is not None
            ):
                publisher = (
                    exact_update_publisher
                    if exact_update_publisher is not None
                    else self.publish_qualified_runtime_update_decision
                )
                published = publisher(
                    ticket=canonical_ticket,
                    skill_id=skill_id,
                    qualification=qualification,
                    cohort_digest=cohort_digest,
                    ctx=ctx,
                    webspace_id=webspace_id,
                )
                if asyncio.iscoroutine(published):
                    published = await published
                interaction = _mapping(_mapping(published).get("interaction"))
                interaction_id = _text(interaction.get("interaction_id"))
                if not interaction_id:
                    raise ValueError(
                        "exact update publisher returned no interaction identity"
                    )
                current = self.get_ticket(canonical_ticket["ticket_id"]) or canonical_ticket
                updated_refs = _merge_refs(
                    current.get("pending_action_refs") or [],
                    [
                        {
                            "id": interaction_id,
                            "kind": "runtime_compatibility_exact_update",
                            "status": "pending",
                            "created_at": interaction.get("created_at"),
                        }
                    ],
                )
                self._update_ticket(
                    current["ticket_id"],
                    pending_action_refs=updated_refs,
                    status="waiting_for_user",
                    history_item={
                        "kind": "runtime_compatibility_exact_update_published",
                        "interaction_id": interaction_id,
                        "cohort_digest": cohort_digest,
                    },
                )
                result["outcome"] = "exact_update_decision_published"
                result["interaction_id"] = interaction_id
            elif code in {"compatible", "foreign_stream_observation"}:
                verified = self._update_ticket(
                    canonical_ticket["ticket_id"],
                    status="verified",
                    history_item={
                        "kind": "compatibility_reconciliation_verified",
                        "qualification_code": code,
                        "cohort_digest": cohort_digest,
                    },
                )
                self._close_ticket(
                    verified["ticket_id"],
                    reason="verified",
                    actor="runtime_compatibility",
                    evidence_refs=[
                        {
                            "type": "runtime_compatibility_reconciliation",
                            "digest": cohort_digest,
                            "status": "passed",
                        }
                    ],
                    expected_revision=int(verified.get("revision") or 0),
                )
                result["outcome"] = "diagnostic_closed"
            else:
                next_status = (
                    "waiting_for_core"
                    if _text(qualification.get("owner")) == "core"
                    else "accepted"
                )
                self._update_ticket(
                    canonical_ticket["ticket_id"],
                    status=next_status,
                    history_item={
                        "kind": "compatibility_reconciliation_routed",
                        "qualification_code": code,
                        "recommended_action": _text(
                            qualification.get("recommended_action")
                        ),
                        "owner": _text(qualification.get("owner")),
                        "cohort_digest": cohort_digest,
                    },
                )
                result["outcome"] = (
                    "typed_update_publisher_required"
                    if code == "eligible_exact_update"
                    else "routed_without_user_decision"
                )
            result["cancelled_pending_action_ids"] = sorted(
                result["cancelled_pending_action_ids"]
            )
            result["superseded_ticket_ids"] = sorted(
                result["superseded_ticket_ids"]
            )
            result["applied"] = True

        return {
            "schema": "adaos.runtime_compatibility.cohort_reconciliation.v1",
            "applied": bool(apply),
            "limit": bounded_limit,
            "scanned": len(recent),
            "candidate_count": len(candidates),
            "group_count": len(group_results),
            "complete": len(recent) < scan_limit and len(candidates) < bounded_limit,
            "groups": group_results,
            "errors": errors,
        }

    async def publish_qualified_runtime_update_decision(
        self,
        *,
        ticket: Mapping[str, Any],
        skill_id: str,
        qualification: Mapping[str, Any],
        cohort_digest: str,
        ctx: Any,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Publish the one typed decision admitted by an exact update plan."""

        from adaos.services.artifact_subscription_update import (
            ArtifactSubscriptionUpdateCoordinator,
        )
        from adaos.services.conversation_interactions import (
            standard_capability_profile,
        )
        from adaos.services.yjs.webspace import default_webspace_id

        ws = _text(webspace_id) or default_webspace_id()
        profile = standard_capability_profile("web")
        profile["profile_id"] = "profile.web.runtime-compatibility-step-up"
        profile.setdefault("capabilities", {})["step_up"] = True
        digest_token = _text(cohort_digest).split(":")[-1]
        interaction_id = f"interaction.runtime-compatibility.{digest_token}"
        expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(
            timespec="seconds"
        )
        return await ArtifactSubscriptionUpdateCoordinator(
            ctx
        ).publish_qualified_runtime_update_interaction(
            "skill",
            skill_id,
            qualification=qualification,
            conversation_id=f"conv.core.general.{ws}",
            owner="skill:runtime_compatibility",
            expires_at=expiry,
            interaction_id=interaction_id,
            task_ref={
                "kind": "development_ticket",
                "id": _text(ticket.get("ticket_id")),
            },
            webspace_id=ws,
            channel_id="general",
            route_id="dialog",
            capability_profile=profile,
        )

    async def execute_qualified_runtime_recovery(
        self,
        ticket_id: str,
        *,
        skill_id: str,
        qualification: Mapping[str, Any],
        compatibility_snapshot: Mapping[str, Any],
        ctx: Any,
    ) -> dict[str, Any]:
        """Apply only an exact classifier-admitted automatic reactivation."""

        qualified = _mapping(qualification)
        if qualified.get("automatic_recovery_eligible") is not True:
            return {
                "ok": True,
                "executed": False,
                "reason": "automatic_recovery_not_eligible",
            }
        snapshot = _mapping(compatibility_snapshot)
        desired = _mapping(snapshot.get("desired_release"))
        installed = _mapping(snapshot.get("installed_release"))
        version = _text(desired.get("version") or installed.get("version"))
        slot = _text(installed.get("slot")).upper()
        package_digest = _text(desired.get("package_digest"))
        source_manifest_digest = _text(desired.get("manifest_digest"))
        if (
            not version
            or slot not in {"A", "B"}
            or not package_digest
            or not source_manifest_digest
            or package_digest != _text(installed.get("package_digest"))
            or source_manifest_digest != _text(installed.get("manifest_digest"))
        ):
            return {
                "ok": False,
                "executed": False,
                "reason": "exact_runtime_identity_incomplete",
            }

        from adaos.services.runtime_reactivation import (
            reactivate_exact_admitted_package,
        )

        receipt = await reactivate_exact_admitted_package(
            ctx,
            skill_id=_text(skill_id),
            expected_version=version,
            expected_slot=slot,
            expected_package_digest=package_digest,
            expected_source_manifest_digest=source_manifest_digest,
            classification=qualified,
        )
        safe_receipt = {
            key: receipt.get(key)
            for key in (
                "schema",
                "ok",
                "admitted",
                "reason",
                "operation_id",
                "skill_id",
                "expected_version",
                "expected_slot",
                "package_digest",
                "source_manifest_digest",
                "attempt_count",
                "attempt_number",
                "started_at",
                "completed_at",
                "duplicate",
            )
            if receipt.get(key) is not None
        }
        receipt_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if receipt.get("ok") is not True:
            ticket = self._append_ticket_history(
                ticket_id,
                {
                    "kind": "runtime_reactivation_attempted",
                    "actor": "runtime_compatibility",
                    "receipt": safe_receipt,
                    "receipt_digest": receipt_digest,
                },
            )
            return {
                "ok": False,
                "executed": True,
                "receipt": safe_receipt,
                "receipt_digest": receipt_digest,
                "ticket": ticket,
            }

        verified = self._update_ticket(
            ticket_id,
            status="verified",
            history_item={
                "kind": "runtime_reactivation_verified",
                "actor": "runtime_compatibility",
                "receipt": safe_receipt,
                "receipt_digest": receipt_digest,
            },
        )
        closed = self._close_ticket(
            verified["ticket_id"],
            reason="verified",
            actor="runtime_compatibility",
            evidence_refs=[
                {
                    "type": "runtime_reactivation_receipt",
                    "operation_id": receipt.get("operation_id"),
                    "digest": receipt_digest,
                }
            ],
            expected_revision=int(verified.get("revision") or 0),
        )
        return {
            "ok": True,
            "executed": True,
            "receipt": safe_receipt,
            "receipt_digest": receipt_digest,
            "ticket": closed,
        }

    def publish_compatibility_pending_action(
        self,
        ticket_id: str,
        *,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        existing = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == COMPATIBILITY_PENDING_ACTION_KIND
        ]
        if existing:
            return {"ok": True, "published": False, "reason": "pending_action_already_linked", "pending_action": existing[-1]}

        qualification = _mapping(
            _mapping(_mapping(ticket.get("metadata")).get("context")).get(
                "qualification"
            )
        )
        if not qualification.get("human_decision_required"):
            return {
                "ok": True,
                "published": False,
                "reason": "compatibility_qualification_does_not_require_user_decision",
                "qualification": qualification,
                "ticket": ticket,
            }
        if _text(qualification.get("recommended_action")) != "choose_builder_repair_mode":
            return {
                "ok": True,
                "published": False,
                "reason": "compatibility_recovery_surface_mismatch",
                "qualification": qualification,
                "ticket": ticket,
            }

        from adaos.services import pending_actions

        action = pending_actions.publish_pending_action(
            ctx=ctx,
            webspace_id=webspace_id,
            kind=COMPATIBILITY_PENDING_ACTION_KIND,
            title="Runtime compatibility issue",
            summary=ticket["summary"],
            producer={"type": "system", "system_id": "development_tickets"},
            owner_scope=ticket.get("owner_scope") or {"type": "workspace", "id": "local"},
            domain_ref={
                "ticket_id": ticket["ticket_id"],
                "signal_ids": list(ticket.get("signal_ids") or []),
                "target_scope": ticket.get("target_scope") or {},
            },
            allowed_actions=[
                {"id": "preview_evidence", "label": "Preview evidence", "terminal": False},
                {"id": "postpone", "label": "Later", "terminal": True},
                {"id": "open_builder", "label": "Open Builder", "terminal": True},
                {"id": "start_autonomous_repair", "label": "Repair autonomously", "terminal": True},
                {"id": "refuse", "label": "Refuse", "terminal": True},
            ],
            response_topic=COMPATIBILITY_RESPONSE_TOPIC,
            metadata={
                "schema": "adaos.dev_ticket.compatibility.pending_action_metadata.v1",
                "ticket_id": ticket["ticket_id"],
                "signal_ids": list(ticket.get("signal_ids") or []),
                "target_scope": ticket.get("target_scope") or {},
                "policy": ticket.get("policy") or {},
                "code": _mapping(ticket.get("metadata")).get("code"),
            },
        )
        ref = {
            "id": action.get("id"),
            "kind": action.get("kind"),
            "status": action.get("status"),
            "created_at": action.get("created_at"),
        }
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=_merge_refs(ticket.get("pending_action_refs") or [], [ref]),
            status="waiting_for_user",
            history_item={"kind": "pending_action_published", "pending_action_id": ref.get("id")},
        )
        return {"ok": True, "published": True, "pending_action": action, "ticket": updated}

    def handle_compatibility_response(
        self,
        *,
        ticket_id: str,
        response_action_id: str,
        pending_action_id: str | None = None,
        responder: Mapping[str, Any] | None = None,
        response_payload: Mapping[str, Any] | None = None,
        repair_service: BuilderRepairService | None = None,
    ) -> dict[str, Any]:
        action = _text(response_action_id)
        if not action:
            raise ValueError("response_action_id is required")
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        actor = _text(_mapping(responder).get("actor") or _mapping(responder).get("id")) or "pending_action"
        if action == "preview_evidence":
            updated = self._append_ticket_history(
                ticket["ticket_id"],
                {"kind": "evidence_previewed", "pending_action_id": _text(pending_action_id), "actor": actor},
            )
            return {
                "ok": True,
                "action": action,
                "ticket": updated,
                "repair": None,
                "preview": {
                    "schema": "adaos.runtime_compatibility.evidence_preview.v1",
                    "ticket_id": updated["ticket_id"],
                    "summary": updated.get("summary"),
                    "qualification": _mapping(
                        _mapping(_mapping(updated.get("metadata")).get("context")).get(
                            "qualification"
                        )
                    ),
                    "evidence_refs": list(updated.get("evidence_refs") or []),
                    "artifact_refs": list(updated.get("artifact_refs") or []),
                    "builder_refs": list(updated.get("builder_refs") or []),
                },
            }
        if action == "postpone":
            updated = self._update_ticket(
                ticket["ticket_id"],
                status="deferred",
                history_item={"kind": "postponed", "pending_action_id": _text(pending_action_id), "actor": actor},
            )
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
        if action == "refuse":
            updated = self._close_ticket(
                ticket["ticket_id"],
                reason="refused",
                actor=actor,
                evidence_refs=_sequence_of_mappings(_mapping(response_payload).get("evidence_refs") or []),
            )
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
        if action in {"open_builder", "start_autonomous_repair"}:
            qualification = _mapping(
                _mapping(_mapping(ticket.get("metadata")).get("context")).get(
                    "qualification"
                )
            )
            if _text(qualification.get("code")) != "application_declaration_defect":
                raise ValueError(
                    "runtime compatibility finding is not qualified as an application declaration defect"
                )
            if not qualification.get("evidence_complete"):
                raise ValueError(
                    "runtime compatibility evidence is incomplete; collect exact package identity before repair"
                )
            mode = "interactive" if action == "open_builder" else "autonomous"
            result = self.handoff_ticket(
                ticket["ticket_id"],
                mode=mode,
                repair_service=repair_service,
                actor=actor,
            )
            return {"ok": True, "action": action, "ticket": result["ticket"], "repair": result["repair"]}
        raise ValueError(f"unsupported compatibility response action: {action}")

    def defer_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        reason: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        if _text(ticket.get("owner_area")) == "core":
            return self.transition_core_ticket(
                ticket_id,
                transition="deferred",
                actor=actor,
                reason=reason,
                expected_revision=expected_revision,
            )["ticket"]
        return self._update_ticket(
            ticket_id,
            status="deferred",
            history_item={
                "kind": "deferred",
                "actor": _text(actor) or "system",
                "reason": _text(reason) or None,
            },
            expected_revision=expected_revision,
        )

    def handoff_ticket(
        self,
        ticket_id: str,
        *,
        mode: str,
        repair_service: BuilderRepairService | None = None,
        actor: str,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        mode_token = _text(mode) or "interactive"
        if mode_token not in {"autonomous", "interactive"}:
            raise ValueError("mode must be autonomous or interactive")
        repair = self._create_builder_repair(ticket, mode=mode_token, repair_service=repair_service)
        updated = self._link_builder_repair(ticket["ticket_id"], repair, mode=mode_token, actor=_text(actor) or "system")
        return {"ok": True, "ticket": updated, "repair": repair}

    def start_autonomous_repair(
        self,
        ticket_id: str,
        *,
        actor: str,
        repair_service: BuilderRepairService | None = None,
        automation_service: Any | None = None,
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        confirmed_technical_application_id: str | None = None,
        source_strategy: str | None = None,
        execution_budget: Mapping[str, Any] | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
            raise ValueError("terminal Dev Ticket cannot start autonomous repair")
        target = _automation_target_from_ticket(ticket)
        development_source = development_source_options(_development_source_scope(ticket, target))
        strategy = _text(source_strategy)
        materialization: dict[str, Any] | None = None
        if automation_service is None:
            from adaos.services.builder.automation import BuilderAutomationService

            automation_service = BuilderAutomationService.from_context()
        if development_source.get("status") == "needs_materialization":
            if not strategy:
                raise ValueError("autonomous repair requires source_strategy when development source is missing")
            if strategy == "defer":
                deferred = self.defer_ticket(ticket["ticket_id"], actor=actor, reason="development_source_missing")
                return {
                    "ok": True,
                    "started": False,
                    "ticket": deferred,
                    "repair": None,
                    "automation": None,
                    "reason": "development_source_deferred",
                    "development_source": development_source,
                }
            workspace_service = getattr(automation_service, "workspace_service", None)
            if workspace_service is None:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                workspace_service = BuilderWorkspaceService.from_context()
            project_id = _project_id_for_materialization(ticket, development_source)
            if strategy == "materialize_dev_source":
                materialization = workspace_service.materialize_dev_source(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    project_id=project_id,
                )
            elif strategy == "create_local_fork":
                materialization = workspace_service.create_local_fork(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    project_id=project_id,
                    actor=_text(actor) or "builder",
                )
            else:
                raise ValueError(
                    "development source strategy must be materialize_dev_source, "
                    "create_local_fork, or defer"
                )
            if not materialization or materialization.get("ok") is False:
                raise ValueError("development source materialization failed")
            development_source = _mapping(materialization.get("development_source"))
            if not development_source:
                development_source = _mapping(
                    workspace_service.development_source_status(
                        kind=target["object_type"],
                        artifact_id=target["object_id"],
                        project_id=project_id,
                    )
                )
        qualification = _autonomous_repair_qualification(ticket)
        qualification_candidate: dict[str, Any] = {}
        if not qualification["ready"]:
            prepared = self.prepare_builder_repair_qualification(
                ticket["ticket_id"],
                actor=_text(actor) or "builder.qualifier",
                apply=False,
            )
            qualification_candidate = _mapping(prepared.get("qualification_candidate"))
            if (
                qualification_candidate.get("ready") is True
                and _text(qualification_candidate.get("confidence")) == "high"
            ):
                applied = self.prepare_builder_repair_qualification(
                    ticket["ticket_id"],
                    actor=_text(actor) or "builder.qualifier",
                    apply=True,
                    expected_revision=int(ticket.get("revision") or 1),
                )
                ticket = _mapping(applied.get("ticket")) or ticket
                qualification = _mapping(applied.get("autonomous_repair_qualification"))
        if not qualification["ready"]:
            return {
                "ok": True,
                "started": False,
                "status": "qualification_required",
                "reason": "builder_qualification_required",
                "qualification": qualification,
                "qualification_candidate": qualification_candidate,
                "ticket": ticket,
                "repair": None,
                "automation": None,
                "development_source": development_source,
                "materialization": materialization,
            }
        source_preconditions = _validate_repair_source_preconditions(
            qualification,
            development_source=development_source,
            target=target,
        )
        if source_preconditions.get("ok") is not True:
            return {
                "ok": True,
                "started": False,
                "status": "source_changed",
                "reason": "builder_requalification_required",
                "qualification": qualification,
                "qualification_candidate": qualification_candidate,
                "source_preconditions": source_preconditions,
                "ticket": ticket,
                "repair": None,
                "automation": None,
                "development_source": development_source,
                "materialization": materialization,
            }
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        handoff = self.handoff_ticket(
            ticket["ticket_id"],
            mode="autonomous",
            repair_service=service,
            actor=_text(actor) or "builder",
        )
        repair = handoff["repair"]
        repair_id = _text(repair.get("repair_id"))
        brief = _autonomous_repair_brief(handoff["ticket"], repair, target=target)
        bounded_budget = _autonomous_repair_budget(qualification, execution_budget)
        automation_links = {
            "development_ticket_id": ticket["ticket_id"],
            "builder_repair_id": repair_id,
            "development_ticket_component_ref": _text(handoff["ticket"].get("component_ref")) or None,
            "development_ticket_owner_area": _text(handoff["ticket"].get("owner_area")) or None,
            **{
                f"development_ticket_{key}": value
                for key, value in _project_identity_from_ticket(handoff["ticket"]).items()
            },
            "development_source_materialization": _source_materialization_ref(
                materialization
            ),
            "source_precondition_validation": source_preconditions,
            **_trusted_publication_gate_lineage(
                handoff["ticket"],
                target=target,
            ),
        }
        resume_failed = getattr(automation_service, "resume_failed_dev_ticket_repair", None)
        resume_waiting_for_core = getattr(
            automation_service,
            "resume_waiting_for_core_dev_ticket_repair",
            None,
        )
        start_followup = getattr(automation_service, "start_followup_dev_ticket_repair", None)
        can_resume = False
        can_resume_waiting_for_core = False
        can_followup = False
        if (
            callable(resume_failed)
            or callable(resume_waiting_for_core)
            or callable(start_followup)
        ):
            current = automation_service.status(
                object_type=target["object_type"],
                object_id=target["object_id"],
            )
            current_session = _automation_session(current)
            current_links = _mapping(current_session.get("links"))
            can_resume = (
                _text(current_session.get("status")) == "failed"
                and _text(current_links.get("development_ticket_id")) == ticket["ticket_id"]
            )
            can_resume_waiting_for_core = (
                callable(resume_waiting_for_core)
                and _text(current_session.get("status")) == "waiting_for_core"
                and _text(current_links.get("development_ticket_id")) == ticket["ticket_id"]
            )
            readiness = _mapping(current_session.get("completion_readiness"))
            workflow_head_method = getattr(
                automation_service,
                "current_workflow_head",
                None,
            )
            workflow_head = (
                _mapping(
                    workflow_head_method(
                        object_type=target["object_type"],
                        object_id=target["object_id"],
                    )
                )
                if callable(workflow_head_method)
                else {}
            )
            workflow_state = _text(workflow_head.get("state"))
            followup_state_ready = not workflow_state or workflow_state in {
                "verification",
                "trial_ready",
                "trial_review",
                "publication_ready",
                "reconciliation_required",
            }
            can_followup = (
                callable(start_followup)
                and _text(current_session.get("status")) == "completed"
                and bool(readiness.get("ok"))
                and bool(_mapping(readiness.get("aprobation")).get("ok"))
                and followup_state_ready
            )
        start_method = (
            resume_waiting_for_core
            if can_resume_waiting_for_core
            else resume_failed
            if can_resume
            else start_followup
            if can_followup
            else automation_service.start_from_execute
        )
        try:
            prepare_data_repair = getattr(automation_service, "prepare_qualified_data_repair", None)
            if callable(prepare_data_repair) and not (can_resume or can_resume_waiting_for_core or can_followup):
                prepare_data_repair(
                    object_type=target["object_type"],
                    object_id=target["object_id"],
                    ticket_id=ticket["ticket_id"],
                    repair_id=repair_id,
                    qualification=qualification,
                    implementation_brief=brief,
                )
            started = start_method(
                object_type=target["object_type"],
                object_id=target["object_id"],
                implementation_brief=brief,
                webspace_id=_text(webspace_id) or "desktop",
                conversation_id=_text(conversation_id) or f"dev-ticket:{ticket['ticket_id']}",
                confirmed_technical_application_id=(
                    _text(confirmed_technical_application_id) or None
                ),
                execution_budget=bounded_budget,
                agent_profile=dict(agent_profile) if isinstance(agent_profile, Mapping) else None,
                mcp=dict(mcp) if isinstance(mcp, Mapping) else None,
                links=automation_links,
            )
        except Exception as exc:
            # Handoff is durable before Automation is queued. Reconcile a
            # failed session when available, then always release the user
            # ticket from the misleading in_builder state.
            try:
                self.sync_builder_repair(
                    ticket["ticket_id"],
                    repair_id=repair_id,
                    actor=_text(actor) or "builder.automation",
                    repair_service=service,
                    automation_service=automation_service,
                )
            except Exception:
                _log.exception(
                    "failed to reconcile autonomous Builder launch ticket=%s repair=%s",
                    ticket["ticket_id"],
                    repair_id,
                )
            try:
                service.transition_work_item(
                    repair_id,
                    status="failed",
                    actor=_text(actor) or "builder.automation",
                    reason=f"automation_start:{type(exc).__name__}",
                )
            except Exception:
                _log.exception(
                    "failed to mark autonomous Builder launch repair failed ticket=%s repair=%s",
                    ticket["ticket_id"],
                    repair_id,
                )
            self._release_builder_start_failure(
                ticket["ticket_id"],
                repair_id=repair_id,
                actor=_text(actor) or "builder.automation",
                error_type=type(exc).__name__,
            )
            raise
        correlated, correlation = _automation_matches_work(
            started,
            ticket_ids=[ticket["ticket_id"]],
            repair_id=repair_id,
        )
        if not correlated:
            raise RuntimeError(
                "Builder automation returned a task that is not correlated with "
                f"the requested Dev Ticket repair: {correlation}"
            )
        linked_repair = service.link_automation(repair_id, automation=started, actor=_text(actor) or "builder")
        linked_ticket = self._link_builder_automation(
            ticket["ticket_id"],
            repair_id=repair_id,
            automation=started,
            actor=_text(actor) or "builder",
        )
        sync = self.sync_builder_repair(
            ticket["ticket_id"],
            repair_id=repair_id,
            actor=_text(actor) or "builder.automation",
            repair_service=service,
            automation_service=automation_service,
        )
        return {
            "ok": True,
            "started": True,
            "ticket": sync.get("ticket") or linked_ticket,
            "repair": sync.get("repair") or linked_repair,
            "automation": sync.get("automation") or started,
            "sync": sync,
            "development_source": development_source,
            "materialization": materialization,
            "qualification": qualification,
        }

    def autonomous_repair_qualification(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        return _autonomous_repair_qualification(ticket)

    def plan_builder_package(
        self,
        ticket_ids: Sequence[str],
        *,
        actor: str,
        repair_service: BuilderRepairService | None = None,
        execution_budget: Mapping[str, Any] | None = None,
        workspace_service: Any | None = None,
        source_strategy: str | None = None,
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(_text(item) for item in ticket_ids if _text(item)))
        if not ids:
            raise ValueError("Builder package requires ticket_ids")
        if len(ids) > 12:
            raise ValueError("Builder package supports at most 12 tickets")
        tickets: list[dict[str, Any]] = []
        for ticket_id in ids:
            ticket = self.get_ticket(ticket_id)
            if not ticket:
                raise KeyError(ticket_id)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError(f"terminal Dev Ticket cannot enter Builder package: {ticket_id}")
            tickets.append(ticket)

        targets = [_automation_target_from_ticket(ticket) for ticket in tickets]
        target_keys = {(item["object_type"], item["object_id"]) for item in targets}
        if len(target_keys) != 1:
            raise ValueError("Builder package tickets must target one skill or scenario")
        target = targets[0]
        project_identity = _project_identity_for_package(tickets)
        qualification_candidates: dict[str, dict[str, Any]] = {}
        qualifications = [_autonomous_repair_qualification(ticket) for ticket in tickets]
        for index, (ticket, qualification) in enumerate(
            zip(tickets, qualifications, strict=True)
        ):
            if qualification.get("ready") is True:
                continue
            prepared = self.prepare_builder_repair_qualification(
                ticket["ticket_id"],
                actor=_text(actor) or "builder.qualifier",
                apply=False,
            )
            candidate = _mapping(prepared.get("qualification_candidate"))
            qualification_candidates[ticket["ticket_id"]] = candidate
            if (
                candidate.get("ready") is not True
                or _text(candidate.get("confidence")) != "high"
            ):
                continue
            applied = self.prepare_builder_repair_qualification(
                ticket["ticket_id"],
                actor=_text(actor) or "builder.qualifier",
                apply=True,
                expected_revision=int(ticket.get("revision") or 1),
            )
            tickets[index] = _mapping(applied.get("ticket")) or ticket
            qualifications[index] = _mapping(
                applied.get("autonomous_repair_qualification")
            )
        missing = [
            _text(ticket.get("ticket_id"))
            for ticket, qualification in zip(tickets, qualifications, strict=True)
            if qualification.get("ready") is not True
        ]
        if missing:
            return {
                "ok": True,
                "ready": False,
                "status": "qualification_required",
                "ticket_ids": ids,
                "unqualified_ticket_ids": missing,
                "qualification_candidates": qualification_candidates,
                "target": target,
                "repair": None,
            }

        project_resolution: dict[str, Any] = {}
        source_materialization: dict[str, Any] = {}
        if not project_identity:
            if workspace_service is None:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                workspace_service = BuilderWorkspaceService.from_context()
            project_resolution = _mapping(
                workspace_service.ensure_owning_dev_project(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    actor=_text(actor) or "builder.qualifier",
                )
            )
            if _text(project_resolution.get("status")) not in {
                "created",
                "source_available",
            }:
                strategy = _text(source_strategy)
                if strategy == "materialize_dev_source":
                    source_materialization = _mapping(
                        workspace_service.materialize_dev_source(
                            kind=target["object_type"],
                            artifact_id=target["object_id"],
                            project_id=_text(project_resolution.get("project_id"))
                            or None,
                        )
                    )
                elif strategy == "create_local_fork":
                    source_materialization = _mapping(
                        workspace_service.create_local_fork(
                            kind=target["object_type"],
                            artifact_id=target["object_id"],
                            project_id=_text(project_resolution.get("project_id"))
                            or None,
                            actor=_text(actor) or "builder.qualifier",
                        )
                    )
                elif strategy:
                    raise ValueError(
                        "Builder package source_strategy must be "
                        "materialize_dev_source or create_local_fork"
                    )
                if source_materialization:
                    project_resolution = _mapping(
                        workspace_service.ensure_owning_dev_project(
                            kind=target["object_type"],
                            artifact_id=target["object_id"],
                            actor=_text(actor) or "builder.qualifier",
                        )
                    )
            if _text(project_resolution.get("status")) not in {
                "created",
                "source_available",
            }:
                return {
                    "ok": True,
                    "ready": False,
                    "status": "owning_project_required",
                    "ticket_ids": ids,
                    "target": target,
                    "project_resolution": project_resolution,
                    "source_materialization": source_materialization,
                    "repair": None,
                }
            project_id = _text(project_resolution.get("project_id"))
            if not project_id:
                raise ValueError("owning DEV Project resolution returned no project_id")
            project_evidence = [
                {
                    "type": "project_manifest",
                    "id": _text(project_resolution.get("project_ref"))
                    or f"project:{project_id}",
                    "path": _text(project_resolution.get("manifest_path")) or None,
                    "status": _text(project_resolution.get("status")),
                }
            ]
            tickets = [
                self.bind_ticket_project_scope(
                    ticket["ticket_id"],
                    project_id=project_id,
                    actor=_text(actor) or "builder.qualifier",
                    evidence_refs=project_evidence,
                    expected_revision=int(ticket.get("revision") or 1),
                )
                for ticket in tickets
            ]
            project_identity = _project_identity_for_package(tickets)

        target_files = list(
            dict.fromkeys(
                path
                for qualification in qualifications
                for path in qualification.get("target_files") or []
            )
        )
        if len(target_files) > 12:
            raise ValueError("Builder package exact file envelope exceeds 12 files; split the package")
        target_refs = list(
            dict.fromkeys(
                ref
                for qualification in qualifications
                for ref in qualification.get("target_refs") or []
            )
        )[:20]
        acceptance_checks = list(
            dict.fromkeys(
                check
                for qualification in qualifications
                for check in qualification.get("acceptance_checks") or []
            )
        )[:24]
        requires_root_mcp = any(
            qualification.get("requires_root_mcp") is True
            for qualification in qualifications
        )
        structured_edit_sets = [
            _mapping(_bounded_repair_hints(ticket).get("structured_edits"))
            for ticket in tickets
        ]
        package_structured_edits: dict[str, Any] = {}
        if all(
            _text(qualification.get("execution_route")) == "structured_edits"
            and structured_edit_sets[index]
            for index, qualification in enumerate(qualifications)
        ):
            package_structured_edits = _normalize_structured_edits(
                {
                    "schema": _STRUCTURED_EDIT_SCHEMA,
                    "operations": [
                        dict(operation)
                        for edit_set in structured_edit_sets
                        for operation in edit_set.get("operations") or []
                        if isinstance(operation, Mapping)
                    ],
                },
                target_files=target_files,
                strict=True,
            )
        source_preconditions = list(
            {
                _text(item.get("path")): dict(item)
                for qualification in qualifications
                for item in _sequence_of_mappings(
                    qualification.get("source_preconditions") or []
                )
                if _text(item.get("path"))
            }.values()
        )
        contract_paths = list(
            dict.fromkeys(
                path
                for qualification in qualifications
                for path in _mapping(qualification.get("contract_closure")).get(
                    "required_paths"
                )
                or []
                if _text(path)
            )
        )
        package_id = f"bpackage.{new_id()}"
        budget = dict(execution_budget) if isinstance(execution_budget, Mapping) else {
            "schema": "adaos.builder.execution_budget.v1",
            "source": "development_ticket.bounded_package",
            "max_tokens": min(
                60000,
                24000
                + 6000 * max(0, len(tickets) - 1)
                + 3000 * max(0, len(target_files) - 2),
            ),
            "token_budget_metric": "fresh_plus_output",
            "max_wall_seconds": min(1800, 720 + 180 * len(tickets)),
        }
        budget.setdefault(
            "max_billable_tokens",
            execution_billable_token_limit(budget),
        )
        repair_hints = {
            "profile": "project_batch",
            "change_summary": "\n".join(
                f"{index + 1}. {_text(ticket.get('summary'))}"
                for index, ticket in enumerate(tickets)
            ),
            "target_files": target_files,
            "target_refs": target_refs,
            "acceptance_checks": acceptance_checks,
            "max_changed_files": len(target_files),
            "requires_root_mcp": requires_root_mcp,
            "source_preconditions": source_preconditions,
        }
        if contract_paths:
            repair_hints["contract_closure"] = {
                "kind": "skill_public_tool_graph",
                "required_paths": contract_paths,
                "reason": "one or more packaged repairs cross the public skill tool graph",
            }
        if package_structured_edits:
            repair_hints["structured_edits"] = package_structured_edits
        execution_route = _builder_package_execution_route(
            target=target,
            target_files=target_files,
            qualifications=qualifications,
            repair_hints=repair_hints,
        )
        repair_hints["execution_route"] = execution_route
        repair_hints["model_call_expected"] = execution_route not in {
            "structured_edits",
            "validation_only",
        }
        if execution_route == "prototype_first":
            repair_hints["prototype_model_policy"] = "deterministic_first"
        source_refs = [
            {"type": "dev_ticket", "id": _text(ticket.get("ticket_id"))}
            for ticket in tickets
        ]
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        report = service.report(
            project_id=_text(project_identity.get("project_id")) or target["object_id"],
            signal_type="other",
            summary=f"Builder package for {len(tickets)} Dev Tickets",
            source_refs=source_refs,
            context={
                "package_id": package_id,
                "package": {
                    "schema": "adaos.builder.work_package.v1",
                    "package_id": package_id,
                    "ticket_ids": ids,
                    "target": target,
                    **project_identity,
                    "repair_hints": repair_hints,
                    "execution_budget": budget,
                    "planned_by": _text(actor) or "builder",
                    "planned_at": _now(),
                },
                "economic": {
                    "schema": "adaos.builder.codex_token_accounting.v1",
                    "subscription_resource": "codex.api.tokens",
                    "source_of_truth": "adaos.root_mgmnt.codex_usage_event.v1",
                    "usage_event_endpoint": "/hub/economic/codex/usage",
                    "required_for_statuses": ["succeeded", "failed", "errored", "cancelled"],
                },
            },
            dedup_key=f"builder-package:{package_id}",
        )
        repair = report["task"]
        linked = [
            self._link_builder_repair(
                ticket["ticket_id"],
                repair,
                mode="package",
                actor=_text(actor) or "builder",
            )
            for ticket in tickets
        ]
        return {
            "ok": True,
            "ready": True,
            "status": "planned",
            "package_id": package_id,
            "ticket_ids": ids,
            "target": target,
            **project_identity,
            "project_resolution": project_resolution,
            "source_materialization": source_materialization,
            "repair": repair,
            "tickets": linked,
            "repair_hints": repair_hints,
            "execution_budget": budget,
            "rollup": service.package_rollup(package_id),
        }

    def start_autonomous_package(
        self,
        package_id: str,
        *,
        actor: str,
        repair_service: BuilderRepairService | None = None,
        automation_service: Any | None = None,
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        confirmed_technical_application_id: str | None = None,
        source_strategy: str | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
        prototype_submitter: Callable[..., Mapping[str, Any]] | None = None,
        prototype_status_reader: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        matches = service.list(package_id=_text(package_id))
        if len(matches) != 1:
            raise KeyError(package_id)
        repair = matches[0]
        ticket_ids = [_text(item) for item in repair.get("ticket_ids") or [] if _text(item)]
        tickets = [self.get_ticket(ticket_id) for ticket_id in ticket_ids]
        if not tickets or any(ticket is None for ticket in tickets):
            raise ValueError("Builder package contains missing Dev Tickets")
        ticket_list = [dict(ticket) for ticket in tickets if isinstance(ticket, Mapping)]
        targets = [_automation_target_from_ticket(ticket) for ticket in ticket_list]
        if len({(item["object_type"], item["object_id"]) for item in targets}) != 1:
            raise ValueError("Builder package target changed since planning")
        target = targets[0]
        project_identity = _project_identity_for_package(ticket_list)
        development_source = development_source_options(_development_source_scope(ticket_list[0], target))
        materialization: dict[str, Any] | None = None
        if automation_service is None:
            from adaos.services.builder.automation import BuilderAutomationService

            automation_service = BuilderAutomationService.from_context()
        if development_source.get("status") == "needs_materialization":
            strategy = _text(source_strategy)
            workspace_service = getattr(automation_service, "workspace_service", None)
            if workspace_service is None:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                workspace_service = BuilderWorkspaceService.from_context()
            project_id = _project_id_for_materialization(ticket_list[0], development_source)
            if strategy == "materialize_dev_source":
                materialization = workspace_service.materialize_dev_source(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    project_id=project_id,
                )
            elif strategy == "create_local_fork":
                materialization = workspace_service.create_local_fork(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    project_id=project_id,
                    actor=_text(actor) or "builder",
                )
            else:
                raise ValueError(
                    "autonomous Builder package requires materialize_dev_source "
                    "or create_local_fork when source is missing"
                )
            if not materialization or materialization.get("ok") is False:
                raise ValueError("development source materialization failed")
            development_source = _mapping(materialization.get("development_source"))
            if not development_source:
                development_source = _mapping(
                    workspace_service.development_source_status(
                        kind=target["object_type"],
                        artifact_id=target["object_id"],
                        project_id=project_id,
                    )
                )
        if (
            development_source.get("status") == "source_available"
            and not _text(
                development_source.get("dev_source_path")
                or development_source.get("source_path")
            )
        ):
            prepared_source = self.prepare_builder_repair_qualification(
                ticket_list[0]["ticket_id"],
                actor=_text(actor) or "builder.qualifier",
                apply=False,
            )
            development_source = (
                _mapping(prepared_source.get("development_source"))
                or development_source
            )

        package = _mapping(_mapping(repair.get("context")).get("package"))
        planned_project_identity = {
            key: _text(package.get(key))
            for key in ("project_ref", "project_id")
            if _text(package.get(key))
        }
        if planned_project_identity != project_identity:
            raise ValueError("Builder package project scope changed since planning")
        budget = _mapping(package.get("execution_budget")) or dict(DEFAULT_AUTONOMOUS_REPAIR_BUDGET)
        budget.setdefault("token_budget_metric", "fresh_plus_output")
        repair_hints = _mapping(package.get("repair_hints"))
        qualifications = [_autonomous_repair_qualification(ticket) for ticket in ticket_list]
        execution_route = _builder_package_execution_route(
            target=target,
            target_files=repair_hints.get("target_files") or [],
            qualifications=qualifications,
            repair_hints=repair_hints,
        )
        workflow_head_method = getattr(
            automation_service,
            "current_workflow_head",
            None,
        )
        workflow_head = (
            _mapping(
                workflow_head_method(
                    object_type=target["object_type"],
                    object_id=target["object_id"],
                )
            )
            if callable(workflow_head_method)
            else {}
        )
        workflow_state = _text(workflow_head.get("state"))
        repair_id = _text(repair.get("repair_id"))
        prototype_link = _mapping(_mapping(repair.get("context")).get("prototype"))
        accepted_prototype_revision = _text(workflow_head.get("prototype_revision"))
        if (
            execution_route == "prototype_first"
            and workflow_state == "automation_ready"
            and workflow_head.get("prototype_accepted") is True
            and accepted_prototype_revision
            and _text(prototype_link.get("revision")) != accepted_prototype_revision
        ):
            # Prototype revisions may be accepted after a previous Automation
            # iteration returned development feedback.  Refresh the package
            # link from the authoritative workflow head before dispatch so the
            # next worker cannot receive stale Prototype identity.
            repair = service.link_prototype(
                repair_id,
                prototype={
                    "session_id": _text(prototype_link.get("session_id"))
                    or target["object_id"],
                    "status": "prototype_accepted",
                    "patch": {"change_id": workflow_head.get("change_set_id")},
                    "ui_revision": {"revision": accepted_prototype_revision},
                },
                actor=_text(actor) or "builder.prototype",
            )
            prototype_link = _mapping(_mapping(repair.get("context")).get("prototype"))
            ticket_list = [
                self._link_builder_prototype(
                    ticket_id,
                    repair_id=repair_id,
                    prototype=prototype_link,
                    actor=_text(actor) or "builder.prototype",
                )
                for ticket_id in ticket_ids
            ]
        package_prototype_ready = _prototype_ready_for_package(
            workflow_state,
            prototype_link,
            accepted_revision=accepted_prototype_revision,
        )
        prototype_message_id = f"m.{package_id}.prototype.request"
        workflow_source_message_ids = {
            _text(item)
            for item in workflow_head.get("source_message_ids") or []
            if _text(item)
        }
        resumable_package_prototype = bool(
            execution_route == "prototype_first"
            and workflow_state == "prototype_editing"
            and not prototype_link
            and prototype_message_id in workflow_source_message_ids
        )
        prototype_kwargs = {
            "object_id": target["object_id"],
            "project_id": _text(project_identity.get("project_id")) or target["object_id"],
            "package_id": _text(package_id),
            "repair_id": repair_id,
            "ticket_ids": ticket_ids,
            "webspace_id": _text(webspace_id) or "desktop",
            "conversation_id": _text(conversation_id)
            or f"dev-ticket-package:{package_id}",
        }
        if execution_route == "prototype_first" and not package_prototype_ready:
            if workflow_state == "prototype_editing" and prototype_link:
                reader = prototype_status_reader or _default_builder_prototype_status_reader
                prototype_session_id = _text(prototype_link.get("session_id"))
                if prototype_session_id:
                    try:
                        observed = reader(
                            session_id=prototype_session_id,
                            **prototype_kwargs,
                        )
                        if isinstance(observed, Mapping) and observed.get("ok") is not False:
                            repair = service.link_prototype(
                                repair_id,
                                prototype=observed,
                                actor=_text(actor) or "builder.prototype",
                            )
                            prototype_link = _mapping(
                                _mapping(repair.get("context")).get("prototype")
                            )
                            ticket_list = [
                                self._link_builder_prototype(
                                    ticket_id,
                                    repair_id=repair_id,
                                    prototype=prototype_link,
                                    actor=_text(actor) or "builder.prototype",
                                )
                                for ticket_id in ticket_ids
                            ]
                    except Exception:
                        _log.debug(
                            "failed to refresh Builder prototype package=%s repair=%s",
                            package_id,
                            repair_id,
                            exc_info=True,
                        )
                prototype_status = _text(prototype_link.get("status"))
                pending = prototype_status in {
                    "llm_pending",
                    "llm_submitting",
                    "queued",
                    "running",
                }
                return {
                    "ok": True,
                    "started": False,
                    "stage": "prototype",
                    "status": (
                        "prototype_pending" if pending else "prototype_acceptance_required"
                    ),
                    "package_id": _text(package_id),
                    "repair": repair,
                    "tickets": ticket_list,
                    "prototype": prototype_link,
                    "automation": None,
                    "development_source": development_source,
                    "materialization": materialization,
                    "rollup": service.package_rollup(_text(package_id)),
                }
            if workflow_state and workflow_state not in {
                "ready",
                "published",
                "cancelled",
                "superseded",
                "automation_ready",
            } and not resumable_package_prototype:
                return {
                    "ok": True,
                    "started": False,
                    "stage": "prototype",
                    "status": "builder_workflow_busy",
                    "reason": f"prototype cannot start while Builder workflow is {workflow_state}",
                    "package_id": _text(package_id),
                    "repair": repair,
                    "tickets": ticket_list,
                    "prototype": prototype_link or None,
                    "automation": None,
                    "workflow_head": workflow_head,
                    "development_source": development_source,
                    "materialization": materialization,
                    "rollup": service.package_rollup(_text(package_id)),
                }
        source_preconditions = _validate_repair_source_preconditions(
            repair_hints,
            development_source=development_source,
            target=target,
        )
        if execution_route == "prototype_first" and package_prototype_ready:
            source_preconditions = {
                "schema": "adaos.builder.source_precondition_validation.v1",
                "status": "superseded_by_accepted_prototype",
                "ok": True,
                "checks": [],
            }
        if source_preconditions.get("ok") is not True:
            service.transition_work_item(
                repair_id,
                status="blocked",
                actor=_text(actor) or "builder.automation",
                reason="qualified_source_changed",
                evidence_refs=[
                    {
                        "type": "source_precondition_validation",
                        "status": source_preconditions.get("status"),
                    }
                ],
            )
            released_tickets = [
                self._release_builder_start_failure(
                    ticket_id,
                    repair_id=repair_id,
                    actor=_text(actor) or "builder.automation",
                    error_type="SourceChanged",
                )
                for ticket_id in ticket_ids
            ]
            return {
                "ok": True,
                "started": False,
                "status": "source_changed",
                "reason": "builder_requalification_required",
                "package_id": _text(package_id),
                "repair": next(
                    iter(service.list(package_id=_text(package_id))),
                    repair,
                ),
                "tickets": released_tickets,
                "source_preconditions": source_preconditions,
                "development_source": development_source,
                "materialization": materialization,
                "rollup": service.package_rollup(_text(package_id)),
            }
        if execution_route == "prototype_first" and not package_prototype_ready:
            submit = prototype_submitter or _default_builder_prototype_submitter
            try:
                submitted = submit(
                    instruction="\n".join(
                        _text(ticket.get("summary")) for ticket in ticket_list
                    ),
                    **prototype_kwargs,
                )
                if not isinstance(submitted, Mapping) or submitted.get("ok") is not True:
                    detail = (
                        _text(submitted.get("detail") or submitted.get("error"))
                        if isinstance(submitted, Mapping)
                        else "prototype submitter returned a non-object result"
                    )
                    raise RuntimeError(detail or "Builder prototype submission failed")
                linked_repair = service.link_prototype(
                    repair_id,
                    prototype=submitted,
                    actor=_text(actor) or "builder.prototype",
                )
                prototype_link = _mapping(
                    _mapping(linked_repair.get("context")).get("prototype")
                )
                ticket_list = [
                    self._link_builder_prototype(
                        ticket_id,
                        repair_id=repair_id,
                        prototype=prototype_link,
                        actor=_text(actor) or "builder.prototype",
                    )
                    for ticket_id in ticket_ids
                ]
            except Exception as exc:
                release_failed_head = getattr(
                    automation_service,
                    "release_failed_prototype_launch",
                    None,
                )
                if callable(release_failed_head):
                    try:
                        release_failed_head(
                            object_type=target["object_type"],
                            object_id=target["object_id"],
                            package_id=_text(package_id),
                            actor=_text(actor) or "builder.prototype",
                            reason=f"prototype_start:{type(exc).__name__}",
                        )
                    except Exception:
                        _log.exception(
                            "failed to release Builder prototype head package=%s repair=%s",
                            package_id,
                            repair_id,
                        )
                try:
                    service.transition_work_item(
                        repair_id,
                        status="failed",
                        actor=_text(actor) or "builder.prototype",
                        reason=f"prototype_start:{type(exc).__name__}",
                    )
                except Exception:
                    _log.exception(
                        "failed to mark Builder prototype launch failed package=%s repair=%s",
                        package_id,
                        repair_id,
                    )
                for ticket_id in ticket_ids:
                    self._release_builder_start_failure(
                        ticket_id,
                        repair_id=repair_id,
                        actor=_text(actor) or "builder.prototype",
                        error_type=type(exc).__name__,
                    )
                raise
            prototype = _mapping(_mapping(linked_repair.get("context")).get("prototype"))
            pending = _text(prototype.get("status")) in {
                "llm_pending",
                "llm_submitting",
                "queued",
                "running",
            }
            return {
                "ok": True,
                "started": True,
                "stage": "prototype",
                "status": "prototype_pending" if pending else "prototype_acceptance_required",
                "package_id": _text(package_id),
                "repair": linked_repair,
                "tickets": ticket_list,
                "prototype": prototype,
                "automation": None,
                "development_source": development_source,
                "materialization": materialization,
                "source_preconditions": source_preconditions,
                "rollup": service.package_rollup(_text(package_id)),
            }
        brief = _autonomous_package_brief(ticket_list, repair, target=target)
        links = {
            "development_ticket_id": ticket_ids[0],
            "development_ticket_ids": ticket_ids,
            "builder_repair_id": _text(repair.get("repair_id")),
            "builder_package_id": _text(package_id),
            **{
                f"development_ticket_{key}": value
                for key, value in project_identity.items()
            },
            "development_source_materialization": _source_materialization_ref(
                materialization
            ),
            "source_precondition_validation": source_preconditions,
        }
        current = automation_service.status(
            object_type=target["object_type"],
            object_id=target["object_id"],
        )
        current_session = _automation_session(current)
        current_links = _mapping(current_session.get("links"))
        followup_state_ready = not workflow_state or workflow_state in {
            "verification",
            "trial_ready",
            "trial_review",
            "publication_ready",
            "reconciliation_required",
        }
        resume = (
            _text(current_session.get("status")) == "failed"
            and _text(current_links.get("builder_package_id")) == _text(package_id)
            and _automation_session_matches_prototype(
                current_session,
                accepted_prototype_revision,
            )
            and callable(getattr(automation_service, "resume_failed_dev_ticket_repair", None))
        )
        followup = (
            not resume
            and _text(current_session.get("status")) == "completed"
            and bool(_mapping(current_session.get("completion_readiness")).get("ok"))
            and bool(
                _mapping(
                    _mapping(current_session.get("completion_readiness")).get("aprobation")
                ).get("ok")
            )
            and followup_state_ready
            and callable(getattr(automation_service, "start_followup_dev_ticket_repair", None))
        )
        start_method = (
            automation_service.resume_failed_dev_ticket_repair
            if resume
            else automation_service.start_followup_dev_ticket_repair
            if followup
            else automation_service.start_from_execute
        )
        try:
            started = start_method(
                object_type=target["object_type"],
                object_id=target["object_id"],
                implementation_brief=brief,
                webspace_id=_text(webspace_id) or "desktop",
                conversation_id=_text(conversation_id) or f"dev-ticket-package:{package_id}",
                confirmed_technical_application_id=(
                    _text(confirmed_technical_application_id) or None
                ),
                execution_budget=budget,
                agent_profile=dict(agent_profile) if isinstance(agent_profile, Mapping) else None,
                mcp=dict(mcp) if isinstance(mcp, Mapping) else None,
                links=links,
            )
        except Exception as exc:
            repair_id = _text(repair.get("repair_id"))
            try:
                service.transition_work_item(
                    repair_id,
                    status="failed",
                    actor=_text(actor) or "builder.automation",
                    reason=f"automation_start:{type(exc).__name__}",
                )
            except Exception:
                _log.exception(
                    "failed to mark autonomous Builder package launch failed package=%s repair=%s",
                    package_id,
                    repair_id,
                )
            for ticket_id in ticket_ids:
                try:
                    self._release_builder_start_failure(
                        ticket_id,
                        repair_id=repair_id,
                        actor=_text(actor) or "builder.automation",
                        error_type=type(exc).__name__,
                    )
                except Exception:
                    _log.exception(
                        "failed to release Dev Ticket after Builder package launch failure "
                        "ticket=%s repair=%s",
                        ticket_id,
                        repair_id,
                    )
            raise
        correlated, correlation = _automation_matches_work(
            started,
            ticket_ids=ticket_ids,
            repair_id=_text(repair.get("repair_id")),
        )
        if not correlated:
            raise RuntimeError(
                "Builder automation returned a task that is not correlated with "
                f"the requested Dev Ticket package: {correlation}"
            )
        linked_repair = service.link_automation(
            repair["repair_id"],
            automation=started,
            actor=_text(actor) or "builder",
        )
        linked_tickets = [
            self._link_builder_automation(
                ticket_id,
                repair_id=repair["repair_id"],
                automation=started,
                actor=_text(actor) or "builder",
            )
            for ticket_id in ticket_ids
        ]
        return {
            "ok": True,
            "started": True,
            "package_id": _text(package_id),
            "repair": linked_repair,
            "tickets": linked_tickets,
            "automation": started,
            "development_source": development_source,
            "materialization": materialization,
            "rollup": service.package_rollup(_text(package_id)),
        }

    def builder_target(self, ticket_id: str) -> dict[str, str]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        return _automation_target_from_ticket(ticket)

    def sync_builder_repair(
        self,
        ticket_id: str,
        *,
        actor: str,
        repair_id: str | None = None,
        repair_service: BuilderRepairService | None = None,
        automation_service: Any | None = None,
        automation_result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        target = _automation_target_from_ticket(ticket)
        linked_repair_id = _text(repair_id) or self._latest_repair_id(ticket)
        if not linked_repair_id:
            return {"ok": True, "synchronized": False, "reason": "builder_repair_not_linked", "ticket": ticket}
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        if isinstance(automation_result, Mapping):
            status_result = dict(automation_result)
        else:
            if automation_service is None:
                from adaos.services.builder.automation import BuilderAutomationService

                automation_service = BuilderAutomationService.from_context()
            status_result = automation_service.status(
                object_type=target["object_type"],
                object_id=target["object_id"],
            )
        if not status_result.get("ok"):
            return {
                "ok": True,
                "synchronized": False,
                "reason": status_result.get("error") or "automation_session_not_found",
                "ticket": ticket,
                "automation": status_result,
            }
        correlated, correlation = _automation_matches_work(
            status_result,
            ticket_ids=[ticket["ticket_id"]],
            repair_id=linked_repair_id,
        )
        if not correlated:
            return {
                "ok": True,
                "synchronized": False,
                "resolved": False,
                "reason": "automation_correlation_mismatch",
                "correlation": correlation,
                "ticket": ticket,
                "automation": status_result,
            }
        repair = service.link_automation(linked_repair_id, automation=status_result, actor=_text(actor) or "builder.automation")
        updated = self._link_builder_automation(
            ticket["ticket_id"],
            repair_id=linked_repair_id,
            automation=status_result,
            actor=_text(actor) or "builder.automation",
        )
        projection = _automation_projection(status_result)
        status = _text(projection.get("status"))
        repair_task_ids = _repair_automation_task_ids(updated, linked_repair_id)
        refs = _automation_evidence_refs(
            status_result,
            repair_id=linked_repair_id,
            allowed_task_ids=repair_task_ids,
        )
        escalations = _automation_development_escalations(status_result)
        escalation_task = _automation_task(status_result)
        if _text(escalation_task.get("status")) == "completed" and escalations:
            task = escalation_task
            session = _automation_session(status_result)
            task_id = _text(task.get("task_id") or session.get("current_task_id"))
            blocked_ticket_ids = [
                _text(item)
                for item in correlation.get("observed_ticket_ids") or []
                if _text(item)
            ] or [ticket["ticket_id"]]
            core_requests: list[dict[str, Any]] = []
            for index, escalation in enumerate(escalations):
                escalation_ref = {
                    "type": "builder_development_escalation",
                    "id": f"{task_id or linked_repair_id}:{index}",
                    "task_id": task_id or None,
                    "repair_id": linked_repair_id,
                    "kind": escalation["kind"],
                    "status": "accepted",
                }
                core_requests.append(
                    self.create_core_capability_request(
                        summary=escalation["summary"],
                        component_ref=escalation["component_ref"],
                        desired_contract=escalation["desired_contract"],
                        actor=_text(actor) or "builder.automation",
                        impact=escalation["impact"],
                        motivation=escalation.get("motivation") or "",
                        observed_limitation=escalation["observed_limitation"],
                        rejected_workarounds=escalation.get("rejected_workarounds") or [],
                        blocked_ticket_ids=blocked_ticket_ids,
                        evidence_refs=_merge_refs(refs, [escalation_ref]),
                        metadata={
                            "development_escalation_kind": escalation["kind"],
                            "source_task_id": task_id or None,
                            "builder_repair_id": linked_repair_id,
                            "builder_session_id": _text(session.get("session_id")) or None,
                        },
                        source="builder_automation",
                        origin_scope={
                            "type": "builder",
                            "surface": "automation",
                            "id": _text(session.get("session_id"))
                            or task_id
                            or linked_repair_id,
                        },
                    )
                )
            repair_evidence = _merge_refs(
                refs,
                [
                    {
                        "type": "dev_ticket",
                        "id": request["ticket"]["ticket_id"],
                        "relation": "blocked_by",
                        "status": request["ticket"].get("status"),
                    }
                    for request in core_requests
                ],
            )
            repair = service.transition_work_item(
                linked_repair_id,
                status="blocked",
                actor=_text(actor) or "builder.automation",
                reason="waiting_for_core_capability",
                evidence_refs=repair_evidence,
            )
            updated = self.get_ticket(ticket["ticket_id"]) or updated
            return {
                "ok": True,
                "synchronized": True,
                "resolved": False,
                "escalated": True,
                "reason": "waiting_for_core",
                "ticket": updated,
                "repair": repair,
                "automation": status_result,
                "evidence_refs": repair_evidence,
                "core_requests": core_requests,
            }
        if (
            status == "completed"
            and _automation_has_validation_evidence(status_result)
            and _text(updated.get("status")) not in {"resolved", "verified", "closed"}
        ):
            result = _automation_task(status_result).get("result")
            result = dict(result) if isinstance(result, Mapping) else _mapping(_automation_session(status_result).get("last_result"))
            resolved = self.record_resolution(
                ticket["ticket_id"],
                evidence_refs=refs,
                actor=_text(actor) or "builder.automation",
                resolved_by_overlay=_text(result.get("commit_hash") or projection.get("result_branch")) or None,
                repair_service=service,
                repair_id=linked_repair_id,
            )
            updated = resolved["ticket"]
            resolved_repairs = service.list(status="resolved")
            repair = next((item for item in resolved_repairs if _text(item.get("repair_id")) == linked_repair_id), None)
            if repair is None:
                repair = service.link_automation(
                    linked_repair_id,
                    automation=status_result,
                    actor=_text(actor) or "builder.automation",
                )
            return {
                "ok": True,
                "synchronized": True,
                "resolved": True,
                "ticket": updated,
                "repair": repair,
                "automation": status_result,
                "evidence_refs": refs,
            }
        if (
            status == "completed"
            and _automation_has_validation_evidence(status_result)
            and _text(updated.get("status")) in {"resolved", "verified", "closed"}
        ):
            updated = self._reconcile_builder_resolution_evidence(
                updated["ticket_id"],
                repair_id=linked_repair_id,
                evidence_refs=refs,
                actor=_text(actor) or "builder.automation",
            )
        return {
            "ok": True,
            "synchronized": True,
            "resolved": False,
            "ticket": updated,
            "repair": repair,
            "automation": status_result,
            "evidence_refs": refs,
        }

    def _reconcile_builder_resolution_evidence(
        self,
        ticket_id: str,
        *,
        repair_id: str,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(evidence_refs)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            allowed_task_ids = set(_repair_automation_task_ids(ticket, repair_id))

            def _without_unrelated_usage(items: Any) -> list[dict[str, Any]]:
                return [
                    dict(ref)
                    for ref in _sequence_of_mappings(items or [])
                    if not (
                        _text(ref.get("type")) == "codex_usage"
                        and _text(ref.get("repair_id")) == _text(repair_id)
                        and _text(ref.get("task_id"))
                        and _text(ref.get("task_id")) not in allowed_task_ids
                    )
                ]

            changed = False
            evidence = _merge_refs(
                _without_unrelated_usage(ticket.get("evidence_refs")),
                refs,
            )
            if evidence != ticket.get("evidence_refs"):
                ticket["evidence_refs"] = evidence
                changed = True
            closure = _mapping(ticket.get("closure"))
            if closure and _text(closure.get("repair_id")) == _text(repair_id):
                closure_refs = _merge_refs(
                    _without_unrelated_usage(closure.get("evidence_refs")),
                    refs,
                )
                if closure_refs != closure.get("evidence_refs"):
                    closure["evidence_refs"] = closure_refs
                    ticket["closure"] = closure
                    changed = True
            if changed:
                now = _now()
                ticket["updated_at"] = now
                self._append_history(
                    ticket,
                    {
                        "kind": "builder_evidence_reconciled",
                        "repair_id": _text(repair_id),
                        "actor": _text(actor) or "builder.automation",
                        "recorded_at": now,
                    },
                )
                for signal_id in ticket.get("signal_ids") or []:
                    signal = state["signals"].get(signal_id)
                    if signal:
                        signal["evidence_refs"] = _merge_refs(
                            _without_unrelated_usage(signal.get("evidence_refs")),
                            refs,
                        )
                        signal["updated_at"] = now
                        self._validate_signal(signal)
                self._validate_ticket(ticket)
                self._write(state)
            return _normalized_ticket(ticket)

    def create_core_capability_request(
        self,
        *,
        summary: str,
        component_ref: str,
        desired_contract: str,
        actor: str,
        impact: str = "contract_gap",
        motivation: str = "",
        observed_limitation: str = "",
        rejected_workarounds: Sequence[Mapping[str, Any]] = (),
        blocked_ticket_ids: Sequence[str] = (),
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        target_scope: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        status: str = "proposed",
        source: str = "builder_intake",
        origin_scope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = _text(summary)
        component = _text(component_ref)
        contract = _text(desired_contract)
        if not text:
            raise ValueError("core capability request summary is required")
        if not component:
            raise ValueError("core capability request component_ref is required")
        if not contract:
            raise ValueError("core capability request desired_contract is required")
        impact_token = (_text(impact) or "contract_gap").lower()
        if impact_token not in CORE_IMPACT_CLASSES:
            raise ValueError(f"unsupported core impact: {impact_token}")
        source_token = _text(source) or "builder_intake"
        target = _mapping(target_scope) or {
            "type": "core",
            "id": component.split(":", 1)[1] if component.startswith("core:") else component,
            "component_ref": component,
            "source": "core",
        }
        blocked_ids = [_text(item) for item in blocked_ticket_ids or [] if _text(item)]
        meta = {
            **_mapping(metadata),
            "actor": _text(actor) or "builder",
            "impact": impact_token,
            "motivation": _text(motivation) or None,
            "desired_contract": contract,
            "observed_limitation": _text(observed_limitation) or None,
            "rejected_workarounds": _sequence_of_mappings(rejected_workarounds),
            "blocked_ticket_ids": blocked_ids,
        }
        relation_refs = [
            {"type": "blocks", "relation": "blocks", "target_ref": f"dticket:{ticket_id}", "ticket_id": ticket_id}
            for ticket_id in blocked_ids
        ]
        signal_result = self.capture_signal(
            kind="core_capability_request",
            summary=text,
            owner_scope={"type": "workspace", "id": "local"},
            origin_scope=_mapping(origin_scope)
            or {
                "type": "builder",
                "surface": "core_capability_request",
                "id": _text(actor) or "builder",
            },
            target_scope=target,
            severity="high" if impact_token == "blocker" else "medium",
            blocking=impact_token == "blocker",
            source=source_token,
            dedup_key=_fingerprint("core-capability", component, contract.lower(), blocked_ids),
            evidence_refs=evidence_refs,
            metadata=meta,
            policy=policy,
            owner_area="core",
            component_ref=component,
            relation_refs=relation_refs,
        )
        ticket_result = self.ensure_ticket_for_signal(
            signal_result["signal"],
            kind="core_capability_request",
            status="accepted" if impact_token == "blocker" and status == "proposed" else status,
            source=source_token,
            dedup_key=signal_result["signal"]["dedup_key"],
            metadata=meta,
            policy=policy,
            owner_area="core",
            component_ref=component,
            relation_refs=relation_refs,
        )
        core_ticket = ticket_result["ticket"]
        blocked = [
            self.block_ticket_by_core(ticket_id, core_ticket["ticket_id"], actor=_text(actor) or "builder")
            for ticket_id in blocked_ids
        ]
        lifecycle = self.transition_core_ticket(
            core_ticket["ticket_id"],
            transition="created",
            actor=_text(actor) or "builder",
            publish_pending_actions=False,
        )
        return {
            "ok": True,
            "signal": signal_result["signal"],
            "ticket": lifecycle["ticket"],
            "blocked_tickets": blocked,
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
            "lifecycle_event": lifecycle["event"],
        }

    def escalate_platform_defect(
        self,
        ticket_id: str,
        *,
        component_ref: str,
        expected_behavior: str,
        observed_behavior: str,
        failure_class: str,
        actor: str,
        confidence: float,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Route a qualified non-project defect to the shared Core/Client backlog."""

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        component = _text(component_ref)
        expected = _text(expected_behavior)
        observed = _text(observed_behavior)
        defect_class = _text(failure_class)
        if not component.startswith("core:"):
            raise ValueError("platform defect component_ref must start with core:")
        if not expected or not observed or not defect_class:
            raise ValueError(
                "platform defect requires expected_behavior, observed_behavior, and failure_class"
            )
        bounded_confidence = max(0.0, min(1.0, float(confidence)))
        if bounded_confidence < 0.85:
            raise ValueError("platform defect confidence is insufficient for automatic escalation")
        result = self.create_core_capability_request(
            summary=_text(ticket.get("summary")),
            component_ref=component,
            desired_contract=expected,
            actor=_text(actor) or "builder.platform_router",
            impact="compatibility_debt",
            motivation=(
                "The behavior belongs to a shared AdaOS component and must not be "
                "reimplemented inside a generated application."
            ),
            observed_limitation=observed,
            blocked_ticket_ids=[ticket_id],
            evidence_refs=_merge_refs(
                ticket.get("evidence_refs") or [], evidence_refs
            ),
            metadata={
                "development_escalation_kind": "platform_defect",
                "failure_class": defect_class,
                "classification_confidence": bounded_confidence,
                "source_ticket_id": ticket_id,
                "source_component_ref": ticket.get("component_ref"),
            },
            source="platform_defect_router",
            origin_scope={
                "type": "builder",
                "surface": "platform_defect_router",
                "id": _text(actor) or "builder.platform_router",
            },
        )
        return {**result, "platform_defect": True, "source_ticket_id": ticket_id}

    def report_artifact_activation_observation(
        self,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        status = _text(observation.get("status")).lower()
        if status != "failed":
            return {"ok": True, "reported": False, "reason": status or "status_missing"}

        error = _text(observation.get("error")) or "delayed activation verification failed"
        component_match = re.search(
            r"materialized package file (?:size |digest )?(?:changed|missing):\s*((?:skill|scenario):[^:\s]+)",
            error,
            flags=re.IGNORECASE,
        )
        affected_component_ref = component_match.group(1) if component_match else ""
        receipt = observation.get("receipt")
        raw_failures = receipt.get("failures") if isinstance(receipt, Mapping) else None
        affected_component_refs = sorted(
            {
                _text(item.get("package"))
                for item in raw_failures or ()
                if isinstance(item, Mapping) and _text(item.get("package"))
            }
        )
        if affected_component_ref and affected_component_ref not in affected_component_refs:
            affected_component_refs.insert(0, affected_component_ref)
        if not affected_component_ref and affected_component_refs:
            affected_component_ref = affected_component_refs[0]
        observation_id = _text(observation.get("observation_id"))
        evidence = {
            "type": "runtime_guard",
            "code": "artifact.workspace_lock_verification_failed",
            "observation_id": observation_id or None,
            "expected_lock_digest": _text(observation.get("expected_lock_digest")) or None,
            "observed_lock_digest": _text(observation.get("observed_lock_digest")) or None,
            "affected_component_ref": affected_component_ref or None,
            "affected_component_refs": affected_component_refs,
            "failed_component_count": len(affected_component_refs),
            "error": error,
        }
        result = self.create_core_capability_request(
            summary="Workspace content diverged from its immutable package after activation",
            component_ref="core:artifact-pipeline.workspace-lock",
            desired_contract=(
                "Materialized Workspace components remain byte-identical to the packages "
                "selected by the active WorkspaceLock."
            ),
            actor="artifact.activation",
            impact="compatibility_debt",
            motivation=(
                "Activation integrity must remain explainable and cannot silently accept "
                "mutable installed source."
            ),
            observed_limitation=error,
            evidence_refs=[evidence],
            metadata={
                "producer": "artifact_activation_observation",
                "observation_id": observation_id or None,
                "affected_component_ref": affected_component_ref or None,
                "affected_component_refs": affected_component_refs,
                "failed_component_count": len(affected_component_refs),
                "expected_lock_digest": _text(observation.get("expected_lock_digest")) or None,
                "observed_lock_digest": _text(observation.get("observed_lock_digest")) or None,
            },
            policy={
                "blocking": False,
                "run_policy": "degrade",
                "design_time_fixable": True,
                "autonomous_repair_eligible": False,
            },
            source="artifact_activation_guard",
            origin_scope={
                "type": "runtime",
                "surface": "artifact_activation_observation",
                "id": observation_id or "delayed_verification",
            },
        )
        return {**result, "reported": True}

    def report_publication_gate_failure(
        self,
        *,
        component_type: str,
        component_id: str,
        gate: str,
        error: str,
        actor: str = "builder.automation",
        related_ticket_ids: Sequence[str] = (),
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        candidate_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        webspace_id: str = "desktop",
        source: str = "builder_publication_gate",
        producer: str = "builder_publication_gate",
        publication_required: bool = True,
        autonomous_repair_eligible: bool = True,
        design_time_fixable: bool = True,
        dedup_namespace: str = "publication-gate",
        metadata: Mapping[str, Any] | None = None,
        summary: str | None = None,
        origin_type: str = "builder",
        origin_surface: str = "publication_gate",
        target_source: str = "dev",
        run_policy: str = "block_publication",
    ) -> dict[str, Any]:
        """Capture a project-owned blocker raised by a release gate.

        Candidate and task identities remain evidence, but are intentionally
        excluded from deduplication so retries cannot flood the inbox.
        """

        kind = _text(component_type).lower().rstrip("s")
        identifier = _text(component_id)
        gate_token = _text(gate).lower() or "publication"
        error_text = _text(error) or "publication gate failed"
        if kind not in {"skill", "scenario"}:
            raise ValueError("publication gate component_type must be skill or scenario")
        if not identifier:
            raise ValueError("publication gate component_id is required")

        normalized_error = _normalized_failure_text(error_text)
        source_token = _text(source) or "builder_publication_gate"
        producer_token = _text(producer) or source_token
        component_ref = f"{kind}:{identifier}"
        linked_ids = list(
            dict.fromkeys(_text(item) for item in related_ticket_ids if _text(item))
        )
        evidence_type = (
            "test"
            if gate_token in {"tests", "test", "validation", "consumer_acceptance"}
            else "runtime_guard"
        )
        gate_evidence = {
            "type": evidence_type,
            "id": f"{component_ref}:{gate_token}",
            "status": "failed",
            "gate": gate_token,
            "error": error_text,
            "candidate_id": _text(candidate_id) or None,
            "task_id": _text(task_id) or None,
            "session_id": _text(session_id) or None,
            "webspace_id": _text(webspace_id) or "desktop",
        }
        gate_metadata = {
            **_mapping(metadata),
            "producer": producer_token,
            "actor": _text(actor) or "builder.automation",
            "gate": gate_token,
            "error": error_text,
            "candidate_id": _text(candidate_id) or None,
            "task_id": _text(task_id) or None,
            "session_id": _text(session_id) or None,
            "webspace_id": _text(webspace_id) or "desktop",
            "related_ticket_ids": linked_ids,
        }
        dedup_key = _fingerprint(
            _text(dedup_namespace) or "publication-gate",
            component_ref,
            gate_token,
            normalized_error,
        )
        signal_result = self.capture_signal(
            kind="runtime_failure",
            summary=_text(summary)
            or f"{kind.capitalize()} {identifier} failed the {gate_token} publication gate",
            owner_scope={"type": "workspace", "id": "local"},
            origin_scope={
                "type": _text(origin_type) or "builder",
                "surface": _text(origin_surface) or "publication_gate",
                "id": _text(session_id) or _text(task_id) or component_ref,
            },
            target_scope={
                "type": kind,
                "id": identifier,
                f"{kind}_id": identifier,
                f"{kind}_ref": component_ref,
                "component_ref": component_ref,
                "source": _text(target_source) or "dev",
            },
            severity="high",
            blocking=True,
            source=source_token,
            dedup_key=dedup_key,
            evidence_refs=[gate_evidence, *_sequence_of_mappings(evidence_refs)],
            policy={
                "blocking": True,
                "run_policy": _text(run_policy) or "block_publication",
                "design_time_fixable": bool(design_time_fixable),
                "autonomous_repair_eligible": bool(autonomous_repair_eligible),
                "publication_required": bool(publication_required),
            },
            metadata=gate_metadata,
            owner_area=kind,
            component_ref=component_ref,
        )
        ticket_result = self.ensure_ticket_for_signal(
            signal_result["signal"],
            kind="runtime_failure",
            status="accepted",
            source=source_token,
            dedup_key=dedup_key,
            metadata=gate_metadata,
            owner_area=kind,
            component_ref=component_ref,
        )
        failure_ticket = ticket_result["ticket"]
        desired_policy = {
            "blocking": True,
            "run_policy": _text(run_policy) or "block_publication",
            "design_time_fixable": bool(design_time_fixable),
            "autonomous_repair_eligible": bool(autonomous_repair_eligible),
            "publication_required": bool(publication_required),
        }
        current_metadata = _mapping(failure_ticket.get("metadata"))
        merged_metadata = {**current_metadata, **gate_metadata}
        if (
            _mapping(failure_ticket.get("policy")) != desired_policy
            or merged_metadata != current_metadata
        ):
            failure_ticket = self._update_ticket(
                failure_ticket["ticket_id"],
                policy=desired_policy,
                metadata=merged_metadata,
                history_item={
                    "kind": "publication_gate_reclassified",
                    "gate": gate_token,
                    "run_policy": desired_policy["run_policy"],
                    "requires_user_decision": bool(
                        gate_metadata.get("requires_user_decision")
                    ),
                },
            )
        for ticket_id in linked_ids:
            if ticket_id == failure_ticket["ticket_id"]:
                continue
            try:
                self.relate_ticket(
                    failure_ticket["ticket_id"],
                    related_ticket_id=ticket_id,
                    relation="related",
                    actor=_text(actor) or "builder.automation",
                )
                self.relate_ticket(
                    ticket_id,
                    related_ticket_id=failure_ticket["ticket_id"],
                    relation="related",
                    actor=_text(actor) or "builder.automation",
                )
            except KeyError:
                continue
        return {
            "ok": True,
            "reported": True,
            "signal": signal_result["signal"],
            "ticket": self.get_ticket(failure_ticket["ticket_id"]) or failure_ticket,
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
        }

    def close_publication_gate_failures(
        self,
        *,
        component_type: str,
        component_id: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]],
        resolved_by_version: str | None = None,
        resolved_by_overlay: str | None = None,
    ) -> list[dict[str, Any]]:
        """Close technical gate findings after the exact candidate is published."""

        kind = _text(component_type).lower().rstrip("s")
        identifier = _text(component_id)
        refs = _sequence_of_mappings(evidence_refs)
        if kind not in {"skill", "scenario"} or not identifier:
            return []
        if not refs:
            raise ValueError("publication gate closure requires evidence_refs")
        tickets = self.list_tickets(
            status_group="open",
            component_ref=f"{kind}:{identifier}",
            kind="runtime_failure",
            source="builder_publication_gate",
        )
        closed: list[dict[str, Any]] = []
        actor_token = _text(actor) or "builder.automation"
        for ticket in tickets:
            ticket_id = _text(ticket.get("ticket_id"))
            status = _text(ticket.get("status"))
            if not ticket_id:
                continue
            if status not in {"resolved", "verified"}:
                ticket = self.record_resolution(
                    ticket_id,
                    actor=actor_token,
                    evidence_refs=refs,
                    resolved_by_version=resolved_by_version,
                    resolved_by_overlay=resolved_by_overlay,
                )["ticket"]
                status = _text(ticket.get("status"))
            if status == "resolved":
                ticket = self.verify_ticket(
                    ticket_id,
                    actor=actor_token,
                    evidence_refs=refs,
                    notes="Publication gate passed for the accepted candidate.",
                )["ticket"]
                status = _text(ticket.get("status"))
            if status == "verified":
                ticket = self.close_ticket(
                    ticket_id,
                    actor=actor_token,
                    reason="verified",
                    evidence_refs=refs,
                )
            closed.append(ticket)
        return closed

    def resolve_publication_gate_failures(
        self,
        *,
        component_type: str,
        component_id: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]],
        resolved_by_version: str | None = None,
        resolved_by_overlay: str | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve every component gate finding after a validated Trial starts."""

        kind = _text(component_type).lower().rstrip("s")
        identifier = _text(component_id)
        refs = _sequence_of_mappings(evidence_refs)
        if kind not in {"skill", "scenario"} or not identifier:
            return []
        if not refs:
            raise ValueError("publication gate resolution requires evidence_refs")
        resolved: list[dict[str, Any]] = []
        for ticket in self.list_tickets(
            status_group="open",
            component_ref=f"{kind}:{identifier}",
            kind="runtime_failure",
            source="builder_publication_gate",
        ):
            ticket_id = _text(ticket.get("ticket_id"))
            if not ticket_id:
                continue
            if _text(ticket.get("status")) not in {"resolved", "verified"}:
                ticket = self.record_resolution(
                    ticket_id,
                    actor=_text(actor) or "builder.automation",
                    evidence_refs=refs,
                    resolved_by_version=resolved_by_version,
                    resolved_by_overlay=resolved_by_overlay,
                )["ticket"]
            resolved.append(ticket)
        return resolved

    def reopen_publication_gate_failures(
        self,
        *,
        component_type: str,
        component_id: str,
        actor: str,
        reason: str,
        evidence_refs: Sequence[Mapping[str, Any]],
        exclude_ticket_ids: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """Return Trial-resolved gate findings when their candidate is rejected."""

        kind = _text(component_type).lower().rstrip("s")
        identifier = _text(component_id)
        refs = _sequence_of_mappings(evidence_refs)
        excluded = {_text(item) for item in exclude_ticket_ids if _text(item)}
        if kind not in {"skill", "scenario"} or not identifier:
            return []
        reopened: list[dict[str, Any]] = []
        for ticket in self.list_tickets(
            status_group="open",
            component_ref=f"{kind}:{identifier}",
            kind="runtime_failure",
            source="builder_publication_gate",
        ):
            ticket_id = _text(ticket.get("ticket_id"))
            if (
                not ticket_id
                or ticket_id in excluded
                or _text(ticket.get("status")) not in {"resolved", "verified"}
            ):
                continue
            reopened.append(
                self.reopen_ticket(
                    ticket_id,
                    actor=_text(actor) or "builder.automation",
                    reason=_text(reason) or "The validated Trial candidate was rejected.",
                    evidence_refs=refs,
                )
            )
        return reopened

    def report_runtime_activation_observation(
        self,
        observation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project manual/runtime activation outcomes into technical debt."""

        status = _text(observation.get("status")).lower()
        policy = _text(observation.get("report_policy")).lower() or "diagnostic_only"
        kind = _text(observation.get("component_type")).lower().rstrip("s") or (
            "scenario" if _text(observation.get("scenario_id")) else "skill"
        )
        identifier = _text(
            observation.get("component_id")
            or observation.get("skill_name")
            or observation.get("scenario_id")
        )
        if kind not in {"skill", "scenario"} or not identifier:
            return {"ok": True, "reported": False, "reason": "component_missing"}
        if status == "failed":
            if policy != "project_inbox":
                return {
                    "ok": True,
                    "reported": False,
                    "reason": "diagnostic_only",
                    "component_ref": f"{kind}:{identifier}",
                }
            gate = _text(observation.get("failed_stage") or observation.get("stage")) or "activation"
            error = _text(observation.get("error") or observation.get("failure_reason"))
            source = _text(observation.get("source")) or "runtime_activation"
            evidence = {
                "type": "test" if gate in {"tests", "validation"} else "runtime_guard",
                "id": _text(observation.get("operation_id"))
                or f"{kind}:{identifier}:{gate}",
                "status": "failed",
                "gate": gate,
                "space": _text(observation.get("space")) or "default",
                "version": _text(
                    observation.get("attempted_version") or observation.get("version")
                )
                or None,
                "slot": _text(observation.get("slot")) or None,
                "error": error or "runtime activation failed",
            }
            return self.report_publication_gate_failure(
                component_type=kind,
                component_id=identifier,
                gate=gate,
                error=error or "runtime activation failed",
                actor=source,
                evidence_refs=[evidence],
                candidate_id=_text(observation.get("attempted_version")) or None,
                webspace_id=_text(observation.get("webspace_id")) or "desktop",
                source="runtime_activation",
                producer="runtime_activation_observation",
                publication_required=False,
                autonomous_repair_eligible=True,
                dedup_namespace="runtime-activation",
                metadata={
                    "report_policy": policy,
                    "activation_source": source,
                    "space": _text(observation.get("space")) or "default",
                    "operation_id": _text(observation.get("operation_id")) or None,
                    "attempted_version": _text(
                        observation.get("attempted_version") or observation.get("version")
                    )
                    or None,
                    "slot": _text(observation.get("slot")) or None,
                },
                summary=f"{kind.capitalize()} {identifier} failed runtime {gate}",
                origin_type="runtime",
                origin_surface="activation",
                target_source=_text(observation.get("space")) or "default",
                run_policy="block_activation",
            )
        if status != "passed":
            return {"ok": True, "reported": False, "reason": status or "status_missing"}

        component_ref = f"{kind}:{identifier}"
        space = _text(observation.get("space")) or "default"
        version = _text(observation.get("version") or observation.get("attempted_version"))
        gate = _text(
            observation.get("stage")
            or observation.get("passed_stage")
            or observation.get("failed_stage")
        ).lower() or "activation"
        evidence = [
            {
                "type": "test" if gate in {"tests", "test", "validation"} else "runtime_guard",
                "id": _text(observation.get("operation_id")) or f"{component_ref}:{gate}",
                "status": "passed",
                "gate": gate,
                "space": space,
                "version": version or None,
                "slot": _text(observation.get("slot")) or None,
            }
        ]
        tickets = self.list_tickets(
            status_group="open",
            component_ref=component_ref,
            kind="runtime_failure",
            source="runtime_activation",
        )
        closed: list[dict[str, Any]] = []
        for ticket in tickets:
            ticket_metadata = _mapping(ticket.get("metadata"))
            if _text(ticket_metadata.get("space")) not in {"", space}:
                continue
            ticket_gate = _text(ticket_metadata.get("gate")).lower() or "activation"
            if ticket_gate != gate:
                continue
            ticket_id = _text(ticket.get("ticket_id"))
            ticket_status = _text(ticket.get("status"))
            if ticket_status not in {"resolved", "verified"}:
                ticket = self.record_resolution(
                    ticket_id,
                    actor="runtime.activation",
                    evidence_refs=evidence,
                    resolved_by_version=version or None,
                    resolved_by_overlay=(None if version else f"runtime:{space}"),
                )["ticket"]
                ticket_status = _text(ticket.get("status"))
            if ticket_status == "resolved":
                ticket = self.verify_ticket(
                    ticket_id,
                    actor="runtime.activation",
                    evidence_refs=evidence,
                    notes="A later runtime activation passed.",
                )["ticket"]
                ticket_status = _text(ticket.get("status"))
            if ticket_status == "verified":
                ticket = self.close_ticket(
                    ticket_id,
                    actor="runtime.activation",
                    reason="verified",
                    evidence_refs=evidence,
                )
            closed.append(ticket)
        return {"ok": True, "reported": bool(closed), "closed_tickets": closed}

    def transition_core_ticket(
        self,
        ticket_id: str,
        *,
        transition: str,
        actor: str,
        reason: str = "",
        notes: str = "",
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        release_ref: Mapping[str, Any] | None = None,
        capability_ref: Mapping[str, Any] | None = None,
        publish_pending_actions: bool = True,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        transition_token = _text(transition).lower()
        allowed = {
            "created",
            "qualified",
            "accepted",
            "deferred",
            "released",
            "verified",
            "reopened",
        }
        if transition_token not in allowed:
            raise ValueError(f"unsupported core ticket transition: {transition_token}")
        actor_token = _text(actor) or "core:maintainer"
        refs = _sequence_of_mappings(evidence_refs)
        release = _mapping(release_ref)
        capability = _mapping(capability_ref)
        if transition_token in {"released", "verified"} and not refs:
            raise ValueError(f"core ticket {transition_token} transition requires evidence_refs")
        if transition_token == "released" and not (release or capability):
            raise ValueError("core ticket released transition requires release_ref or capability_ref")
        pending_ticket_ids: list[str] = []
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if _text(ticket.get("owner_area")) != "core" and _text(_mapping(ticket.get("target_scope")).get("type")) != "core":
                raise ValueError("core lifecycle transition requires a Core Dev Ticket")
            semantic_type = f"core_ticket.{transition_token}"
            existing = next(
                (
                    dict(item)
                    for item in reversed(ticket.get("history") or [])
                    if isinstance(item, Mapping)
                    and _text(item.get("semantic_type")) == semantic_type
                    and transition_token == "created"
                ),
                None,
            )
            if existing is not None:
                return {
                    "ok": True,
                    "duplicate": True,
                    "ticket": _normalized_ticket(ticket),
                    "event": self._history_lifecycle_event(ticket, existing),
                    "affected_tickets": [],
                }
            current_status = _text(ticket.get("status"))
            if transition_token == "released" and current_status not in {"accepted", "claimed", "in_progress", "deferred"}:
                raise ValueError("core ticket release requires an accepted or active ticket")
            if transition_token == "verified" and current_status != "resolved":
                raise ValueError("core ticket verification requires released/resolved status")
            if transition_token == "reopened" and current_status not in {"resolved", "verified", "closed", "deferred"}:
                raise ValueError("core ticket reopen requires deferred or completed status")
            next_status = {
                "qualified": "accepted",
                "accepted": "accepted",
                "deferred": "deferred",
                "released": "resolved",
                "verified": "verified",
                "reopened": "accepted",
            }.get(transition_token, current_status)
            now = _now()
            if next_status:
                ticket["status"] = next_status
            metadata = _mapping(ticket.get("metadata"))
            core_lifecycle = _mapping(metadata.get("core_lifecycle"))
            core_lifecycle.update(
                {
                    "stage": transition_token,
                    "actor": actor_token,
                    "reason": _text(reason) or None,
                    "notes": _text(notes) or None,
                    "release_ref": release or core_lifecycle.get("release_ref"),
                    "capability_ref": capability or core_lifecycle.get("capability_ref"),
                    "updated_at": now,
                }
            )
            metadata["core_lifecycle"] = core_lifecycle
            ticket["metadata"] = metadata
            if refs:
                ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            if transition_token == "released":
                ticket["closure"] = {
                    "kind": "released",
                    "actor": actor_token,
                    "evidence_refs": refs,
                    "release_ref": release or None,
                    "capability_ref": capability or None,
                    "recorded_at": now,
                }
            elif transition_token == "verified":
                ticket["verification"] = {
                    "kind": "verified",
                    "actor": actor_token,
                    "evidence_refs": refs,
                    "notes": _text(notes) or None,
                    "recorded_at": now,
                }
            elif transition_token == "reopened":
                ticket.pop("verification", None)
            event_id = f"dtevent.{new_id()}"
            history_item = {
                "kind": transition_token,
                "semantic_type": semantic_type,
                "event_id": event_id,
                "actor": actor_token,
                "reason": _text(reason) or None,
                "notes": _text(notes) or None,
                "release_ref": release or None,
                "capability_ref": capability or None,
                "evidence_refs": refs,
                "status": next_status,
                "recorded_at": now,
            }
            ticket["updated_at"] = now
            self._append_history(ticket, history_item)
            affected: list[dict[str, Any]] = []
            if transition_token in {"released", "verified", "reopened", "deferred"}:
                affected = self._fanout_core_transition(
                    state,
                    core_ticket=ticket,
                    transition=transition_token,
                    actor=actor_token,
                    release_ref=release,
                    capability_ref=capability,
                    recorded_at=now,
                )
                if transition_token == "verified":
                    pending_ticket_ids = [item["ticket_id"] for item in affected]
            self._validate_ticket(ticket)
            self._write(state)
            normalized = _normalized_ticket(ticket)
            event = self._history_lifecycle_event(normalized, history_item)
        self._publish_lifecycle_event(event)
        pending_actions: list[dict[str, Any]] = []
        if publish_pending_actions and transition_token == "verified":
            for blocked_ticket_id in pending_ticket_ids:
                try:
                    pending_actions.append(
                        self.publish_core_capability_pending_action(
                            blocked_ticket_id,
                            core_ticket_id=normalized["ticket_id"],
                        )
                    )
                except Exception:
                    _log.warning(
                        "failed to publish core capability Pending Action ticket=%s core_ticket=%s",
                        blocked_ticket_id,
                        normalized["ticket_id"],
                        exc_info=True,
                    )
        return {
            "ok": True,
            "duplicate": False,
            "ticket": normalized,
            "event": event,
            "affected_tickets": affected,
            "pending_actions": pending_actions,
        }

    def _fanout_core_transition(
        self,
        state: dict[str, Any],
        *,
        core_ticket: Mapping[str, Any],
        transition: str,
        actor: str,
        release_ref: Mapping[str, Any],
        capability_ref: Mapping[str, Any],
        recorded_at: str,
    ) -> list[dict[str, Any]]:
        core_ticket_id = _text(core_ticket.get("ticket_id"))
        blocked_ids = {
            _text(ref.get("ticket_id"))
            for ref in _sequence_of_mappings(core_ticket.get("relation_refs") or [])
            if _text(ref.get("relation") or ref.get("type")) == "blocks"
            and _text(ref.get("ticket_id"))
        }
        affected: list[dict[str, Any]] = []
        for blocked_id in sorted(blocked_ids):
            ticket = state["tickets"].get(blocked_id)
            if not ticket:
                continue
            if transition == "verified":
                ticket["status"] = "ready_for_builder"
            elif transition in {"reopened", "deferred"}:
                ticket["status"] = "waiting_for_core"
            metadata = _mapping(ticket.get("metadata"))
            metadata["core_capability"] = {
                "core_ticket_id": core_ticket_id,
                "stage": transition,
                "release_ref": dict(release_ref) or None,
                "capability_ref": dict(capability_ref) or None,
                "updated_at": recorded_at,
            }
            ticket["metadata"] = metadata
            ticket["updated_at"] = recorded_at
            self._append_history(
                ticket,
                {
                    "kind": f"core_capability_{transition}",
                    "semantic_type": f"project_ticket.core_capability_{transition}",
                    "event_id": f"dtevent.{new_id()}",
                    "core_ticket_id": core_ticket_id,
                    "actor": actor,
                    "release_ref": dict(release_ref) or None,
                    "capability_ref": dict(capability_ref) or None,
                    "status": _text(ticket.get("status")),
                    "recorded_at": recorded_at,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "in_progress" if transition == "verified" else "deferred"
                    signal["updated_at"] = recorded_at
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            affected.append(_normalized_ticket(ticket))
        return affected

    def publish_core_capability_pending_action(
        self,
        ticket_id: str,
        *,
        core_ticket_id: str,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        core_ticket = self.get_ticket(core_ticket_id)
        if not ticket or not core_ticket:
            raise KeyError(ticket_id if not ticket else core_ticket_id)
        if _mapping(ticket.get("policy")).get("notify_core_available") is False:
            return {"ok": True, "published": False, "reason": "notification_disabled"}
        existing = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == CORE_CAPABILITY_PENDING_ACTION_KIND
            and _text(ref.get("core_ticket_id")) == _text(core_ticket_id)
        ]
        if existing:
            return {"ok": True, "published": False, "reason": "pending_action_already_linked", "pending_action": existing[-1]}
        from adaos.services import pending_actions

        action = pending_actions.publish_pending_action(
            ctx=ctx,
            webspace_id=webspace_id,
            kind=CORE_CAPABILITY_PENDING_ACTION_KIND,
            title="Core capability is available",
            summary=f"{ticket['summary']} - the blocking AdaOS capability is now verified.",
            producer={"type": "system", "system_id": "development_tickets"},
            owner_scope=ticket.get("owner_scope") or {"type": "workspace", "id": "local"},
            domain_ref={
                "ticket_id": ticket["ticket_id"],
                "core_ticket_id": core_ticket_id,
                "target_scope": ticket.get("target_scope") or {},
            },
            allowed_actions=[
                {"id": "postpone", "label": "Later", "terminal": True},
                {"id": "open_builder", "label": "Open Builder", "terminal": True},
                {"id": "start_autonomous_repair", "label": "Resume autonomously", "terminal": True},
            ],
            response_topic=CORE_CAPABILITY_RESPONSE_TOPIC,
            metadata={
                "schema": "adaos.dev_ticket.core_capability.pending_action_metadata.v1",
                "ticket_id": ticket["ticket_id"],
                "core_ticket_id": core_ticket_id,
                "core_component_ref": core_ticket.get("component_ref"),
                "core_lifecycle": _mapping(_mapping(core_ticket.get("metadata")).get("core_lifecycle")),
            },
        )
        ref = {
            "id": action.get("id"),
            "kind": action.get("kind"),
            "core_ticket_id": core_ticket_id,
            "status": action.get("status"),
            "created_at": action.get("created_at"),
        }
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=_merge_refs(ticket.get("pending_action_refs") or [], [ref]),
            history_item={
                "kind": "core_capability_pending_action_published",
                "pending_action_id": ref.get("id"),
                "core_ticket_id": core_ticket_id,
            },
        )
        return {"ok": True, "published": True, "pending_action": action, "ticket": updated}

    def record_sdk_understanding_signal(
        self,
        *,
        kind: str,
        summary: str,
        method_ref: str,
        actor: str,
        expected_behavior: str = "",
        observed_behavior: str = "",
        diagnosis: str = "",
        project_ticket_id: str | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        metadata: Mapping[str, Any] | None = None,
        status: str = "proposed",
    ) -> dict[str, Any]:
        signal_kind = (_text(kind) or "sdk_unclear_definition").lower()
        if signal_kind not in SDK_UNDERSTANDING_SIGNAL_KINDS:
            raise ValueError(f"unsupported SDK understanding kind: {signal_kind}")
        text = _text(summary)
        method = _text(method_ref)
        if not text:
            raise ValueError("SDK understanding summary is required")
        if not method:
            raise ValueError("SDK understanding method_ref is required")
        relation_refs = []
        project_ticket = _text(project_ticket_id)
        if project_ticket:
            relation_refs.append(
                {
                    "type": "caused_by",
                    "relation": "caused_by",
                    "target_ref": f"dticket:{project_ticket}",
                    "ticket_id": project_ticket,
                }
            )
        meta = {
            **_mapping(metadata),
            "actor": _text(actor) or "builder",
            "method_ref": method,
            "expected_behavior": _text(expected_behavior) or None,
            "observed_behavior": _text(observed_behavior) or None,
            "diagnosis": _text(diagnosis) or None,
            "project_ticket_id": project_ticket or None,
        }
        target = {"type": "sdk", "id": method, "component_ref": f"sdk:{method}", "source": "sdk"}
        signal_result = self.capture_signal(
            kind=signal_kind,
            summary=text,
            owner_scope={"type": "workspace", "id": "local"},
            origin_scope={"type": "builder", "surface": "sdk_understanding", "id": _text(actor) or "builder"},
            target_scope=target,
            severity="medium",
            blocking=False,
            source="builder_intake",
            dedup_key=_fingerprint("sdk-understanding", signal_kind, method, text.lower(), project_ticket),
            evidence_refs=evidence_refs,
            metadata=meta,
            owner_area="sdk",
            component_ref=f"sdk:{method}",
            relation_refs=relation_refs,
        )
        ticket_result = self.ensure_ticket_for_signal(
            signal_result["signal"],
            kind="sdk_understanding",
            status=status,
            source="builder_intake",
            dedup_key=signal_result["signal"]["dedup_key"],
            metadata=meta,
            owner_area="sdk",
            component_ref=f"sdk:{method}",
            relation_refs=relation_refs,
        )
        if project_ticket:
            self.relate_ticket(project_ticket, related_ticket_id=ticket_result["ticket"]["ticket_id"], relation="caused_by", actor=_text(actor) or "builder")
        return {
            "ok": True,
            "signal": signal_result["signal"],
            "ticket": ticket_result["ticket"],
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
        }

    def block_ticket_by_core(self, ticket_id: str, core_ticket_id: str, *, actor: str) -> dict[str, Any]:
        ticket_token = _text(ticket_id)
        core_token = _text(core_ticket_id)
        if not ticket_token or not core_token:
            raise ValueError("ticket_id and core_ticket_id are required")
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(ticket_token)
            core = state["tickets"].get(core_token)
            if not ticket:
                raise KeyError(ticket_token)
            if not core:
                raise KeyError(core_token)
            now = _now()
            ticket["status"] = "waiting_for_core"
            ticket["relation_refs"] = _normalize_relation_refs(
                _sequence_of_mappings(ticket.get("relation_refs") or []),
                [{"type": "blocked_by", "relation": "blocked_by", "target_ref": f"dticket:{core_token}", "ticket_id": core_token}],
            )
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "blocked_by_core",
                    "actor": actor_token,
                    "core_ticket_id": core_token,
                    "recorded_at": now,
                },
            )
            core["relation_refs"] = _normalize_relation_refs(
                _sequence_of_mappings(core.get("relation_refs") or []),
                [{"type": "blocks", "relation": "blocks", "target_ref": f"dticket:{ticket_token}", "ticket_id": ticket_token}],
            )
            core["updated_at"] = now
            self._append_history(
                core,
                {
                    "kind": "blocks_project_ticket",
                    "actor": actor_token,
                    "blocked_ticket_id": ticket_token,
                    "recorded_at": now,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "deferred"
                    signal["relation_refs"] = _normalize_relation_refs(
                        _sequence_of_mappings(signal.get("relation_refs") or []),
                        [{"type": "blocked_by", "relation": "blocked_by", "target_ref": f"dticket:{core_token}", "ticket_id": core_token}],
                    )
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._validate_ticket(core)
            self._write(state)
            return _normalized_ticket(ticket)

    def close_ticket(
        self,
        ticket_id: str,
        *,
        reason: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._close_ticket(
            ticket_id,
            reason=reason,
            actor=_text(actor) or "system",
            evidence_refs=evidence_refs,
            expected_revision=expected_revision,
        )

    def update_ticket_summary(
        self,
        ticket_id: str,
        *,
        summary: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        text = _text(summary)
        if not text:
            raise ValueError("ticket summary is required")
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError("terminal Dev Ticket cannot be edited")
            previous = _text(ticket.get("summary"))
            if previous == text:
                return _normalized_ticket(ticket)
            now = _now()
            ticket["summary"] = text
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "summary_updated",
                    "actor": actor_token,
                    "previous_summary": previous,
                    "summary": text,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def update_ticket_priority(
        self,
        ticket_id: str,
        *,
        priority: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        priority_token = _text(priority).lower()
        if priority_token not in TICKET_PRIORITIES:
            raise ValueError(f"unsupported Dev Ticket priority: {priority_token or '<missing>'}")
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError("terminal Dev Ticket cannot be edited")
            stored_priority = _text(ticket.get("priority")).lower()
            previous = stored_priority or "should"
            if stored_priority == priority_token:
                return _normalized_ticket(ticket)
            now = _now()
            ticket["priority"] = priority_token
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "priority_updated",
                    "actor": actor_token,
                    "previous_priority": previous,
                    "priority": priority_token,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def requalify_builder_repair(
        self,
        ticket_id: str,
        *,
        builder_repair: Mapping[str, Any],
        actor: str,
        reason: str,
        expected_updated_at: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Replace the bounded Builder repair envelope with an audited revision."""

        raw = dict(builder_repair)
        profile = _text(raw.get("profile")).lower()
        if profile not in _REPAIR_HINT_PROFILES:
            raise ValueError(f"unsupported Builder repair profile: {profile or '<missing>'}")
        requested_target_type = _text(raw.get("target_object_type")).lower()
        requested_target_id = _text(raw.get("target_object_id"))
        if bool(requested_target_type) != bool(requested_target_id):
            raise ValueError("Builder repair target_object_type and target_object_id must be provided together")
        if requested_target_type and requested_target_type not in {"skill", "scenario"}:
            raise ValueError("Builder repair target_object_type must be skill or scenario")
        if requested_target_id and not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", requested_target_id):
            raise ValueError("Builder repair target_object_id is invalid")
        requested_files = [
            _text(value).replace("\\", "/").strip("/")
            for value in raw.get("target_files") or []
            if _text(value)
        ]
        normalized = _bounded_repair_hints({"metadata": {"builder_repair": raw}})
        target_files = list(normalized.get("target_files") or [])
        if target_files != requested_files:
            raise ValueError("Builder repair target_files contain duplicates or unsafe paths")
        if not target_files:
            raise ValueError("Builder repair qualification requires target_files")
        if int(normalized.get("max_changed_files") or 0) < len(target_files):
            raise ValueError("max_changed_files must cover every qualified target file")
        if raw.get("structured_edits") is not None:
            structured_edits = _normalize_structured_edits(
                raw.get("structured_edits"),
                target_files=target_files,
                strict=True,
            )
            if structured_edits != normalized.get("structured_edits"):
                raise ValueError("structured_edits changed during qualification normalization")
        if raw.get("contract_closure") is not None:
            contract_closure = _normalize_contract_closure(
                raw.get("contract_closure"),
                target_files=target_files,
            )
            if contract_closure != normalized.get("contract_closure"):
                raise ValueError("contract_closure contains invalid or out-of-scope paths")
        requested_preconditions = [
            dict(value)
            for value in raw.get("source_preconditions") or []
            if isinstance(value, Mapping)
        ]
        if requested_preconditions != list(normalized.get("source_preconditions") or []):
            raise ValueError("source_preconditions contain invalid paths or digests")
        reason_token = _text(reason)
        if not reason_token:
            raise ValueError("Builder repair requalification reason is required")
        actor_token = _text(actor) or "builder"

        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            ticket_status = _text(ticket.get("status"))
            if ticket_status in {*TERMINAL_TICKET_STATES, "resolved", "verified"}:
                raise ValueError("completed Dev Ticket cannot be requalified")
            if ticket_status == "in_builder":
                raise ValueError("Dev Ticket cannot be requalified while Builder is running")
            expected = _text(expected_updated_at)
            if expected and expected != _text(ticket.get("updated_at")):
                raise ValueError("Dev Ticket changed since the qualification was loaded")

            previous = _bounded_repair_hints(ticket)
            if previous == normalized:
                return _normalized_ticket(ticket)
            now = _now()
            metadata = _mapping(ticket.get("metadata"))
            metadata["builder_repair"] = normalized
            ticket["metadata"] = metadata
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "builder_repair_requalified",
                    "actor": actor_token,
                    "reason": reason_token,
                    "previous_builder_repair": previous,
                    "builder_repair": normalized,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def claim_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        owner: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        actor_token = _text(actor) or "system"
        owner_token = _text(owner) or actor_token
        return self._update_ticket(
            ticket_id,
            status="claimed",
            metadata={
                **_mapping((self.get_ticket(ticket_id) or {}).get("metadata")),
                "claimed_by": owner_token,
            },
            history_item={
                "kind": "claimed",
                "actor": actor_token,
                "owner": owner_token,
            },
            expected_revision=expected_revision,
        )

    def start_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._update_ticket(
            ticket_id,
            status="in_progress",
            history_item={
                "kind": "in_progress",
                "actor": _text(actor) or "system",
            },
            expected_revision=expected_revision,
        )

    def comment_ticket(
        self,
        ticket_id: str,
        *,
        body: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
        assistant_requested: bool = False,
    ) -> dict[str, Any]:
        text = _text(body)
        if not text:
            raise ValueError("ticket comment is required")
        actor_token = _text(actor) or "system"
        refs = _sequence_of_mappings(evidence_refs)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError("terminal Dev Ticket cannot be commented")
            now = _now()
            comment = {
                "id": f"dcomment.{new_id()}",
                "body": text,
                "actor": actor_token,
                "evidence_refs": refs,
                "created_at": now,
                **({"assistant_status": "pending"} if assistant_requested else {}),
            }
            comments = [dict(item) for item in ticket.get("comments") or [] if isinstance(item, Mapping)]
            comments.append(comment)
            ticket["comments"] = comments[-100:]
            if refs:
                ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "commented",
                    "comment_id": comment["id"],
                    "actor": actor_token,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def link_development_report(
        self,
        ticket_id: str,
        *,
        report: Mapping[str, Any],
        relay: Mapping[str, Any] | None = None,
        actor: str = "system:development_report",
    ) -> dict[str, Any]:
        """Attach the public publisher feedback identity to a local ticket."""

        report_id = _text(report.get("report_id"))
        if not report_id:
            raise ValueError("Development Report identity is required")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            metadata = _mapping(ticket.get("metadata"))
            existing = _mapping(metadata.get("development_report"))
            linked = {
                "schema": "adaos.dev_ticket.development_report_link.v1",
                "report_id": report_id,
                "application_id": _text(report.get("application_id")) or None,
                "publisher_ref": _text(report.get("publisher_ref")) or None,
                "status": _text(report.get("status")) or "queued",
                "revision": int(report.get("revision") or 1),
                "relay_status": _text(_mapping(relay).get("local_status")) or None,
                "updated_at": _now(),
            }
            if existing.get("report_id") not in {None, "", report_id}:
                raise ValueError("Dev Ticket already links another Development Report")
            if {
                key: value for key, value in existing.items() if key != "updated_at"
            } == {
                key: value for key, value in linked.items() if key != "updated_at"
            }:
                return _normalized_ticket(ticket)
            metadata["development_report"] = linked
            ticket["metadata"] = metadata
            ticket["updated_at"] = linked["updated_at"]
            self._append_history(
                ticket,
                {
                    "kind": "development_report_linked",
                    "actor": _text(actor) or "system:development_report",
                    "report_id": report_id,
                    "status": linked["status"],
                    "recorded_at": linked["updated_at"],
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def assist_ticket_comment(
        self,
        ticket_id: str,
        *,
        comment_id: str,
        llm_call: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Classify a human comment and append one bounded assistant reply."""
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None
        source_comment = next(
            (
                dict(item)
                for item in ticket.get("comments") or []
                if isinstance(item, Mapping) and _text(item.get("id")) == _text(comment_id)
            ),
            None,
        )
        if not source_comment or _text(source_comment.get("assistant_status")) == "completed":
            return ticket
        request_id = _fingerprint("dev-ticket-comment-assist", ticket_id, comment_id)
        caller = llm_call
        if caller is None:
            from adaos.sdk.llm.llm_client import send_response

            caller = send_response
        response: Mapping[str, Any] = {}
        try:
            response = caller(
                self._comment_assistance_messages(ticket, source_comment),
                max_tokens=700,
                text={"format": {"type": "json_object"}, "verbosity": "low"},
                request_id=request_id,
                prompt_cache_key="adaos.dev-ticket.comment-assistance.v1",
                timeout=45,
            )
            if not isinstance(response, Mapping):
                raise ValueError("Root LLM comment assistance response must be an object")
            proposal = _parse_language_qualification_output(response.get("output_text"))
            intent = _text(proposal.get("intent")).lower()
            if intent not in {"question", "remark", "uncertain"}:
                raise ValueError("comment assistance intent is invalid")
            confidence = float(proposal.get("confidence") or 0.0)
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("comment assistance confidence is invalid")
            message = _text(proposal.get("message"))[:4000]
            qualification = _text(proposal.get("qualification"))[:2000]
            clarification = _text(proposal.get("clarification_question"))[:2000]
            if intent == "uncertain":
                reply = clarification or message
            elif intent == "remark":
                reply = qualification or message
            else:
                reply = message
            if not reply:
                raise ValueError("comment assistance reply is empty")
            usage = _language_qualification_usage_receipt(
                response,
                request_id=request_id,
                status="completed",
            )
            usage["accounting_scope"] = "development_ticket_comment_assistance"
            analysis = {
                "schema": "adaos.dev_ticket.comment_assistance.v1",
                "intent": intent,
                "confidence": confidence,
                "qualification": qualification or None,
                "clarification_question": clarification or None,
                "usage": usage,
            }
            return self._record_comment_assistance(
                ticket_id,
                comment_id=comment_id,
                status="completed",
                reply=reply,
                analysis=analysis,
            )
        except Exception as exc:
            usage = _language_qualification_usage_receipt(
                response,
                request_id=request_id,
                status="failed",
            )
            usage["accounting_scope"] = "development_ticket_comment_assistance"
            _log.warning(
                "dev-ticket comment assistance failed ticket_id=%s comment_id=%s error=%s",
                ticket_id,
                comment_id,
                exc,
            )
            return self._record_comment_assistance(
                ticket_id,
                comment_id=comment_id,
                status="failed",
                reply="",
                analysis={
                    "schema": "adaos.dev_ticket.comment_assistance.v1",
                    "error_type": type(exc).__name__,
                    "error": _text(exc)[:1000],
                    "usage": usage,
                },
            )

    @staticmethod
    def _comment_assistance_messages(
        ticket: Mapping[str, Any],
        comment: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        recent = [
            {
                "actor": _text(item.get("actor"))[:100],
                "body": _text(item.get("body"))[:1200],
            }
            for item in _sequence_of_mappings(ticket.get("comments") or [])[-8:]
        ]
        payload = {
            "ticket": {
                "summary": _text(ticket.get("summary"))[:1600],
                "status": _text(ticket.get("status")),
                "priority": _text(ticket.get("priority")),
                "target_scope": _mapping(ticket.get("target_scope")),
            },
            "recent_comments": recent,
            "new_comment": _text(comment.get("body"))[:4000],
        }
        return [
            {
                "role": "system",
                "content": (
                    "You assist inside an AdaOS Dev Ticket discussion. Classify the new human comment as "
                    "question, remark, or uncertain. For a question, answer immediately using only the supplied "
                    "ticket context and say what is unknown. For a remark, provide a concise engineering "
                    "qualification that preserves the user's intent. If essential meaning is ambiguous, use "
                    "uncertain and ask exactly one focused clarification question. Reply in the language of the "
                    "new comment. Do not claim implementation or verification that is not in the context. Return "
                    "one JSON object with intent, message, qualification, clarification_question, confidence."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def _record_comment_assistance(
        self,
        ticket_id: str,
        *,
        comment_id: str,
        status: str,
        reply: str,
        analysis: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                return None
            comments = [dict(item) for item in ticket.get("comments") or [] if isinstance(item, Mapping)]
            source = next((item for item in comments if _text(item.get("id")) == _text(comment_id)), None)
            if not source:
                return _normalized_ticket(ticket)
            existing_reply = _text(source.get("assistant_comment_id"))
            if existing_reply:
                return _normalized_ticket(ticket)
            now = _now()
            source["assistant_status"] = status
            source["assistant_analysis"] = dict(analysis)
            if status == "completed" and reply:
                assistant_comment_id = f"dcomment.{new_id()}"
                source["assistant_comment_id"] = assistant_comment_id
                comments.append(
                    {
                        "id": assistant_comment_id,
                        "body": reply,
                        "actor": "assistant:dev_ticket",
                        "actor_type": "assistant",
                        "reply_to_comment_id": _text(comment_id),
                        "evidence_refs": [],
                        "analysis": dict(analysis),
                        "created_at": now,
                    }
                )
            ticket["comments"] = comments[-100:]
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": f"comment_assistance_{status}",
                    "comment_id": _text(comment_id),
                    "assistant_comment_id": _text(source.get("assistant_comment_id")) or None,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def record_resolution(
        self,
        ticket_id: str,
        *,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
        resolved_by_version: str | None = None,
        resolved_by_overlay: str | None = None,
        repair_service: BuilderRepairService | None = None,
        repair_id: str | None = None,
        capability_works: bool = True,
        regression_free: bool = True,
        accept_reduced_scope: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(evidence_refs)
        if not refs:
            raise ValueError("ticket resolution requires evidence_refs")
        actor_token = _text(actor)
        if not actor_token:
            raise ValueError("ticket resolution requires actor")
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        if _text(ticket.get("owner_area")) == "core":
            raise ValueError("Core Dev Ticket resolution requires the released lifecycle transition")
        linked_repair_id = _text(repair_id) or self._latest_repair_id(ticket)
        if repair_service is not None and linked_repair_id:
            repair_service.record_acceptance(
                linked_repair_id,
                capability_works=capability_works,
                regression_free=regression_free,
                evidence_refs=refs,
                actor=actor_token,
            )
        closure = {
            "kind": "resolved",
            "actor": actor_token,
            "evidence_refs": refs,
            "resolved_by_version": _text(resolved_by_version) or None,
            "resolved_by_overlay": _text(resolved_by_overlay) or None,
            "repair_id": linked_repair_id or None,
            "capability_works": bool(capability_works),
            "regression_free": bool(regression_free),
            "recorded_at": _now(),
        }
        signal_status = "resolved_by_version" if closure["resolved_by_version"] else "resolved_by_overlay"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            stored = state["tickets"].get(ticket["ticket_id"])
            if not stored:
                raise KeyError(ticket["ticket_id"])
            self._assert_expected_revision(stored, expected_revision)
            blockers = self._unresolved_core_blockers(stored, state)
            if blockers and not accept_reduced_scope:
                raise ValueError(
                    "Dev Ticket cannot be resolved while blocked by unresolved Core Dev Tickets: "
                    + ", ".join(blockers)
                )
            stored["status"] = "resolved"
            if blockers:
                closure["reduced_scope_accepted"] = True
                closure["unresolved_core_ticket_ids"] = blockers
            stored["closure"] = closure
            stored["evidence_refs"] = _merge_refs(stored.get("evidence_refs") or [], refs)
            stored["updated_at"] = closure["recorded_at"]
            self._append_history(stored, {"kind": "resolved", "actor": actor_token, "recorded_at": closure["recorded_at"]})
            for signal_id in stored.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if not signal:
                    continue
                signal["status"] = signal_status
                signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                signal["updated_at"] = closure["recorded_at"]
                signal["builder_ref"] = {
                    **_mapping(signal.get("builder_ref")),
                    "repair_id": linked_repair_id or None,
                    "resolved_by_version": closure["resolved_by_version"],
                    "resolved_by_overlay": closure["resolved_by_overlay"],
                }
                self._validate_signal(signal)
            self._validate_ticket(stored)
            self._write(state)
            resolved_ticket = _normalized_ticket(stored)
        resolved_ticket = self._supersede_builder_clarification_pending_actions(
            resolved_ticket,
            reason="ticket_resolved",
        )
        resolved_ticket = self._supersede_publication_permission_pending_actions(
            resolved_ticket,
            reason="ticket_resolved",
        )
        return {
            "ok": True,
            "ticket": resolved_ticket,
            "closure": _clone(closure),
        }

    @staticmethod
    def _unresolved_core_blockers(
        ticket: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> list[str]:
        tickets = _mapping(state.get("tickets"))
        blockers: list[str] = []
        for ref in _sequence_of_mappings(ticket.get("relation_refs") or []):
            if _text(ref.get("relation") or ref.get("type")) != "blocked_by":
                continue
            blocker_id = _text(ref.get("ticket_id"))
            blocker = _mapping(tickets.get(blocker_id))
            if not blocker_id or not blocker:
                continue
            if _text(blocker.get("owner_area")) != "core":
                continue
            if _text(blocker.get("status")) not in {"verified", "closed", "superseded"}:
                blockers.append(blocker_id)
        return sorted(set(blockers))

    @staticmethod
    def _missing_publication_verification_evidence(
        ticket: Mapping[str, Any],
        refs: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        if _mapping(ticket.get("policy")).get("publication_required") is not True:
            return []
        accepted_trial = any(
            _text(ref.get("type")) == "builder_trial"
            and _text(ref.get("status")).lower() == "accepted"
            and _text(ref.get("decision")).lower() == "accept"
            and bool(_text(ref.get("id")))
            for ref in refs
        )
        published_release = any(
            _text(ref.get("type")) == "project_release"
            and _text(ref.get("status")).lower() == "published"
            and bool(_text(ref.get("id")))
            for ref in refs
        )
        missing: list[str] = []
        if not accepted_trial:
            missing.append("accepted builder_trial")
        if not published_release:
            missing.append("published project_release")
        return missing

    def verify_ticket(
        self,
        ticket_id: str,
        *,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
        repair_id: str | None = None,
        notes: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(evidence_refs)
        if not refs:
            raise ValueError("ticket verification requires evidence_refs")
        actor_token = _text(actor)
        if not actor_token:
            raise ValueError("ticket verification requires actor")
        current = self.get_ticket(ticket_id)
        if not current:
            raise KeyError(ticket_id)
        if _text(current.get("owner_area")) == "core":
            result = self.transition_core_ticket(
                ticket_id,
                transition="verified",
                actor=actor_token,
                notes=notes,
                evidence_refs=refs,
                expected_revision=expected_revision,
            )
            return {
                "ok": True,
                "ticket": result["ticket"],
                "verification": _clone(result["ticket"].get("verification") or {}),
                "event": result["event"],
                "affected_tickets": result["affected_tickets"],
                "pending_actions": result["pending_actions"],
            }
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if _text(ticket.get("status")) != "resolved":
                raise ValueError("ticket verification requires resolved status")
            missing_publication_evidence = self._missing_publication_verification_evidence(
                ticket,
                refs,
            )
            if missing_publication_evidence:
                missing = ", ".join(missing_publication_evidence)
                raise ValueError(
                    "ticket verification requires publication evidence "
                    f"({missing}); accept the current changeset before verifying the ticket"
                )
            now = _now()
            verification = {
                "kind": "verified",
                "actor": actor_token,
                "evidence_refs": refs,
                "repair_id": _text(repair_id) or self._latest_repair_id(ticket) or None,
                "notes": _text(notes) or None,
                "recorded_at": now,
            }
            ticket["status"] = "verified"
            ticket["verification"] = verification
            ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(ticket, {"kind": "verified", "actor": actor_token, "recorded_at": now})
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return {"ok": True, "ticket": _normalized_ticket(ticket), "verification": _clone(verification)}

    def reopen_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        reason: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        reason_token = _text(reason)
        if not reason_token:
            raise ValueError("ticket reopen requires reason")
        actor_token = _text(actor) or "system"
        refs = _sequence_of_mappings(evidence_refs)
        current = self.get_ticket(ticket_id)
        if not current:
            raise KeyError(ticket_id)
        if _text(current.get("owner_area")) == "core":
            return self.transition_core_ticket(
                ticket_id,
                transition="reopened",
                actor=actor_token,
                reason=reason_token,
                evidence_refs=refs,
                expected_revision=expected_revision,
            )["ticket"]
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            previous_status = _text(ticket.get("status"))
            previous_verification = _mapping(ticket.pop("verification", None))
            previous_closure = _mapping(ticket.pop("closure", None))
            now = _now()
            ticket["status"] = "in_progress" if ticket.get("builder_refs") else "accepted"
            ticket["reopened_at"] = now
            if refs:
                ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "reopened",
                    "actor": actor_token,
                    "reason": reason_token,
                    "previous_status": previous_status or None,
                    "previous_verification": previous_verification or None,
                    "previous_closure": previous_closure or None,
                    "recorded_at": now,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "in_progress"
                    if refs:
                        signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def relate_ticket(
        self,
        ticket_id: str,
        *,
        related_ticket_id: str,
        relation: str = "related",
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        related = _text(related_ticket_id)
        if not related:
            raise ValueError("related_ticket_id is required")
        relation_token = (_text(relation) or "related").lower()
        if relation_token not in TICKET_RELATION_KINDS:
            raise ValueError(f"unsupported ticket relation: {relation_token}")
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if related not in state["tickets"]:
                raise KeyError(related)
            now = _now()
            ref = {
                "type": relation_token,
                "relation": relation_token,
                "target_ref": f"dticket:{related}",
                "ticket_id": related,
            }
            ticket["relation_refs"] = _normalize_relation_refs(
                _sequence_of_mappings(ticket.get("relation_refs") or []),
                [ref],
            )
            ticket["related_refs"] = _merge_refs(
                ticket.get("related_refs") or [],
                [{"type": "dev_ticket", "ticket_id": related, "relation": relation_token}],
            )
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "related",
                    "actor": actor_token,
                    "related_ticket_id": related,
                    "relation": relation_token,
                    "recorded_at": now,
                },
            )
            inverse = INVERSE_TICKET_RELATION.get(relation_token)
            if inverse:
                other = state["tickets"].get(related)
                if other:
                    other_ref = {
                        "type": inverse,
                        "relation": inverse,
                        "target_ref": f"dticket:{ticket['ticket_id']}",
                        "ticket_id": ticket["ticket_id"],
                    }
                    other["relation_refs"] = _normalize_relation_refs(
                        _sequence_of_mappings(other.get("relation_refs") or []),
                        [other_ref],
                    )
                    other["related_refs"] = _merge_refs(
                        other.get("related_refs") or [],
                        [{"type": "dev_ticket", "ticket_id": ticket["ticket_id"], "relation": inverse}],
                    )
                    other["updated_at"] = now
                    self._append_history(
                        other,
                        {
                            "kind": "related",
                            "actor": actor_token,
                            "related_ticket_id": ticket["ticket_id"],
                            "relation": inverse,
                            "recorded_at": now,
                        },
                    )
                    self._validate_ticket(other)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def duplicate_ticket(
        self,
        ticket_id: str,
        *,
        duplicate_of: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        duplicate_target = _text(duplicate_of)
        if not duplicate_target:
            raise ValueError("duplicate_of is required")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            if duplicate_target not in state["tickets"]:
                raise KeyError(duplicate_target)
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            relation_ref = {
                "type": "duplicate_of",
                "relation": "duplicate_of",
                "target_ref": f"dticket:{duplicate_target}",
                "ticket_id": duplicate_target,
            }
            ticket["relation_refs"] = _normalize_relation_refs(
                _sequence_of_mappings(ticket.get("relation_refs") or []),
                [relation_ref],
            )
            ticket["related_refs"] = _merge_refs(
                ticket.get("related_refs") or [],
                [{"type": "dev_ticket", "ticket_id": duplicate_target, "relation": "duplicate_of"}],
            )
            canonical = state["tickets"].get(duplicate_target)
            if canonical:
                canonical["relation_refs"] = _normalize_relation_refs(
                    _sequence_of_mappings(canonical.get("relation_refs") or []),
                    [
                        {
                            "type": "supersedes",
                            "relation": "supersedes",
                            "target_ref": f"dticket:{ticket['ticket_id']}",
                            "ticket_id": ticket["ticket_id"],
                        }
                    ],
                )
                canonical["related_refs"] = _merge_refs(
                    canonical.get("related_refs") or [],
                    [{"type": "dev_ticket", "ticket_id": ticket["ticket_id"], "relation": "supersedes"}],
                )
            now = _now()
            ticket["status"] = "superseded"
            ticket["closure"] = {
                "kind": "closed",
                "reason": "duplicate",
                "actor": _text(actor) or "system",
                "duplicate_of": duplicate_target,
                "evidence_refs": [],
                "recorded_at": now,
            }
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "duplicated",
                    "actor": _text(actor) or "system",
                    "duplicate_of": duplicate_target,
                    "recorded_at": now,
                },
            )
            if canonical:
                canonical["updated_at"] = now
                self._append_history(
                    canonical,
                    {
                        "kind": "related",
                        "actor": _text(actor) or "system",
                        "related_ticket_id": ticket["ticket_id"],
                        "relation": "supersedes",
                        "recorded_at": now,
                    },
                )
                self._validate_ticket(canonical)
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "superseded"
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        signal = self._read_snapshot()["signals"].get(_text(signal_id))
        return _clone(signal) if signal else None

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._read_snapshot()["tickets"].get(_text(ticket_id))
        return _normalized_ticket(ticket) if ticket else None

    def list_tickets(
        self,
        *,
        status: str | None = None,
        status_group: str | None = None,
        target_id: str | None = None,
        target_tokens: Sequence[str] = (),
        kind: str | None = None,
        exclude_kind: str | None = None,
        scenario_id: str | None = None,
        skill_id: str | None = None,
        modal_id: str | None = None,
        component: str | None = None,
        severity: str | None = None,
        priority: str | None = None,
        blocking: bool | None = None,
        source: str | None = None,
        owner: str | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
        updated_since: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        projection: str = "full",
    ) -> list[dict[str, Any]]:
        projection_token = _text(projection).lower() or "full"
        if projection_token not in {"full", "summary"}:
            raise ValueError(f"unsupported Dev Ticket projection: {projection_token}")
        tickets = [
            _normalized_ticket_view(item)
            for item in self._read_snapshot()["tickets"].values()
        ]
        if status:
            allowed = {_text(part) for part in _text(status).split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("status")) in allowed]
        group = _text(status_group)
        if group:
            allowed = set()
            for part in group.split(","):
                allowed.update(TICKET_STATUS_GROUPS.get(_text(part), {_text(part)}))
            tickets = [item for item in tickets if _text(item.get("status")) in allowed]
        if target_id:
            token = _text(target_id)
            tickets = [
                item
                for item in tickets
                if _text(_mapping(item.get("target_scope")).get("id")) == token
            ]
        tokens = {_text(item) for item in target_tokens if _text(item)}
        scoped_tokens = [_text(scenario_id), _text(skill_id), _text(modal_id), _text(component)]
        tokens.update(item for item in scoped_tokens if item)
        if tokens:
            tickets = [item for item in tickets if _ticket_scope_tokens(item) & tokens]
        kind_token = _text(kind)
        if kind_token:
            allowed = {_text(part) for part in kind_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("kind")) in allowed]
        excluded_kind_token = _text(exclude_kind)
        if excluded_kind_token:
            excluded = {
                _text(part)
                for part in excluded_kind_token.split(",")
                if _text(part)
            }
            tickets = [
                item
                for item in tickets
                if _text(item.get("kind")) not in excluded
            ]
        severity_token = _text(severity)
        if severity_token:
            allowed = {_text(part) for part in severity_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("severity")) in allowed]
        priority_token = _text(priority).lower()
        if priority_token:
            if priority_token == "non_deferred":
                tickets = [
                    item
                    for item in tickets
                    if _text(item.get("priority") or "should").lower() != "deferred"
                ]
            else:
                allowed = {_text(part).lower() for part in priority_token.split(",") if _text(part)}
                if not allowed <= TICKET_PRIORITIES:
                    raise ValueError(f"unsupported Dev Ticket priority filter: {priority_token}")
                tickets = [
                    item
                    for item in tickets
                    if _text(item.get("priority") or "should").lower() in allowed
                ]
        if blocking is not None:
            tickets = [item for item in tickets if bool(item.get("blocking")) is bool(blocking)]
        source_token = _text(source)
        if source_token:
            allowed = {_text(part) for part in source_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("source")) in allowed]
        owner_token = _text(owner)
        if owner_token:
            tickets = [item for item in tickets if owner_token in _ticket_owner_tokens(item)]
        owner_area_token = _text(owner_area)
        if owner_area_token:
            allowed = {_text(part).lower() for part in owner_area_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("owner_area")).lower() in allowed]
        component_ref_token = _text(component_ref)
        if component_ref_token:
            allowed = {_text(part).lower() for part in component_ref_token.split(",") if _text(part)}
            tickets = [
                item
                for item in tickets
                if _text(item.get("component_ref")).lower() in allowed
                or bool({_text(token).lower() for token in _ticket_scope_tokens(item)} & allowed)
            ]
        since_token = _text(updated_since)
        if since_token:
            tickets = [item for item in tickets if _text(item.get("updated_at") or item.get("created_at")) >= since_token]
        search_token = _text(search).lower()
        if search_token:
            tickets = [item for item in tickets if search_token in _ticket_search_text(item)]
        sorted_tickets = sorted(tickets, key=lambda item: item.get("updated_at") or item.get("created_at") or "")
        if limit is not None and int(limit) >= 0:
            sorted_tickets = sorted_tickets[-int(limit):]
        if projection_token == "summary":
            return [
                _clone(project_development_ticket_summary(item))
                for item in sorted_tickets
            ]
        return [_clone(item) for item in sorted_tickets]

    def list_report_sync_candidates(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Internal relay index: never copy evidence, comments or Builder history."""
        rows = self._read_snapshot()["tickets"].values()
        relevant = [row for row in rows if row.get("source") in {"client_feedback", "ui_feedback"}
                    or _mapping(row.get("metadata")).get("development_report")]
        relevant.sort(key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)
        return [
            {"ticket_id": row.get("ticket_id"), "source": row.get("source"),
             "metadata": {"development_report": _clone(_mapping(row.get("metadata")).get("development_report") or {})}}
            for row in relevant[:max(0, min(int(limit), 1000))]
        ]

    def list_core_backlog(
        self,
        *,
        component_ref: str | None = None,
        impact: str | None = None,
        status_group: str | None = "open",
        affected_project_id: str | None = None,
        affected_subnet_id: str | None = None,
        release_target: str | None = None,
        verification_state: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        state = self._read_snapshot()
        tickets = {
            _text(ticket_id): _normalized_ticket(ticket)
            for ticket_id, ticket in state["tickets"].items()
        }
        core_tickets = self.list_tickets(
            owner_area="core",
            component_ref=component_ref,
            status_group=status_group,
            search=search,
        )
        impact_filter = {_text(item) for item in _text(impact).split(",") if _text(item)}
        items: list[dict[str, Any]] = []
        for ticket in core_tickets:
            metadata = _mapping(ticket.get("metadata"))
            relation_ids = {
                _text(ref.get("ticket_id"))
                for ref in _sequence_of_mappings(ticket.get("relation_refs") or [])
                if _text(ref.get("relation") or ref.get("type")) == "blocks"
                and _text(ref.get("ticket_id"))
            }
            relation_ids.update(
                _text(item)
                for item in metadata.get("blocked_ticket_ids") or []
                if _text(item)
            )
            affected = [tickets[ticket_id] for ticket_id in sorted(relation_ids) if ticket_id in tickets]
            project_ids = sorted(
                {
                    identity["project_id"]
                    for item in affected
                    for identity in [_project_identity_from_ticket(item)]
                    if identity.get("project_id")
                }
            )
            subnet_ids = sorted(
                {
                    _text(source.get("subnet_id"))
                    for item in affected
                    for source in (_mapping(item.get("target_scope")), _mapping(item.get("metadata")))
                    if _text(source.get("subnet_id"))
                }
            )
            impact_token = _text(metadata.get("impact") or "contract_gap")
            lifecycle = _mapping(metadata.get("core_lifecycle"))
            target = metadata.get("release_target") or lifecycle.get("release_ref")
            target_text = json.dumps(target, ensure_ascii=False, sort_keys=True, default=str) if target else ""
            status_token = _text(ticket.get("status"))
            verification = (
                "verified"
                if status_token in {"verified", "closed"}
                else "awaiting_verification"
                if status_token == "resolved"
                else "pending"
            )
            if impact_filter and impact_token not in impact_filter:
                continue
            if _text(affected_project_id) and _text(affected_project_id) not in project_ids:
                continue
            if _text(affected_subnet_id) and _text(affected_subnet_id) not in subnet_ids:
                continue
            if _text(release_target).lower() not in target_text.lower():
                continue
            if _text(verification_state) and verification != _text(verification_state):
                continue
            blocked_count = len(affected)
            priority_score = (
                _CORE_IMPACT_PRIORITY.get(impact_token, 20)
                + min(50, blocked_count * 10)
                + min(20, max(0, int(ticket.get("occurrence_count") or 1) - 1) * 4)
                + (15 if bool(ticket.get("blocking")) else 0)
                + (10 if target else 0)
            )
            items.append(
                {
                    "ticket_id": ticket["ticket_id"],
                    "revision": ticket["revision"],
                    "summary": ticket.get("summary"),
                    "status": status_token,
                    "status_group": ticket.get("status_group"),
                    "component_ref": ticket.get("component_ref"),
                    "impact": impact_token,
                    "blocking": bool(ticket.get("blocking")),
                    "priority_score": priority_score,
                    "affected_ticket_ids": [item["ticket_id"] for item in affected],
                    "affected_project_ids": project_ids,
                    "affected_subnet_ids": subnet_ids,
                    "release_target": _clone(target) if target else None,
                    "verification_state": verification,
                    "occurrence_count": int(ticket.get("occurrence_count") or 1),
                    "updated_at": ticket.get("updated_at"),
                }
            )
        items.sort(
            key=lambda item: (
                -int(item["priority_score"]),
                _text(item.get("updated_at")),
                _text(item.get("ticket_id")),
            )
        )
        bounded = items[: max(1, min(int(limit), 1000))]
        return {
            "schema": "adaos.dev_ticket.core_backlog.v1",
            "items": bounded,
            "count": len(bounded),
            "total": len(items),
            "filters": {
                "component_ref": _text(component_ref) or None,
                "impact": sorted(impact_filter),
                "status_group": _text(status_group) or None,
                "affected_project_id": _text(affected_project_id) or None,
                "affected_subnet_id": _text(affected_subnet_id) or None,
                "release_target": _text(release_target) or None,
                "verification_state": _text(verification_state) or None,
                "search": _text(search) or None,
            },
        }

    def prepare_external_issue_draft(
        self,
        ticket_id: str,
        *,
        actor: str,
        policy_mode: str = "draft_export",
        provider: str = "github",
        repository: str = "",
        visibility: str = "private",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        mode = (_text(policy_mode) or "draft_export").lower()
        if mode not in _EXTERNAL_ISSUE_POLICY_MODES or mode in {"none", "link_only", "mirror_status"}:
            raise ValueError(f"policy mode does not allow issue draft export: {mode}")
        visibility_token = (_text(visibility) or "private").lower()
        if visibility_token not in {"private", "public"}:
            raise ValueError("external issue visibility must be private or public")
        if mode == "public_upstream_issue" and visibility_token != "public":
            raise ValueError("public_upstream_issue requires public visibility")
        provider_token = (_text(provider) or "github").lower()
        repository_token = _text(repository)
        if mode in {"private_repo_issue", "public_upstream_issue"} and not repository_token:
            raise ValueError(f"{mode} requires repository")
        if provider_token == "github" and repository_token and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            repository_token,
        ):
            raise ValueError("GitHub repository must use owner/name format")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            metadata = _mapping(ticket.get("metadata"))
            title, title_findings = _redact_external_issue_text(ticket.get("summary"))
            sections: list[tuple[str, str]] = []
            for heading, key in (
                ("Motivation", "motivation"),
                ("Observed limitation", "observed_limitation"),
                ("Desired public contract", "desired_contract"),
            ):
                value, findings = _redact_external_issue_text(metadata.get(key))
                title_findings.extend(findings)
                if value:
                    sections.append((heading, value))
            workarounds: list[str] = []
            for raw in _sequence_of_mappings(metadata.get("rejected_workarounds") or []):
                value, findings = _redact_external_issue_text(
                    raw.get("reason") or raw.get("description") or raw.get("summary")
                )
                title_findings.extend(findings)
                if value:
                    workarounds.append(value)
            if workarounds:
                sections.append(("Rejected workarounds", "\n".join(f"- {item}" for item in workarounds)))
            safe_evidence, excluded_evidence = _safe_external_evidence(
                _sequence_of_mappings(ticket.get("evidence_refs") or [])
            )
            if safe_evidence:
                evidence_lines = [
                    "- " + ", ".join(f"{key}: {value}" for key, value in ref.items())
                    for ref in safe_evidence
                ]
                sections.append(("Public evidence", "\n".join(evidence_lines)))
            sections.append(
                (
                    "AdaOS provenance",
                    "Prepared from an internal Dev Ticket. Private evidence and local runtime context remain in AdaOS.",
                )
            )
            body = "\n\n".join(f"## {heading}\n\n{value}" for heading, value in sections)
            final_title, final_title_findings = _redact_external_issue_text(title)
            final_body, final_body_findings = _redact_external_issue_text(body)
            redaction_findings = sorted(
                set(title_findings + final_title_findings + final_body_findings + excluded_evidence)
            )
            draft_id = f"dissue.{new_id()}"
            now = _now()
            draft = {
                "schema": "adaos.dev_ticket.external_issue_draft.v1",
                "external_ref_id": draft_id,
                "provider": provider_token,
                "repository": repository_token or None,
                "issue_id": None,
                "target_path": f"{provider_token}:{repository_token or 'unassigned'}:issues/new",
                "privacy": visibility_token,
                "sync_mode": mode,
                "status": "awaiting_approval",
                "approval_required": True,
                "approved": False,
                "redaction": {
                    "passed": True,
                    "findings": redaction_findings,
                    "excluded_evidence_types": excluded_evidence,
                    "private_evidence_retained_locally": True,
                },
                "draft": {
                    "title": final_title,
                    "body": final_body,
                    "labels": [
                        "adaos-core" if _text(ticket.get("owner_area")) == "core" else "adaos-project",
                        f"impact:{_text(metadata.get('impact') or 'development')}",
                    ],
                    "safe_evidence_refs": safe_evidence,
                },
                "provenance": {
                    "ticket_id": ticket["ticket_id"],
                    "ticket_revision": int(ticket.get("revision") or 1),
                    "prepared_by": _text(actor) or "builder",
                    "prepared_at": now,
                },
            }
            ticket["external_refs"] = _merge_refs(ticket.get("external_refs") or [], [draft])
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "external_issue_draft_prepared",
                    "actor": _text(actor) or "builder",
                    "external_ref_id": draft_id,
                    "policy_mode": mode,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return {"ticket": _normalized_ticket(ticket), "draft": _clone(draft)}

    def approve_external_issue_draft(
        self,
        ticket_id: str,
        *,
        external_ref_id: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        actor_token = _text(actor)
        if not actor_token.startswith(("user:", "owner:", "core:maintainer")):
            raise ValueError("external issue draft approval requires a human owner or core maintainer")
        ref_token = _text(external_ref_id)
        if not ref_token:
            raise ValueError("external_ref_id is required")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            refs = [dict(ref) for ref in ticket.get("external_refs") or [] if isinstance(ref, Mapping)]
            selected: dict[str, Any] | None = None
            now = _now()
            for index, ref in enumerate(refs):
                if _text(ref.get("external_ref_id")) != ref_token:
                    continue
                if _text(ref.get("status")) != "awaiting_approval":
                    raise ValueError("external issue draft is not awaiting approval")
                if not bool(_mapping(ref.get("redaction")).get("passed")):
                    raise ValueError("external issue draft did not pass redaction")
                selected = {
                    **ref,
                    "status": "approved_for_export",
                    "approved": True,
                    "approved_by": actor_token,
                    "approved_at": now,
                }
                refs[index] = selected
                break
            if selected is None:
                raise KeyError(ref_token)
            ticket["external_refs"] = refs
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "external_issue_draft_approved",
                    "actor": actor_token,
                    "external_ref_id": ref_token,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return {"ticket": _normalized_ticket(ticket), "draft": _clone(selected)}

    def link_external_issue(
        self,
        ticket_id: str,
        *,
        provider: str,
        repository: str,
        issue_id: str,
        actor: str,
        target_path: str = "",
        privacy: str = "private",
        sync_mode: str = "link_only",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        actor_token = _text(actor)
        if not actor_token.startswith(("user:", "owner:", "core:maintainer")):
            raise ValueError("external issue linking requires a human owner or core maintainer")
        mode = (_text(sync_mode) or "link_only").lower()
        if mode not in _EXTERNAL_ISSUE_POLICY_MODES or mode == "none":
            raise ValueError(f"unsupported external issue sync mode: {mode}")
        provider_token = (_text(provider) or "github").lower()
        repository_token = _text(repository)
        issue_token = _text(issue_id)
        if not repository_token or not issue_token:
            raise ValueError("external issue repository and issue_id are required")
        if provider_token == "github" and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            repository_token,
        ):
            raise ValueError("GitHub repository must use owner/name format")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", issue_token):
            raise ValueError("external issue_id is invalid")
        privacy_token = (_text(privacy) or "private").lower()
        if privacy_token not in {"private", "public"}:
            raise ValueError("external issue privacy must be private or public")
        target_token = _text(target_path) or f"{provider_token}:{repository_token}:issues/{issue_token}"
        cleaned_target, target_findings = _redact_external_issue_text(target_token)
        if target_findings or cleaned_target != target_token:
            raise ValueError("external issue target_path contains private or credential-like data")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            now = _now()
            ref = {
                "schema": "adaos.dev_ticket.external_issue_ref.v1",
                "external_ref_id": f"dissue.{new_id()}",
                "provider": provider_token,
                "repository": repository_token,
                "issue_id": issue_token,
                "target_path": target_token,
                "privacy": privacy_token,
                "sync_mode": mode,
                "status": "linked",
                "provenance": {
                    "ticket_id": ticket["ticket_id"],
                    "ticket_revision": int(ticket.get("revision") or 1),
                    "linked_by": actor_token,
                    "linked_at": now,
                },
            }
            ticket["external_refs"] = _merge_refs(ticket.get("external_refs") or [], [ref])
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "external_issue_linked",
                    "actor": actor_token,
                    "external_ref_id": ref["external_ref_id"],
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return {"ticket": _normalized_ticket(ticket), "external_ref": _clone(ref)}

    @staticmethod
    def _history_lifecycle_event(
        ticket: Mapping[str, Any],
        history_item: Mapping[str, Any],
        *,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        ticket_id = _text(ticket.get("ticket_id"))
        kind = _text(history_item.get("kind")) or "changed"
        recorded_at = _text(history_item.get("recorded_at")) or _text(ticket.get("updated_at"))
        event_id = _text(history_item.get("event_id")) or "dtevent." + hashlib.sha256(
            f"{ticket_id}:{sequence if sequence is not None else -1}:{kind}:{recorded_at}".encode("utf-8")
        ).hexdigest()[:26]
        semantic_type = _text(history_item.get("semantic_type")) or f"dev_ticket.{kind}"
        payload = {
            key: _clone(value)
            for key, value in history_item.items()
            if key not in {"event_id", "semantic_type", "recorded_at"} and value is not None
        }
        status = _text(history_item.get("status")) or {
            "accepted": "accepted",
            "claimed": "claimed",
            "in_progress": "in_progress",
            "deferred": "deferred",
            "postponed": "deferred",
            "resolved": "resolved",
            "released": "resolved",
            "verified": "verified",
            "closed": "closed",
            "reopened": "accepted",
            "superseded": "superseded",
            "stale": "stale",
            "core_capability_verified": "ready_for_builder",
            "core_capability_reopened": "waiting_for_core",
            "core_capability_deferred": "waiting_for_core",
        }.get(kind, "unknown")
        identity = {
            "event_id": event_id,
            "semantic_type": semantic_type,
            "ticket_id": ticket_id,
            "owner_area": _text(ticket.get("owner_area")),
            "component_ref": _text(ticket.get("component_ref")) or None,
            "status": status,
            "recorded_at": recorded_at,
            "payload": payload,
        }
        digest = "sha256:" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return {
            "schema": DEV_TICKET_LIFECYCLE_EVENT_SCHEMA,
            **identity,
            "source_authority": "core:development_tickets",
            "integrity": {
                "algorithm": "sha256",
                "digest": digest,
                "transport_authentication": "adaos.runtime.event_bus",
                "cryptographic_signature": False,
            },
        }

    def list_lifecycle_events(
        self,
        *,
        after: str | None = None,
        updated_since: str | None = None,
        ticket_id: str | None = None,
        owner_area: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        state = self._read_snapshot()
        events: list[dict[str, Any]] = []
        for ticket in state["tickets"].values():
            if _text(ticket_id) and _text(ticket.get("ticket_id")) != _text(ticket_id):
                continue
            if _text(owner_area) and _text(ticket.get("owner_area")) != _text(owner_area):
                continue
            for sequence, item in enumerate(ticket.get("history") or []):
                if not isinstance(item, Mapping):
                    continue
                event = self._history_lifecycle_event(ticket, item, sequence=sequence)
                if _text(updated_since) and event["recorded_at"] < _text(updated_since):
                    continue
                events.append(event)
        events.sort(key=lambda item: (item["recorded_at"], item["event_id"]))
        cursor = _text(after)
        if cursor:
            index = next(
                (index for index, event in enumerate(events) if event["event_id"] == cursor),
                None,
            )
            events = events[index + 1 :] if index is not None else []
        return events[-max(1, min(int(limit), 2000)) :]

    def read_change_feed(
        self,
        *,
        after: str | None = None,
        updated_since: str | None = None,
        include_snapshot: bool = True,
        status_group: str = "open",
        target_tokens: Sequence[str] = (),
        project_id: str | None = None,
        scenario_id: str | None = None,
        skill_id: str | None = None,
        modal_id: str | None = None,
        component: str | None = None,
        kind: str | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        scope_tokens = [*target_tokens]
        if _text(project_id):
            scope_tokens.extend([_text(project_id), f"project:{_text(project_id)}"])
        relevant = self.list_tickets(
            target_tokens=scope_tokens,
            scenario_id=scenario_id,
            skill_id=skill_id,
            modal_id=modal_id,
            component=component,
            kind=kind,
            owner_area=owner_area,
            component_ref=component_ref,
            search=search,
        )
        relevant_ids = {_text(ticket.get("ticket_id")) for ticket in relevant}
        scanned_events = self.list_lifecycle_events(
            after=after,
            updated_since=updated_since,
            owner_area=owner_area,
            limit=limit,
        )
        events = [event for event in scanned_events if _text(event.get("ticket_id")) in relevant_ids]
        snapshot: list[dict[str, Any]] = []
        if include_snapshot and not _text(after):
            allowed_statuses: set[str] = set()
            for group in _text(status_group).split(","):
                allowed_statuses.update(TICKET_STATUS_GROUPS.get(_text(group), {_text(group)}))
            snapshot = [
                ticket
                for ticket in relevant
                if not allowed_statuses or _text(ticket.get("status")) in allowed_statuses
            ]
        cursor = (
            _text(scanned_events[-1].get("event_id"))
            if scanned_events
            else _text(after) or None
        )
        return {
            "schema": "adaos.dev_ticket.change_feed.v1",
            "snapshot": snapshot,
            "events": events,
            "cursor": cursor,
            "scanned_event_count": len(scanned_events),
            "matched_event_count": len(events),
            "relevance": {
                "target_tokens": sorted({_text(item) for item in scope_tokens if _text(item)}),
                "scenario_id": _text(scenario_id) or None,
                "skill_id": _text(skill_id) or None,
                "modal_id": _text(modal_id) or None,
                "component": _text(component) or None,
                "owner_area": _text(owner_area) or None,
                "component_ref": _text(component_ref) or None,
                "search": _text(search) or None,
            },
        }

    @staticmethod
    def _publish_lifecycle_event(event: Mapping[str, Any]) -> None:
        try:
            from adaos.services.agent_context import get_ctx
            from adaos.services.eventbus import emit as bus_emit

            ctx = get_ctx()
            bus = getattr(ctx, "bus", None)
            if bus is None:
                return
            bus_emit(
                bus,
                _text(event.get("semantic_type")) or "dev_ticket.changed",
                dict(event),
                "development_tickets",
                event_id=_text(event.get("event_id")) or None,
                source_authority="core:development_tickets",
                schema=DEV_TICKET_LIFECYCLE_EVENT_SCHEMA,
                version=1,
            )
            if _text(event.get("semantic_type")) == "core_ticket.verified":
                bus_emit(
                    bus,
                    "core_capability.available",
                    dict(event),
                    "development_tickets",
                    event_id=_text(event.get("event_id")) or None,
                    source_authority="core:development_tickets",
                    schema=DEV_TICKET_LIFECYCLE_EVENT_SCHEMA,
                    version=1,
                )
        except Exception:
            _log.debug("Dev Ticket lifecycle event publication skipped", exc_info=True)

    def list_artifacts(self, ticket_id: str | None = None) -> list[dict[str, Any]]:
        root = self.root / "artifacts"
        if not root.is_dir():
            return []
        wanted: set[str] = set()
        if _text(ticket_id):
            ticket = self.get_ticket(_text(ticket_id))
            if not ticket:
                raise KeyError(_text(ticket_id))
            for ref in [
                *_sequence_of_mappings(ticket.get("artifact_refs") or []),
                *_sequence_of_mappings(ticket.get("evidence_refs") or []),
            ]:
                artifact_id = _artifact_id_from_ref(ref)
                if artifact_id:
                    wanted.add(artifact_id)
            for signal_id in ticket.get("signal_ids") or []:
                signal = self.get_signal(_text(signal_id))
                if not signal:
                    continue
                for ref in [
                    *_sequence_of_mappings(signal.get("artifact_refs") or []),
                    *_sequence_of_mappings(signal.get("evidence_refs") or []),
                ]:
                    artifact_id = _artifact_id_from_ref(ref)
                    if artifact_id:
                        wanted.add(artifact_id)
        items: list[dict[str, Any]] = []
        for path in root.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            artifact_id = _text(manifest.get("artifact_id") or path.stem)
            if wanted and artifact_id not in wanted:
                continue
            items.append({**manifest, "manifest_path": str(path)})
        return sorted(items, key=lambda item: _text(item.get("artifact_id")))

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        token = _text(artifact_id)
        if not token or "/" in token or "\\" in token or ".." in token:
            return None
        manifest_path = self.root / "artifacts" / f"{token}.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        file_name = _text(manifest.get("file_name"))
        file_path = self.root / "artifacts" / file_name if file_name else None
        return {
            **manifest,
            "manifest_path": str(manifest_path),
            "path": str(file_path) if file_path else None,
            "exists": bool(file_path and file_path.is_file()),
        }

    def _create_builder_repair(
        self,
        ticket: Mapping[str, Any],
        *,
        mode: str,
        repair_service: BuilderRepairService | None,
    ) -> dict[str, Any]:
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        target = _mapping(ticket.get("target_scope"))
        automation_target = _automation_target_from_ticket(ticket)
        ticket_id = _text(ticket.get("ticket_id"))
        signal_ids = [_text(item) for item in ticket.get("signal_ids") or [] if _text(item)]
        source_refs = [
            {"type": "dev_ticket", "id": ticket_id},
            *({"type": "development_signal", "id": signal_id} for signal_id in signal_ids),
            *_sequence_of_mappings(ticket.get("evidence_refs") or []),
        ]
        report = service.report(
            project_id=automation_target["object_id"],
            signal_type="guard",
            summary=_text(ticket.get("summary")) or "Runtime compatibility debt",
            source_refs=source_refs,
            context={
                "development_ticket": {
                    "ticket_id": ticket_id,
                    "signal_ids": signal_ids,
                    "handoff_mode": mode,
                },
                "target_scope": target,
                "development_source": development_source_options(
                    _development_source_scope(ticket, automation_target)
                ),
                "compatibility": _mapping(ticket.get("metadata")),
                "policy": _mapping(ticket.get("policy")),
                "economic": {
                    "schema": "adaos.builder.codex_token_accounting.v1",
                    "subscription_resource": "codex.api.tokens",
                    "source_of_truth": "adaos.root_mgmnt.codex_usage_event.v1",
                    "usage_event_endpoint": "/hub/economic/codex/usage",
                    "required_for_statuses": ["succeeded", "failed", "errored", "cancelled"],
                    "policy": "record provider-reported billable tokens even when repair work fails",
                },
                "acceptance": {
                    "checks": [
                        "strict skill/scenario validation passes",
                        "activation or smoke import passes",
                        "expected receiver admission passes",
                        "unrelated receiver admission remains denied",
                    ]
                },
            },
            design_time_fixable=bool(_mapping(ticket.get("policy")).get("design_time_fixable", True)),
            dedup_key=f"repair:{ticket.get('dedup_key')}",
        )
        return report["task"]

    def _link_builder_repair(self, ticket_id: str, repair: Mapping[str, Any], *, mode: str, actor: str) -> dict[str, Any]:
        repair_id = _text(repair.get("repair_id"))
        ref = {
            "type": "builder_repair_task",
            "repair_id": repair_id,
            "mode": mode,
            "status": repair.get("status"),
            "created_at": repair.get("created_at"),
        }
        economic = _mapping(_mapping(repair.get("context")).get("economic"))
        if economic:
            ref["token_accounting"] = economic
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            ticket["status"] = "in_builder"
            ticket["builder_refs"] = _merge_refs(ticket.get("builder_refs") or [], [ref])
            ticket["updated_at"] = _now()
            self._append_history(
                ticket,
                {
                    "kind": "builder_handoff",
                    "mode": mode,
                    "repair_id": repair_id,
                    "actor": actor,
                    "recorded_at": ticket["updated_at"],
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "repair_created"
                    signal["builder_ref"] = {
                        **_mapping(signal.get("builder_ref")),
                        "repair_id": repair_id,
                        "handoff_mode": mode,
                    }
                    signal["updated_at"] = ticket["updated_at"]
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def prepare_builder_repair_qualification(
        self,
        ticket_id: str,
        *,
        actor: str = "builder.qualifier",
        apply: bool = False,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Derive a bounded repair envelope from authoritative DEV source without model use."""

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        if _text(ticket.get("status")) in {*TERMINAL_TICKET_STATES, "resolved", "verified"}:
            raise ValueError("completed Dev Ticket cannot be qualified")
        target = _automation_target_from_ticket(ticket)
        source_scope = _development_source_scope(ticket, target)
        development_source = development_source_options(source_scope)
        if (
            development_source.get("status") == "source_available"
            and not _text(
                development_source.get("dev_source_path")
                or development_source.get("source_path")
            )
        ):
            try:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                resolved_source = BuilderWorkspaceService.from_context().development_source_status(
                    kind=target["object_type"],
                    artifact_id=target["object_id"],
                    project_id=_project_id_for_materialization(ticket, development_source),
                )
                if resolved_source:
                    development_source = {
                        **development_source,
                        **resolved_source,
                    }
            except Exception:
                pass
        from adaos.services.builder.ticket_qualification import prepare_repair_qualification

        candidate = prepare_repair_qualification(
            ticket,
            development_source=development_source,
            object_type=target["object_type"],
            object_id=target["object_id"],
        )
        result: dict[str, Any] = {
            "ok": True,
            "applied": False,
            "ticket": ticket,
            "development_source": development_source,
            "qualification_candidate": candidate,
            "autonomous_repair_qualification": _autonomous_repair_qualification(ticket),
        }
        if not apply:
            return result
        if candidate.get("ready") is not True or _text(candidate.get("confidence")) != "high":
            raise ValueError("local Builder qualification is not ready for automatic application")
        builder_repair = _mapping(candidate.get("builder_repair"))
        updated = self.requalify_builder_repair(
            ticket_id,
            builder_repair=builder_repair,
            actor=_text(actor) or "builder.qualifier",
            reason="deterministic qualification from authoritative DEV source index",
            expected_revision=expected_revision,
        )
        result.update(
            {
                "applied": True,
                "ticket": updated,
                "autonomous_repair_qualification": _autonomous_repair_qualification(updated),
            }
        )
        return result

    def publish_publication_permission_pending_action(
        self,
        ticket_id: str,
        *,
        object_type: str,
        object_id: str,
        task_id: str,
        package_digest: str = "",
        permissions: Sequence[str] = (),
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask for the attended permission decision required by Trial.

        This is deliberately separate from Builder repair qualification.  A
        missing permission decision is not source code that Codex can repair;
        it is an operator decision bound to one validated task/checkpoint.
        """

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        active = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == PUBLICATION_PERMISSION_PENDING_ACTION_KIND
            and _text(ref.get("status") or "pending") in {"pending", "postponed"}
        ]
        if active:
            return {
                "ok": True,
                "published": False,
                "reason": "permission_decision_already_pending",
                "pending_action": active[-1],
                "ticket": ticket,
            }

        permission_ids = sorted(
            dict.fromkeys(_text(item) for item in permissions if _text(item))
        )
        permission_text = ", ".join(permission_ids) or "the permissions in the Trial plan"
        question = (
            f"Allow the validated beta candidate {object_type}:{object_id} to use "
            f"{permission_text}? The decision applies only to task {task_id}."
        )
        from adaos.services import pending_actions

        action = pending_actions.publish_pending_action(
            ctx=ctx,
            webspace_id=webspace_id,
            kind=PUBLICATION_PERMISSION_PENDING_ACTION_KIND,
            title="Trial permission approval required",
            summary=ticket["summary"],
            request_text=question,
            producer={"type": "system", "system_id": "development_tickets"},
            owner_scope=ticket.get("owner_scope")
            or {"type": "workspace", "id": "local"},
            domain_ref={
                "ticket_id": ticket["ticket_id"],
                "object_type": _text(object_type),
                "object_id": _text(object_id),
                "task_id": _text(task_id),
                "package_digest": _text(package_digest),
            },
            allowed_actions=[
                {"id": "approve", "label": "Approve", "terminal": True},
                {"id": "refuse", "label": "Refuse", "terminal": True},
                {"id": "postpone", "label": "Later", "terminal": False},
            ],
            response_topic=PUBLICATION_PERMISSION_RESPONSE_TOPIC,
            metadata={
                "schema": "adaos.dev_ticket.publication_permission.pending_action_metadata.v1",
                "ticket_id": ticket["ticket_id"],
                "task_id": _text(task_id),
                "package_digest": _text(package_digest) or None,
                "permissions": permission_ids,
                "question": question,
            },
        )
        ref = {
            "id": action.get("id"),
            "kind": action.get("kind"),
            "status": action.get("status"),
            "created_at": action.get("created_at"),
            "task_id": _text(task_id),
            "package_digest": _text(package_digest) or None,
        }
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=_merge_refs(
                ticket.get("pending_action_refs") or [], [ref]
            ),
            status="waiting_for_user",
            history_item={
                "kind": "publication_permission_requested",
                "pending_action_id": ref.get("id"),
                "task_id": _text(task_id),
                "package_digest": _text(package_digest) or None,
                "permissions": permission_ids,
            },
        )
        return {
            "ok": True,
            "published": True,
            "pending_action": action,
            "ticket": updated,
        }

    def handle_publication_permission_response(
        self,
        *,
        ticket_id: str,
        response_action_id: str,
        object_type: str,
        object_id: str,
        task_id: str,
        package_digest: str = "",
        pending_action_id: str | None = None,
        responder: Mapping[str, Any] | None = None,
        response_payload: Mapping[str, Any] | None = None,
        resume: bool = True,
    ) -> dict[str, Any]:
        """Record an attended permission decision and resume without Codex."""

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        action = _text(response_action_id)
        actor = (
            _text(_mapping(responder).get("actor") or _mapping(responder).get("id"))
            or "user:owner"
        )
        if action == "postpone":
            updated = self._update_ticket(
                ticket_id,
                pending_action_refs=_with_pending_action_ref_status(
                    ticket.get("pending_action_refs") or [],
                    action_id=pending_action_id,
                    status="postponed",
                ),
                status="waiting_for_user",
                history_item={
                    "kind": "publication_permission_postponed",
                    "pending_action_id": _text(pending_action_id),
                    "actor": actor,
                },
            )
            return {"ok": True, "action": action, "ticket": updated, "recovery": None}
        if action not in {"approve", "refuse"}:
            raise ValueError(f"unsupported publication permission response: {action}")

        approved = action == "approve"
        decision = {
            "approved": approved,
            "actor": actor,
            "actor_type": "user",
            "approval_id": f"pending-action:{_text(pending_action_id) or ticket_id}",
            "reason": _text(_mapping(response_payload).get("text"))[:1000]
            or ("approved in Pending Actions" if approved else "refused in Pending Actions"),
            "task_id": _text(task_id),
            "package_digest": _text(package_digest) or None,
        }
        metadata = _mapping(ticket.get("metadata"))
        metadata["publication_permission_decision"] = decision
        updated = self._update_ticket(
            ticket_id,
            pending_action_refs=_with_pending_action_ref_status(
                ticket.get("pending_action_refs") or [],
                action_id=pending_action_id,
                status="responded",
            ),
            metadata=metadata,
            status="accepted",
            history_item={
                "kind": "publication_permission_decided",
                "pending_action_id": _text(pending_action_id),
                "actor": actor,
                "approved": approved,
                "task_id": _text(task_id),
                "package_digest": _text(package_digest) or None,
            },
        )
        recovery = None
        if approved and resume:
            from adaos.services.builder.automation import BuilderAutomationService

            recovery = BuilderAutomationService.from_context(
                background=True
            ).recover_validated_result(
                object_type=_text(object_type),
                object_id=_text(object_id),
                permission_decision=decision,
            )
        return {
            "ok": bool(not approved or not resume or _mapping(recovery).get("ok")),
            "action": action,
            "ticket": self.get_ticket(ticket_id) or updated,
            "decision": decision,
            "recovery": recovery,
        }

    def publish_builder_clarification_pending_action(
        self,
        ticket_id: str,
        *,
        question: str,
        qualification_request_id: str,
        reason: str = "",
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        question_text = _text(question)[:500]
        if not question_text:
            raise ValueError("clarification question is required")
        metadata = _mapping(ticket.get("metadata"))
        responses = _sequence_of_mappings(metadata.get("clarification_responses") or [])
        if len(responses) >= 2:
            return {
                "ok": True,
                "published": False,
                "reason": "clarification_limit_reached",
                "ticket": ticket,
            }
        answered_action_ids = {
            _text(item.get("pending_action_id"))
            for item in responses
            if _text(item.get("pending_action_id"))
        }
        unresolved = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == BUILDER_CLARIFICATION_PENDING_ACTION_KIND
            and _text(ref.get("id")) not in answered_action_ids
        ]
        if unresolved:
            return {
                "ok": True,
                "published": False,
                "reason": "clarification_already_pending",
                "pending_action": unresolved[-1],
                "ticket": ticket,
            }
        existing = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == BUILDER_CLARIFICATION_PENDING_ACTION_KIND
            and _text(ref.get("qualification_request_id"))
            == _text(qualification_request_id)
        ]
        if existing:
            return {
                "ok": True,
                "published": False,
                "reason": "pending_action_already_linked",
                "pending_action": existing[-1],
                "ticket": ticket,
            }
        from adaos.services import pending_actions

        action = pending_actions.publish_pending_action(
            ctx=ctx,
            webspace_id=webspace_id,
            kind=BUILDER_CLARIFICATION_PENDING_ACTION_KIND,
            title="Builder needs clarification",
            summary=ticket["summary"],
            request_text=question_text,
            producer={"type": "system", "system_id": "development_tickets"},
            owner_scope=ticket.get("owner_scope")
            or {"type": "workspace", "id": "local"},
            domain_ref={
                "ticket_id": ticket["ticket_id"],
                "ticket_revision": ticket.get("revision"),
                "qualification_request_id": _text(qualification_request_id),
            },
            allowed_actions=[
                {"id": "submit_answer", "label": "Submit answer", "terminal": True},
                {"id": "postpone", "label": "Later", "terminal": True},
            ],
            default_text_binding=True,
            response_topic=BUILDER_CLARIFICATION_RESPONSE_TOPIC,
            metadata={
                "schema": "adaos.dev_ticket.builder_clarification.pending_action_metadata.v1",
                "ticket_id": ticket["ticket_id"],
                "qualification_request_id": _text(qualification_request_id),
                "question": question_text,
                "reason": _text(reason)[:1000] or None,
                "response_schema": {"type": "text", "min_length": 1, "max_length": 2000},
            },
        )
        ref = {
            "id": action.get("id"),
            "kind": action.get("kind"),
            "status": action.get("status"),
            "created_at": action.get("created_at"),
            "qualification_request_id": _text(qualification_request_id),
        }
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=_merge_refs(
                ticket.get("pending_action_refs") or [], [ref]
            ),
            status="waiting_for_user",
            history_item={
                "kind": "builder_clarification_requested",
                "pending_action_id": ref.get("id"),
                "qualification_request_id": _text(qualification_request_id),
                "question": question_text,
            },
        )
        return {
            "ok": True,
            "published": True,
            "pending_action": action,
            "ticket": updated,
        }

    def _supersede_builder_clarification_pending_actions(
        self,
        ticket: Mapping[str, Any],
        *,
        reason: str,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(ticket.get("pending_action_refs") or [])
        active = [
            ref
            for ref in refs
            if ref.get("kind") == BUILDER_CLARIFICATION_PENDING_ACTION_KIND
            and _text(ref.get("status") or "pending") in {"pending", "postponed"}
            and _text(ref.get("id"))
        ]
        if not active:
            return dict(ticket)
        from adaos.services import pending_actions

        updated_refs = [dict(ref) for ref in refs]
        cancelled_ids: list[str] = []
        for ref in active:
            action_id = _text(ref.get("id"))
            try:
                pending_actions.cancel_pending_action(
                    action_id,
                    reason=reason,
                    ctx=ctx,
                    webspace_id=webspace_id,
                    actor={"type": "system", "system_id": "development_tickets"},
                )
            except Exception:
                continue
            updated_refs = _with_pending_action_ref_status(
                updated_refs,
                action_id=action_id,
                status="cancelled",
                reason=reason,
            )
            cancelled_ids.append(action_id)
        if not cancelled_ids:
            return dict(ticket)
        return self._update_ticket(
            _text(ticket.get("ticket_id")),
            pending_action_refs=updated_refs,
            history_item={
                "kind": "builder_clarifications_superseded",
                "pending_action_ids": cancelled_ids,
                "reason": _text(reason),
            },
        )

    def _supersede_publication_permission_pending_actions(
        self,
        ticket: Mapping[str, Any],
        *,
        reason: str,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(ticket.get("pending_action_refs") or [])
        active = [
            ref
            for ref in refs
            if ref.get("kind") == PUBLICATION_PERMISSION_PENDING_ACTION_KIND
            and _text(ref.get("status") or "pending") in {"pending", "postponed"}
            and _text(ref.get("id"))
        ]
        if not active:
            return dict(ticket)
        from adaos.services import pending_actions

        updated_refs = [dict(ref) for ref in refs]
        cancelled_ids: list[str] = []
        for ref in active:
            action_id = _text(ref.get("id"))
            try:
                pending_actions.cancel_pending_action(
                    action_id,
                    ctx=ctx,
                    webspace_id=webspace_id,
                    reason=_text(reason) or "ticket_terminal",
                )
            except (KeyError, ValueError):
                pass
            updated_refs = _with_pending_action_ref_status(
                updated_refs,
                action_id=action_id,
                status="cancelled",
            )
            cancelled_ids.append(action_id)
        return self._update_ticket(
            _text(ticket.get("ticket_id")),
            pending_action_refs=updated_refs,
            history_item={
                "kind": "publication_permission_requests_superseded",
                "pending_action_ids": cancelled_ids,
                "reason": _text(reason) or "ticket_terminal",
            },
        )

    def handle_builder_clarification_response(
        self,
        *,
        ticket_id: str,
        response_action_id: str,
        response_payload: Mapping[str, Any] | None = None,
        pending_action_id: str | None = None,
        qualification_request_id: str | None = None,
        responder: Mapping[str, Any] | None = None,
        requalify: bool = True,
        llm_call: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        action = _text(response_action_id)
        actor = (
            _text(_mapping(responder).get("actor") or _mapping(responder).get("id"))
            or "pending_action"
        )
        if action == "postpone":
            updated = self._update_ticket(
                ticket_id,
                status="deferred",
                pending_action_refs=_with_pending_action_ref_status(
                    ticket.get("pending_action_refs") or [],
                    action_id=pending_action_id,
                    status="responded",
                    reason="user_postponed",
                ),
                history_item={
                    "kind": "builder_clarification_postponed",
                    "pending_action_id": _text(pending_action_id),
                    "actor": actor,
                },
            )
            return {"ok": True, "action": action, "ticket": updated, "qualification": None}
        if action != "submit_answer":
            raise ValueError(f"unsupported clarification response: {action}")
        payload = _mapping(response_payload)
        answer = _text(payload.get("text") or payload.get("answer"))[:2000]
        if not answer:
            raise ValueError("clarification answer is required")
        metadata = _mapping(ticket.get("metadata"))
        qualification = _mapping(metadata.get("builder_language_qualification"))
        candidate = _mapping(qualification.get("qualification_candidate"))
        question = _text(candidate.get("clarification_question")) or _text(
            payload.get("question")
        )
        response_record = {
            "schema": "adaos.dev_ticket.clarification_response.v1",
            "qualification_request_id": _text(qualification_request_id)
            or _text(qualification.get("request_id")),
            "pending_action_id": _text(pending_action_id) or None,
            "question": question[:500] or None,
            "answer": answer,
            "actor": actor,
            "recorded_at": _now(),
        }
        responses = [
            *_sequence_of_mappings(metadata.get("clarification_responses") or []),
            response_record,
        ][-2:]
        metadata["clarification_responses"] = responses
        updated = self._update_ticket(
            ticket_id,
            metadata=metadata,
            status="captured",
            pending_action_refs=_with_pending_action_ref_status(
                ticket.get("pending_action_refs") or [],
                action_id=pending_action_id,
                status="responded",
                reason="user_answered",
            ),
            evidence_refs=_merge_refs(
                ticket.get("evidence_refs") or [],
                [
                    {
                        "type": "user_clarification",
                        "id": response_record["qualification_request_id"],
                        "pending_action_id": response_record["pending_action_id"],
                    }
                ],
            ),
            history_item={
                "kind": "builder_clarification_answered",
                "pending_action_id": response_record["pending_action_id"],
                "qualification_request_id": response_record[
                    "qualification_request_id"
                ],
                "actor": actor,
            },
        )
        rerun = None
        if requalify:
            rerun = self.qualify_builder_repair_language(
                ticket_id,
                actor="builder.clarification",
                apply=True,
                expected_revision=int(updated.get("revision") or 1),
                llm_call=llm_call,
                publish_pending_action=True,
            )
            updated = rerun["ticket"]
        return {
            "ok": True,
            "action": action,
            "ticket": updated,
            "qualification": rerun,
        }

    def qualify_builder_repair_language(
        self,
        ticket_id: str,
        *,
        actor: str = "builder.language_qualifier",
        apply: bool = False,
        expected_revision: int | None = None,
        llm_call: Callable[..., Mapping[str, Any]] | None = None,
        publish_pending_action: bool = True,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Use one Root-accounted LLM call only when local qualification is ambiguous."""

        deterministic = self.prepare_builder_repair_qualification(
            ticket_id,
            actor=actor,
            apply=False,
            expected_revision=expected_revision,
        )
        ticket = dict(deterministic["ticket"])
        candidate = _mapping(deterministic.get("qualification_candidate"))
        if candidate.get("ready") is True and _text(candidate.get("confidence")) == "high":
            if apply:
                deterministic = self.prepare_builder_repair_qualification(
                    ticket_id,
                    actor=actor,
                    apply=True,
                    expected_revision=expected_revision,
                )
                deterministic["ticket"] = (
                    self._supersede_builder_clarification_pending_actions(
                        deterministic["ticket"],
                        reason="qualification_ready",
                        ctx=ctx,
                        webspace_id=webspace_id,
                    )
                )
            return {
                **deterministic,
                "qualification_mode": "deterministic",
                "language_model_called": False,
                "language_qualification_usage": None,
            }
        if _text(candidate.get("recommended_next")) != (
            "bounded_language_qualification_or_user_clarification"
        ):
            return {
                **deterministic,
                "qualification_mode": "deterministic",
                "language_model_called": False,
                "language_qualification_usage": None,
            }
        source_index = _mapping(candidate.get("source_index"))
        if not _sequence_of_mappings(source_index.get("entries") or []):
            return {
                **deterministic,
                "qualification_mode": "deterministic",
                "language_model_called": False,
                "language_qualification_usage": None,
            }

        request_id = _language_qualification_request_id(ticket, candidate)
        caller = llm_call
        if caller is None:
            from adaos.sdk.llm.llm_client import send_response

            caller = send_response
        response: Mapping[str, Any] = {}
        try:
            response = caller(
                _language_qualification_messages(ticket, candidate),
                max_tokens=800,
                text={"format": {"type": "json_object"}},
                request_id=request_id,
                prompt_cache_key="adaos.builder.ticket-language-qualification.v1",
                timeout=45,
            )
            if not isinstance(response, Mapping):
                raise ValueError("Root LLM qualification response must be an object")
        except Exception as exc:
            usage = _language_qualification_usage_receipt(
                response,
                request_id=request_id,
                status="failed",
            )
            fallback = {
                "schema": "adaos.builder.repair_qualification_candidate.v1",
                "status": "needs_clarification",
                "ready": False,
                "confidence": "low",
                "model_call_expected": False,
                "recommended_next": "user_clarification",
                "reason": (
                    "Root language qualification is unavailable; user clarification "
                    "is required before Builder model spend"
                ),
                "clarification_question": (
                    "Which visible component or action should change, and what should "
                    "happen instead?"
                ),
            }
            updated_ticket = self._record_builder_language_qualification(
                ticket_id,
                record={
                    "schema": "adaos.builder.language_qualification_record.v1",
                    "request_id": request_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": _text(exc)[:1000],
                    "usage": usage,
                    "base_ticket_revision": ticket.get("revision"),
                    "recorded_at": _now(),
                },
                actor=actor,
                expected_updated_at=_text(ticket.get("updated_at")),
                expected_revision=expected_revision,
            )
            pending_action = None
            pending_action_published = False
            if publish_pending_action:
                clarification = self.publish_builder_clarification_pending_action(
                    ticket_id,
                    question=fallback["clarification_question"],
                    qualification_request_id=request_id,
                    reason=fallback["reason"],
                    ctx=ctx,
                    webspace_id=webspace_id,
                )
                pending_action = clarification.get("pending_action")
                pending_action_published = bool(clarification.get("published"))
                updated_ticket = clarification.get("ticket") or updated_ticket
            return {
                "ok": True,
                "applied": False,
                "ticket": updated_ticket,
                "development_source": deterministic.get("development_source"),
                "qualification_candidate": fallback,
                "autonomous_repair_qualification": _autonomous_repair_qualification(
                    updated_ticket
                ),
                "qualification_mode": "user_clarification_fallback",
                "language_model_called": True,
                "language_qualification_usage": usage,
                "development_feedback": [],
                "pending_action": pending_action,
                "pending_action_published": pending_action_published,
            }

        usage = _language_qualification_usage_receipt(
            response,
            request_id=request_id,
            status="completed",
        )
        rejected_output = ""
        rejection_error = ""
        try:
            proposal = _parse_language_qualification_output(response.get("output_text"))
            errors = sorted(
                Draft202012Validator(
                    _schema("builder.language_qualification_proposal.v1")
                ).iter_errors(proposal),
                key=lambda item: list(item.path),
            )
            if errors:
                raise ValueError(
                    "invalid language qualification proposal: " + errors[0].message
                )
            target = _automation_target_from_ticket(ticket)
            from adaos.services.builder.ticket_qualification import (
                resolve_language_qualification_proposal,
            )

            resolved = resolve_language_qualification_proposal(
                ticket,
                proposal,
                development_source=_mapping(deterministic.get("development_source")),
                object_type=target["object_type"],
                object_id=target["object_id"],
            )
            feedback_records = self._record_language_development_feedback(
                ticket,
                proposal=proposal,
                usage=usage,
                request_id=request_id,
                actor=actor,
            )
        except ValueError as exc:
            rejected_output = _text(response.get("output_text"))[:8000]
            rejection_error = _text(exc)[:1000]
            proposal = {}
            feedback_records = []
            resolved = {
                "schema": "adaos.builder.repair_qualification_candidate.v1",
                "status": "needs_clarification",
                "ready": False,
                "confidence": "low",
                "model_call_expected": False,
                "recommended_next": "user_clarification",
                "reason": f"bounded language qualification was rejected: {_text(exc)[:500]}",
                "clarification_question": (
                    "Which visible component or action should be changed?"
                ),
            }

        applied = False
        updated_ticket = ticket
        if resolved.get("ready") is True and _text(resolved.get("confidence")) == "high" and apply:
            updated_ticket = self.requalify_builder_repair(
                ticket_id,
                builder_repair=_mapping(resolved.get("builder_repair")),
                actor=_text(actor) or "builder.language_qualifier",
                reason="Root language proposal resolved against authoritative DEV source index",
                expected_updated_at=_text(ticket.get("updated_at")),
                expected_revision=expected_revision,
            )
            applied = True

        record_status = (
            "applied"
            if applied
            else "qualified"
            if resolved.get("ready") is True
            else "needs_clarification"
        )
        record = {
            "schema": "adaos.builder.language_qualification_record.v1",
            "request_id": request_id,
            "status": record_status,
            "proposal": proposal,
            "qualification_candidate": _language_qualification_candidate_projection(
                resolved
            ),
            "usage": usage,
            "development_feedback_refs": [
                item["feedback_id"] for item in feedback_records
            ],
            "base_ticket_revision": ticket.get("revision"),
            "recorded_at": _now(),
        }
        if rejected_output:
            record["rejected_output"] = rejected_output
            record["rejection_error"] = rejection_error
        updated_ticket = self._record_builder_language_qualification(
            ticket_id,
            record=record,
            actor=actor,
            expected_updated_at=_text(updated_ticket.get("updated_at")),
            expected_revision=None if applied else expected_revision,
        )
        if applied:
            updated_ticket = self._supersede_builder_clarification_pending_actions(
                updated_ticket,
                reason="qualification_ready",
                ctx=ctx,
                webspace_id=webspace_id,
            )
        pending_action = None
        pending_action_published = False
        if (
            publish_pending_action
            and resolved.get("ready") is not True
            and _text(resolved.get("clarification_question"))
        ):
            clarification = self.publish_builder_clarification_pending_action(
                ticket_id,
                question=_text(resolved.get("clarification_question")),
                qualification_request_id=request_id,
                reason=_text(resolved.get("reason")),
                ctx=ctx,
                webspace_id=webspace_id,
            )
            pending_action = clarification.get("pending_action")
            pending_action_published = bool(clarification.get("published"))
            updated_ticket = clarification.get("ticket") or updated_ticket
        return {
            "ok": True,
            "applied": applied,
            "ticket": updated_ticket,
            "development_source": deterministic.get("development_source"),
            "qualification_candidate": resolved,
            "autonomous_repair_qualification": _autonomous_repair_qualification(
                updated_ticket
            ),
            "qualification_mode": "bounded_language_llm",
            "language_model_called": True,
            "language_qualification_usage": usage,
            "development_feedback": feedback_records,
            "pending_action": pending_action,
            "pending_action_published": pending_action_published,
        }

    def _record_language_development_feedback(
        self,
        ticket: Mapping[str, Any],
        *,
        proposal: Mapping[str, Any],
        usage: Mapping[str, Any],
        request_id: str,
        actor: str,
    ) -> list[dict[str, Any]]:
        raw_items = proposal.get("development_feedback")
        if not isinstance(raw_items, list):
            return []
        from adaos.services.development_feedback import DevelopmentFeedbackService

        feedback = DevelopmentFeedbackService(state_dir=self.state_dir)
        ticket_id = _text(ticket.get("ticket_id"))
        component_ref = _text(ticket.get("component_ref"))
        target_scope = _mapping(ticket.get("target_scope"))
        target_refs = list(
            dict.fromkeys(
                value
                for value in (
                    component_ref,
                    *[
                        f"{kind}:{target_scope.get(key)}"
                        for kind, key in (
                            ("project", "project_id"),
                            ("scenario", "scenario_id"),
                            ("skill", "skill_id"),
                            ("modal", "modal_id"),
                        )
                        if _text(target_scope.get(key))
                    ],
                )
                if _text(value)
            )
        )
        records: list[dict[str, Any]] = []
        for item in raw_items[:4]:
            if not isinstance(item, Mapping):
                continue
            result = feedback.capture(
                source="pre_codex_llm",
                category=_text(item.get("category")),
                summary=_text(item.get("summary")),
                blocking=bool(item.get("blocking")),
                confidence=float(item.get("confidence", 0.5)),
                impact=item.get("impact") or [],
                target_refs=[*target_refs, *(item.get("target_refs") or [])],
                details=_text(item.get("details")),
                recommendation=_text(item.get("recommendation")),
                evidence_refs=[
                    {
                        "type": "llm_usage",
                        "request_id": request_id,
                        "total_tokens": usage.get("total_tokens"),
                        "accuracy": usage.get("accuracy"),
                    }
                ],
                relation_refs=[{"type": "dev_ticket", "id": ticket_id}],
                classification={
                    "stage": "builder_language_qualification",
                    "proposal_confidence": proposal.get("confidence"),
                },
                dedup_key=_fingerprint(
                    "pre-codex-feedback",
                    ticket_id,
                    item.get("category"),
                    _text(item.get("summary")).casefold(),
                ),
                actor=_text(actor) or "builder.language_qualifier",
                idempotent_replay=True,
            )
            records.append(result["feedback"])
        return records

    def _record_builder_language_qualification(
        self,
        ticket_id: str,
        *,
        record: Mapping[str, Any],
        actor: str,
        expected_updated_at: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if expected_updated_at and expected_updated_at != _text(ticket.get("updated_at")):
                raise ValueError("Dev Ticket changed while language qualification was running")
            now = _now()
            item = _clone(record)
            item["recorded_at"] = now
            metadata = _mapping(ticket.get("metadata"))
            metadata["builder_language_qualification"] = item
            ticket["metadata"] = metadata
            usage = _mapping(item.get("usage"))
            ticket["evidence_refs"] = _merge_refs(
                ticket.get("evidence_refs") or [],
                [
                    {
                        "type": "llm_usage",
                        "id": _text(item.get("request_id")),
                        "purpose": "builder_language_qualification",
                        "status": _text(item.get("status")),
                        "root_response_id": usage.get("root_response_id"),
                        "input_tokens": usage.get("input_tokens"),
                        "cached_input_tokens": usage.get("cached_input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "reasoning_tokens": usage.get("reasoning_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "accuracy": usage.get("accuracy"),
                    }
                ],
            )
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "builder_language_qualification",
                    "actor": _text(actor) or "builder.language_qualifier",
                    "request_id": _text(item.get("request_id")),
                    "status": _text(item.get("status")),
                    "total_tokens": usage.get("total_tokens"),
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def bind_ticket_project_scope(
        self,
        ticket_id: str,
        *,
        project_id: str,
        actor: str = "builder.qualifier",
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        project_token = _text(project_id)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", project_token):
            raise ValueError("project_id is invalid")
        project_ref = f"project:{project_token}"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            current = _project_identity_from_ticket(ticket)
            if current:
                if _text(current.get("project_id")) != project_token:
                    raise ValueError("Dev Ticket is already bound to a different Project")
                return _normalized_ticket(ticket)
            target_scope = _mapping(ticket.get("target_scope"))
            target_scope.update({"project_id": project_token, "project_ref": project_ref})
            metadata = _mapping(ticket.get("metadata"))
            metadata.update({"project_id": project_token, "project_ref": project_ref})
            ticket["target_scope"] = target_scope
            ticket["metadata"] = metadata
            ticket["evidence_refs"] = _merge_refs(
                ticket.get("evidence_refs") or [],
                evidence_refs,
            )
            now = _now()
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "project_scope_bound",
                    "actor": _text(actor) or "builder.qualifier",
                    "project_id": project_token,
                    "project_ref": project_ref,
                    "recorded_at": now,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if not signal:
                    continue
                signal_target = _mapping(signal.get("target_scope"))
                signal_target.update({"project_id": project_token, "project_ref": project_ref})
                signal["target_scope"] = signal_target
                signal_metadata = _mapping(signal.get("metadata"))
                signal_metadata.update({"project_id": project_token, "project_ref": project_ref})
                signal["metadata"] = signal_metadata
                signal["updated_at"] = now
                self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _release_builder_start_failure(
        self,
        ticket_id: str,
        *,
        repair_id: str,
        actor: str,
        error_type: str,
    ) -> dict[str, Any]:
        """Return a ticket to the queue when Automation never produced a task."""

        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            now = _now()
            refs = [dict(ref) for ref in ticket.get("builder_refs") or [] if isinstance(ref, Mapping)]
            for ref in refs:
                if _text(ref.get("repair_id")) != _text(repair_id):
                    continue
                ref["status"] = "failed"
                ref["work_status"] = "failed"
                ref.setdefault("automation_status", "start_failed")
                ref["error_type"] = _text(error_type) or "Error"
                ref["updated_at"] = now
            ticket["builder_refs"] = refs[-100:]
            if _text(ticket.get("status")) not in {"resolved", "verified", "closed", *TERMINAL_TICKET_STATES}:
                ticket["status"] = "ready_for_builder"
            ticket["updated_at"] = now
            if not any(
                _text(item.get("kind")) == "builder_automation_start_failed"
                and _text(item.get("repair_id")) == _text(repair_id)
                for item in _sequence_of_mappings(ticket.get("history") or [])
            ):
                self._append_history(
                    ticket,
                    {
                        "kind": "builder_automation_start_failed",
                        "actor": _text(actor) or "builder.automation",
                        "repair_id": _text(repair_id),
                        "error_type": _text(error_type) or "Error",
                        "recorded_at": now,
                    },
                )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "in_progress"
                    signal["builder_ref"] = {
                        **_mapping(signal.get("builder_ref")),
                        "repair_id": _text(repair_id),
                        "handoff_mode": "autonomous",
                        "automation_status": "start_failed",
                    }
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _link_builder_automation(
        self,
        ticket_id: str,
        *,
        repair_id: str,
        automation: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        projection = _automation_projection(automation)
        session = _automation_session(automation)
        task = _automation_task(automation)
        automation_status = _text(
            projection.get("status")
            or session.get("status")
            or task.get("status")
            or "linked"
        )
        session_id = _text(projection.get("session_id") or session.get("session_id"))
        task_id = _text(projection.get("task_id") or session.get("current_task_id") or task.get("task_id"))
        if not session_id and not task_id:
            raise ValueError("builder automation link requires session_id or task_id")
        budget_usage = projection.get("budget_usage") if isinstance(projection.get("budget_usage"), Mapping) else {}
        declared = budget_usage.get("declared") if isinstance(budget_usage.get("declared"), Mapping) else {}
        observed = budget_usage.get("observed") if isinstance(budget_usage.get("observed"), Mapping) else {}
        receipt = (
            session.get("codex_usage_accounting")
            if isinstance(session.get("codex_usage_accounting"), Mapping)
            else {}
        )
        token_usage = dict(observed) if observed else {}
        for key in (
            "model_tokens",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "billable_tokens",
        ):
            if receipt.get(key) is not None:
                token_usage[key] = receipt.get(key)
        if receipt.get("status"):
            token_usage["receipt_status"] = receipt.get("status")
        if receipt.get("root_event_id"):
            token_usage["root_event_id"] = receipt.get("root_event_id")
        work_status = "in_progress"
        failed_automation = automation_status in {"failed", "cancelled", "errored", "error"}
        if automation_status in {"completed"}:
            work_status = "resolved" if _automation_has_validation_evidence(automation) else "in_progress"
        elif failed_automation:
            work_status = automation_status
        automation_ref = {
            "type": "builder_repair_task",
            "repair_id": _text(repair_id),
            "mode": "autonomous",
            "status": work_status,
            "automation_session_id": session_id or None,
            "automation_task_id": task_id or None,
            "automation_status": automation_status or None,
            "automation": {
                "schema": "adaos.dev_ticket.builder_automation_ref.v1",
                "session_id": session_id or None,
                "task_id": task_id or None,
                "status": automation_status or None,
                "phase": projection.get("phase"),
                "terminal": bool(projection.get("terminal")),
                "busy": bool(projection.get("busy")),
                "change_set_id": projection.get("change_set_id"),
                "change_id": projection.get("change_id"),
                "webspace_id": projection.get("webspace_id"),
                "result_branch": projection.get("result_branch"),
                "summary": projection.get("summary"),
                "error": projection.get("error"),
                "links": _automation_correlation(automation),
            },
        }
        if declared:
            automation_ref["cost_estimate"] = dict(declared)
        if token_usage:
            automation_ref["token_usage"] = token_usage
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            now = _now()
            refs = [dict(ref) for ref in ticket.get("builder_refs") or [] if isinstance(ref, Mapping)]
            if task_id:
                refs = [
                    ref
                    for ref in refs
                    if _text(ref.get("automation_task_id")) != task_id
                    or _text(ref.get("repair_id")) == _text(repair_id)
                ]
            matched = False
            for index, ref in enumerate(refs):
                if _text(ref.get("repair_id")) != _text(repair_id):
                    continue
                existing_task_id = _text(ref.get("automation_task_id"))
                if task_id and existing_task_id and existing_task_id != task_id:
                    continue
                refs[index] = {**ref, **automation_ref, "updated_at": now}
                matched = True
                break
            if not matched:
                refs.append({**automation_ref, "created_at": now, "updated_at": now})
            task_order = {
                _text(item): index
                for index, item in enumerate(session.get("task_history") or [])
                if _text(item)
            }
            if task_order:
                refs = sorted(
                    enumerate(refs),
                    key=lambda item: (
                        task_order.get(
                            _text(item[1].get("automation_task_id")),
                            len(task_order) + item[0],
                        ),
                        item[0],
                    ),
                )
                refs = [item for _, item in refs]
            ticket["builder_refs"] = refs[-100:]
            if _text(ticket.get("status")) not in {"resolved", "verified", "closed", *TERMINAL_TICKET_STATES}:
                ticket["status"] = "ready_for_builder" if failed_automation else "in_builder"
            if failed_automation:
                repair_task_ids = sorted(
                    {
                        _text(ref.get("automation_task_id"))
                        for ref in refs
                        if _text(ref.get("repair_id")) == _text(repair_id)
                        and _text(ref.get("automation_task_id"))
                    }
                )
                ticket["evidence_refs"] = _merge_refs(
                    ticket.get("evidence_refs") or [],
                    _automation_evidence_refs(
                        automation,
                        repair_id=_text(repair_id),
                        allowed_task_ids=repair_task_ids,
                    ),
                )
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "builder_automation_linked",
                    "repair_id": _text(repair_id),
                    "automation_session_id": session_id or None,
                    "automation_task_id": task_id or None,
                    "automation_status": automation_status or None,
                    "actor": _text(actor) or "builder.automation",
                    "recorded_at": now,
                },
            )
            if failed_automation:
                self._append_history(
                    ticket,
                    {
                        "kind": "builder_automation_failed",
                        "repair_id": _text(repair_id),
                        "automation_session_id": session_id or None,
                        "automation_task_id": task_id or None,
                        "automation_status": automation_status or None,
                        "actor": _text(actor) or "builder.automation",
                        "recorded_at": now,
                    },
                )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "in_progress" if failed_automation else "repair_created"
                    signal["builder_ref"] = {
                        **_mapping(signal.get("builder_ref")),
                        "repair_id": _text(repair_id),
                        "handoff_mode": "autonomous",
                        "automation_session_id": session_id or None,
                        "automation_task_id": task_id or None,
                        "automation_status": automation_status or None,
                    }
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _link_builder_prototype(
        self,
        ticket_id: str,
        *,
        repair_id: str,
        prototype: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        prototype_ref = {
            key: value
            for key, value in {
                "schema": "adaos.dev_ticket.builder_prototype_ref.v1",
                "session_id": _text(prototype.get("session_id")) or None,
                "change_id": _text(prototype.get("change_id")) or None,
                "revision": _text(prototype.get("revision")) or None,
                "status": _text(prototype.get("status")) or "linked",
                "llm_job_id": _text(prototype.get("llm_job_id")) or None,
                "model_call_expected": bool(
                    prototype.get("model_call_expected")
                    or _text(prototype.get("llm_job_id"))
                ),
            }.items()
            if value is not None
        }
        if not any(
            prototype_ref.get(key)
            for key in ("session_id", "change_id", "revision", "llm_job_id")
        ):
            raise ValueError("builder prototype link requires correlation identity")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            now = _now()
            refs = [
                dict(ref)
                for ref in ticket.get("builder_refs") or []
                if isinstance(ref, Mapping)
            ]
            matched = False
            changed = False
            for index, ref in enumerate(refs):
                if _text(ref.get("repair_id")) != _text(repair_id):
                    continue
                previous = _mapping(ref.get("prototype"))
                updated = {
                    **ref,
                    "status": "in_progress",
                    "work_status": "in_progress",
                    "prototype_status": prototype_ref["status"],
                    "prototype": prototype_ref,
                    "updated_at": now,
                }
                updated.pop("error_type", None)
                if _text(updated.get("automation_status")) == "start_failed":
                    updated.pop("automation_status", None)
                refs[index] = updated
                changed = previous != prototype_ref or _text(ref.get("status")) != "in_progress"
                matched = True
                break
            if not matched:
                refs.append(
                    {
                        "type": "builder_repair_task",
                        "repair_id": _text(repair_id),
                        "mode": "prototype",
                        "status": "in_progress",
                        "work_status": "in_progress",
                        "prototype_status": prototype_ref["status"],
                        "prototype": prototype_ref,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                changed = True
            ticket["builder_refs"] = refs[-100:]
            if _text(ticket.get("status")) not in {
                "resolved",
                "verified",
                "closed",
                *TERMINAL_TICKET_STATES,
            }:
                changed = changed or _text(ticket.get("status")) != "in_builder"
                ticket["status"] = "in_builder"
            if changed:
                self._append_history(
                    ticket,
                    {
                        "kind": "builder_prototype_linked",
                        "repair_id": _text(repair_id),
                        "prototype_status": prototype_ref["status"],
                        "prototype_revision": prototype_ref.get("revision"),
                        "llm_job_id": prototype_ref.get("llm_job_id"),
                        "actor": _text(actor) or "builder.prototype",
                        "recorded_at": now,
                    },
                )
            ticket["updated_at"] = now
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if not signal:
                    continue
                signal["status"] = "repair_created"
                signal["builder_ref"] = {
                    **_mapping(signal.get("builder_ref")),
                    "repair_id": _text(repair_id),
                    "handoff_mode": "prototype",
                    "prototype_status": prototype_ref["status"],
                    "prototype_revision": prototype_ref.get("revision"),
                    "llm_job_id": prototype_ref.get("llm_job_id"),
                }
                signal["updated_at"] = now
                self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _update_ticket(
        self,
        ticket_id: str,
        *,
        history_item: Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
        **patch: Any,
    ) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            for key, value in patch.items():
                ticket[key] = value
            ticket["updated_at"] = _now()
            if history_item:
                self._append_history(ticket, {**dict(history_item), "recorded_at": ticket["updated_at"]})
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _append_ticket_history(self, ticket_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            ticket["updated_at"] = _now()
            self._append_history(ticket, {**dict(item), "recorded_at": ticket["updated_at"]})
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _close_ticket(
        self,
        ticket_id: str,
        *,
        reason: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        reason_token = _text(reason) or "closed"
        normal_close = reason_token in {"closed", "closed_from_client", "done", "verified"}
        ticket_status = {
            "duplicate": "superseded",
            "superseded": "superseded",
            "stale": "stale",
        }.get(reason_token, "closed")
        signal_status = {
            "refused": "rejected",
            "duplicate": "superseded",
            "superseded": "superseded",
            "stale": "stale",
            "not-design-time-fixable": "not_design_time_fixable",
            "not_design_time_fixable": "not_design_time_fixable",
        }.get(reason_token)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            self._assert_expected_revision(ticket, expected_revision)
            if normal_close and _text(ticket.get("status")) != "verified":
                raise ValueError("normal Dev Ticket closure requires verified status")
            now = _now()
            ticket["status"] = ticket_status
            ticket["closure"] = {
                "kind": "closed",
                "reason": reason_token,
                "actor": _text(actor) or "system",
                "evidence_refs": _merge_refs([], evidence_refs),
                "recorded_at": now,
            }
            ticket["updated_at"] = now
            self._append_history(ticket, {"kind": "closed", "reason": reason_token, "actor": actor, "recorded_at": now})
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    if signal_status:
                        signal["status"] = signal_status
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            closed_ticket = _normalized_ticket(ticket)
        closed_ticket = self._supersede_builder_clarification_pending_actions(
            closed_ticket,
            reason="ticket_closed",
        )
        return self._supersede_publication_permission_pending_actions(
            closed_ticket,
            reason="ticket_closed",
        )

    @staticmethod
    def _latest_repair_id(ticket: Mapping[str, Any]) -> str:
        for ref in reversed(_sequence_of_mappings(ticket.get("builder_refs") or [])):
            repair_id = _text(ref.get("repair_id"))
            if repair_id:
                return repair_id
        return ""

    @staticmethod
    def _append_history(record: dict[str, Any], item: Mapping[str, Any]) -> None:
        history = [dict(entry) for entry in record.get("history") or [] if isinstance(entry, Mapping)]
        history.append(dict(item))
        record["history"] = history[-100:]

    @staticmethod
    def _assert_expected_revision(ticket: Mapping[str, Any], expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_revision must be a positive integer") from exc
        if expected < 1:
            raise ValueError("expected_revision must be a positive integer")
        current = max(1, int(ticket.get("revision") or 1))
        if current != expected:
            raise ValueError(f"Dev Ticket revision conflict: expected {expected}, current {current}")

    def _read(self) -> dict[str, Any]:
        """Return a detached state for callers that may mutate it."""

        return _clone(self._read_snapshot())

    def _read_snapshot(self) -> Mapping[str, Any]:
        """Return the process-local read-only snapshot without a full clone.

        Public read operations detach the records they return. Mutation paths
        continue to use ``_read`` so a caller can never modify the shared cache.
        """

        if not self.state_path.is_file():
            return {"schema": STATE_SCHEMA, "signals": {}, "tickets": {}, "command_receipts": {}}
        cache_key = str(self.state_path.resolve())
        stat = self.state_path.stat()
        fingerprint = (int(stat.st_mtime_ns), int(stat.st_size))
        with _LOCK:
            cached = _STATE_READ_CACHE.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("development ticket state is corrupt")
        signals = value.get("signals")
        tickets = value.get("tickets")
        receipts = value.get("command_receipts") or {}
        if not isinstance(signals, Mapping) or not isinstance(tickets, Mapping):
            raise ValueError("development ticket state is corrupt")
        if not isinstance(receipts, Mapping):
            receipts = {}
        normalized_tickets: dict[str, Any] = {}
        for ticket_id, raw_ticket in tickets.items():
            ticket = dict(raw_ticket) if isinstance(raw_ticket, Mapping) else raw_ticket
            if isinstance(ticket, dict):
                try:
                    ticket["revision"] = max(1, int(ticket.get("revision") or 1))
                except (TypeError, ValueError):
                    ticket["revision"] = 1
            normalized_tickets[str(ticket_id)] = ticket
        normalized = {
            "schema": STATE_SCHEMA,
            "signals": {
                str(signal_id): _portable_structured_paths(signal)
                for signal_id, signal in signals.items()
            },
            "tickets": {
                ticket_id: _portable_structured_paths(ticket)
                for ticket_id, ticket in normalized_tickets.items()
            },
            "command_receipts": {
                str(request_id): dict(receipt)
                for request_id, receipt in receipts.items()
                if isinstance(receipt, Mapping)
            },
        }
        with _LOCK:
            _STATE_READ_CACHE[cache_key] = (fingerprint, normalized)
        return normalized

    def _write(self, state: Mapping[str, Any]) -> None:
        previous_tickets: Mapping[str, Any] = {}
        if self.state_path.is_file():
            try:
                previous = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(previous, Mapping) and isinstance(previous.get("tickets"), Mapping):
                    previous_tickets = previous["tickets"]
            except Exception:
                previous_tickets = {}
        tickets = state.get("tickets") if isinstance(state, Mapping) else None
        if isinstance(tickets, Mapping):
            for ticket_id, raw_ticket in tickets.items():
                if not isinstance(raw_ticket, dict):
                    continue
                previous_ticket = previous_tickets.get(ticket_id)
                if not isinstance(previous_ticket, Mapping):
                    raw_ticket["revision"] = max(1, int(raw_ticket.get("revision") or 1))
                    continue
                previous_revision = max(1, int(previous_ticket.get("revision") or 1))
                current_content = {key: value for key, value in raw_ticket.items() if key != "revision"}
                previous_content = {key: value for key, value in previous_ticket.items() if key != "revision"}
                raw_ticket["revision"] = previous_revision + (1 if current_content != previous_content else 0)
        atomic_write_json(self.state_path, dict(state))
        with _LOCK:
            _STATE_READ_CACHE.pop(str(self.state_path.resolve()), None)

    @staticmethod
    def _validate_signal(signal: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_schema("development_signal.v1")).iter_errors(signal),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(f"invalid Development Signal: {errors[0].message}")

    @staticmethod
    def _validate_ticket(ticket: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_schema("dev_ticket.v1")).iter_errors(ticket),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(f"invalid Dev Ticket: {errors[0].message}")


@subscribe(COMPATIBILITY_RESPONSE_TOPIC)
@subscribe(CORE_CAPABILITY_RESPONSE_TOPIC)
async def _on_compatibility_pending_action_response(evt: Any) -> None:
    payload = _event_payload(evt)
    response = _mapping(payload.get("response"))
    domain_ref = _mapping(payload.get("domain_ref"))
    ticket_id = _text(domain_ref.get("ticket_id") or _mapping(response.get("payload")).get("ticket_id"))
    response_action_id = _text(payload.get("response_action_id") or response.get("response_action_id"))
    if not ticket_id or not response_action_id:
        return
    responder = _mapping(response.get("responder"))
    try:
        DevelopmentTicketService().handle_compatibility_response(
            ticket_id=ticket_id,
            response_action_id=response_action_id,
            pending_action_id=_text(payload.get("pending_action_id")),
            responder=responder,
            response_payload=_mapping(response.get("payload")),
        )
    except Exception:
        _log.warning("failed to handle compatibility ticket pending action response", exc_info=True)


@subscribe(BUILDER_CLARIFICATION_RESPONSE_TOPIC)
async def _on_builder_clarification_pending_action_response(evt: Any) -> None:
    payload = _event_payload(evt)
    response = _mapping(payload.get("response"))
    domain_ref = _mapping(payload.get("domain_ref"))
    ticket_id = _text(
        domain_ref.get("ticket_id")
        or _mapping(response.get("payload")).get("ticket_id")
    )
    response_action_id = _text(
        payload.get("response_action_id") or response.get("response_action_id")
    )
    if not ticket_id or not response_action_id:
        return
    try:
        DevelopmentTicketService().handle_builder_clarification_response(
            ticket_id=ticket_id,
            response_action_id=response_action_id,
            pending_action_id=_text(payload.get("pending_action_id")),
            qualification_request_id=_text(
                domain_ref.get("qualification_request_id")
            ),
            responder=_mapping(response.get("responder")),
            response_payload=_mapping(response.get("payload")),
        )
    except Exception:
        _log.warning(
            "failed to handle Builder clarification pending action response",
            exc_info=True,
        )


@subscribe(PUBLICATION_PERMISSION_RESPONSE_TOPIC)
async def _on_publication_permission_pending_action_response(evt: Any) -> None:
    payload = _event_payload(evt)
    response = _mapping(payload.get("response"))
    domain_ref = _mapping(payload.get("domain_ref"))
    ticket_id = _text(
        domain_ref.get("ticket_id")
        or _mapping(response.get("payload")).get("ticket_id")
    )
    response_action_id = _text(
        payload.get("response_action_id") or response.get("response_action_id")
    )
    if not ticket_id or not response_action_id:
        return
    try:
        await asyncio.to_thread(
            DevelopmentTicketService().handle_publication_permission_response,
            ticket_id=ticket_id,
            response_action_id=response_action_id,
            object_type=_text(domain_ref.get("object_type")),
            object_id=_text(domain_ref.get("object_id")),
            task_id=_text(domain_ref.get("task_id")),
            package_digest=_text(domain_ref.get("package_digest")),
            pending_action_id=_text(payload.get("pending_action_id")),
            responder=_mapping(response.get("responder")),
            response_payload=_mapping(response.get("payload")),
        )
    except Exception:
        _log.warning(
            "failed to handle publication permission pending action response",
            exc_info=True,
        )


@subscribe("skills.activation.failed")
@subscribe("scenarios.activation.failed")
async def _on_runtime_activation_failed(evt: Any) -> None:
    payload = _event_payload(evt)
    if _mapping(payload.get("development_ticket_projection")).get("processed") is True:
        return
    try:
        DevelopmentTicketService().report_runtime_activation_observation(
            {**payload, "status": "failed"}
        )
    except Exception:
        _log.warning("failed to record runtime activation Dev Ticket", exc_info=True)


@subscribe("skills.activated")
@subscribe("scenarios.synced")
@subscribe("skills.activation.passed")
@subscribe("scenarios.activation.passed")
async def _on_runtime_activation_passed(evt: Any) -> None:
    payload = _event_payload(evt)
    if _mapping(payload.get("development_ticket_projection")).get("processed") is True:
        return
    try:
        DevelopmentTicketService().report_runtime_activation_observation(
            {**payload, "status": "passed"}
        )
    except Exception:
        _log.warning("failed to reconcile runtime activation Dev Ticket", exc_info=True)


__all__ = [
    "ACTIVE_SIGNAL_STATES",
    "ACTIVE_TICKET_STATES",
    "COMPATIBILITY_PENDING_ACTION_KIND",
    "COMPATIBILITY_RESPONSE_TOPIC",
    "CORE_CAPABILITY_PENDING_ACTION_KIND",
    "CORE_CAPABILITY_RESPONSE_TOPIC",
    "BUILDER_CLARIFICATION_PENDING_ACTION_KIND",
    "BUILDER_CLARIFICATION_RESPONSE_TOPIC",
    "DEVELOPMENT_SIGNAL_SCHEMA",
    "DEV_TICKET_LIFECYCLE_EVENT_SCHEMA",
    "DEV_TICKET_SCHEMA",
    "DevelopmentTicketService",
    "RECEIVER_COMPATIBILITY_REASONS",
    "STATE_SCHEMA",
    "TERMINAL_TICKET_STATES",
    "project_development_ticket_summary",
]
