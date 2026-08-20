from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.domain.artifact_release import (
    ArtifactReleaseContractError,
    WorkspaceLock,
    canonical_payload_digest,
)
from adaos.services.agent_context import get_ctx
from adaos.services.builder.action_contracts import build_builder_action
from adaos.services.builder.activity_executors import (
    builder_lifecycle_executor_registrations,
)
from adaos.services.builder.governed import (
    admit_legacy_transition,
    compiled_builder_change_definition,
    governed_instance,
    legacy_action_for_command,
    workflow_description,
)
from adaos.services.builder.data_modes import (
    BuilderDataModeError,
    implementation_mapping_report,
    normalize_binding_state,
    put_profile,
    select_profile,
)
from adaos.services.builder.project_aggregate import (
    BuilderProjectError,
    begin_mutation,
    capture_compatibility_record,
    finish_mutation,
    normalize_portfolio,
    normalize_project,
    rebase_change as rebase_project_change,
    restore_compatibility_record,
    set_dependencies,
    set_focus,
)
from adaos.services.builder.placement import (
    BuilderPlacementError,
    active_project_placement,
    normalize_project_placement,
)
from adaos.services.conversation_interactions import create_interaction
from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    compile_definition,
    migrate_workflow_instance,
    validate_workflow_record,
    verified_workflow_principal,
    workflow_definition_digest,
)
from adaos.services.builder.release_evidence import applied_release_record
from adaos.services.builder.surface import (
    builder_action_label,
    builder_action_label_ref,
    builder_input_prompt,
    builder_surface_locale_context,
    localize_builder_explanation,
    normalize_builder_locale,
)
from adaos.services.builder.specification import specification_projection
from adaos.services.runtime_paths import current_state_dir
from adaos.services.workflow_artifacts import (
    WorkflowArtifactError,
    canonical_workflow_bytes,
    load_manifest_bound_workflow,
    validate_workflow_definition_report,
)
from adaos.services.workflow_authoring import (
    default_workflow_role_policy,
    workflow_abi_schema_records,
)
from adaos.services.workflow_registry import (
    WorkflowAdapterRegistryError,
    platform_workflow_adapter_registry,
)
from adaos.services.workflow_metrics import (
    workflow_metrics_evidence,
    workflow_metrics_report,
)
from adaos.services.workflow_static_reports import workflow_static_report
from adaos.services.workflow_execution import (
    WorkflowExecutorRegistry,
    description_with_executor_readiness,
    prepare_interaction_invocation,
    prepare_sdk_invocation,
)
from adaos.services.scenario.workflow_translation import (
    LegacyWorkflowTranslationError,
    shadow_compare_legacy_workflow,
    translate_legacy_scenario_workflow,
)


BUILDER_WORKFLOW_SCHEMA = "adaos.builder.workflow.v1"
BUILDER_CHANGE_SET_SCHEMA = "adaos.builder.change_set.v1"
BUILDER_CHANGE_SCHEMA = "adaos.builder.change.v1"
BUILDER_RUN_SCHEMA = "adaos.builder.run.v1"
BUILDER_PACKAGE_CUTOVER_ENV = "ADAOS_BUILDER_REQUIRE_ACTIVE_PACKAGE"


def _feature_flag(value: bool | None, *, env_name: str) -> bool:
    if value is not None:
        return bool(value)
    token = str(os.getenv(env_name) or "").strip().lower()
    if token in {"", "0", "false", "no", "off"}:
        return False
    if token in {"1", "true", "yes", "on"}:
        return True
    raise BuilderWorkflowError(f"{env_name} must be a boolean feature flag")


BUILDER_CONTEXT_PACKET_SCHEMA = "adaos.builder.context_packet.v1"
BUILDER_INTERACTION_FRAME_SCHEMA = "adaos.builder.interaction_frame.v1"
BUILDER_WORKFLOW_EVENT = "builder.workflow.changed"
_LOCK = threading.RLock()
_MAX_STATE_BYTES = 512 * 1024
_MAX_HISTORY = 50
_MAX_CHANGE_ISSUES = 50
_MAX_CHANGE_RUNS = 100
_MAX_ACCEPTANCE_CONSTRAINTS = 100
_CHANGE_SET_TERMINAL_STATES = {"published", "rejected", "superseded"}
_ISSUE_STATES = {"open", "in_progress", "resolved", "deferred"}
_ISSUE_LANES = {"prototype", "automation"}
_PROJECT_MUTATION_START_ACTIONS = {
    "automation_started",
    "handoff_to_automation",
    "automation_iteration_started",
    "request_return_to_prototype",
}
_PROJECT_MUTATION_FINISH_ACTIONS = {
    "automation_completed": (False, True),
    "automation_failed": (False, False),
    "return_to_prototype": (False, True),
    "return_to_prototype_failed": (False, False),
}
_PROJECT_ATOMIC_MUTATION_ACTIONS = {"prototype_revision_recorded", "adopt_experiment"}
_BUILDER_WORKFLOW_GUARDS = frozenset({"always"})
_BUILDER_WORKFLOW_ACTIVITIES = frozenset(
    {
        "builder.codex.run",
        "builder.prototype.derive",
        "builder.publication.publish",
        "builder.trial.activate",
    }
)


class BuilderWorkflowError(ValueError):
    """Raised when a Builder lifecycle transition is not permitted."""


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=32)
def _inspect_workflow_definition(raw: bytes, source: str) -> dict[str, Any]:
    validation = validate_workflow_definition_report(raw)
    inspection: dict[str, Any] = {
        "schema": "adaos.workflow.inspection.v1",
        "source": source,
        "status": "invalid",
        "validation": copy.deepcopy(validation.report),
        "binding": None,
    }
    if validation.compiled is None:
        return inspection
    try:
        binding = platform_workflow_adapter_registry().bind(validation.compiled)
    except WorkflowAdapterRegistryError as exc:
        inspection["status"] = "binding_rejected"
        inspection["validation"]["diagnostics"].append(
            {
                "code": "workflow.registry.binding_rejected",
                "severity": "error",
                "path": "$.transitions",
                "message": str(exc),
            }
        )
    else:
        inspection["status"] = "admitted"
        inspection["binding"] = binding
    return inspection


@lru_cache(maxsize=16)
def _load_builder_skill_definition(
    skill_root: str,
    _manifest_signature: tuple[int, int],
    _definition_signature: tuple[int, int],
) -> CompiledWorkflowDefinition:
    artifact = load_manifest_bound_workflow(
        Path(skill_root),
        manifest_name="skill.yaml",
        registered_guards=set(_BUILDER_WORKFLOW_GUARDS),
        registered_activities=set(_BUILDER_WORKFLOW_ACTIVITIES),
        allow_legacy_inline=False,
    )
    if artifact is None:
        raise WorkflowArtifactError("builder_skill must reference workflow.json")
    if artifact.compiled.workflow_type != "builder.change":
        raise WorkflowArtifactError("builder_skill workflow_type must be builder.change")
    platform_workflow_adapter_registry().bind(artifact.compiled)
    return artifact.compiled


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _replace_path(source: Path, target: Path) -> None:
    """Retry a bounded atomic replace when Windows briefly locks the target."""

    for attempt in range(6):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt >= 5:
                raise
            time.sleep(0.01 * (2**attempt))


def _kind(value: Any) -> str:
    token = str(value or "").strip().lower().rstrip("s")
    if token not in {"scenario", "skill"}:
        raise BuilderWorkflowError("object_type must be scenario or skill")
    return token


def _project_id(value: Any) -> str:
    token = str(value or "").strip()
    if not token or token in {".", ".."} or any(char in token for char in ("/", "\\", "\0")):
        raise BuilderWorkflowError("object_id is required and must be a project id")
    return token


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_text(value: Any, *, field: str, max_length: int) -> str:
    token = " ".join(str(value or "").split())
    if not token:
        raise BuilderWorkflowError(f"{field} is required")
    if len(token) > max_length:
        raise BuilderWorkflowError(f"{field} exceeds {max_length} characters")
    return token


def _reject_transport_corruption(value: Any, *, field: str) -> None:
    """Reject new text whose original Unicode code points were already lost."""

    values: list[Any]
    if isinstance(value, Mapping):
        values = list(value.values())
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        token = str(value or "")
        if "\ufffd" in token or "????" in token:
            raise BuilderWorkflowError(
                f"{field} appears transport-corrupted; submit the original text as UTF-8"
            )
        return
    for item in values:
        _reject_transport_corruption(item, field=field)


def _normalize_issue(value: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError("change set issues must be objects")
    issue_id = str(value.get("issue_id") or value.get("id") or f"I{index:03d}").strip()
    if not issue_id or len(issue_id) > 80:
        raise BuilderWorkflowError("change set issue_id is required and must be at most 80 characters")
    title = _bounded_text(value.get("title") or value.get("summary"), field="change set issue title", max_length=240)
    lane = str(value.get("lane") or value.get("target_phase") or "").strip().lower()
    if lane not in _ISSUE_LANES:
        raise BuilderWorkflowError("change set issue lane must be prototype or automation")
    status = str(value.get("status") or "open").strip().lower()
    if status not in _ISSUE_STATES:
        raise BuilderWorkflowError(
            "change set issue status must be open, in_progress, resolved, or deferred"
        )
    raw_criteria = value.get("acceptance_criteria") or value.get("acceptance") or []
    if isinstance(raw_criteria, str):
        raw_criteria = [raw_criteria]
    if not isinstance(raw_criteria, (list, tuple)):
        raise BuilderWorkflowError("change set issue acceptance_criteria must be a list")
    criteria = [
        _bounded_text(item, field="acceptance criterion", max_length=500)
        for item in raw_criteria[:20]
    ]
    if not criteria:
        raise BuilderWorkflowError("every change set issue requires acceptance_criteria")
    structural_status = str(value.get("structural_status") or "active").strip().lower()
    if structural_status not in {"active", "split", "merged"}:
        raise BuilderWorkflowError("issue structural_status must be active, split, or merged")
    return {
        "issue_id": issue_id,
        "title": title,
        "lane": lane,
        "status": status,
        "acceptance_criteria": criteria,
        "priority": str(value.get("priority") or "").strip() or None,
        "confidence": (
            max(0.0, min(1.0, float(value.get("confidence"))))
            if value.get("confidence") is not None
            else None
        ),
        "semantic_refs": list(
            dict.fromkeys(
                str(item).strip()
                for item in value.get("semantic_refs") or []
                if str(item).strip()
            )
        )[:100],
        "structural_status": structural_status,
        "derived_from_issue_ids": list(
            dict.fromkeys(
                str(item).strip()
                for item in value.get("derived_from_issue_ids") or []
                if str(item).strip()
            )
        )[:50],
        "superseded_by_issue_ids": list(
            dict.fromkeys(
                str(item).strip()
                for item in value.get("superseded_by_issue_ids") or []
                if str(item).strip()
            )
        )[:50],
    }


def _normalize_change_set(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or not str(value.get("change_set_id") or "").strip():
        return None
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value.get("issues") or [], start=1):
        issue = _normalize_issue(item, index=index)
        issue_id = issue["issue_id"]
        if issue_id in seen:
            raise BuilderWorkflowError(f"duplicate change set issue_id: {issue_id}")
        seen.add(issue_id)
        issues.append(issue)
    route = str(value.get("route") or "").strip().lower()
    if route not in {"prototype_first", "automation_direct"}:
        route = "prototype_first" if any(item["lane"] == "prototype" for item in issues) else "automation_direct"
    gate = str(value.get("gate") or ("prototype" if route == "prototype_first" else "automation")).strip().lower()
    if gate not in {"prototype", "automation", "trial", "publication", "complete"}:
        gate = "prototype" if route == "prototype_first" else "automation"
    status = str(value.get("status") or "planned").strip().lower()
    member_change_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in value.get("member_change_ids") or []
            if str(item).strip()
        )
    )[-100:]
    change_set_id = str(value.get("change_set_id") or "").strip()
    if change_set_id not in member_change_ids:
        member_change_ids.insert(0, change_set_id)
    return {
        "schema": BUILDER_CHANGE_SET_SCHEMA,
        "change_set_id": change_set_id,
        "request": _bounded_text(value.get("request"), field="change set request", max_length=4000),
        "request_addenda": [
            _bounded_text(item, field="change set request addendum", max_length=4000)
            for item in value.get("request_addenda") or []
        ][-50:],
        "route": route,
        "gate": gate,
        "status": status,
        "issues": issues,
        "member_change_ids": member_change_ids,
        "source_message_ids": [
            str(item).strip()
            for item in value.get("source_message_ids") or []
            if str(item).strip()
        ][-100:],
        "created_at": str(value.get("created_at") or "").strip() or None,
        "updated_at": str(value.get("updated_at") or "").strip() or None,
        "supersedes_change_set_id": str(
            value.get("supersedes_change_set_id") or value.get("supersedes_change_id") or ""
        ).strip()
        or None,
    }


