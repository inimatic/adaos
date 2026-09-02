from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.sdk.core.decorators import subscribe
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
CORE_IMPACT_CLASSES = {
    "blocker",
    "speed",
    "generalization",
    "contract_gap",
    "observability_gap",
    "lifecycle_gap",
    "policy_boundary",
    "compatibility_debt",
    "security_governance",
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
    "max_tokens": 200000,
    "token_budget_metric": "fresh_plus_output",
    "max_wall_seconds": 1800,
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
_log = logging.getLogger("adaos.development_tickets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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


def _normalized_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(ticket)
    try:
        out["revision"] = max(1, int(out.get("revision") or 1))
    except (TypeError, ValueError):
        out["revision"] = 1
    out["status_group"] = ticket_status_group(_text(out.get("status")))
    out["owner_area"] = _text(out.get("owner_area")) or _owner_area_from_scope(
        _mapping(out.get("target_scope")),
        _mapping(out.get("metadata")),
    )
    out["component_ref"] = _text(out.get("component_ref")) or _component_ref_from_scopes(
        _mapping(out.get("target_scope")),
        _mapping(out.get("origin_scope")),
        _mapping(out.get("metadata")),
    )
    out["relation_refs"] = _normalize_relation_refs(
        _sequence_of_mappings(out.get("relation_refs") or []),
        _sequence_of_mappings(out.get("related_refs") or []),
    )
    return _clone(out)


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
            "create_runtime_overlay",
            "defer",
        ],
        "default_option": "materialize_dev_source",
    }
    if extra:
        payload.update(dict(extra))
    return payload


def development_source_options(target_scope: Mapping[str, Any]) -> dict[str, Any]:
    target = _mapping(target_scope)
    source = _text(target.get("source")).lower()
    resolved = _development_source_target(target)
    target_type = str(resolved.get("type") or "unknown")
    target_id = _text(resolved.get("id"))
    project_id = _text(resolved.get("project_id")) or None
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
    if target_type in {"project", "scenario", "skill"} and target_id:
        try:
            from adaos.services.builder.workspace import BuilderWorkspaceService

            actual = BuilderWorkspaceService.from_context().development_source_status(
                kind=target_type,
                artifact_id=target_id,
                project_id=project_id,
            )
            if actual:
                if source:
                    actual = {**actual, "declared_source": source}
                return actual
        except Exception:
            pass
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
    if qualified_type in {"skill", "scenario"} and qualified_id:
        return {"object_type": qualified_type, "object_id": qualified_id}
    target_type = _text(target.get("type")).lower().rstrip("s")
    target_id = _text(target.get("id") or target.get("name"))
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
            refs.append({"type": ref_type, "id": path, "path": path, "source": "builder_automation", "status": status or None})
    result = task.get("result") if isinstance(task.get("result"), Mapping) else session.get("last_result")
    result = dict(result) if isinstance(result, Mapping) else {}
    tests = result.get("tests") if isinstance(result.get("tests"), Mapping) else {}
    if _text(tests.get("report")):
        refs.append({"type": "test", "id": _text(tests.get("report")), "status": _text(tests.get("status")) or "unknown"})
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


def _bounded_repair_hints(ticket: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(_mapping(ticket.get("metadata")).get("builder_repair"))
    if not raw:
        return {}
    profile = _text(raw.get("profile")).lower()
    if profile not in _REPAIR_HINT_PROFILES:
        profile = ""
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
        "change_summary": _text(raw.get("change_summary"))[:1000] or None,
        "target_files": target_files,
        "target_refs": target_refs,
        "acceptance_checks": acceptance_checks,
        "max_changed_files": max_changed_files,
        "requires_root_mcp": raw.get("requires_root_mcp") is True,
        "target_object_type": target_object_type or None,
        "target_object_id": target_object_id or None,
    }
    structured_edits = _normalize_structured_edits(
        raw.get("structured_edits"),
        target_files=target_files,
    )
    if structured_edits:
        hints["structured_edits"] = structured_edits
    return {key: value for key, value in hints.items() if value not in (None, "", [])}


