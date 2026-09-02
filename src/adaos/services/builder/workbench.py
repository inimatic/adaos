from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.project_events import (
    BUILDER_CONTEXT_SELECTED,
    BUILDER_PREVIEW_DESIRED,
    BUILDER_PREVIEW_OBSERVED,
    BUILDER_PREVIEW_TRANSITIONED,
)
from adaos.sdk.core.decorators import subscribe
from adaos.services.builder.preview_reconciler import BuilderPreviewReconciler
from adaos.services.runtime_paths import current_state_dir
from adaos.services.webspace_id import coerce_webspace_id
from adaos.services.workspaces.relations import (
    BUILDER_PROJECT_PREVIEW,
    BUILDER_SELF_HOST,
    WebspaceRelationshipRegistry,
    relation_purpose_for_scenario,
)


BUILDER_WORKBENCH_SCENARIO_ID = "prompt_engineer_scenario"
BUILDER_HOST_SCENARIO_ID = "builder"
BUILDER_RUNTIME_FALLBACK_SCENARIO_ID = "web_desktop"
BUILDER_DIALOG_CHANNEL_ID = "builder"
BUILDER_SKILL_ID = "builder_skill"
BUILDER_OWNER = f"skill:{BUILDER_SKILL_ID}"
_log = logging.getLogger("adaos.builder.workbench")
_PROJECTION_TASKS: dict[str, asyncio.Task[Any]] = {}
_PROJECTION_PENDING: dict[str, dict[str, Any]] = {}


def safe_source_webspace_id(value: Any) -> str:
    fallback = os.getenv("ADAOS_WEBSPACE_ID") or "desktop"
    token = coerce_webspace_id(value, fallback=fallback)
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(token or "").strip()).strip(".:-_")
    return token or fallback


def source_webspace_id_for(value: Any) -> str:
    """Resolve a Builder host from explicit topology, without parsing its id."""
    token = safe_source_webspace_id(value)
    try:
        return WebspaceRelationshipRegistry.from_context().resolve_builder_host(token)
    except Exception:
        return token


def dev_webspace_id_for_source(source_webspace_id: Any) -> str:
    """Compatibility lookup for the explicitly paired preview webspace."""

    source = source_webspace_id_for(source_webspace_id)
    registry = WebspaceRelationshipRegistry.from_context()
    relation = registry.get_outgoing(source)
    if relation is None:
        relation, _created = registry.ensure(source, purpose=BUILDER_PROJECT_PREVIEW)
    return relation.target_webspace_id


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(data, dict):
        return dict(default or {})
    return data


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_path_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return token or "draft"


def _binding_semantic_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = (
        "source_webspace_id",
        "dev_webspace_id",
        "preview_webspace_id",
        "relationship",
        "scenario_id",
        "runtime_scenario_id",
        "purpose",
        "active_draft_id",
        "selection",
        "preview_target",
        "dialog",
    )
    return all(left.get(key) == right.get(key) for key in keys)


def _latest_ticket_repair_id(ticket: Mapping[str, Any]) -> str:
    refs = ticket.get("builder_refs") if isinstance(ticket.get("builder_refs"), list) else []
    for raw in reversed(refs):
        if not isinstance(raw, Mapping):
            continue
        repair_id = str(raw.get("repair_id") or "").strip()
        if repair_id:
            return repair_id
    return ""


def _builder_work_id(ref: Mapping[str, Any], index: int) -> str:
    for key in ("repair_id", "task_id", "work_id", "id"):
        token = str(ref.get(key) or "").strip()
        if token:
            return token
    return f"builder-work-{index + 1}"