def _normalize_run(value: Any, *, change_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError("change runs must be objects")
    run_id = str(value.get("run_id") or value.get("id") or "").strip()
    if not run_id or len(run_id) > 160:
        raise BuilderWorkflowError("run_id is required and must be at most 160 characters")
    linked_change_id = str(value.get("change_id") or change_id).strip()
    if linked_change_id != change_id:
        raise BuilderWorkflowError("run change_id does not match its Change")
    status = str(value.get("status") or "succeeded").strip().lower()
    if status not in {"queued", "running", "succeeded", "failed", "cancelled", "superseded"}:
        raise BuilderWorkflowError("invalid Builder Run status")
    purpose = str(value.get("purpose") or "iteration").strip().lower()
    if purpose not in {"iteration", "experiment", "evaluation", "recovery"}:
        raise BuilderWorkflowError("invalid Builder Run purpose")
    adoption_status = str(
        value.get("adoption_status")
        or ("pending" if purpose == "experiment" else "not_applicable")
    ).strip().lower()
    if adoption_status not in {"not_applicable", "pending", "adopted", "discarded"}:
        raise BuilderWorkflowError("invalid Builder Run adoption status")
    if purpose != "experiment" and adoption_status != "not_applicable":
        raise BuilderWorkflowError("only Experiment Runs have adoption state")
    workflow_metrics = value.get("workflow_metrics")
    if workflow_metrics is not None and not isinstance(workflow_metrics, Mapping):
        raise BuilderWorkflowError("Builder Run workflow_metrics must be an object")
    if isinstance(workflow_metrics, Mapping):
        try:
            validate_workflow_record(
                "adaos.workflow.metrics_evidence.v1",
                workflow_metrics,
            )
        except ValueError as exc:
            raise BuilderWorkflowError(f"invalid Builder Run workflow_metrics: {exc}") from exc
    return {
        "schema": BUILDER_RUN_SCHEMA,
        "run_id": run_id,
        "change_id": change_id,
        "activity": str(value.get("activity") or "workflow").strip() or "workflow",
        "executor": str(value.get("executor") or "builder.workflow").strip() or "builder.workflow",
        "purpose": purpose,
        "adoption_status": adoption_status,
        "status": status,
        "context_packet_digest": str(value.get("context_packet_digest") or "").strip() or None,
        "environment_ref": str(value.get("environment_ref") or "").strip() or None,
        "input_refs": [str(item).strip() for item in value.get("input_refs") or [] if str(item).strip()][-100:],
        "output_refs": [str(item).strip() for item in value.get("output_refs") or [] if str(item).strip()][-100:],
        "evidence_refs": [str(item).strip() for item in value.get("evidence_refs") or [] if str(item).strip()][-100:],
        "workflow_metrics": copy.deepcopy(dict(workflow_metrics or {})) or None,
        "started_at": str(value.get("started_at") or "").strip() or None,
        "completed_at": str(value.get("completed_at") or "").strip() or None,
        "error": str(value.get("error") or "").strip() or None,
    }


def _normalize_acceptance_constraint(value: Any, *, change_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError("acceptance constraints must be objects")
    constraint_id = str(value.get("constraint_id") or "").strip()
    if not constraint_id or len(constraint_id) > 160:
        raise BuilderWorkflowError("acceptance constraint_id is required and must be at most 160 characters")
    linked_change_id = str(value.get("change_id") or "").strip()
    if linked_change_id != change_id:
        raise BuilderWorkflowError("acceptance constraint change_id does not match its Change")
    review_id = str(value.get("review_id") or "").strip()
    project_ref = str(value.get("project_ref") or "").strip()
    artifact_ref = str(value.get("artifact_ref") or "").strip()
    target_ref = str(value.get("target_ref") or "").strip()
    kind = str(value.get("kind") or "").strip().lower()
    status = str(value.get("status") or "active").strip().lower()
    source_revision = str(value.get("source_revision") or "").strip()
    created_at = str(value.get("created_at") or "").strip()
    if not review_id or len(review_id) > 160:
        raise BuilderWorkflowError("acceptance constraint review_id is required")
    if not project_ref.startswith("scenario:") or len(project_ref) > 300:
        raise BuilderWorkflowError("acceptance constraint project_ref must identify a scenario")
    if not artifact_ref or len(artifact_ref) > 300:
        raise BuilderWorkflowError("acceptance constraint artifact_ref is required")
    if not target_ref.startswith(("widget:", "field:")) or len(target_ref) > 300:
        raise BuilderWorkflowError("acceptance constraint target_ref must identify a widget or field")
    if kind not in {"present", "label_equals", "property_equals", "visible", "order_before", "data_mode"}:
        raise BuilderWorkflowError("invalid acceptance constraint kind")
    if status not in {"active", "satisfied", "violated", "unverifiable", "superseded"}:
        raise BuilderWorkflowError("invalid acceptance constraint status")
    if not source_revision or len(source_revision) > 80 or not created_at:
        raise BuilderWorkflowError("acceptance constraint source revision and created_at are required")
    return {
        "schema": "adaos.builder.acceptance_constraint.v1",
        "constraint_id": constraint_id,
        "change_id": change_id,
        "review_id": review_id,
        "project_ref": project_ref,
        "artifact_ref": artifact_ref,
        "target_ref": target_ref,
        "kind": kind,
        "expected": copy.deepcopy(value.get("expected")),
        "source_revision": source_revision,
        "status": status,
        "last_evaluation": copy.deepcopy(value.get("last_evaluation")) if isinstance(value.get("last_evaluation"), Mapping) else None,
        "created_at": created_at,
        "updated_at": str(value.get("updated_at") or "").strip() or None,
        "superseded_reason": str(value.get("superseded_reason") or "").strip() or None,
        "superseded_by_ref": str(value.get("superseded_by_ref") or "").strip() or None,
    }


def _normalize_change(value: Any) -> dict[str, Any] | None:
    legacy = _normalize_change_set(value)
    if legacy is None:
        return None
    change_id = str(value.get("change_id") or legacy["change_set_id"]).strip()
    if change_id != legacy["change_set_id"]:
        raise BuilderWorkflowError("change_id and change_set_id must identify the same Change")
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value.get("runs") or []:
        run = _normalize_run(item, change_id=change_id)
        if run["run_id"] in seen:
            raise BuilderWorkflowError(f"duplicate Builder Run id: {run['run_id']}")
        seen.add(run["run_id"])
        runs.append(run)
    constraints: list[dict[str, Any]] = []
    seen_constraints: set[str] = set()
    for item in value.get("acceptance_constraints") or []:
        constraint = _normalize_acceptance_constraint(item, change_id=change_id)
        constraint_id = constraint["constraint_id"]
        if constraint_id in seen_constraints:
            raise BuilderWorkflowError(f"duplicate acceptance constraint id: {constraint_id}")
        seen_constraints.add(constraint_id)
        constraints.append(constraint)
    if len(constraints) > _MAX_ACCEPTANCE_CONSTRAINTS:
        raise BuilderWorkflowError(
            f"a Change supports at most {_MAX_ACCEPTANCE_CONSTRAINTS} acceptance constraints"
        )
    return {
        **legacy,
        "schema": BUILDER_CHANGE_SCHEMA,
        "change_id": change_id,
        "change_set_id": change_id,
        "project_ref": str(value.get("project_ref") or "").strip() or None,
        "base_ref": copy.deepcopy(value.get("base_ref")) if isinstance(value.get("base_ref"), Mapping) else None,
        "base_generation": max(0, int(value.get("base_generation") or 0)),
        "affected_refs": list(
            dict.fromkeys(
                str(item).strip()
                for item in value.get("affected_refs") or []
                if str(item).strip()
            )
        )[:500],
        "runs": runs[-_MAX_CHANGE_RUNS:],
        "acceptance_constraints": constraints,
        "context_packet_digest": str(value.get("context_packet_digest") or "").strip() or None,
        "teacher_candidate_refs": [
            copy.deepcopy(dict(item))
            for item in value.get("teacher_candidate_refs") or []
            if isinstance(item, Mapping)
        ][-100:],
        "promotion_privacy_scope": str(value.get("promotion_privacy_scope") or "").strip() or None,
        "supersedes_change_id": str(
            value.get("supersedes_change_id") or value.get("supersedes_change_set_id") or ""
        ).strip()
        or None,
    }


def _change_set_compatibility(change: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(change, Mapping):
        return None
    value = copy.deepcopy(dict(change))
    value["schema"] = BUILDER_CHANGE_SET_SCHEMA
    value["change_set_id"] = str(value.get("change_id") or value.get("change_set_id") or "").strip()
    value.pop("change_id", None)
    value.pop("runs", None)
    value.pop("acceptance_constraints", None)
    value.pop("context_packet_digest", None)
    value.pop("teacher_candidate_refs", None)
    value.pop("promotion_privacy_scope", None)
    value.pop("project_ref", None)
    value.pop("base_ref", None)
    value.pop("base_generation", None)
    value.pop("affected_refs", None)
    value["supersedes_change_set_id"] = str(
        value.pop("supersedes_change_id", None) or value.get("supersedes_change_set_id") or ""
    ).strip() or None
    return value


def _stable_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _load_bounded_project_json(
    root: Path,
    artifact_ref: Any,
    *,
    label: str,
    max_bytes: int = 256 * 1024,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one manifest-declared JSON artifact without escaping the project."""

    token = str(artifact_ref or "").replace("\\", "/").strip().lstrip("/")
    if not token:
        raise BuilderWorkflowError(f"{label} artifact ref is required")
    project_root = root.resolve()
    candidate = (project_root / token).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise BuilderWorkflowError(f"{label} artifact must stay inside the project") from exc
    if not candidate.is_file():
        raise BuilderWorkflowError(f"{label} artifact is missing: {token}")
    raw = candidate.read_bytes()
    if len(raw) > max_bytes:
        raise BuilderWorkflowError(f"{label} artifact exceeds {max_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuilderWorkflowError(f"{label} artifact is not valid UTF-8 JSON: {token}") from exc
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError(f"{label} artifact must contain one JSON object")
    return dict(value), {
        "ref": token,
        "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "schema": str(value.get("schema") or "").strip() or None,
    }


def _bounded_ref(value: Any, *, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, str):
            token = item.strip()
            if token:
                result[key] = token[:500]
        elif isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, Mapping):
            nested = _bounded_ref(
                item,
                keys=(
                    "type",
                    "kind",
                    "id",
                    "message_id",
                    "segment_id",
                    "memory_id",
                    "conversation_id",
                    "thread_id",
                    "object_type",
                    "object_id",
                    "change_id",
                    "run_id",
                ),
            )
            if nested:
                result[key] = nested
    return result or None


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _finite_float(value: Any) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in {float("inf"), float("-inf")} else 0.0


def _bounded_conversation_context(value: Any) -> dict[str, Any] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, Mapping) or str(value.get("schema") or "").strip() != "adaos.context.packet.v1":
        raise BuilderWorkflowError("conversation_context must use adaos.context.packet.v1")

    messages: list[dict[str, Any]] = []
    for item in list(value.get("messages") or [])[-12:]:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "")[:1000]
        message = {
            "id": str(item.get("id") or "").strip()[:160],
            "seq": _nonnegative_int(item.get("seq")),
            "role": str(item.get("role") or "").strip()[:40],
            "text": text,
            "ts": _finite_float(item.get("ts")),
            "actor_id": str(item.get("actor_id") or "").strip()[:160] or None,
            "trust_boundary": "retrieved_untrusted_evidence",
            "source_ref": _bounded_ref(
                item.get("source_ref"),
                keys=("type", "kind", "conversation_id", "message_id", "seq"),
            ),
        }
        messages.append({key: nested for key, nested in message.items() if nested not in (None, "")})

    segments: list[dict[str, Any]] = []
    for item in list(value.get("segments") or [])[-8:]:
        if not isinstance(item, Mapping):
            continue
        segment = {
            "id": str(item.get("id") or item.get("segment_id") or "").strip()[:160],
            "thread_id": str(item.get("thread_id") or "").strip()[:300] or None,
            "summary": str(item.get("summary") or item.get("text") or "")[:1200],
            "start_seq": _nonnegative_int(item.get("start_seq")),
            "end_seq": _nonnegative_int(item.get("end_seq")),
            "trust_boundary": "retrieved_untrusted_evidence",
            "source_ref": _bounded_ref(
                item.get("source_ref"),
                keys=("type", "segment_id", "conversation_id", "thread_id", "start_seq", "end_seq"),
            ),
        }
        segments.append({key: nested for key, nested in segment.items() if nested not in (None, "")})

    memory: list[dict[str, Any]] = []
    for item in list(value.get("memory") or [])[-12:]:
        if not isinstance(item, Mapping):
            continue
        memory_item = {
            "id": str(item.get("id") or "").strip()[:160],
            "scope": str(item.get("scope") or "").strip()[:80],
            "owner": str(item.get("owner") or "").strip()[:160],
            "key": str(item.get("key") or "").strip()[:160] or None,
            "text": str(item.get("text") or "")[:1000],
            "confidence": item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
            "consent_state": str(item.get("consent_state") or "").strip()[:80] or None,
            "visibility": str(item.get("visibility") or "").strip()[:80] or None,
            "trust_boundary": "retrieved_untrusted_evidence",
            "source_ref": _bounded_ref(
                item.get("source_ref"),
                keys=("type", "memory_id", "scope", "owner", "source_ref"),
            ),
        }
        memory.append({key: nested for key, nested in memory_item.items() if nested not in (None, "")})

    diagnostics = value.get("diagnostics") if isinstance(value.get("diagnostics"), Mapping) else {}
    fallback_refs = [str(item).strip()[:160] for item in diagnostics.get("fallbacks") or [] if str(item).strip()][:20]
    return {
        "schema": "adaos.context.packet.v1",
        "conversation_id": str(value.get("conversation_id") or "").strip()[:300] or None,
        "thread_id": str(value.get("thread_id") or "").strip()[:300] or None,
        "topic_id": str(value.get("topic_id") or "").strip()[:300] or None,
        "channel_id": str(value.get("channel_id") or "").strip()[:80] or None,
        "requester_owner": str(value.get("requester_owner") or "").strip()[:160] or None,
        "messages": messages,
        "segments": segments,
        "memory": memory,
        "diagnostics": {
            "fallbacks": fallback_refs,
            "selected_message_count": len(messages),
            "selected_segment_count": len(segments),
            "selected_memory_count": len(memory),
        },
    }


def _bounded_pending_action_refs(values: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(values or [])[-30:]:
        if not isinstance(item, Mapping):
            continue
        action_id = str(item.get("id") or item.get("action_id") or "").strip()[:160]
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)
        ref = {
            "id": action_id,
            "kind": str(item.get("kind") or "").strip()[:160] or None,
            "status": str(item.get("status") or "").strip()[:80] or None,
            "webspace_id": str(item.get("webspace_id") or "").strip()[:160] or None,
            "domain_ref": _bounded_ref(
                item.get("domain_ref"),
                keys=("type", "kind", "id", "object_type", "object_id", "change_id", "run_id"),
            ),
            "allowed_actions": [
                str(value).strip()[:80]
                for value in item.get("allowed_actions") or item.get("actions") or []
                if isinstance(value, str) and str(value).strip()
            ][:20],
            "expires_at": str(item.get("expires_at") or "").strip()[:80] or None,
        }
        refs.append({key: value for key, value in ref.items() if value not in (None, "", [])})
    return refs


def _semantic_target_context(webui: Mapping[str, Any], refs: list[str]) -> dict[str, Any]:
    """Resolve stable UI refs with parent/sibling/order evidence, without text guessing."""

    matches: dict[str, list[dict[str, Any]]] = {ref: [] for ref in refs}

    def visit(value: Any, *, parent_ref: str | None = None, siblings: list[Any] | None = None) -> None:
        if isinstance(value, list):
            sibling_ids = [
                str(item.get("id") or "").strip()
                for item in value
                if isinstance(item, Mapping) and str(item.get("id") or "").strip()
            ]
            for item in value:
                visit(item, parent_ref=parent_ref, siblings=sibling_ids)
            return
        if not isinstance(value, Mapping):
            return
        item_id = str(value.get("id") or "").strip()
        candidate_refs = {f"widget:{item_id}", f"surface:{item_id}"} if item_id else set()
        for ref in refs:
            parts = ref.split(":")
            if ref in candidate_refs or (
                parts[0] == "field" and item_id and parts[-1] == item_id
            ):
                fragment = {
                    key: copy.deepcopy(value.get(key))
                    for key in (
                        "id",
                        "type",
                        "title",
                        "label",
                        "area",
                        "hidden",
                        "visibleIf",
                        "layout",
                        "responsive",
                        "dataSource",
                        "actions",
                    )
                    if key in value
                }
                matches[ref].append(
                    {
                        "target_ref": ref,
                        "parent_ref": parent_ref,
                        "siblings": list(siblings or []),
                        "order": (siblings or []).index(item_id) if item_id in (siblings or []) else None,
                        "fragment": fragment,
                    }
                )
        next_parent = f"widget:{item_id}" if item_id else parent_ref
        for child in value.values():
            visit(child, parent_ref=next_parent, siblings=None)

    visit(webui)
    resolved = [items[0] for items in matches.values() if len(items) == 1]
    missing = [ref for ref, items in matches.items() if not items]
    ambiguous = [ref for ref, items in matches.items() if len(items) > 1]
    return {
        "requested_refs": refs,
        "resolved": resolved,
        "missing_refs": missing,
        "ambiguous_refs": ambiguous,
        "status": "ambiguous" if ambiguous else "missing" if missing or not refs else "present",
    }


def _facet_coverage(facets: Mapping[str, Any], required: list[str]) -> dict[str, Any]:
    present: list[str] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for facet in required:
        value = facets.get(facet)
        status = str(value.get("status") or "") if isinstance(value, Mapping) else ""
        if status == "ambiguous":
            ambiguous.append(facet)
        elif value in (None, "", [], {}) or status == "missing":
            missing.append(facet)
        else:
            present.append(facet)
    return {
        "required": required,
        "present": present,
        "missing": missing,
        "ambiguous": ambiguous,
        "ready": not missing and not ambiguous,
    }


def _legacy_phase(value: Any) -> str:
    token = str(value or "").strip().lower()
    return "automation" if token in {"automation", "publication"} else "prototype"


@dataclass(slots=True)
class BuilderWorkflowService:
    dev_skills_root: Path
    dev_scenarios_root: Path
    state_dir: Path | None = None
    event_sink: Any = None
    workspace_root: Path | None = None
    require_active_builder_package: bool | None = None
    _active_package_digest: str | None = field(init=False, default=None)
    _active_binding_digest: str | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.dev_skills_root = Path(self.dev_skills_root)
        self.dev_scenarios_root = Path(self.dev_scenarios_root)
        self.state_dir = Path(self.state_dir or current_state_dir())
        self.workspace_root = (
            Path(self.workspace_root).expanduser().resolve()
            if self.workspace_root is not None
            else None
        )
        self.require_active_builder_package = _feature_flag(
            self.require_active_builder_package,
            env_name=BUILDER_PACKAGE_CUTOVER_ENV,
        )

    @classmethod
    def from_context(cls) -> "BuilderWorkflowService":
        ctx = get_ctx()
        return cls(
            dev_skills_root=Path(ctx.paths.dev_skills_dir()),
            dev_scenarios_root=Path(ctx.paths.dev_scenarios_dir()),
            state_dir=current_state_dir(),
            event_sink=cls._publish,
            workspace_root=Path(ctx.paths.workspace_dir()),
        )

    @staticmethod
    def _publish(projection: Mapping[str, Any]) -> None:
        try:
            from adaos.services.eventbus import emit

            emit(get_ctx().bus, BUILDER_WORKFLOW_EVENT, dict(projection), source="builder.workflow")
        except Exception:
            return

    @staticmethod
    def _executor_registry() -> WorkflowExecutorRegistry:
        return WorkflowExecutorRegistry(
            platform_workflow_adapter_registry(),
            builder_lifecycle_executor_registrations(),
        )

    def project_root(self, object_type: str, object_id: str) -> Path:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        root = (self.dev_scenarios_root if kind == "scenario" else self.dev_skills_root) / project_id
        if not root.is_dir():
            raise FileNotFoundError(f"DEV {kind} project not found: {project_id}")
        return root

    def _governed_definition(self) -> CompiledWorkflowDefinition:
        self._active_package_digest = None
        self._active_binding_digest = None
        if self.require_active_builder_package:
            return self._active_builder_definition()
        skill_root = (self.dev_skills_root / "builder_skill").resolve()
        if not skill_root.is_dir():
            # Bounded compatibility for isolated core tests and rollback only. A
            # real DEV Builder installation is authoritative when it is present.
            return compiled_builder_change_definition()
        manifest_path = skill_root / "skill.yaml"
        definition_path = skill_root / "workflow.json"
        try:
            return _load_builder_skill_definition(
                str(skill_root),
                _file_signature(manifest_path),
                _file_signature(definition_path),
            )
        except (OSError, WorkflowArtifactError) as exc:
            raise BuilderWorkflowError(f"invalid declarative Builder workflow: {exc}") from exc

    def _active_builder_definition(self) -> CompiledWorkflowDefinition:
        if self.workspace_root is None:
            raise BuilderWorkflowError(
                "Builder package cutover requires a configured Workspace root"
            )
        lock_path = self.workspace_root / ".adaos" / "workspace.lock.json"
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            workspace_lock = WorkspaceLock.from_mapping(payload)
        except FileNotFoundError as exc:
            raise BuilderWorkflowError(
                "Builder package cutover requires an active WorkspaceLock"
            ) from exc
        except (OSError, ValueError, ArtifactReleaseContractError) as exc:
            raise BuilderWorkflowError(f"invalid active WorkspaceLock: {exc}") from exc
        package = next(
            (item for item in workspace_lock.components if item.key == "skill:builder_skill"),
            None,
        )
        if package is None:
            raise BuilderWorkflowError(
                "active WorkspaceLock does not contain skill:builder_skill"
            )
        if (
            package.workflow_lock is None
            or package.workflow_validation_lock is None
            or package.workflow_binding_digest is None
        ):
            raise BuilderWorkflowError(
                "active Builder package has no complete workflow binding"
            )
        relative = package.materialization_path or "skills/builder_skill"
        skill_root = (self.workspace_root / relative).resolve()
        if self.workspace_root not in skill_root.parents:
            raise BuilderWorkflowError("active Builder materialization escapes Workspace")
        try:
            artifact = load_manifest_bound_workflow(
                skill_root,
                manifest_name="skill.yaml",
                registered_guards=set(_BUILDER_WORKFLOW_GUARDS),
                registered_activities=set(_BUILDER_WORKFLOW_ACTIVITIES),
                allow_legacy_inline=False,
            )
        except (OSError, WorkflowArtifactError) as exc:
            raise BuilderWorkflowError(
                f"invalid active Builder workflow package: {exc}"
            ) from exc
        if artifact is None or artifact.compiled.workflow_type != "builder.change":
            raise BuilderWorkflowError(
                "active Builder package must contain builder.change workflow.json"
            )
        if artifact.definition_digest != package.workflow_lock.digest:
            raise BuilderWorkflowError(
                "active Builder workflow definition differs from WorkspaceLock"
            )
        validation_digest = canonical_payload_digest(artifact.validation_report)
        if validation_digest != package.workflow_validation_lock.digest:
            raise BuilderWorkflowError(
                "active Builder workflow validation differs from WorkspaceLock"
            )
        try:
            binding = platform_workflow_adapter_registry().bind(
                artifact.compiled,
                expected_locks=(item.to_dict() for item in package.workflow_adapter_locks),
            )
        except WorkflowAdapterRegistryError as exc:
            raise BuilderWorkflowError(
                f"active Builder workflow adapter binding is invalid: {exc}"
            ) from exc
        if binding["binding_digest"] != package.workflow_binding_digest:
            raise BuilderWorkflowError(
                "active Builder workflow binding differs from WorkspaceLock"
            )
        self._active_package_digest = package.digest
        self._active_binding_digest = package.workflow_binding_digest
        return artifact.compiled

    def _workflow_inspection(self, object_type: str, object_id: str) -> dict[str, Any]:
        definition = self._governed_definition()
        process = copy.deepcopy(
            _inspect_workflow_definition(
                canonical_workflow_bytes(definition.source),
                "builder_skill/workflow.json",
            )
        )
        root = self.project_root(object_type, object_id)
        manifest_name = "scenario.yaml" if object_type == "scenario" else "skill.yaml"
        try:
            artifact = load_manifest_bound_workflow(
                root,
                manifest_name=manifest_name,
                allow_legacy_inline=object_type == "scenario",
            )
        except WorkflowArtifactError as exc:
            project: dict[str, Any] = {
                "schema": "adaos.workflow.inspection.v1",
                "source": f"{object_id}/{manifest_name}",
                "status": "invalid",
                "validation": {
                    "valid": False,
                    "diagnostics": [
                        {
                            "code": "workflow.artifact.invalid",
                            "severity": "error",
                            "path": "$",
                            "message": str(exc),
                        }
                    ],
                },
                "binding": None,
            }
        else:
            if artifact is not None:
                project = copy.deepcopy(
                    _inspect_workflow_definition(
                        canonical_workflow_bytes(artifact.definition),
                        f"{object_id}/workflow.json",
                    )
                )
            elif object_type == "scenario":
                try:
                    manifest = yaml.safe_load(
                        (root / manifest_name).read_text(encoding="utf-8")
                    ) or {}
                    legacy = manifest.get("workflow") if isinstance(manifest, Mapping) else None
                    if isinstance(legacy, Mapping) and isinstance(legacy.get("states"), Mapping):
                        translated = translate_legacy_scenario_workflow(
                            legacy,
                            scenario_id=object_id,
                        )
                        project = copy.deepcopy(
                            _inspect_workflow_definition(
                                canonical_workflow_bytes(translated),
                                f"{object_id}/{manifest_name}#workflow",
                            )
                        )
                        project["status"] = "legacy_shadow"
                        project["shadow"] = shadow_compare_legacy_workflow(
                            legacy,
                            translated,
                            scenario_id=object_id,
                        )
                    else:
                        project = {
                            "schema": "adaos.workflow.inspection.v1",
                            "source": f"{object_id}/{manifest_name}",
                            "status": "not_declared",
                            "validation": None,
                            "binding": None,
                        }
                except (OSError, UnicodeError, yaml.YAMLError, LegacyWorkflowTranslationError) as exc:
                    project = {
                        "schema": "adaos.workflow.inspection.v1",
                        "source": f"{object_id}/{manifest_name}#workflow",
                        "status": "invalid",
                        "validation": {
                            "valid": False,
                            "diagnostics": [
                                {
                                    "code": "workflow.legacy.translation_failed",
                                    "severity": "error",
                                    "path": "$.workflow",
                                    "message": str(exc),
                                }
                            ],
                        },
                        "binding": None,
                    }
            else:
                project = {
                    "schema": "adaos.workflow.inspection.v1",
                    "source": f"{object_id}/{manifest_name}",
                    "status": "not_declared",
                    "validation": None,
                    "binding": None,
                }
        return {"process": process, "project": project}

    def _state_path(self, object_type: str, object_id: str) -> Path:
        return self.project_root(object_type, object_id) / "prompt_state.json"

    def _read_state(self, object_type: str, object_id: str) -> dict[str, Any]:
        path = self._state_path(object_type, object_id)
        if not path.is_file():
            return {}
        try:
            if path.stat().st_size > _MAX_STATE_BYTES:
                raise BuilderWorkflowError("prompt context exceeds the bounded state size")
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise BuilderWorkflowError(f"invalid prompt_state.json: {exc}") from exc
        return dict(value) if isinstance(value, Mapping) else {}

    def _write_state(self, object_type: str, object_id: str, state: Mapping[str, Any]) -> None:
        path = self._state_path(object_type, object_id)
        raw = (json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(raw) > _MAX_STATE_BYTES:
            raise BuilderWorkflowError("prompt context exceeds the bounded state size")
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(raw)
        _replace_path(temporary, path)

    def _migration_checkpoint_path(self, checkpoint_id: str) -> Path:
        token = str(checkpoint_id or "").strip().lower()
        if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
            raise BuilderWorkflowError("Builder migration checkpoint id is invalid")
        return Path(self.state_dir) / "builder" / "workflow_migrations" / f"{token}.json"

    def migrate_in_flight_instance(
        self,
        object_type: str,
        object_id: str,
        *,
        source_definition: CompiledWorkflowDefinition | Mapping[str, Any],
        target_definition: CompiledWorkflowDefinition | Mapping[str, Any],
        migration: Mapping[str, Any],
        expected_generation: int,
        idempotency_key: str,
        actor_id: str = "user:local",
        permissions: tuple[str, ...] = ("workflow.definition.migrate",),
        target_package_digest: str | None = None,
        target_binding_digest: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Migrate one persisted Builder instance with a restart-safe checkpoint."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        source = (
            source_definition
            if isinstance(source_definition, CompiledWorkflowDefinition)
            else compile_definition(source_definition)
        )
        target = (
            target_definition
            if isinstance(target_definition, CompiledWorkflowDefinition)
            else compile_definition(target_definition)
        )
        state = self._read_state(kind, project_id)
        workflow = _mapping(state.get("workflow"))
        current = _mapping(workflow.get("governed"))
        if not current:
            raise BuilderWorkflowError("Builder project has no in-flight governed instance")
        checkpoint_id = hashlib.sha256(
            canonical_workflow_bytes(
                {
                    "project": f"{kind}:{project_id}",
                    "instance_id": current.get("instance_id"),
                    "migration_id": migration.get("migration_id"),
                    "idempotency_key": idempotency_key,
                }
            )
        ).hexdigest()
        checkpoint_path = self._migration_checkpoint_path(checkpoint_id)
        if checkpoint_path.is_file():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if current == checkpoint.get("after"):
                return {
                    "status": "completed",
                    "checkpoint_id": checkpoint_id,
                    "instance": copy.deepcopy(current),
                    "idempotent_replay": True,
                }
            if current == checkpoint.get("before") and checkpoint.get("after"):
                workflow["governed"] = copy.deepcopy(checkpoint["after"])
                state["workflow"] = workflow
                self._write_state(kind, project_id, state)
                return {
                    "status": "completed",
                    "checkpoint_id": checkpoint_id,
                    "instance": copy.deepcopy(checkpoint["after"]),
                    "idempotent_replay": True,
                }
            raise BuilderWorkflowError(
                "Builder migration checkpoint conflicts with current instance"
            )
        principal = verified_workflow_principal(
            actor_id,
            authenticated=True,
            issuer="adaos.builder.workflow_migration",
            permissions=permissions,
        )
        try:
            decision = migrate_workflow_instance(
                source,
                target,
                current,
                migration,
                actor=actor_id,
                permissions=permissions,
                principal=principal,
                require_verified_principal=True,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
                target_package_digest=target_package_digest,
                target_binding_digest=target_binding_digest,
                now=now,
            )
        except (ValueError, TypeError) as exc:
            raise BuilderWorkflowError(f"Builder workflow migration rejected: {exc}") from exc
        checkpoint = {
            "schema": "adaos.builder.workflow_migration_checkpoint.v1",
            "checkpoint_id": checkpoint_id,
            "object_type": kind,
            "object_id": project_id,
            "migration_id": migration.get("migration_id"),
            "source_definition_digest": workflow_definition_digest(source),
            "target_definition_digest": workflow_definition_digest(target),
            "before": copy.deepcopy(current),
            "after": copy.deepcopy(decision["after"]),
            "created_at": now or _now(),
            "rolled_back_at": None,
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_tmp = checkpoint_path.with_name(f".{checkpoint_path.name}.tmp")
        checkpoint_tmp.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _replace_path(checkpoint_tmp, checkpoint_path)
        workflow["governed"] = copy.deepcopy(decision["after"])
        state["workflow"] = workflow
        self._write_state(kind, project_id, state)
        return {
            "status": "completed",
            "checkpoint_id": checkpoint_id,
            "instance": copy.deepcopy(decision["after"]),
            "idempotent_replay": False,
        }

    def rollback_in_flight_migration(
        self,
        checkpoint_id: str,
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        checkpoint_path = self._migration_checkpoint_path(checkpoint_id)
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BuilderWorkflowError(f"cannot read Builder migration checkpoint: {exc}") from exc
        kind = _kind(checkpoint.get("object_type"))
        project_id = _project_id(checkpoint.get("object_id"))
        state = self._read_state(kind, project_id)
        workflow = _mapping(state.get("workflow"))
        current = _mapping(workflow.get("governed"))
        before = _mapping(checkpoint.get("before"))
        after = _mapping(checkpoint.get("after"))
        if not before or not after:
            raise BuilderWorkflowError("Builder migration checkpoint is incomplete")
        if current == before:
            if not checkpoint.get("rolled_back_at"):
                checkpoint["rolled_back_at"] = now or _now()
                checkpoint_tmp = checkpoint_path.with_name(f".{checkpoint_path.name}.tmp")
                checkpoint_tmp.write_text(
                    json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                _replace_path(checkpoint_tmp, checkpoint_path)
            return {
                "status": "rolled_back",
                "checkpoint_id": checkpoint_id,
                "instance": copy.deepcopy(before),
                "idempotent_replay": True,
            }
        if current != after:
            raise BuilderWorkflowError(
                "Builder migration rollback requires the exact migrated instance"
            )
        workflow["governed"] = copy.deepcopy(before)
        state["workflow"] = workflow
        self._write_state(kind, project_id, state)
        checkpoint["rolled_back_at"] = now or _now()
        checkpoint_tmp = checkpoint_path.with_name(f".{checkpoint_path.name}.tmp")
        checkpoint_tmp.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _replace_path(checkpoint_tmp, checkpoint_path)
        return {
            "status": "rolled_back",
            "checkpoint_id": checkpoint_id,
            "instance": copy.deepcopy(before),
            "idempotent_replay": False,
        }

    def _project_version(self, object_type: str, object_id: str) -> str | None:
        kind = _kind(object_type)
        root = self.project_root(kind, object_id)
        path = root / ("scenario.yaml" if kind == "scenario" else "skill.yaml")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            return None
        return str(value.get("version") or "").strip() or None if isinstance(value, Mapping) else None

    def _project_manifest_metadata(self, object_type: str, object_id: str) -> dict[str, Any]:
        kind = _kind(object_type)
        root = self.project_root(kind, object_id)
        path = root / ("scenario.yaml" if kind == "scenario" else "skill.yaml")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            value = {}
        manifest = dict(value) if isinstance(value, Mapping) else {}
        return {
            "title": str(manifest.get("title") or manifest.get("name") or object_id).strip()
            or object_id,
            "description": str(manifest.get("description") or "").strip() or None,
            "version": str(manifest.get("version") or "").strip() or None,
        }

    def current_prototype_revision(self, object_type: str, object_id: str) -> str | None:
        kind = _kind(object_type)
        if kind != "scenario":
            return self._project_version(kind, object_id)
        revision_dir = self.project_root(kind, object_id) / "ui_revisions"
        path = revision_dir / "current.txt"
        try:
            revision = str(path.read_text(encoding="utf-8-sig")).strip()
        except OSError:
            return None
        if not revision.isdigit() or not (revision_dir / f"{revision}.json").is_file():
            return None
        return revision

    def _normalized_workflow(
        self,
        state: Mapping[str, Any],
        *,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        raw = _mapping(state.get("workflow"))
        legacy_state = str(state.get("workflow_state") or "prototype").strip().lower()
        active_phase = str(raw.get("active_phase") or _legacy_phase(legacy_state)).strip().lower()
        if active_phase not in {"prototype", "automation"}:
            active_phase = "prototype"

        prototype = _mapping(raw.get("prototype"))
        automation = _mapping(raw.get("automation"))
        publication = _mapping(raw.get("publication"))
        delivery = _mapping(raw.get("delivery"))
        raw_change = raw.get("change")
        raw_change_set = raw.get("change_set")
        if isinstance(raw_change, Mapping) and isinstance(raw_change_set, Mapping):
            change_id = str(raw_change.get("change_id") or raw_change.get("change_set_id") or "").strip()
            change_set_id = str(raw_change_set.get("change_set_id") or raw_change_set.get("change_id") or "").strip()
            if change_id and change_set_id and change_id != change_set_id:
                raise BuilderWorkflowError("workflow change and change_set identities diverge")
        change = _normalize_change(raw_change if isinstance(raw_change, Mapping) else raw_change_set)
        if change:
            change["project_ref"] = change.get("project_ref") or f"{_kind(object_type)}:{_project_id(object_id)}"
            if not change.get("supersedes_change_id"):
                for event in reversed(raw.get("history") or []):
                    if not isinstance(event, Mapping) or event.get("action") != "plan_change_set":
                        continue
                    event_metadata = _mapping(event.get("metadata"))
                    if str(event_metadata.get("change_set_id") or "") != change["change_id"]:
                        continue
                    supersedes = str(event_metadata.get("supersedes_change_set_id") or "").strip()
                    if supersedes:
                        change["supersedes_change_id"] = supersedes
                    break
        change_set = _change_set_compatibility(change)
        current_revision = self.current_prototype_revision(object_type, object_id)
        prototype.setdefault("head_revision", current_revision)
        if _kind(object_type) == "scenario" and active_phase == "prototype":
            prototype["head_revision"] = current_revision
        prototype.setdefault("status", "working" if active_phase == "prototype" else "frozen")
        prototype.setdefault("stable", legacy_state in {"prototype_stable", "automation", "publication"})

        if "status" not in automation:
            if legacy_state == "publication":
                automation["status"] = "completed"
            elif active_phase == "automation":
                automation["status"] = "working"
            else:
                automation["status"] = "not_started"
        automation.setdefault("iteration", 0)
        automation.setdefault("source_prototype_revision", prototype.get("head_revision"))
        if not str(automation.get("snapshot_task_id") or "").strip():
            snapshot_path = Path(str(automation.get("snapshot_path") or "").strip())
            try:
                snapshot = json.loads((snapshot_path / "snapshot.json").read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError):
                snapshot = {}
            if isinstance(snapshot, Mapping):
                snapshot_task_id = str(snapshot.get("task_id") or "").strip()
                if snapshot_task_id:
                    automation["snapshot_task_id"] = snapshot_task_id

        if "status" not in publication:
            publication["status"] = "published" if legacy_state == "publication" else "not_started"
        publication.setdefault("current_version", None)
        publication.setdefault("published_at", None)

        if "status" not in delivery:
            delivery["status"] = (
                "published" if publication.get("status") == "published" else "idle"
            )
        delivery.setdefault("candidate_id", None)
        delivery.setdefault("release_digest", None)
        delivery.setdefault("package_digest", None)
        delivery.setdefault("base_release", None)
        delivery.setdefault("trial_workspace", None)
        delivery.setdefault("prepared_at", None)
        delivery.setdefault("decided_at", None)
        delivery.setdefault("replaces_candidate_id", None)
        delivery.setdefault("rebase_plan", None)

        normalized = {
            "schema": BUILDER_WORKFLOW_SCHEMA,
            "generation": max(0, int(raw.get("generation") or 0)),
            "active_phase": active_phase,
            "prototype": prototype,
            "automation": automation,
            "delivery": delivery,
            "publication": publication,
            "change": change,
            "change_set": change_set,
            "context_packet": _mapping(raw.get("context_packet")) or None,
            "reviews": [
                copy.deepcopy(dict(item))
                for item in raw.get("reviews") or []
                if isinstance(item, Mapping)
            ][-200:],
            "interaction": {
                "conversation_focus": str(
                    _mapping(raw.get("interaction")).get("conversation_focus")
                    or (f"change:{change['change_id']}" if change else f"{_kind(object_type)}:{_project_id(object_id)}")
                ).strip(),
                "inspected_ref": str(
                    _mapping(raw.get("interaction")).get("inspected_ref") or ""
                ).strip()
                or None,
                "preview_target": str(
                    _mapping(raw.get("interaction")).get("preview_target") or ""
                ).strip()
                or None,
            },
            "pending_transition": _mapping(raw.get("pending_transition")) or None,
            "checkpoint_versions": _mapping(raw.get("checkpoint_versions")),
            "history": [
                dict(item)
                for item in raw.get("history") or []
                if isinstance(item, Mapping)
            ][-_MAX_HISTORY:],
            "updated_at": str(raw.get("updated_at") or state.get("updated_at") or "").strip() or None,
        }
        definition = self._governed_definition()
        normalized["governed"] = governed_instance(
            {**normalized, "governed": raw.get("governed")},
            project_ref=f"{_kind(object_type)}:{_project_id(object_id)}",
            definition=definition,
            package_digest=self._active_package_digest,
            binding_digest=self._active_binding_digest,
        )
        normalized["data_binding"] = normalize_binding_state(
            raw.get("data_binding"),
            project_ref=f"{_kind(object_type)}:{_project_id(object_id)}",
        )
        normalized["change_portfolio"] = normalize_portfolio(
            raw.get("change_portfolio"),
            normalized,
        )
        manifest_metadata = self._project_manifest_metadata(object_type, object_id)
        normalized["project"] = normalize_project(
            raw.get("project"),
            object_type=_kind(object_type),
            object_id=_project_id(object_id),
            archived=bool(state.get("archived")),
            workflow=normalized,
            title=manifest_metadata["title"],
            description=manifest_metadata["description"],
        )
        return normalized

    @staticmethod
    def _capabilities(workflow: Mapping[str, Any], *, archived: bool, object_type: str) -> dict[str, bool]:
        active = str(workflow.get("active_phase") or "prototype")
        automation = _mapping(workflow.get("automation"))
        automation_status = str(automation.get("status") or "not_started")
        delivery_status = str(_mapping(workflow.get("delivery")).get("status") or "idle")
        retained_automation = bool(str(automation.get("snapshot_path") or "").strip())
        change = _normalize_change(workflow.get("change") or workflow.get("change_set"))
        change_set_status = str((change or {}).get("status") or "")
        automation_previewable = automation_status == "completed" or (
            retained_automation and automation_status in {"adapting", "failed", "frozen"}
        )
        mutable = not archived
        return {
            "can_edit_prototype": mutable and active == "prototype",
            "can_stabilize_prototype": mutable and active == "prototype",
            "can_handoff_to_automation": mutable and active == "prototype",
            "can_edit_automation": mutable and active == "automation" and automation_status != "adapting",
            "can_return_to_prototype": mutable and active == "automation" and automation_status == "completed",
            "can_prepare_candidate": mutable
            and active == "automation"
            and automation_status == "completed"
            and delivery_status == "checkpoint",
            "can_decide_candidate": mutable and delivery_status == "trial",
            "can_publish": mutable
            and active == "automation"
            and automation_status == "completed"
            and delivery_status == "accepted",
            "can_preview_prototype": object_type == "scenario",
            "can_preview_automation": object_type == "scenario" and automation_previewable,
            "can_preview_publication": object_type == "scenario"
            and str(_mapping(workflow.get("publication")).get("status") or "") == "published",
            "can_plan_change_set": mutable
            and (not change or change_set_status in _CHANGE_SET_TERMINAL_STATES),
            "can_update_change_set": mutable
            and bool(change)
            and change_set_status not in _CHANGE_SET_TERMINAL_STATES,
        }

    def describe(self, object_type: str, object_id: str) -> dict[str, Any]:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
        projection = {
            **copy.deepcopy(workflow),
            "object_type": kind,
            "object_id": project_id,
            "archived": bool(state.get("archived")),
            "capabilities": self._capabilities(workflow, archived=bool(state.get("archived")), object_type=kind),
        }
        description = workflow_description(
            workflow,
            project_ref=f"{kind}:{project_id}",
            definition=self._governed_definition(),
        )
        projection["workflow_description"] = description_with_executor_readiness(
            description,
            self._governed_definition(),
            self._executor_registry(),
        )
        projection["workflow_inspection"] = self._workflow_inspection(kind, project_id)
        projection["process"] = self._process_projection(projection)
        projection["project_summary"] = self._project_summary(projection)
        projection["specification"] = specification_projection(
            projection.get("change") or projection.get("change_set")
        )
        return projection

    @staticmethod
    def _compact_explanation(projection: Mapping[str, Any]) -> dict[str, Any]:
        description = _mapping(projection.get("workflow_description"))
        change = _normalize_change(projection.get("change") or projection.get("change_set"))
        process = _mapping(projection.get("process"))
        state = str(description.get("state") or "ready")
        blockers = [
            {
                "command": str(item.get("command") or ""),
                "reason_code": str(item.get("reason_code") or "blocked"),
            }
            for item in description.get("blockers") or []
            if isinstance(item, Mapping)
        ]
        workflow_commands = [
            str(item.get("command") or "")
            for item in description.get("allowed_commands") or []
            if isinstance(item, Mapping) and str(item.get("command") or "").strip()
        ]
        project_commands = [
            str(item.get("command") or "")
            for item in _mapping(projection.get("project_summary")).get("commands") or []
            if isinstance(item, Mapping)
            and str(item.get("command") or "").strip() == "builder.change.plan"
        ]
        next_commands = list(dict.fromkeys([*workflow_commands, *project_commands]))
        progress = _mapping(description.get("progress"))
        project = _mapping(projection.get("project"))
        identity = _mapping(project.get("identity"))
        publication = _mapping(projection.get("publication"))
        placements = [
            dict(item)
            for item in project.get("placements") or []
            if isinstance(item, Mapping)
        ]
        stable_placement = active_project_placement(placements, kind="stable")
        installed = _mapping(project.get("installed_release_ref"))
        project_title = str(identity.get("title") or projection.get("object_id") or "Project")
        published_version = str(publication.get("current_version") or "").strip()
        if state == "published":
            release_label = published_version or "current"
            summary = f'Version {release_label} of "{project_title}" is published to stable.'
            installation_text = (
                "Installed in Workspace."
                if installed
                else "Workspace installation is not recorded."
            )
            placement_text = (
                f"Placed in Webspace {_mapping(stable_placement.get('target')).get('webspace_id')}."
                if stable_placement
                else "Not placed in a Webspace yet."
            )
            reason = f"{installation_text} {placement_text}"
            next_commands = [
                "builder.publication.open" if stable_placement else "builder.publication.place",
                "builder.process.inspect",
                "builder.change.plan",
                "builder.project.list",
                "builder.help",
            ]
        elif change is None:
            summary = "No active Change. Describe the requested change to begin."
            reason = "No active blocker."
        else:
            summary = f"Change {change['change_id']} is in {state}."
            reason = "No active blocker."
        if state != "published" and progress.get("waiting") and progress.get("wait_explanation"):
            reason = str(progress["wait_explanation"])
        elif state != "published" and blockers:
            reason = "; ".join(item["reason_code"] for item in blockers[:3])
        next_text = ", ".join(next_commands[:4]) if next_commands else "wait for input or inspect the process"
        return {
            "schema": "adaos.builder.compact_workflow_explanation.v1",
            "project_ref": f"{projection.get('object_type')}:{projection.get('object_id')}",
            "change_ref": f"change:{change['change_id']}" if change else None,
            "state": state,
            "generation": int(description.get("generation") or 0),
            "target": copy.deepcopy(description.get("target")),
            "project_title": project_title,
            "published_version": published_version or None,
            "installed": bool(installed),
            "placement": copy.deepcopy(stable_placement),
            "summary": summary,
            "reason": reason,
            "blockers": blockers,
            "evidence_refs": copy.deepcopy(description.get("evidence_refs") or []),
            "next_commands": next_commands,
            "process_node_refs": [
                str(item.get("ref") or "")
                for item in process.get("nodes") or []
                if isinstance(item, Mapping) and str(item.get("ref") or "").strip()
            ],
            "text": f"{summary} Why: {reason} Next: {next_text}.",
        }

    def compact_explanation(self, object_type: str, object_id: str) -> dict[str, Any]:
        """Return one channel-neutral answer to what, why, and what next."""

        return self._compact_explanation(self.describe(object_type, object_id))

    def process_explanation(
        self,
        object_type: str,
        object_id: str,
        *,
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Render the canonical lineage as a useful compact channel timeline."""

        projection = self.describe(object_type, object_id)
        process = _mapping(projection.get("process"))
        project = _mapping(projection.get("project"))
        identity = _mapping(project.get("identity"))
        selected_locale = normalize_builder_locale(locale)
        labels = {
            "change": {"en": "Change", "ru": "Изменение"},
            "prototype": {"en": "Prototype", "ru": "Прототип"},
            "automation": {"en": "Automation", "ru": "Автоматизация"},
            "verification": {"en": "Verification", "ru": "Проверка"},
            "trial": {"en": "Trial", "ru": "Апробация"},
            "publication": {"en": "Stable release", "ru": "Стабильная версия"},
            "workspace_installation": {"en": "Workspace installation", "ru": "Установка в Workspace"},
            "placement": {"en": "Webspace placement", "ru": "Размещение в Webspace"},
        }
        status_labels = {
            "frozen": {"en": "frozen", "ru": "зафиксировано"},
            "ready": {"en": "ready", "ru": "готово"},
            "active": {"en": "active", "ru": "активно"},
            "working": {"en": "in progress", "ru": "в работе"},
            "waiting": {"en": "waiting", "ru": "ожидание"},
            "reviewing": {"en": "awaiting review", "ru": "ожидает проверки"},
            "completed": {"en": "completed", "ru": "завершено"},
            "accepted": {"en": "accepted", "ru": "принято"},
            "published": {"en": "published", "ru": "опубликовано"},
            "installed": {"en": "installed", "ru": "установлено"},
            "failed": {"en": "failed", "ru": "ошибка"},
            "rejected": {"en": "changes requested", "ru": "нужна доработка"},
        }
        nodes = [dict(item) for item in process.get("nodes") or [] if isinstance(item, Mapping)]
        lines = [
            (
                f"Project: {identity.get('title') or object_id}"
                if selected_locale == "en"
                else f"Проект: {identity.get('title') or object_id}"
            )
        ]
        for item in nodes:
            kind = str(item.get("kind") or "")
            label = str(_mapping(labels.get(kind)).get(selected_locale) or kind)
            detail = str(item.get("label") or "").strip()
            status = str(item.get("status") or "").strip()
            generic_prefixes = {
                "change": "Change ",
                "prototype": "Prototype ",
                "automation": "Automation ",
                "verification": "Verification",
                "trial": "Trial ",
                "publication": "Publication ",
                "workspace_installation": "Workspace installation ",
                "placement": "Webspace ",
            }
            prefix = generic_prefixes.get(kind, "")
            if prefix and detail.startswith(prefix):
                detail = detail[len(prefix) :].strip()
            if detail == "Verification":
                detail = ""
            localized_status = str(
                _mapping(status_labels.get(status)).get(selected_locale) or status
            )
            marker = (
                "✗"
                if status in {"failed", "rejected", "blocked"}
                else "→"
                if status in {"active", "working", "waiting", "reviewing", "running"}
                else "✓"
            )
            detail_text = f": {detail}" if detail else ""
            lines.append(f"{marker} {label}{detail_text} — {localized_status}")
        workflow_state = str(process.get("workflow_state") or "ready")
        compact = self._compact_explanation(projection)
        next_commands = [
            str(item) for item in compact.get("next_commands") or [] if str(item).strip()
        ]
        if next_commands:
            primary = builder_action_label(next_commands[0], locale=selected_locale)
            lines.append(
                f"Next: {primary}"
                if selected_locale == "en"
                else f"Дальше: {primary}"
            )
        elif compact.get("reason"):
            lines.append(str(compact.get("reason")))
        return {
            "schema": "adaos.builder.process_explanation.v1",
            "project_ref": process.get("project_ref"),
            "workflow_state": workflow_state,
            "generation": process.get("generation"),
            "nodes": nodes,
            "text": "\n".join(lines),
            "locale_context": builder_surface_locale_context(selected_locale),
        }

    @staticmethod
    def _project_summary(workflow: Mapping[str, Any]) -> dict[str, Any]:
        project = _mapping(workflow.get("project"))
        changes = [dict(item) for item in project.get("changes") or [] if isinstance(item, Mapping)]
        open_changes = [
            item
            for item in changes
            if str(item.get("status") or "") not in _CHANGE_SET_TERMINAL_STATES
        ]
        artifact_generation = int(project.get("artifact_generation") or 0)
        stale = [
            item["change_id"]
            for item in open_changes
            if int(item.get("base_generation") or 0) != artifact_generation
        ]
        commands: list[dict[str, Any]] = []
        if bool(project.get("archived")):
            commands.append({"command": "builder.project.restore", "risk": "local_reversible"})
        else:
            commands.append({"command": "builder.change.plan", "risk": "local_reversible"})
            if changes:
                commands.append(
                    {
                        "command": "builder.change.focus",
                        "risk": "read",
                        "change_ids": [item["change_id"] for item in changes],
                    }
                )
            if stale:
                commands.append(
                    {
                        "command": "builder.change.rebase",
                        "risk": "isolated_write",
                        "change_ids": stale,
                    }
                )
            commands.append({"command": "builder.project.archive", "risk": "destructive"})
        return {
            "schema": "adaos.builder.project_summary.v1",
            "project_ref": project.get("project_ref"),
            "open_change_count": len(open_changes),
            "terminal_change_count": len(changes) - len(open_changes),
            "active_mutation_count": sum(
                1 for item in open_changes if item.get("mutation_status") == "active"
            ),
            "unknown_outcome_count": sum(
                1 for item in open_changes if item.get("mutation_status") == "outcome_unknown"
            ),
            "conflict_count": len(project.get("conflicts") or []),
            "stale_change_ids": stale,
            "commands": commands,
            "focused_change_ids": copy.deepcopy(project.get("focus_by_context") or {}),
            "generation": int(project.get("generation") or 0),
            "artifact_generation": artifact_generation,
        }

    def focus_change(
        self,
        object_type: str,
        object_id: str,
        change_id: str,
        *,
        command_context_id: str = "default",
        expected_view_generation: int | None = None,
    ) -> dict[str, Any]:
        """Select a Change for one command context without advancing that Change."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        target_id = str(change_id or "").strip()
        context_id = str(command_context_id or "default").strip() or "default"
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            project = _mapping(workflow.get("project"))
            if expected_view_generation is not None and int(project.get("view_generation") or 0) != int(
                expected_view_generation
            ):
                raise BuilderWorkflowError("stale Builder project view generation")
            try:
                project = set_focus(project, context_id, target_id)
            except ValueError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
            if context_id == "default":
                record = portfolio.get(target_id)
                if not isinstance(record, Mapping):
                    raise BuilderWorkflowError(f"Builder Change state is unavailable: {target_id}")
                restore_compatibility_record(workflow, record)
                interaction = _mapping(workflow.get("interaction"))
                interaction["conversation_focus"] = f"change:{target_id}"
                workflow["interaction"] = interaction
            workflow["change_portfolio"] = portfolio
            workflow["project"] = project
            state["workflow"] = workflow
            state["updated_at"] = project["updated_at"]
            self._write_state(kind, project_id, state)
        projection = self.describe(kind, project_id)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "workflow": projection, "project": copy.deepcopy(project)}

    def rebase_change(
        self,
        object_type: str,
        object_id: str,
        change_id: str,
        *,
        expected_project_generation: int,
        verified_unchanged_refs: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        """Explicitly rebase one scoped Change after deterministic ref verification."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        target_id = str(change_id or "").strip()
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            try:
                project = rebase_project_change(
                    _mapping(workflow.get("project")),
                    target_id,
                    expected_project_generation=expected_project_generation,
                    verified_unchanged_refs=verified_unchanged_refs,
                )
            except BuilderProjectError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            current = _normalize_change(workflow.get("change") or workflow.get("change_set"))
            summary = next(
                (
                    item
                    for item in project.get("changes") or []
                    if isinstance(item, Mapping) and item.get("change_id") == target_id
                ),
                None,
            )
            if current and current.get("change_id") == target_id and isinstance(summary, Mapping):
                current["base_generation"] = int(summary.get("base_generation") or 0)
                workflow["change"] = current
                workflow["change_set"] = _change_set_compatibility(current)
            workflow["project"] = project
            workflow["change_portfolio"] = normalize_portfolio(
                workflow.get("change_portfolio"), workflow
            )
            state["workflow"] = workflow
            state["updated_at"] = project["updated_at"]
            self._write_state(kind, project_id, state)
        projection = self.describe(kind, project_id)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "workflow": projection, "project": copy.deepcopy(project)}

    def configure_project_dependencies(
        self,
        object_type: str,
        object_id: str,
        dependencies: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        expected_project_generation: int,
    ) -> dict[str, Any]:
        """Replace the bounded component graph and recompute indirect conflicts."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            try:
                project = set_dependencies(
                    _mapping(workflow.get("project")),
                    dependencies,
                    expected_project_generation=expected_project_generation,
                )
            except BuilderProjectError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            workflow["project"] = project
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = project["updated_at"]
            state["workflow"] = workflow
            state["updated_at"] = project["updated_at"]
            self._write_state(kind, project_id, state)
        projection = self.describe(kind, project_id)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "workflow": projection, "project": copy.deepcopy(project)}

    def configure_binding_profile(
        self,
        object_type: str,
        object_id: str,
        profile: Mapping[str, Any],
        *,
        expected_binding_generation: int,
    ) -> dict[str, Any]:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            binding = _mapping(workflow.get("data_binding"))
            if int(binding.get("generation") or 0) != int(expected_binding_generation):
                raise BuilderWorkflowError("stale Builder binding generation")
            try:
                workflow["data_binding"] = put_profile(binding, profile)
            except BuilderDataModeError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = workflow["data_binding"]["updated_at"]
            state["workflow"] = workflow
            state["updated_at"] = workflow["updated_at"]
            self._write_state(kind, project_id, state)
        return {"ok": True, "workflow": self.describe(kind, project_id)}

    def record_project_placement(
        self,
        object_type: str,
        object_id: str,
        placement: Mapping[str, Any],
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        """Persist one exact stable/Trial result placement under the Project aggregate."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            if int(workflow.get("generation") or 0) != int(expected_generation):
                raise BuilderWorkflowError("stale Builder workflow generation")
            project = _mapping(workflow.get("project"))
            try:
                normalized = normalize_project_placement(
                    placement,
                    project_ref=f"{kind}:{project_id}",
                )
            except BuilderPlacementError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            placements = [
                copy.deepcopy(dict(item))
                for item in project.get("placements") or []
                if isinstance(item, Mapping)
                and str(item.get("placement_id") or "") != normalized["placement_id"]
            ]
            if normalized["status"] == "active":
                placements = [
                    {
                        **item,
                        "status": "detached",
                        "detached_at": normalized["updated_at"],
                        "updated_at": normalized["updated_at"],
                    }
                    if str(item.get("kind") or "") == normalized["kind"]
                    and str(item.get("status") or "") == "active"
                    else item
                    for item in placements
                ]
            project["placements"] = [*placements, normalized][-100:]
            workflow["project"] = project
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = normalized["updated_at"]
            state["workflow"] = workflow
            state["updated_at"] = workflow["updated_at"]
            self._write_state(kind, project_id, state)
        projection = self.describe(kind, project_id)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "placement": normalized, "workflow": projection}

    def project_placement_navigation(
        self,
        object_type: str,
        object_id: str,
        *,
        kind: str = "stable",
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Build a topology-aware link from an accepted ProjectPlacement."""

        from adaos.sdk import navigation
        from adaos.services.zone_hosts import DEFAULT_PUBLIC_APP_BASE_URL

        projection = self.describe(object_type, object_id)
        project = _mapping(projection.get("project"))
        placement = active_project_placement(
            [dict(item) for item in project.get("placements") or [] if isinstance(item, Mapping)],
            kind=kind,
        )
        if placement is None:
            raise BuilderWorkflowError(f"active {kind} ProjectPlacement is unavailable")
        target = _mapping(placement.get("target"))
        runtime_scope = navigation.runtime_scope()
        zone = str(target.get("zone") or runtime_scope.get("zone") or "").strip()
        subnet_id = str(target.get("subnet_id") or runtime_scope.get("subnet_id") or "").strip()
        if not zone or not subnet_id:
            raise BuilderWorkflowError("ProjectPlacement navigation requires zone and subnet identity")
        result_ref = _mapping(placement.get("result_ref"))
        destination = navigation.webspace_destination(
            zone=zone,
            subnet_id=subnet_id,
            webspace_id=str(target.get("webspace_id") or ""),
            space_kind=str(target.get("space_kind") or ("trial" if kind == "trial" else "workspace")),
            expected_scenario_id=str(placement.get("scenario_id") or object_id).strip() or None,
            expected_revision=str(result_ref.get("version") or "").strip() or None,
            preview_stage="publication" if kind == "stable" else "trial",
        )
        return {
            "schema": "adaos.builder.placement_navigation.v1",
            "placement": copy.deepcopy(placement),
            "destination": destination,
            "url": navigation.build_url(
                destination,
                base_url=str(base_url or DEFAULT_PUBLIC_APP_BASE_URL),
            ),
        }

    def select_binding_profile(
        self,
        object_type: str,
        object_id: str,
        profile_id: str,
        *,
        expected_binding_generation: int,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Switch Preview data without changing the accepted UI Revision."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            binding = _mapping(workflow.get("data_binding"))
            if int(binding.get("generation") or 0) != int(expected_binding_generation):
                raise BuilderWorkflowError("stale Builder binding generation")
            revision_before = _mapping(workflow.get("prototype")).get("head_revision")
            try:
                workflow["data_binding"] = select_profile(
                    binding,
                    str(profile_id or "").strip(),
                    phase=str(workflow.get("active_phase") or "prototype"),
                    confirmed=confirmed,
                )
            except BuilderDataModeError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            if _mapping(workflow.get("prototype")).get("head_revision") != revision_before:
                raise BuilderWorkflowError("binding selection must not rewrite the Prototype Revision")
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = workflow["data_binding"]["updated_at"]
            state["workflow"] = workflow
            state["updated_at"] = workflow["updated_at"]
            self._write_state(kind, project_id, state)
        return {"ok": True, "workflow": self.describe(kind, project_id)}

    @staticmethod
    def _process_projection(workflow: Mapping[str, Any]) -> dict[str, Any]:
        """Build one dependent lineage tree from canonical state and exact refs."""

        object_type = str(workflow.get("object_type") or "scenario")
        object_id = str(workflow.get("object_id") or "")
        project_ref = f"{object_type}:{object_id}"
        change = _normalize_change(workflow.get("change") or workflow.get("change_set"))
        prototype = _mapping(workflow.get("prototype"))
        automation = _mapping(workflow.get("automation"))
        delivery = _mapping(workflow.get("delivery"))
        publication = _mapping(workflow.get("publication"))
        project = _mapping(workflow.get("project"))
        description = _mapping(workflow.get("workflow_description"))
        state = str(description.get("state") or _mapping(workflow.get("governed")).get("state") or "ready")
        nodes: list[dict[str, Any]] = []
        if change:
            change_ref = f"change:{change['change_id']}"
            nodes.append(
                {
                    "ref": change_ref,
                    "kind": "change",
                    "parent_ref": project_ref,
                    "label": str(change.get("request") or change["change_id"]),
                    "status": str(change.get("status") or state),
                    "workflow_state": state,
                }
            )
            parent_ref = change_ref
        else:
            parent_ref = project_ref
        prototype_revision = str(prototype.get("head_revision") or "").strip()
        prototype_ref = f"prototype:{object_id}:{prototype_revision or 'current'}"
        if object_type == "scenario":
            nodes.append(
                {
                    "ref": prototype_ref,
                    "kind": "prototype",
                    "parent_ref": parent_ref,
                    "label": f"Prototype {prototype_revision or 'current'}",
                    "status": str(prototype.get("status") or "working"),
                    "preview": f"proto:{object_id}:{prototype_revision or 'current'}",
                }
            )
            parent_ref = prototype_ref
        automation_status = str(automation.get("status") or "not_started")
        automation_ref = f"automation:{object_id}:{automation.get('snapshot_task_id') or automation.get('head_task_id') or 'current'}"
        if automation_status != "not_started" or str(change.get("route") if change else "") in {"automation_direct", "implementation_direct"}:
            nodes.append(
                {
                    "ref": automation_ref,
                    "kind": "automation",
                    "parent_ref": parent_ref,
                    "label": f"Automation {automation.get('result_version') or automation.get('iteration') or 'current'}",
                    "status": automation_status,
                    "source_ref": prototype_ref if object_type == "scenario" else None,
                    "preview": f"active:{object_id}:current" if automation_status in {"completed", "frozen", "adapting"} else None,
                }
            )
            parent_ref = automation_ref
        if automation_status == "completed" or state in {
            "verification",
            "trial_ready",
            "trial_waiting",
            "trial_review",
            "publication_ready",
            "publication_waiting",
            "published",
        }:
            verification_ref = f"verification:{object_id}:{automation.get('head_task_id') or 'current'}"
            nodes.append(
                {
                    "ref": verification_ref,
                    "kind": "verification",
                    "parent_ref": parent_ref,
                    "label": "Verification",
                    "status": "reviewing" if state == "verification" else "accepted",
                    "source_ref": automation_ref,
                }
            )
            parent_ref = verification_ref
        delivery_status = str(delivery.get("status") or "idle")
        if delivery_status not in {"idle", "stale"}:
            trial_ref = f"trial:{delivery.get('candidate_id') or object_id}"
            # Delivery continues beyond Trial, but the Trial lineage node must
            # retain its own business outcome. A later Publication state is not
            # a second Trial status.
            trial_status = {
                "accepted": "accepted",
                "published": "accepted",
                "rejected": "rejected",
                "trial": "reviewing",
                "prepared": "active",
                "preparing": "working",
            }.get(delivery_status, delivery_status)
            nodes.append(
                {
                    "ref": trial_ref,
                    "kind": "trial",
                    "parent_ref": parent_ref,
                    "label": f"Trial {delivery.get('candidate_id') or ''}".strip(),
                    "status": trial_status,
                    "candidate_digest": delivery.get("package_digest") or delivery.get("release_digest"),
                }
            )
            parent_ref = trial_ref
            trial_placement = active_project_placement(
                [dict(item) for item in project.get("placements") or [] if isinstance(item, Mapping)],
                kind="trial",
            )
            if trial_placement:
                trial_webspace = str(_mapping(trial_placement.get("target")).get("webspace_id") or "")
                trial_placement_ref = f"placement:{trial_placement['placement_id']}"
                nodes.append(
                    {
                        "ref": trial_placement_ref,
                        "kind": "placement",
                        "parent_ref": parent_ref,
                        "label": f"Trial placement {trial_webspace}".strip(),
                        "status": "active",
                        "source_ref": trial_ref,
                    }
                )
        if str(publication.get("status") or "") == "published":
            version = str(publication.get("current_version") or "current")
            nodes.append(
                {
                    "ref": f"publication:{object_id}:{version}",
                    "kind": "publication",
                    "parent_ref": parent_ref,
                    "label": f"Publication {version}",
                    "status": "published",
                    "preview": f"public:{object_id}:{version}",
                }
            )
            publication_ref = f"publication:{object_id}:{version}"
            installed_release = _mapping(project.get("installed_release_ref"))
            if installed_release:
                installation_ref = f"workspace-installation:{object_id}:{version}"
                nodes.append(
                    {
                        "ref": installation_ref,
                        "kind": "workspace_installation",
                        "parent_ref": publication_ref,
                        "label": f"Workspace installation {version}",
                        "status": "installed",
                        "source_ref": publication_ref,
                    }
                )
                placement_parent = installation_ref
            else:
                placement_parent = publication_ref
            stable_placement = active_project_placement(
                [dict(item) for item in project.get("placements") or [] if isinstance(item, Mapping)],
                kind="stable",
            )
            if stable_placement:
                webspace_id = str(_mapping(stable_placement.get("target")).get("webspace_id") or "")
                nodes.append(
                    {
                        "ref": f"placement:{stable_placement['placement_id']}",
                        "kind": "placement",
                        "parent_ref": placement_parent,
                        "label": f"Webspace {webspace_id}",
                        "status": "active",
                        "source_ref": publication_ref,
                    }
                )
        interaction = _mapping(workflow.get("interaction"))
        preview_options = [
            {"kind": item["kind"], "ref": item["ref"], "label": item["preview"]}
            for item in nodes
            if item.get("preview")
        ]
        return {
            "schema": "adaos.builder.process_projection.v1",
            "project_ref": project_ref,
            "workflow_state": state,
            "generation": int(description.get("generation") or _mapping(workflow.get("governed")).get("generation") or 0),
            "nodes": nodes,
            "conversation_focus": interaction.get("conversation_focus"),
            "inspected_ref": interaction.get("inspected_ref"),
            "preview_target": interaction.get("preview_target"),
            "data_mode": str(_mapping(workflow.get("data_binding")).get("selected_mode") or "mock"),
            "preview_options": preview_options,
            "allowed_commands": copy.deepcopy(description.get("allowed_commands") or []),
            "blockers": copy.deepcopy(description.get("blockers") or []),
        }

    def interaction_frame(
        self,
        object_type: str,
        object_id: str,
        *,
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Project the current workflow into channel-neutral deterministic actions."""

        projection = self.describe(object_type, object_id)
        generation = int(projection.get("generation") or 0)
        project_ref = f"{projection['object_type']}:{projection['object_id']}"
        interaction = _mapping(projection.get("interaction"))
        change = _normalize_change(projection.get("change") or projection.get("change_set"))
        active_phase = str(projection.get("active_phase") or "prototype")
        delivery_status = str(_mapping(projection.get("delivery")).get("status") or "idle")
        automation_status = str(
            _mapping(projection.get("automation")).get("status") or "not_started"
        )
        workflow_explanation = _mapping(projection.get("workflow_description"))
        compact_explanation = self._compact_explanation(projection)
        workflow_state = str(compact_explanation.get("state") or "ready")
        canonical_generation = int(workflow_explanation.get("generation") or 0)
        canonical_action_list = [
            dict(item)
            for item in workflow_explanation.get("allowed_commands") or []
            if isinstance(item, Mapping) and str(item.get("command") or "").strip()
        ]
        canonical_priority = {
            "extend_with_prototype_issues": 10,
            "extend_with_automation_issues": 10,
            "plan_prototype_change": 20,
            "plan_automation_change": 20,
            "record_prototype_revision": 30,
            "revise_prototype": 30,
            "accept_prototype": 40,
            "start_automation": 50,
            "retry_automation": 50,
            "request_prototype_derivation": 60,
            "accept_verification": 70,
            "start_trial": 80,
            "accept_trial": 90,
            "reject_trial": 90,
            "begin_publication": 100,
        }
        canonical_action_list.sort(
            key=lambda item: canonical_priority.get(str(item.get("command") or ""), 500)
        )
        canonical_actions = {
            str(item.get("command") or ""): item for item in canonical_action_list
        }

        actions: list[dict[str, Any]] = []
        selected_locale = normalize_builder_locale(locale)

        def add_action(
            command: str,
            label: str,
            risk: str,
            *,
            target_ref: str | None = None,
            presentation: str = "button",
            fallback: str = "compact_action",
            workflow_command: str | None = None,
        ) -> None:
            if workflow_command and workflow_command not in canonical_actions:
                return
            canonical = canonical_actions.get(str(workflow_command or ""))
            canonical_risk = str(_mapping((canonical or {}).get("risk")).get("class") or risk)
            translated_label = builder_action_label(
                command,
                locale=selected_locale,
                fallback=label,
            )
            actions.append(
                build_builder_action(
                    command,
                    translated_label,
                    canonical_risk,
                    expected_generation=generation,
                    target_ref=target_ref,
                    presentation=presentation,
                    fallback=fallback,
                    workflow_command=workflow_command,
                    workflow_generation=canonical_generation if workflow_command else None,
                    label_ref=builder_action_label_ref(command),
                )
            )
        change_ref = f"change:{change['change_id']}" if change else project_ref
        candidate = _mapping(projection.get("delivery"))
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        candidate_digest = str(
            candidate.get("package_digest") or candidate.get("release_digest") or ""
        ).strip()
        candidate_ref = (
            f"candidate:{candidate_id}@{candidate_digest}"
            if candidate_id and candidate_digest
            else f"candidate:{candidate_id}"
            if candidate_id
            else change_ref
        )
        # This table is presentation-only. Availability and ordering come
        # exclusively from WorkflowDescription.allowed_commands, including
        # role policy, guards, generation, and executor readiness.
        canonical_surface = {
            "plan_prototype_change": ("builder.change.plan", "Plan change", "local_reversible", project_ref),
            "plan_automation_change": ("builder.change.plan", "Plan change", "local_reversible", project_ref),
            "extend_with_prototype_issues": ("builder.change.extend", "Add requirement", "local_reversible", change_ref),
            "extend_with_automation_issues": ("builder.change.extend", "Add requirement", "local_reversible", change_ref),
            "record_prototype_revision": ("builder.prototype.edit", "Correct prototype", "local_reversible", change_ref),
            "revise_prototype": ("builder.prototype.edit", "Correct prototype", "local_reversible", change_ref),
            "accept_prototype": ("builder.prototype.approve", "Approve prototype", "isolated_write", change_ref),
            "start_automation": ("builder.implementation.start", "Start implementation", "isolated_write", change_ref),
            "retry_automation": ("builder.implementation.iterate", "Continue implementation", "isolated_write", change_ref),
            "request_prototype_derivation": ("builder.prototype.derive", "Return result to prototype", "isolated_write", change_ref),
            "accept_verification": ("builder.verification.accept", "Accept verification", "isolated_write", change_ref),
            "start_trial": ("builder.trial.prepare", "Start trial", "trial_activation", change_ref),
            "accept_trial": ("builder.trial.accept", "Accept trial", "workspace_activation", candidate_ref),
            "reject_trial": ("builder.trial.reject", "Request changes", "local_reversible", candidate_ref),
            "begin_publication": ("builder.publication.publish", "Begin publication", "publication", candidate_ref),
        }
        seen_surface_commands: set[str] = set()
        for canonical in canonical_action_list:
            workflow_command = str(canonical.get("command") or "")
            surface = canonical_surface.get(workflow_command)
            if surface is None:
                continue
            surface_command, label, risk, target = surface
            # A published result starts a fresh Change; it must not expose a
            # stale lifecycle continuation as though publication were Preview.
            if workflow_state == "published" and surface_command == "builder.change.plan":
                continue
            if surface_command in seen_surface_commands:
                continue
            seen_surface_commands.add(surface_command)
            add_action(
                surface_command,
                label,
                risk,
                target_ref=target,
                workflow_command=workflow_command,
            )

        # Project commands live above an individual Change statechart. They
        # remain authoritative Project-aggregate commands and are added only
        # when the aggregate exposes them; they never make a lifecycle
        # transition appear ready.
        project_commands = [
            _mapping(item)
            for item in _mapping(projection.get("project_summary")).get("commands") or []
            if isinstance(item, Mapping)
        ]
        if (
            workflow_state != "published"
            and
            any(str(item.get("command") or "") == "builder.change.plan" for item in project_commands)
            and "builder.change.plan" not in seen_surface_commands
        ):
            add_action(
                "builder.change.plan",
                "Plan new change" if change else "Plan change",
                "local_reversible",
                target_ref=project_ref,
            )

        project = _mapping(projection.get("project"))
        project_placements = [
            dict(item) for item in project.get("placements") or [] if isinstance(item, Mapping)
        ]
        stable_placement = active_project_placement(project_placements, kind="stable")
        trial_placement = active_project_placement(project_placements, kind="trial")
        if workflow_state == "published":
            if stable_placement:
                add_action(
                    "builder.publication.open",
                    "Open published project",
                    "read",
                    target_ref=f"placement:{stable_placement['placement_id']}",
                )
            else:
                add_action(
                    "builder.publication.place",
                    "Place in Webspace",
                    "workspace_activation",
                    target_ref=project_ref,
                )
        elif trial_placement:
            add_action(
                "builder.trial.open",
                "Open trial",
                "read",
                target_ref=f"placement:{trial_placement['placement_id']}",
            )

        preview_options = {
            str(item.get("stage") or ""): dict(item)
            for item in _mapping(projection.get("process")).get("preview_options") or []
            if isinstance(item, Mapping)
        }
        if workflow_state != "published" and "prototype" in preview_options:
            add_action(
                "builder.preview.prototype",
                "Preview prototype",
                "read",
                target_ref=f"prototype:{projection['object_id']}:{_mapping(projection.get('prototype')).get('head_revision') or 'current'}",
            )
        if workflow_state != "published" and "automation" in preview_options:
            add_action(
                "builder.preview.active",
                "Preview implementation",
                "read",
                target_ref=f"implementation:{projection['object_id']}:active",
            )
        if workflow_state != "published" and "publication" in preview_options:
            add_action(
                "builder.preview.publication",
                "Preview publication",
                "read",
                target_ref=f"publication:{projection['object_id']}:{_mapping(projection.get('publication')).get('current_version') or 'current'}",
            )

        # Optional inspection and navigation are deliberately last.  Limited
        # channels therefore retain the primary dependent continuation when
        # their button budget truncates the presentation.
        add_action(
            "builder.process.inspect",
            "Show process",
            "read",
            target_ref=change and f"change:{change['change_id']}" or project_ref,
            presentation="panel",
            fallback="compact_status",
        )
        if workflow_state == "published":
            add_action(
                "builder.change.plan",
                "Refine project",
                "local_reversible",
                target_ref=project_ref,
            )
        add_action("builder.project.list", "Show projects", "read", target_ref=project_ref)
        if workflow_state == "published" and actions:
            actions[-1]["label"] = "Сменить проект" if selected_locale == "ru" else "Change project"
        if workflow_state != "published":
            add_action("builder.preview.link", "Preview link", "read", target_ref=project_ref)
        add_action("builder.help", "Help", "read", target_ref=project_ref)

        views = [
            {"kind": "conversation", "presentation": "primary", "fallback": "messages"},
            {"kind": "process", "presentation": "panel", "fallback": "compact_status"},
            {"kind": "overview", "presentation": "panel", "fallback": "deep_link"},
            {"kind": "artifacts", "presentation": "panel", "fallback": "deep_link"},
            {"kind": "preview", "presentation": "adjacent", "fallback": "deep_link"},
        ]
        return {
            "schema": BUILDER_INTERACTION_FRAME_SCHEMA,
            "message": localize_builder_explanation(
                compact_explanation,
                locale=selected_locale,
            ),
            "context": {
                "project_ref": project_ref,
                "change_ref": f"change:{change['change_id']}" if change else None,
                "conversation_focus": interaction.get("conversation_focus"),
                "inspected_ref": interaction.get("inspected_ref"),
                "preview_target": interaction.get("preview_target"),
            },
            "status": {
                "phase": active_phase,
                "change": change.get("status") if change else None,
                "gate": change.get("gate") if change else None,
                "implementation": automation_status,
                "delivery": delivery_status,
                "data_mode": str(_mapping(projection.get("data_binding")).get("selected_mode") or "mock"),
                "workflow_state": workflow_state,
                "reason": compact_explanation["reason"],
                "next_commands": compact_explanation["next_commands"],
            },
            "actions": actions,
            "views": views,
            "generation": generation,
            "locale_context": builder_surface_locale_context(selected_locale),
        }

    def conversation_interaction(
        self,
        object_type: str,
        object_id: str,
        *,
        conversation_id: str,
        principal_id: str,
        command_context_id: str,
        prompt: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Project the same canonical commands into the shared interaction protocol."""

        projection = self.describe(object_type, object_id)
        selected_locale = normalize_builder_locale(locale)
        frame = self.interaction_frame(object_type, object_id, locale=selected_locale)
        governed = _mapping(projection.get("governed"))
        workflow_ref = {
            "schema": "adaos.workflow.ref.v1",
            "kind": "workflow",
            "id": str(governed.get("instance_id") or ""),
            "version": str(governed.get("definition_version") or ""),
            "generation": int(governed.get("generation") or 0),
        }
        context_ref = {
            "schema": "adaos.workflow.ref.v1",
            "kind": "view",
            "id": command_context_id,
        }
        input_only = {
            "builder.change.plan",
            "builder.change.extend",
            "builder.prototype.edit",
            "builder.implementation.iterate",
        }

        def target_ref(value: Any) -> dict[str, Any] | None:
            token = str(value or "").strip()
            if not token:
                return None
            kind, separator, identifier = token.partition(":")
            return {
                "schema": "adaos.workflow.ref.v1",
                "kind": kind if separator else "builder_target",
                "id": identifier if separator else token,
            }

        actions: list[dict[str, Any]] = []
        for index, raw in enumerate(frame.get("actions") or []):
            item = _mapping(raw)
            surface_command = str(item.get("command") or "").strip()
            workflow_command = str(item.get("workflow_command") or "").strip()
            command = (
                workflow_command
                if workflow_command and surface_command not in input_only
                else surface_command
            )
            policy = _mapping(item.get("risk_policy"))
            actions.append(
                {
                    "action_id": f"builder:{index}:{surface_command}",
                    "label": str(item.get("label") or command),
                    "label_ref": str(item.get("label_ref") or "").strip() or None,
                    "command": command,
                    "value": surface_command,
                    "risk": str(item.get("risk") or "read"),
                    "confirmation_required": bool(policy.get("confirmation_required")),
                    "target_ref": target_ref(item.get("target_ref")),
                    "expected_generation": int(
                        item.get("workflow_generation")
                        if workflow_command and surface_command not in input_only
                        else governed.get("generation") or 0
                    ),
                    "principal_scope": ["user", "transport"],
                    "command_context_ref": context_ref,
                }
            )
        interaction = create_interaction(
            conversation_id=conversation_id,
            owner=principal_id,
            thread_id=command_context_id,
            workflow_ref=workflow_ref,
            prompt=prompt or frame["message"],
            prompt_ref="builder.prompt.current_state",
            locale_context=builder_surface_locale_context(selected_locale),
            input_spec={
                "kind": "choice",
                "required_fields": [],
                "choices": [
                    {
                        "value": str(item["value"]),
                        "label": str(item["label"]),
                        "description": None,
                    }
                    for item in actions
                ],
                "sensitive": False,
            },
            actions=actions,
            optional_capabilities=("buttons",),
            fallbacks=("numbered_text", "plain_text", "unsupported"),
            metadata={
                "domain": "builder",
                "project_ref": f"{projection['object_type']}:{projection['object_id']}",
                "process_generation": _mapping(projection.get("process")).get("generation"),
                "workflow_type": str(governed.get("workflow_type") or "builder.change"),
                "surface_commands": {
                    str(item["action_id"]): str(
                        next(
                            (
                                source.get("command")
                                for source in frame.get("actions") or []
                                if isinstance(source, Mapping)
                                and str(source.get("label_ref") or "") == str(item.get("label_ref") or "")
                            ),
                            item["command"],
                        )
                    )
                    for item in actions
                },
                **copy.deepcopy(dict(metadata or {})),
            },
        )
        return interaction

    def conversation_input_interaction(
        self,
        object_type: str,
        object_id: str,
        *,
        surface_command: str,
        conversation_id: str,
        principal_id: str,
        command_context_id: str,
        metadata: Mapping[str, Any] | None = None,
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable input-required continuation for a Builder affordance."""

        command = str(surface_command or "").strip()
        input_commands = {
            "builder.change.plan",
            "builder.change.extend",
            "builder.prototype.edit",
            "builder.implementation.iterate",
            "builder.publication.place",
        }
        if command not in input_commands:
            raise BuilderWorkflowError(f"Builder command does not accept conversational input: {command}")
        projection = self.describe(object_type, object_id)
        selected_locale = normalize_builder_locale(locale)
        frame = self.interaction_frame(object_type, object_id, locale=selected_locale)
        if not any(str(item.get("command") or "") == command for item in frame.get("actions") or []):
            raise BuilderWorkflowError(f"Builder command is not available in the current state: {command}")
        governed = _mapping(projection.get("governed"))
        workflow_ref = {
            "schema": "adaos.workflow.ref.v1",
            "kind": "workflow",
            "id": str(governed.get("instance_id") or ""),
            "version": str(governed.get("definition_version") or ""),
            "generation": int(governed.get("generation") or 0),
        }
        return create_interaction(
            conversation_id=conversation_id,
            owner=principal_id,
            thread_id=command_context_id,
            workflow_ref=workflow_ref,
            prompt=builder_input_prompt(command, locale=selected_locale),
            prompt_ref=f"builder.prompt.{command.removeprefix('builder.').replace('.', '_')}",
            locale_context=builder_surface_locale_context(selected_locale),
            input_spec={
                "kind": "text",
                "required_fields": ["text"],
                "choices": [],
                "sensitive": False,
            },
            actions=[],
            fallbacks=("plain_text", "unsupported"),
            metadata={
                "domain": "builder",
                "project_ref": f"{projection['object_type']}:{projection['object_id']}",
                "continuation": {
                    "surface_command": command,
                    "expected_generation": int(projection.get("generation") or 0),
                    "workflow_generation": int(governed.get("generation") or 0),
                },
                **copy.deepcopy(dict(metadata or {})),
            },
        )

    def invoke_interaction_response(
        self,
        object_type: str,
        object_id: str,
        response: Mapping[str, Any],
        *,
        actor: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Admit one validated InteractionResponse through the canonical Builder ingress."""

        try:
            invocation = prepare_interaction_invocation(response)
        except ValueError as exc:
            raise BuilderWorkflowError(f"Builder interaction invocation is invalid: {exc}") from exc
        return self._invoke_prepared_command(
            object_type,
            object_id,
            invocation,
            actor=actor,
            metadata=metadata,
        )

    def invoke_command(
        self,
        object_type: str,
        object_id: str,
        command: str,
        *,
        actor: str,
        idempotency_key: str,
        input_value: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke an SDK command through the same normalized ingress as chat."""

        current = self.describe(object_type, object_id)
        canonical = _mapping(current.get("governed"))
        command_projection = next(
            (
                _mapping(item)
                for item in _mapping(current.get("workflow_description")).get("allowed_commands") or []
                if isinstance(item, Mapping) and str(item.get("command") or "") == str(command)
            ),
            {},
        )
        if not command_projection:
            blocked = next(
                (
                    _mapping(item)
                    for item in _mapping(current.get("workflow_description")).get("blocked_commands") or []
                    if isinstance(item, Mapping) and str(item.get("command") or "") == str(command)
                ),
                {},
            )
            raise BuilderWorkflowError(
                f"Builder command is unavailable: {command} "
                f"({blocked.get('reason_code') or 'command_not_allowed'})"
            )
        risk = _mapping(command_projection.get("risk"))
        invocation = prepare_sdk_invocation(
            workflow_type=str(canonical.get("workflow_type") or ""),
            instance_ref={
                "schema": "adaos.workflow.ref.v1",
                "kind": "workflow",
                "id": str(canonical.get("instance_id") or ""),
                "version": str(canonical.get("definition_version") or ""),
                "generation": int(canonical.get("generation") or 0),
                "digest": str(canonical.get("definition_digest") or "").strip() or None,
            },
            actor_id=actor,
            command_id=command,
            expected_generation=int(canonical.get("generation") or 0),
            idempotency_key=idempotency_key,
            input_value=input_value,
            target_ref=_mapping(current.get("workflow_description")).get("target"),
            context_ref={
                "schema": "adaos.workflow.ref.v1",
                "kind": "command_context",
                "id": f"sdk:{object_type}:{object_id}",
            },
            risk=str(risk.get("class") or "read"),
            confirmation_required=str(risk.get("confirmation") or "none") != "none",
        )
        return self._invoke_prepared_command(
            object_type,
            object_id,
            invocation,
            actor=actor,
            metadata=metadata,
        )

    def _invoke_prepared_command(
        self,
        object_type: str,
        object_id: str,
        invocation: Mapping[str, Any],
        *,
        actor: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        command_record = _mapping(invocation.get("command"))
        command = str(command_record.get("command_id") or "").strip()
        from adaos.sdk.builder import lifecycle as builder_lifecycle

        if command in builder_lifecycle.ACTIVITY_COMMANDS:
            result = builder_lifecycle.invoke_activity_command(
                command,
                object_type,
                object_id,
                actor=actor,
                idempotency_key=str(command_record.get("idempotency_key") or "").strip(),
                input_value=_mapping(command_record.get("input")),
                metadata={
                    **dict(metadata or {}),
                    "conversation_id": invocation.get("conversation_id"),
                },
            )
            output = dict(result)
            output.setdefault("ok", True)
            output.setdefault("workflow", self.describe(object_type, object_id))
            output["invocation"] = copy.deepcopy(dict(invocation))
            return output
        action = legacy_action_for_command(command)
        if action is None:
            raise BuilderWorkflowError(f"Builder command has no compatibility activity adapter: {command}")
        current = self.describe(object_type, object_id)
        canonical = _mapping(current.get("governed"))
        instance_ref = _mapping(command_record.get("instance_ref"))
        if str(instance_ref.get("id") or "") != str(canonical.get("instance_id") or ""):
            raise BuilderWorkflowError("Builder command targets another workflow instance")
        if str(command_record.get("actor_ref", {}).get("id") or "") != str(actor or ""):
            raise BuilderWorkflowError("Builder command actor differs from the verified caller")
        expected = int(command_record.get("expected_generation") or 0)
        if int(canonical.get("generation") or 0) != expected:
            raise BuilderWorkflowError(
                f"stale Builder interaction command: expected {expected}, "
                f"current {int(canonical.get('generation') or 0)}"
            )
        details = {
            **dict(metadata or {}),
            **_mapping(command_record.get("input")),
            "idempotency_key": str(command_record.get("idempotency_key") or "").strip(),
            "workflow_invocation_id": invocation.get("invocation_id"),
            "interaction_response_id": _mapping(invocation.get("response_ref")).get("id"),
        }
        result = self.transition(
            object_type,
            object_id,
            action,
            actor=actor,
            metadata=details,
        )
        result["invocation"] = copy.deepcopy(dict(invocation))
        return result

    def update_interaction_context(
        self,
        object_type: str,
        object_id: str,
        updates: Mapping[str, Any],
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        """Update focus, inspection, or Preview independently with optimistic locking."""

        allowed = {"conversation_focus", "inspected_ref", "preview_target"}
        unknown = set(updates) - allowed
        if unknown:
            raise BuilderWorkflowError(
                f"unsupported Builder interaction fields: {', '.join(sorted(unknown))}"
            )
        if not updates:
            raise BuilderWorkflowError("at least one Builder interaction field is required")
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            current_generation = int(workflow.get("generation") or 0)
            if current_generation != int(expected_generation):
                raise BuilderWorkflowError(
                    f"stale Builder action generation: expected {expected_generation}, current {current_generation}"
                )
            interaction = _mapping(workflow.get("interaction"))
            for key, value in updates.items():
                token = str(value or "").strip()
                if len(token) > 300:
                    raise BuilderWorkflowError(f"{key} exceeds 300 characters")
                interaction[key] = token or None
            if not interaction.get("conversation_focus"):
                interaction["conversation_focus"] = f"{kind}:{project_id}"
            workflow["interaction"] = interaction
            conversation_focus = str(interaction.get("conversation_focus") or "")
            if conversation_focus.startswith("change:"):
                change_id = conversation_focus.split(":", 1)[1]
                try:
                    workflow["project"] = set_focus(
                        _mapping(workflow.get("project")),
                        "default",
                        change_id,
                    )
                except ValueError as exc:
                    raise BuilderWorkflowError(str(exc)) from exc
                portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
                record = portfolio.get(change_id)
                if not isinstance(record, Mapping):
                    raise BuilderWorkflowError(f"Builder Change state is unavailable: {change_id}")
                restore_compatibility_record(workflow, record)
                workflow["change_portfolio"] = portfolio
            workflow["generation"] = current_generation + 1
            workflow["updated_at"] = _now()
            state["workflow"] = workflow
            state["updated_at"] = workflow["updated_at"]
            self._write_state(kind, project_id, state)
        projection = self.describe(kind, project_id)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "workflow": projection, "interaction_frame": self.interaction_frame(kind, project_id)}

    def transition(
        self,
        object_type: str,
        object_id: str,
        action: str,
        *,
        actor: str = "builder",
        reason: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        action_token = str(action or "").strip().lower()
        details = dict(metadata or {})
        changed_at = _now()
        with _LOCK:
            state = self._read_state(kind, project_id)
            if bool(state.get("archived")):
                raise BuilderWorkflowError("archived projects cannot change workflow")
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            originating_change_id = str(details.get("originating_change_id") or "").strip()
            scoped_original_record: dict[str, Any] | None = None
            if originating_change_id and action_token != "plan_change_set":
                current = _normalize_change(workflow.get("change") or workflow.get("change_set"))
                current_id = str((current or {}).get("change_id") or "")
                if current_id != originating_change_id:
                    portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
                    target_record = portfolio.get(originating_change_id)
                    if not isinstance(target_record, Mapping):
                        raise BuilderWorkflowError(
                            f"originating Builder Change is unavailable: {originating_change_id}"
                        )
                    scoped_original_record = capture_compatibility_record(workflow)
                    restore_compatibility_record(workflow, target_record)
                    workflow["change_portfolio"] = portfolio
            parallel_plan = bool(details.get("parallel")) and action_token == "plan_change_set"
            current_change = _normalize_change(workflow.get("change") or workflow.get("change_set"))
            if (
                parallel_plan
                and current_change
                and str(current_change.get("status") or "") not in _CHANGE_SET_TERMINAL_STATES
            ):
                portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
                current_record = capture_compatibility_record(workflow)
                if current_record:
                    portfolio[current_record["change_id"]] = current_record
                workflow["change_portfolio"] = portfolio
                workflow["change"] = None
                workflow["change_set"] = None
                workflow["context_packet"] = None
                workflow["governed"] = None
                workflow["pending_transition"] = None
                workflow["active_phase"] = "prototype"
                workflow["prototype"] = {
                    **_mapping(workflow.get("prototype")),
                    "status": "working",
                    "stable": False,
                }
                workflow["automation"] = {
                    "status": "not_started",
                    "iteration": 0,
                    "source_prototype_revision": _mapping(workflow.get("prototype")).get(
                        "head_revision"
                    ),
                }
                workflow["delivery"] = {"status": "idle"}
            if expected_generation is not None and int(workflow.get("generation") or 0) != int(expected_generation):
                raise BuilderWorkflowError(
                    f"stale Builder action generation: expected {expected_generation}, "
                    f"current {int(workflow.get('generation') or 0)}"
                )
            if action_token in {"automation_started", "handoff_to_automation"}:
                mapping_report = implementation_mapping_report(
                    _mapping(workflow.get("data_binding"))
                )
                if not bool(mapping_report.get("ready")):
                    missing = ", ".join(mapping_report.get("missing") or [])
                    raise BuilderWorkflowError(
                        f"Prototype data contracts require implementation mappings: {missing}"
                    )
            mutation_started = False
            mutation_change_id = str(
                (_normalize_change(workflow.get("change") or workflow.get("change_set")) or {}).get(
                    "change_id"
                )
                or ""
            )
            if mutation_change_id and action_token in {
                *_PROJECT_MUTATION_START_ACTIONS,
                *_PROJECT_ATOMIC_MUTATION_ACTIONS,
            }:
                project = _mapping(workflow.get("project"))
                summary = next(
                    (
                        item
                        for item in project.get("changes") or []
                        if isinstance(item, Mapping) and item.get("change_id") == mutation_change_id
                    ),
                    None,
                )
                if not isinstance(summary, Mapping):
                    raise BuilderWorkflowError("Builder Project does not contain the focused Change")
                try:
                    workflow["project"] = begin_mutation(
                        project,
                        mutation_change_id,
                        expected_project_generation=int(
                            details.get("expected_project_generation")
                            if details.get("expected_project_generation") is not None
                            else project.get("generation") or 0
                        ),
                        expected_base_generation=int(
                            details.get("expected_base_generation")
                            if details.get("expected_base_generation") is not None
                            else summary.get("base_generation") or 0
                        ),
                        now=changed_at,
                    )
                except BuilderProjectError as exc:
                    raise BuilderWorkflowError(str(exc)) from exc
                mutation_started = True
            before = {
                "active_phase": workflow["active_phase"],
                "prototype_status": workflow["prototype"].get("status"),
                "automation_status": workflow["automation"].get("status"),
                "delivery_status": workflow["delivery"].get("status"),
                "publication_status": workflow["publication"].get("status"),
                "change_set_status": (workflow.get("change_set") or {}).get("status"),
                "change_set_gate": (workflow.get("change_set") or {}).get("gate"),
            }
            governed_decision = None
            if action_token == "plan_change_set" or workflow.get("change") or workflow.get("change_set"):
                definition = self._governed_definition()
                governed_decision = admit_legacy_transition(
                    workflow,
                    action_token,
                    details,
                    project_ref=f"{kind}:{project_id}",
                    actor=str(actor or "builder"),
                    idempotency_key=str(details.get("idempotency_key") or "").strip()
                    or f"legacy:{kind}:{project_id}:{int(workflow.get('generation') or 0)}:{action_token}",
                    now=changed_at,
                    definition=definition,
                    package_digest=self._active_package_digest,
                    binding_digest=self._active_binding_digest,
                )
                if governed_decision is not None and not bool(governed_decision.get("accepted")):
                    raise BuilderWorkflowError(
                        "canonical Builder transition rejected: "
                        f"{governed_decision.get('reason_code') or 'transition_not_allowed'}"
                    )
            self._apply_transition(
                workflow,
                action_token,
                details,
                changed_at=changed_at,
                project_id=project_id,
            )
            if governed_decision is not None:
                workflow["governed"] = copy.deepcopy(governed_decision["after"])
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = changed_at
            self._record_transition_run(
                workflow,
                action=action_token,
                actor=str(actor or "builder"),
                metadata=details,
                changed_at=changed_at,
                project_ref=f"{kind}:{project_id}",
            )
            portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
            workflow["change_portfolio"] = portfolio
            previous_project = _mapping(workflow.get("project"))
            manifest_metadata = self._project_manifest_metadata(kind, project_id)
            project = normalize_project(
                previous_project,
                object_type=kind,
                object_id=project_id,
                archived=False,
                workflow=workflow,
                title=manifest_metadata["title"],
                description=manifest_metadata["description"],
                now=changed_at,
            )
            project["generation"] = int(previous_project.get("generation") or 0) + 1
            current_after = _normalize_change(workflow.get("change") or workflow.get("change_set"))
            if current_after:
                focus = _mapping(project.get("focus_by_context"))
                focus["default"] = current_after["change_id"]
                project["focus_by_context"] = focus
            finish_policy = _PROJECT_MUTATION_FINISH_ACTIONS.get(action_token)
            if mutation_change_id and (finish_policy is not None or mutation_started and action_token in _PROJECT_ATOMIC_MUTATION_ACTIONS):
                outcome_unknown, advance_base = finish_policy or (False, True)
                try:
                    project = finish_mutation(
                        project,
                        mutation_change_id,
                        outcome_unknown=outcome_unknown,
                        advance_base=advance_base,
                        now=changed_at,
                    )
                except BuilderProjectError as exc:
                    raise BuilderWorkflowError(str(exc)) from exc
                current_change_value = _normalize_change(
                    workflow.get("change") or workflow.get("change_set")
                )
                target_summary = next(
                    (
                        item
                        for item in project.get("changes") or []
                        if isinstance(item, Mapping) and item.get("change_id") == mutation_change_id
                    ),
                    None,
                )
                if current_change_value and isinstance(target_summary, Mapping):
                    current_change_value["base_generation"] = int(
                        target_summary.get("base_generation") or 0
                    )
                    current_change_value["affected_refs"] = list(
                        target_summary.get("affected_refs") or []
                    )
                    workflow["change"] = current_change_value
                    workflow["change_set"] = _change_set_compatibility(current_change_value)
                    workflow["change_portfolio"] = normalize_portfolio(
                        workflow.get("change_portfolio"), workflow
                    )
            workflow["project"] = project
            after = {
                "active_phase": workflow["active_phase"],
                "prototype_status": workflow["prototype"].get("status"),
                "automation_status": workflow["automation"].get("status"),
                "delivery_status": workflow["delivery"].get("status"),
                "publication_status": workflow["publication"].get("status"),
                "change_set_status": (workflow.get("change_set") or {}).get("status"),
                "change_set_gate": (workflow.get("change_set") or {}).get("gate"),
            }
            history = list(workflow.get("history") or [])
            history.append(
                {
                    "generation": workflow["generation"],
                    "action": action_token,
                    "actor": str(actor or "builder"),
                    "reason": str(reason or "").strip() or None,
                    "at": changed_at,
                    "before": before,
                    "after": after,
                    "metadata": details,
                    "canonical": {
                        "command": governed_decision.get("command"),
                        "transition_id": governed_decision.get("transition_id"),
                        "generation": _mapping(governed_decision.get("after")).get("generation"),
                    }
                    if governed_decision is not None
                    else None,
                }
            )
            workflow["history"] = history[-_MAX_HISTORY:]
            if scoped_original_record is not None:
                portfolio = normalize_portfolio(workflow.get("change_portfolio"), workflow)
                restore_compatibility_record(workflow, scoped_original_record)
                workflow["change_portfolio"] = portfolio
                focus = _mapping(project.get("focus_by_context"))
                focus["default"] = scoped_original_record["change_id"]
                project["focus_by_context"] = focus
                workflow["project"] = project
            state["workflow"] = workflow
            state["workflow_state"] = workflow["active_phase"]
            state["updated_at"] = changed_at
            self._write_state(kind, project_id, state)

        projection = {
            **copy.deepcopy(workflow),
            "object_type": kind,
            "object_id": project_id,
            "archived": False,
            "capabilities": self._capabilities(workflow, archived=False, object_type=kind),
        }
        projection["workflow_description"] = description_with_executor_readiness(
            workflow_description(
                workflow,
                project_ref=f"{kind}:{project_id}",
                definition=self._governed_definition(),
            ),
            self._governed_definition(),
            self._executor_registry(),
        )
        projection["workflow_inspection"] = self._workflow_inspection(kind, project_id)
        projection["process"] = self._process_projection(projection)
        projection["project_summary"] = self._project_summary(projection)
        if callable(self.event_sink):
            self.event_sink(projection)
        return {
            "ok": True,
            "action": action_token,
            "updated_change_id": originating_change_id or mutation_change_id or None,
            "workflow": projection,
        }

    def _record_transition_run(
        self,
        workflow: dict[str, Any],
        *,
        action: str,
        actor: str,
        metadata: Mapping[str, Any],
        changed_at: str,
        project_ref: str,
    ) -> None:
        legacy = _normalize_change_set(workflow.get("change_set"))
        if legacy is None:
            workflow["change"] = None
            return
        previous = _normalize_change(workflow.get("change"))
        change_id = str(legacy.get("change_set_id") or "").strip()
        if previous and str(previous.get("change_id") or "") != change_id:
            previous = None
        change = {
            **(previous or {}),
            **legacy,
            "schema": BUILDER_CHANGE_SCHEMA,
            "change_id": change_id,
            "change_set_id": change_id,
            "project_ref": str((previous or {}).get("project_ref") or project_ref),
            "base_generation": max(
                0,
                int(
                    metadata.get("base_generation")
                    if metadata.get("base_generation") is not None
                    else (previous or {}).get("base_generation") or 0
                ),
            ),
            "affected_refs": list(
                dict.fromkeys(
                    str(item).strip()
                    for item in metadata.get("affected_refs")
                    or (previous or {}).get("affected_refs")
                    or []
                    if str(item).strip()
                )
            )[:500],
            "teacher_candidate_refs": [
                copy.deepcopy(dict(item))
                for item in metadata.get("teacher_candidate_refs")
                or (previous or {}).get("teacher_candidate_refs")
                or []
                if isinstance(item, Mapping)
            ][-100:],
            "promotion_privacy_scope": str(
                metadata.get("promotion_privacy_scope")
                or (previous or {}).get("promotion_privacy_scope")
                or ""
            ).strip()
            or None,
        }
        runs = [
            _normalize_run(item, change_id=change_id)
            for item in (previous or {}).get("runs") or []
            if isinstance(item, Mapping)
        ]
        run_id = str(metadata.get("run_id") or "").strip()
        if not run_id and action in {
            "automation_started",
            "handoff_to_automation",
            "automation_iteration_started",
            "automation_completed",
            "automation_failed",
            "request_return_to_prototype",
            "return_to_prototype",
            "return_to_prototype_failed",
        }:
            run_id = str(metadata.get("task_id") or "").strip()
        if not run_id:
            run_id = f"{change_id}:run:{int(workflow.get('generation') or 0):04d}"
        failure = action.endswith("_failed") or action in {"candidate_rejected"}
        running = action.endswith("_started") or action in {"request_return_to_prototype"}
        status = "failed" if failure else ("running" if running else "succeeded")
        purpose = str(metadata.get("purpose") or "").strip().lower()
        if not purpose:
            if action in {"review_constraints_evaluated", "candidate_prepared", "candidate_accepted", "candidate_rejected"}:
                purpose = "evaluation"
            elif action in {"return_to_prototype_failed"}:
                purpose = "recovery"
            elif action in {"prototype_experiment_recorded", "adopt_experiment", "discard_experiment"}:
                purpose = "experiment"
            else:
                purpose = "iteration"
        if purpose not in {"iteration", "experiment", "evaluation", "recovery"}:
            raise BuilderWorkflowError("invalid Builder Run purpose")
        adoption_status = str(metadata.get("adoption_status") or "").strip().lower()
        if purpose == "experiment":
            if action == "adopt_experiment":
                adoption_status = "adopted"
            elif action == "discard_experiment":
                adoption_status = "discarded"
            else:
                adoption_status = adoption_status or "pending"
        else:
            adoption_status = "not_applicable"
        context_packet = workflow.get("context_packet") if isinstance(workflow.get("context_packet"), Mapping) else {}
        supplied_metrics = metadata.get("workflow_metrics")
        if supplied_metrics is not None and not isinstance(supplied_metrics, Mapping):
            raise BuilderWorkflowError("workflow_metrics must be an object")
        if isinstance(supplied_metrics, Mapping):
            metrics_evidence = validate_workflow_record(
                "adaos.workflow.metrics_evidence.v1",
                supplied_metrics,
            )
        else:
            story_reports = tuple(
                dict(item)
                for item in metadata.get("story_reports") or []
                if isinstance(item, Mapping)
            )
            measurement = (
                dict(metadata["workflow_measurement"])
                if isinstance(metadata.get("workflow_measurement"), Mapping)
                else None
            )
            metrics_evidence = workflow_metrics_evidence(
                workflow_metrics_report(
                    self._governed_definition(),
                    story_reports=story_reports,
                    context_packet=context_packet,
                    measurement=measurement,
                    report_id=f"workflow-metrics:builder-run:{run_id}",
                    generated_at=changed_at,
                )
            )
        run = {
            "schema": BUILDER_RUN_SCHEMA,
            "run_id": run_id,
            "change_id": change_id,
            "activity": action,
            "executor": str(metadata.get("executor") or actor or "builder.workflow"),
            "purpose": purpose,
            "adoption_status": adoption_status,
            "status": status,
            "context_packet_digest": str(
                metadata.get("context_packet_digest") or context_packet.get("digest") or ""
            ).strip()
            or None,
            "environment_ref": str(metadata.get("environment_ref") or "").strip() or None,
            "input_refs": [
                str(item).strip()
                for item in metadata.get("input_refs")
                or metadata.get("source_message_ids")
                or []
                if str(item).strip()
            ][-100:],
            "output_refs": [
                str(item).strip()
                for item in metadata.get("output_refs") or []
                if str(item).strip()
            ][-100:],
            "evidence_refs": [
                str(item).strip()
                for item in metadata.get("evidence_refs") or []
                if str(item).strip()
            ][-100:],
            "workflow_metrics": metrics_evidence,
            "started_at": changed_at,
            "completed_at": None if status == "running" else changed_at,
            "error": str(metadata.get("error") or "").strip() or None,
        }
        existing = next((item for item in runs if item.get("run_id") == run_id), None)
        if existing is None:
            runs.append(run)
        else:
            original_started_at = existing.get("started_at")
            existing.update(run)
            existing["started_at"] = original_started_at or changed_at
        change["runs"] = runs[-_MAX_CHANGE_RUNS:]
        change["context_packet_digest"] = str(
            context_packet.get("digest") or change.get("context_packet_digest") or ""
        ).strip() or None
        workflow["change"] = _normalize_change(change)
        workflow["change_set"] = _change_set_compatibility(workflow["change"])

    def build_context_packet(
        self,
        object_type: str,
        object_id: str,
        *,
        allowed_paths: list[str] | tuple[str, ...] | None = None,
        instruction_refs: list[str] | tuple[str, ...] | None = None,
        conversation_context: Mapping[str, Any] | None = None,
        pending_action_refs: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
        run_purpose: str = "iteration",
        required_facets: list[str] | tuple[str, ...] | None = None,
        enforce_context_coverage: bool = False,
        persist: bool = False,
    ) -> dict[str, Any]:
        """Build a bounded, stable-digested execution context for one Change."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
            change = _normalize_change(workflow.get("change") or workflow.get("change_set"))
            if change is None:
                raise BuilderWorkflowError("an active Change is required to build a context packet")
            root = self.project_root(kind, project_id)
            manifest_name = "scenario.yaml" if kind == "scenario" else "skill.yaml"
            manifest_path = root / manifest_name
            manifest_raw = manifest_path.read_bytes()
            try:
                manifest = yaml.safe_load(manifest_raw.decode("utf-8-sig")) or {}
            except (UnicodeDecodeError, yaml.YAMLError) as exc:
                raise BuilderWorkflowError(f"cannot build context from {manifest_name}: {exc}") from exc
            if not isinstance(manifest, Mapping):
                manifest = {}
            dependencies: list[str] = []
            for item in manifest.get("depends") or manifest.get("dependencies") or []:
                token = str(item).strip()
                if token and token not in dependencies:
                    dependencies.append(token)
            runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), Mapping) else {}
            skills = runtime.get("skills") if isinstance(runtime.get("skills"), Mapping) else {}
            for item in skills.get("required") or []:
                token = str(item).strip()
                if token and token not in dependencies:
                    dependencies.append(token)
            selected_paths = [
                str(item).replace("\\", "/").strip().lstrip("/")
                for item in allowed_paths or [manifest_name, "prompt_state.json", "webui.json"]
                if str(item).strip()
            ]
            selected_paths = list(dict.fromkeys(selected_paths))[:200]
            purpose = str(run_purpose or "iteration").strip().lower()
            if purpose not in {"iteration", "experiment", "evaluation", "recovery"}:
                raise BuilderWorkflowError("invalid Builder Run purpose")
            previous_run = None
            if change.get("runs"):
                previous_run = copy.deepcopy(change["runs"][-1])
            bounded_conversation = _bounded_conversation_context(conversation_context)
            bounded_pending_actions = _bounded_pending_action_refs(pending_action_refs)
            active_reviews = [
                copy.deepcopy(dict(item))
                for item in workflow.get("reviews") or []
                if isinstance(item, Mapping)
                and str(item.get("status") or "")
                not in {"withdrawn", "dismissed", "resolved", "superseded", "rejected"}
            ][:100]
            semantic_refs = list(
                dict.fromkeys(
                    str(ref).strip()
                    for issue in change.get("issues") or []
                    if isinstance(issue, Mapping)
                    for ref in issue.get("semantic_refs") or []
                    if str(ref).strip()
                )
            )
            for constraint in change.get("acceptance_constraints") or []:
                if isinstance(constraint, Mapping) and str(constraint.get("status") or "") != "superseded":
                    ref = str(constraint.get("target_ref") or "").strip()
                    if ref and ref not in semantic_refs:
                        semantic_refs.append(ref)
            for review in active_reviews:
                ref = str(review.get("target_ref") or "").strip()
                if ref and ref not in semantic_refs:
                    semantic_refs.append(ref)
            webui: dict[str, Any] = {}
            webui_digest = None
            webui_path = root / "webui.json"
            if webui_path.is_file():
                try:
                    webui_raw = webui_path.read_bytes()
                    webui_value = json.loads(webui_raw.decode("utf-8-sig"))
                    webui = dict(webui_value) if isinstance(webui_value, Mapping) else {}
                    webui_digest = f"sha256:{hashlib.sha256(webui_raw).hexdigest()}"
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    webui = {}
            target_structure = _semantic_target_context(webui, semantic_refs)
            constraints = {
                "status": "present",
                "issue_acceptance": [
                    {
                        "issue_id": item.get("issue_id"),
                        "criteria": copy.deepcopy(item.get("acceptance_criteria") or []),
                    }
                    for item in change.get("issues") or []
                    if isinstance(item, Mapping)
                ],
                "acceptance_constraints": copy.deepcopy(
                    change.get("acceptance_constraints") or []
                ),
                "active_review_refs": [f"review:{item['review_id']}" for item in active_reviews],
            }
            data_binding = copy.deepcopy(_mapping(workflow.get("data_binding")))
            data_policy = {
                "status": "present" if data_binding else "missing",
                "selected_profile_id": data_binding.get("selected_profile_id"),
                "selected_mode": data_binding.get("selected_mode"),
                "profiles": data_binding.get("profiles") or [],
                "implementation_mapping": implementation_mapping_report(data_binding),
            }
            workflow_inspection = _mapping(
                self._workflow_inspection(kind, project_id).get("project")
            )
            project_workflow_artifact = None
            try:
                project_workflow_artifact = load_manifest_bound_workflow(
                    root,
                    manifest_name=manifest_name,
                    allow_legacy_inline=kind == "scenario",
                )
            except WorkflowArtifactError:
                project_workflow_artifact = None
            inspection_status = str(workflow_inspection.get("status") or "not_declared")
            workflow_validation = _mapping(workflow_inspection.get("validation"))
            workflow_binding = _mapping(workflow_inspection.get("binding"))
            workflow_definition_status = (
                "present"
                if inspection_status in {"admitted", "legacy_shadow"}
                else "missing"
                if inspection_status == "not_declared"
                else "ambiguous"
            )
            workflow_adapter_catalog = [
                dict(item)
                for item in platform_workflow_adapter_registry().registry_entries()
            ]
            workflow_definition = {
                "status": workflow_definition_status,
                "inspection_status": inspection_status,
                "source": workflow_inspection.get("source"),
                "schema": "adaos.workflow.definition.v1",
                "definition_ref": "abi:workflow.definition.v1.schema.json",
                "definition_digest": workflow_validation.get("definition_digest"),
                "valid": workflow_validation.get("valid"),
                "metrics": copy.deepcopy(_mapping(workflow_validation.get("metrics"))),
                "diagnostics": copy.deepcopy(
                    list(workflow_validation.get("diagnostics") or [])[:50]
                ),
                "graph_diff": copy.deepcopy(
                    _mapping(workflow_validation.get("graph_diff"))
                ),
                "binding_digest": workflow_binding.get("binding_digest"),
                "authoring": {
                    "status": "present",
                    "context_schema": "adaos.workflow.authoring_context.v1",
                    "attempt_schema": "adaos.workflow.authoring_attempt.v1",
                    "definition_schema_ref": "abi:workflow.definition.v1.schema.json",
                    "definition_path": "workflow.json",
                    "definition_authority": "component_root_workflow_json_only",
                    "abi_schemas": [dict(item) for item in workflow_abi_schema_records()],
                    "adapter_catalog": workflow_adapter_catalog,
                    "adapter_catalog_digest": _stable_digest(
                        {"adapter_catalog": workflow_adapter_catalog}
                    ),
                    "role_policy": default_workflow_role_policy(),
                    "activation_boundary": "package_admission",
                    "publish_policy": {
                        "code_definition_atomic": True,
                        "role_policy_source": "workflow_authoring_context",
                        "role_policy_mismatch": "reject",
                    },
                },
            }
            if project_workflow_artifact is not None:
                static_review = workflow_static_report(
                    project_workflow_artifact.compiled,
                    generated_at="1970-01-01T00:00:00+00:00",
                    report_id=f"workflow-static:{kind}:{project_id}",
                )
                workflow_definition["static_review"] = {
                    "schema": static_review["schema"],
                    "report_digest": _stable_digest(static_review),
                    "definition_digest": static_review["definition_digest"],
                    "state_count": static_review["definition_review"]["state_count"],
                    "transition_count": static_review["definition_review"]["transition_count"],
                    "command_count": static_review["definition_review"]["command_count"],
                    "conformance_case_count": static_review["conformance"]["case_count"],
                    "statechart_edge_count": len(static_review["statechart"]["edges"]),
                    "coverage": copy.deepcopy(static_review["coverage"]),
                }
            executable_prototype: dict[str, Any] = {
                "status": "missing",
                "profile": "conversational_mvp",
                "manifest_field": "prototype_runtime",
                "artifacts": {},
                "composition_slices": [],
                "activity_requirements": [],
                "simulation_trace": {
                    "schema": "adaos.builder.prototype_trace.v1",
                    "definition_ref": "abi:builder.prototype_trace.v1.schema.json",
                    "authority": "prototype_evidence_only",
                    "implementation_evidence": False,
                },
                "diagnostics": [],
            }
            prototype_declaration = (
                manifest.get("prototype_runtime")
                if isinstance(manifest.get("prototype_runtime"), Mapping)
                else None
            )
            if prototype_declaration is not None:
                from jsonschema import Draft202012Validator, ValidationError

                from adaos.services.builder.composition import extract_composition_slice
                from adaos.services.builder.conversational_prototype import (
                    validate_conversational_workflow_slice,
                )
                from adaos.services.builder.prototype_handoff import (
                    REQUIRED_REPRESENTATIVE_STATES,
                )
                from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

                executable_prototype["status"] = "ambiguous"
                prototype_values: dict[str, dict[str, Any]] = {}
                diagnostics: list[dict[str, Any]] = []
                for field_name, label in (
                    ("data", "prototype data"),
                    ("binding", "prototype binding"),
                    ("workflow_slice", "prototype workflow slice"),
                    ("representative_states", "prototype representative states"),
                ):
                    try:
                        payload, artifact = _load_bounded_project_json(
                            root,
                            prototype_declaration.get(field_name),
                            label=label,
                        )
                    except BuilderWorkflowError as exc:
                        diagnostics.append(
                            {
                                "code": f"prototype.{field_name}.invalid",
                                "severity": "error",
                                "message": str(exc),
                            }
                        )
                        continue
                    prototype_values[field_name] = payload
                    executable_prototype["artifacts"][field_name] = artifact

                data_definition = prototype_values.get("data")
                if data_definition is not None:
                    try:
                        prototype_data_runtime = PrototypeDataRuntime.start(data_definition)
                    except Exception as exc:
                        diagnostics.append(
                            {
                                "code": "prototype.data.validation_failed",
                                "severity": "error",
                                "message": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    else:
                        executable_prototype["data_definition"] = copy.deepcopy(data_definition)
                        executable_prototype["data_mode"] = prototype_data_runtime.definition["mode"]
                        executable_prototype["activity_requirements"].extend(
                            prototype_data_runtime.activity_requirements()
                        )

                binding_profile = prototype_values.get("binding")
                if binding_profile is not None:
                    binding_schema_path = (
                        Path(__file__).resolve().parents[2]
                        / "abi"
                        / "builder.binding_profile.v1.schema.json"
                    )
                    try:
                        Draft202012Validator(
                            json.loads(binding_schema_path.read_text(encoding="utf-8"))
                        ).validate(binding_profile)
                    except (OSError, json.JSONDecodeError, ValidationError) as exc:
                        diagnostics.append(
                            {
                                "code": "prototype.binding.validation_failed",
                                "severity": "error",
                                "message": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    else:
                        executable_prototype["binding_profile"] = copy.deepcopy(binding_profile)
                        implementation_mappings = [
                            dict(item)
                            for item in binding_profile.get("implementation_mappings") or []
                            if isinstance(item, Mapping)
                        ]
                        missing_mappings = [
                            str(item.get("logical_ref") or "")
                            for item in implementation_mappings
                            if item.get("status") != "mapped"
                            or not str(item.get("implementation_ref") or "").strip()
                        ]
                        executable_prototype["implementation_mapping"] = {
                            "schema": "adaos.builder.implementation_mapping_report.v1",
                            "profile_id": binding_profile.get("profile_id"),
                            "mode": binding_profile.get("mode"),
                            "mapping_count": len(implementation_mappings),
                            "missing": missing_mappings,
                            "ready": not missing_mappings,
                        }

                workflow_slice = prototype_values.get("workflow_slice")
                if workflow_slice is not None:
                    if project_workflow_artifact is None:
                        diagnostics.append(
                            {
                                "code": "prototype.workflow.source_missing",
                                "severity": "error",
                                "message": "prototype workflow slice requires canonical workflow.json",
                            }
                        )
                    else:
                        try:
                            prototype_workflow_report = validate_conversational_workflow_slice(
                                workflow_slice,
                                source_definition=project_workflow_artifact.definition,
                            )
                        except BuilderWorkflowError as exc:
                            diagnostics.append(
                                {
                                    "code": "prototype.workflow.validation_failed",
                                    "severity": "error",
                                    "message": str(exc),
                                }
                            )
                        else:
                            executable_prototype["workflow_slice"] = copy.deepcopy(workflow_slice)
                            executable_prototype["workflow_validation"] = {
                                key: copy.deepcopy(value)
                                for key, value in prototype_workflow_report.items()
                                if key != "candidate_patch"
                            }
                            executable_prototype["activity_requirements"].extend(
                                copy.deepcopy(workflow_slice.get("activity_requirements") or [])
                            )

                representative_state_set = prototype_values.get("representative_states")
                representative_states = (
                    representative_state_set.get("states")
                    if isinstance(representative_state_set, Mapping)
                    else None
                )
                if not isinstance(representative_states, list):
                    diagnostics.append(
                        {
                            "code": "prototype.representative_states.invalid",
                            "severity": "error",
                            "message": "representative states artifact requires a states array",
                        }
                    )
                else:
                    supplied_states = {
                        str(item.get("state_id") or "")
                        for item in representative_states
                        if isinstance(item, Mapping)
                    }
                    missing_states = sorted(REQUIRED_REPRESENTATIVE_STATES - supplied_states)
                    if missing_states:
                        diagnostics.append(
                            {
                                "code": "prototype.representative_states.incomplete",
                                "severity": "error",
                                "message": "missing representative states: " + ", ".join(missing_states),
                            }
                        )
                    executable_prototype["representative_states"] = copy.deepcopy(
                        representative_states[:40]
                    )

                revision_ref = None
                current_revision_path = root / "ui_revisions" / "current.txt"
                if current_revision_path.is_file():
                    try:
                        revision_ref = current_revision_path.read_text(
                            encoding="utf-8-sig"
                        ).strip()
                    except OSError:
                        revision_ref = None
                revision_ref = revision_ref or webui_digest or "webui:unversioned"
                acceptance_by_target: dict[str, list[Mapping[str, Any]]] = {}
                for constraint in change.get("acceptance_constraints") or []:
                    if not isinstance(constraint, Mapping):
                        continue
                    target_ref = str(constraint.get("target_ref") or "").strip()
                    if target_ref:
                        acceptance_by_target.setdefault(target_ref, []).append(constraint)
                for target_ref in semantic_refs:
                    if not target_ref.startswith(("widget:", "field:")):
                        continue
                    try:
                        composition_slice = extract_composition_slice(
                            webui,
                            target_ref,
                            source_revision=str(revision_ref),
                            acceptance=acceptance_by_target.get(target_ref),
                        )
                    except BuilderWorkflowError as exc:
                        diagnostics.append(
                            {
                                "code": "prototype.composition.unresolved",
                                "severity": "error",
                                "target_ref": target_ref,
                                "message": str(exc),
                            }
                        )
                    else:
                        executable_prototype["composition_slices"].append(composition_slice)

                executable_prototype["activity_requirements"] = copy.deepcopy(
                    executable_prototype["activity_requirements"][:100]
                )
                executable_prototype["diagnostics"] = diagnostics[:50]
                executable_prototype["status"] = "ambiguous" if diagnostics else "present"
            conversational_definition: dict[str, Any] = {
                "status": "missing",
                "manifest_ref": "conversational/manifest.yaml",
                "package_digest": None,
                "valid": None,
                "metrics": {},
                "diagnostics": [],
                "story_reports": [],
                "static_report": None,
            }
            if isinstance(manifest.get("conversational"), Mapping):
                operation_catalog: dict[str, tuple[str, ...]] = {}
                for dependency in dependencies:
                    dependency_root = self.dev_skills_root / dependency
                    dependency_manifest = dependency_root / "skill.yaml"
                    if not dependency_manifest.is_file():
                        continue
                    try:
                        dependency_payload = yaml.safe_load(
                            dependency_manifest.read_text(encoding="utf-8-sig")
                        ) or {}
                    except (OSError, UnicodeDecodeError, yaml.YAMLError):
                        continue
                    if not isinstance(dependency_payload, Mapping):
                        continue
                    operations = [
                        str(item.get("name") or "").strip()
                        for item in dependency_payload.get("tools") or []
                        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
                    ]
                    exports = (
                        dependency_payload.get("exports")
                        if isinstance(dependency_payload.get("exports"), Mapping)
                        else {}
                    )
                    operations.extend(
                        str(item.get("name") if isinstance(item, Mapping) else item).strip()
                        for item in dict(exports).get("tools") or []
                        if str(item.get("name") if isinstance(item, Mapping) else item).strip()
                    )
                    operation_catalog[dependency] = tuple(sorted(set(operations)))
                try:
                    conversational_result = compile_conversational_package(
                        root,
                        manifest_name=manifest_name,
                        operation_catalog=operation_catalog,
                    )
                except Exception as exc:
                    conversational_definition.update(
                        {
                            "status": "ambiguous",
                            "valid": False,
                            "diagnostics": [
                                {
                                    "code": "conversational.pipeline.failed",
                                    "severity": "error",
                                    "path": "conversational",
                                    "message": f"{type(exc).__name__}: {exc}",
                                }
                            ],
                        }
                    )
                else:
                    validation_report = conversational_result.validation.report
                    static_report = conversational_result.static_report
                    conversational_definition.update(
                        {
                            "status": "present" if conversational_result.valid else "ambiguous",
                            "package_digest": validation_report.get("package_digest"),
                            "valid": conversational_result.valid,
                            "metrics": copy.deepcopy(validation_report.get("metrics") or {}),
                            "diagnostics": copy.deepcopy(
                                list(validation_report.get("diagnostics") or [])[:50]
                            ),
                            "story_reports": [
                                {
                                    "story_id": item.get("story_id"),
                                    "valid": item.get("valid"),
                                    "steps": item.get("steps"),
                                    "final_state": item.get("final_state"),
                                }
                                for item in validation_report.get("story_reports") or []
                                if isinstance(item, Mapping)
                            ][:50],
                            "static_report": None
                            if static_report is None
                            else {
                                "schema": static_report.get("schema"),
                                "report_digest": _stable_digest(static_report),
                                "definition_digest": static_report.get("definition_digest"),
                                "coverage": copy.deepcopy(static_report.get("coverage") or {}),
                            },
                        }
                    )
            from adaos.services.builder.repair import BuilderRepairService

            repair_context = BuilderRepairService(
                state_dir=Path(self.state_dir)
            ).task_context(project_id)
            facets: dict[str, Any] = {
                "target_structure": target_structure,
                "abi": {
                    "status": "present" if webui_digest else "missing",
                    "schema": webui.get("schema"),
                    "definition_ref": "abi:webui.v1.schema.json",
                    "artifact_ref": "webui.json",
                    "artifact_digest": webui_digest,
                },
                "constraints": constraints,
                "data_policy": data_policy,
                "workflow_definition": workflow_definition,
                "conversational_definition": conversational_definition,
                "executable_prototype": executable_prototype,
                "repair_context": repair_context,
                "execution_authority": {
                    "status": "present" if selected_paths else "missing",
                    "allowed_paths": selected_paths,
                    "actor": "builder",
                    "phase": str(workflow.get("active_phase") or "prototype"),
                },
            }
            required = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in required_facets or []
                    if str(item).strip()
                )
            )
            unknown_facets = sorted(set(required) - set(facets))
            if unknown_facets:
                raise BuilderWorkflowError(
                    f"unknown required context facet: {unknown_facets[0]}"
                )
            coverage = _facet_coverage(facets, required)
            if enforce_context_coverage and not coverage["ready"]:
                details = [
                    *(f"missing:{item}" for item in coverage["missing"]),
                    *(f"ambiguous:{item}" for item in coverage["ambiguous"]),
                ]
                raise BuilderWorkflowError(
                    "Builder context is insufficient before model submission: "
                    + ", ".join(details)
                )
            packet_body: dict[str, Any] = {
                "schema": BUILDER_CONTEXT_PACKET_SCHEMA,
                "project": {
                    "ref": f"{kind}:{project_id}",
                    "object_type": kind,
                    "object_id": project_id,
                    "manifest_ref": manifest_name,
                    "manifest_version": str(manifest.get("version") or "").strip() or None,
                    "manifest_digest": f"sha256:{hashlib.sha256(manifest_raw).hexdigest()}",
                },
                "change": {
                    "change_id": change["change_id"],
                    "intent": change.get("request"),
                    "request_addenda": copy.deepcopy(change.get("request_addenda") or []),
                    "route": change.get("route"),
                    "gate": change.get("gate"),
                    "status": change.get("status"),
                    "issues": copy.deepcopy(change.get("issues") or []),
                    "acceptance_constraints": copy.deepcopy(change.get("acceptance_constraints") or []),
                    "reviews": active_reviews,
                    "source_message_ids": copy.deepcopy(change.get("source_message_ids") or []),
                    "teacher_candidate_refs": copy.deepcopy(change.get("teacher_candidate_refs") or []),
                    "promotion_privacy_scope": change.get("promotion_privacy_scope"),
                },
                "base": {
                    "source": copy.deepcopy(change.get("base_ref")),
                    "release": copy.deepcopy(_mapping(workflow.get("delivery")).get("base_release")),
                    "release_digest": _mapping(workflow.get("delivery")).get("base_release_digest"),
                },
                "artifacts": {
                    "prototype": copy.deepcopy(_mapping(workflow.get("prototype"))),
                    "implementation": copy.deepcopy(_mapping(workflow.get("automation"))),
                    "trial": copy.deepcopy(_mapping(workflow.get("delivery"))),
                    "publication": copy.deepcopy(_mapping(workflow.get("publication"))),
                },
                "dependencies": dependencies[:200],
                "allowed_paths": selected_paths,
                "instruction_refs": [str(item).strip() for item in instruction_refs or [] if str(item).strip()][:100],
                "previous_run": previous_run,
                "conversation": bounded_conversation,
                "pending_actions": bounded_pending_actions,
                "run": {"purpose": purpose},
                "facets": facets,
                "coverage": coverage,
                "budget": {
                    "max_state_bytes": _MAX_STATE_BYTES,
                    "issue_count": len(change.get("issues") or []),
                    "acceptance_constraint_count": len(change.get("acceptance_constraints") or []),
                    "run_count": len(change.get("runs") or []),
                    "source_message_ref_count": len(change.get("source_message_ids") or []),
                    "conversation_message_count": len((bounded_conversation or {}).get("messages") or []),
                    "conversation_segment_count": len((bounded_conversation or {}).get("segments") or []),
                    "memory_item_count": len((bounded_conversation or {}).get("memory") or []),
                    "pending_action_ref_count": len(bounded_pending_actions),
                    "active_review_count": len(active_reviews),
                    "active_repair_count": int(repair_context.get("active_count") or 0),
                    "required_facet_count": len(required),
                    "missing_facet_count": len(coverage["missing"]),
                    "ambiguous_facet_count": len(coverage["ambiguous"]),
                },
            }
            packet = {
                **packet_body,
                "digest": _stable_digest(packet_body),
                "built_at": _now(),
            }
            if persist:
                workflow["context_packet"] = copy.deepcopy(packet)
                change["context_packet_digest"] = packet["digest"]
                workflow["change"] = _normalize_change(change)
                workflow["change_set"] = _change_set_compatibility(workflow["change"])
                state["workflow"] = workflow
                state["updated_at"] = packet["built_at"]
                self._write_state(kind, project_id, state)

        if persist and callable(self.event_sink):
            self.event_sink(self.describe(kind, project_id))
        return copy.deepcopy(packet)

    @staticmethod
    def _require_active(workflow: Mapping[str, Any], phase: str, action: str) -> None:
        active = str(workflow.get("active_phase") or "prototype")
        if active != phase:
            raise BuilderWorkflowError(f"{action} requires active {phase}; active phase is {active}")

    def _apply_transition(
        self,
        workflow: dict[str, Any],
        action: str,
        metadata: Mapping[str, Any],
        *,
        changed_at: str,
        project_id: str,
    ) -> None:
        prototype = workflow["prototype"]
        automation = workflow["automation"]
        delivery = workflow["delivery"]
        publication = workflow["publication"]
        change_set = _normalize_change_set(workflow.get("change_set"))
        workflow["change_set"] = change_set

        def require_change_set(change_set_id: Any = None) -> dict[str, Any]:
            current = workflow.get("change_set")
            if not isinstance(current, dict):
                raise BuilderWorkflowError("an active change set is required")
            expected = str(change_set_id or current.get("change_set_id") or "").strip()
            if expected != str(current.get("change_set_id") or ""):
                raise BuilderWorkflowError("change set identity does not match the active change set")
            if str(current.get("status") or "") in _CHANGE_SET_TERMINAL_STATES:
                raise BuilderWorkflowError("the active change set is already terminal")
            return current

        def update_change_set(*, status: str | None = None, gate: str | None = None) -> None:
            current = workflow.get("change_set")
            if not isinstance(current, dict):
                return
            if status:
                current["status"] = status
            if gate:
                current["gate"] = gate
            current["updated_at"] = changed_at

        def add_change_evidence(change_id: Any) -> None:
            token = str(change_id or "").strip()
            current = workflow.get("change_set")
            if not token or not isinstance(current, dict):
                return
            members = [
                str(item).strip()
                for item in current.get("member_change_ids") or []
                if str(item).strip()
            ]
            if token not in members:
                members.append(token)
            current["member_change_ids"] = members[-100:]
            current["updated_at"] = changed_at

        def canonical_change() -> dict[str, Any]:
            current = require_change_set(metadata.get("change_id") or metadata.get("change_set_id"))
            existing = _normalize_change(workflow.get("change"))
            if existing is not None:
                return existing
            created = _normalize_change(
                {
                    **current,
                    "change_id": current["change_set_id"],
                    "project_ref": str(
                        _mapping(metadata.get("constraint")).get("project_ref") or ""
                    ).strip()
                    or None,
                    "runs": [],
                    "acceptance_constraints": [],
                }
            )
            if created is None:
                raise BuilderWorkflowError("an active Change is required")
            return created

        def invalidate_delivery(reason: str) -> None:
            if str(delivery.get("status") or "idle") in {"checkpoint", "trial", "accepted"}:
                delivery.update(
                    {
                        "status": "stale",
                        "stale_reason": reason,
                        "stale_at": changed_at,
                    }
                )
        if action == "plan_change_set":
            change_set_id = str(metadata.get("change_set_id") or "").strip()
            if not change_set_id:
                raise BuilderWorkflowError("change_set_id is required")
            existing = workflow.get("change_set")
            if isinstance(existing, Mapping) and str(existing.get("status") or "") not in _CHANGE_SET_TERMINAL_STATES:
                supersedes = str(metadata.get("supersedes_change_set_id") or "").strip()
                if supersedes != str(existing.get("change_set_id") or ""):
                    raise BuilderWorkflowError(
                        "an active change set already exists; supersedes_change_set_id is required"
                    )
            raw_issues = metadata.get("issues")
            if not isinstance(raw_issues, (list, tuple)) or not raw_issues:
                raise BuilderWorkflowError("change set requires at least one issue")
            _reject_transport_corruption(metadata.get("request"), field="change set request")
            _reject_transport_corruption(raw_issues, field="change set issues")
            if len(raw_issues) > _MAX_CHANGE_ISSUES:
                raise BuilderWorkflowError(f"change set supports at most {_MAX_CHANGE_ISSUES} issues")
            issues = [_normalize_issue(item, index=index) for index, item in enumerate(raw_issues, start=1)]
            issue_ids = [item["issue_id"] for item in issues]
            if len(set(issue_ids)) != len(issue_ids):
                raise BuilderWorkflowError("change set issue_ids must be unique")
            route = "prototype_first" if any(item["lane"] == "prototype" for item in issues) else "automation_direct"
            gate = "prototype" if route == "prototype_first" else "automation"
            workflow["change_set"] = {
                "schema": BUILDER_CHANGE_SET_SCHEMA,
                "change_set_id": change_set_id,
                "request": _bounded_text(metadata.get("request"), field="change set request", max_length=4000),
                "request_addenda": [],
                "route": route,
                "gate": gate,
                "status": "planned",
                "issues": issues,
                "member_change_ids": [change_set_id],
                "source_message_ids": [
                    str(item).strip()
                    for item in metadata.get("source_message_ids") or []
                    if str(item).strip()
                ][-100:],
                "created_at": changed_at,
                "updated_at": changed_at,
                "supersedes_change_set_id": str(
                    metadata.get("supersedes_change_set_id") or ""
                ).strip()
                or None,
            }
            interaction = _mapping(workflow.get("interaction"))
            interaction["conversation_focus"] = f"change:{change_set_id}"
            workflow["interaction"] = interaction
            return
        if action == "change_issues_added":
            current = require_change_set(metadata.get("change_set_id"))
            raw_issues = metadata.get("issues")
            if not isinstance(raw_issues, (list, tuple)) or not raw_issues:
                raise BuilderWorkflowError("change set extension requires at least one issue")
            _reject_transport_corruption(metadata.get("request"), field="change set request addendum")
            _reject_transport_corruption(raw_issues, field="change set issues")
            existing_issues = [
                item for item in current.get("issues") or [] if isinstance(item, dict)
            ]
            if len(existing_issues) + len(raw_issues) > _MAX_CHANGE_ISSUES:
                raise BuilderWorkflowError(f"change set supports at most {_MAX_CHANGE_ISSUES} issues")
            known_ids = {str(item.get("issue_id") or "") for item in existing_issues}
            additions: list[dict[str, Any]] = []
            for index, item in enumerate(raw_issues, start=len(existing_issues) + 1):
                issue = _normalize_issue(item, index=index)
                if issue["issue_id"] in known_ids:
                    raise BuilderWorkflowError(
                        f"duplicate change set issue_id: {issue['issue_id']}"
                    )
                known_ids.add(issue["issue_id"])
                additions.append(issue)
            current["issues"] = [*existing_issues, *additions]
            addendum = str(metadata.get("request") or "").strip()
            if addendum:
                current["request_addenda"] = [
                    *list(current.get("request_addenda") or []),
                    _bounded_text(
                        addendum,
                        field="change set request addendum",
                        max_length=4000,
                    ),
                ][-50:]
            source_message_ids = [
                str(item).strip()
                for item in current.get("source_message_ids") or []
                if str(item).strip()
            ]
            for message_id in metadata.get("source_message_ids") or []:
                token = str(message_id).strip()
                if token and token not in source_message_ids:
                    source_message_ids.append(token)
            current["source_message_ids"] = source_message_ids[-100:]
            add_change_evidence(metadata.get("change_id"))
            invalidate_delivery("change_set_extended")
            prototype_added = any(item.get("lane") == "prototype" for item in additions)
            prototype_pending = any(
                item.get("lane") == "prototype"
                and item.get("status") not in {"resolved", "deferred"}
                for item in current.get("issues") or []
                if isinstance(item, Mapping)
            )
            if prototype_added or prototype_pending:
                current["route"] = "prototype_first"
                update_change_set(
                    status="changes_requested" if prototype_added else "in_progress",
                    gate="prototype",
                )
            else:
                update_change_set(status="in_progress", gate="automation")
            return
        if action == "change_issue_updated":
            current = require_change_set(metadata.get("change_set_id"))
            issue_id = str(metadata.get("issue_id") or "").strip()
            status = str(metadata.get("status") or "").strip().lower()
            if status not in _ISSUE_STATES:
                raise BuilderWorkflowError(
                    "change set issue status must be open, in_progress, resolved, or deferred"
                )
            issue = next(
                (item for item in current.get("issues") or [] if item.get("issue_id") == issue_id),
                None,
            )
            if not isinstance(issue, dict):
                raise BuilderWorkflowError(f"unknown change set issue_id: {issue_id}")
            issue["status"] = status
            update_change_set(status="in_progress" if status == "in_progress" else None)
            return
        if action == "change_issue_split":
            current = require_change_set(metadata.get("change_set_id"))
            issue_id = str(metadata.get("issue_id") or "").strip()
            source = next(
                (item for item in current.get("issues") or [] if item.get("issue_id") == issue_id),
                None,
            )
            if not isinstance(source, dict):
                raise BuilderWorkflowError(f"unknown change set issue_id: {issue_id}")
            if source.get("structural_status") != "active":
                raise BuilderWorkflowError("only an active issue can be split")
            raw_children = metadata.get("issues")
            if not isinstance(raw_children, (list, tuple)) or len(raw_children) < 2:
                raise BuilderWorkflowError("issue split requires at least two replacement issues")
            existing = [item for item in current.get("issues") or [] if isinstance(item, dict)]
            if len(existing) + len(raw_children) > _MAX_CHANGE_ISSUES:
                raise BuilderWorkflowError(f"change set supports at most {_MAX_CHANGE_ISSUES} issues")
            known = {str(item.get("issue_id") or "") for item in existing}
            children: list[dict[str, Any]] = []
            for index, raw in enumerate(raw_children, start=len(existing) + 1):
                child = _normalize_issue(raw, index=index)
                if child["issue_id"] in known:
                    raise BuilderWorkflowError(f"duplicate change set issue_id: {child['issue_id']}")
                known.add(child["issue_id"])
                child["derived_from_issue_ids"] = [issue_id]
                children.append(child)
            source["status"] = "deferred"
            source["structural_status"] = "split"
            source["superseded_by_issue_ids"] = [item["issue_id"] for item in children]
            current["issues"] = [*existing, *children]
            prototype_pending = any(
                item.get("structural_status") == "active"
                and item.get("lane") == "prototype"
                and item.get("status") not in {"resolved", "deferred"}
                for item in current["issues"]
            )
            update_change_set(
                status="changes_requested",
                gate="prototype" if prototype_pending else "automation",
            )
            invalidate_delivery("issue_structure_changed")
            return
        if action == "change_issues_merged":
            current = require_change_set(metadata.get("change_set_id"))
            issue_ids = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in metadata.get("issue_ids") or []
                    if str(item).strip()
                )
            )
            if len(issue_ids) < 2:
                raise BuilderWorkflowError("issue merge requires at least two source issues")
            existing = [item for item in current.get("issues") or [] if isinstance(item, dict)]
            sources = [item for item in existing if item.get("issue_id") in issue_ids]
            if len(sources) != len(issue_ids):
                raise BuilderWorkflowError("issue merge contains an unknown issue_id")
            if any(item.get("structural_status") != "active" for item in sources):
                raise BuilderWorkflowError("only active issues can be merged")
            merged = _normalize_issue(metadata.get("issue"), index=len(existing) + 1)
            if any(item.get("issue_id") == merged["issue_id"] for item in existing):
                raise BuilderWorkflowError(f"duplicate change set issue_id: {merged['issue_id']}")
            merged["derived_from_issue_ids"] = issue_ids
            for source in sources:
                source["status"] = "deferred"
                source["structural_status"] = "merged"
                source["superseded_by_issue_ids"] = [merged["issue_id"]]
            current["issues"] = [*existing, merged]
            update_change_set(
                status="changes_requested",
                gate="prototype" if merged["lane"] == "prototype" else "automation",
            )
            invalidate_delivery("issue_structure_changed")
            return
        if action == "change_evidence_recorded":
            require_change_set(metadata.get("change_set_id"))
            change_id = str(metadata.get("change_id") or "").strip()
            if not change_id:
                raise BuilderWorkflowError("change evidence requires change_id")
            add_change_evidence(change_id)
            return
        if action == "review_constraint_added":
            current_change = canonical_change()
            constraint = _normalize_acceptance_constraint(
                metadata.get("constraint"),
                change_id=current_change["change_id"],
            )
            constraints = list(current_change.get("acceptance_constraints") or [])
            if any(item.get("constraint_id") == constraint["constraint_id"] for item in constraints):
                raise BuilderWorkflowError(
                    f"acceptance constraint already exists: {constraint['constraint_id']}"
                )
            if len(constraints) >= _MAX_ACCEPTANCE_CONSTRAINTS:
                raise BuilderWorkflowError(
                    f"a Change supports at most {_MAX_ACCEPTANCE_CONSTRAINTS} acceptance constraints"
                )
            constraints.append(constraint)
            current_change["acceptance_constraints"] = constraints
            workflow["change"] = current_change
            update_change_set(status="changes_requested", gate="prototype")
            invalidate_delivery("review_constraint_added")
            return
        if action == "review_constraints_evaluated":
            current_change = canonical_change()
            evaluations = metadata.get("evaluations")
            if not isinstance(evaluations, (list, tuple)) or not evaluations:
                raise BuilderWorkflowError("Review constraint evaluation requires results")
            by_id = {
                str(item.get("constraint_id") or "").strip(): dict(item)
                for item in evaluations
                if isinstance(item, Mapping) and str(item.get("constraint_id") or "").strip()
            }
            if not by_id:
                raise BuilderWorkflowError("Review constraint evaluation requires identified results")
            constraints = list(current_change.get("acceptance_constraints") or [])
            known = {str(item.get("constraint_id") or "") for item in constraints}
            unknown = sorted(set(by_id) - known)
            if unknown:
                raise BuilderWorkflowError(f"unknown acceptance constraint: {unknown[0]}")
            any_violation = False
            for constraint in constraints:
                evaluation = by_id.get(str(constraint.get("constraint_id") or ""))
                if evaluation is None:
                    continue
                status = str(evaluation.get("status") or "").strip().lower()
                if status not in {"satisfied", "violated", "unverifiable"}:
                    raise BuilderWorkflowError("invalid acceptance constraint evaluation status")
                constraint["status"] = status
                constraint["last_evaluation"] = copy.deepcopy(evaluation)
                constraint["updated_at"] = changed_at
                any_violation = any_violation or status != "satisfied"
            current_change["acceptance_constraints"] = constraints
            workflow["change"] = current_change
            if any_violation:
                update_change_set(status="changes_requested", gate="prototype")
            return
        if action == "review_constraint_superseded":
            current_change = canonical_change()
            constraint_id = str(metadata.get("constraint_id") or "").strip()
            reason = _bounded_text(
                metadata.get("reason"), field="constraint supersede reason", max_length=2000
            )
            constraint = next(
                (
                    item
                    for item in current_change.get("acceptance_constraints") or []
                    if item.get("constraint_id") == constraint_id
                ),
                None,
            )
            if not isinstance(constraint, dict):
                raise BuilderWorkflowError(f"unknown acceptance constraint: {constraint_id}")
            if str(constraint.get("status") or "") == "superseded":
                raise BuilderWorkflowError("acceptance constraint is already superseded")
            constraint["status"] = "superseded"
            constraint["updated_at"] = changed_at
            constraint["superseded_reason"] = reason
            constraint["superseded_by_ref"] = str(
                metadata.get("superseded_by_ref") or ""
            ).strip() or None
            workflow["change"] = current_change
            return
        if action == "prototype_revision_recorded":
            self._require_active(workflow, "prototype", action)
            revision = str(metadata.get("revision") or "").strip()
            if not revision:
                raise BuilderWorkflowError("Prototype revision recording requires revision")
            if _kind(str(metadata.get("object_type") or "scenario")) != "scenario":
                raise BuilderWorkflowError("Prototype revisions are supported only for scenarios")
            prototype.update(
                {
                    "status": "working",
                    "stable": False,
                    "head_revision": revision,
                    "revised_at": changed_at,
                }
            )
            invalidate_delivery("prototype_revision_recorded")
            current = workflow.get("change_set")
            if isinstance(current, dict) and current.get("gate") == "prototype":
                update_change_set(status="in_progress", gate="prototype")
            add_change_evidence(metadata.get("change_id"))
            return
        if action == "prototype_experiment_recorded":
            self._require_active(workflow, "prototype", action)
            revision = str(metadata.get("revision") or "").strip()
            experiment_id = str(metadata.get("experiment_id") or "").strip()
            if not revision or not experiment_id:
                raise BuilderWorkflowError("Prototype experiment requires experiment_id and revision")
            experiments = [
                dict(item) for item in prototype.get("experiments") or [] if isinstance(item, Mapping)
            ]
            if any(item.get("experiment_id") == experiment_id for item in experiments):
                raise BuilderWorkflowError(f"Prototype experiment already exists: {experiment_id}")
            experiments.append(
                {
                    "experiment_id": experiment_id,
                    "revision": revision,
                    "status": "pending",
                    "base_revision": str(
                        metadata.get("base_revision") or prototype.get("head_revision") or ""
                    )
                    or None,
                    "created_at": changed_at,
                    "evidence_refs": [
                        str(item).strip()
                        for item in metadata.get("evidence_refs") or []
                        if str(item).strip()
                    ][:100],
                }
            )
            prototype["experiments"] = experiments[-50:]
            return
        if action in {"adopt_experiment", "discard_experiment"}:
            self._require_active(workflow, "prototype", action)
            experiment_id = str(metadata.get("experiment_id") or "").strip()
            experiments = [
                dict(item) for item in prototype.get("experiments") or [] if isinstance(item, Mapping)
            ]
            experiment = next(
                (item for item in experiments if item.get("experiment_id") == experiment_id), None
            )
            if not isinstance(experiment, dict):
                raise BuilderWorkflowError(f"unknown Prototype experiment: {experiment_id}")
            if str(experiment.get("status") or "") != "pending":
                raise BuilderWorkflowError("Prototype experiment is already decided")
            if action == "discard_experiment":
                experiment["status"] = "discarded"
                experiment["decided_at"] = changed_at
                experiment["reason"] = _bounded_text(
                    metadata.get("reason"), field="experiment discard reason", max_length=1000
                )
                prototype["experiments"] = experiments
                return
            if not bool(metadata.get("confirmed")):
                raise BuilderWorkflowError("adopting a Prototype experiment requires confirmation")
            prototype.update(
                {
                    "head_revision": experiment["revision"],
                    "status": "working",
                    "stable": False,
                    "revised_at": changed_at,
                }
            )
            experiment["status"] = "adopted"
            experiment["decided_at"] = changed_at
            prototype["experiments"] = experiments
            invalidate_delivery("prototype_experiment_adopted")
            update_change_set(status="in_progress", gate="prototype")
            return
        if action == "stabilize_prototype":
            self._require_active(workflow, "prototype", action)
            prototype.update({"status": "working", "stable": True, "stabilized_at": changed_at})
            prototype["head_revision"] = metadata.get("revision") or prototype.get("head_revision")
            current = workflow.get("change_set")
            if isinstance(current, dict) and current.get("gate") == "prototype":
                for issue in current.get("issues") or []:
                    if (
                        isinstance(issue, dict)
                        and issue.get("lane") == "prototype"
                        and issue.get("status") != "deferred"
                    ):
                        issue["status"] = "resolved"
                update_change_set(status="approved", gate="automation")
            return
        if action in {"handoff_to_automation", "automation_started"}:
            self._require_active(workflow, "prototype", action)
            source_revision = str(metadata.get("source_prototype_revision") or "").strip()
            if source_revision.lower().startswith("ui "):
                source_revision = source_revision[3:].strip()
            source_revision = source_revision or str(prototype.get("head_revision") or "").strip() or None
            prototype.update({"status": "frozen", "stable": True, "frozen_at": changed_at})
            prototype["head_revision"] = source_revision
            automation["iteration"] = int(automation.get("iteration") or 0) + 1
            workflow["active_phase"] = "automation"
            automation.update(
                {
                    "status": "working",
                    "source_prototype_revision": source_revision,
                    "head_task_id": metadata.get("task_id") or automation.get("head_task_id"),
                    "started_at": changed_at,
                    "completed_at": None,
                    "error": None,
                }
            )
            invalidate_delivery("automation_started")
            workflow["pending_transition"] = None
            update_change_set(status="in_progress", gate="automation")
            add_change_evidence(metadata.get("change_id"))
            return
        if action == "automation_iteration_started":
            self._require_active(workflow, "automation", action)
            status = str(automation.get("status") or "")
            reconciliation = bool(metadata.get("reconciliation"))
            next_task_id = str(metadata.get("task_id") or "").strip()
            previous_task_id = str(automation.get("head_task_id") or "").strip()
            reconciles_stale_working_state = bool(
                status == "working" and next_task_id and next_task_id != previous_task_id
            )
            if status not in {"completed", "failed"} and not reconciles_stale_working_state:
                raise BuilderWorkflowError(
                    "a new Automation iteration requires a completed or failed Automation result"
                )
            if not reconciliation:
                automation["iteration"] = int(automation.get("iteration") or 0) + 1
            automation.update(
                {
                    "status": "working",
                    "head_task_id": metadata.get("task_id") or automation.get("head_task_id"),
                    "started_at": changed_at,
                    "completed_at": None,
                    "error": None,
                }
            )
            invalidate_delivery("automation_iteration_started")
            workflow["pending_transition"] = None
            update_change_set(status="in_progress", gate="automation")
            add_change_evidence(metadata.get("change_id"))
            return
        if action == "automation_completed":
            self._require_active(workflow, "automation", action)
            automation.update(
                {
                    "status": "completed",
                    "head_task_id": metadata.get("task_id") or automation.get("head_task_id"),
                    "snapshot_task_id": metadata.get("task_id") or automation.get("snapshot_task_id"),
                    "result_version": metadata.get("version") or automation.get("result_version"),
                    "snapshot_path": metadata.get("snapshot_path") or automation.get("snapshot_path"),
                    "completed_at": changed_at,
                    "error": None,
                }
            )
            current = workflow.get("change_set")
            if isinstance(current, dict):
                for issue in current.get("issues") or []:
                    if (
                        isinstance(issue, dict)
                        and issue.get("lane") == "automation"
                        and issue.get("status") != "deferred"
                    ):
                        issue["status"] = "resolved"
                update_change_set(status="implemented", gate="trial")
                add_change_evidence(metadata.get("change_id"))
            return
        if action == "automation_failed":
            self._require_active(workflow, "automation", action)
            automation.update(
                {
                    "status": "failed",
                    "head_task_id": metadata.get("task_id") or automation.get("head_task_id"),
                    "error": metadata.get("error"),
                    "completed_at": changed_at,
                }
            )
            workflow["pending_transition"] = None
            update_change_set(status="blocked", gate="automation")
            add_change_evidence(metadata.get("change_id"))
            return
        if action == "request_return_to_prototype":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("return to prototype requires completed automation")
            automation["status"] = "adapting"
            automation.pop("adaptation_error", None)
            automation.pop("adaptation_failed_at", None)
            workflow["pending_transition"] = {
                "action": "return_to_prototype",
                "requested_at": changed_at,
                "task_id": metadata.get("task_id"),
            }
            return
        if action == "return_to_prototype_failed":
            self._require_active(workflow, "automation", action)
            status = str(automation.get("status") or "")
            recoverable_failed_state = bool(status == "failed" and automation.get("snapshot_path"))
            if status != "adapting" and not recoverable_failed_state:
                raise BuilderWorkflowError(
                    "failed Prototype adaptation recovery requires adapting Automation or a retained snapshot"
                )
            automation.update(
                {
                    "status": "completed",
                    "error": None,
                    "adaptation_error": metadata.get("error"),
                    "adaptation_failed_at": changed_at,
                }
            )
            workflow["pending_transition"] = None
            return
        if action == "return_to_prototype":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") not in {"adapting", "completed"}:
                raise BuilderWorkflowError("return to prototype requires completed adaptation")
            automation.update({"status": "frozen", "frozen_at": changed_at})
            automation.pop("adaptation_error", None)
            automation.pop("adaptation_failed_at", None)
            workflow["active_phase"] = "prototype"
            prototype.update(
                {
                    "status": "working",
                    "stable": False,
                    "head_revision": metadata.get("revision") or prototype.get("head_revision"),
                    "derived_from_automation_task": metadata.get("task_id") or automation.get("head_task_id"),
                    "resumed_at": changed_at,
                }
            )
            invalidate_delivery("returned_to_prototype")
            workflow["pending_transition"] = None
            update_change_set(status="in_progress", gate="prototype")
            add_change_evidence(metadata.get("change_id"))
            return
        if action == "checkpoint_recorded":
            change_id = str(metadata.get("change_id") or "").strip()
            package_digest = str(metadata.get("package_digest") or "").strip()
            source_revision = str(metadata.get("source_revision") or "").strip()
            if not change_id or not package_digest or not source_revision:
                raise BuilderWorkflowError(
                    "checkpoint requires change, package, and source identities"
                )
            checkpoint_version = str(metadata.get("version") or "").strip() or None
            checkpoint_versions = _mapping(workflow.get("checkpoint_versions"))
            if checkpoint_version:
                previous = _mapping(checkpoint_versions.get(checkpoint_version))
                previous_digest = str(previous.get("package_digest") or "").strip()
                if previous_digest and previous_digest != package_digest:
                    raise BuilderWorkflowError(
                        "DEV checkpoint semantic version already maps to different bytes; "
                        "bump the version before checkpointing"
                    )
                checkpoint_versions[checkpoint_version] = {
                    "package_digest": package_digest,
                    "source_revision": source_revision,
                    "recorded_at": changed_at,
                }
                workflow["checkpoint_versions"] = checkpoint_versions
            rebase_plan = delivery.get("rebase_plan")
            has_rebase_plan = isinstance(rebase_plan, Mapping)
            replaces_candidate_id = (
                delivery.get("candidate_id") or delivery.get("replaces_candidate_id")
                if has_rebase_plan
                else None
            )
            delivery.clear()
            delivery.update(
                {
                    "status": "checkpoint",
                    "checkpoint_change_id": change_id,
                    "package_digest": package_digest,
                    "source_revision": source_revision,
                    "version": checkpoint_version,
                    "checkpoint_at": changed_at,
                    "candidate_id": None,
                    "release_digest": None,
                    "base_release": None,
                    "trial_workspace": None,
                    "prepared_at": None,
                    "decided_at": None,
                    "replaces_candidate_id": replaces_candidate_id,
                    "rebase_plan": dict(rebase_plan) if has_rebase_plan else None,
                }
            )
            add_change_evidence(change_id)
            update_change_set(status="checkpointed", gate="trial")
            return
        if action == "candidate_preparation_started":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("trial activation requires completed automation")
            if str(delivery.get("status") or "") != "checkpoint":
                raise BuilderWorkflowError("trial activation requires an exact checkpoint")
            delivery.update(
                {
                    "status": "activating",
                    "activity_attempt_id": str(metadata.get("activity_attempt_id") or "").strip() or None,
                    "activation_started_at": changed_at,
                    "activation_error": None,
                }
            )
            update_change_set(status="trial_waiting", gate="trial")
            return
        if action == "candidate_prepared":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("candidate preparation requires completed automation")
            if str(delivery.get("status") or "") not in {"activating", "checkpoint"}:
                raise BuilderWorkflowError("candidate result requires an active Trial activity")
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            release_digest = str(metadata.get("release_digest") or "").strip()
            package_digest = str(metadata.get("package_digest") or "").strip()
            if not candidate_id or not release_digest or not package_digest:
                raise BuilderWorkflowError(
                    "candidate preparation requires candidate, release, and package identities"
                )
            delivery.clear()
            delivery.update(
                {
                    "status": "trial",
                    "candidate_id": candidate_id,
                    "release": metadata.get("release"),
                    "release_digest": release_digest,
                    "package_digest": package_digest,
                    "base_release": metadata.get("base_release"),
                    "base_release_digest": metadata.get("base_release_digest"),
                    "trial_workspace": metadata.get("trial_workspace"),
                    "prepared_at": changed_at,
                    "decided_at": None,
                    "stale_reason": None,
                }
            )
            update_change_set(status="trial", gate="trial")
            return
        if action in {"candidate_preparation_failed", "candidate_preparation_unknown"}:
            if str(delivery.get("status") or "") != "activating":
                raise BuilderWorkflowError("Trial failure requires an active Trial activity")
            unknown = action == "candidate_preparation_unknown"
            delivery.update(
                {
                    "status": "unknown" if unknown else "checkpoint",
                    "activation_error": str(metadata.get("error") or "trial_activation_failed")[:1000],
                    "activation_finished_at": changed_at,
                }
            )
            update_change_set(
                status="reconciliation_required" if unknown else "checkpointed",
                gate="trial",
            )
            return
        if action in {"candidate_accepted", "candidate_rejected"}:
            if str(delivery.get("status") or "") != "trial":
                raise BuilderWorkflowError("candidate decision requires an active trial")
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            if candidate_id != str(delivery.get("candidate_id") or ""):
                raise BuilderWorkflowError("candidate decision does not match the active trial")
            candidate_digest = str(metadata.get("candidate_digest") or "").strip()
            expected_digest = str(
                delivery.get("package_digest") or delivery.get("release_digest") or ""
            ).strip()
            if not candidate_digest or candidate_digest != expected_digest:
                raise BuilderWorkflowError(
                    "candidate decision requires the exact immutable candidate digest"
                )
            delivery.update(
                {
                    "status": "accepted" if action == "candidate_accepted" else "rejected",
                    "decided_at": changed_at,
                    "decision_observations": list(metadata.get("observations") or ()),
                }
            )
            update_change_set(
                status="accepted" if action == "candidate_accepted" else "changes_requested",
                gate="publication" if action == "candidate_accepted" else "automation",
            )
            return
        if action == "candidate_stale":
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            if candidate_id != str(delivery.get("candidate_id") or ""):
                raise BuilderWorkflowError("stale candidate does not match the active delivery")
            rebase_plan = metadata.get("rebase_plan")
            if not isinstance(rebase_plan, Mapping):
                raise BuilderWorkflowError("stale candidate requires an exact rebase plan")
            delivery.update(
                {
                    "status": "stale",
                    "stale_reason": rebase_plan.get("stale_reason") or "base_release_moved",
                    "stale_at": changed_at,
                    "replaces_candidate_id": candidate_id,
                    "rebase_plan": dict(rebase_plan),
                }
            )
            update_change_set(status="rebase_required", gate="automation")
            return
        if action == "publication_started":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("publication requires completed automation")
            if (
                str(delivery.get("status") or "") == "publication_waiting"
                and str(publication.get("status") or "") == "publishing"
            ):
                # The compatibility projection may already contain the local
                # waiting mutation when an externally completed promotion is
                # being reconciled into a newer canonical attempt.  Re-admit
                # only the governed transition; do not reset timestamps or
                # dispatch the publication activity here.
                return
            if str(delivery.get("status") or "") != "accepted":
                raise BuilderWorkflowError("publication requires an accepted candidate trial")
            delivery["status"] = "publication_waiting"
            publication.update(
                {
                    "status": "publishing",
                    "activity_attempt_id": str(metadata.get("activity_attempt_id") or "").strip() or None,
                    "started_at": changed_at,
                    "error": None,
                }
            )
            update_change_set(status="publication_waiting", gate="publication")
            return
        if action == "publish":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("publication requires completed automation")
            if str(delivery.get("status") or "") not in {"accepted", "publication_waiting"}:
                raise BuilderWorkflowError("publication result requires an active Publication activity")
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            if candidate_id != str(delivery.get("candidate_id") or ""):
                raise BuilderWorkflowError("publication candidate does not match the accepted trial")
            candidate_digest = str(metadata.get("candidate_digest") or "").strip()
            expected_digest = str(
                delivery.get("package_digest") or delivery.get("release_digest") or ""
            ).strip()
            if not candidate_digest or candidate_digest != expected_digest:
                raise BuilderWorkflowError(
                    "publication requires the exact immutable candidate digest"
                )
            version = str(metadata.get("version") or "").strip()
            if not version:
                raise BuilderWorkflowError("publication version is required")
            release_digest = str(delivery.get("release_digest") or "").strip()
            package_digest = str(delivery.get("package_digest") or "").strip()
            try:
                release_record = applied_release_record(
                    project_id=project_id,
                    candidate_id=candidate_id,
                    version=version,
                    release_digest=release_digest,
                    package_digest=package_digest,
                    apply_evidence=_mapping(metadata.get("apply_evidence")),
                    recorded_at=changed_at,
                )
            except ValueError as exc:
                raise BuilderWorkflowError(str(exc)) from exc
            publication.update(
                {
                    "status": "published",
                    "current_version": version,
                    "published_at": changed_at,
                    "source_automation_task": metadata.get("task_id") or automation.get("head_task_id"),
                    "release": metadata.get("release"),
                    "release_record": release_record,
                }
            )
            delivery.update(
                {
                    "status": "published",
                    "published_at": changed_at,
                    "approval": release_record["approval"],
                    "activation": release_record["activation"],
                    "rollback": release_record["rollback"],
                }
            )
            update_change_set(status="published", gate="complete")
            return
        if action in {"publication_failed", "publication_unknown"}:
            if str(delivery.get("status") or "") != "publication_waiting":
                raise BuilderWorkflowError("Publication failure requires an active Publication activity")
            unknown = action == "publication_unknown"
            publication.update(
                {
                    "status": "unknown" if unknown else "failed",
                    "error": str(metadata.get("error") or "publication_failed")[:1000],
                    "finished_at": changed_at,
                }
            )
            delivery["status"] = "unknown" if unknown else "accepted"
            update_change_set(
                status="reconciliation_required" if unknown else "accepted",
                gate="publication",
            )
            return
        if action in {
            "reconcile_automation",
            "reconcile_verification",
            "reconcile_publication",
        }:
            current = workflow.get("change_set")
            if not isinstance(current, dict) or str(current.get("status") or "") != "reconciliation_required":
                raise BuilderWorkflowError(
                    "workflow reconciliation requires an unknown external outcome"
                )
            evidence_refs = [
                str(item).strip()
                for item in metadata.get("evidence_refs") or []
                if str(item).strip()
            ][:100]
            history = [
                dict(item)
                for item in workflow.get("reconciliation_history") or []
                if isinstance(item, Mapping)
            ]
            history.append(
                {
                    "action": action,
                    "at": changed_at,
                    "actor": str(metadata.get("actor") or "builder"),
                    "evidence_refs": evidence_refs,
                    "previous_delivery_status": str(delivery.get("status") or ""),
                    "previous_publication_status": str(publication.get("status") or ""),
                    "previous_error": (
                        publication.get("error")
                        if action == "reconcile_publication"
                        else delivery.get("activation_error")
                    ),
                }
            )
            workflow["reconciliation_history"] = history[-50:]
            if action == "reconcile_publication":
                if (
                    str(delivery.get("status") or "") != "unknown"
                    or str(publication.get("status") or "") != "unknown"
                ):
                    raise BuilderWorkflowError(
                        "Publication reconciliation requires an unknown Publication result"
                    )
                delivery["status"] = "accepted"
                publication.update(
                    {
                        "status": "ready",
                        "error": None,
                        "reconciled_at": changed_at,
                    }
                )
                update_change_set(status="accepted", gate="publication")
                return
            if action == "reconcile_verification":
                if str(delivery.get("status") or "") != "unknown":
                    raise BuilderWorkflowError(
                        "Verification reconciliation requires an unknown Trial result"
                    )
                # The canonical reconciliation transition returns to
                # ``verification``.  Do not project that state as
                # ``trial_ready`` by restoring the compatibility checkpoint
                # prematurely.  The exact retained package/source identities
                # can be accepted again through ``checkpoint_recorded``, which
                # is the canonical ``accept_verification`` transition.
                delivery["status"] = "idle"
                delivery["activation_error"] = None
                delivery["reconciled_at"] = changed_at
                update_change_set(status="implemented", gate="trial")
                return
            if str(automation.get("status") or "") not in {"failed", "working"}:
                raise BuilderWorkflowError(
                    "Automation reconciliation requires an incomplete Automation result"
                )
            automation["status"] = "failed"
            automation["reconciled_at"] = changed_at
            update_change_set(status="in_progress", gate="automation")
            return
        raise BuilderWorkflowError(f"unsupported Builder workflow transition: {action}")

    def automation_snapshot_root(self, object_type: str, object_id: str) -> Path:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        return Path(self.state_dir or current_state_dir()) / "builder" / "workflow_snapshots" / kind / project_id / "automation"

    def snapshot_current_automation(
        self,
        object_type: str,
        object_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the one retained Automation snapshot used by Preview and the next cycle."""

        kind = _kind(object_type)
        project_id = _project_id(object_id)
        root = self.project_root(kind, project_id)
        snapshot_root = self.automation_snapshot_root(kind, project_id)
        temporary = snapshot_root.with_name(f".{snapshot_root.name}.tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=False)
        copied: list[str] = []
        names = ("webui.json", "scenario.yaml", "scenario.json") if kind == "scenario" else ("skill.yaml",)
        try:
            for name in names:
                source = root / name
                if not source.is_file():
                    continue
                shutil.copy2(source, temporary / name)
                copied.append(name)
            if kind == "scenario" and "webui.json" not in copied:
                raise BuilderWorkflowError("cannot snapshot Automation: webui.json is missing")
            created_at = _now()
            metadata = {
                "schema": "adaos.builder.automation_snapshot.v1",
                "object_type": kind,
                "object_id": project_id,
                "task_id": str(task_id or "").strip() or None,
                "version": self._project_version(kind, project_id),
                "created_at": created_at,
                "files": copied,
            }
            (temporary / "snapshot.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            snapshot_root.parent.mkdir(parents=True, exist_ok=True)
            previous = snapshot_root.with_name(f".{snapshot_root.name}.previous")
            if previous.exists():
                shutil.rmtree(previous)
            if snapshot_root.exists():
                _replace_path(snapshot_root, previous)
            _replace_path(temporary, snapshot_root)
            if previous.exists():
                shutil.rmtree(previous)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return {
            "ok": True,
            "path": str(snapshot_root),
            **metadata,
        }

    def snapshot_current_prototype(
        self,
        object_type: str,
        object_id: str,
        *,
        source_task_id: str | None = None,
        request_text: str | None = None,
    ) -> dict[str, Any]:
        kind = _kind(object_type)
        if kind != "scenario":
            return {"ok": True, "revision": self._project_version(kind, object_id), "created": False}
        root = self.project_root(kind, object_id)
        webui_path = root / "webui.json"
        try:
            webui = json.loads(webui_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuilderWorkflowError(f"cannot snapshot prototype webui.json: {exc}") from exc
        if not isinstance(webui, Mapping) or not isinstance(webui.get("ui"), Mapping):
            raise BuilderWorkflowError("cannot snapshot prototype: webui.json has no ui object")
        revision_dir = root / "ui_revisions"
        revision_dir.mkdir(parents=True, exist_ok=True)
        numbers = [
            int(path.stem)
            for path in revision_dir.glob("*.json")
            if path.stem.isdigit()
        ]
        revision = f"{(max(numbers) + 1) if numbers else 1:03d}"
        created_at = _now()
        payload = {
            "schema": "adaos.builder.ui_revision.v1",
            "revision": revision,
            "created_at": created_at,
            "scenario_id": _project_id(object_id),
            "request": {"text": str(request_text or "Derived safe prototype from Automation result")},
            "patch": {
                "id": f"workflow-return-{revision}",
                "target": "ui",
                "operation": "derive_prototype_from_automation",
                "status": "applied",
                "source_task_id": str(source_task_id or "").strip() or None,
            },
            "after_webui": copy.deepcopy(dict(webui)),
            "preview_state": {},
        }
        path = revision_dir / f"{revision}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (revision_dir / "current.txt").write_text(revision + "\n", encoding="utf-8")
        return {"ok": True, "revision": revision, "path": str(path), "created": True, "created_at": created_at}


__all__ = [
    "BUILDER_CHANGE_SCHEMA",
    "BUILDER_CHANGE_SET_SCHEMA",
    "BUILDER_CONTEXT_PACKET_SCHEMA",
    "BUILDER_INTERACTION_FRAME_SCHEMA",
    "BUILDER_RUN_SCHEMA",
    "BUILDER_WORKFLOW_EVENT",
    "BUILDER_WORKFLOW_SCHEMA",
    "BuilderWorkflowError",
    "BuilderWorkflowService",
]
