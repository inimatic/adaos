from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.context_control import ContextAccessDenied, ContextConflict, ContextControlService
from adaos.services.development_tickets import DevelopmentTicketService
from adaos.services.development_feedback import DevelopmentFeedbackService
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


RESOURCE_DEFINITION_SCHEMA = "adaos.resource.definition.v1"
RESOURCE_QUERY_SCHEMA = "adaos.resource.query.v1"
RESOURCE_OPERATION_SCHEMA = "adaos.resource.operation.v1"
RESOURCE_EVENT_SCHEMA = "adaos.resource.event.v1"
RESOURCE_TRACE_SCHEMA = "adaos.resource.trace.v1"


class ResourceAccessDenied(PermissionError):
    pass


class ResourceConflict(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name: str, payload: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(_schema(schema_name)).iter_errors(payload),
        key=lambda item: list(item.path),
    )
    if errors:
        raise ValueError(f"invalid {schema_name}: {errors[0].message}")


def _digest(value: Mapping[str, Any]) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _actor_id(actor: Mapping[str, Any] | None) -> str:
    value = _mapping(actor)
    return _text(value.get("id") or value.get("actor") or value.get("subject") or value.get("role")) or "system"


def _actor_role(actor: Mapping[str, Any] | None) -> str:
    value = _mapping(actor)
    return _text(value.get("role") or value.get("preset") or value.get("kind")).lower() or "owner"


def _ticket_signal_kind(ticket_kind: str) -> str:
    mapping = {
        "feedback": "feedback_note",
        "development_request": "development_request",
        "runtime_compatibility_debt": "compatibility_finding",
        "runtime_failure": "runtime_failure",
        "review_debt": "review_comment",
        "nlu_repair": "nlu_failure",
        "user_adaptation": "user_adaptation_request",
        "sdk_understanding": "sdk_unclear_definition",
        "core_capability_request": "core_capability_request",
    }
    return mapping.get(_text(ticket_kind), "development_request")


def _operation_map(definition: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(item.get("id")): dict(item)
        for item in definition.get("operations") or []
        if isinstance(item, Mapping) and _text(item.get("id"))
    }


def _query_filter_set(definition: Mapping[str, Any]) -> set[str]:
    query = _mapping(definition.get("query"))
    return {_text(item) for item in query.get("filters") or [] if _text(item)}


def _demo_metric_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "cpu",
            "title": "CPU Load",
            "title_i18n": {"ru": "Нагрузка CPU", "en": "CPU Load"},
            "status": "healthy",
            "value": 42,
            "unit": "%",
            "updated_at": "2026-05-07T10:00:00Z",
            "group": "compute",
            "revision": 1,
        },
        {
            "id": "memory",
            "title": "Memory Pressure",
            "title_i18n": {"ru": "Давление памяти", "en": "Memory Pressure"},
            "status": "warning",
            "value": 76,
            "unit": "%",
            "updated_at": "2026-05-07T10:00:00Z",
            "group": "compute",
            "revision": 1,
        },
        {
            "id": "queue",
            "title": "Queue Depth",
            "title_i18n": {"ru": "Глубина очереди", "en": "Queue Depth"},
            "status": "healthy",
            "value": 7,
            "unit": "jobs",
            "updated_at": "2026-05-07T10:00:00Z",
            "group": "runtime",
            "revision": 1,
        },
    ]


def _matches_text(record: Mapping[str, Any], search: str) -> bool:
    token = _text(search).lower()
    if not token:
        return True
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).lower()
    return token in body