def _builder_automation_context(ref: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    automation = context.get("automation") if isinstance(context.get("automation"), Mapping) else {}
    if not automation and isinstance(ref.get("automation"), Mapping):
        automation = ref.get("automation") or {}
    return dict(automation) if isinstance(automation, Mapping) else {}


def _builder_token_accounting(ref: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
    economic = context.get("economic") if isinstance(context.get("economic"), Mapping) else {}
    usage = (
        ref.get("token_usage")
        if isinstance(ref.get("token_usage"), Mapping)
        else task.get("token_usage")
        if isinstance(task.get("token_usage"), Mapping)
        else context.get("usage")
        if isinstance(context.get("usage"), Mapping)
        else {}
    )
    estimate = (
        ref.get("cost_estimate")
        if isinstance(ref.get("cost_estimate"), Mapping)
        else task.get("cost_estimate")
        if isinstance(task.get("cost_estimate"), Mapping)
        else context.get("cost_estimate")
        if isinstance(context.get("cost_estimate"), Mapping)
        else {}
    )
    return {
        "schema": "adaos.builder.codex_token_accounting.v1",
        "subscription_resource": str(economic.get("subscription_resource") or "codex.api.tokens"),
        "source_of_truth": str(economic.get("source_of_truth") or "adaos.root_mgmnt.codex_usage_event.v1"),
        "usage_event_endpoint": str(economic.get("usage_event_endpoint") or "/hub/economic/codex/usage"),
        "required_for_statuses": list(
            economic.get("required_for_statuses")
            if isinstance(economic.get("required_for_statuses"), list)
            else ["succeeded", "failed", "errored", "cancelled"]
        ),
        "policy": str(
            economic.get("policy")
            or "record provider-reported billable tokens even when repair work fails"
        ),
        "reported_usage": dict(usage) if isinstance(usage, Mapping) else {},
        "estimate": dict(estimate) if isinstance(estimate, Mapping) else {},
    }


def _builder_ticket_work_stream(
    ticket: Mapping[str, Any],
    *,
    repair_tasks: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    entries: list[dict[str, Any]] = []
    builder_items: list[dict[str, Any]] = []
    repairs = repair_tasks or {}

    entries.append(
        {
            "entry_id": f"{ticket_id}:ticket",
            "kind": "user_ticket",
            "authority": "adaos.dev.ticket",
            "title": str(ticket.get("summary") or "").strip(),
            "status": ticket.get("status"),
            "status_group": ticket.get("status_group"),
            "human_manageable": True,
            "read_only": False,
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
        }
    )

    for index, comment in enumerate(_ticket_mapping_list(ticket.get("comments"))):
        comment_id = str(comment.get("id") or index).strip()
        entries.append(
            {
                "entry_id": f"{ticket_id}:comment:{comment_id}",
                "kind": "user_comment",
                "authority": "adaos.dev.ticket.comment",
                "title": str(comment.get("body") or comment.get("summary") or "").strip(),
                "actor": comment.get("actor"),
                "human_manageable": True,
                "read_only": True,
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("created_at"),
                "evidence_refs": _ticket_mapping_list(comment.get("evidence_refs")),
            }
        )

    for index, ref in enumerate(_ticket_mapping_list(ticket.get("builder_refs"))):
        work_id = _builder_work_id(ref, index)
        task = repairs.get(work_id) or {}
        context = task.get("context") if isinstance(task.get("context"), Mapping) else {}
        automation = _builder_automation_context(ref, task)
        item = {
            "entry_id": f"{ticket_id}:builder:{work_id}",
            "kind": "builder_work_item",
            "authority": "adaos.builder.repair_task",
            "work_id": work_id,
            "work_type": str(ref.get("type") or "builder_repair_task"),
            "mode": str(ref.get("mode") or ref.get("handoff_mode") or "").strip() or None,
            "status": task.get("status") or ref.get("status") or "linked",
            "summary": task.get("summary") or ref.get("summary") or "",
            "project_id": task.get("project_id") or context.get("project_id") or None,
            "repair_id": str(ref.get("repair_id") or task.get("repair_id") or "").strip() or None,
            "human_manageable": False,
            "read_only": True,
            "created_at": task.get("created_at") or ref.get("created_at"),
            "updated_at": task.get("updated_at") or ref.get("updated_at") or ref.get("created_at"),
            "acceptance": dict(task.get("acceptance") or {}) if isinstance(task.get("acceptance"), Mapping) else {},
            "automation": automation,
            "automation_session_id": automation.get("session_id") or ref.get("automation_session_id"),
            "automation_task_id": automation.get("task_id") or ref.get("automation_task_id"),
            "automation_status": automation.get("status") or ref.get("automation_status"),
            "token_accounting": _builder_token_accounting(ref, task),
        }
        builder_items.append(item)
        entries.append(item)

    def _entry_order(entry: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(entry.get("created_at") or entry.get("updated_at") or ""),
            str(entry.get("entry_id") or ""),
        )

    entries = sorted(entries, key=_entry_order)
    return {
        "schema": "adaos.builder.ticket_work_stream.v1",
        "ticket_id": ticket_id,
        "authority": {
            "user_ticket": "adaos.dev.ticket",
            "builder_work": "adaos.builder.repair_task",
            "token_usage": "adaos.root_mgmnt.codex_usage_event.v1",
        },
        "lifecycle_split": {
            "user_ticket_human_manageable": True,
            "builder_work_human_manageable": False,
            "builder_work_status_source": "Builder repair/task registry",
            "one_user_ticket_can_spawn_many_builder_items": True,
        },
        "builder_work_count": len(builder_items),
        "builder_work_items": builder_items,
        "entries": entries,
    }


def _ticket_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _ticket_owner_area(ticket: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    explicit = str(ticket.get("owner_area") or target.get("owner_area") or "").strip().lower()
    if explicit:
        return explicit
    target_type = str(target.get("type") or "").strip().lower()
    if target_type in {"core", "api", "sdk", "runtime", "builder", "project", "skill", "scenario", "nlu"}:
        return target_type
    if target_type in {"modal", "component", "webui", "ui"}:
        return "project"
    if str(target.get("project_ref") or target.get("project_id") or "").strip():
        return "project"
    if str(target.get("skill_ref") or target.get("skill_id") or "").strip():
        return "skill"
    if str(target.get("scenario_ref") or target.get("scenario_id") or "").strip():
        return "scenario"
    return "workspace"


def _ticket_component_ref(ticket: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    for source in (ticket, target):
        for key in (
            "component_ref",
            "ref",
            "canonical_ref",
            "target_ref",
            "modal_ref",
            "skill_ref",
            "scenario_ref",
            "project_ref",
            "core_ref",
            "sdk_ref",
            "api_ref",
        ):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or target.get("name") or "").strip()
    return f"{target_type}:{target_id}" if target_type and target_id else ""


def _ticket_relation_refs(ticket: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = _ticket_mapping_list(ticket.get("relation_refs"))
    legacy = _ticket_mapping_list(ticket.get("related_refs"))
    if not legacy:
        return refs
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in refs}
    for item in legacy:
        relation = str(item.get("relation") or item.get("type") or "related").strip().lower() or "related"
        ticket_id = str(item.get("ticket_id") or item.get("id") or "").strip()
        normalized = {
            **item,
            "type": relation,
            "relation": relation,
            "target_ref": str(item.get("target_ref") or (f"dticket:{ticket_id}" if ticket_id else "")).strip(),
        }
        if ticket_id:
            normalized["ticket_id"] = ticket_id
        key = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            refs.append(normalized)
            seen.add(key)
    return refs


def _builder_ticket_qualification(
    ticket: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    development_source: Mapping[str, Any],
) -> dict[str, Any]:
    owner_area = _ticket_owner_area(ticket, target)
    component_ref = _ticket_component_ref(ticket, target)
    kind = str(ticket.get("kind") or "").strip().lower()
    status = str(ticket.get("status") or "").strip().lower()
    source_status = str(development_source.get("status") or "").strip().lower()
    relations = _ticket_relation_refs(ticket)
    blocked_by = [item for item in relations if str(item.get("relation") or item.get("type") or "").strip().lower() == "blocked_by"]
    target_id = str(target.get("id") or target.get("name") or "").strip()
    guardrails = [
        "builder_uses_public_sdk_api_only",
        "project_repair_must_not_modify_core",
        "ticket_resolution_requires_validation_evidence",
    ]
    if status == "waiting_for_core" or blocked_by:
        return {
            "schema": "adaos.builder.ticket_qualification.v1",
            "class": "needs_core",
            "confidence": "high",
            "repair_allowed": False,
            "autonomous_allowed": False,
            "recommended_next": "wait_for_linked_core_ticket_or_rescope",
            "reason": "ticket is blocked by a core/API/SDK capability ticket",
            "owner_area": owner_area,
            "component_ref": component_ref or None,
            "blocked_by": blocked_by,
            "guardrails": guardrails,
        }
    if owner_area in {"core", "api", "runtime"} or component_ref.startswith(("core:", "api:", "runtime:")):
        return {
            "schema": "adaos.builder.ticket_qualification.v1",
            "class": "needs_core",
            "confidence": "high",
            "repair_allowed": False,
            "autonomous_allowed": False,
            "recommended_next": "create_or_update_core_capability_request",
            "reason": "ticket is owned by core/runtime/API rather than the project repair surface",
            "owner_area": owner_area,
            "component_ref": component_ref or None,
            "guardrails": guardrails,
        }
    if owner_area == "sdk" or kind == "sdk_understanding" or component_ref.startswith("sdk:"):
        return {
            "schema": "adaos.builder.ticket_qualification.v1",
            "class": "uncertain_sdk",
            "confidence": "high",
            "repair_allowed": False,
            "autonomous_allowed": False,
            "recommended_next": "record_sdk_understanding_or_link_core_request",
            "reason": "ticket describes SDK/API contract understanding rather than a direct project patch",
            "owner_area": owner_area,
            "component_ref": component_ref or None,
            "guardrails": guardrails,
        }
    if not target_id:
        return {
            "schema": "adaos.builder.ticket_qualification.v1",
            "class": "needs_user_clarification",
            "confidence": "medium",
            "repair_allowed": False,
            "autonomous_allowed": False,
            "recommended_next": "ask_user_for_target_artifact",
            "reason": "ticket target is missing a stable artifact id",
            "owner_area": owner_area,
            "component_ref": component_ref or None,
            "guardrails": guardrails,
        }
    if source_status == "needs_materialization":
        return {
            "schema": "adaos.builder.ticket_qualification.v1",
            "class": "needs_source",
            "confidence": "high",
            "repair_allowed": True,
            "autonomous_allowed": False,
            "recommended_next": "choose_materialize_fork_overlay_or_defer",
            "reason": "target source is not yet available in the development workspace",
            "owner_area": owner_area,
            "component_ref": component_ref or None,
            "guardrails": guardrails,
        }
    return {
        "schema": "adaos.builder.ticket_qualification.v1",
        "class": "project_solvable",
        "confidence": "medium",
        "repair_allowed": True,
        "autonomous_allowed": True,
        "recommended_next": "plan_builder_repair_with_validation_evidence",
        "reason": "ticket targets a project-owned artifact with development source available",
        "owner_area": owner_area,
        "component_ref": component_ref or None,
        "guardrails": guardrails,
    }


def _builder_ticket_batch(service: Any, ticket: Mapping[str, Any], *, target: Mapping[str, Any], limit: int = 20) -> dict[str, Any]:
    owner_area = _ticket_owner_area(ticket, target)
    component_ref = _ticket_component_ref(ticket, target)
    ticket_id = str(ticket.get("ticket_id") or "").strip()
    query_kwargs: dict[str, Any] = {
        "status_group": "triage,waiting,work,review",
        "limit": max(1, min(int(limit or 20), 50)),
    }
    if owner_area and owner_area != "workspace":
        query_kwargs["owner_area"] = owner_area
    if component_ref:
        query_kwargs["component_ref"] = component_ref
    else:
        target_id = str(target.get("id") or target.get("name") or "").strip()
        if target_id:
            query_kwargs["target_id"] = target_id
    try:
        candidates = service.list_tickets(**query_kwargs)
    except Exception:
        candidates = []
    related: list[dict[str, Any]] = []
    for item in candidates:
        candidate_id = str(item.get("ticket_id") or "").strip()
        if not candidate_id:
            continue
        related.append(
            {
                "ticket_id": candidate_id,
                "current": candidate_id == ticket_id,
                "status": item.get("status"),
                "status_group": item.get("status_group"),
                "kind": item.get("kind"),
                "summary": item.get("summary"),
                "owner_area": item.get("owner_area"),
                "component_ref": item.get("component_ref"),
                "updated_at": item.get("updated_at"),
            }
        )
    return {
        "schema": "adaos.builder.ticket_repair_batch.v1",
        "strategy": "component_family" if component_ref else "target_artifact",
        "owner_area": owner_area,
        "component_ref": component_ref or None,
        "count": len(related),
        "tickets": related,
    }


def _project_selection(
    object_type: Any,
    object_id: Any,
    *,
    title: Any = None,
    description: Any = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = str(object_type or "scenario").strip().lower().rstrip("s")
    project_id = str(object_id or "builder").strip() or "builder"
    old = dict(previous) if isinstance(previous, Mapping) else {}
    same_project = old.get("object_type") == kind and old.get("object_id") == project_id
    resolved_title = str(title or (old.get("title") if same_project else "") or project_id).strip() or project_id
    resolved_description = str(
        description if description is not None else (old.get("description") if same_project else "")
    ).strip()
    topic_id = f"prompt-project:{kind}:{project_id}"
    return {
        "object_type": kind,
        "object_id": project_id,
        "ref": f"{kind}:{project_id}",
        "title": resolved_title,
        "description": resolved_description,
        "topic_id": topic_id,
        "thread_id": topic_id,
    }


def _preview_runtime_projection(value: Any) -> dict[str, Any]:
    runtime = dict(value) if isinstance(value, Mapping) else {}
    return {
        key: runtime.get(key)
        for key in (
            "schema",
            "source_webspace_id",
            "preview_webspace_id",
            "selected_project",
            "desired_scenario",
            "observed_scenario",
            "generation",
            "operation_id",
            "status",
            "requested_at",
            "started_at",
            "completed_at",
            "updated_at",
            "error",
            "drift",
            "observed_version",
            "observed_at",
        )
    }


def _preview_state_projection(value: Any) -> dict[str, Any]:
    state = dict(value) if isinstance(value, Mapping) else {}
    return {
        key: state.get(key)
        for key in (
            "scenario_id",
            "draft_id",
            "version",
            "revision",
            "title",
            "status",
            "phase",
            "updated_at",
            "selected_component_ref",
            "selected_component_id",
            "presentation",
            "bindings",
        )
        if key in state
    }


def _info_to_dict(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    to_dict = getattr(info, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in (
        "id",
        "webspace_id",
        "title",
        "kind",
        "source_mode",
        "home_scenario",
        "current_scenario",
        "current_scenario_exists",
        "degraded",
        "validation_reason",
        "recommended_action",
    ):
        value = getattr(info, key, None)
        if value is not None:
            out[key] = value
    return out


def _draft_runtime_scenario_id(state_dir: Path | None, draft_id: str | None) -> str | None:
    token = str(draft_id or "").strip()
    if not token:
        return None
    draft_path = Path(state_dir or current_state_dir()) / "builder" / "drafts" / token / "builder.draft.json"
    draft = _read_json(draft_path)
    artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
    if str(artifact.get("kind") or "").strip() != "scenario":
        return None
    scenario_id = str(artifact.get("id") or "").strip()
    return scenario_id or None


@dataclass(slots=True)
class BuilderWorkbenchService:
    state_dir: Path | None = None
    webspace_service: Any | None = None
    relationship_registry: WebspaceRelationshipRegistry | None = None
    preview_reconciler: BuilderPreviewReconciler | None = None

    @classmethod
    def from_context(cls) -> "BuilderWorkbenchService":
        return cls(state_dir=current_state_dir())

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "builder" / "workbench"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def binding_path(self, source_webspace_id: str) -> Path:
        return self.root / "bindings" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    def snapshot_path(self, source_webspace_id: str) -> Path:
        return self.root / "snapshots" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    @property
    def relationships(self) -> WebspaceRelationshipRegistry:
        if self.relationship_registry is None:
            self.relationship_registry = WebspaceRelationshipRegistry.from_context()
        return self.relationship_registry

    @property
    def reconciler(self) -> BuilderPreviewReconciler:
        if self.preview_reconciler is None:
            self.preview_reconciler = BuilderPreviewReconciler(state_dir=self.state_dir)
        return self.preview_reconciler

    def resolve_source_webspace_id(self, value: Any) -> str:
        token = safe_source_webspace_id(value)
        incoming = self.relationships.get_incoming(token)
        if incoming is not None:
            return token if incoming.purpose == BUILDER_SELF_HOST else incoming.source_webspace_id

        bindings_root = self.root / "bindings"
        for path in bindings_root.glob("*.json") if bindings_root.is_dir() else ():
            legacy = _read_json(path)
            if str(legacy.get("dev_webspace_id") or legacy.get("preview_webspace_id") or "").strip() != token:
                continue
            source = safe_source_webspace_id(legacy.get("source_webspace_id") or path.stem)
            scenario_id = str(legacy.get("runtime_scenario_id") or "").strip() or None
            relation, _created = self.relationships.ensure(
                source,
                purpose=relation_purpose_for_scenario(scenario_id),
                scenario_id=scenario_id,
                legacy_target_webspace_id=token,
                metadata={"migrated_from": "builder_workbench_binding"},
            )
            return token if relation.purpose == BUILDER_SELF_HOST else source
        return self.relationships.resolve_builder_host(token)

    def resolve_action_source_webspace_id(
        self,
        value: Any,
        *,
        current_scenario_id: Any = None,
    ) -> str:
        """Resolve an action host, claiming the bounded self-host level for Builder."""

        token = safe_source_webspace_id(value)
        if relation_purpose_for_scenario(current_scenario_id) == BUILDER_SELF_HOST:
            return self.relationships.claim_builder_self_host(
                token,
                scenario_id=current_scenario_id,
            )
        return self.resolve_source_webspace_id(token)

    def _ensure_preview_relation(
        self,
        source_webspace_id: str,
        *,
        scenario_id: str | None,
        legacy_target_webspace_id: str | None = None,
    ):
        return self.relationships.ensure(
            source_webspace_id,
            purpose=relation_purpose_for_scenario(scenario_id),
            scenario_id=scenario_id,
            legacy_target_webspace_id=legacy_target_webspace_id,
            metadata={"owner": "builder.workbench"},
        )

    def list_workspace_bindings(self) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        sources: set[str] = set()
        root = self.root / "bindings"
        if root.is_dir():
            for path in root.glob("*.json"):
                raw = _read_json(path)
                source = safe_source_webspace_id(raw.get("source_webspace_id") or path.stem)
                if source in sources:
                    continue
                sources.add(source)
                bindings.append(self.get_workspace_binding(source))
        for relation in self.relationships.list():
            if relation.source_webspace_id in sources:
                continue
            sources.add(relation.source_webspace_id)
            bindings.append(self.get_workspace_binding(relation.source_webspace_id))
        return bindings

    def _webspace_inventory(self) -> dict[str, dict[str, Any]]:
        """Return the operational Webspace inventory without changing topology."""

        svc = self.webspace_service
        if svc is None:
            from adaos.services.scenario.webspace_runtime import WebspaceService

            svc = WebspaceService()
        inventory: dict[str, dict[str, Any]] = {}
        for item in svc.list(mode="mixed"):
            payload = _info_to_dict(item)
            webspace_id = str(payload.get("id") or payload.get("webspace_id") or "").strip()
            if webspace_id:
                inventory[webspace_id] = payload
        return inventory

    def list_builder_hosts(self) -> list[dict[str, Any]]:
        """Discover active Builder surfaces and their explicit Preview targets.

        Discovery is intentionally read-only: it never creates a workbench
        binding, Webspace relation, or Preview Webspace. A Builder host is a
        Webspace whose effective current scenario is a configured Builder
        scenario. Merely having Builder installed or a stale binding is not
        sufficient.
        """

        inventory = self._webspace_inventory()
        configured_builder_scenarios = (
            os.getenv("ADAOS_BUILDER_HOST_SCENARIO_IDS") or BUILDER_HOST_SCENARIO_ID
        )
        builder_scenarios = {
            item.strip()
            for item in str(configured_builder_scenarios).split(",")
            if item.strip()
        }
        contexts: list[dict[str, Any]] = []
        for webspace_id, info in inventory.items():
            current_scenario = str(info.get("current_scenario") or "").strip()
            home_scenario = str(info.get("home_scenario") or "").strip()
            effective_scenario = current_scenario or home_scenario
            if effective_scenario not in builder_scenarios:
                continue

            relation = self.relationships.get_outgoing(webspace_id)
            preview_id = relation.target_webspace_id if relation is not None else ""
            preview = inventory.get(preview_id) if preview_id else None
            status = "ready"
            reason: str | None = None
            if info.get("current_scenario_exists") is False or bool(info.get("degraded")):
                status = "builder_degraded"
                reason = str(info.get("validation_reason") or "builder_webspace_degraded")
            elif relation is None:
                status = "preview_relation_missing"
                reason = "builder_preview_relation_missing"
            elif preview is None:
                status = "preview_webspace_missing"
                reason = "builder_preview_webspace_missing"
            elif str(preview.get("kind") or "").strip() != "dev":
                status = "preview_webspace_invalid"
                reason = "builder_preview_must_be_dev_webspace"

            contexts.append(
                {
                    "schema": "adaos.builder.context_ref.v1",
                    "builder_webspace_id": webspace_id,
                    "builder_title": str(info.get("title") or webspace_id).strip() or webspace_id,
                    "builder_space_kind": str(info.get("kind") or "workspace").strip() or "workspace",
                    "builder_source_mode": str(info.get("source_mode") or "workspace").strip() or "workspace",
                    "builder_scenario_id": effective_scenario,
                    "preview_webspace_id": preview_id or None,
                    "preview_relation_id": relation.relation_id if relation is not None else None,
                    "preview_relation_generation": relation.generation if relation is not None else None,
                    "status": status,
                    "selectable": status == "ready",
                    "reason": reason,
                }
            )
        return sorted(
            contexts,
            key=lambda item: (
                not bool(item.get("selectable")),
                str(item.get("builder_space_kind") or "workspace") == "dev",
                str(item.get("builder_title") or "").casefold(),
                str(item.get("builder_webspace_id") or "").casefold(),
            ),
        )

    def resolve_builder_context(
        self,
        builder_webspace_id: Any,
        *,
        require_ready: bool = True,
    ) -> dict[str, Any]:
        """Resolve one explicit Builder host; never infer it from an id suffix."""

        requested = safe_source_webspace_id(builder_webspace_id)
        context = next(
            (
                item
                for item in self.list_builder_hosts()
                if str(item.get("builder_webspace_id") or "") == requested
            ),
            None,
        )
        if context is None:
            raise ValueError(f"Builder is not active in Webspace {requested!r}")
        if require_ready and not bool(context.get("selectable")):
            status = str(context.get("status") or "unavailable")
            raise ValueError(f"Builder Webspace {requested!r} is not ready: {status}")
        return dict(context)

    async def ensure_dev_webspace(
        self,
        source_webspace_id: str | None = None,
        *,
        active_draft_id: str | None = None,
        scenario_id: str | None = None,
        runtime_scenario_id: str | None = None,
        preview_state: Mapping[str, Any] | None = None,
        wait_for_rebuild: bool = True,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        workbench_scenario = str(scenario_id or "").strip() or BUILDER_WORKBENCH_SCENARIO_ID
        runtime_scenario = (
            str(runtime_scenario_id or "").strip()
            or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
            or BUILDER_RUNTIME_FALLBACK_SCENARIO_ID
        )
        legacy_binding = _read_json(self.binding_path(source_id))
        legacy_target = str(
            legacy_binding.get("preview_webspace_id") or legacy_binding.get("dev_webspace_id") or ""
        ).strip() or None
        relation, relation_created = self._ensure_preview_relation(
            source_id,
            scenario_id=runtime_scenario,
            legacy_target_webspace_id=legacy_target,
        )
        dev_id = relation.target_webspace_id
        created = False
        info_payload: dict[str, Any] = {}
        runtime_payload: dict[str, Any] = {}
        try:
            svc = self.webspace_service
            if svc is None:
                from adaos.services.scenario.webspace_runtime import WebspaceService

                svc = WebspaceService()
            existing = None
            for item in svc.list(mode="mixed"):
                if str(getattr(item, "id", "") or getattr(item, "webspace_id", "") or "").strip() == dev_id:
                    existing = item
                    break
            if existing is None:
                existing = await svc.create(
                    dev_id,
                    f"DEV: {source_id}",
                    scenario_id=runtime_scenario,
                    dev=True,
                )
                created = True
            else:
                kind = str(getattr(existing, "kind", "") or "").strip()
                if kind and kind != "dev":
                    raise ValueError(f"paired webspace {dev_id!r} exists but is not a dev webspace")
            info_payload = _info_to_dict(existing)
            if runtime_scenario and self.webspace_service is None:
                try:
                    from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario, switch_webspace_scenario

                    _requested, coalesced = self.reconciler.request(
                        source_webspace_id=source_id,
                        preview_webspace_id=dev_id,
                        project_kind="scenario",
                        project_id=runtime_scenario,
                        desired_scenario=runtime_scenario,
                    )

                    async def _apply_preview(record: Mapping[str, Any]) -> Mapping[str, Any]:
                        desired = str(record.get("desired_scenario") or "").strip()
                        preview_id = str(record.get("preview_webspace_id") or "").strip()
                        switch_payload = await switch_webspace_scenario(
                            preview_id,
                            desired,
                            set_home=True,
                            # Reconcile owns the background boundary. Waiting here keeps
                            # one materialization in flight while newer desired states
                            # coalesce behind it.
                            wait_for_rebuild=True,
                            request_id=str(record.get("operation_id") or "").strip() or None,
                            request_source="builder.workbench.reconciler",
                        )
                        skip_reason = (
                            str(switch_payload.get("skip_reason") or "").strip()
                            if isinstance(switch_payload, Mapping)
                            else ""
                        )
                        if skip_reason and skip_reason not in {
                            "already_current",
                            "already_current_ready",
                            "already_pending_rebuild",
                        }:
                            return await reload_webspace_from_scenario(
                                preview_id,
                                scenario_id=desired,
                                action="reload",
                                event_payload={
                                    "source": "builder.workbench.reconciler",
                                    "source_webspace_id": source_id,
                                    "operation_id": record.get("operation_id"),
                                },
                            )
                        return switch_payload

                    reconciled = await self.reconciler.reconcile(
                        source_id,
                        _apply_preview,
                        wait=wait_for_rebuild,
                    )
                    runtime_payload = {
                        "ok": str(reconciled.get("status") or "") != "failed",
                        "webspace_id": dev_id,
                        "scenario_id": runtime_scenario,
                        "coalesced": coalesced,
                        "switch": reconciled.get("result"),
                        "error": reconciled.get("error"),
                        "preview_runtime": reconciled,
                    }
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    runtime_payload = {
                        "ok": False,
                        "error": "dev_runtime_reload_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
            elif runtime_scenario:
                _requested, coalesced = self.reconciler.request(
                    source_webspace_id=source_id,
                    preview_webspace_id=dev_id,
                    project_kind="scenario",
                    project_id=runtime_scenario,
                    desired_scenario=runtime_scenario,
                )

                async def _accept_injected(_record: Mapping[str, Any]) -> Mapping[str, Any]:
                    return {"ok": True, "accepted": True, "skipped": "injected_webspace_service"}

                reconciled = await self.reconciler.reconcile(source_id, _accept_injected, wait=True)
                runtime_payload = {
                    "ok": True,
                    "skipped": "injected_webspace_service",
                    "webspace_id": dev_id,
                    "scenario_id": runtime_scenario,
                    "coalesced": coalesced,
                    "preview_runtime": reconciled,
                }
        except Exception as exc:
            info_payload = {"ok": False, "error": "dev_webspace_unavailable", "detail": f"{type(exc).__name__}: {exc}"}

        binding = self.set_active_draft(
            source_webspace_id=source_id,
            active_draft_id=active_draft_id,
            scenario_id=workbench_scenario,
            dev_webspace_id=dev_id,
            runtime_scenario_id=runtime_scenario,
            persist_projection=False,
        )
        binding["created"] = created
        binding["relationship_created"] = relation_created
        binding["dev_webspace"] = info_payload
        binding["runtime"] = runtime_payload
        binding["preview_runtime"] = self.reconciler.describe(source_id)
        _schedule_projection_publish(self, source_id, preview_state=preview_state)
        return binding

    def get_workspace_binding(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        existing = _read_json(self.binding_path(source_id))
        if existing:
            normalized = dict(existing)
            active_draft_id = str(normalized.get("active_draft_id") or "").strip() or None
            runtime_scenario_id = str(normalized.get("runtime_scenario_id") or "").strip() or None
            selection = (
                dict(normalized.get("selection"))
                if isinstance(normalized.get("selection"), Mapping)
                else _project_selection(
                    "scenario",
                    runtime_scenario_id or "builder",
                    title="Builder" if not runtime_scenario_id else None,
                )
            )
            relation, _created = self._ensure_preview_relation(
                source_id,
                scenario_id=runtime_scenario_id,
                legacy_target_webspace_id=str(
                    normalized.get("preview_webspace_id") or normalized.get("dev_webspace_id") or ""
                ).strip() or None,
            )
            dev_id = relation.target_webspace_id
            refreshed_dialog = self.dialog_widget_config(
                source_id,
                active_draft_id=active_draft_id,
                runtime_scenario_id=runtime_scenario_id,
                dev_webspace_id=dev_id,
            )
            relation_payload = relation.to_dict()
            if (
                normalized.get("dialog") != refreshed_dialog
                or normalized.get("preview_webspace_id") != dev_id
                or normalized.get("relationship") != relation_payload
                or normalized.get("selection") != selection
            ):
                normalized["source_webspace_id"] = source_id
                normalized["dev_webspace_id"] = dev_id
                normalized["preview_webspace_id"] = dev_id
                normalized["relationship"] = relation_payload
                normalized["selection"] = selection
                normalized["dialog"] = refreshed_dialog
                normalized["updated_at"] = _now()
                _write_json(self.binding_path(source_id), normalized)
            return normalized
        relation, _created = self._ensure_preview_relation(source_id, scenario_id=None)
        return {
            "source_webspace_id": source_id,
            "dev_webspace_id": relation.target_webspace_id,
            "preview_webspace_id": relation.target_webspace_id,
            "relationship": relation.to_dict(),
            "scenario_id": BUILDER_WORKBENCH_SCENARIO_ID,
            "runtime_scenario_id": None,
            "purpose": "builder_prompt_ide",
            "active_draft_id": None,
            "selection": _project_selection("scenario", "builder", title="Builder"),
            "preview_target": None,
            "dialog": self.dialog_widget_config(
                source_id,
                dev_webspace_id=relation.target_webspace_id,
            ),
            "created_at": None,
            "updated_at": None,
        }

    def set_active_draft(
        self,
        *,
        source_webspace_id: str | None = None,
        active_draft_id: str | None,
        scenario_id: str = BUILDER_WORKBENCH_SCENARIO_ID,
        dev_webspace_id: str | None = None,
        runtime_scenario_id: str | None = None,
        persist_projection: bool = True,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        now = _now()
        existing = _read_json(self.binding_path(source_id))
        runtime_id = (
            str(runtime_scenario_id or "").strip()
            or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
            or existing.get("runtime_scenario_id")
            or None
        )
        relation, _created = self._ensure_preview_relation(
            source_id,
            scenario_id=str(runtime_id or "").strip() or None,
            legacy_target_webspace_id=str(
                dev_webspace_id
                or existing.get("preview_webspace_id")
                or existing.get("dev_webspace_id")
                or ""
            ).strip() or None,
        )
        dev_id = relation.target_webspace_id
        scenario_token = str(scenario_id or existing.get("scenario_id") or BUILDER_WORKBENCH_SCENARIO_ID).strip()
        active_draft_token = str(active_draft_id or "").strip() or None
        previous_selection = existing.get("selection") if isinstance(existing.get("selection"), Mapping) else None
        explicit_runtime_id = str(runtime_scenario_id or "").strip()
        if explicit_runtime_id and (
            not previous_selection
            or str(previous_selection.get("object_type") or "") == "scenario"
            and str(previous_selection.get("object_id") or "") != explicit_runtime_id
        ):
            selection = _project_selection("scenario", explicit_runtime_id, previous=previous_selection)
        else:
            selection = dict(previous_selection) if previous_selection else _project_selection("scenario", "builder", title="Builder")
        preview_target = (
            dict(existing.get("preview_target"))
            if isinstance(existing.get("preview_target"), Mapping)
            else None
        )
        if not previous_selection or (
            str(previous_selection.get("object_type") or "") != str(selection.get("object_type") or "")
            or str(previous_selection.get("object_id") or "") != str(selection.get("object_id") or "")
        ):
            preview_target = None
        binding = {
            "source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "preview_webspace_id": dev_id,
            "relationship": relation.to_dict(),
            "scenario_id": scenario_token,
            "runtime_scenario_id": runtime_id,
            "purpose": "builder_prompt_ide",
            "active_draft_id": active_draft_token,
            "selection": selection,
            "preview_target": preview_target,
            "dialog": self.dialog_widget_config(
                source_id,
                active_draft_id=active_draft_token,
                runtime_scenario_id=runtime_id,
                dev_webspace_id=dev_id,
            ),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        if existing and not persist_projection and _binding_semantic_equal(existing, binding):
            return existing
        _write_json(self.binding_path(source_id), binding)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return binding

    def set_selected_project(
        self,
        *,
        source_webspace_id: str | None = None,
        object_type: str,
        object_id: str,
        title: str | None = None,
        description: str | None = None,
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        previous = binding.get("selection") if isinstance(binding.get("selection"), Mapping) else None
        selection = _project_selection(
            object_type,
            object_id,
            title=title,
            description=description,
            previous=previous,
        )
        if selection == previous:
            return binding
        updated = {**binding, "selection": selection, "updated_at": _now()}
        if not previous or (
            str(previous.get("object_type") or "") != str(selection.get("object_type") or "")
            or str(previous.get("object_id") or "") != str(selection.get("object_id") or "")
        ):
            updated["preview_target"] = None
        _write_json(self.binding_path(source_id), updated)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return updated

    def set_development_ticket_context(
        self,
        *,
        source_webspace_id: str | None = None,
        context: Mapping[str, Any],
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        normalized = dict(context) if isinstance(context, Mapping) else {}
        updated = {**binding, "development_ticket": normalized, "updated_at": _now()}
        _write_json(self.binding_path(source_id), updated)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return updated

    def select_development_ticket(
        self,
        *,
        source_webspace_id: str | None = None,
        ticket_id: str,
        object_type: str | None = None,
        object_id: str | None = None,
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        from adaos.services.builder.repair import BuilderRepairService
        from adaos.services.development_tickets import DevelopmentTicketService, development_source_options

        ticket_token = str(ticket_id or "").strip()
        if not ticket_token:
            raise ValueError("ticket_id is required")
        ticket_service = DevelopmentTicketService(state_dir=self.state_dir)
        ticket = ticket_service.get_ticket(ticket_token)
        if not ticket:
            raise ValueError(f"development ticket not found: {ticket_token}")
        target = ticket.get("target_scope") if isinstance(ticket.get("target_scope"), Mapping) else {}
        target_type = str(object_type or target.get("type") or "").strip().lower().rstrip("s") or "scenario"
        target_id = str(object_id or target.get("id") or target.get("name") or "").strip()
        if not target_id:
            raise ValueError(f"development ticket target is missing id: {ticket_token}")
        binding = self.set_selected_project(
            source_webspace_id=source_webspace_id,
            object_type=target_type,
            object_id=target_id,
            title=str(ticket.get("summary") or target_id).strip() or target_id,
            description=f"Development ticket {ticket_token}",
            persist_projection=False,
        )
        development_source = development_source_options(target)
        try:
            from adaos.services.builder.workspace import BuilderWorkspaceService

            recovery_plan = BuilderWorkspaceService.from_context().development_source_recovery_plan(
                kind=str(development_source.get("target_type") or target_type),
                artifact_id=str(development_source.get("target_id") or target_id),
                project_id=str(development_source.get("project_id") or "").strip() or None,
            )
        except Exception as exc:
            recovery_plan = {
                "schema": "adaos.builder.source_recovery_plan.v1",
                "status": "unavailable",
                "safe_to_apply": False,
                "requires_review": True,
                "errors": [f"{type(exc).__name__}:{exc}"],
            }
        development_source = {
            **development_source,
            "source_recovery_plan": recovery_plan,
        }
        relation_refs = _ticket_relation_refs(ticket)
        qualification = _builder_ticket_qualification(
            ticket,
            target=target,
            development_source=development_source,
        )
        repair_ids = {
            str(ref.get("repair_id") or "").strip()
            for ref in _ticket_mapping_list(ticket.get("builder_refs"))
            if str(ref.get("repair_id") or "").strip()
        }
        repair_tasks: dict[str, Mapping[str, Any]] = {}
        if repair_ids:
            try:
                repair_tasks = {
                    str(task.get("repair_id") or "").strip(): task
                    for task in BuilderRepairService(state_dir=self.state_dir).list()
                    if str(task.get("repair_id") or "").strip() in repair_ids
                }
            except Exception:
                repair_tasks = {}
        work_stream = _builder_ticket_work_stream(ticket, repair_tasks=repair_tasks)
        context = {
            "schema": "adaos.builder.development_ticket_context.v1",
            "ticket_id": ticket_token,
            "kind": ticket.get("kind"),
            "status": ticket.get("status"),
            "status_group": ticket.get("status_group"),
            "summary": ticket.get("summary"),
            "owner_area": _ticket_owner_area(ticket, target),
            "component_ref": _ticket_component_ref(ticket, target) or None,
            "target_scope": dict(target),
            "development_source": development_source,
            "qualification": qualification,
            "repair_batch": _builder_ticket_batch(ticket_service, ticket, target=target),
            "work_stream": work_stream,
            "builder_work_items": work_stream["builder_work_items"],
            "relation_refs": relation_refs,
            "comments": _ticket_mapping_list(ticket.get("comments")),
            "builder_refs": list(ticket.get("builder_refs") or []),
            "latest_repair_id": _latest_ticket_repair_id(ticket) or None,
            "evidence_refs": list(ticket.get("evidence_refs") or []),
            "artifact_refs": list(ticket.get("artifact_refs") or []),
            "policy": dict(ticket.get("policy") or {}),
            "metadata": dict(ticket.get("metadata") or {}),
        }
        return self.set_development_ticket_context(
            source_webspace_id=binding.get("source_webspace_id") or source_webspace_id,
            context=context,
            persist_projection=persist_projection,
        )

    def set_preview_target(
        self,
        *,
        source_webspace_id: str | None = None,
        target: Mapping[str, Any] | None,
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        normalized = dict(target) if isinstance(target, Mapping) else None
        updated = {**binding, "preview_target": normalized, "updated_at": _now()}
        _write_json(self.binding_path(source_id), updated)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return updated

    def open_dev_webspace(self, source_webspace_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
        binding = self.get_workspace_binding(source_webspace_id)
        dev_id = str(binding.get("dev_webspace_id") or "").strip()
        base = str(base_url or "").strip().rstrip("/")
        url = f"{base}/?webspace={dev_id}" if base else f"/?webspace={dev_id}"
        return {"ok": True, "url": url, "webspace_id": dev_id, "binding": binding}

    async def open_dev_webspace_ready(
        self,
        source_webspace_id: str | None = None,
        *,
        base_url: str | None = None,
        active_draft_id: str | None = None,
        runtime_scenario_id: str | None = None,
        ticket_id: str | None = None,
        selected_object_type: str | None = None,
        selected_object_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_workspace_binding(source_webspace_id)
        binding = await self.ensure_dev_webspace(
            source_webspace_id,
            active_draft_id=active_draft_id if active_draft_id is not None else existing.get("active_draft_id"),
            runtime_scenario_id=runtime_scenario_id or existing.get("runtime_scenario_id"),
        )
        ticket_token = str(ticket_id or "").strip()
        if ticket_token:
            binding = self.select_development_ticket(
                source_webspace_id=binding.get("source_webspace_id") or source_webspace_id,
                ticket_id=ticket_token,
                object_type=selected_object_type,
                object_id=selected_object_id,
                persist_projection=False,
            )
        return {
            **self.open_dev_webspace(source_webspace_id, base_url=base_url),
            "binding": binding,
        }

    def dialog_widget_config(
        self,
        source_webspace_id: str | None = None,
        *,
        active_draft_id: str | None = None,
        runtime_scenario_id: str | None = None,
        dev_webspace_id: str | None = None,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        relation = self.relationships.get_outgoing(source_id)
        if relation is None:
            relation, _created = self._ensure_preview_relation(
                source_id,
                scenario_id=str(runtime_scenario_id or "").strip() or None,
            )
        dev_id = str(dev_webspace_id or relation.target_webspace_id).strip()
        try:
            from adaos.services.conversation_links import ensure_builder_topic

            topic = ensure_builder_topic(
                source_id,
                active_draft_id=active_draft_id,
                scenario_id=runtime_scenario_id,
                dev_webspace_id=dev_id,
            )
        except Exception:
            runtime_token = str(runtime_scenario_id or "").strip()
            project_id = str(active_draft_id or runtime_token or dev_id or "default").strip() or "default"
            if runtime_token and not str(active_draft_id or "").strip():
                fallback_topic_id = f"prompt-project:scenario:{runtime_token}"
                fallback_thread_id = fallback_topic_id
                fallback_kind = "builder_scenario"
            else:
                token = project_id
                fallback_topic_id = f"builder:{source_id}:{token}"
                fallback_thread_id = f"thread.builder.{source_id}.{token}"
                fallback_kind = "builder_default"
            topic = {
                "schema": "adaos.conversation.topic_ref.v1",
                "topic_id": fallback_topic_id,
                "thread_id": fallback_thread_id,
                "topic_kind": fallback_kind,
                "webspace_id": source_id,
                "source_webspace_id": source_id,
                "active_draft_id": str(active_draft_id or "").strip() or None,
                "scenario_id": str(runtime_scenario_id or "").strip() or None,
                "dev_webspace_id": dev_id,
                "project_id": project_id,
                "conversation_id": f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}",
                "channel_id": BUILDER_DIALOG_CHANNEL_ID,
                "owner": BUILDER_OWNER,
                "stored": False,
            }
        runtime_token = str(runtime_scenario_id or "").strip()
        if runtime_token and runtime_token != BUILDER_RUNTIME_FALLBACK_SCENARIO_ID:
            scenario_topic_id = f"prompt-project:scenario:{runtime_token}"
            topic = {k: v for k, v in dict(topic).items() if v is not None}
            topic["topic_id"] = scenario_topic_id
            topic["thread_id"] = scenario_topic_id
            topic["topic_kind"] = "builder_scenario"
            topic["scenario_id"] = runtime_token
            topic["project_id"] = runtime_token
            topic.setdefault("schema", "adaos.conversation.topic_ref.v1")
            topic.setdefault("webspace_id", source_id)
            topic.setdefault("source_webspace_id", source_id)
            topic.setdefault("dev_webspace_id", dev_id)
            topic.setdefault("conversation_id", f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}")
            topic.setdefault("channel_id", BUILDER_DIALOG_CHANNEL_ID)
            topic.setdefault("owner", BUILDER_OWNER)
        conversation_id = str(topic.get("conversation_id") or f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}").strip()
        thread_id = str(topic.get("thread_id") or "").strip()
        topic_id = str(topic.get("topic_id") or "").strip()
        send_meta = {
            "source_webspace_id": source_id,
            "builder_source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
            "conversation_id": conversation_id,
            "conversation_owner": BUILDER_OWNER,
            "thread_id": thread_id,
            "topic_id": topic_id,
            "conversation_thread_id": thread_id,
            "conversation_topic_id": thread_id or topic_id,
            "builder_topic": {k: v for k, v in topic.items() if k != "stored"},
            "route_id": "voice_chat",
        }
        return {
            "widget": "voice_chat",
            "mode": "embedded",
            "source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "topic_id": topic_id,
            "topic": {k: v for k, v in topic.items() if k != "stored"},
            "owner": BUILDER_OWNER,
            "default_tool": f"{BUILDER_SKILL_ID}.chat",
            "meta": send_meta,
            "allow_voice": True,
            "allow_text": True,
        }

    def list_development_skills(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        drafts: list[dict[str, Any]] = []
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        if drafts_root.exists():
            for path in sorted(drafts_root.glob("*/builder.draft.json")):
                draft = _read_json(path)
                artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
                metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
                links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
                conversation = links.get("conversation") if isinstance(links.get("conversation"), dict) else {}
                draft_webspace = str(metadata.get("webspace_id") or conversation.get("webspace_id") or source_id).strip()
                if draft_webspace and draft_webspace != source_id:
                    continue
                drafts.append(
                    {
                        "draft_id": draft.get("draft_id"),
                        "status": draft.get("status"),
                        "kind": artifact.get("kind"),
                        "id": artifact.get("id"),
                        "root": artifact.get("draft_root") or artifact.get("root"),
                        "source_idea": metadata.get("source_idea"),
                        "active": draft.get("draft_id") == binding.get("active_draft_id"),
                        "updated_at": draft.get("updated_at") or draft.get("created_at"),
                    }
                )
        return {"ok": True, "source_webspace_id": source_id, "active_draft_id": binding.get("active_draft_id"), "items": drafts}

    def delete_development_skill(self, draft_id: str, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        token = str(draft_id or "").strip()
        if not token:
            return {"ok": False, "error": "draft_id_required"}
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        draft_dir = (drafts_root / token).resolve()
        root = drafts_root.resolve()
        if root not in draft_dir.parents or not draft_dir.exists():
            return {"ok": False, "error": "draft_not_found", "draft_id": token}
        draft = _read_json(draft_dir / "builder.draft.json")
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        raw_artifact_root = str(artifact.get("draft_root") or artifact.get("root") or "").strip()
        if not raw_artifact_root:
            return {"ok": False, "error": "artifact_root_missing", "draft_id": token}

        candidate = Path(raw_artifact_root).expanduser()
        artifact_root = (candidate if candidate.is_absolute() else draft_dir / candidate).resolve()
        artifact_id = str(artifact.get("id") or "").strip()
        artifact_kind = str(artifact.get("kind") or "").strip()
        expected_parent = {"scenario": "scenarios", "skill": "skills"}.get(artifact_kind)
        if (
            not artifact_id
            or expected_parent is None
            or artifact_root.name != artifact_id
            or artifact_root.parent.name != expected_parent
            or artifact_root == root
            or root in artifact_root.parents
            or artifact_root in root.parents
        ):
            return {
                "ok": False,
                "error": "unsafe_artifact_root",
                "draft_id": token,
                "artifact_root": str(artifact_root),
            }

        archive_root = Path(self.state_dir or current_state_dir()) / "builder" / "archive"
        archive_dir = archive_root / f"{int(_now() * 1000)}-{_safe_path_token(token)}"
        archive_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copytree(draft_dir, archive_dir / "draft")
            artifact_exists = artifact_root.exists()
            if artifact_exists:
                shutil.copytree(artifact_root, archive_dir / "artifact")
            _write_json(
                archive_dir / "archive.json",
                {
                    "schema": "adaos.builder.archive.v1",
                    "draft_id": token,
                    "source_webspace_id": source_id,
                    "artifact": dict(artifact),
                    "original_draft_root": str(draft_dir),
                    "original_artifact_root": str(artifact_root),
                    "artifact_existed": artifact_exists,
                    "archived_at": _now(),
                },
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": "draft_archive_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "draft_id": token,
                "archive_root": str(archive_dir),
            }

        removed_artifact = False
        try:
            if artifact_root.exists():
                shutil.rmtree(artifact_root)
                removed_artifact = True
            shutil.rmtree(draft_dir)
        except Exception as exc:
            return {
                "ok": False,
                "error": "draft_delete_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "draft_id": token,
                "archive_root": str(archive_dir),
                "removed_artifact": removed_artifact,
            }
        binding = self.get_workspace_binding(source_id)
        if binding.get("active_draft_id") == token:
            self.set_active_draft(source_webspace_id=source_id, active_draft_id=None, persist_projection=False)
        return {
            "ok": True,
            "draft_id": token,
            "removed_artifact": removed_artifact,
            "archive_root": str(archive_dir),
        }

    def context_inspector(
        self,
        source_webspace_id: str | None = None,
        *,
        run_ref: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        from adaos.services.context_control import ContextControlService

        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        selection = dict(binding.get("selection") or {})
        object_type = str(selection.get("object_type") or "").strip().lower()
        object_id = str(selection.get("object_id") or "").strip()
        ticket_context = (
            dict(binding.get("development_ticket"))
            if isinstance(binding.get("development_ticket"), Mapping)
            else {}
        )
        ticket_target = (
            dict(ticket_context.get("target_scope"))
            if isinstance(ticket_context.get("target_scope"), Mapping)
            else {}
        )
        target_type = str(ticket_target.get("type") or "").strip().lower().rstrip("s")
        target_id = str(ticket_target.get("id") or ticket_target.get("name") or "").strip()
        ticket_matches_selection = bool(
            object_id
            and target_id == object_id
            and (not target_type or target_type == object_type.rstrip("s"))
        )
        ticket_project_ref = str(ticket_target.get("project_ref") or "").strip()
        ticket_project_id = str(ticket_target.get("project_id") or "").strip()
        if ticket_project_ref and not ticket_project_ref.startswith("project:"):
            ticket_project_ref = ""
        project_ref = (
            ticket_project_ref
            if ticket_matches_selection and ticket_project_ref
            else f"project:{ticket_project_id}"
            if ticket_matches_selection and ticket_project_id
            else f"project:{object_id}"
            if object_id
            else None
        )
        ticket_component_ref = str(ticket_context.get("component_ref") or "").strip()
        component_ref = (
            ticket_component_ref
            if ticket_matches_selection and ticket_component_ref
            else f"{object_type}:{object_id}"
            if object_type and object_id
            else None
        )
        context = ContextControlService(state_dir=self.state_dir)
        selected_run = str(run_ref or "").strip()
        plans = context.list_plans(limit=max(20, min(int(limit) * 5, 500)))

        def applies(plan: Mapping[str, Any]) -> bool:
            if selected_run and selected_run in (plan.get("subject_refs") or []):
                return True
            refs = {str(item) for item in plan.get("subject_refs") or []}
            if project_ref and project_ref in refs:
                return True
            for item in plan.get("selected") or []:
                if not isinstance(item, Mapping):
                    continue
                subject_refs = {str(ref) for ref in item.get("subject_refs") or []}
                if project_ref and project_ref in subject_refs:
                    return True
                if component_ref and component_ref in subject_refs:
                    return True
            return False

        matched_plans = [dict(item) for item in plans if applies(item)][: max(1, min(int(limit), 100))]
        plan_refs = {str(item.get("plan_ref") or "") for item in matched_plans}
        if selected_run:
            receipt_candidates = context.list_receipts(
                run_ref=selected_run,
                limit=max(50, min(int(limit) * 10, 1000)),
            )
        elif plan_refs:
            receipt_candidates = context.list_receipts(
                limit=max(50, min(int(limit) * 10, 1000)),
            )
        else:
            receipt_candidates = []
        receipts = [
            dict(item)
            for item in receipt_candidates
            if not plan_refs or str(item.get("plan_ref") or "") in plan_refs
        ]

        usage_by_route: dict[str, dict[str, int]] = {}
        for receipt in receipts:
            route = str(receipt.get("execution_route") or "unknown").strip() or "unknown"
            usage = dict(receipt.get("usage") or {})
            aggregate = usage_by_route.setdefault(
                route,
                {
                    "provider_input_tokens": 0,
                    "cached_input_tokens": 0,
                    "fresh_input_tokens": 0,
                    "output_tokens": 0,
                    "fresh_plus_output": 0,
                },
            )
            for key in aggregate:
                aggregate[key] += int(usage.get(key) or 0)

        plan_rows = [
            {
                "plan_id": plan.get("plan_id"),
                "plan_ref": plan.get("plan_ref"),
                "subject_refs": list(plan.get("subject_refs") or []),
                "purpose": plan.get("purpose"),
                "audience": plan.get("audience"),
                "status": plan.get("status"),
                "estimated_tokens": int(plan.get("estimated_tokens") or 0),
                "token_budget": int(plan.get("token_budget") or 0),
                "selected": [
                    {
                        key: item.get(key)
                        for key in ("ref", "kind", "digest", "trust_class", "tainted", "estimated_tokens", "selection_reason")
                    }
                    for item in plan.get("selected") or []
                    if isinstance(item, Mapping)
                ],
                "omitted": list(plan.get("omitted") or []),
                "denied": list(plan.get("denied") or []),
                "unavailable": list(plan.get("unavailable") or []),
                "created_at": plan.get("created_at"),
            }
            for plan in matched_plans
        ]
        return {
            "schema": "adaos.builder.context_inspector.v1",
            "source_webspace_id": source_id,
            "scope": {
                "project_ref": project_ref,
                "component_ref": component_ref,
                "run_ref": selected_run or None,
            },
            "summary": {
                "plan_count": len(plan_rows),
                "receipt_count": len(receipts),
                "selected_units": sum(len(item["selected"]) for item in plan_rows),
                "denied_units": sum(len(item["denied"]) for item in plan_rows),
                "estimated_context_tokens": sum(int(item["estimated_tokens"]) for item in plan_rows),
            },
            "usage_by_route": usage_by_route,
            "plans": plan_rows,
            "receipts": receipts,
            "privacy": {
                "sealed_content_disclosed": False,
                "denied_units_are_metadata_only": True,
            },
            "i18n": {
                "title": {"en": "Context Inspector", "ru": "Инспектор контекста"},
                "empty": {"en": "No context runs for this project yet.", "ru": "Для этого проекта еще нет запусков с контекстом."},
            },
            "updated_at": _now(),
        }

    def snapshot(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        snapshot = {
            "schema": "adaos.builder.workbench.v1",
            "source_webspace_id": source_id,
            "binding": binding,
            "selection": dict(binding.get("selection") or {}),
            "dialog": binding.get("dialog") if isinstance(binding.get("dialog"), dict) else self.dialog_widget_config(source_id),
            "development_skills": self.list_development_skills(source_id).get("items", []),
            "preview_runtime": self.reconciler.describe(source_id),
            "preview_state": dict(preview_state or {}),
            "context_inspector": self.context_inspector(source_id),
            "updated_at": _now(),
        }
        _write_json(self.snapshot_path(source_id), snapshot)
        return snapshot

    def runtime_projection(
        self,
        source_webspace_id: str | None = None,
        *,
        preview_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        return {
            "schema": "adaos.builder.runtime_projection.v1",
            "source_webspace_id": source_id,
            "selection": dict(binding.get("selection") or {}),
            "binding": {
                key: binding.get(key)
                for key in (
                    "source_webspace_id",
                    "preview_webspace_id",
                    "runtime_scenario_id",
                    "active_draft_id",
                    "purpose",
                )
            },
            "preview_runtime": _preview_runtime_projection(self.reconciler.describe(source_id)),
            "preview_state": _preview_state_projection(preview_state),
            "context_inspector": self.context_inspector(source_id),
            "updated_at": _now(),
        }

    async def publish_projection(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        snapshot = self.runtime_projection(source_id, preview_state=preview_state)
        published: list[str] = []
        try:
            from adaos.services.yjs.doc import async_get_ydoc

            async with async_get_ydoc(source_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
                data = ydoc.get_map("data")
                with ydoc.begin_transaction() as txn:
                    data.set(txn, "builder", snapshot)
            published.append(source_id)
        except Exception:
            pass
        return {"ok": True, "snapshot": snapshot, "published_webspaces": published}

    def publish_projection_sync(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.publish_projection(source_webspace_id, preview_state=preview_state))
        snapshot = self.runtime_projection(source_webspace_id, preview_state=preview_state)
        return {"ok": True, "snapshot": snapshot, "published_webspaces": [], "deferred": True}


def _payload_from_event(evt: Any) -> dict[str, Any]:
    if isinstance(evt, Mapping):
        payload = evt.get("payload") if isinstance(evt.get("payload"), Mapping) else evt
        return dict(payload)
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def _schedule_projection_publish(
    service: BuilderWorkbenchService,
    source_webspace_id: str,
    *,
    preview_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_id = service.resolve_source_webspace_id(source_webspace_id)
    _PROJECTION_PENDING[source_id] = {
        "preview_state": dict(preview_state or {}),
    }
    current = _PROJECTION_TASKS.get(source_id)
    if current is not None and not current.done():
        return {"scheduled": True, "coalesced": True, "source_webspace_id": source_id}

    async def _runner() -> None:
        try:
            while True:
                request = _PROJECTION_PENDING.pop(source_id, None)
                if request is None:
                    break
                try:
                    await service.publish_projection(
                        source_id,
                        preview_state=request.get("preview_state"),
                    )
                except Exception:
                    _log.warning(
                        "failed to publish deferred Builder projection source_webspace=%s",
                        source_id,
                        exc_info=True,
                    )
                if source_id not in _PROJECTION_PENDING:
                    break
        finally:
            if _PROJECTION_TASKS.get(source_id) is asyncio.current_task():
                _PROJECTION_TASKS.pop(source_id, None)

    task = asyncio.create_task(
        _runner(),
        name=f"builder-projection:{source_id}"[:120],
    )
    _PROJECTION_TASKS[source_id] = task
    return {
        "scheduled": True,
        "coalesced": False,
        "source_webspace_id": source_id,
        "task": task.get_name(),
    }


@subscribe(BUILDER_CONTEXT_SELECTED)
async def _on_builder_context_selected(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip()
    object_type = str(payload.get("object_type") or payload.get("project_kind") or "").strip()
    object_id = str(payload.get("object_id") or payload.get("project_id") or "").strip()
    if not source_webspace_id or not object_type or not object_id:
        return
    service = BuilderWorkbenchService()
    service.set_selected_project(
        source_webspace_id=source_webspace_id,
        object_type=object_type,
        object_id=object_id,
        title=str(payload.get("title") or "").strip() or None,
        description=str(payload.get("description") or "").strip() or None,
        persist_projection=False,
    )
    _schedule_projection_publish(service, source_webspace_id)


@subscribe("desktop.webspace.reloaded")
async def _on_builder_source_webspace_reloaded(evt: Any) -> None:
    """Restore dynamic Builder context after scenario materialization.

    A governed webspace reset rebuilds scenario-owned branches before emitting
    ``desktop.webspace.reloaded``.  Builder selection is runtime-owned state,
    so the durable workbench binding must be projected once the rebuild has
    completed instead of allowing the scenario seed to become authoritative.
    """

    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("webspace_id") or "").strip()
    scenario_id = str(
        payload.get("scenario_id")
        or payload.get("current_scenario")
        or payload.get("materialized_scenario")
        or ""
    ).strip()
    if not source_webspace_id or scenario_id not in {
        BUILDER_HOST_SCENARIO_ID,
        BUILDER_WORKBENCH_SCENARIO_ID,
    }:
        return
    service = BuilderWorkbenchService()
    # A Builder opened inside another Builder's preview is a bounded self-host.
    # Claim that topology as soon as scenario materialization tells us the
    # authoritative scenario id. UI actions do not always carry scenario_id in
    # their tool metadata, so deferring the claim until the first action can
    # incorrectly resolve ``dev1-dev`` back to ``dev1`` and overwrite the
    # Builder itself instead of allocating ``dev1-dev-dev`` for its project.
    source_webspace_id = service.resolve_action_source_webspace_id(
        source_webspace_id,
        current_scenario_id=scenario_id,
    )
    if not service.binding_path(source_webspace_id).is_file():
        return
    _schedule_projection_publish(service, source_webspace_id)


@subscribe("builder.workbench.ensure_requested")
async def _on_builder_workbench_ensure_requested(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip() or None
    if not source_webspace_id:
        return
    await BuilderWorkbenchService().ensure_dev_webspace(
        source_webspace_id,
        active_draft_id=str(payload.get("active_draft_id") or "").strip() or None,
        runtime_scenario_id=str(payload.get("runtime_scenario_id") or "").strip() or None,
        preview_state=payload.get("preview_state") if isinstance(payload.get("preview_state"), Mapping) else None,
    )


@subscribe(BUILDER_PREVIEW_DESIRED)
@subscribe("builder.preview.selected")
async def _on_builder_preview_selected(evt: Any) -> None:
    payload = _payload_from_event(evt)
    if bool(payload.get("reconciled")):
        return
    scenario_id = str(payload.get("scenario_id") or payload.get("object_id") or "").strip()
    object_type = str(payload.get("object_type") or "scenario").strip().lower()
    if object_type != "scenario" or not scenario_id:
        return
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip() or None
    service = BuilderWorkbenchService()
    reason = str(payload.get("reason") or "").strip()
    if reason in {"builder_project_created", "builder_project_switched"}:
        binding = service.get_workspace_binding(source_webspace_id)
        desired_scenario = str(binding.get("runtime_scenario_id") or "").strip()
        if desired_scenario and desired_scenario != scenario_id:
            _log.info(
                "builder preview selection superseded source_webspace=%s scenario=%s desired_scenario=%s reason=%s",
                source_webspace_id,
                scenario_id,
                desired_scenario,
                reason,
            )
            return
    await service.ensure_dev_webspace(
        source_webspace_id,
        active_draft_id=str(payload.get("draft_id") or "").strip() or None,
        runtime_scenario_id=scenario_id,
        preview_state=payload.get("preview_state") if isinstance(payload.get("preview_state"), Mapping) else None,
        wait_for_rebuild=bool(payload.get("wait_for_rebuild", False)),
    )


@subscribe(BUILDER_PREVIEW_OBSERVED)
async def _on_builder_preview_observed(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or "").strip()
    operation_id = str(payload.get("operation_id") or "").strip()
    if not source_webspace_id or not operation_id:
        return
    service = BuilderWorkbenchService()
    current = service.reconciler.describe(source_webspace_id)
    if (
        str(current.get("operation_id") or "").strip() != operation_id
        or str(current.get("status") or "").strip() != "ready"
    ):
        return
    _schedule_projection_publish(service, source_webspace_id)


@subscribe(BUILDER_PREVIEW_TRANSITIONED)
async def _on_builder_preview_transitioned(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or "").strip()
    if not source_webspace_id:
        return
    service = BuilderWorkbenchService()
    current = service.reconciler.describe(source_webspace_id)
    if int(current.get("generation") or 0) != int(payload.get("generation") or 0):
        return
    _schedule_projection_publish(service, source_webspace_id)


@subscribe("desktop.webspace.reloaded")
async def _on_builder_preview_webspace_reloaded(evt: Any) -> None:
    """Project runtime truth back to the owning Builder after reload/reconnect."""

    payload = _payload_from_event(evt)
    preview_webspace_id = str(payload.get("webspace_id") or "").strip()
    observed_scenario = str(
        payload.get("scenario_id")
        or payload.get("current_scenario")
        or payload.get("materialized_scenario")
        or ""
    ).strip()
    if not preview_webspace_id or not observed_scenario:
        return
    service = BuilderWorkbenchService()
    incoming = service.relationships.get_incoming(preview_webspace_id)
    if incoming is None or incoming.purpose != BUILDER_PROJECT_PREVIEW:
        return
    service.reconciler.observe(
        source_webspace_id=incoming.source_webspace_id,
        preview_webspace_id=preview_webspace_id,
        observed_scenario=observed_scenario,
        observed_version=str(payload.get("version") or payload.get("revision") or "").strip() or None,
        reason=str(payload.get("reason") or payload.get("action") or "webspace_reloaded"),
    )