def _autonomous_repair_brief(ticket: Mapping[str, Any], repair: Mapping[str, Any], *, target: Mapping[str, str]) -> str:
    policy = _mapping(ticket.get("policy"))
    policy.setdefault("publication_required", True)
    payload = {
        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
        "execution_mode": "surgical_dev_ticket_repair",
        "ticket_id": _text(ticket.get("ticket_id")),
        "repair_id": _text(repair.get("repair_id")),
        "kind": _text(ticket.get("kind")),
        "summary": _text(ticket.get("summary")),
        "target": target,
        "target_scope": _mapping(ticket.get("target_scope")),
        "owner_area": _text(ticket.get("owner_area")),
        "component_ref": _text(ticket.get("component_ref")),
        "policy": policy,
        "metadata": _mapping(ticket.get("metadata")),
        "relation_refs": _sequence_of_mappings(ticket.get("relation_refs") or []),
        "evidence_refs": _sequence_of_mappings(ticket.get("evidence_refs") or []),
        "artifact_refs": _sequence_of_mappings(ticket.get("artifact_refs") or []),
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
    ticket_items = [
        {
            "ticket_id": _text(ticket.get("ticket_id")),
            "kind": _text(ticket.get("kind")),
            "summary": _text(ticket.get("summary")),
            "component_ref": _text(ticket.get("component_ref")) or None,
            "acceptance_checks": _bounded_repair_hints(ticket).get("acceptance_checks") or [],
            "evidence_refs": _sequence_of_mappings(ticket.get("evidence_refs") or []),
            "artifact_refs": _sequence_of_mappings(ticket.get("artifact_refs") or []),
        }
        for ticket in tickets
    ]
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
        },
        "repair_hints": _mapping(package.get("repair_hints")),
        "guardrails": [
            "Use only public AdaOS SDK/API surfaces available to the project.",
            "Do not modify AdaOS core/runtime from project Builder automation.",
            "Implement all package issues in one bounded project change and one release.",
            "Do not close an issue that lacks its own validation evidence.",
            "Stop and create a linked core capability request when project-owned repair is impossible.",
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

    def ensure_ticket_for_signal(
        self,
        signal: Mapping[str, Any],
        *,
        kind: str,
        summary: str | None = None,
        status: str = "captured",
        source: str | None = None,
        dedup_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
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
        key = _text(dedup_key) or _fingerprint("dticket", ticket_kind, signal.get("dedup_key"), _target_identity(target))
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
                "summary": text,
                "severity": _text(signal.get("severity")) or "medium",
                "blocking": bool(signal.get("blocking")),
                "owner_area": area,
                "component_ref": component,
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
        receiver = _text(admission.get("receiver"))
        summary = (
            f"Skill {skill} lacks receiver/data-route declaration"
            + (f" for {receiver}" if receiver else "")
        )
        context = {
            "code": f"compat.{reason}",
            "reason": reason,
            "receiver": receiver or None,
            "receiver_patterns": list(admission.get("receiver_patterns") or [])[:12],
            "topic": _text(topic) or None,
            "event_type": _text(event_type) or _text(topic) or None,
        }
        target = _mapping(target_scope) or {"type": "skill", "id": skill, "source": "installed"}
        return {
            **self.report_compatibility_finding(
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
                autonomous_repair_eligible=True,
                source="runtime_guard",
                dedup_key=_fingerprint("compat.receiver", skill, reason, receiver or _text(topic)),
                publish_pending_action=publish_pending_action,
                ctx=ctx,
                webspace_id=webspace_id,
            ),
            "reported": True,
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
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
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
            if strategy != "materialize_dev_source":
                raise ValueError(f"development source strategy is not implemented for autonomous repair: {strategy}")
            workspace_service = getattr(automation_service, "workspace_service", None)
            if workspace_service is None:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                workspace_service = BuilderWorkspaceService.from_context()
            materialization = workspace_service.materialize_dev_source(
                kind=target["object_type"],
                artifact_id=target["object_id"],
                project_id=_project_id_for_materialization(ticket, development_source),
            )
            if not materialization.get("ok"):
                raise ValueError("development source materialization failed")
            development_source = _mapping(materialization.get("development_source")) or development_source
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
        bounded_budget = dict(execution_budget) if isinstance(execution_budget, Mapping) else dict(DEFAULT_AUTONOMOUS_REPAIR_BUDGET)
        bounded_budget.setdefault("token_budget_metric", "fresh_plus_output")
        automation_links = {
            "development_ticket_id": ticket["ticket_id"],
            "builder_repair_id": repair_id,
            "development_ticket_component_ref": _text(handoff["ticket"].get("component_ref")) or None,
            "development_ticket_owner_area": _text(handoff["ticket"].get("owner_area")) or None,
            **{
                f"development_ticket_{key}": value
                for key, value in _project_identity_from_ticket(handoff["ticket"]).items()
            },
            "development_source_materialization": materialization,
        }
        resume_failed = getattr(automation_service, "resume_failed_dev_ticket_repair", None)
        start_followup = getattr(automation_service, "start_followup_dev_ticket_repair", None)
        can_resume = False
        can_followup = False
        if callable(resume_failed) or callable(start_followup):
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
            resume_failed
            if can_resume
            else start_followup
            if can_followup
            else automation_service.start_from_execute
        )
        try:
            started = start_method(
                object_type=target["object_type"],
                object_id=target["object_id"],
                implementation_brief=brief,
                webspace_id=_text(webspace_id) or "desktop",
                conversation_id=_text(conversation_id) or f"dev-ticket:{ticket['ticket_id']}",
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
        }

    def plan_builder_package(
        self,
        ticket_ids: Sequence[str],
        *,
        actor: str,
        repair_service: BuilderRepairService | None = None,
        execution_budget: Mapping[str, Any] | None = None,
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
        qualifications = [_bounded_repair_hints(ticket) for ticket in tickets]
        missing = [
            _text(ticket.get("ticket_id"))
            for ticket, qualification in zip(tickets, qualifications, strict=True)
            if not qualification.get("profile") or not qualification.get("target_files")
        ]
        if missing:
            return {
                "ok": True,
                "ready": False,
                "status": "qualification_required",
                "ticket_ids": ids,
                "unqualified_ticket_ids": missing,
                "target": target,
                "repair": None,
            }

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
        package_id = f"bpackage.{new_id()}"
        budget = dict(execution_budget) if isinstance(execution_budget, Mapping) else {
            "schema": "adaos.builder.execution_budget.v1",
            "source": "development_ticket.package_default",
            "max_tokens": min(200000, 30000 + 15000 * len(tickets)),
            "max_wall_seconds": min(3600, 600 + 240 * len(tickets)),
        }
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
        }
        source_refs = [
            {"type": "dev_ticket", "id": _text(ticket.get("ticket_id"))}
            for ticket in tickets
        ]
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        report = service.report(
            project_id=target["object_id"],
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
        source_strategy: str | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
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
            if strategy != "materialize_dev_source":
                raise ValueError("autonomous Builder package requires materialize_dev_source when source is missing")
            workspace_service = getattr(automation_service, "workspace_service", None)
            if workspace_service is None:
                from adaos.services.builder.workspace import BuilderWorkspaceService

                workspace_service = BuilderWorkspaceService.from_context()
            materialization = workspace_service.materialize_dev_source(
                kind=target["object_type"],
                artifact_id=target["object_id"],
                project_id=_project_id_for_materialization(ticket_list[0], development_source),
            )
            if not materialization.get("ok"):
                raise ValueError("development source materialization failed")
            development_source = _mapping(materialization.get("development_source")) or development_source

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
            "development_source_materialization": materialization,
        }
        current = automation_service.status(
            object_type=target["object_type"],
            object_id=target["object_id"],
        )
        current_session = _automation_session(current)
        current_links = _mapping(current_session.get("links"))
        resume = (
            _text(current_session.get("status")) == "failed"
            and _text(current_links.get("builder_package_id")) == _text(package_id)
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
            and callable(getattr(automation_service, "start_followup_dev_ticket_repair", None))
        )
        start_method = (
            automation_service.resume_failed_dev_ticket_repair
            if resume
            else automation_service.start_followup_dev_ticket_repair
            if followup
            else automation_service.start_from_execute
        )
        started = start_method(
            object_type=target["object_type"],
            object_id=target["object_id"],
            implementation_brief=brief,
            webspace_id=_text(webspace_id) or "desktop",
            conversation_id=_text(conversation_id) or f"dev-ticket-package:{package_id}",
            execution_budget=budget,
            agent_profile=dict(agent_profile) if isinstance(agent_profile, Mapping) else None,
            mcp=dict(mcp) if isinstance(mcp, Mapping) else None,
            links=links,
        )
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
        observation_id = _text(observation.get("observation_id"))
        evidence = {
            "type": "runtime_guard",
            "code": "artifact.workspace_lock_verification_failed",
            "observation_id": observation_id or None,
            "expected_lock_digest": _text(observation.get("expected_lock_digest")) or None,
            "observed_lock_digest": _text(observation.get("observed_lock_digest")) or None,
            "affected_component_ref": affected_component_ref or None,
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
                "design_time_fixable": True,
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
            return {"ok": True, "ticket": _normalized_ticket(stored), "closure": _clone(closure)}

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
        signal = self._read()["signals"].get(_text(signal_id))
        return _clone(signal) if signal else None

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._read()["tickets"].get(_text(ticket_id))
        return _normalized_ticket(ticket) if ticket else None

    def list_tickets(
        self,
        *,
        status: str | None = None,
        status_group: str | None = None,
        target_id: str | None = None,
        target_tokens: Sequence[str] = (),
        kind: str | None = None,
        scenario_id: str | None = None,
        skill_id: str | None = None,
        modal_id: str | None = None,
        component: str | None = None,
        severity: str | None = None,
        blocking: bool | None = None,
        source: str | None = None,
        owner: str | None = None,
        owner_area: str | None = None,
        component_ref: str | None = None,
        updated_since: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        tickets = [_normalized_ticket(item) for item in self._read()["tickets"].values()]
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
        severity_token = _text(severity)
        if severity_token:
            allowed = {_text(part) for part in severity_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("severity")) in allowed]
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
        return [_clone(item) for item in sorted_tickets]

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
        state = self._read()
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
        state = self._read()
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
            return _normalized_ticket(ticket)

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
        if not self.state_path.is_file():
            return {"schema": STATE_SCHEMA, "signals": {}, "tickets": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("development ticket state is corrupt")
        signals = value.get("signals")
        tickets = value.get("tickets")
        if not isinstance(signals, Mapping) or not isinstance(tickets, Mapping):
            raise ValueError("development ticket state is corrupt")
        normalized_tickets: dict[str, Any] = {}
        for ticket_id, raw_ticket in tickets.items():
            ticket = dict(raw_ticket) if isinstance(raw_ticket, Mapping) else raw_ticket
            if isinstance(ticket, dict):
                try:
                    ticket["revision"] = max(1, int(ticket.get("revision") or 1))
                except (TypeError, ValueError):
                    ticket["revision"] = 1
            normalized_tickets[str(ticket_id)] = ticket
        return {"schema": STATE_SCHEMA, "signals": dict(signals), "tickets": normalized_tickets}

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


@subscribe("skills.activation.failed")
@subscribe("scenarios.activation.failed")
async def _on_runtime_activation_failed(evt: Any) -> None:
    try:
        DevelopmentTicketService().report_runtime_activation_observation(
            {**_event_payload(evt), "status": "failed"}
        )
    except Exception:
        _log.warning("failed to record runtime activation Dev Ticket", exc_info=True)


@subscribe("skills.activated")
@subscribe("scenarios.synced")
@subscribe("skills.activation.passed")
@subscribe("scenarios.activation.passed")
async def _on_runtime_activation_passed(evt: Any) -> None:
    try:
        DevelopmentTicketService().report_runtime_activation_observation(
            {**_event_payload(evt), "status": "passed"}
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
    "DEVELOPMENT_SIGNAL_SCHEMA",
    "DEV_TICKET_LIFECYCLE_EVENT_SCHEMA",
    "DEV_TICKET_SCHEMA",
    "DevelopmentTicketService",
    "RECEIVER_COMPATIBILITY_REASONS",
    "STATE_SCHEMA",
    "TERMINAL_TICKET_STATES",
]