def _filter_records(records: Sequence[Mapping[str, Any]], filters: Mapping[str, Any], search: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in records:
        item = dict(raw)
        if not _matches_text(item, search):
            continue
        matched = True
        for key, expected in filters.items():
            if key in {"status_group", "search"}:
                continue
            values = expected if isinstance(expected, (list, tuple, set)) else [expected]
            wanted = {_text(value) for value in values if _text(value)}
            if wanted and _text(item.get(key)) not in wanted:
                matched = False
                break
        if matched:
            out.append(item)
    return out


def _dev_ticket_definition() -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": "adaos.dev.ticket",
        "version": "1.0.0",
        "title": "Dev Ticket",
        "description": "Governed development backlog item over Development Signals.",
        "scope": {
            "owner": "workspace",
            "target_refs": ["workspace", "project", "scenario", "skill", "modal", "component"],
        },
        "authority": {
            "provider": "api",
            "binding": "development_tickets",
            "writes": "governed",
            "source_of_truth": "workspace_inbox",
        },
        "record_schema_ref": "abi:dev_ticket.v1",
        "query": {
            "default": "open",
            "filters": [
                "status",
                "status_group",
                "target_id",
                "target_ref",
                "project_id",
                "scenario_id",
                "skill_id",
                "modal_id",
                "component",
                "owner_area",
                "component_ref",
                "kind",
                "severity",
                "blocking",
                "source",
                "owner",
                "updated_since",
                "search",
            ],
            "sort": ["updated_at", "severity", "relevance"],
            "cursor": True,
            "include": ["evidence", "artifacts", "history", "comments", "traces"],
        },
        "operations": [
            {"id": "create", "kind": "create", "risk": "low", "required_capabilities": ["dev_ticket.create"]},
            {"id": "update", "kind": "patch", "risk": "low", "required_capabilities": ["dev_ticket.update"]},
            {"id": "claim", "kind": "claim", "risk": "low", "required_capabilities": ["dev_ticket.claim"]},
            {"id": "start", "kind": "transition", "risk": "low", "required_capabilities": ["dev_ticket.update"]},
            {"id": "comment", "kind": "comment", "risk": "low", "required_capabilities": ["dev_ticket.comment"]},
            {"id": "postpone", "kind": "transition", "risk": "low", "required_capabilities": ["dev_ticket.update"]},
            {"id": "open_builder", "kind": "handoff", "risk": "medium", "required_capabilities": ["builder.open"]},
            {"id": "autonomous_repair", "kind": "handoff", "risk": "high", "required_capabilities": ["builder.autonomous"]},
            {"id": "resolve", "kind": "transition", "risk": "medium", "requires": ["evidence_ref"], "required_capabilities": ["dev_ticket.resolve"]},
            {"id": "verify", "kind": "verify", "risk": "medium", "requires": ["validation_evidence_ref"], "required_capabilities": ["dev_ticket.verify"]},
            {"id": "close", "kind": "transition", "risk": "low", "required_capabilities": ["dev_ticket.close"]},
            {"id": "reopen", "kind": "reopen", "risk": "medium", "requires": ["reason"], "required_capabilities": ["dev_ticket.reopen"]},
            {"id": "duplicate", "kind": "transition", "risk": "low", "required_capabilities": ["dev_ticket.close"]},
            {"id": "related", "kind": "transition", "risk": "low", "required_capabilities": ["dev_ticket.update"]},
            {"id": "core_request", "kind": "core_capability_request", "risk": "medium", "requires": ["desired_contract"], "required_capabilities": ["dev_ticket.core_request"]},
            {"id": "core_transition", "kind": "transition", "risk": "medium", "requires": ["transition"], "required_capabilities": ["dev_ticket.core_transition"]},
            {"id": "sdk_understanding", "kind": "sdk_understanding", "risk": "low", "requires": ["method_ref"], "required_capabilities": ["dev_ticket.sdk_understanding"]},
            {"id": "preview_evidence", "kind": "preview_evidence", "risk": "read", "required_capabilities": ["dev_ticket.read"]},
            {"id": "open_artifact", "kind": "open_artifact", "risk": "read", "required_capabilities": ["dev_ticket.artifact.read"]},
        ],
        "views": [
            {"id": "list", "kind": "list", "title": "Tickets"},
            {"id": "detail", "kind": "detail", "title": "Ticket"},
            {"id": "form", "kind": "form", "title": "Edit"},
            {"id": "evidence", "kind": "evidence", "title": "Evidence"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {
            "emits": ["resource.record.created", "resource.operation.completed"],
            "semantic_types": [
                "dev_ticket.created",
                "dev_ticket.resolved",
                "dev_ticket.verified",
                "dev_ticket.reopened",
                "core_ticket.created",
                "core_ticket.released",
                "core_ticket.verified",
                "core_capability.available",
                "sdk_understanding.created",
            ],
        },
        "i18n": {
            "default_locale": "en",
            "locales": ["en", "ru"],
            "title_i18n": {"en": "Dev Ticket", "ru": "Dev Ticket"},
            "status_i18n": {
                "captured": {"en": "Captured", "ru": "Зафиксирован"},
                "accepted": {"en": "Accepted", "ru": "Принят"},
                "claimed": {"en": "Claimed", "ru": "Взят в работу"},
                "in_progress": {"en": "In progress", "ru": "В работе"},
                "resolved": {"en": "Resolved", "ru": "Исправлен"},
                "verified": {"en": "Verified", "ru": "Проверен"},
                "closed": {"en": "Closed", "ru": "Закрыт"},
            },
        },
        "access": {
            "read": {"required_capabilities": ["dev_ticket.read"]},
            "fields": {
                "summary": {"visibility": "workspace", "sensitivity": "user_text"},
                "evidence_refs": {"visibility": "owner_or_builder", "sensitivity": "mixed"},
                "artifact_refs": {"visibility": "owner_or_builder", "sensitivity": "screenshot"},
            },
            "operations": {
                "resolve": {"required_capabilities": ["dev_ticket.resolve"]},
                "verify": {"required_capabilities": ["dev_ticket.verify"]},
                "close": {"required_capabilities": ["dev_ticket.close"]},
            },
        },
        "privacy": {
            "sensitivity": "mixed",
            "retention": "workspace_lifecycle",
            "external_export": "draft_only_with_human_approval",
        },
        "readiness": {"states": ["ready", "stale", "read_only", "permission_denied", "unsupported_query"]},
        "workflow_links": {"development_signal": True, "builder_repair": True, "nlu_teacher": True},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _demo_metric_definition() -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": "demo.metric",
        "version": "1.0.0",
        "title": "Demo Metric",
        "description": "Synthetic read-only metrics used by the resource workbench demo.",
        "scope": {"owner": "demo_metrics_skill", "target_refs": ["skill:demo_metrics_skill"]},
        "authority": {"provider": "synthetic", "binding": "demo_metrics.snapshot", "writes": "read_only"},
        "record_schema_ref": "inline:demo.metric.v1",
        "query": {
            "default": "normal",
            "filters": ["id", "status", "group", "search", "fixture", "role"],
            "sort": ["id", "status", "group"],
            "cursor": False,
            "include": ["trace"],
        },
        "operations": [
            {"id": "list", "kind": "list", "risk": "read", "required_capabilities": ["demo_metrics.read"]},
            {"id": "show", "kind": "show", "risk": "read", "required_capabilities": ["demo_metrics.read"]},
        ],
        "views": [
            {"id": "list", "kind": "list", "title": "Metrics"},
            {"id": "detail", "kind": "detail", "title": "Metric"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {"emits": [], "semantic_types": ["demo.metric.queried"]},
        "i18n": {
            "default_locale": "en",
            "locales": ["en", "ru"],
            "title_i18n": {"en": "Demo Metric", "ru": "Демо-метрика"},
            "fields_i18n": {
                "title": {"en": "Title", "ru": "Название"},
                "status": {"en": "Status", "ru": "Статус"},
                "value": {"en": "Value", "ru": "Значение"},
            },
        },
        "access": {
            "read": {"required_capabilities": ["demo_metrics.read"]},
            "role_fixtures": {"owner": "allowed", "admin": "allowed", "member": "allowed", "guest": "allowed"},
        },
        "privacy": {"sensitivity": "demo", "retention": "ephemeral", "external_export": "allowed"},
        "readiness": {"fixtures": ["empty", "normal", "validation_failure", "unavailable_provider", "long_text"]},
        "metadata": {"demo_surface": "Declarative Resource Workbench"},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _demo_metric_note_definition() -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": "demo.metric_note",
        "version": "1.0.0",
        "title": "Demo Metric Note",
        "description": "Synthetic mutable note resource for CRUD, validation, and revision-conflict demos.",
        "scope": {"owner": "demo_metrics_skill", "target_refs": ["skill:demo_metrics_skill", "demo.metric"]},
        "authority": {"provider": "synthetic", "binding": "resources/demo_metric_notes.json", "writes": "governed"},
        "record_schema_ref": "inline:demo.metric_note.v1",
        "query": {
            "default": "normal",
            "filters": ["id", "metric_id", "status", "search", "fixture", "role"],
            "sort": ["updated_at", "metric_id"],
            "cursor": True,
            "include": ["history", "trace"],
        },
        "operations": [
            {"id": "create", "kind": "create", "risk": "low", "required_capabilities": ["demo_metrics.note.write"]},
            {"id": "update", "kind": "update", "risk": "low", "required_capabilities": ["demo_metrics.note.write"]},
            {"id": "delete", "kind": "delete", "risk": "medium", "required_capabilities": ["demo_metrics.note.delete"]},
        ],
        "views": [
            {"id": "list", "kind": "list", "title": "Notes"},
            {"id": "detail", "kind": "detail", "title": "Note"},
            {"id": "form", "kind": "form", "title": "Note Form"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {
            "emits": ["resource.record.created", "resource.record.updated", "resource.record.deleted"],
            "semantic_types": ["demo.metric_note.created", "demo.metric_note.updated", "demo.metric_note.deleted"],
        },
        "i18n": {
            "default_locale": "en",
            "locales": ["en", "ru"],
            "title_i18n": {"en": "Metric Note", "ru": "Заметка к метрике"},
            "validation_i18n": {
                "title_required": {"en": "Title is required.", "ru": "Нужно указать название."},
                "revision_conflict": {"en": "The note changed. Reload it first.", "ru": "Заметка изменилась. Сначала обновите ее."},
                "permission_denied": {"en": "This role cannot change notes.", "ru": "Эта роль не может менять заметки."},
            },
        },
        "access": {
            "read": {"required_capabilities": ["demo_metrics.read"]},
            "operations": {
                "create": {"required_capabilities": ["demo_metrics.note.write"]},
                "update": {"required_capabilities": ["demo_metrics.note.write"]},
                "delete": {"required_capabilities": ["demo_metrics.note.delete"]},
            },
            "role_fixtures": {
                "owner": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                "admin": {"create": "allowed", "update": "allowed", "delete": "allowed"},
                "member": {"create": "allowed", "update": "allowed", "delete": "disabled"},
                "guest": {"create": "denied", "update": "denied", "delete": "hidden"},
            },
        },
        "privacy": {"sensitivity": "demo_user_text", "retention": "ephemeral", "external_export": "allowed"},
        "readiness": {"fixtures": ["empty", "normal", "validation_failure", "unavailable_provider", "long_text", "ru", "en"]},
        "metadata": {"demo_surface": "Declarative Resource Workbench"},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _demo_metric_event_definition() -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": "demo.metric_event",
        "version": "1.0.0",
        "title": "Demo Metric Event",
        "description": "Read-only synthetic event stream projection for the workbench trace demo.",
        "scope": {"owner": "demo_metrics_skill", "target_refs": ["stream:demo_metrics.events"]},
        "authority": {"provider": "synthetic", "binding": "demo_metrics.events", "writes": "append_only"},
        "record_schema_ref": "abi:resource.event.v1",
        "query": {
            "default": "latest",
            "filters": ["semantic_type", "resource_type", "record_ref", "search", "role"],
            "sort": ["occurred_at"],
            "cursor": True,
            "include": ["payload"],
        },
        "operations": [
            {"id": "list", "kind": "list", "risk": "read", "required_capabilities": ["demo_metrics.read"]},
        ],
        "views": [
            {"id": "event_log", "kind": "event_log", "title": "Events"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {"emits": [], "semantic_types": ["demo.metric_event.queried"]},
        "i18n": {"default_locale": "en", "locales": ["en", "ru"], "title_i18n": {"en": "Metric Event", "ru": "Событие метрики"}},
        "access": {"read": {"required_capabilities": ["demo_metrics.read"]}},
        "privacy": {"sensitivity": "demo", "retention": "ephemeral", "external_export": "allowed"},
        "readiness": {"states": ["ready", "stale", "provider_unavailable"]},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _development_feedback_definition() -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": "adaos.development.feedback",
        "version": "1.0.0",
        "title": "Development Feedback",
        "description": "Model and developer observations awaiting human or deterministic triage and optional Dev Ticket promotion.",
        "scope": {"owner": "workspace", "target_refs": ["project", "scenario", "skill", "sdk", "api", "core", "run"]},
        "authority": {
            "provider": "api",
            "binding": "development_feedback",
            "writes": "governed",
            "source_of_truth": "workspace_feedback_registry",
        },
        "record_schema_ref": "abi:development_feedback.v1",
        "query": {
            "default": "observed",
            "filters": [
                "status",
                "category",
                "source",
                "blocking",
                "target_ref",
                "rejection_class",
                "contract_ref",
                "operation_id",
                "owner_route",
                "qualification_status",
                "updated_since",
                "search",
                "role",
            ],
            "sort": ["updated_at", "category", "impact"],
            "cursor": True,
            "include": ["evidence", "relations", "comments", "history", "promotion"],
        },
        "operations": [
            {"id": "show", "kind": "show", "risk": "read", "required_capabilities": ["development_feedback.read"]},
            {"id": "qualification_preview", "kind": "show", "risk": "read", "required_capabilities": ["development_feedback.read"]},
            {"id": "capture", "kind": "create", "risk": "low", "required_capabilities": ["development_feedback.create"]},
            {"id": "triage", "kind": "transition", "risk": "low", "required_capabilities": ["development_feedback.triage"]},
            {"id": "qualify", "kind": "qualification", "risk": "medium", "required_capabilities": ["development_feedback.triage"]},
            {"id": "accept", "kind": "transition", "risk": "medium", "required_capabilities": ["development_feedback.triage"]},
            {"id": "reject", "kind": "transition", "risk": "low", "required_capabilities": ["development_feedback.triage"]},
            {"id": "comment", "kind": "comment", "risk": "low", "required_capabilities": ["development_feedback.comment"]},
            {"id": "promote", "kind": "handoff", "risk": "high", "requires": ["route"], "required_capabilities": ["development_feedback.promote"]},
        ],
        "views": [
            {"id": "list", "kind": "list", "title": "Feedback"},
            {"id": "detail", "kind": "detail", "title": "Feedback detail"},
            {"id": "discussion", "kind": "event_log", "title": "Discussion"},
            {"id": "evidence", "kind": "evidence", "title": "Evidence"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {
            "emits": ["development.feedback.observed", "development.feedback.triaged", "development.feedback.qualified", "development.feedback.accepted", "development.feedback.rejected", "development.feedback.promoted"],
            "semantic_types": ["development.feedback.changed"],
        },
        "i18n": {
            "default_locale": "en",
            "locales": ["en", "ru"],
            "title_i18n": {"en": "Development Feedback", "ru": "Обратная связь разработки"},
            "status_i18n": {
                "observed": {"en": "Observed", "ru": "Зафиксировано"},
                "triaged": {"en": "Triaged", "ru": "Квалифицировано"},
                "accepted": {"en": "Accepted", "ru": "Принято"},
                "rejected": {"en": "Rejected", "ru": "Отклонено"},
                "promoted": {"en": "Promoted", "ru": "Передано в Dev Tickets"},
            },
        },
        "access": {
            "read": {"required_capabilities": ["development_feedback.read"]},
            "role_fixtures": {
                "owner": "allowed",
                "admin": "allowed",
                "builder": {
                    "show": "allowed",
                    "qualification_preview": "allowed",
                    "capture": "allowed",
                    "triage": "denied",
                    "qualify": "denied",
                    "accept": "denied",
                    "reject": "denied",
                    "comment": "allowed",
                    "promote": "denied"
                },
                "member": {"show": "allowed", "capture": "allowed", "triage": "denied", "accept": "denied", "reject": "denied", "comment": "allowed", "promote": "denied"},
                "guest": "denied",
            },
        },
        "privacy": {
            "sensitivity": "internal_development",
            "retention": "workspace_lifecycle",
            "external_export": "denied_by_default",
        },
        "readiness": {"states": ["ready", "empty", "permission_denied", "provider_unavailable"]},
        "workflow_links": {"builder_session": True, "dev_ticket": True, "core_ticket": True},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _context_resource_definition(
    *,
    resource_type: str,
    title: str,
    record_schema_ref: str,
    filters: Sequence[str],
    operations: Sequence[Mapping[str, Any]],
    writes: str,
) -> dict[str, Any]:
    definition = {
        "schema": RESOURCE_DEFINITION_SCHEMA,
        "resource_type": resource_type,
        "version": "1.0.0",
        "title": title,
        "description": f"Governed {title.lower()} projection from the AdaOS Context Control Plane.",
        "scope": {"owner": "context_control", "target_refs": ["project", "component", "run", "agent"]},
        "authority": {
            "provider": "context_control",
            "binding": "context_control/registry.sqlite3",
            "writes": writes,
            "source_of_truth": "context_control",
        },
        "record_schema_ref": record_schema_ref,
        "query": {
            "default": "latest",
            "filters": [*filters, "search", "role"],
            "sort": ["updated_at", "created_at", "recorded_at"],
            "cursor": True,
            "include": ["provenance", "access_decisions", "cost", "content_ref"],
        },
        "operations": [dict(item) for item in operations],
        "views": [
            {"id": "list", "kind": "list", "title": title},
            {"id": "detail", "kind": "detail", "title": f"{title} detail"},
            {"id": "trace", "kind": "trace", "title": "Trace"},
        ],
        "events": {
            "emits": ["resource.operation.completed"],
            "semantic_types": [f"{resource_type}.changed"],
        },
        "i18n": {
            "default_locale": "en",
            "locales": ["en", "ru"],
            "title_i18n": {"en": title, "ru": title},
        },
        "access": {
            "read": {"required_capabilities": ["context.read"]},
            "role_fixtures": {
                "owner": "allowed",
                "admin": "allowed",
                "builder": "allowed",
                "member": "allowed",
                "guest": "denied",
            },
        },
        "privacy": {
            "sensitivity": "mixed_metadata",
            "retention": "context_policy",
            "external_export": "denied_by_default",
        },
        "readiness": {"states": ["ready", "empty", "permission_denied", "stale", "insufficient"]},
        "workflow_links": {"builder": True, "subscription": resource_type == "adaos.agent.context_receipt"},
    }
    _validate("resource.definition.v1", definition)
    return definition


def _context_resource_definitions() -> list[dict[str, Any]]:
    read = lambda operation_id: {  # noqa: E731 - compact declarative factory
        "id": operation_id,
        "kind": operation_id,
        "risk": "read",
        "required_capabilities": ["context.read"],
    }
    write = lambda operation_id, risk="low": {  # noqa: E731 - compact declarative factory
        "id": operation_id,
        "kind": operation_id,
        "risk": risk,
        "required_capabilities": ["context.write"],
    }
    return [
        _context_resource_definition(
            resource_type="adaos.context.capsule",
            title="Context Capsule",
            record_schema_ref="abi:context.capsule.v2",
            filters=["subject_ref", "kind", "trust_class", "sensitivity", "include_revoked"],
            operations=[read("show"), write("create"), write("revoke", "medium")],
            writes="immutable_plus_revoke",
        ),
        _context_resource_definition(
            resource_type="adaos.context.relationship",
            title="Context Relationship",
            record_schema_ref="abi:context.relationship.v1",
            filters=["from_capsule_id", "to_capsule_id", "relation_type", "include_revoked"],
            operations=[read("show"), write("create")],
            writes="append_only",
        ),
        _context_resource_definition(
            resource_type="adaos.context.subject_binding",
            title="Context Subject Binding",
            record_schema_ref="abi:context.subject_binding.v1",
            filters=["subject_ref", "purpose", "audience", "branch"],
            operations=[read("show"), write("bind")],
            writes="optimistic",
        ),
        _context_resource_definition(
            resource_type="adaos.context.plan",
            title="Context Plan",
            record_schema_ref="abi:context.plan.v1",
            filters=["subject_ref", "purpose", "audience", "status"],
            operations=[read("show"), write("create"), write("compile")],
            writes="append_only",
        ),
        _context_resource_definition(
            resource_type="adaos.context.memory_candidate",
            title="Memory Candidate",
            record_schema_ref="abi:context.memory_candidate.v1",
            filters=["status", "kind", "authority_ref", "proposed_by"],
            operations=[read("show"), write("propose"), write("qualify", "medium"), write("promote", "high"), write("rollback", "high")],
            writes="governed",
        ),
        _context_resource_definition(
            resource_type="adaos.agent.context_receipt",
            title="Context Attribution Receipt",
            record_schema_ref="abi:agent.context_receipt.v1",
            filters=["run_ref", "plan_ref", "execution_route"],
            operations=[read("show"), write("create")],
            writes="append_only",
        ),
    ]


@dataclass(slots=True)
class ResourceWorkbenchService:
    state_dir: Path | None = None
    ticket_service: DevelopmentTicketService | None = None
    feedback_service: DevelopmentFeedbackService | None = None
    context_service: ContextControlService | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "resources"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def trace_path(self) -> Path:
        return self.root / "traces.json"

    @property
    def event_path(self) -> Path:
        return self.root / "events.json"

    @property
    def notes_path(self) -> Path:
        return self.root / "demo_metric_notes.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".workbench.lock"

    def definitions(self) -> list[dict[str, Any]]:
        return sorted(
            [
                _dev_ticket_definition(),
                _development_feedback_definition(),
                _demo_metric_definition(),
                _demo_metric_note_definition(),
                _demo_metric_event_definition(),
                *_context_resource_definitions(),
            ],
            key=lambda item: item["resource_type"],
        )

    def definition(self, resource_type: str) -> dict[str, Any] | None:
        token = _text(resource_type)
        for definition in self.definitions():
            if definition["resource_type"] == token:
                return _clone(definition)
        return None

    def query(self, query: Mapping[str, Any]) -> dict[str, Any]:
        request = {
            "schema": RESOURCE_QUERY_SCHEMA,
            "filters": {},
            "relation_filters": {},
            "sort": [],
            "include": [],
            **dict(query),
        }
        if not isinstance(request.get("filters"), Mapping):
            request["filters"] = {}
        if not isinstance(request.get("relation_filters"), Mapping):
            request["relation_filters"] = {}
        if not isinstance(request.get("actor"), Mapping):
            request["actor"] = {"id": "system", "role": "owner"}
        if not isinstance(request.get("subject"), Mapping):
            request["subject"] = {}
        _validate("resource.query.v1", request)
        resource_type = _text(request.get("resource_type"))
        definition = self.definition(resource_type)
        if not definition:
            raise ValueError(f"unknown resource_type: {resource_type}")
        filters = _mapping(request.get("filters"))
        search = _text(request.get("search") or filters.get("search"))
        unsupported = sorted(set(filters) - _query_filter_set(definition))
        started_at = _now()
        trace_base = {
            "schema": RESOURCE_TRACE_SCHEMA,
            "trace_id": f"rtrace.{new_id()}",
            "resource_type": resource_type,
            "semantic_type": "resource.query",
            "query_id": f"rquery.{new_id()}",
            "actor": _mapping(request.get("actor")) or {"id": "system", "role": "owner"},
            "subject": _mapping(request.get("subject")),
            "request": request,
            "started_at": started_at,
        }
        if unsupported:
            trace = {
                **trace_base,
                "status": "unsupported_query",
                "readiness": {"state": "unsupported_query", "unsupported_filters": unsupported},
                "result": {"unsupported_filters": unsupported},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise ValueError(f"unsupported resource query filters: {', '.join(unsupported)}")
        try:
            items = self._query_items(resource_type, filters=filters, search=search, limit=request.get("limit"))
            trace = {
                **trace_base,
                "status": "completed",
                "readiness": {"state": "ready"},
                "result": {"count": len(items)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            return {
                "ok": True,
                "resource_type": resource_type,
                "definition": definition,
                "items": items,
                "count": len(items),
                "cursor": None,
                "unsupported_filters": [],
                "trace": trace,
            }
        except Exception as exc:
            trace = {
                **trace_base,
                "status": "failed",
                "readiness": {"state": "provider_unavailable" if resource_type.startswith("demo.") else "failed"},
                "result": {"error": str(exc)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise

    def operate(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        request = {
            "schema": RESOURCE_OPERATION_SCHEMA,
            "payload": {},
            "actor": {"id": "system", "role": "owner"},
            "subject": {},
            "evidence_refs": [],
            "context": {},
            **dict(operation),
        }
        if not isinstance(request.get("payload"), Mapping):
            request["payload"] = {}
        if not isinstance(request.get("actor"), Mapping):
            request["actor"] = {"id": "system", "role": "owner"}
        if not isinstance(request.get("subject"), Mapping):
            request["subject"] = {}
        if not isinstance(request.get("delegation"), Mapping):
            request["delegation"] = {}
        if not isinstance(request.get("context"), Mapping):
            request["context"] = {}
        if not isinstance(request.get("evidence_refs"), list):
            request["evidence_refs"] = []
        _validate("resource.operation.v1", request)
        resource_type = _text(request.get("resource_type"))
        operation_id = _text(request.get("operation_id"))
        definition = self.definition(resource_type)
        if not definition:
            raise ValueError(f"unknown resource_type: {resource_type}")
        operation_spec = _operation_map(definition).get(operation_id)
        if not operation_spec:
            raise ValueError(f"unsupported resource operation: {resource_type}.{operation_id}")
        access = self._access_decision(definition, operation_spec, request)
        started_at = _now()
        trace_base = {
            "schema": RESOURCE_TRACE_SCHEMA,
            "trace_id": f"rtrace.{new_id()}",
            "resource_type": resource_type,
            "semantic_type": "resource.operation",
            "operation_id": operation_id,
            "actor": _mapping(request.get("actor")),
            "subject": _mapping(request.get("subject")),
            "access_decision": access,
            "request": request,
            "started_at": started_at,
        }
        if not access["allowed"]:
            trace = {
                **trace_base,
                "status": "permission_denied",
                "readiness": {"state": "permission_denied", "reason": access.get("reason")},
                "result": {"error": access.get("reason")},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise ResourceAccessDenied(access.get("reason") or "resource access denied")
        try:
            result = self._operate(resource_type, operation_id, request)
            trace = {
                **trace_base,
                "status": "completed",
                "readiness": {"state": "ready"},
                "result": self._result_summary(result),
                "completed_at": _now(),
            }
            self._append_trace(trace)
            event = self._append_event(
                resource_type=resource_type,
                semantic_type=f"{resource_type}.{operation_id}",
                record_ref=_text(request.get("record_id") or _mapping(request.get("payload")).get("ticket_id") or _mapping(result).get("record_id")),
                operation_id=operation_id,
                actor=_mapping(request.get("actor")),
                payload=self._result_summary(result),
            )
            return {"ok": True, "resource_type": resource_type, "operation_id": operation_id, "result": result, "trace": trace, "event": event}
        except ResourceConflict as exc:
            trace = {
                **trace_base,
                "status": "conflict",
                "readiness": {"state": "conflict"},
                "result": {"error": str(exc)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise
        except ContextConflict as exc:
            trace = {
                **trace_base,
                "status": "conflict",
                "readiness": {"state": "conflict"},
                "result": {"error": str(exc)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise ResourceConflict(str(exc)) from exc
        except ContextAccessDenied as exc:
            trace = {
                **trace_base,
                "status": "permission_denied",
                "readiness": {"state": "permission_denied"},
                "result": {"error": str(exc)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise ResourceAccessDenied(str(exc)) from exc
        except ValueError as exc:
            trace = {
                **trace_base,
                "status": "validation_error",
                "readiness": {"state": "validation_error"},
                "result": {"error": str(exc)},
                "completed_at": _now(),
            }
            self._append_trace(trace)
            raise

    def traces(self, *, resource_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        payload = self._read_trace_state()
        items = [dict(item) for item in payload.get("items") or [] if isinstance(item, Mapping)]
        if _text(resource_type):
            items = [item for item in items if _text(item.get("resource_type")) == _text(resource_type)]
        return _clone(items[-max(0, int(limit)):])

    def events(self, *, resource_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        payload = self._read_event_state()
        items = [dict(item) for item in payload.get("items") or [] if isinstance(item, Mapping)]
        if _text(resource_type):
            items = [item for item in items if _text(item.get("resource_type")) == _text(resource_type)]
        return _clone(items[-max(0, int(limit)):])

    def _tickets(self) -> DevelopmentTicketService:
        return self.ticket_service or DevelopmentTicketService(state_dir=self.state_dir)

    def _feedback(self) -> DevelopmentFeedbackService:
        return self.feedback_service or DevelopmentFeedbackService(state_dir=self.state_dir)

    def _contexts(self) -> ContextControlService:
        return self.context_service or ContextControlService(state_dir=self.state_dir)

    def _query_items(self, resource_type: str, *, filters: Mapping[str, Any], search: str, limit: Any) -> list[dict[str, Any]]:
        max_items = int(limit) if isinstance(limit, int) else None
        if resource_type == "adaos.dev.ticket":
            target_tokens = []
            for key in ("target_ref", "project_id", "scenario_id", "skill_id", "modal_id", "component"):
                value = filters.get(key)
                if isinstance(value, (list, tuple, set)):
                    target_tokens.extend(_text(item) for item in value if _text(item))
                elif _text(value):
                    target_tokens.append(_text(value))
            return self._tickets().list_tickets(
                status=_text(filters.get("status")) or None,
                status_group=_text(filters.get("status_group")) or None,
                target_id=_text(filters.get("target_id")) or None,
                target_tokens=target_tokens,
                kind=_text(filters.get("kind")) or None,
                severity=_text(filters.get("severity")) or None,
                blocking=filters.get("blocking") if isinstance(filters.get("blocking"), bool) else None,
                source=_text(filters.get("source")) or None,
                owner=_text(filters.get("owner")) or None,
                owner_area=_text(filters.get("owner_area")) or None,
                component_ref=_text(filters.get("component_ref")) or None,
                updated_since=_text(filters.get("updated_since")) or None,
                search=search or None,
                limit=max_items,
            )
        if resource_type == "adaos.development.feedback":
            return self._feedback().list(
                status=_text(filters.get("status")) or None,
                category=_text(filters.get("category")) or None,
                source=_text(filters.get("source")) or None,
                blocking=filters.get("blocking") if isinstance(filters.get("blocking"), bool) else None,
                target_ref=_text(filters.get("target_ref")) or None,
                search=search or None,
                rejection_class=_text(filters.get("rejection_class")) or None,
                contract_ref=_text(filters.get("contract_ref")) or None,
                operation_id=_text(filters.get("operation_id")) or None,
                owner_route=_text(filters.get("owner_route")) or None,
                qualification_status=_text(filters.get("qualification_status")) or None,
                updated_since=_text(filters.get("updated_since")) or None,
                limit=max_items or 200,
            )
        if resource_type == "demo.metric":
            if _text(filters.get("fixture")) == "unavailable_provider":
                raise RuntimeError("demo metric provider unavailable")
            records = [] if _text(filters.get("fixture")) == "empty" else _demo_metric_rows()
            if _text(filters.get("fixture")) == "long_text":
                records = [
                    {
                        **record,
                        "title": record["title"] + " with a deliberately long localized label for layout checks",
                    }
                    for record in records
                ]
            return _filter_records(records, filters, search)[:max_items]
        if resource_type == "demo.metric_note":
            if _text(filters.get("fixture")) == "unavailable_provider":
                raise RuntimeError("demo metric note provider unavailable")
            records = [] if _text(filters.get("fixture")) == "empty" else self._read_notes()
            return _filter_records(records, filters, search)[:max_items]
        if resource_type == "demo.metric_event":
            return _filter_records(self.events(limit=1000), filters, search)[:max_items]
        contexts = self._contexts()
        if resource_type == "adaos.context.capsule":
            records = contexts.list_capsules(
                subject_ref=_text(filters.get("subject_ref")) or None,
                kind=_text(filters.get("kind")) or None,
                trust_class=_text(filters.get("trust_class")) or None,
                include_revoked=bool(filters.get("include_revoked")),
                limit=max_items or 200,
            )
            return _filter_records(records, {key: value for key, value in filters.items() if key in {"sensitivity"}}, search)
        if resource_type == "adaos.context.relationship":
            records = contexts.list_relationships(
                from_capsule_id=_text(filters.get("from_capsule_id")) or None,
                to_capsule_id=_text(filters.get("to_capsule_id")) or None,
                relation_type=_text(filters.get("relation_type")) or None,
                include_revoked=bool(filters.get("include_revoked")),
                limit=max_items or 200,
            )
            return _filter_records(records, {}, search)
        if resource_type == "adaos.context.subject_binding":
            records = contexts.list_bindings(
                subject_ref=_text(filters.get("subject_ref")) or None,
                purpose=_text(filters.get("purpose")) or None,
                audience=_text(filters.get("audience")) or None,
                branch=_text(filters.get("branch")) or None,
                limit=max_items or 200,
            )
            return _filter_records(records, {}, search)
        if resource_type == "adaos.context.plan":
            records = contexts.list_plans(subject_ref=_text(filters.get("subject_ref")) or None, limit=max_items or 200)
            return _filter_records(records, {key: value for key, value in filters.items() if key in {"purpose", "audience", "status"}}, search)
        if resource_type == "adaos.context.memory_candidate":
            records = contexts.list_memory_candidates(status=_text(filters.get("status")) or None, limit=max_items or 200)
            return _filter_records(records, {key: value for key, value in filters.items() if key in {"kind", "authority_ref", "proposed_by"}}, search)
        if resource_type == "adaos.agent.context_receipt":
            records = contexts.list_receipts(run_ref=_text(filters.get("run_ref")) or None, limit=max_items or 200)
            return _filter_records(records, {key: value for key, value in filters.items() if key in {"plan_ref", "execution_route"}}, search)
        raise ValueError(f"unknown resource_type: {resource_type}")

    def _operate(self, resource_type: str, operation_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if resource_type == "adaos.dev.ticket":
            return self._operate_dev_ticket(operation_id, request)
        if resource_type == "adaos.development.feedback":
            return self._operate_development_feedback(operation_id, request)
        if resource_type == "demo.metric_note":
            return self._operate_demo_note(operation_id, request)
        if resource_type.startswith("adaos.context.") or resource_type == "adaos.agent.context_receipt":
            return self._operate_context_resource(resource_type, operation_id, request)
        raise ValueError(f"resource operation is read-only: {resource_type}.{operation_id}")

    def _operate_development_feedback(self, operation_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        service = self._feedback()
        payload = _mapping(request.get("payload"))
        feedback_id = _text(request.get("record_id") or payload.get("feedback_id"))
        actor = _actor_id(_mapping(request.get("actor")))
        expected = request.get("expected_revision")
        expected_revision = int(expected) if expected is not None else None
        if operation_id == "show":
            record = service.get(feedback_id)
            if not record:
                raise KeyError(feedback_id)
            return {"record": record, "record_id": feedback_id}
        if operation_id == "qualification_preview":
            return {
                "record": service.qualification_preview(feedback_id),
                "record_id": feedback_id,
            }
        if operation_id == "capture":
            result = service.capture(
                source=_text(payload.get("source")) or "builder",
                category=_text(payload.get("category")),
                summary=_text(payload.get("summary")),
                blocking=bool(payload.get("blocking")),
                confidence=float(payload.get("confidence", 1.0)),
                impact=payload.get("impact") or [],
                target_refs=payload.get("target_refs") or [],
                details=_text(payload.get("details")),
                recommendation=_text(payload.get("recommendation")),
                evidence_refs=_sequence_of_mappings(payload.get("evidence_refs") or []),
                relation_refs=_sequence_of_mappings(payload.get("relation_refs") or []),
                classification=_mapping(payload.get("classification")),
                dedup_key=_text(payload.get("dedup_key")) or None,
                actor=actor,
            )
            record = result["feedback"]
            return {"record": record, "record_id": record["feedback_id"], "duplicate": result["duplicate"]}
        if operation_id in {"triage", "accept", "reject"}:
            status_by_operation = {"triage": "triaged", "accept": "accepted", "reject": "rejected"}
            record = service.transition(
                feedback_id,
                status=status_by_operation[operation_id],
                actor=actor,
                reason=_text(payload.get("reason")),
                classification=_mapping(payload.get("classification")),
                expected_revision=expected_revision,
            )
            return {"record": record, "record_id": feedback_id}
        if operation_id == "qualify":
            record = service.qualify(
                feedback_id,
                owner_route=_text(payload.get("owner_route")),
                promotion_route=_text(payload.get("promotion_route")),
                owner_ref=_text(payload.get("owner_ref")),
                rationale=_text(payload.get("rationale")),
                actor=actor,
                expected_revision=expected_revision,
            )
            return {"record": record, "record_id": feedback_id}
        if operation_id == "comment":
            record = service.comment(
                feedback_id,
                body=_text(payload.get("body")),
                actor=actor,
                expected_revision=expected_revision,
            )
            return {"record": record, "record_id": feedback_id}
        if operation_id == "promote":
            result = service.promote(
                feedback_id,
                route=_text(payload.get("route")),
                actor=actor,
                payload=_mapping(payload.get("promotion")),
                expected_revision=expected_revision,
            )
            return {"record": result["feedback"], "record_id": feedback_id, "ticket": result.get("ticket")}
        raise ValueError(f"unsupported development feedback operation: {operation_id}")

    def _operate_context_resource(self, resource_type: str, operation_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        service = self._contexts()
        payload = _mapping(request.get("payload"))
        record_id = _text(request.get("record_id"))
        actor = _actor_id(_mapping(request.get("actor")))
        expected = request.get("expected_revision")
        if resource_type == "adaos.context.capsule":
            if operation_id == "show":
                include = request.get("include") if isinstance(request.get("include"), list) else []
                return {"record": service.get_capsule(record_id, include_content="content" in include), "record_id": record_id}
            if operation_id == "create":
                record = service.register_capsule(payload, bind=bool(payload.get("bind")))
                return {"record": record, "record_id": record["capsule_id"]}
            if operation_id == "revoke":
                record = service.revoke_capsule(record_id, actor_ref=actor, reason=_text(payload.get("reason")))
                return {"record": record, "record_id": record_id}
        if resource_type == "adaos.context.relationship" and operation_id == "create":
            record = service.add_relationship(payload)
            return {"record": record, "record_id": record["relationship_id"]}
        if resource_type == "adaos.context.subject_binding" and operation_id == "bind":
            record = service.bind_subject(
                subject_ref=_text(payload.get("subject_ref")),
                capsule_id=_text(payload.get("capsule_id")),
                purpose=_text(payload.get("purpose")) or "*",
                audience=_text(payload.get("audience")) or "*",
                branch=_text(payload.get("branch")) or "main",
                expected_revision=int(expected) if expected is not None else None,
                actor_ref=actor,
                reason=_text(payload.get("reason")) or "resource_workbench",
            )
            return {"record": record, "record_id": record["binding_id"]}
        if resource_type == "adaos.context.plan":
            if operation_id == "create":
                record = service.plan(payload)
                return {"record": record, "record_id": record["plan_id"]}
            if operation_id == "compile":
                record = service.compile(payload or {"plan_id": record_id})
                return {"record": record, "record_id": record_id or _text(record.get("packet_digest"))}
        if resource_type == "adaos.agent.context_receipt" and operation_id == "create":
            record = service.record_receipt(payload)
            return {"record": record, "record_id": record["receipt_id"]}
        if resource_type == "adaos.context.memory_candidate":
            if operation_id == "propose":
                record = service.propose_memory(payload)
            elif operation_id == "qualify":
                record = service.qualify_memory(
                    record_id,
                    validation_refs=payload.get("validation_refs") or [],
                    qualified_by=actor,
                    expected_revision=int(expected) if expected is not None else None,
                )
            elif operation_id == "promote":
                result = service.promote_memory(
                    record_id,
                    actor_ref=actor,
                    subject_refs=payload.get("subject_refs") or [],
                    bind=bool(payload.get("bind", True)),
                    expected_revision=int(expected) if expected is not None else None,
                )
                return {"record": result["candidate"], "record_id": record_id, "capsule": result["capsule"]}
            elif operation_id == "rollback":
                result = service.rollback_memory(
                    record_id,
                    actor_ref=actor,
                    reason=_text(payload.get("reason")),
                    restore_capsule_id=_text(payload.get("restore_capsule_id")) or None,
                )
                return {"record": result["candidate"], "record_id": record_id, "rollback": result}
            else:
                raise ValueError(f"unsupported context memory operation: {operation_id}")
            return {"record": record, "record_id": record["candidate_id"]}
        raise ValueError(f"unsupported context resource operation: {resource_type}.{operation_id}")

    def _operate_dev_ticket(self, operation_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        service = self._tickets()
        payload = _mapping(request.get("payload"))
        ticket_id = _text(request.get("record_id") or payload.get("ticket_id"))
        actor = _actor_id(_mapping(request.get("actor")))
        evidence_refs = _sequence_of_mappings(request.get("evidence_refs") or payload.get("evidence_refs") or [])
        if operation_id == "create":
            kind = _text(payload.get("kind") or payload.get("ticket_kind")) or "development_request"
            signal_result = service.capture_signal(
                kind=_text(payload.get("signal_kind")) or _ticket_signal_kind(kind),
                summary=_text(payload.get("summary")),
                owner_scope=_mapping(payload.get("owner_scope")) or {"type": "workspace", "id": "local"},
                origin_scope=_mapping(payload.get("origin_scope")) or {"type": "resource_workbench"},
                target_scope=_mapping(payload.get("target_scope")) or {"type": "unknown"},
                severity=_text(payload.get("severity")) or "medium",
                blocking=bool(payload.get("blocking")),
                source=_text(payload.get("source")) or "resource_workbench",
                dedup_key=_text(payload.get("dedup_key")) or None,
                artifact_refs=_sequence_of_mappings(payload.get("artifact_refs") or []),
                evidence_refs=evidence_refs,
                metadata=_mapping(payload.get("metadata")),
                policy=_mapping(payload.get("policy")),
                owner_area=_text(payload.get("owner_area")) or None,
                component_ref=_text(payload.get("component_ref")) or None,
                relation_refs=_sequence_of_mappings(payload.get("relation_refs") or []),
            )
            ticket_result = service.ensure_ticket_for_signal(
                signal_result["signal"],
                kind=kind,
                status=_text(payload.get("status")) or "proposed",
                source=_text(payload.get("source")) or "resource_workbench",
                dedup_key=_text(payload.get("dedup_key")) or None,
                metadata=_mapping(payload.get("metadata")),
                policy=_mapping(payload.get("policy")),
                owner_area=_text(payload.get("owner_area")) or None,
                component_ref=_text(payload.get("component_ref")) or None,
                relation_refs=_sequence_of_mappings(payload.get("relation_refs") or []),
            )
            return {"signal": signal_result["signal"], "ticket": ticket_result["ticket"], "record_id": ticket_result["ticket"]["ticket_id"]}
        if not ticket_id:
            raise ValueError("ticket_id is required")
        if operation_id == "update":
            ticket = service.update_ticket_summary(ticket_id, summary=_text(payload.get("summary")), actor=actor)
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "claim":
            ticket = service.claim_ticket(ticket_id, actor=actor, owner=_text(payload.get("owner")) or None)
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "start":
            ticket = service.start_ticket(ticket_id, actor=actor)
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "comment":
            ticket = service.comment_ticket(ticket_id, body=_text(payload.get("body") or payload.get("comment")), actor=actor, evidence_refs=evidence_refs)
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "postpone":
            ticket = service.defer_ticket(ticket_id, actor=actor, reason=_text(payload.get("reason")) or "postponed_from_resource_workbench")
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id in {"open_builder", "autonomous_repair"}:
            mode = "autonomous" if operation_id == "autonomous_repair" else "interactive"
            result = service.handoff_ticket(ticket_id, mode=mode, actor=actor, repair_service=BuilderRepairService(state_dir=self.state_dir))
            return {**result, "record_id": ticket_id}
        if operation_id == "resolve":
            return {**service.record_resolution(
                ticket_id,
                evidence_refs=evidence_refs,
                actor=actor,
                resolved_by_version=_text(payload.get("resolved_by_version")) or None,
                resolved_by_overlay=_text(payload.get("resolved_by_overlay")) or None,
                repair_id=_text(payload.get("repair_id")) or None,
                repair_service=BuilderRepairService(state_dir=self.state_dir),
                capability_works=bool(payload.get("capability_works", True)),
                regression_free=bool(payload.get("regression_free", True)),
                accept_reduced_scope=bool(payload.get("accept_reduced_scope", False)),
            ), "record_id": ticket_id}
        if operation_id == "verify":
            return {**service.verify_ticket(
                ticket_id,
                evidence_refs=evidence_refs,
                actor=actor,
                repair_id=_text(payload.get("repair_id")) or None,
                notes=_text(payload.get("notes")),
            ), "record_id": ticket_id}
        if operation_id == "close":
            ticket = service.close_ticket(
                ticket_id,
                reason=_text(payload.get("reason")) or "closed",
                actor=actor,
                evidence_refs=evidence_refs,
            )
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "reopen":
            ticket = service.reopen_ticket(
                ticket_id,
                actor=actor,
                reason=_text(payload.get("reason")),
                evidence_refs=evidence_refs,
            )
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "duplicate":
            ticket = service.duplicate_ticket(ticket_id, duplicate_of=_text(payload.get("duplicate_of")), actor=actor)
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "related":
            ticket = service.relate_ticket(
                ticket_id,
                related_ticket_id=_text(payload.get("related_ticket_id")),
                relation=_text(payload.get("relation")) or "related",
                actor=actor,
            )
            return {"ticket": ticket, "record_id": ticket_id}
        if operation_id == "core_request":
            return {**service.create_core_capability_request(
                summary=_text(payload.get("summary")),
                component_ref=_text(payload.get("component_ref")),
                desired_contract=_text(payload.get("desired_contract")),
                actor=actor,
                impact=_text(payload.get("impact")) or "contract_gap",
                motivation=_text(payload.get("motivation")),
                observed_limitation=_text(payload.get("observed_limitation")),
                rejected_workarounds=_sequence_of_mappings(payload.get("rejected_workarounds") or []),
                blocked_ticket_ids=[_text(ticket_id)] if ticket_id else [
                    _text(item) for item in payload.get("blocked_ticket_ids") or [] if _text(item)
                ],
                evidence_refs=evidence_refs,
                metadata=_mapping(payload.get("metadata")),
                policy=_mapping(payload.get("policy")),
                status=_text(payload.get("status")) or "proposed",
            ), "record_id": ticket_id}
        if operation_id == "core_transition":
            return {
                **service.transition_core_ticket(
                    ticket_id,
                    transition=_text(payload.get("transition")),
                    actor=actor,
                    reason=_text(payload.get("reason")),
                    notes=_text(payload.get("notes")),
                    evidence_refs=evidence_refs,
                    release_ref=_mapping(payload.get("release_ref")) or None,
                    capability_ref=_mapping(payload.get("capability_ref")) or None,
                    publish_pending_actions=bool(payload.get("publish_pending_actions", True)),
                ),
                "record_id": ticket_id,
            }
        if operation_id == "sdk_understanding":
            return {**service.record_sdk_understanding_signal(
                kind=_text(payload.get("kind")) or "sdk_unclear_definition",
                summary=_text(payload.get("summary")),
                method_ref=_text(payload.get("method_ref")),
                actor=actor,
                expected_behavior=_text(payload.get("expected_behavior")),
                observed_behavior=_text(payload.get("observed_behavior")),
                diagnosis=_text(payload.get("diagnosis")),
                project_ticket_id=ticket_id or _text(payload.get("project_ticket_id")) or None,
                evidence_refs=evidence_refs,
                metadata=_mapping(payload.get("metadata")),
                status=_text(payload.get("status")) or "proposed",
            ), "record_id": ticket_id}
        if operation_id == "preview_evidence":
            ticket = service.get_ticket(ticket_id)
            if not ticket:
                raise KeyError(ticket_id)
            return {"ticket": ticket, "evidence_refs": list(ticket.get("evidence_refs") or []), "record_id": ticket_id}
        if operation_id == "open_artifact":
            artifact_id = _text(payload.get("artifact_id"))
            artifact = service.get_artifact(artifact_id)
            if not artifact:
                raise KeyError(artifact_id)
            return {"artifact": artifact, "record_id": ticket_id}
        raise ValueError(f"unsupported Dev Ticket operation: {operation_id}")

    def _operate_demo_note(self, operation_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        payload = _mapping(request.get("payload"))
        actor = _actor_id(_mapping(request.get("actor")))
        with mutation_lock(self.lock_path, timeout_s=30.0):
            notes = self._read_notes()
            if operation_id == "create":
                title = _text(payload.get("title"))
                if not title:
                    raise ValueError("demo.metric_note title is required")
                now = _now()
                note = {
                    "id": _text(payload.get("id")) or f"dnote.{new_id()}",
                    "metric_id": _text(payload.get("metric_id")) or "cpu",
                    "title": title,
                    "body": _text(payload.get("body")),
                    "status": "active",
                    "revision": 1,
                    "actor": actor,
                    "created_at": now,
                    "updated_at": now,
                }
                notes.append(note)
                self._write_notes(notes)
                return {"record": note, "record_id": note["id"]}
            record_id = _text(request.get("record_id") or payload.get("id"))
            if not record_id:
                raise ValueError("demo.metric_note id is required")
            index = next((idx for idx, item in enumerate(notes) if item.get("id") == record_id), -1)
            if index < 0:
                raise KeyError(record_id)
            note = dict(notes[index])
            expected = request.get("expected_revision")
            if expected is not None and _text(expected) != _text(note.get("revision")):
                raise ResourceConflict("demo.metric_note revision conflict")
            if operation_id == "update":
                title = _text(payload.get("title") if "title" in payload else note.get("title"))
                if not title:
                    raise ValueError("demo.metric_note title is required")
                now = _now()
                note.update(
                    {
                        "metric_id": _text(payload.get("metric_id") if "metric_id" in payload else note.get("metric_id")) or "cpu",
                        "title": title,
                        "body": _text(payload.get("body") if "body" in payload else note.get("body")),
                        "revision": int(note.get("revision") or 1) + 1,
                        "actor": actor,
                        "updated_at": now,
                    }
                )
                notes[index] = note
                self._write_notes(notes)
                return {"record": note, "record_id": record_id}
            if operation_id == "delete":
                removed = notes.pop(index)
                self._write_notes(notes)
                return {"record": removed, "record_id": record_id, "deleted": True}
        raise ValueError(f"unsupported demo.metric_note operation: {operation_id}")

    def _access_decision(
        self,
        definition: Mapping[str, Any],
        operation_spec: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        actor = _mapping(request.get("actor"))
        role = _actor_role(actor)
        operation_id = _text(operation_spec.get("id"))
        required = list(operation_spec.get("required_capabilities") or [])
        role_policy = _mapping(_mapping(definition.get("access")).get("role_fixtures"))
        decision = "allowed"
        if role_policy:
            role_rules = role_policy.get(role)
            if isinstance(role_rules, Mapping):
                decision = _text(role_rules.get(operation_id)) or "allowed"
            elif isinstance(role_rules, str):
                decision = _text(role_rules) or "allowed"
        mutating = _text(operation_spec.get("risk")) not in {"", "read"} and _text(operation_spec.get("kind")) not in {"list", "show"}
        if role == "guest" and mutating and decision == "allowed":
            decision = "denied"
        policy_digest = _digest(_mapping(definition.get("access")))
        return {
            "allowed": decision not in {"denied", "hidden"},
            "decision": decision,
            "actor": _actor_id(actor),
            "subject": _mapping(request.get("subject")) or actor,
            "role": role,
            "required_capabilities": required,
            "policy_digest": policy_digest,
            "reason": None if decision not in {"denied", "hidden"} else f"role_{role}_{decision}",
        }

    def _result_summary(self, result: Any) -> dict[str, Any]:
        value = _mapping(result)
        if "ticket" in value and isinstance(value["ticket"], Mapping):
            ticket = value["ticket"]
            return {"ticket_id": ticket.get("ticket_id"), "status": ticket.get("status")}
        if "record" in value and isinstance(value["record"], Mapping):
            record = value["record"]
            return {"record_id": record.get("id"), "revision": record.get("revision"), "status": record.get("status")}
        return {key: value.get(key) for key in ("record_id", "ok", "deleted") if key in value}

    def _read_notes(self) -> list[dict[str, Any]]:
        if not self.notes_path.is_file():
            return [
                {
                    "id": "dnote.seed",
                    "metric_id": "cpu",
                    "title": "Investigate CPU spike",
                    "body": "Synthetic note used by the workbench CRUD demo.",
                    "status": "active",
                    "revision": 1,
                    "actor": "demo",
                    "created_at": "2026-05-07T10:00:00+00:00",
                    "updated_at": "2026-05-07T10:00:00+00:00",
                }
            ]
        value = json.loads(self.notes_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            return []
        return [dict(item) for item in value.get("items") or [] if isinstance(item, Mapping)]

    def _write_notes(self, notes: Sequence[Mapping[str, Any]]) -> None:
        atomic_write_json(
            self.notes_path,
            {
                "schema": "adaos.demo.metric_notes.state.v1",
                "items": [dict(item) for item in notes],
                "updated_at": _now(),
            },
        )

    def _read_trace_state(self) -> dict[str, Any]:
        if not self.trace_path.is_file():
            return {"schema": "adaos.resource.traces.v1", "items": []}
        value = json.loads(self.trace_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            return {"schema": "adaos.resource.traces.v1", "items": []}
        return {"schema": "adaos.resource.traces.v1", "items": list(value.get("items") or [])}

    def _append_trace(self, trace: Mapping[str, Any]) -> None:
        payload = dict(trace)
        _validate("resource.trace.v1", payload)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read_trace_state()
            items = [dict(item) for item in state.get("items") or [] if isinstance(item, Mapping)]
            items.append(payload)
            atomic_write_json(self.trace_path, {"schema": "adaos.resource.traces.v1", "items": items[-1000:]})

    def _read_event_state(self) -> dict[str, Any]:
        if not self.event_path.is_file():
            return {"schema": "adaos.resource.events.v1", "items": []}
        value = json.loads(self.event_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            return {"schema": "adaos.resource.events.v1", "items": []}
        return {"schema": "adaos.resource.events.v1", "items": list(value.get("items") or [])}

    def _append_event(
        self,
        *,
        resource_type: str,
        semantic_type: str,
        record_ref: str,
        operation_id: str,
        actor: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        event = {
            "schema": RESOURCE_EVENT_SCHEMA,
            "event_id": f"revent.{new_id()}",
            "resource_type": resource_type,
            "semantic_type": semantic_type,
            "record_ref": record_ref,
            "operation_id": operation_id,
            "actor": dict(actor),
            "payload": dict(payload),
            "occurred_at": _now(),
        }
        _validate("resource.event.v1", event)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read_event_state()
            items = [dict(item) for item in state.get("items") or [] if isinstance(item, Mapping)]
            items.append(event)
            atomic_write_json(self.event_path, {"schema": "adaos.resource.events.v1", "items": items[-1000:]})
        return event


__all__ = [
    "RESOURCE_DEFINITION_SCHEMA",
    "RESOURCE_EVENT_SCHEMA",
    "RESOURCE_OPERATION_SCHEMA",
    "RESOURCE_QUERY_SCHEMA",
    "RESOURCE_TRACE_SCHEMA",
    "ResourceAccessDenied",
    "ResourceConflict",
    "ResourceWorkbenchService",
]
