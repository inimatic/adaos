from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import psutil
import yaml

from adaos.build_info import BUILD_INFO
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.context_control import ContextControlService
from adaos.services.runtime_paths import current_repo_root, current_state_dir
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_sources import capture_source_snapshot
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, context_packet_prompt_projection


AUTOMATION_SESSION_SCHEMA = "adaos.builder.automation_session.v1"
STANDARD_PROMPT_VERSION = "adaos-skill-realization/0.12.0"
FINALIZATION_HEARTBEAT_SECONDS = 10.0
TRIAL_PREPARATION_RECOVERY_GRACE_SECONDS = 300.0
AUTOMATION_PROJECTION_SCHEMA = "adaos.builder.automation_projection.v1"
_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()
_log = logging.getLogger("adaos.builder.automation")

_ACTIVE_STATUSES = {
    "starting",
    "queued",
    "assigned",
    "workspace_preparing",
    "in_progress",
    "tests_running",
    "commit_ready",
}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}
_STATUS_RANK = {
    "starting": 0,
    "queued": 0,
    "assigned": 1,
    "workspace_preparing": 1,
    "in_progress": 2,
    "tests_running": 3,
    "commit_ready": 4,
    "completed": 5,
    "failed": 5,
    "cancelled": 5,
    "expired": 5,
}
_AUTOMATION_STEPS = (
    ("queued", "builder.automation.step.queued", 0),
    ("workspace", "builder.automation.step.workspace", 1),
    ("implementation", "builder.automation.step.implementation", 2),
    ("verification", "builder.automation.step.verification", 3),
    ("result", "builder.automation.step.result", 4),
)
_DEVELOPMENT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_RUNTIME_DIAGNOSTIC_MAX_FILES = 4096
_RUNTIME_DIAGNOSTIC_MAX_BYTES = 128 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any, *, fallback: str = "project") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or fallback


def _sanitized_mcp_profile(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    mcp = {
        key: copy.deepcopy(raw_value)
        for key, raw_value in value.items()
        if key not in {"root_mcp", "access_token", "token", "authorization", "secret"}
    }
    root = value.get("root_mcp")
    if isinstance(root, Mapping):
        sanitized_root = {
            key: copy.deepcopy(raw_value)
            for key, raw_value in root.items()
            if key not in {"access_token", "token", "authorization", "secret"}
        }
        if sanitized_root:
            mcp["root_mcp"] = sanitized_root
    return mcp or None


def _reject_transport_corruption(value: Any, *, field: str) -> None:
    """Reject new durable Automation text after Unicode code points were lost."""

    token = str(value or "")
    if "\ufffd" in token or "????" in token:
        raise ValueError(
            f"{field} appears transport-corrupted; submit the original text as UTF-8"
        )


def _brief_digest(value: Any) -> str | None:
    try:
        decoded = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, Mapping):
        return None
    return str(decoded.get("digest") or "").strip() or None


def _brief_payload(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _brief_summary(value: Any) -> str:
    brief = str(value or "").strip()
    payload = _brief_payload(brief)
    repair_hints = (
        payload.get("repair_hints")
        if isinstance(payload.get("repair_hints"), Mapping)
        else {}
    )
    summary = str(
        payload.get("summary")
        or repair_hints.get("change_summary")
        or brief
    ).strip()
    return " ".join(summary.split())


def _workflow_request_projection(value: Any) -> str:
    """Project a full implementation brief into the bounded Change request."""

    brief = str(value or "").strip()
    payload = _brief_payload(brief)
    if not payload:
        return " ".join(brief.split())[:3800]
    repair_hints = (
        dict(payload.get("repair_hints"))
        if isinstance(payload.get("repair_hints"), Mapping)
        else {}
    )
    target = (
        dict(payload.get("target"))
        if isinstance(payload.get("target"), Mapping)
        else {}
    )

    def _items(name: str, *, count: int, length: int) -> list[str]:
        return [
            " ".join(str(item).split())[:length]
            for item in repair_hints.get(name) or []
            if str(item).strip()
        ][:count]

    projection = {
        "schema": "adaos.builder.workflow_request.v1",
        "ticket_id": str(payload.get("ticket_id") or "").strip() or None,
        "summary": _brief_summary(brief)[:700],
        "target": {
            "object_type": str(target.get("object_type") or "").strip() or None,
            "object_id": str(target.get("object_id") or "").strip() or None,
            "component_ref": str(payload.get("component_ref") or "").strip() or None,
        },
        "profile": str(repair_hints.get("profile") or "").strip() or None,
        "target_files": _items("target_files", count=6, length=140),
        "target_refs": _items("target_refs", count=6, length=180),
        "acceptance": _items("acceptance_checks", count=4, length=240),
        "brief_digest": "sha256:" + hashlib.sha256(brief.encode("utf-8")).hexdigest(),
    }
    request = json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
    if len(request) <= 3800:
        return request
    fallback = {
        "schema": projection["schema"],
        "ticket_id": projection["ticket_id"],
        "summary": str(projection["summary"])[:1200],
        "target": projection["target"],
        "profile": projection["profile"],
        "brief_digest": projection["brief_digest"],
    }
    return json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))


def _brief_has_structured_edits(value: Any) -> bool:
    payload = _brief_payload(value)
    repair_hints = (
        payload.get("repair_hints")
        if isinstance(payload.get("repair_hints"), Mapping)
        else {}
    )
    structured = (
        repair_hints.get("structured_edits")
        if isinstance(repair_hints.get("structured_edits"), Mapping)
        else {}
    )
    return bool(
        str(structured.get("schema") or "").strip()
        and any(isinstance(item, Mapping) for item in structured.get("operations") or [])
    )


def _canonical_repair_path(value: Any, *, kind: str, object_id: str) -> str:
    path = str(value or "").replace("\\", "/").strip("/")
    if not path:
        return ""
    if path.split("/", 1)[0] in {"skills", "scenarios", "docs"}:
        return path
    prefix = {"skill": "skills", "scenario": "scenarios"}.get(str(kind).strip())
    target = _safe_token(object_id, fallback="")
    return f"{prefix}/{target}/{path}" if prefix and target else path


def _canonical_repair_hints(
    value: Mapping[str, Any],
    *,
    kind: str,
    object_id: str,
) -> dict[str, Any]:
    hints = copy.deepcopy(dict(value))
    hints["target_files"] = [
        path
        for item in hints.get("target_files") or []
        for path in [_canonical_repair_path(item, kind=kind, object_id=object_id)]
        if path
    ]
    structured = hints.get("structured_edits")
    if isinstance(structured, Mapping):
        structured_copy = copy.deepcopy(dict(structured))
        operations = []
        for raw in structured_copy.get("operations") or []:
            if not isinstance(raw, Mapping):
                continue
            operation = copy.deepcopy(dict(raw))
            operation["path"] = _canonical_repair_path(
                operation.get("path"),
                kind=kind,
                object_id=object_id,
            )
            operations.append(operation)
        structured_copy["operations"] = operations
        hints["structured_edits"] = structured_copy
    return hints


def _iteration_context_projection(
    context_packet: Mapping[str, Any],
    *,
    implementation_brief: str,
    packet_ref: str,
    packet_digest: str | None,
    kind: str,
    project_id: str,
) -> dict[str, Any]:
    projection = context_packet_prompt_projection(
        context_packet,
        implementation_brief=implementation_brief,
    )
    if not _brief_has_structured_edits(implementation_brief):
        return projection
    payload = _brief_payload(implementation_brief)
    repair_hints = (
        dict(payload.get("repair_hints"))
        if isinstance(payload.get("repair_hints"), Mapping)
        else {}
    )
    structured = (
        dict(repair_hints.get("structured_edits"))
        if isinstance(repair_hints.get("structured_edits"), Mapping)
        else {}
    )
    structured_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            structured,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "adaos.builder.deterministic_context_projection.v1",
        "target": {"object_type": kind, "object_id": project_id},
        "context_packet": {
            "ref": packet_ref,
            "digest": packet_digest,
        },
        "repair": {
            "ticket_id": str(payload.get("ticket_id") or "").strip() or None,
            "profile": str(repair_hints.get("profile") or "").strip() or None,
            "target_files": [
                str(item).replace("\\", "/").strip("/")
                for item in repair_hints.get("target_files") or []
                if str(item).strip()
            ][:12],
            "target_refs": [
                " ".join(str(item).split())[:300]
                for item in repair_hints.get("target_refs") or []
                if str(item).strip()
            ][:20],
            "acceptance_checks": [
                " ".join(str(item).split())[:500]
                for item in repair_hints.get("acceptance_checks") or []
                if str(item).strip()
            ][:12],
            "structured_edit_set_digest": structured_digest,
            "operation_count": len(structured.get("operations") or []),
        },
        "authority": {
            "write_scope": f"{kind}:{project_id}",
            "core_mutation": "denied",
            "execution_strategy": "structured_edits",
        },
    }


def _context_projection_brief(
    session: Mapping[str, Any],
    iteration_brief: str,
) -> str:
    canonical_brief = str(session.get("implementation_brief") or "")
    return (
        canonical_brief
        if _brief_has_structured_edits(canonical_brief)
        else iteration_brief
    )


def _cleanup_dev_skill_runtime(skill_id: str) -> dict[str, Any]:
    """Invoke the core-owned DEV runtime lifecycle without deleting source."""

    from adaos.adapters.db import SqliteSkillRegistry
    from adaos.services.agent_context import get_ctx
    from adaos.services.skill.manager import SkillManager

    ctx = get_ctx()
    manager = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )
    return dict(manager.cleanup_dev_runtime(skill_id, purge_data=True))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(path, payload)


def _automation_worker_resource_policy(
    command: Sequence[str],
    *,
    platform_name: str | None = None,
) -> tuple[list[str], int, dict[str, Any]]:
    selected_platform = str(platform_name or os.name).strip().lower()
    requested = str(os.getenv("ADAOS_BUILDER_AUTOMATION_RESOURCE_PRIORITY") or "background").strip().lower()
    if requested in {"normal", "off", "disabled"}:
        return list(command), 0, {
            "mode": "normal",
            "cpu_priority": "inherited",
            "io_priority": "inherited",
        }
    if selected_platform == "nt":
        flag = int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0)
        return list(command), flag, {
            "mode": "background",
            "cpu_priority": "below_normal" if flag else "inherited_unavailable",
            "io_priority": "inherited",
            "inherited_by_children": True,
        }

    try:
        nice_value = max(1, min(int(os.getenv("ADAOS_BUILDER_AUTOMATION_NICE") or "10"), 19))
    except ValueError:
        nice_value = 10
    wrapped = list(command)
    nice_path = shutil.which("nice")
    ionice_path = shutil.which("ionice")
    if nice_path:
        wrapped = [nice_path, "-n", str(nice_value), *wrapped]
    if ionice_path:
        wrapped = [ionice_path, "-c", "3", *wrapped]
    return wrapped, 0, {
        "mode": "background",
        "cpu_priority": f"nice:{nice_value}" if nice_path else "inherited_unavailable",
        "io_priority": "idle" if ionice_path else "inherited_unavailable",
        "inherited_by_children": True,
    }


def _prefer_persisted_session(
    previous: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> bool:
    """Reject a stale projection that would move one task backwards.

    Builder API reads and the durable Automation worker run in separate
    processes.  A process-local lock therefore cannot prevent a slow read from
    persisting an older projection after the worker has committed a terminal
    result.  Explicit recovery remains possible when a validated task has not
    yet produced completion readiness.
    """

    if str(previous.get("session_id") or "") != str(incoming.get("session_id") or ""):
        return False
    previous_task = str(previous.get("current_task_id") or "").strip()
    incoming_task = str(incoming.get("current_task_id") or "").strip()
    if not previous_task or previous_task != incoming_task:
        return False

    previous_status = str(previous.get("status") or "starting").strip() or "starting"
    incoming_status = str(incoming.get("status") or "starting").strip() or "starting"
    previous_readiness = (
        previous.get("completion_readiness")
        if isinstance(previous.get("completion_readiness"), Mapping)
        else {}
    )
    terminal_readiness = bool(
        previous_status == "completed"
        and previous_readiness.get("ok")
        and str(previous_readiness.get("task_id") or "").strip() == previous_task
    )
    incoming_readiness = (
        incoming.get("completion_readiness")
        if isinstance(incoming.get("completion_readiness"), Mapping)
        else {}
    )
    task = incoming.get("task") if isinstance(incoming.get("task"), Mapping) else {}
    explicit_finalization = bool(
        incoming_status == "commit_ready"
        and str(incoming.get("finalizing_task_id") or "").strip() == incoming_task
        and str(task.get("status") or "").strip() == "completed"
        and isinstance(incoming.get("last_result"), Mapping)
    )
    explicit_checkpoint_recovery = bool(
        explicit_finalization
        and incoming.get("reuse_confirmed_checkpoints") is True
        and (
            not isinstance(previous_readiness.get("workflow_checkpoint"), Mapping)
            or incoming.get("rebind_confirmed_checkpoint") is True
        )
    )
    if terminal_readiness and not explicit_checkpoint_recovery and not (
        incoming_status == "completed"
        and incoming_readiness.get("ok")
        and str(incoming_readiness.get("task_id") or "").strip() == incoming_task
    ):
        return True

    previous_rank = _STATUS_RANK.get(previous_status, -1)
    incoming_rank = _STATUS_RANK.get(incoming_status, -1)
    if previous_rank > incoming_rank:
        if not explicit_finalization or (terminal_readiness and not explicit_checkpoint_recovery):
            return True

    previous_updated = str(previous.get("updated_at") or "").strip()
    incoming_updated = str(incoming.get("updated_at") or "").strip()
    return bool(
        previous_updated
        and incoming_updated
        and previous_updated > incoming_updated
    )


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _publish_automation_changed(projection: Mapping[str, Any]) -> None:
    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit

        emit(
            get_ctx().bus,
            "builder.automation.changed",
            dict(projection),
            source="builder.automation",
        )
    except Exception:
        # The service also runs in validation, tests, and early startup where no
        # process-wide AgentContext exists yet. Persistence remains authoritative.
        return


@dataclass(slots=True)
class BuilderAutomationService:
    state_dir: Path
    repo_root: Path
    dev_skills_root: Path
    dev_scenarios_root: Path
    runs_root: Path | None = None
    worker_factory: Callable[[], LocalSkillFactoryWorker] | None = None
    event_sink: Callable[[Mapping[str, Any]], None] | None = None
    workspace_service: BuilderWorkspaceService | None = None
    workflow_service: BuilderWorkflowService | None = None
    context_service: ContextControlService | None = None
    codex_usage_reporter: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    background: bool = True
    materialize_on_completion: bool = True
    factory: SkillFactoryService = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        self.repo_root = Path(self.repo_root)
        self.dev_skills_root = Path(self.dev_skills_root)
        self.dev_scenarios_root = Path(self.dev_scenarios_root)
        self.runs_root = Path(self.runs_root or (self.state_dir / "skill_factory" / "local_runs"))
        self.factory = SkillFactoryService(state_dir=self.state_dir)

    @classmethod
    def from_context(cls, *, background: bool = True) -> "BuilderAutomationService":
        from adaos.services.economic_policy import report_codex_usage_to_root

        workspace = BuilderWorkspaceService.from_context()
        repo_root = Path(workspace.repo_root or current_repo_root() or Path.cwd())
        dev_skills = workspace.dev_skills_root or (repo_root / ".adaos" / "workspace" / "skills")
        dev_scenarios = workspace.dev_scenarios_root or (repo_root / ".adaos" / "workspace" / "scenarios")
        return cls(
            state_dir=Path(workspace.state_dir or current_state_dir()),
            repo_root=repo_root,
            dev_skills_root=Path(dev_skills),
            dev_scenarios_root=Path(dev_scenarios),
            event_sink=_publish_automation_changed,
            workspace_service=workspace,
            codex_usage_reporter=report_codex_usage_to_root,
            background=background,
        )

    @property
    def root(self) -> Path:
        path = self.state_dir / "builder" / "automation"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _workflow(self) -> BuilderWorkflowService:
        if self.workflow_service is None:
            self.workflow_service = BuilderWorkflowService(
                dev_skills_root=self.dev_skills_root,
                dev_scenarios_root=self.dev_scenarios_root,
                state_dir=self.state_dir,
            )
        return self.workflow_service

    def _contexts(self) -> ContextControlService:
        if self.context_service is None:
            self.context_service = ContextControlService(state_dir=self.state_dir)
        return self.context_service

    def current_workflow_head(
        self,
        *,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        """Return the live Change head used for lifecycle routing decisions."""

        kind, project_id = self._project_ref(object_type, object_id)
        workflow = self._workflow().describe(kind, project_id)
        governed = (
            workflow.get("governed")
            if isinstance(workflow.get("governed"), Mapping)
            else {}
        )
        change_set = (
            workflow.get("change_set")
            if isinstance(workflow.get("change_set"), Mapping)
            else {}
        )
        return {
            "schema": "adaos.builder.workflow_head.v1",
            "object_type": kind,
            "object_id": project_id,
            "state": str(governed.get("state") or "").strip() or None,
            "generation": governed.get("generation"),
            "change_set_id": str(change_set.get("change_set_id") or "").strip()
            or None,
            "change_set_status": str(change_set.get("status") or "").strip()
            or None,
        }

    def _compile_iteration_context(
        self,
        *,
        session: Mapping[str, Any],
        kind: str,
        project_id: str,
        context_packet: Mapping[str, Any],
        source_snapshot: Mapping[str, Any],
        implementation_brief: str,
    ) -> dict[str, Any]:
        compile_started_at = time.perf_counter()
        service = self._contexts()
        now = _now_iso()
        component_ref = f"{kind}:{project_id}"
        project_ref = self._context_project_ref(
            session=session,
            component_ref=component_ref,
            fallback_project_id=project_id,
        )
        run_ref = f"builder-run:{session.get('session_id')}:{int(session.get('iteration') or 0)}"
        change_ref = f"change:{session.get('change_set_id')}"
        packet_artifact = service.put_artifact(dict(context_packet))
        projection = _iteration_context_projection(
            context_packet,
            implementation_brief=_context_projection_brief(
                session,
                implementation_brief,
            ),
            packet_ref=packet_artifact["ref"],
            packet_digest=str(context_packet.get("digest") or "").strip() or None,
            kind=kind,
            project_id=project_id,
        )
        platform = service.register_capsule(
            {
                "kind": "platform",
                "subject_refs": ["platform:adaos"],
                "authority_ref": "core:adaos",
                "trust_class": "accepted",
                "sensitivity": "workspace",
                "license": "internal",
                "retention_class": "accepted_release_lineage",
                "source_digests": {
                    "core": f"git:{BUILD_INFO.git_commit}" if BUILD_INFO.git_commit else BUILD_INFO.version,
                    "prompt_profile": STANDARD_PROMPT_VERSION,
                },
                "valid_from": BUILD_INFO.build_date,
                "recorded_at": BUILD_INFO.build_date,
                "summary": f"AdaOS {BUILD_INFO.version} public Builder and SDK contracts.",
                "content": {
                    "build_version": BUILD_INFO.version,
                    "git_commit": BUILD_INFO.git_commit or None,
                    "standard_prompt_version": STANDARD_PROMPT_VERSION,
                    "authority": "Public SDK/API/ABI only; project Builder cannot mutate core.",
                },
                "metadata": {"utility": 1.0, "cache_class": "stable_prefix"},
            }
        )
        project = service.register_capsule(
            {
                "kind": "project",
                "subject_refs": [project_ref, component_ref],
                "authority_ref": project_ref,
                "trust_class": "accepted",
                "sensitivity": "workspace",
                "license": "internal",
                "retention_class": "project_generation",
                "source_digests": {
                    "source_snapshot": source_snapshot.get("digest"),
                    "builder_context_packet": context_packet.get("digest"),
                },
                "valid_from": str(source_snapshot.get("created_at") or now),
                "recorded_at": now,
                "summary": f"Current {component_ref} source generation and governed Change.",
                "index": [
                    {"kind": "canonical_builder_packet", "ref": packet_artifact["ref"], "digest": packet_artifact["digest"]},
                ],
                "content": projection,
                "metadata": {"utility": 1.0, "source_generation": source_snapshot.get("digest")},
            }
        )
        ticket_ids = [
            str(item).strip()
            for item in [
                dict(session.get("links") or {}).get("development_ticket_id"),
                *(dict(session.get("links") or {}).get("development_ticket_ids") or []),
            ]
            if str(item or "").strip()
        ]
        task = service.register_capsule(
            {
                "kind": "task",
                "subject_refs": [run_ref, change_ref, *[f"dev-ticket:{item}" for item in ticket_ids]],
                "authority_ref": change_ref,
                "trust_class": "validated",
                "sensitivity": "workspace",
                "license": "internal",
                "retention_class": "episodic_run",
                "source_digests": {"builder_context_packet": context_packet.get("digest")},
                "valid_from": now,
                "recorded_at": now,
                "summary": implementation_brief[:2000],
                "content": {
                    "implementation_brief": implementation_brief,
                    "links": dict(session.get("links") or {}),
                    "change_set_id": session.get("change_set_id"),
                    "iteration": session.get("iteration"),
                },
                "metadata": {"utility": 1.0, "working_context": True},
            }
        )
        service.add_relationship(
            {
                "from_capsule_id": task["capsule_id"],
                "to_capsule_id": project["capsule_id"],
                "relation_type": "implements",
                "required": True,
            }
        )
        service.add_relationship(
            {
                "from_capsule_id": project["capsule_id"],
                "to_capsule_id": platform["capsule_id"],
                "relation_type": "uses",
                "required": True,
            }
        )
        service.bind_subject(
            subject_ref=project_ref,
            capsule_id=project["capsule_id"],
            purpose="builder.automation",
            audience="builder",
            actor_ref="builder.automation",
            reason="source_generation_selected",
        )
        service.bind_subject(
            subject_ref=run_ref,
            capsule_id=task["capsule_id"],
            purpose="builder.automation",
            audience="builder",
            actor_ref="builder.automation",
            reason="automation_iteration_started",
        )
        resolution = service.resolve(
            {
                "subject_refs": [run_ref, project_ref],
                "purpose": "builder.automation",
                "audience": "builder",
                "policy": {
                    "minimum_trust": "validated",
                    "allowed_sensitivity": ["public", "subnet", "workspace"],
                    "allow_tainted": False,
                },
            }
        )
        execution_budget = (
            dict(session.get("execution_budget"))
            if isinstance(session.get("execution_budget"), Mapping)
            else {}
        )
        explicit_context_budget = int(execution_budget.get("max_context_tokens") or 0)
        model_budget = int(execution_budget.get("max_model_tokens") or execution_budget.get("max_tokens") or 0)
        context_budget = explicit_context_budget or max(8_000, min(32_000, model_budget // 4 if model_budget else 16_000))
        plan = service.plan(
            {
                "resolution": resolution,
                "token_budget": context_budget,
                "model_profile": dict(session.get("agent_profile") or {}),
            }
        )
        if plan["status"] != "ready":
            raise ValueError("Builder Context Plan is insufficient for Automation")
        compilation = service.compile(
            {
                "plan": plan,
                "output_format": "min_json",
                "role_authority": {
                    "role": "project_builder",
                    "write_scope": component_ref,
                    "core_mutation": "denied",
                },
                "output_contract": {"result": "skill_factory.dev_result.v1"},
            }
        )
        return {
            "run_ref": run_ref,
            "project_ref": project_ref,
            "capsule_refs": [task["capsule_id"], project["capsule_id"], platform["capsule_id"]],
            "context_packet_ref": packet_artifact["ref"],
            "context_packet_digest": context_packet.get("digest"),
            "context_packet_artifact_digest": packet_artifact["digest"],
            "context_projection": projection,
            "resolution_ref": resolution["resolution_ref"],
            "plan_id": plan["plan_id"],
            "plan_ref": plan["plan_ref"],
            "compiled_context_ref": compilation["packet_ref"],
            "compiled_context_digest": compilation["packet_digest"],
            "model_projection_ref": compilation["model_projection_ref"],
            "model_projection_digest": compilation["model_projection_digest"],
            "context_delta_mode": compilation["delta_mode"],
            "layer_usage": compilation["layer_usage"],
            "selected_refs": compilation["selected_refs"],
            "omitted": plan["omitted"],
            "denied": plan["denied"],
            "unavailable": plan["unavailable"],
            "estimated_tokens": plan["estimated_tokens"],
            "token_budget": plan["token_budget"],
            "context_latency_ms": max(
                0,
                int((time.perf_counter() - compile_started_at) * 1000),
            ),
        }

    def _context_project_ref(
        self,
        *,
        session: Mapping[str, Any],
        component_ref: str,
        fallback_project_id: str,
    ) -> str:
        development_session_id = str(session.get("development_session_id") or "").strip()
        if development_session_id:
            development_session, _ = self._load_development_session(
                development_session_id,
                target_ref=component_ref,
            )
            project_ref = str(development_session.get("project_ref") or "").strip()
            if project_ref.startswith("project:"):
                return project_ref

        links = dict(session.get("links") or {})
        for key in ("development_ticket_project_ref", "project_ref"):
            project_ref = str(links.get(key) or "").strip()
            if project_ref.startswith("project:"):
                return project_ref
        for key in ("development_ticket_project_id", "project_id"):
            project_id = str(links.get(key) or "").strip()
            if project_id and ":" not in project_id:
                return f"project:{project_id}"

        materialization = (
            links.get("development_source_materialization")
            if isinstance(links.get("development_source_materialization"), Mapping)
            else {}
        )
        materialized_project_id = str(materialization.get("project_id") or "").strip()
        if materialized_project_id and ":" not in materialized_project_id:
            return f"project:{materialized_project_id}"
        return f"project:{fallback_project_id}"

    def _load_development_session(
        self,
        session_id: str,
        *,
        target_ref: str,
    ) -> tuple[dict[str, Any], Path]:
        token = str(session_id or "").strip()
        if not _DEVELOPMENT_SESSION_ID_RE.fullmatch(token):
            raise ValueError("development_session_id is invalid")
        root = (self.state_dir / "builder" / "development_sessions").resolve()
        path = (root / token / "session.json").resolve()
        if path.parent.parent != root or not path.is_file():
            raise ValueError("development session is unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("development session is unreadable") from exc
        if not isinstance(value, Mapping):
            raise ValueError("development session must be an object")
        from adaos.sdk.builder.development_sessions import validate as validate_session

        session = validate_session(value)
        admitted_targets = {
            str(item.get("ref") or "")
            for group in session["targets"].values()
            for item in group
            if isinstance(item, Mapping)
        }
        if target_ref not in admitted_targets:
            raise ValueError("development session does not admit the Automation target")
        if str(session.get("status") or "") not in {"ready", "active"}:
            raise ValueError("development session is not open for Automation")
        return session, path

    def _development_context(
        self,
        session_id: str,
        *,
        target_ref: str,
    ) -> tuple[dict[str, Any], list[tuple[str, Path, str]]]:
        session, state_path = self._load_development_session(
            session_id,
            target_ref=target_ref,
        )
        token = str(session["session_id"])
        context_root = f".adaos_context/{token}"
        attachments: list[tuple[str, Path, str]] = []
        artifact_receipts: list[dict[str, Any]] = []
        for index, item in enumerate(session.get("artifact_inputs") or []):
            source = Path(str(item["root_path"])).resolve()
            if not source.is_dir():
                raise ValueError(f"development artifact input is unavailable: {item['ref']}")
            target_path = f"{context_root}/artifacts/{index:02d}"
            attachments.append((f"development_artifact_{index:02d}", source, target_path))
            artifact_receipts.append(
                {
                    "ref": item["ref"],
                    "access": "read-only",
                    "manifest_digest": item["manifest_digest"],
                    "context_digest": item.get("context_digest"),
                    "audience": item.get("audience"),
                    "path": target_path,
                }
            )
        instruction_receipts: list[dict[str, Any]] = []
        instruction_root = (state_path.parent / "instructions").resolve()
        if session.get("instruction_inputs"):
            if not instruction_root.is_dir():
                raise ValueError("development instruction root is unavailable")
            attachments.append(
                ("development_instructions", instruction_root, f"{context_root}/instructions")
            )
        for item in session.get("instruction_inputs") or []:
            source = Path(str(item["path"])).resolve()
            if source.parent != instruction_root or not source.is_file():
                raise ValueError(f"development instruction is unavailable: {item['kind']}")
            payload = source.read_bytes()
            media_type = str(item.get("media_type") or "").lower()
            digest_mode = str(item.get("digest_mode") or "").strip() or (
                "canonical-json" if media_type == "application/json" else "bytes"
            )
            if digest_mode == "canonical-json":
                try:
                    decoded = json.loads(payload.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"development JSON instruction is invalid: {item['kind']}") from exc
                if not isinstance(decoded, Mapping):
                    raise ValueError(f"development JSON instruction must be an object: {item['kind']}")
                content_digest = _canonical_digest(decoded)
            else:
                content_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            if content_digest != str(item["content_digest"]):
                raise ValueError(f"development instruction digest drifted: {item['kind']}")
            instruction_receipts.append(
                {
                    "ref": item["ref"],
                    "kind": item["kind"],
                    "access": "read-only",
                    "media_type": item["media_type"],
                    "digest_mode": digest_mode,
                    "content_digest": item["content_digest"],
                    "path": f"{context_root}/instructions/{source.name}",
                }
            )
        identity = {
            "schema": "adaos.builder.development_context_receipt.v1",
            "session_id": token,
            "project_ref": session["project_ref"],
            "target_ref": target_ref,
            "request": session["handoff"].get("request"),
            "execution_budget": copy.deepcopy(session["handoff"].get("execution_budget")),
            "validation_budget": copy.deepcopy(session["handoff"].get("validation_budget")),
            "agent_profile": copy.deepcopy(session["handoff"].get("agent_profile")),
            "artifact_inputs": artifact_receipts,
            "instruction_inputs": instruction_receipts,
            "acceptance_profiles": list(session.get("acceptance_profiles") or []),
            "acceptance_requirements": copy.deepcopy(
                list(session.get("acceptance_requirements") or [])
            ),
            "prohibited_actions": list(session["handoff"]["prohibited_actions"]),
        }
        return {**identity, "digest": _canonical_digest(identity)}, attachments

    def _development_instruction(
        self,
        session_id: str,
        *,
        target_ref: str,
        kind: str,
    ) -> tuple[dict[str, Any], Path]:
        """Load one digest-verified JSON instruction from a Development Session."""

        session, state_path = self._load_development_session(
            session_id,
            target_ref=target_ref,
        )
        descriptor = next(
            (
                dict(item)
                for item in session.get("instruction_inputs") or []
                if isinstance(item, Mapping) and str(item.get("kind") or "") == kind
            ),
            None,
        )
        if descriptor is None:
            raise ValueError(f"development session is missing the {kind} instruction")
        instruction_root = (state_path.parent / "instructions").resolve()
        source = Path(str(descriptor.get("path") or "")).resolve()
        if source.parent != instruction_root or not source.is_file():
            raise ValueError(f"development instruction is unavailable: {kind}")
        try:
            value = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"development JSON instruction is invalid: {kind}") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"development JSON instruction must be an object: {kind}")
        if _canonical_digest(value) != str(descriptor.get("content_digest") or ""):
            raise ValueError(f"development instruction digest drifted: {kind}")
        return dict(value), source

    def _rebind_development_session(
        self,
        session: dict[str, Any],
        *,
        development_session_id: str,
    ) -> bool:
        """Adopt a newer exact Development Session before a terminal retry.

        A long-lived Builder Automation projection may outlive the compilation
        or consumer ABI that created it.  Reusing its old instruction envelope
        would let Codex implement one contract and make the active consumer
        validate another.  Rebinding is therefore explicit, target-scoped and
        digest-verified; it also replaces the cached AutomationBrief with the
        exact brief owned by the incoming Development Session.
        """

        incoming = str(development_session_id or "").strip()
        current = str(session.get("development_session_id") or "").strip()
        if not incoming or incoming == current:
            return False
        kind = str(session.get("object_type") or "").strip()
        project_id = str(session.get("object_id") or "").strip()
        target_ref = f"{kind}:{project_id}"
        brief, brief_path = self._development_instruction(
            incoming,
            target_ref=target_ref,
            kind="automation_brief",
        )
        changed_at = _now_iso()
        session.setdefault("development_session_history", []).append(
            {
                "development_session_id": current or None,
                "automation_brief_digest": _brief_digest(session.get("implementation_brief")),
                "rebound_at": changed_at,
            }
        )
        session["development_session_history"] = session["development_session_history"][-20:]
        session["development_session_id"] = incoming
        session["implementation_brief"] = json.dumps(
            brief,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        session["brief_path"] = str(brief_path)
        session["development_session_rebound_at"] = changed_at
        session["updated_at"] = changed_at
        return True

    @staticmethod
    def _change_id(*, session_id: str, iteration: int, seed: str) -> str:
        identity = f"{session_id}:{max(0, int(iteration))}:{seed}"
        return "builder_change_automation_" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:16]

    def _capture_preview_binding(self, session: dict[str, Any]) -> None:
        """Remember the user's Preview choice so completion cannot overwrite a later one."""

        if str(session.get("object_type") or "").strip().lower().rstrip("s") != "scenario":
            return
        try:
            from adaos.services.builder.workbench import BuilderWorkbenchService

            workbench = BuilderWorkbenchService(state_dir=self.state_dir)
            binding = dict(
                workbench.get_workspace_binding(
                    str(session.get("webspace_id") or "desktop").strip() or "desktop"
                )
                or {}
            )
            target = binding.get("preview_target")
            session["preview_binding_at_submit"] = {
                "captured": True,
                "updated_at": binding.get("updated_at"),
                "target": dict(target) if isinstance(target, Mapping) else None,
            }
        except Exception as exc:
            _log.debug("Builder Preview intent capture skipped: %s", exc)

    @staticmethod
    def _preview_binding_unchanged(
        session: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> bool:
        captured = session.get("preview_binding_at_submit")
        if not isinstance(captured, Mapping) or not bool(captured.get("captured")):
            return False
        current_target = binding.get("preview_target")
        normalized_current = dict(current_target) if isinstance(current_target, Mapping) else None
        submitted_target = captured.get("target")
        normalized_submitted = dict(submitted_target) if isinstance(submitted_target, Mapping) else None
        return (
            normalized_current == normalized_submitted
            and binding.get("updated_at") == captured.get("updated_at")
        )

    @staticmethod
    def _preview_target_matches_project(
        target: Mapping[str, Any] | None,
        *,
        object_type: str,
        object_id: str,
    ) -> bool:
        if not target:
            return False
        return (
            str(target.get("object_type") or "").strip().lower().rstrip("s")
            == str(object_type or "").strip().lower().rstrip("s")
            and str(target.get("object_id") or "").strip() == str(object_id or "").strip()
        )

    def start_from_execute(
        self,
        *,
        object_type: str,
        object_id: str,
        implementation_brief: str,
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        brief_path: str | None = None,
        change_set_id: str | None = None,
        prototype_handoff: Mapping[str, Any] | None = None,
        development_session_id: str | None = None,
        links: Mapping[str, Any] | None = None,
        execution_budget: Mapping[str, Any] | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind, project_id = self._project_ref(object_type, object_id)
        brief = str(implementation_brief or "").strip()
        if not brief:
            raise ValueError("implementation_brief is required after Prompt IDE Execute")
        _reject_transport_corruption(brief, field="implementation_brief")
        external_links = dict(links) if isinstance(links, Mapping) else {}
        admitted_execution_budget = dict(execution_budget) if isinstance(execution_budget, Mapping) else None
        admitted_agent_profile = dict(agent_profile) if isinstance(agent_profile, Mapping) else None
        admitted_mcp = _sanitized_mcp_profile(mcp)
        admitted_development_session_id = str(development_session_id or "").strip() or None
        if admitted_development_session_id:
            self._load_development_session(
                admitted_development_session_id,
                target_ref=f"{kind}:{project_id}",
            )
        admitted_handoff: dict[str, Any] | None = None
        if prototype_handoff is not None:
            from adaos.services.builder.prototype_handoff import admit_automation_handoff

            admitted_handoff = admit_automation_handoff(
                prototype_handoff,
                expected_project_ref=f"{kind}:{project_id}",
            )
        workflow_before = self._workflow().describe(kind, project_id)
        if workflow_before.get("archived"):
            raise ValueError("archived projects cannot start automation")
        active_change_set = (
            workflow_before.get("change_set")
            if isinstance(workflow_before.get("change_set"), Mapping)
            else {}
        )
        if (
            active_change_set
            and not change_set_id
            and admitted_handoff is None
            and str(active_change_set.get("status") or "").strip().lower()
            not in {"published", "rejected", "superseded"}
            and str(active_change_set.get("gate") or "").strip().lower() != "automation"
            and str(
                (
                    workflow_before.get("governed")
                    if isinstance(workflow_before.get("governed"), Mapping)
                    else {}
                ).get("state")
                or ""
            ).strip()
            in {"verification", "trial_ready", "trial_review"}
        ):
            # A verified checkpoint is still the active unpublished Change.
            # Follow-up Dev Tickets belong to that trial batch, but each one
            # receives a separate Automation task and usage receipt.
            ticket_id = str(external_links.get("development_ticket_id") or "").strip()
            issue_seed = ticket_id or hashlib.sha256(brief.encode("utf-8")).hexdigest()[:20]
            issue_id = f"automation-followup-{issue_seed}"[:160]
            try:
                brief_payload = json.loads(brief)
            except (TypeError, ValueError):
                brief_payload = {}
            if not isinstance(brief_payload, Mapping):
                brief_payload = {}
            repair_hints = (
                brief_payload.get("repair_hints")
                if isinstance(brief_payload.get("repair_hints"), Mapping)
                else {}
            )
            issue_summary = " ".join(
                str(
                    brief_payload.get("summary")
                    or repair_hints.get("change_summary")
                    or brief
                ).split()
            )[:3800]
            existing_issue_ids = {
                str(item.get("issue_id") or "").strip()
                for item in active_change_set.get("issues") or []
                if isinstance(item, Mapping)
            }
            if issue_id in existing_issue_ids:
                repair_revision = str(
                    external_links.get("builder_repair_id")
                    or external_links.get("repair_id")
                    or ""
                ).strip()
                revision_seed = repair_revision or brief
                revision_suffix = hashlib.sha256(
                    revision_seed.encode("utf-8")
                ).hexdigest()[:12]
                issue_id = f"{issue_id[:147]}-{revision_suffix}"
            if issue_id not in existing_issue_ids:
                extended = self._workflow().transition(
                    kind,
                    project_id,
                    "change_issues_added",
                    actor="builder.automation.compat",
                    reason="follow-up Dev Ticket admitted into the active trial batch",
                    metadata={
                        "change_set_id": str(active_change_set.get("change_set_id") or ""),
                        "request": issue_summary,
                        "issues": [
                            {
                                "issue_id": issue_id,
                                "title": issue_summary[:240],
                                "lane": "automation",
                                "status": "open",
                                "acceptance_criteria": [
                                    f"The follow-up implementation satisfies: {issue_summary}"[:500]
                                ],
                            }
                        ],
                        "source_message_ids": ([ticket_id] if ticket_id else []),
                        "idempotency_key": (
                            f"builder-automation-followup:{active_change_set.get('change_set_id')}:{issue_id}"
                        ),
                    },
                )
                workflow_before = (
                    extended.get("workflow")
                    if isinstance(extended.get("workflow"), Mapping)
                    else self._workflow().describe(kind, project_id)
                )
                active_change_set = (
                    workflow_before.get("change_set")
                    if isinstance(workflow_before.get("change_set"), Mapping)
                    else {}
                )
        if not active_change_set or str(active_change_set.get("status") or "").strip().lower() in {
            "published",
            "rejected",
            "superseded",
        }:
            # Compatibility callers may still enter Automation directly. They
            # no longer bypass the target model: project one bounded
            # automation_direct Change before constructing the execution
            # capsule. The normal Builder control path already supplies a
            # richer Change and therefore never uses this fallback.
            planned_at = _now_iso()
            request_digest = hashlib.sha256(
                f"{kind}:{project_id}:{brief}:{planned_at}".encode("utf-8")
            ).hexdigest()[:20]
            workflow_request = _workflow_request_projection(brief)
            issue_summary = _brief_summary(brief)
            planned = self._workflow().transition(
                kind,
                project_id,
                "plan_change_set",
                actor="builder.automation.compat",
                reason="direct Automation entry projected into canonical Change",
                metadata={
                    "change_set_id": f"CH-automation-{request_digest}",
                    "run_id": f"RUN-plan-{request_digest}",
                    "request": workflow_request,
                    "issues": [
                        {
                            "issue_id": f"automation-{request_digest}",
                            "title": issue_summary[:240],
                            "lane": "automation",
                            "status": "open",
                            "acceptance_criteria": [
                                f"The implementation and its tests satisfy: {issue_summary}"[:500]
                            ],
                        }
                    ],
                    "source_message_ids": [],
                },
            )
            workflow_before = (
                planned.get("workflow")
                if isinstance(planned.get("workflow"), Mapping)
                else self._workflow().describe(kind, project_id)
            )
            active_change_set = (
                workflow_before.get("change_set")
                if isinstance(workflow_before.get("change_set"), Mapping)
                else {}
            )
        active_change_set_id = str(active_change_set.get("change_set_id") or "").strip()
        requested_change_set_id = str(change_set_id or active_change_set_id).strip() or None
        if change_set_id and active_change_set_id and str(change_set_id).strip() != active_change_set_id:
            raise ValueError("change_set_id does not match the active Builder change set")
        if (
            active_change_set_id
            and str(active_change_set.get("status") or "")
            not in {"published", "rejected", "superseded"}
            and str(active_change_set.get("gate") or "") != "automation"
        ):
            raise ValueError(
                "the active change set must pass its Prototype approval gate before Automation starts"
            )
        with _LOCK:
            current = self.get_session(kind, project_id)
            if current and current.get("status") in {"queued", "assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready"}:
                if admitted_handoff is not None:
                    current_handoff = (
                        current.get("prototype_handoff")
                        if isinstance(current.get("prototype_handoff"), Mapping)
                        else None
                    )
                    current_digest = str((current_handoff or {}).get("digest") or "")
                    incoming_digest = str(admitted_handoff.get("digest") or "")
                    if current_digest and current_digest != incoming_digest:
                        raise ValueError("another Prototype handoff already owns the active Automation session")
                    if not current_digest:
                        current["prototype_handoff"] = copy.deepcopy(admitted_handoff)
                        current["updated_at"] = _now_iso()
                        self._save_session(current)
                incoming_conversation_id = str(conversation_id or "").strip()
                if incoming_conversation_id and not str(current.get("conversation_id") or "").strip():
                    current["conversation_id"] = incoming_conversation_id
                    current["updated_at"] = _now_iso()
                    self._save_session(current)
                current_change_set_id = str(current.get("change_set_id") or "").strip() or None
                if requested_change_set_id and not current_change_set_id:
                    # One-time migration for pre-Change queued sessions. The
                    # already queued task is retained and linked to the Change
                    # just projected from the same implementation brief; no
                    # second Codex task is submitted.
                    current["change_set_id"] = requested_change_set_id
                    current["canonical_change_id"] = requested_change_set_id
                    current["updated_at"] = _now_iso()
                    self._save_session(current)
                    current_change_set_id = requested_change_set_id
                if requested_change_set_id and current_change_set_id != requested_change_set_id:
                    raise ValueError("another Builder change set already owns the active Automation session")
                if external_links:
                    current_links = current.get("links") if isinstance(current.get("links"), Mapping) else {}
                    merged_links = {**dict(current_links), **external_links}
                    if merged_links != current_links:
                        current["links"] = merged_links
                        current["updated_at"] = _now_iso()
                        self._save_session(current)
                if admitted_mcp:
                    current_mcp = current.get("mcp") if isinstance(current.get("mcp"), Mapping) else {}
                    merged_mcp = {**dict(current_mcp), **admitted_mcp}
                    if merged_mcp != current_mcp:
                        current["mcp"] = merged_mcp
                        current["updated_at"] = _now_iso()
                        self._save_session(current)
                current_development_session_id = str(
                    current.get("development_session_id") or ""
                ).strip() or None
                if (
                    admitted_development_session_id
                    and current_development_session_id != admitted_development_session_id
                ):
                    raise ValueError("another Development Session already owns the active Automation session")
                refreshed = self.refresh_session(current)
                result = {
                    "ok": True,
                    "duplicate": True,
                    "session": refreshed,
                    "automation": self.project_session(refreshed),
                }
                # A queued task may outlive the short-lived caller that created
                # it (for example a CLI tool invocation).  A persistent Builder
                # caller can safely recover that task because it has not been
                # assigned to any worker yet.
                if refreshed.get("status") == "queued":
                    self._launch_worker(str(refreshed.get("session_id") or ""))
                    result["worker_relaunched"] = True
                return result
            governed_before = (
                workflow_before.get("governed")
                if isinstance(workflow_before.get("governed"), Mapping)
                else {}
            )
            governed_state = str(governed_before.get("state") or "").strip()
            if governed_state:
                if governed_state != "automation_ready":
                    raise ValueError(
                        "Automation cannot start from governed state "
                        f"{governed_state}; submit an iteration only for the active Change"
                    )
            elif str(workflow_before.get("active_phase") or "prototype") != "prototype":
                # Legacy state without a canonical governed instance retains
                # the pre-workflow compatibility guard.
                raise ValueError(
                    "Automation is already the active process; submit a new Automation iteration instead"
                )
            companion_skill_ids = self._resolve_companion_skill_ids(kind, project_id)
            companion_skill_id = companion_skill_ids[0] if companion_skill_ids else None
            created_artifacts = self._ensure_automation_artifacts_created(
                kind=kind,
                project_id=project_id,
                companion_skill_ids=companion_skill_ids,
                implementation_brief=brief,
            )
            if created_artifacts:
                refreshed_companion_skill_ids = self._resolve_companion_skill_ids(kind, project_id)
                if refreshed_companion_skill_ids != companion_skill_ids:
                    companion_skill_ids = refreshed_companion_skill_ids
                    companion_skill_id = companion_skill_ids[0] if companion_skill_ids else None
            session = {
                "schema": AUTOMATION_SESSION_SCHEMA,
                "session_id": f"automation.{kind}.{project_id}",
                "object_type": kind,
                "object_id": project_id,
                "companion_skill_id": companion_skill_id,
                "companion_skill_ids": companion_skill_ids,
                "webspace_id": str(webspace_id or "desktop"),
                "conversation_id": str(conversation_id or "").strip() or None,
                "topic_id": f"prompt-project:{kind}:{project_id}",
                "implementation_brief": brief,
                "brief_path": str(brief_path or "").strip() or None,
                "change_set_id": requested_change_set_id,
                "canonical_change_id": requested_change_set_id,
                "source_prototype_version": self._project_prototype_ref(kind, project_id),
                "prototype_handoff": admitted_handoff,
                "development_session_id": admitted_development_session_id,
                "execution_budget": admitted_execution_budget,
                "agent_profile": admitted_agent_profile,
                "mcp": admitted_mcp,
                "links": external_links,
                "standard_prompt_version": STANDARD_PROMPT_VERSION,
                "status": "starting",
                "iteration": 0,
                "turns": [],
                "task_history": [],
                "created_artifacts": created_artifacts,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            session["change_id"] = self._change_id(
                session_id=str(session["session_id"]),
                iteration=0,
                seed=str(session["created_at"]),
            )
            self._capture_preview_binding(session)
            submitted = self._submit(session, iteration_instruction="")
            session["status"] = "queued"
            session["current_task_id"] = submitted["task"]["task_id"]
            session["task_history"].append(session["current_task_id"])
            self._save_session(session)
            self._workflow().transition(
                kind,
                project_id,
                "automation_started",
                actor="builder.automation",
                reason="approved prototype handed to Automation",
                metadata={
                    "confirmed": True,
                    "source_prototype_revision": (
                        workflow_before.get("prototype", {}).get("head_revision")
                        if isinstance(workflow_before.get("prototype"), Mapping)
                        else session.get("source_prototype_version")
                    ),
                    "task_id": session.get("current_task_id"),
                    "change_id": session.get("change_id"),
                    # The Run is the exact executor task. The development
                    # change id remains lineage evidence, not execution
                    # identity; completion must close this same Run.
                    "run_id": session.get("current_task_id"),
                    "context_packet_digest": session.get("context_packet_digest"),
                },
            )
        session = self._notify_started_session(session)
        self._launch_worker(session["session_id"])
        return {
            "ok": True,
            "duplicate": False,
            "session": session,
            "task": submitted["task"],
            "automation": self.project_session(session),
        }

    def resume_failed_dev_ticket_repair(
        self,
        *,
        object_type: str,
        object_id: str,
        implementation_brief: str,
        links: Mapping[str, Any],
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        execution_budget: Mapping[str, Any] | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resume the same failed ticket while preserving its candidate and usage history."""

        kind, project_id = self._project_ref(object_type, object_id)
        brief = str(implementation_brief or "").strip()
        ticket_id = str(dict(links or {}).get("development_ticket_id") or "").strip()
        if not brief or not ticket_id:
            raise ValueError("failed Dev Ticket repair resume requires its brief and ticket link")
        _reject_transport_corruption(brief, field="implementation_brief")
        with _LOCK:
            session = self.get_session(kind, project_id)
            if not session:
                raise ValueError("automation_session_not_found")
            session = self.refresh_session(session)
            if str(session.get("status") or "").strip() != "failed":
                raise ValueError("only a failed Automation session can resume a Dev Ticket repair")
            current_links = (
                dict(session.get("links") or {})
                if isinstance(session.get("links"), Mapping)
                else {}
            )
            if str(current_links.get("development_ticket_id") or "").strip() != ticket_id:
                raise ValueError("failed Automation session belongs to another Dev Ticket")
            session["implementation_brief"] = brief
            session["links"] = {**current_links, **dict(links)}
            session["webspace_id"] = str(webspace_id or session.get("webspace_id") or "desktop")
            if str(conversation_id or "").strip():
                session["conversation_id"] = str(conversation_id).strip()
            if isinstance(agent_profile, Mapping):
                session["agent_profile"] = dict(agent_profile)
            admitted_mcp = _sanitized_mcp_profile(mcp)
            if admitted_mcp:
                current_mcp = (
                    dict(session.get("mcp") or {})
                    if isinstance(session.get("mcp"), Mapping)
                    else {}
                )
                session["mcp"] = {**current_mcp, **admitted_mcp}
            session["updated_at"] = _now_iso()
            self._save_session(session)

        result = self.submit_turn(
            text="Resume the requalified Dev Ticket repair from its preserved candidate.",
            object_type=kind,
            object_id=project_id,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
            execution_budget=execution_budget,
        )
        result["resumed_failed_dev_ticket"] = True
        return result

    def start_followup_dev_ticket_repair(
        self,
        *,
        object_type: str,
        object_id: str,
        implementation_brief: str,
        links: Mapping[str, Any],
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        execution_budget: Mapping[str, Any] | None = None,
        agent_profile: Mapping[str, Any] | None = None,
        mcp: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a qualified repair as a new iteration of the active trial Change."""

        kind, project_id = self._project_ref(object_type, object_id)
        brief = str(implementation_brief or "").strip()
        if not brief:
            raise ValueError("follow-up Dev Ticket repair requires its implementation brief")
        _reject_transport_corruption(brief, field="implementation_brief")
        incoming_links = dict(links or {})
        ticket_ids = list(
            dict.fromkeys(
                [
                    str(incoming_links.get("development_ticket_id") or "").strip(),
                    *[
                        str(item).strip()
                        for item in incoming_links.get("development_ticket_ids") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        ticket_ids = [item for item in ticket_ids if item]
        if not ticket_ids:
            raise ValueError("follow-up Dev Ticket repair requires a ticket link")

        with _LOCK:
            session = self.get_session(kind, project_id)
            if not session:
                raise ValueError("automation_session_not_found")
            session = self.refresh_session(session)
            if str(session.get("status") or "").strip() != "completed":
                raise ValueError("follow-up Dev Ticket repair requires a completed Automation trial")
            workflow = self._workflow().describe(kind, project_id)
            governed = workflow.get("governed") if isinstance(workflow.get("governed"), Mapping) else {}
            governed_state = str(governed.get("state") or "").strip()
            change_set = (
                workflow.get("change_set")
                if isinstance(workflow.get("change_set"), Mapping)
                else {}
            )
            change_set_id = str(change_set.get("change_set_id") or "").strip()
            if governed_state == "trial_waiting":
                delivery = (
                    workflow.get("delivery")
                    if isinstance(workflow.get("delivery"), Mapping)
                    else {}
                )
                started_raw = str(delivery.get("activation_started_at") or "").strip()
                age_seconds: float | None = None
                if started_raw:
                    try:
                        started_at = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
                        if started_at.tzinfo is None:
                            started_at = started_at.replace(tzinfo=timezone.utc)
                        age_seconds = max(
                            0.0,
                            (datetime.now(timezone.utc) - started_at).total_seconds(),
                        )
                    except ValueError:
                        age_seconds = None
                if age_seconds is not None and age_seconds < TRIAL_PREPARATION_RECOVERY_GRACE_SECONDS:
                    raise ValueError(
                        "follow-up Dev Ticket repair is waiting for active Trial preparation"
                    )
                recovered = self._workflow().transition(
                    kind,
                    project_id,
                    "candidate_preparation_unknown",
                    actor="builder.automation.recovery",
                    reason="stale Trial preparation was interrupted before immutable candidate identity",
                    metadata={
                        "error": "stale_trial_preparation_without_candidate_identity",
                        "idempotency_key": (
                            f"builder-automation-stale-trial:{change_set_id or project_id}:"
                            f"{str(delivery.get('activity_attempt_id') or 'unknown')}"
                        ),
                    },
                )
                workflow = (
                    recovered.get("workflow")
                    if isinstance(recovered.get("workflow"), Mapping)
                    else self._workflow().describe(kind, project_id)
                )
                governed = (
                    workflow.get("governed")
                    if isinstance(workflow.get("governed"), Mapping)
                    else {}
                )
                governed_state = str(governed.get("state") or "").strip()
                change_set = (
                    workflow.get("change_set")
                    if isinstance(workflow.get("change_set"), Mapping)
                    else {}
                )
                change_set_id = str(change_set.get("change_set_id") or "").strip()
            if governed_state not in {
                "verification",
                "trial_ready",
                "trial_review",
                "publication_ready",
                "reconciliation_required",
            }:
                raise ValueError(
                    "follow-up Dev Ticket repair requires an active verification or trial Change"
                )
            if not change_set_id or str(change_set.get("status") or "").strip() in {
                "published",
                "rejected",
                "superseded",
            }:
                raise ValueError("follow-up Dev Ticket repair requires a non-terminal Change")

            summary = _brief_summary(brief)
            existing_issue_ids = {
                str(item.get("issue_id") or "").strip()
                for item in change_set.get("issues") or []
                if isinstance(item, Mapping)
            }
            issues = []
            for ticket_id in ticket_ids:
                issue_id = f"automation-followup-{ticket_id}"[:160]
                if issue_id in existing_issue_ids:
                    repair_revision = str(
                        incoming_links.get("builder_repair_id")
                        or incoming_links.get("repair_id")
                        or ""
                    ).strip()
                    revision_seed = repair_revision or brief
                    revision_suffix = hashlib.sha256(
                        revision_seed.encode("utf-8")
                    ).hexdigest()[:12]
                    issue_id = f"{issue_id[:147]}-{revision_suffix}"
                if issue_id in existing_issue_ids:
                    continue
                issues.append(
                    {
                        "issue_id": issue_id,
                        "title": summary[:240],
                        "lane": "automation",
                        "status": "open",
                        "acceptance_criteria": [
                            f"The follow-up implementation satisfies: {summary}"[:500]
                        ],
                    }
                )
            if issues:
                self._workflow().transition(
                    kind,
                    project_id,
                    "change_issues_added",
                    actor="builder.automation.dev_ticket",
                    reason="qualified Dev Tickets joined the active trial batch",
                    metadata={
                        "change_set_id": change_set_id,
                        "request": summary,
                        "issues": issues,
                        "source_message_ids": ticket_ids,
                        "idempotency_key": (
                            f"builder-automation-followup:{change_set_id}:"
                            f"{hashlib.sha256('|'.join(ticket_ids).encode('utf-8')).hexdigest()[:20]}"
                        ),
                    },
                )

            current_links = (
                dict(session.get("links") or {})
                if isinstance(session.get("links"), Mapping)
                else {}
            )
            all_ticket_ids = list(
                dict.fromkeys(
                    [
                        str(current_links.get("development_ticket_id") or "").strip(),
                        *[
                            str(item).strip()
                            for item in current_links.get("development_ticket_ids") or []
                            if str(item).strip()
                        ],
                        *ticket_ids,
                    ]
                )
            )
            session["links"] = {
                **current_links,
                **incoming_links,
                "development_ticket_ids": [item for item in all_ticket_ids if item],
            }
            session["implementation_brief"] = brief
            session["webspace_id"] = str(webspace_id or session.get("webspace_id") or "desktop")
            if str(conversation_id or "").strip():
                session["conversation_id"] = str(conversation_id).strip()
            if isinstance(agent_profile, Mapping):
                session["agent_profile"] = dict(agent_profile)
            admitted_mcp = _sanitized_mcp_profile(mcp)
            if admitted_mcp:
                session["mcp"] = admitted_mcp
            session["updated_at"] = _now_iso()
            self._save_session(session)

        result = self.submit_turn(
            text="Apply the newly qualified Dev Ticket repair from implementation_brief.",
            object_type=kind,
            object_id=project_id,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
            execution_budget=execution_budget,
        )
        result["followup_dev_ticket_repair"] = True
        return result

    def _ensure_automation_artifacts_created(
        self,
        *,
        kind: str,
        project_id: str,
        companion_skill_ids: Sequence[str],
        implementation_brief: str,
    ) -> list[dict[str, Any]]:
        service = self.workspace_service or BuilderWorkspaceService.from_context()
        artifacts = [(kind, project_id)]
        if kind == "scenario":
            artifacts.extend(("skill", skill_id) for skill_id in companion_skill_ids)

        created: list[dict[str, Any]] = []
        for artifact_kind, artifact_id in artifacts:
            root = (
                self.dev_scenarios_root / artifact_id
                if artifact_kind == "scenario"
                else self.dev_skills_root / artifact_id
            )
            if root.exists():
                continue
            try:
                materialized = service.materialize_dev_source(
                    kind=artifact_kind,
                    artifact_id=artifact_id,
                )
            except (FileNotFoundError, ValueError, OSError):
                materialized = {}
            if root.exists():
                created.append(
                    {
                        "kind": artifact_kind,
                        "name": artifact_id,
                        "draft_id": None,
                        "artifact_root": str(root),
                        "source": "workspace_materialized",
                        "materialization": materialized or None,
                    }
                )
                continue
            result = service.create_draft(
                kind=artifact_kind,
                artifact_id=artifact_id,
                source_idea=implementation_brief,
                template_id="scenario_default" if artifact_kind == "scenario" else "skill_default",
            )
            created.append(
                {
                    "kind": artifact_kind,
                    "name": artifact_id,
                    "draft_id": str((result.get("draft") or {}).get("draft_id") or "") or None,
                    "artifact_root": str(result.get("artifact_root") or root),
                }
            )
        return created

    def _workspace_skills_root(self) -> Path:
        if self.workspace_service is not None and self.workspace_service.skills_root is not None:
            return Path(self.workspace_service.skills_root)
        return self.repo_root / ".adaos" / "workspace" / "skills"

    def _is_mutable_companion_skill(self, skill_id: str) -> bool:
        """Separate project-owned DEV sources from installed runtime dependencies."""

        token = _safe_token(skill_id, fallback="")
        if not token:
            return False
        if (self.dev_skills_root / token).is_dir():
            return True
        # An installed-only skill is a versioned dependency contract.  It must
        # not be copied into an isolated change set or replaced by a blank DEV
        # scaffold merely because a scenario declares it as required at runtime.
        return not (self._workspace_skills_root() / token).is_dir()

    def _resolve_companion_skill_ids(self, kind: str, project_id: str) -> list[str]:
        """Resolve every declared scenario skill, retaining the conventional primary.

        The current Prototype is allowed to have fewer bindings than the last
        functional result.  Preserve dependencies from both the retained
        Automation snapshot and the installed Workspace publication so a new
        Automation cycle cannot silently drop a functional companion skill.
        """
        if kind != "scenario":
            return [project_id]

        scenario_root = self.dev_scenarios_root / project_id
        manifests: list[Mapping[str, Any]] = []
        paths = [scenario_root / "scenario.yaml"]
        previous_manifest = (
            self.state_dir
            / "builder"
            / "workflow_snapshots"
            / "scenario"
            / project_id
            / "automation"
            / "scenario.yaml"
        )
        if previous_manifest.is_file():
            paths.append(previous_manifest)
        workspace_scenarios_root = (
            Path(self.workspace_service.scenarios_root)
            if self.workspace_service is not None
            and self.workspace_service.scenarios_root is not None
            else self.repo_root / ".adaos" / "workspace" / "scenarios"
        )
        published_manifest = workspace_scenarios_root / project_id / "scenario.yaml"
        if published_manifest.is_file():
            paths.append(published_manifest)
        for path in paths:
            if not path.is_file():
                continue
            try:
                value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            except (OSError, ValueError, yaml.YAMLError):
                value = {}
            if isinstance(value, Mapping):
                manifests.append(value)

        candidates: list[str] = []

        def add(values: Any) -> None:
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, (list, tuple)):
                return
            for value in values:
                token = _safe_token(value, fallback="")
                if token and token not in candidates:
                    candidates.append(token)

        for manifest in manifests:
            runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), Mapping) else {}
            runtime_skills = runtime.get("skills") if isinstance(runtime.get("skills"), Mapping) else {}
            skills = manifest.get("skills") if isinstance(manifest.get("skills"), Mapping) else {}
            add(runtime_skills.get("required"))
            add(skills.get("required"))
            add(manifest.get("depends"))

        conventional = f"{project_id}_skill"
        if conventional in candidates:
            candidates.remove(conventional)
            candidates.insert(0, conventional)
        mutable = [skill_id for skill_id in candidates if self._is_mutable_companion_skill(skill_id)]
        return mutable if candidates else [conventional]

    def _resolve_companion_skill_id(self, kind: str, project_id: str) -> str:
        """Compatibility accessor for the primary scenario companion skill."""
        companions = self._resolve_companion_skill_ids(kind, project_id)
        return companions[0] if companions else ""

    @staticmethod
    def _session_companion_skill_ids(session: Mapping[str, Any]) -> list[str]:
        values = session.get("companion_skill_ids")
        if not isinstance(values, (list, tuple)):
            values = [session.get("companion_skill_id")]
        result: list[str] = []
        for value in values:
            token = _safe_token(value, fallback="")
            if token and token not in result:
                result.append(token)
        return result

    @classmethod
    def _session_changed_companion_skill_ids(
        cls,
        session: Mapping[str, Any],
    ) -> list[str]:
        """Return only companion skills changed by the trusted worker result."""

        companion_ids = cls._session_companion_skill_ids(session)
        result = (
            session.get("last_result")
            if isinstance(session.get("last_result"), Mapping)
            else {}
        )
        changed_paths_value: Any = result.get("changed_paths")
        if not isinstance(changed_paths_value, list):
            provenance = (
                result.get("provenance")
                if isinstance(result.get("provenance"), Mapping)
                else {}
            )
            receipt = (
                provenance.get("structured_edit_receipt")
                if isinstance(provenance.get("structured_edit_receipt"), Mapping)
                else {}
            )
            changed_paths_value = receipt.get("changed_files")
        if not isinstance(changed_paths_value, list):
            # Compatibility with completed sessions produced before workers
            # reported a qualified changed-path manifest.
            return companion_ids

        changed_paths = {
            str(path or "").replace("\\", "/").lstrip("./")
            for path in changed_paths_value
            if str(path or "").strip()
        }
        created_skills = {
            str(item.get("name") or item.get("id") or "").strip()
            for item in session.get("created_artifacts") or []
            if isinstance(item, Mapping)
            and str(item.get("kind") or "").strip().lower().rstrip("s") == "skill"
        }
        return [
            skill_id
            for skill_id in companion_ids
            if skill_id in created_skills
            or any(
                path == f"skills/{skill_id}"
                or path.startswith(f"skills/{skill_id}/")
                for path in changed_paths
            )
        ]

    def _refresh_session_companion_skill_ids(
        self,
        session: dict[str, Any],
    ) -> list[str]:
        """Merge durable session companions with current functional lineage."""

        existing = [
            skill_id
            for skill_id in self._session_companion_skill_ids(session)
            if self._is_mutable_companion_skill(skill_id)
        ]
        resolved = self._resolve_companion_skill_ids(
            str(session.get("object_type") or ""),
            str(session.get("object_id") or ""),
        )
        companions: list[str] = []
        # ``resolved`` contains only DEV-owned or not-yet-created sources;
        # installed-only dependencies were filtered above and remain immutable.
        for value in [*resolved, *existing]:
            token = _safe_token(value, fallback="")
            if token and token not in companions:
                companions.append(token)
        session["companion_skill_ids"] = companions
        session["companion_skill_id"] = companions[0] if companions else None
        return companions

    def _qualified_continuation_checkpoint(
        self,
        session: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if _brief_has_structured_edits(session.get("implementation_brief")):
            return None
        return self._budget_continuation_checkpoint(session)

    def submit_turn(
        self,
        *,
        text: str,
        object_type: str | None = None,
        object_id: str | None = None,
        webspace_id: str | None = None,
        conversation_id: str | None = None,
        workflow_transition: str | None = None,
        development_session_id: str | None = None,
        execution_budget: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        instruction = str(text or "").strip()
        if not instruction:
            raise ValueError("automation chat text is required")
        _reject_transport_corruption(instruction, field="automation chat text")
        with _LOCK:
            session = (
                self.get_session(str(object_type), str(object_id))
                if object_type and object_id
                else self.find_active_session(webspace_id=webspace_id)
            )
            if not session:
                return {"ok": False, "handled": False, "error": "automation_session_not_found"}
            incoming_conversation_id = str(conversation_id or "").strip()
            if incoming_conversation_id and not str(session.get("conversation_id") or "").strip():
                session["conversation_id"] = incoming_conversation_id
                session["updated_at"] = _now_iso()
                self._save_session(session)
            session = self.refresh_session(session)
            if session.get("status") == "completed":
                session = self._notify_completed_session(session)
            if session.get("status") in {"queued", "assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready"}:
                return {
                    "ok": True,
                    "handled": True,
                    "status": "automation_busy",
                    "message": "Локальный Codex ещё выполняет предыдущую итерацию. Дождитесь завершения и отправьте уточнение повторно.",
                    "session": session,
                    "automation": self.project_session(session),
                }
            # A newly qualified deterministic repair supersedes a preserved
            # candidate from an earlier model-budget failure. Reusing that
            # candidate would validate stale source and skip the exact edits.
            continuation_checkpoint = self._qualified_continuation_checkpoint(session)
            if continuation_checkpoint:
                session["pending_continuation_checkpoint"] = continuation_checkpoint
            else:
                session.pop("pending_continuation_checkpoint", None)
            self._rebind_development_session(
                session,
                development_session_id=str(development_session_id or ""),
            )
            if isinstance(execution_budget, Mapping):
                previous_budget = (
                    dict(session.get("execution_budget"))
                    if isinstance(session.get("execution_budget"), Mapping)
                    else {}
                )
                next_budget = {**previous_budget, **dict(execution_budget)}
                try:
                    max_tokens = int(next_budget.get("max_tokens") or 0)
                    max_wall_seconds = int(next_budget.get("max_wall_seconds") or 0)
                except (TypeError, ValueError) as exc:
                    raise ValueError("execution budget limits must be integers") from exc
                if not 1024 <= max_tokens <= 20_000_000:
                    raise ValueError("execution budget max_tokens must be between 1024 and 20000000")
                if not 60 <= max_wall_seconds <= 86_400:
                    raise ValueError("execution budget max_wall_seconds must be between 60 and 86400")
                next_budget.update(
                    {
                        "schema": "adaos.builder.execution_budget.v1",
                        "source": str(next_budget.get("source") or "builder.continuation").strip()
                        or "builder.continuation",
                        "max_tokens": max_tokens,
                        "max_wall_seconds": max_wall_seconds,
                    }
                )
                history = [
                    dict(item)
                    for item in session.get("execution_budget_history") or []
                    if isinstance(item, Mapping)
                ]
                if previous_budget:
                    history.append(
                        {
                            **previous_budget,
                            "replaced_at": _now_iso(),
                            "replaced_by_iteration": int(session.get("iteration") or 0) + 1,
                        }
                    )
                session["execution_budget_history"] = history[-20:]
                session["execution_budget"] = next_budget
            transition_token = str(workflow_transition or "").strip() or None
            starts_successor_change = False
            if transition_token != "return_to_prototype":
                workflow_before = self._workflow().describe(
                    str(session.get("object_type") or ""),
                    str(session.get("object_id") or ""),
                )
                governed = (
                    workflow_before.get("governed")
                    if isinstance(workflow_before.get("governed"), Mapping)
                    else {}
                )
                governed_state = str(governed.get("state") or "").strip()
                if governed_state in {"trial_ready", "trial_review", "publication_ready"}:
                    delivery = (
                        workflow_before.get("delivery")
                        if isinstance(workflow_before.get("delivery"), Mapping)
                        else {}
                    )
                    workflow_before = self._workflow().transition(
                        str(session.get("object_type") or ""),
                        str(session.get("object_id") or ""),
                        "candidate_stale",
                        actor="builder.automation",
                        reason="a reviewed Automation correction supersedes the current checkpoint or trial",
                        metadata={
                            "confirmed": True,
                            "candidate_id": str(delivery.get("candidate_id") or ""),
                            "rebase_plan": {
                                "stale_reason": "automation_iteration_requested",
                                "source_change_id": session.get("change_id"),
                            },
                        },
                    )["workflow"]
                    governed = (
                        workflow_before.get("governed")
                        if isinstance(workflow_before.get("governed"), Mapping)
                        else {}
                    )
                    governed_state = str(governed.get("state") or "").strip()
                if governed_state and governed_state not in {
                    "automation_ready",
                    "automation_waiting",
                    "verification",
                }:
                    raise ValueError(
                        f"Automation iteration is unavailable from governed state {governed_state}"
                    )
                active_change_set = (
                    workflow_before.get("change_set")
                    if isinstance(workflow_before.get("change_set"), Mapping)
                    else {}
                )
                active_change_set_id = str(
                    active_change_set.get("change_set_id") or ""
                ).strip()
                session_change_set_id = str(session.get("change_set_id") or "").strip()
                if active_change_set_id and active_change_set_id != session_change_set_id:
                    # Automation sessions are durable per project, while Builder
                    # Changes are deliberately short-lived review envelopes.  A
                    # terminal session may therefore be reused for an approved
                    # successor Change.  Rebind explicitly before the context
                    # packet is built and retain the previous binding as lineage;
                    # _submit still verifies the freshly built packet against the
                    # new canonical Change, so this does not weaken admission.
                    if str(active_change_set.get("gate") or "") != "automation":
                        raise ValueError(
                            "the active Builder change set must pass its Prototype "
                            "approval gate before an Automation iteration starts"
                        )
                    if str(active_change_set.get("status") or "") in {
                        "published",
                        "rejected",
                        "superseded",
                    }:
                        raise ValueError(
                            "a terminal Builder change set cannot own an Automation iteration"
                        )
                    if session_change_set_id:
                        history = [
                            str(item).strip()
                            for item in session.get("change_set_history") or []
                            if str(item).strip()
                        ]
                        if session_change_set_id not in history:
                            history.append(session_change_set_id)
                        session["change_set_history"] = history[-50:]
                    session["change_set_id"] = active_change_set_id
                    session["canonical_change_id"] = active_change_set_id
                    session.pop("context_packet_digest", None)
                    starts_successor_change = governed_state == "automation_ready"
            session["iteration"] = int(session.get("iteration") or 0) + 1
            changed_at = _now_iso()
            previous_change_id = str(session.get("change_id") or "").strip()
            if previous_change_id:
                session.setdefault("change_history", []).append(previous_change_id)
            session["change_id"] = self._change_id(
                session_id=str(session.get("session_id") or ""),
                iteration=int(session["iteration"]),
                seed=changed_at,
            )
            session.setdefault("turns", []).append(
                {"iteration": session["iteration"], "text": instruction, "created_at": changed_at}
            )
            if transition_token == "return_to_prototype":
                workflow_before = self._workflow().describe(
                    str(session.get("object_type") or ""),
                    str(session.get("object_id") or ""),
                )
                capabilities = (
                    workflow_before.get("capabilities")
                    if isinstance(workflow_before.get("capabilities"), Mapping)
                    else {}
                )
                if not bool(capabilities.get("can_return_to_prototype")):
                    raise ValueError("return to Prototype requires the current completed Automation result")
            if transition_token:
                session["pending_workflow_transition"] = transition_token
            previous_readiness = session.get("completion_readiness")
            if isinstance(previous_readiness, Mapping):
                history = [
                    dict(item)
                    for item in session.get("completion_history") or []
                    if isinstance(item, Mapping)
                ]
                history.append(
                    {
                        "task_id": str(session.get("current_task_id") or "").strip() or None,
                        "iteration": max(0, int(session.get("iteration") or 1) - 1),
                        **dict(previous_readiness),
                    }
                )
                session["completion_history"] = history[-20:]
            current_usage = session.get("codex_usage_accounting")
            if isinstance(current_usage, Mapping):
                self._retain_codex_usage_receipt(session, current_usage)
            for stale_key in (
                "completion_readiness",
                "completion_notified_task_id",
                "completion_notified_at",
                "finalizing_task_id",
                "last_result",
                "last_failure",
                "local_run",
                "progress",
                "task",
                "codex_usage_accounting",
            ):
                session.pop(stale_key, None)
            self._refresh_session_companion_skill_ids(session)
            self._capture_preview_binding(session)
            submitted = self._submit(session, iteration_instruction=instruction)
            if continuation_checkpoint:
                history = [
                    dict(item)
                    for item in session.get("continuation_history") or []
                    if isinstance(item, Mapping)
                ]
                history.append(
                    {
                        **continuation_checkpoint,
                        "resumed_by_task_id": submitted["task"]["task_id"],
                    }
                )
                session["continuation_history"] = history[-20:]
            session.pop("pending_continuation_checkpoint", None)
            session["status"] = "queued"
            session["current_task_id"] = submitted["task"]["task_id"]
            session.setdefault("task_history", []).append(session["current_task_id"])
            session["updated_at"] = _now_iso()
            self._save_session(session)
            if transition_token == "return_to_prototype":
                self._workflow().transition(
                    str(session.get("object_type") or ""),
                    str(session.get("object_id") or ""),
                    "request_return_to_prototype",
                    actor="builder.automation",
                    reason="Automation result is being adapted into a safe prototype",
                    metadata={
                        "confirmed": True,
                        "task_id": session.get("current_task_id"),
                        "change_id": session.get("change_id"),
                        "run_id": session.get("change_id"),
                        "context_packet_digest": session.get("context_packet_digest"),
                    },
                )
            else:
                self._workflow().transition(
                    str(session.get("object_type") or ""),
                    str(session.get("object_id") or ""),
                    (
                        "automation_started"
                        if starts_successor_change
                        else "automation_iteration_started"
                    ),
                    actor="builder.automation",
                    reason=(
                        "a new Automation was queued for the approved successor Change"
                        if starts_successor_change
                        else "a new Automation iteration was queued"
                    ),
                    metadata={
                        "confirmed": True,
                        "task_id": session.get("current_task_id"),
                        "change_id": session.get("change_id"),
                        "run_id": session.get("change_id"),
                        "context_packet_digest": session.get("context_packet_digest"),
                    },
                )
        session = self._notify_started_session(session)
        self._launch_worker(session["session_id"])
        return {
            "ok": True,
            "handled": True,
            "status": "automation_queued",
            "message": f"Локальный Codex принял итерацию {session['iteration']}: {instruction}",
            "session": session,
            "task": submitted["task"],
            "automation": self.project_session(session),
        }

    def _budget_continuation_checkpoint(self, session: Mapping[str, Any]) -> dict[str, Any] | None:
        task_id = str(session.get("current_task_id") or "").strip()
        if not task_id:
            return None
        try:
            task = self.factory.read_task(task_id)
        except (KeyError, RuntimeError):
            return None
        if str(task.get("status") or "").strip() != "failed":
            return None
        failures = [
            dict(item)
            for item in task.get("failure_history") or []
            if isinstance(item, Mapping)
        ]
        failure = failures[-1] if failures else {}
        failure_message = str(failure.get("message") or "")
        source_task_id = task_id
        source_failure = failure
        reason = "codex_token_budget_exceeded"
        trigger_failure_id: str | None = None
        if "Codex token budget exceeded:" not in failure_message:
            if "changed paths outside the exact repair files:" not in failure_message:
                return None
            failed_run_root = Path(self.runs_root) / _safe_token(task_id)
            assignment_path = failed_run_root / "input" / "assignment.json"
            try:
                assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return None
            request = (
                dict(assignment.get("realize_request") or {})
                if isinstance(assignment, Mapping)
                else {}
            )
            artifacts = (
                dict(request.get("artifacts") or {})
                if isinstance(request.get("artifacts"), Mapping)
                else {}
            )
            checkpoint = (
                dict(artifacts.get("continuation_checkpoint") or {})
                if isinstance(artifacts.get("continuation_checkpoint"), Mapping)
                else {}
            )
            if checkpoint.get("mode") != "validate_preserved_candidate":
                return None
            source_task_id = str(checkpoint.get("source_task_id") or "").strip()
            if not source_task_id or source_task_id == task_id:
                return None
            try:
                source_task = self.factory.read_task(source_task_id)
            except (KeyError, RuntimeError):
                return None
            source_failures = [
                dict(item)
                for item in source_task.get("failure_history") or []
                if isinstance(item, Mapping)
            ]
            source_failure = source_failures[-1] if source_failures else {}
            if (
                str(source_task.get("status") or "").strip() != "failed"
                or "Codex token budget exceeded:"
                not in str(source_failure.get("message") or "")
            ):
                return None
            trigger_failure_id = str(failure.get("failure_id") or "").strip() or None
            reason = "repair_envelope_requalified_after_path_guard"
        run_root = Path(self.runs_root) / _safe_token(source_task_id)
        if not (run_root / "workspace" / ".git").is_dir():
            return None
        if not (run_root / "input" / "assignment.json").is_file():
            return None
        return {
            "schema": "adaos.builder.automation_continuation_checkpoint.v1",
            "mode": "validate_preserved_candidate",
            "source_task_id": source_task_id,
            "failure_id": str(source_failure.get("failure_id") or "").strip() or None,
            "trigger_failure_id": trigger_failure_id,
            "reason": reason,
            "created_at": _now_iso(),
        }

    def reconcile_checkpoint(self, *, object_type: str, object_id: str) -> dict[str, Any]:
        """Explicitly reconcile failed Forge checkpoints for a validated task.

        This recovery never submits or runs Codex.  When a paired checkpoint is
        partial, it retains the original change id so already committed artifacts
        are verified and returned idempotently while only missing artifacts write.
        """

        with _LOCK:
            session = self.get_session(object_type, object_id)
            if not session:
                raise ValueError("automation_session_not_found")
            current = self.refresh_session(session)
            failure = (
                current.get("last_failure")
                if isinstance(current.get("last_failure"), Mapping)
                else {}
            )
            readiness = (
                current.get("completion_readiness")
                if isinstance(current.get("completion_readiness"), Mapping)
                else {}
            )
            checkpoints = [
                dict(item)
                for item in readiness.get("vcs_checkpoints") or []
                if isinstance(item, Mapping)
            ]
            task = current.get("task") if isinstance(current.get("task"), Mapping) else {}
            result = current.get("last_result") if isinstance(current.get("last_result"), Mapping) else {}
            if str(current.get("status") or "") != "failed" or str(failure.get("stage") or "") != "forge_checkpoint":
                raise ValueError("checkpoint reconciliation requires a Forge checkpoint failure")
            if str(task.get("status") or "") != "completed" or not result:
                raise ValueError("checkpoint reconciliation requires a validated completed Codex result")
            if not checkpoints or not any(not bool(item.get("ok")) for item in checkpoints):
                raise ValueError("checkpoint reconciliation requires at least one failed artifact")

            task_id = str(current.get("current_task_id") or "").strip()
            previous_change_id = str(current.get("change_id") or "").strip()
            partial_checkpoint = any(bool(item.get("ok")) for item in checkpoints)
            if partial_checkpoint and not previous_change_id:
                raise ValueError("partial checkpoint reconciliation requires the original change id")
            reconciliation_id = previous_change_id if partial_checkpoint else self._change_id(
                session_id=str(current.get("session_id") or ""),
                iteration=int(current.get("iteration") or 0),
                seed=f"{task_id}:checkpoint-reconcile",
            )
            history = [
                dict(item)
                for item in current.get("reconciliation_history") or []
                if isinstance(item, Mapping)
            ]
            history.append(
                {
                    "stage": "forge_checkpoint",
                    "task_id": task_id,
                    "previous_change_id": previous_change_id or None,
                    "change_id": reconciliation_id,
                    "mode": "resume_partial" if partial_checkpoint else "retry_precommit",
                    "requested_at": _now_iso(),
                }
            )
            current["reconciliation_history"] = history[-20:]
            current["change_id"] = reconciliation_id
            current["status"] = "commit_ready"
            current["finalizing_task_id"] = task_id or None
            current.pop("last_failure", None)
            current["updated_at"] = _now_iso()
            self._save_session(current)

        self._finalize_completed_session(current)
        reconciled = self.get_session(object_type, object_id) or current
        return {
            "ok": str(reconciled.get("status") or "") == "completed",
            "reconciled": True,
            "change_id": reconciliation_id,
            "session": reconciled,
            "automation": self.project_session(reconciled),
        }

    def recover_validated_result(self, *, object_type: str, object_id: str) -> dict[str, Any]:
        """Activate a preserved validated task result without rerunning Codex."""

        with _LOCK:
            session = self.get_session(object_type, object_id)
            if not session:
                raise ValueError("automation_session_not_found")
            current = self.refresh_session(session)
            task = current.get("task") if isinstance(current.get("task"), Mapping) else {}
            failure = current.get("last_failure") if isinstance(current.get("last_failure"), Mapping) else {}
            current_status = str(current.get("status") or "")
            task_id = str(current.get("current_task_id") or "").strip()
            task_status = str(task.get("status") or "")
            pending_transition = str(current.get("pending_workflow_transition") or "").strip()
            readiness = (
                current.get("completion_readiness")
                if isinstance(current.get("completion_readiness"), Mapping)
                else {}
            )
            confirmed_primary_checkpoint = next(
                (
                    item
                    for item in readiness.get("vcs_checkpoints") or []
                    if isinstance(item, Mapping)
                    and bool(item.get("ok"))
                    and str(item.get("kind") or "").strip().lower().rstrip("s")
                    == str(current.get("object_type") or "").strip().lower().rstrip("s")
                    and str(item.get("name") or "").strip()
                    == str(current.get("object_id") or "").strip()
                    and str(item.get("package_digest") or "").strip()
                    and str(item.get("source_revision") or item.get("commit") or "").strip()
                ),
                None,
            )
            workflow_checkpoint_pending = bool(
                current_status == "completed"
                and readiness.get("ok")
                and confirmed_primary_checkpoint
                and not isinstance(readiness.get("workflow_checkpoint"), Mapping)
                and str(current.get("change_id") or "").strip()
            )
            trial_checkpoint_rebind_pending = False
            if (
                current_status == "completed"
                and readiness.get("ok")
                and confirmed_primary_checkpoint
                and isinstance(readiness.get("workflow_checkpoint"), Mapping)
                and str(current.get("change_id") or "").strip()
            ):
                try:
                    workflow = self._workflow().describe(
                        str(current.get("object_type") or ""),
                        str(current.get("object_id") or ""),
                    )
                except Exception:
                    workflow = {}
                workflow_automation = (
                    workflow.get("automation")
                    if isinstance(workflow.get("automation"), Mapping)
                    else {}
                )
                workflow_delivery = (
                    workflow.get("delivery")
                    if isinstance(workflow.get("delivery"), Mapping)
                    else {}
                )
                trial_checkpoint_rebind_pending = bool(
                    str(workflow_automation.get("status") or "") == "completed"
                    and str(workflow_automation.get("head_task_id") or "") == task_id
                    and str(workflow_delivery.get("status") or "") == "idle"
                    and str(workflow_delivery.get("reconciled_at") or "").strip()
                    and str(workflow_delivery.get("checkpoint_change_id") or "").strip()
                    == str(current.get("change_id") or "").strip()
                    and str(workflow_delivery.get("package_digest") or "").strip()
                    == str(confirmed_primary_checkpoint.get("package_digest") or "").strip()
                    and str(workflow_delivery.get("source_revision") or "").strip()
                    == str(
                        confirmed_primary_checkpoint.get("source_revision")
                        or confirmed_primary_checkpoint.get("commit")
                        or ""
                    ).strip()
                )
            recovered_transition_pending = (
                current_status == "completed"
                and bool(pending_transition)
                and isinstance(current.get("last_result"), Mapping)
                and not isinstance(current.get("completion_readiness"), Mapping)
            )
            validated_activation_pending = (
                current_status == "completed"
                and task_status == "completed"
                and isinstance(current.get("last_result"), Mapping)
                and not isinstance(current.get("completion_readiness"), Mapping)
            )
            if (
                current_status != "failed"
                and not recovered_transition_pending
                and not workflow_checkpoint_pending
                and not trial_checkpoint_rebind_pending
                and not validated_activation_pending
            ):
                raise ValueError("validated result recovery requires a failed Automation task")
            failure_stage = str(failure.get("stage") or "")
            if (
                recovered_transition_pending
                or workflow_checkpoint_pending
                or trial_checkpoint_rebind_pending
                or validated_activation_pending
                or task_status == "completed"
                and failure_stage == "live_readiness"
                and isinstance(current.get("last_result"), Mapping)
            ):
                recovered_result = {
                    "ok": True,
                    "task_id": task_id,
                    "reused_validated_result": True,
                    "recovery_stage": (
                        "workflow_transition"
                        if recovered_transition_pending
                        else "workflow_checkpoint"
                        if workflow_checkpoint_pending
                        else "trial_checkpoint_rebind"
                        if trial_checkpoint_rebind_pending
                        else "validated_activation"
                        if validated_activation_pending
                        else "live_readiness"
                    ),
                }
                if (
                    not recovered_transition_pending
                    and not validated_activation_pending
                ):
                    current["reuse_confirmed_checkpoints"] = True
                if trial_checkpoint_rebind_pending:
                    # The operator-facing reconciliation deliberately cleared
                    # an unknown Trial outcome.  Only a system-validated,
                    # previously persisted Forge receipt may restore the exact
                    # checkpoint; callers cannot supply replacement identities.
                    current["rebind_confirmed_checkpoint"] = True
            else:
                if task_status != "failed":
                    raise ValueError("validated result recovery requires a failed Automation task")
                if not bool(failure.get("retryable")):
                    raise ValueError("validated result recovery requires a retryable task failure")
                worker = self.worker_factory() if self.worker_factory else LocalSkillFactoryWorker(
                    state_dir=self.state_dir,
                    repo_root=self.repo_root,
                    dev_skills_root=self.dev_skills_root,
                    dev_scenarios_root=self.dev_scenarios_root,
                    runs_root=self.runs_root,
                    progress_callback=lambda recovered_task_id, status, message: self._on_worker_progress(
                        str(current.get("session_id") or ""),
                        recovered_task_id,
                        status,
                        message,
                    ),
                )
                recovered_result = worker.recover_validated_run(task_id)
                current = self.refresh_session(current)
                if str(current.get("status") or "") != "completed" or not isinstance(current.get("last_result"), Mapping):
                    raise RuntimeError("validated result recovery did not complete the Automation task")
            current["status"] = "commit_ready"
            current["finalizing_task_id"] = task_id
            current["progress"] = {
                "task_id": task_id,
                "status": "commit_ready",
                "message": "Finalizing recovered DEV activation and Forge checkpoints",
                "updated_at": _now_iso(),
            }
            current["updated_at"] = current["progress"]["updated_at"]
            self._save_session(current)

        self._finalize_completed_session(current)
        reconciled = self.get_session(object_type, object_id) or current
        return {
            "ok": str(reconciled.get("status") or "") == "completed",
            "recovered": True,
            "worker": recovered_result,
            "session": reconciled,
            "automation": self.project_session(reconciled),
        }

    def status(
        self,
        *,
        object_type: str,
        object_id: str,
        include_session: bool = True,
    ) -> dict[str, Any]:
        if not include_session:
            cached = self._read_compact_status(object_type, object_id)
            cached_automation = (
                cached.get("automation")
                if isinstance(cached, Mapping)
                and isinstance(cached.get("automation"), Mapping)
                else {}
            )
            if str(cached_automation.get("status") or "") in _TERMINAL_STATUSES:
                return dict(cached)
        session = self.get_session(object_type, object_id)
        if not session:
            return {"ok": False, "error": "automation_session_not_found"}
        current = self.refresh_session(session)
        current = self._reconcile_required_aprobation(current)
        if current.get("status") == "completed":
            current = self._notify_completed_session(current)
        if not include_session:
            current = self._save_session(current, emit_projection=False)
        return {
            "ok": True,
            "session": current if include_session else self.compact_session(current),
            "automation": self.project_session(current),
            "detail_available": not include_session,
        }

    @staticmethod
    def compact_session(session: Mapping[str, Any]) -> dict[str, Any]:
        from adaos.services.builder.repair import _aggregate_codex_usage

        task_history = [
            str(item).strip()
            for item in session.get("task_history") or []
            if str(item).strip()
        ]
        links = session.get("links") if isinstance(session.get("links"), Mapping) else {}
        compact_links = {
            key: copy.deepcopy(links.get(key))
            for key in (
                "development_ticket_id",
                "development_ticket_ids",
                "builder_repair_id",
                "builder_package_id",
                "development_ticket_component_ref",
                "development_ticket_owner_area",
                "development_ticket_project_ref",
                "development_ticket_project_id",
            )
            if links.get(key) not in (None, "", [])
        }
        readiness = (
            session.get("completion_readiness")
            if isinstance(session.get("completion_readiness"), Mapping)
            else {}
        )
        trial = (
            readiness.get("aprobation")
            if isinstance(readiness.get("aprobation"), Mapping)
            else {}
        )
        checks = [
            {
                key: item.get(key)
                for key in ("id", "status", "required", "message")
                if item.get(key) not in (None, "")
            }
            for item in readiness.get("checks") or []
            if isinstance(item, Mapping)
        ][:20]
        receipt = (
            session.get("codex_usage_accounting")
            if isinstance(session.get("codex_usage_accounting"), Mapping)
            else {}
        )
        current_usage = {
            key: receipt.get(key)
            for key in (
                "task_id",
                "status",
                "accuracy",
                "model_tokens",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "billable_tokens",
                "root_event_id",
                "recorded_at",
            )
            if receipt.get(key) is not None
        }
        failure = (
            session.get("last_failure")
            if isinstance(session.get("last_failure"), Mapping)
            else {}
        )
        progress = session.get("progress") if isinstance(session.get("progress"), Mapping) else {}
        return {
            "schema": "adaos.builder.automation_session_summary.v1",
            "session_id": str(session.get("session_id") or "") or None,
            "object_type": str(session.get("object_type") or "") or None,
            "object_id": str(session.get("object_id") or "") or None,
            "status": str(session.get("status") or "starting"),
            "current_task_id": str(session.get("current_task_id") or "") or None,
            "task_history": {
                "count": len(task_history),
                "latest": task_history[-10:],
            },
            "iteration": int(session.get("iteration") or 0),
            "change_set_id": str(session.get("change_set_id") or "") or None,
            "change_id": str(session.get("change_id") or "") or None,
            "conversation_id": str(session.get("conversation_id") or "") or None,
            "webspace_id": str(session.get("webspace_id") or "desktop"),
            "links": compact_links,
            "progress": {
                key: progress.get(key)
                for key in ("task_id", "status", "message", "updated_at")
                if progress.get(key) not in (None, "")
            }
            or None,
            "completion": {
                "ok": bool(readiness.get("ok")),
                "task_id": readiness.get("task_id"),
                "checks": checks,
                "trial": {
                    key: trial.get(key)
                    for key in (
                        "ok",
                        "mode",
                        "candidate_id",
                        "candidate_digest",
                        "version",
                        "status",
                    )
                    if trial.get(key) not in (None, "")
                },
            }
            if readiness
            else None,
            "usage": {
                "current": current_usage,
                "aggregate": _aggregate_codex_usage(session),
                "receipt_count": len(
                    [
                        item
                        for item in session.get("codex_usage_history") or []
                        if isinstance(item, Mapping)
                    ]
                )
                + (1 if receipt else 0),
            },
            "failure": {
                key: failure.get(key)
                for key in ("failure_id", "stage", "error", "message", "retryable")
                if failure.get(key) not in (None, "")
            }
            or None,
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }

    def decide_aprobation(
        self,
        *,
        object_type: str,
        object_id: str,
        decision: str,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Accept, revise, or roll back one user-visible Builder Trial."""

        decision_token = str(decision or "").strip().lower()
        if decision_token not in {"accept", "revise", "rollback"}:
            raise ValueError("Trial decision must be accept, revise, or rollback")
        actor_token = str(actor or "").strip()
        if not actor_token:
            raise ValueError("Trial decision requires actor")
        kind, project_id = self._project_ref(object_type, object_id)
        with _LOCK:
            session = self.get_session(kind, project_id)
            if not session:
                raise ValueError("automation_session_not_found")
            current = self.refresh_session(session)
            readiness = (
                dict(current.get("completion_readiness"))
                if isinstance(current.get("completion_readiness"), Mapping)
                else {}
            )
            aprobation = (
                dict(readiness.get("aprobation"))
                if isinstance(readiness.get("aprobation"), Mapping)
                else {}
            )
            trial = (
                dict(aprobation.get("trial"))
                if isinstance(aprobation.get("trial"), Mapping)
                else {}
            )
            candidate_id = str(trial.get("candidate_id") or "").strip()
            candidate_digest = str(trial.get("candidate_digest") or "").strip()
            if not bool(aprobation.get("ok")) or not candidate_id or not candidate_digest:
                raise ValueError("reviewable Builder Trial is unavailable")

        from adaos.sdk.builder import lifecycle

        accepted = decision_token == "accept"
        rollback: dict[str, Any] | None = None
        if not accepted:
            rollback = self._rollback_aprobation_overlay(current, aprobation)
        workflow_before = self._workflow().describe(kind, project_id)
        governed_before = (
            dict(workflow_before.get("governed"))
            if isinstance(workflow_before.get("governed"), Mapping)
            else {}
        )
        governed_state = str(governed_before.get("state") or "").strip()
        delivery_before = (
            dict(workflow_before.get("delivery"))
            if isinstance(workflow_before.get("delivery"), Mapping)
            else {}
        )
        publication_before = (
            dict(workflow_before.get("publication"))
            if isinstance(workflow_before.get("publication"), Mapping)
            else {}
        )
        release_record_before = (
            dict(publication_before.get("release_record"))
            if isinstance(publication_before.get("release_record"), Mapping)
            else {}
        )
        current_delivery = (
            str(delivery_before.get("candidate_id") or "").strip() == candidate_id
        )
        current_publication = (
            str(publication_before.get("status") or "").strip() == "published"
            and str(
                release_record_before.get("candidate_id")
                or publication_before.get("candidate_id")
                or ""
            ).strip()
            == candidate_id
        )
        accepted_already = accepted and governed_state in {
            "publication_ready",
            "publication_waiting",
            "published",
        } and current_delivery
        published_already = accepted and current_publication
        if accepted_already:
            decision_result = {
                "ok": True,
                "accepted": True,
                "duplicate": True,
                "workflow": workflow_before,
            }
        else:
            decision_result = lifecycle.decide_trial(
                kind,
                project_id,
                accepted=accepted,
                actor=actor_token,
                idempotency_key=f"trial-decision:{candidate_id}:{decision_token}",
            )
        publication: dict[str, Any] | None = None
        if accepted:
            publication = (
                {
                    "ok": True,
                    "status": "published",
                    "duplicate": True,
                    "workflow": workflow_before,
                }
                if published_already
                else lifecycle.publish_candidate(
                    kind,
                    project_id,
                    actor=actor_token,
                    idempotency_key=f"trial-publication:{candidate_id}",
                )
            )
            if not bool(publication.get("ok", True)) or publication.get("error"):
                raise RuntimeError(
                    str(publication.get("error") or "Builder Trial publication failed")
                )

        workflow = self._workflow().describe(kind, project_id)
        delivery = (
            dict(workflow.get("delivery"))
            if isinstance(workflow.get("delivery"), Mapping)
            else {}
        )
        publication_state = (
            dict(workflow.get("publication"))
            if isinstance(workflow.get("publication"), Mapping)
            else {}
        )
        published_release_record = (
            dict(publication_state.get("release_record"))
            if isinstance(publication_state.get("release_record"), Mapping)
            else {}
        )
        if accepted and (
            str(publication_state.get("status") or "").strip() != "published"
            or str(
                published_release_record.get("candidate_id")
                or publication_state.get("candidate_id")
                or ""
            ).strip()
            != candidate_id
        ):
            raise RuntimeError(
                "Builder Trial publication did not durably publish the accepted candidate"
            )
        evidence_refs = [
            {
                "type": "builder_trial",
                "id": candidate_id,
                "digest": candidate_digest,
                "status": "accepted" if accepted else "rejected",
                "decision": decision_token,
            }
        ]
        if accepted:
            evidence_refs.append(
                {
                    "type": "project_release",
                    "id": str(
                        publication_state.get("release")
                        or delivery.get("release")
                        or candidate_id
                    ),
                    "version": publication_state.get("version")
                    or delivery.get("version")
                    or trial.get("version"),
                    "digest": publication_state.get("release_digest")
                    or delivery.get("release_digest"),
                    "status": "published",
                }
            )
        elif rollback:
            evidence_refs.append(
                {
                    "type": "runtime_overlay_rollback",
                    "id": candidate_id,
                    "status": "completed" if rollback.get("ok") else "failed",
                }
            )

        from adaos.services.development_tickets import DevelopmentTicketService

        ticket_service = DevelopmentTicketService(state_dir=self.state_dir)
        links = (
            dict(current.get("links"))
            if isinstance(current.get("links"), Mapping)
            else {}
        )
        ticket_ids = list(
            dict.fromkeys(
                [
                    str(links.get("development_ticket_id") or "").strip(),
                    *[
                        str(item).strip()
                        for item in links.get("development_ticket_ids") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        now = _now_iso()
        with _LOCK:
            persisted = self.get_session(kind, project_id) or current
            persisted_readiness = (
                dict(persisted.get("completion_readiness"))
                if isinstance(persisted.get("completion_readiness"), Mapping)
                else {}
            )
            persisted_aprobation = (
                dict(persisted_readiness.get("aprobation"))
                if isinstance(persisted_readiness.get("aprobation"), Mapping)
                else dict(aprobation)
            )
            persisted_trial = (
                dict(persisted_aprobation.get("trial"))
                if isinstance(persisted_aprobation.get("trial"), Mapping)
                else dict(trial)
            )
            persisted_trial.update(
                {
                    "status": "published" if accepted else "rejected",
                    "decision": decision_token,
                    "decided_by": actor_token,
                    "decided_at": now,
                }
            )
            persisted_aprobation["trial"] = persisted_trial
            if rollback is not None:
                persisted_aprobation["rollback"] = rollback
            persisted_readiness["aprobation"] = persisted_aprobation
            persisted["completion_readiness"] = persisted_readiness
            persisted["updated_at"] = now
            self._save_session(persisted)

        component_update = self._record_component_update(persisted, persisted_aprobation)
        if component_update is None:
            raise RuntimeError("Builder Trial component update notice was not persisted")
        component_update_projection = self._refresh_component_update_projection(
            persisted,
            persisted_aprobation,
        )
        persisted_aprobation["component_update"] = component_update
        if component_update_projection is not None:
            persisted_aprobation["component_update_projection"] = component_update_projection
        with _LOCK:
            persisted_readiness["aprobation"] = persisted_aprobation
            persisted["completion_readiness"] = persisted_readiness
            persisted["updated_at"] = _now_iso()
            self._save_session(persisted)

        # Tickets become verified/closed only after the accepted release and
        # its user-visible notice are both projected into the target runtime.
        tickets: list[dict[str, Any]] = []
        for ticket_id in (item for item in ticket_ids if item):
            ticket = ticket_service.get_ticket(ticket_id)
            if not ticket:
                continue
            if accepted:
                if str(ticket.get("status") or "") == "resolved":
                    ticket = ticket_service.verify_ticket(
                        ticket_id,
                        actor=actor_token,
                        evidence_refs=evidence_refs,
                        notes=str(reason or "").strip() or "User accepted the Builder Trial.",
                    )["ticket"]
                if str(ticket.get("status") or "") == "verified":
                    ticket = ticket_service.close_ticket(
                        ticket_id,
                        actor=actor_token,
                        reason="verified",
                        evidence_refs=evidence_refs,
                    )
            else:
                ticket = ticket_service.reopen_ticket(
                    ticket_id,
                    actor=actor_token,
                    reason=str(reason or "").strip()
                    or (
                        "User requested another Builder iteration."
                        if decision_token == "revise"
                        else "User rolled back the trial update."
                    ),
                    evidence_refs=evidence_refs,
                )
                ticket = ticket_service.comment_ticket(
                    ticket_id,
                    actor=actor_token,
                    body=str(reason or "").strip()
                    or (
                        "Trial rejected; prepare a revised candidate."
                        if decision_token == "revise"
                        else "Trial rolled back to the previous runtime."
                    ),
                    evidence_refs=evidence_refs,
                )
            tickets.append(ticket)

        return {
            "ok": True,
            "decision": decision_token,
            "candidate_id": candidate_id,
            "decision_result": decision_result,
            "publication": publication,
            "rollback": rollback,
            "workflow": workflow,
            "tickets": tickets,
            "evidence_refs": evidence_refs,
            "component_update": component_update,
            "component_update_projection": component_update_projection,
        }

    def projection(
        self,
        *,
        object_type: str | None = None,
        object_id: str | None = None,
        webspace_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        session = (
            self.get_session(str(object_type), str(object_id))
            if object_type and object_id
            else self.find_active_session(webspace_id=webspace_id)
        )
        if not session:
            return {
                "ok": False,
                "error": "automation_session_not_found",
                "automation": self.empty_projection(webspace_id=webspace_id),
            }
        incoming_conversation_id = str(conversation_id or "").strip()
        if incoming_conversation_id and not str(session.get("conversation_id") or "").strip():
            session["conversation_id"] = incoming_conversation_id
            session["updated_at"] = _now_iso()
            self._save_session(session)
        current = self.refresh_session(session)
        current = self._reconcile_required_aprobation(current)
        if current.get("status") == "completed":
            current = self._notify_completed_session(current)
        return {"ok": True, "session": current, "automation": self.project_session(current)}

    def _reconcile_required_aprobation(
        self,
        session: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = dict(session)
        readiness = (
            current.get("completion_readiness")
            if isinstance(current.get("completion_readiness"), Mapping)
            else {}
        )
        aprobation = (
            readiness.get("aprobation")
            if isinstance(readiness.get("aprobation"), Mapping)
            else {}
        )
        if (
            str(current.get("status") or "").strip() == "completed"
            and bool(readiness.get("ok"))
            and self._session_requires_aprobation_overlay(current)
            and not self._aprobation_overlay_ready(aprobation)
        ):
            _log.info(
                "reconciling required Dev Ticket aprobation session=%s task=%s",
                current.get("session_id"),
                current.get("current_task_id"),
            )
            reconciled = self._reconcile_completed_workflow(current)
            if reconciled is None:
                _log.warning(
                    "Dev Ticket aprobation reconciliation declined by workflow guard "
                    "session=%s task=%s change=%s",
                    current.get("session_id"),
                    current.get("current_task_id"),
                    current.get("change_id"),
                )
            return reconciled or current
        if (
            str(current.get("status") or "").strip() == "completed"
            and bool(readiness.get("ok"))
            and self._session_requires_aprobation_overlay(current)
            and self._aprobation_overlay_ready(aprobation)
            and not isinstance(aprobation.get("component_update_projection"), Mapping)
        ):
            _log.info(
                "reconciling missing component update projection session=%s task=%s",
                current.get("session_id"),
                current.get("current_task_id"),
            )
            notice = self._record_component_update(current, aprobation)
            if notice is None:
                raise RuntimeError("Builder Trial component update notice was not persisted")
            projection = self._refresh_component_update_projection(current, aprobation)
            reconciled_aprobation = dict(aprobation)
            reconciled_aprobation["component_update"] = notice
            if projection is not None:
                reconciled_aprobation["component_update_projection"] = projection
            reconciled_readiness = dict(readiness)
            reconciled_readiness["aprobation"] = reconciled_aprobation
            current["completion_readiness"] = reconciled_readiness
            current["updated_at"] = _now_iso()
            self._save_session(current)
        return current

    @staticmethod
    def _aprobation_overlay_ready(value: Mapping[str, Any] | None) -> bool:
        receipt = dict(value) if isinstance(value, Mapping) else {}
        if not bool(receipt.get("ok")):
            return False
        trial = (
            dict(receipt.get("trial"))
            if isinstance(receipt.get("trial"), Mapping)
            else {}
        )
        if (
            str(trial.get("status") or "").strip()
            not in {"trial", "accepted", "published"}
            or not str(trial.get("candidate_id") or "").strip()
            or not str(trial.get("candidate_digest") or "").strip()
        ):
            return False
        for raw_skill in receipt.get("skills") or []:
            if not isinstance(raw_skill, Mapping):
                continue
            skill = dict(raw_skill)
            projection = (
                dict(skill.get("webspace_projection"))
                if isinstance(skill.get("webspace_projection"), Mapping)
                else {}
            )
            if projection and not bool(projection.get("ok")):
                return False
            materialization = (
                projection.get("materialization")
                if isinstance(projection.get("materialization"), Mapping)
                else None
            )
            cache = (
                skill.get("materialization_cache")
                if isinstance(skill.get("materialization_cache"), Mapping)
                else {}
            )
            if materialization is None and isinstance(cache.get("materialization"), Mapping):
                materialization = cache["materialization"]
            if isinstance(materialization, Mapping) and materialization.get("ready") is not True:
                return False
        return True

    @staticmethod
    def empty_projection(*, webspace_id: str | None = None) -> dict[str, Any]:
        return {
            "schema": AUTOMATION_PROJECTION_SCHEMA,
            "stage": "automation",
            "status": "idle",
            "phase": "idle",
            "busy": False,
            "terminal": False,
            "can_submit": False,
            "webspace_id": str(webspace_id or "desktop"),
            "project": None,
            "iteration": 0,
            "task_id": None,
            "steps": BuilderAutomationService._step_projection("idle"),
            "created_at": None,
            "updated_at": None,
        }

    @staticmethod
    def project_session(session: Mapping[str, Any]) -> dict[str, Any]:
        status = str(session.get("status") or "starting").strip() or "starting"
        task = session.get("task") if isinstance(session.get("task"), Mapping) else {}
        result = session.get("last_result") if isinstance(session.get("last_result"), Mapping) else {}
        forge = task.get("forge") if isinstance(task.get("forge"), Mapping) else {}
        failure = session.get("last_failure") if isinstance(session.get("last_failure"), Mapping) else {}
        progress = session.get("progress") if isinstance(session.get("progress"), Mapping) else {}
        local_run = session.get("local_run") if isinstance(session.get("local_run"), Mapping) else {}
        budget_usage = BuilderAutomationService._budget_usage_projection(
            status=status,
            task=task,
            local_run=local_run,
        )
        readiness = (
            session.get("completion_readiness")
            if isinstance(session.get("completion_readiness"), Mapping)
            else {}
        )
        aprobation = (
            readiness.get("aprobation")
            if isinstance(readiness.get("aprobation"), Mapping)
            else {}
        )
        error = str(failure.get("error") or failure.get("message") or task.get("error") or "").strip() or None
        return {
            "schema": AUTOMATION_PROJECTION_SCHEMA,
            "stage": "automation",
            "session_id": str(session.get("session_id") or "") or None,
            "status": status,
            "phase": BuilderAutomationService._phase_for_status(status),
            "busy": status in _ACTIVE_STATUSES,
            "terminal": status in _TERMINAL_STATUSES,
            "can_submit": status in {"completed", "failed", "cancelled", "expired"},
            "webspace_id": str(session.get("webspace_id") or "desktop"),
            "project": {
                "type": str(session.get("object_type") or ""),
                "id": str(session.get("object_id") or ""),
                "companion_skill_id": str(session.get("companion_skill_id") or "") or None,
                "companion_skill_ids": BuilderAutomationService._session_companion_skill_ids(session),
            },
            "source_prototype_version": str(session.get("source_prototype_version") or "").strip() or None,
            "prototype_handoff_digest": str(
                dict(session.get("prototype_handoff") or {}).get("digest") or ""
            ) or None,
            "iteration": int(session.get("iteration") or 0),
            "task_id": str(session.get("current_task_id") or task.get("task_id") or "") or None,
            "change_set_id": str(session.get("change_set_id") or "").strip() or None,
            "change_id": str(session.get("change_id") or "").strip() or None,
            "result_branch": str(result.get("branch") or forge.get("branch") or "").strip() or None,
            "steps": BuilderAutomationService._step_projection(status),
            "progress": dict(progress) if progress else None,
            "summary": str(result.get("summary") or result.get("message") or "").strip() or None,
            "budget_usage": budget_usage,
            "delivery": {
                "aprobation_required": BuilderAutomationService._session_requires_aprobation_overlay(
                    session
                ),
                "aprobation_ready": bool(aprobation.get("ok")),
                "mode": str(aprobation.get("mode") or "").strip() or None,
            },
            "error": error,
            "failure_id": str(failure.get("failure_id") or "").strip() or None,
            "failure_stage": str(failure.get("stage") or "").strip() or None,
            "retryable": bool(failure.get("retryable")) if failure else None,
            "links": dict(session.get("links")) if isinstance(session.get("links"), Mapping) else {},
            "diagnostic_hint": (
                "Исправьте причину и отправьте уточнение в Автоматизации, чтобы запустить новую итерацию."
                if error
                else None
            ),
            "evidence": {
                "events_path": str(local_run.get("events_path") or "").strip() or None,
                "stderr_path": str(local_run.get("stderr_path") or "").strip() or None,
                "result_path": str(local_run.get("result_path") or "").strip() or None,
            }
            if local_run
            else None,
            # ``created_at`` is the durable Automation-start boundary.  It is
            # intentionally projected alongside ``updated_at`` so independent
            # schedulers and evaluators can prove a preregistered execution
            # order without reading Builder's private session file.
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }

    @staticmethod
    def _budget_usage_projection(
        *,
        status: str,
        task: Mapping[str, Any],
        local_run: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        realize_request = (
            task.get("realize_request")
            if isinstance(task.get("realize_request"), Mapping)
            else {}
        )
        artifacts = (
            realize_request.get("artifacts")
            if isinstance(realize_request.get("artifacts"), Mapping)
            else {}
        )
        declared = (
            dict(artifacts.get("execution_budget"))
            if isinstance(artifacts.get("execution_budget"), Mapping)
            else None
        )
        usage = BuilderAutomationService._codex_run_usage(local_run)
        started_raw = str(task.get("assigned_at") or task.get("created_at") or "").strip()
        finished_raw = str(task.get("updated_at") or "").strip()
        wall_seconds = 0.0
        try:
            started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            if status in _TERMINAL_STATUSES and finished_raw:
                finished = datetime.fromisoformat(finished_raw.replace("Z", "+00:00"))
            else:
                finished = datetime.now(timezone.utc)
            wall_seconds = max(0.0, (finished - started).total_seconds())
        except (TypeError, ValueError):
            pass
        if declared is None and not usage and not started_raw:
            return None
        declared_model_tokens = 0
        if declared:
            try:
                declared_model_tokens = int(
                    declared.get("max_model_tokens") or declared.get("max_tokens") or 0
                )
            except (TypeError, ValueError):
                declared_model_tokens = 0
        observed_model_tokens = int(usage.get("model_tokens") or 0)
        budget_metric = str((declared or {}).get("token_budget_metric") or "model_tokens").strip()
        observed_budget_tokens = observed_model_tokens
        if budget_metric == "fresh_plus_output":
            observed_budget_tokens = max(
                0,
                int(usage.get("input_tokens") or 0) - int(usage.get("cached_input_tokens") or 0),
            ) + int(usage.get("output_tokens") or 0)
        budget_status = "unknown"
        overrun_tokens = 0
        if declared_model_tokens > 0 and observed_budget_tokens > 0:
            if observed_budget_tokens > declared_model_tokens:
                budget_status = "exceeded"
                overrun_tokens = observed_budget_tokens - declared_model_tokens
            else:
                budget_status = "within_budget"
        return {
            "declared": declared,
            "observed": {
                "model_tokens": observed_model_tokens,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
                "budget_metric": budget_metric,
                "budget_tokens": observed_budget_tokens,
                "attempts": int(usage.get("attempts") or 0),
                "wall_seconds": wall_seconds,
                "terminal": status in _TERMINAL_STATUSES,
            },
            "status": budget_status,
            "overrun_tokens": overrun_tokens,
        }

    @staticmethod
    def _codex_journal_usage(path_value: str) -> dict[str, int]:
        path = Path(str(path_value or "").strip())
        if not path.is_file():
            return {}
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                return {}
            values: dict[str, int] = {}
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = event.get("usage") if isinstance(event.get("usage"), Mapping) else None
                if usage is None and isinstance(event.get("turn"), Mapping):
                    turn = event["turn"]
                    usage = turn.get("usage") if isinstance(turn.get("usage"), Mapping) else None
                if usage is None:
                    continue
                aliases = {
                    "input_tokens": ("input_tokens",),
                    "cached_input_tokens": ("cached_input_tokens",),
                    "output_tokens": ("output_tokens",),
                    "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens"),
                }
                for key, candidates in aliases.items():
                    try:
                        observed = next((usage.get(candidate) for candidate in candidates if usage.get(candidate) is not None), 0)
                        values[key] = max(values.get(key, 0), int(observed or 0))
                    except (TypeError, ValueError):
                        continue
            if values:
                values["model_tokens"] = int(values.get("input_tokens") or 0) + int(
                    values.get("output_tokens") or 0
                )
            return values
        except OSError:
            return {}

    @staticmethod
    def _codex_run_usage(local_run: Mapping[str, Any]) -> dict[str, Any]:
        run_root = Path(str(local_run.get("path") or "").strip())
        runtime_root = run_root / "runtime"
        paths = sorted(runtime_root.glob("codex-events*.jsonl")) if runtime_root.is_dir() else []
        if not paths:
            event_path = str(local_run.get("events_path") or "").strip()
            paths = [Path(event_path)] if event_path else []
        total: dict[str, Any] = {}
        for path in paths:
            usage = BuilderAutomationService._codex_journal_usage(str(path))
            accuracy = "provider_reported"
            if not usage:
                budget_path = path.with_name(
                    path.name.replace("codex-events", "codex-token-budget").replace(
                        ".jsonl",
                        ".json",
                    )
                )
                if budget_path.is_file():
                    try:
                        receipt = json.loads(budget_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        receipt = {}
                    estimated = receipt.get("usage") if isinstance(receipt, Mapping) else {}
                    if isinstance(estimated, Mapping) and int(estimated.get("model_tokens") or 0) > 0:
                        usage = {
                            key: int(estimated.get(key) or 0)
                            for key in (
                                "input_tokens",
                                "cached_input_tokens",
                                "output_tokens",
                                "reasoning_tokens",
                                "model_tokens",
                            )
                        }
                        accuracy = str(estimated.get("accuracy") or "estimated")
            if usage:
                total["attempts"] = int(total.get("attempts") or 0) + 1
                if accuracy != "provider_reported":
                    total["accuracy"] = accuracy
            for key, value in usage.items():
                if key == "model_tokens":
                    continue
                total[key] = int(total.get(key) or 0) + int(value or 0)
        if total:
            total["model_tokens"] = int(total.get("input_tokens") or 0) + int(
                total.get("output_tokens") or 0
            )
        return total

    @staticmethod
    def _retain_codex_usage_receipt(
        session: dict[str, Any],
        receipt: Mapping[str, Any],
    ) -> None:
        task_id = str(receipt.get("task_id") or "").strip()
        if not task_id:
            return
        history = [
            dict(item)
            for item in session.get("codex_usage_history") or []
            if isinstance(item, Mapping)
            and str(item.get("task_id") or "").strip() != task_id
        ]
        history.append(dict(receipt))
        session["codex_usage_history"] = history[-50:]

    @staticmethod
    def _zero_model_execution(
        session: Mapping[str, Any],
        task_id: str,
    ) -> dict[str, str] | None:
        preserved_candidate = any(
            str(item.get("mode") or "").strip() == "validate_preserved_candidate"
            and str(item.get("resumed_by_task_id") or "").strip() == task_id
            for item in session.get("continuation_history") or []
            if isinstance(item, Mapping)
        )
        if preserved_candidate:
            return {
                "strategy": "preserved_candidate",
                "reason": "Validated a preserved candidate without starting a model turn.",
            }
        result = (
            dict(session.get("last_result"))
            if isinstance(session.get("last_result"), Mapping)
            else {}
        )
        provenance = (
            dict(result.get("provenance"))
            if isinstance(result.get("provenance"), Mapping)
            else {}
        )
        strategy = str(
            result.get("execution_strategy")
            or provenance.get("execution_strategy")
            or ""
        ).strip()
        if strategy == "structured_edits":
            return {
                "strategy": strategy,
                "reason": "Applied qualified structured edits and validation without starting a model turn.",
            }
        return None

    @staticmethod
    def _is_zero_model_continuation(session: Mapping[str, Any], task_id: str) -> bool:
        execution = BuilderAutomationService._zero_model_execution(session, task_id)
        return bool(execution and execution.get("strategy") == "preserved_candidate")

    @staticmethod
    def _source_slice_coverage(session: Mapping[str, Any]) -> dict[str, Any] | None:
        local_run = (
            dict(session.get("local_run"))
            if isinstance(session.get("local_run"), Mapping)
            else {}
        )
        run_path = str(local_run.get("path") or "").strip()
        if not run_path:
            return None
        packet_path = Path(run_path) / "input" / "packet.json"
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        target = (
            dict(packet.get("repair_target_context"))
            if isinstance(packet, Mapping)
            and isinstance(packet.get("repair_target_context"), Mapping)
            else {}
        )
        coverage = target.get("coverage")
        return dict(coverage) if isinstance(coverage, Mapping) else None

    def _record_context_attribution(
        self,
        session: Mapping[str, Any],
        *,
        task_id: str,
        task_status: str,
        usage: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        current = dict(session)
        control = (
            dict(current.get("context_control"))
            if isinstance(current.get("context_control"), Mapping)
            else {}
        )
        run_ref = str(control.get("run_ref") or "").strip()
        plan_ref = str(control.get("plan_ref") or "").strip()
        if not run_ref or not plan_ref:
            return current
        existing = (
            dict(current.get("context_attribution_receipt"))
            if isinstance(current.get("context_attribution_receipt"), Mapping)
            else {}
        )
        if existing.get("status") == "recorded" and existing.get("task_id") == task_id:
            return current
        usage_value = dict(usage or {})
        validation = (
            dict(current.get("last_validation"))
            if isinstance(current.get("last_validation"), Mapping)
            else {}
        )
        project_ref = str(control.get("project_ref") or "").strip()
        if not project_ref:
            project_ref = (
                f"project:{current.get('object_id')}"
                if current.get("object_id")
                else ""
            )
        try:
            receipt = self._contexts().record_receipt(
                {
                    "run_ref": run_ref,
                    "plan_ref": plan_ref,
                    "subject_refs": [
                        run_ref,
                        *([project_ref] if project_ref else []),
                    ],
                    "purpose": "builder.automation",
                    "audience": "builder",
                    "selected_refs": control.get("selected_refs") or [],
                    "omitted": control.get("omitted") or [],
                    "denied": control.get("denied") or [],
                    "unavailable": control.get("unavailable") or [],
                    "layer_usage": control.get("layer_usage") or [],
                    "usage": {
                        "provider_input_tokens": int(usage_value.get("input_tokens") or 0),
                        "cached_input_tokens": int(usage_value.get("cached_input_tokens") or 0),
                        "output_tokens": int(usage_value.get("output_tokens") or 0),
                        "reasoning_tokens": int(usage_value.get("reasoning_tokens") or 0),
                        "model_tokens": int(usage_value.get("model_tokens") or 0),
                    },
                    "tool_boundary_count": 1,
                    "source_slice_coverage": self._source_slice_coverage(current),
                    "execution_route": "skill_factory.local_codex",
                    "validation": {
                        "task_status": task_status,
                        "ok": task_status == "completed",
                        "summary": validation.get("summary"),
                    },
                    "evidence_refs": [
                        {"type": "builder_task", "ref": task_id},
                        *(
                            [
                                {
                                    "type": "context_model_projection",
                                    "ref": control.get("model_projection_ref"),
                                    "digest": control.get("model_projection_digest"),
                                }
                            ]
                            if control.get("model_projection_ref")
                            else []
                        ),
                        *(
                            [{"type": "root_usage_event", "ref": usage_value.get("root_event_id")}]
                            if usage_value.get("root_event_id")
                            else []
                        ),
                    ],
                    "latency_ms": int(control.get("context_latency_ms") or 0),
                    "created_at": str(current.get("updated_at") or _now_iso()),
                }
            )
            current["context_attribution_receipt"] = {
                "status": "recorded",
                "task_id": task_id,
                "run_ref": run_ref,
                "plan_ref": plan_ref,
                "receipt_id": receipt.get("receipt_id"),
                "receipt_ref": receipt.get("receipt_ref"),
                "usage": receipt.get("usage"),
            }
        except Exception as exc:
            current["context_attribution_receipt"] = {
                "status": "record_failed",
                "task_id": task_id,
                "run_ref": run_ref,
                "plan_ref": plan_ref,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            }
        return current

    def _report_terminal_codex_usage(
        self,
        session: Mapping[str, Any],
        *,
        task_status: str,
    ) -> dict[str, Any]:
        """Report one terminal Codex usage event and retain an auditable receipt."""

        current = dict(session)
        task_id = str(current.get("current_task_id") or "").strip()
        if not task_id or task_status not in _TERMINAL_STATUSES:
            return current
        accounting = (
            current.get("codex_usage_accounting")
            if isinstance(current.get("codex_usage_accounting"), Mapping)
            else {}
        )
        if (
            (
                accounting.get("status") == "reported"
                or (
                    accounting.get("status") == "not_applicable"
                    and self.codex_usage_reporter is None
                )
            )
            and str(accounting.get("task_id") or "") == task_id
        ):
            return current
        local_run = (
            current.get("local_run")
            if isinstance(current.get("local_run"), Mapping)
            else {}
        )
        usage = self._codex_run_usage(local_run)
        if not usage:
            zero_model_execution = self._zero_model_execution(current, task_id)
            if (
                not zero_model_execution
                and accounting.get("status") == "unavailable"
                and str(accounting.get("task_id") or "") == task_id
            ):
                return current
            if zero_model_execution:
                receipt = {
                    "schema": "adaos.builder.codex_usage_receipt.v1",
                    "task_id": task_id,
                    "status": "not_applicable",
                    "accuracy": "exact",
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "model_tokens": 0,
                    "total_tokens": 0,
                    "billable_tokens": 0,
                    "idempotency_key": (
                        f"builder:{current.get('session_id') or 'session'}:"
                        f"{task_id}:codex-usage:v1"
                    ),
                    "execution_strategy": zero_model_execution["strategy"],
                    "reason": zero_model_execution["reason"],
                    "checked_at": _now_iso(),
                }
                if self.codex_usage_reporter is not None:
                    event = {
                        "idempotency_key": receipt["idempotency_key"],
                        "run_id": task_id,
                        "job_id": task_id,
                        "status": task_status,
                        "source": "builder_automation",
                        "accuracy": "reported",
                        "metering_disposition": "zero_model",
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_tokens": 0,
                        "total_tokens": 0,
                        "billable_tokens": 0,
                        "occurred_at": str(current.get("updated_at") or _now_iso()),
                        "change_id": str(current.get("change_id") or "").strip() or None,
                        "note": (
                            f"builder_status={task_status}; "
                            f"deterministic_strategy={zero_model_execution['strategy']}"
                        ),
                    }
                    object_type = str(current.get("object_type") or "").strip()
                    object_id = str(current.get("object_id") or "").strip()
                    if object_type == "scenario" and object_id:
                        event["scenario_id"] = object_id
                    elif object_id:
                        event["project_id"] = object_id
                    try:
                        reported = dict(self.codex_usage_reporter(event))
                        root_event = (
                            reported.get("event")
                            if isinstance(reported.get("event"), Mapping)
                            else {}
                        )
                        receipt.update(
                            {
                                "status": "reported",
                                "root_event_id": root_event.get("event_id"),
                                "duplicate": bool(reported.get("duplicate")),
                                "reported_at": _now_iso(),
                            }
                        )
                    except Exception as exc:
                        receipt.update(
                            {
                                "status": "report_failed",
                                "error": f"{type(exc).__name__}: {exc}"[:2000],
                            }
                        )
            else:
                receipt = {
                    "schema": "adaos.builder.codex_usage_receipt.v1",
                    "task_id": task_id,
                    "status": "unavailable",
                    "accuracy": "unavailable",
                    "total_tokens": None,
                    "reason": "No provider usage was found in the terminal Codex journal.",
                    "checked_at": _now_iso(),
                }
            current["codex_usage_accounting"] = receipt
            self._retain_codex_usage_receipt(current, receipt)
            if zero_model_execution or accounting:
                current["updated_at"] = _now_iso()
            return self._record_context_attribution(
                current,
                task_id=task_id,
                task_status=task_status,
                usage=receipt if zero_model_execution else None,
            )
        if self.codex_usage_reporter is None:
            usage_accuracy = str(usage.get("accuracy") or "provider_reported")
            receipt = {
                "schema": "adaos.builder.codex_usage_receipt.v1",
                "task_id": task_id,
                "status": "reporter_unavailable",
                "accuracy": usage_accuracy,
                **usage,
                "total_tokens": int(usage.get("model_tokens") or 0),
                "checked_at": _now_iso(),
            }
            current["codex_usage_accounting"] = receipt
            self._retain_codex_usage_receipt(current, receipt)
            current["updated_at"] = _now_iso()
            return self._record_context_attribution(
                current,
                task_id=task_id,
                task_status=task_status,
                usage=receipt,
            )
        idempotency_key = f"builder:{current.get('session_id') or 'session'}:{task_id}:codex-usage:v1"
        object_type = str(current.get("object_type") or "").strip()
        object_id = str(current.get("object_id") or "").strip()
        usage_accuracy = str(usage.get("accuracy") or "provider_reported")
        event = {
            "idempotency_key": idempotency_key,
            "run_id": task_id,
            "job_id": task_id,
            "status": task_status,
            "source": "builder_automation",
            "accuracy": "reported" if usage_accuracy == "provider_reported" else "estimated",
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "total_tokens": int(usage.get("model_tokens") or 0),
            "billable_tokens": int(usage.get("model_tokens") or 0),
            "occurred_at": str(current.get("updated_at") or _now_iso()),
            "change_id": str(current.get("change_id") or "").strip() or None,
            "note": f"builder_status={task_status}; attempts={int(usage.get('attempts') or 0)}",
        }
        if object_type == "scenario" and object_id:
            event["scenario_id"] = object_id
        elif object_id:
            event["project_id"] = object_id
        try:
            reported = dict(self.codex_usage_reporter(event))
            root_event = reported.get("event") if isinstance(reported.get("event"), Mapping) else {}
            receipt = {
                "schema": "adaos.builder.codex_usage_receipt.v1",
                "task_id": task_id,
                "status": "reported",
                "accuracy": usage_accuracy,
                **usage,
                "total_tokens": int(usage.get("model_tokens") or 0),
                "idempotency_key": idempotency_key,
                "root_event_id": root_event.get("event_id"),
                "duplicate": bool(reported.get("duplicate")),
                "reported_at": _now_iso(),
            }
        except Exception as exc:
            receipt = {
                "schema": "adaos.builder.codex_usage_receipt.v1",
                "task_id": task_id,
                "status": "report_failed",
                "accuracy": usage_accuracy,
                **usage,
                "total_tokens": int(usage.get("model_tokens") or 0),
                "idempotency_key": idempotency_key,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
                "checked_at": _now_iso(),
            }
        current["codex_usage_accounting"] = receipt
        self._retain_codex_usage_receipt(current, receipt)
        current["updated_at"] = _now_iso()
        return self._record_context_attribution(
            current,
            task_id=task_id,
            task_status=task_status,
            usage=receipt,
        )

    @staticmethod
    def _phase_for_status(status: str) -> str:
        return {
            "starting": "queued",
            "queued": "queued",
            "assigned": "workspace",
            "workspace_preparing": "workspace",
            "in_progress": "implementation",
            "tests_running": "verification",
            "commit_ready": "result",
            "completed": "completed",
            "failed": "error",
            "cancelled": "cancelled",
            "expired": "expired",
        }.get(status, "unknown")

    @staticmethod
    def _step_projection(status: str) -> list[dict[str, Any]]:
        current_rank = _STATUS_RANK.get(status, -1)
        failed = status in {"failed", "cancelled", "expired"}
        steps: list[dict[str, Any]] = []
        for step_id, label_key, rank in _AUTOMATION_STEPS:
            if failed and step_id == "result":
                state = "error"
            elif status == "completed" or current_rank > rank:
                state = "completed"
            elif current_rank == rank:
                state = "current"
            else:
                state = "pending"
            steps.append({"id": step_id, "label_i18n": {"key": label_key}, "state": state})
        return steps

    def get_session(self, object_type: str, object_id: str) -> dict[str, Any] | None:
        try:
            kind, project_id = self._project_ref(object_type, object_id)
        except ValueError:
            return None
        path = self._session_path(kind, project_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return dict(raw) if isinstance(raw, Mapping) else None

    def release_candidate_runtime(
        self,
        *,
        object_type: str,
        object_id: str,
        development_session_id: str,
    ) -> dict[str, Any]:
        """Release a terminal candidate's DEV runtime while retaining evidence.

        The exact Development Session binding prevents a caller from using a
        stale result to release a newer candidate.  Only skill candidates are
        admitted because scenarios do not own Python runtimes.
        """

        kind, project_id = self._project_ref(object_type, object_id)
        if kind != "skill":
            raise ValueError("candidate runtime release requires object_type=skill")
        expected_session_id = str(development_session_id or "").strip()
        if not expected_session_id or not _DEVELOPMENT_SESSION_ID_RE.fullmatch(expected_session_id):
            raise ValueError("a valid development_session_id is required")

        with _LOCK:
            session = self.get_session(kind, project_id)
            if not session:
                raise ValueError("automation_session_not_found")
            actual_session_id = str(session.get("development_session_id") or "").strip()
            if actual_session_id != expected_session_id:
                raise ValueError("development_session_id does not match the candidate Automation session")
            status = str(session.get("status") or "").strip().lower()
            if status not in _TERMINAL_STATUSES:
                raise ValueError("candidate runtime may be released only after terminal Automation")

            previous = session.get("runtime_release")
            if isinstance(previous, Mapping):
                if str(previous.get("development_session_id") or "") != expected_session_id:
                    raise ValueError("stored runtime release belongs to a different Development Session")
                if str(previous.get("status") or "released") == "released":
                    return {"ok": True, "idempotent": True, "runtime_release": dict(previous)}

            diagnostics = (
                dict(previous.get("diagnostics") or {})
                if isinstance(previous, Mapping)
                and isinstance(previous.get("diagnostics"), Mapping)
                else self._archive_candidate_runtime_diagnostics(
                    project_id=project_id,
                    development_session_id=expected_session_id,
                )
            )
            cleanup_attempts = (
                int(previous.get("cleanup_attempts") or 0) + 1
                if isinstance(previous, Mapping)
                else 1
            )
            cleanup: dict[str, Any]
            cleanup_error: OSError | None = None
            try:
                cleanup = _cleanup_dev_skill_runtime(project_id)
            except OSError as exc:
                if not self._retryable_runtime_cleanup_error(exc):
                    raise
                cleanup_error = exc
                cleanup = {
                    "runtime_existed": True,
                    "runtime_removed": False,
                    "purged_data": False,
                }
            released = bool(cleanup.get("runtime_removed"))
            attempted_at = _now_iso()
            receipt = {
                "schema": "adaos.builder.runtime_release.v1",
                "object_type": kind,
                "object_id": project_id,
                "development_session_id": expected_session_id,
                "automation_status": status,
                "status": "released" if released else "cleanup_pending",
                "cleanup_attempts": cleanup_attempts,
                "attempted_at": attempted_at,
                "runtime_removed": bool(cleanup.get("runtime_removed")),
                "runtime_existed": bool(cleanup.get("runtime_existed")),
                "purged_data": bool(cleanup.get("purged_data")),
                "retryable": not released,
            }
            if diagnostics is not None:
                receipt["diagnostics"] = diagnostics
            if released:
                receipt["released_at"] = attempted_at
            else:
                receipt["pending_reason"] = (
                    "native_module_mapped_by_runtime_process"
                    if cleanup_error is not None
                    else "runtime_not_removed"
                )
                if cleanup_error is not None:
                    receipt["cleanup_error"] = {
                        "type": type(cleanup_error).__name__,
                        "errno": cleanup_error.errno,
                        "winerror": getattr(cleanup_error, "winerror", None),
                    }
            session["runtime_release"] = receipt
            session["updated_at"] = attempted_at
            self._save_session(session)
            return {
                "ok": True,
                "idempotent": False,
                "cleanup_pending": not released,
                "runtime_release": receipt,
            }

    @staticmethod
    def _retryable_runtime_cleanup_error(exc: OSError) -> bool:
        """Recognize OS errors caused by a still-mapped native module."""

        if isinstance(exc, PermissionError):
            return True
        return getattr(exc, "winerror", None) in {5, 32, 145}

    def _archive_candidate_runtime_diagnostics(
        self,
        *,
        project_id: str,
        development_session_id: str,
    ) -> dict[str, Any] | None:
        """Move runtime diagnostics into Builder-owned durable evidence.

        Candidate ``data`` belongs to the candidate skill and is captured by
        its scientific consumer.  Build and packaged-test diagnostics belong
        to the Automation session instead.  Preserve the latter before the
        ephemeral candidate runtime (including its vendored dependencies and
        data) is purged.
        """

        source = (self.dev_skills_root / ".runtime" / project_id / "diagnostics").resolve()
        runtime_root = (self.dev_skills_root / ".runtime" / project_id).resolve()
        if source.parent != runtime_root or not source.is_dir():
            return None

        evidence_root = (
            self.state_dir
            / "builder"
            / "automation-evidence"
            / "runtime-diagnostics"
            / _safe_token(project_id)
        ).resolve()
        destination = (evidence_root / _safe_token(development_session_id)).resolve()
        if destination.parent != evidence_root:
            raise ValueError("candidate diagnostic destination escaped its evidence root")

        def manifest_for(root: Path) -> tuple[list[dict[str, Any]], int]:
            entries: list[dict[str, Any]] = []
            total_bytes = 0
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise RuntimeError("candidate runtime diagnostics must not contain symlinks")
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise RuntimeError("candidate runtime diagnostic escaped its evidence root")
                size = int(path.stat().st_size)
                total_bytes += size
                if len(entries) >= _RUNTIME_DIAGNOSTIC_MAX_FILES:
                    raise RuntimeError("candidate runtime diagnostics exceed the file-count limit")
                if total_bytes > _RUNTIME_DIAGNOSTIC_MAX_BYTES:
                    raise RuntimeError("candidate runtime diagnostics exceed the byte limit")
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "bytes": size,
                        "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            return entries, total_bytes

        if destination.is_dir():
            entries, total_bytes = manifest_for(destination)
        else:
            entries, total_bytes = manifest_for(source)
            evidence_root.mkdir(parents=True, exist_ok=True)
            staging = evidence_root / f".staging-{_safe_token(development_session_id)}"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            try:
                for entry in entries:
                    relative = Path(str(entry["path"]))
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source / relative, target)
                staging.replace(destination)
                entries, total_bytes = manifest_for(destination)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        manifest = {
            "schema": "adaos.builder.runtime_diagnostics.v1",
            "object_type": "skill",
            "object_id": project_id,
            "development_session_id": development_session_id,
            "files": entries,
            "file_count": len(entries),
            "bytes": total_bytes,
        }
        return {
            **manifest,
            "root": str(destination),
            "digest": _canonical_digest(manifest),
        }

    def find_active_session(self, *, webspace_id: str | None = None) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, Mapping):
                continue
            session = dict(raw)
            if webspace_id and str(session.get("webspace_id") or "") != str(webspace_id):
                continue
            if session.get("status") not in {"cancelled"}:
                candidates.append(session)
        if not candidates:
            return None
        return max(candidates, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))

    def refresh_session(self, session: Mapping[str, Any]) -> dict[str, Any]:
        current = dict(session)
        task_id = str(current.get("current_task_id") or "").strip()
        if not task_id:
            return current
        try:
            task = self.factory.read_task(task_id)
        except KeyError:
            return current
        recovered = self._recover_orphaned_task(current, task)
        if recovered is not None:
            return recovered
        # A failed one-shot reconciliation updates the authoritative factory
        # task before returning.  Refresh the snapshot so this read exposes the
        # failure instead of preserving the stale in-progress projection.
        try:
            task = self.factory.read_task(task_id)
        except KeyError:
            pass
        task_status = task.get("status")
        materialization_pending = bool(
            task_status == "completed"
            and self.materialize_on_completion
            and not isinstance(current.get("completion_readiness"), Mapping)
        )
        finalization_in_progress = bool(
            task_status == "completed"
            and self.materialize_on_completion
            and str(current.get("finalizing_task_id") or "").strip() == task_id
        )
        current["status"] = (
            "commit_ready"
            if materialization_pending or finalization_in_progress
            else task_status
        )
        if materialization_pending:
            # The Skill Factory result is only an intermediate checkpoint.
            # Mark finalization ownership before publishing any projection so
            # readers can never mistake a validated candidate for a fully
            # activated/checkpointed terminal Automation result.
            current["finalizing_task_id"] = task_id
        current["task"] = task
        current["updated_at"] = task.get("updated_at") or _now_iso()
        realize_request = (
            task.get("realize_request")
            if isinstance(task.get("realize_request"), Mapping)
            else {}
        )
        request_artifacts = (
            realize_request.get("artifacts")
            if isinstance(realize_request.get("artifacts"), Mapping)
            else {}
        )
        recovered_transition = str(request_artifacts.get("workflow_transition") or "").strip()
        if (
            task_status == "completed"
            and recovered_transition
            and not str(current.get("pending_workflow_transition") or "").strip()
            and not isinstance(current.get("completion_readiness"), Mapping)
        ):
            # A retryable worker failure clears the pending transition so the
            # old Automation remains authoritative.  If that same preserved
            # task is later recovered, restore its original transition before
            # finalization; otherwise the safe Prototype would be applied to
            # DEV but never entered in the workflow state machine.
            current["pending_workflow_transition"] = recovered_transition
        run_dir = Path(self.runs_root) / _safe_token(task_id)
        current["local_run"] = {
            "path": str(run_dir),
            "events_path": str(run_dir / "output" / "codex-live.jsonl"),
            "stderr_path": str(run_dir / "output" / "codex-live.stderr.log"),
            "result_path": str(run_dir / "output" / "result.json"),
        }
        if task.get("result"):
            current["last_result"] = task.get("result")
            current.pop("last_failure", None)
        if task_status != "completed" and task.get("failure_history"):
            current["last_failure"] = task.get("failure_history")[-1]
            current.pop("last_result", None)
        # Deterministic executions declare their zero-model strategy in the
        # result. Retain it before metering so the first terminal projection
        # records an exact zero-token receipt instead of a transient
        # unavailable receipt that needs a later status poll to repair.
        current = self._report_terminal_codex_usage(
            current,
            task_status=str(task_status or ""),
        )
        readiness = current.get("completion_readiness")
        finalizing = str(current.get("finalizing_task_id") or "").strip() == task_id
        if (
            task_status == "completed"
            and isinstance(readiness, Mapping)
            and str(readiness.get("task_id") or "").strip() == task_id
            and not finalizing
        ):
            checkpoints = [
                item
                for item in readiness.get("vcs_checkpoints") or []
                if isinstance(item, Mapping)
            ]
            failed_checkpoints = [item for item in checkpoints if not bool(item.get("ok"))]
            if failed_checkpoints:
                failed_refs = ", ".join(
                    f"{item.get('kind') or 'artifact'}:{item.get('name') or '?'}"
                    for item in failed_checkpoints
                )
                error = f"Forge checkpoint failed for {failed_refs}"
                readiness = {**dict(readiness), "ok": False, "error": error}
                current["completion_readiness"] = readiness
                current["status"] = "failed"
                current["last_failure"] = {
                    "stage": "forge_checkpoint",
                    "message": error,
                    "updated_at": readiness.get("completed_at") or current.get("updated_at"),
                }
            elif not bool(readiness.get("ok", False)):
                error = str(
                    readiness.get("error")
                    or "Automation result is validated but live readiness is not confirmed"
                )
                readiness = {**dict(readiness), "ok": False, "error": error}
                current["completion_readiness"] = readiness
                current["status"] = "failed"
                current["last_failure"] = {
                    "stage": "live_readiness",
                    "message": error,
                    "updated_at": readiness.get("completed_at") or current.get("updated_at"),
                }
        terminal_readiness = bool(
            task_status == "completed"
            and isinstance(readiness, Mapping)
            and bool(readiness.get("ok"))
            and str(readiness.get("task_id") or "").strip() == task_id
        )
        aprobation = (
            readiness.get("aprobation")
            if isinstance(readiness, Mapping)
            and isinstance(readiness.get("aprobation"), Mapping)
            else {}
        )
        if (
            terminal_readiness
            and self._session_requires_aprobation_overlay(current)
            and not bool(aprobation.get("ok"))
        ):
            reconciled = self._reconcile_completed_workflow(current)
            if reconciled is not None:
                return reconciled
        task_progress = task.get("progress") if isinstance(task.get("progress"), list) else []
        if terminal_readiness:
            existing_progress = (
                current.get("progress")
                if isinstance(current.get("progress"), Mapping)
                else {}
            )
            current["status"] = "completed"
            current["progress"] = {
                "task_id": task_id,
                "status": "completed",
                "message": (
                    existing_progress.get("message")
                    if str(existing_progress.get("status") or "") == "completed"
                    else "Automation result activated and checkpointed"
                ),
                "updated_at": readiness.get("completed_at") or current.get("updated_at"),
            }
            current["updated_at"] = max(
                str(current.get("updated_at") or ""),
                str(current["progress"]["updated_at"] or ""),
            )
            current.pop("last_failure", None)
        elif task_progress and isinstance(task_progress[-1], Mapping) and not finalizing:
            current["progress"] = dict(task_progress[-1])
        if current.get("status") == "failed" and isinstance(current.get("last_failure"), Mapping):
            failure = current["last_failure"]
            current["progress"] = {
                "task_id": task_id,
                "status": "failed",
                "stage": failure.get("stage") or "failed",
                "message": failure.get("message") or failure.get("error") or "Automation failed",
                "updated_at": failure.get("reported_at") or current.get("updated_at"),
            }
        self._save_session(current)
        needs_detached_finalization = bool(
            self.materialize_on_completion
            and task_status == "completed"
            and finalizing
            and isinstance(current.get("last_result"), Mapping)
            and not isinstance(current.get("completion_readiness"), Mapping)
        )
        if needs_detached_finalization:
            # A node-level orphan recovery may finish the validated Skill
            # Factory task after the Automation worker process has died.  The
            # durable finalizing_task_id is the ownership marker that makes
            # this replay bounded; finalization itself reconciles an existing
            # workflow checkpoint before performing any writes.
            if self._detached_worker_is_active(str(current.get("session_id") or "")):
                # The worker remains responsible after the nested Skill
                # Factory run becomes terminal.  In particular, dependency
                # installation and DEV activation may still be running; a
                # status reader must not execute those writes concurrently.
                return current
            self._finalize_completed_session(current)
            return self.get_session(
                str(current.get("object_type") or ""),
                str(current.get("object_id") or ""),
            ) or current
        return current

    def _recover_orphaned_task(
        self,
        session: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resume one terminal local Codex run after its API supervisor died."""

        task_id = str(task.get("task_id") or "").strip()
        task_status = str(task.get("status") or "").strip()
        if (
            not task_id
            or task_status not in _ACTIVE_STATUSES
            or isinstance(session.get("completion_readiness"), Mapping)
        ):
            return None
        run_dir = Path(self.runs_root) / _safe_token(task_id)
        if not LocalSkillFactoryWorker._codex_journal_completed(
            run_dir / "output" / "codex-live.jsonl"
        ):
            return None
        if not _WORKER_LOCK.acquire(blocking=False):
            # The original in-process worker still owns finalization.  It will
            # publish the terminal projection when it leaves the lock.
            return None
        try:
            latest = self.get_session(
                str(session.get("object_type") or ""),
                str(session.get("object_id") or ""),
            )
            if (
                not latest
                or str(latest.get("current_task_id") or "").strip() != task_id
                or isinstance(latest.get("completion_readiness"), Mapping)
            ):
                return None
            worker = self.worker_factory() if self.worker_factory else LocalSkillFactoryWorker(
                state_dir=self.state_dir,
                repo_root=self.repo_root,
                dev_skills_root=self.dev_skills_root,
                dev_scenarios_root=self.dev_scenarios_root,
                runs_root=self.runs_root,
            )
            try:
                worker.recover_orphaned_codex_run(task_id)
            except ValueError:
                # A terminal journal can be observed a few milliseconds before
                # the original worker persists its own local state.  In that
                # normal race the explicit local-state guards decline recovery.
                return None
            except Exception:
                _log.exception("one-shot orphaned Automation recovery failed task=%s", task_id)
                return None

            try:
                completed_task = self.factory.read_task(task_id)
            except KeyError:
                completed_task = None
            if not isinstance(completed_task, Mapping) or completed_task.get("status") != "completed":
                return None
            current = dict(latest)
            current["task"] = dict(completed_task)
            if completed_task.get("result"):
                current["last_result"] = completed_task.get("result")
            current["status"] = "commit_ready" if self.materialize_on_completion else "completed"
            current["finalizing_task_id"] = task_id if self.materialize_on_completion else None
            current["progress"] = {
                "task_id": task_id,
                "status": current["status"],
                "message": (
                    "Recovered completed Codex turn; finalizing DEV activation and checkpoints"
                    if self.materialize_on_completion
                    else "Recovered completed Codex turn"
                ),
                "updated_at": _now_iso(),
            }
            current["updated_at"] = current["progress"]["updated_at"]
            self._save_session(current)
            if self.event_sink:
                self.event_sink(self.project_session(current))
            if self.materialize_on_completion:
                self._finalize_completed_session(current)
                return self.get_session(
                    str(current.get("object_type") or ""),
                    str(current.get("object_id") or ""),
                ) or current
            return current
        finally:
            _WORKER_LOCK.release()

    def _submit(self, session: Mapping[str, Any], *, iteration_instruction: str) -> dict[str, Any]:
        kind = str(session["object_type"])
        project_id = str(session["object_id"])
        companions = self._resolve_companion_skill_ids(kind, project_id)
        existing = [
            skill_id
            for skill_id in self._session_companion_skill_ids(session)
            if self._is_mutable_companion_skill(skill_id)
        ]
        for skill_id in existing:
            if skill_id not in companions:
                companions.append(skill_id)
        companion = companions[0] if companions else ""
        sparse_paths = [f"{kind}s/{project_id}/" if kind == "scenario" else f"skills/{project_id}/"]
        source_artifacts: list[tuple[str, str, Path]] = [
            (
                kind,
                project_id,
                (self.dev_scenarios_root if kind == "scenario" else self.dev_skills_root) / project_id,
            )
        ]
        if kind == "scenario":
            for skill_id in companions:
                sparse_paths.append(f"skills/{skill_id}/")
                source_artifacts.append(("skill", skill_id, self.dev_skills_root / skill_id))
        sparse_paths.append(f"docs/requirements/{project_id}/")
        attachments: list[tuple[str, Path, str]] = []
        if kind == "scenario":
            automation_snapshot = (
                self.state_dir
                / "builder"
                / "workflow_snapshots"
                / "scenario"
                / project_id
                / "automation"
            )
            if automation_snapshot.is_dir():
                attachments.append(
                    (
                        "previous_automation",
                        automation_snapshot,
                        f"scenarios/{project_id}/.builder_previous_automation",
                    )
                )
            workspace_scenarios_root = (
                Path(self.workspace_service.scenarios_root)
                if self.workspace_service is not None
                and self.workspace_service.scenarios_root is not None
                else self.repo_root / ".adaos" / "workspace" / "scenarios"
            )
            current_publication = workspace_scenarios_root / project_id
            if current_publication.is_dir():
                attachments.append(
                    (
                        "current_publication",
                        current_publication,
                        f"scenarios/{project_id}/.builder_current_publication",
                    )
                )
        development_context: dict[str, Any] | None = None
        development_session_id = str(session.get("development_session_id") or "").strip()
        if development_session_id:
            development_context, development_attachments = self._development_context(
                development_session_id,
                target_ref=f"{kind}:{project_id}",
            )
            attachments.extend(development_attachments)
        source_snapshot = capture_source_snapshot(
            state_dir=self.state_dir,
            artifacts=source_artifacts,
            attachments=attachments,
            created_at=_now_iso(),
        )
        workflow_service = self._workflow()
        workflow_state = workflow_service.describe(kind, project_id)
        change_set = (
            workflow_state.get("change_set")
            if isinstance(workflow_state.get("change_set"), Mapping)
            else {}
        )
        semantic_refs = [
            str(ref).strip()
            for issue in change_set.get("issues") or []
            if isinstance(issue, Mapping)
            for ref in issue.get("semantic_refs") or []
            if str(ref).strip()
        ]
        required_context_facets = ["data_policy", "execution_authority"]
        if semantic_refs:
            required_context_facets = [
                "target_structure",
                "abi",
                "constraints",
                *required_context_facets,
            ]
        context_packet = workflow_service.build_context_packet(
            kind,
            project_id,
            allowed_paths=[
                *sparse_paths,
                "prompt_state.json",
            ],
            instruction_refs=[
                str(session.get("brief_path") or "").strip(),
                str(session.get("topic_id") or "").strip(),
                *(
                    [str(item.get("ref") or "") for item in development_context["instruction_inputs"]]
                    if development_context
                    else []
                ),
            ],
            run_purpose=str(session.get("run_purpose") or "iteration"),
            required_facets=required_context_facets,
            enforce_context_coverage=True,
            persist=True,
        )
        packet_change = (
            context_packet.get("change")
            if isinstance(context_packet.get("change"), Mapping)
            else {}
        )
        canonical_change_id = str(packet_change.get("change_id") or "").strip()
        session_change_set_id = str(session.get("change_set_id") or "").strip()
        if not canonical_change_id or canonical_change_id != session_change_set_id:
            raise ValueError("Automation context packet does not match the active Builder Change")
        if isinstance(session, dict):
            session["canonical_change_id"] = canonical_change_id
            session["context_packet_digest"] = context_packet.get("digest")
        context_control = self._compile_iteration_context(
            session=session,
            kind=kind,
            project_id=project_id,
            context_packet=context_packet,
            source_snapshot=source_snapshot,
            implementation_brief=iteration_instruction
            or str(session.get("implementation_brief") or ""),
        )
        if isinstance(session, dict):
            session["context_control"] = {
                key: copy.deepcopy(context_control.get(key))
                for key in (
                    "run_ref",
                    "project_ref",
                    "capsule_refs",
                    "context_packet_ref",
                    "context_packet_digest",
                    "context_packet_artifact_digest",
                    "resolution_ref",
                    "plan_id",
                    "plan_ref",
                    "compiled_context_ref",
                    "compiled_context_digest",
                    "model_projection_ref",
                    "model_projection_digest",
                    "context_delta_mode",
                    "layer_usage",
                    "selected_refs",
                    "omitted",
                    "denied",
                    "unavailable",
                    "estimated_tokens",
                    "token_budget",
                    "context_latency_ms",
                )
            }
        acceptance_checks = [
            str(criterion).strip()
            for issue in change_set.get("issues") or []
            if isinstance(issue, Mapping) and issue.get("status") != "deferred"
            for criterion in issue.get("acceptance_criteria") or []
            if str(criterion).strip()
        ]
        is_dev_ticket_repair = bool(str(dict(session.get("links") or {}).get("development_ticket_id") or "").strip())
        repair_brief = self._session_repair_brief(session) if is_dev_ticket_repair else {}
        repair_hints = (
            dict(repair_brief.get("repair_hints"))
            if isinstance(repair_brief.get("repair_hints"), Mapping)
            else {}
        )
        repair_hints = _canonical_repair_hints(
            repair_hints,
            kind=kind,
            object_id=project_id,
        )
        realization_constraints = {
            "no_external_api": True,
            "no_secrets": True,
            "must_add_tests": True,
            "must_update_manifest": not is_dev_ticket_repair,
            "local_process_debug": True,
        }
        if is_dev_ticket_repair:
            realization_constraints.update(
                {
                    "mode": "dev_ticket_repair",
                    "minimal_diff": True,
                    "preserve_declarative_manifests": True,
                }
            )
            profile = str(repair_hints.get("profile") or "").strip()
            target_files = [
                str(item).replace("\\", "/").strip("/")
                for item in repair_hints.get("target_files") or []
                if str(item).strip()
            ]
            if profile:
                realization_constraints["repair_profile"] = profile
            if target_files:
                realization_constraints["exact_changed_paths"] = target_files
            if repair_hints.get("max_changed_files"):
                realization_constraints["max_changed_files"] = int(
                    repair_hints["max_changed_files"]
                )
        request_id = (
            f"realize.{_safe_token(kind)}.{_safe_token(project_id)}."
            f"{_safe_token(session.get('change_id'), fallback='change')}."
            f"{max(0, int(session.get('iteration') or 0))}"
        )
        request = {
            "request_id": request_id,
            "target": {"type": kind, "id": project_id},
            "source": {
                "type": "prompt_ide_execute" if not iteration_instruction else "builder_automation_chat",
                "text": iteration_instruction or str(session.get("implementation_brief") or ""),
            },
            "source_conversation_id": session.get("conversation_id"),
            "artifacts": {
                "implementation_brief": session.get("implementation_brief"),
                "implementation_brief_path": session.get("brief_path"),
                "companion_skill_id": companion,
                "companion_skill_ids": companions,
                "iteration_instruction": iteration_instruction,
                "workflow_transition": session.get("pending_workflow_transition"),
                "standard_prompt_version": STANDARD_PROMPT_VERSION,
                "change_set": dict(change_set) if change_set else None,
                "context_packet": context_packet,
                "context_packet_ref": context_control["context_packet_ref"],
                "context_packet_digest": context_control["context_packet_digest"],
                "context_projection": context_control["context_projection"],
                "context_plan_ref": context_control["plan_ref"],
                "compiled_context_ref": context_control["compiled_context_ref"],
                "development_context": development_context,
                "prototype_handoff": copy.deepcopy(session.get("prototype_handoff")),
                "continuation_checkpoint": copy.deepcopy(
                    session.get("pending_continuation_checkpoint")
                ),
                "repair_hints": copy.deepcopy(repair_hints) or None,
            },
            "repo": {
                "sparse_paths": sparse_paths,
                "base_branch": "dev/local",
                "base_revision": source_snapshot["digest"],
                "source_snapshot": source_snapshot,
            },
            "constraints": {
                **realization_constraints,
            },
            "mcp": (
                {"enabled": False, "requested_scope": []}
                if is_dev_ticket_repair and repair_hints.get("requires_root_mcp") is False
                else _sanitized_mcp_profile(session.get("mcp"))
                or {
                    "requested_scope": [
                        "capability_snapshot",
                        "requirement_spec",
                        "mock_runtime",
                        "staging_validation",
                    ]
                }
            ),
            "acceptance": {
                "checks": [
                    *acceptance_checks,
                    "skill manifest is valid",
                    "Python handlers compile",
                    "scenario and webui JSON are valid when present",
                    "changed files stay inside the project envelope",
                ]
            },
            "links": {
                "automation_session_id": session.get("session_id"),
                "webspace_id": session.get("webspace_id"),
                "iteration": session.get("iteration"),
                "change_set_id": session.get("change_set_id"),
                "canonical_change_id": canonical_change_id,
                "context_packet_digest": context_packet.get("digest"),
                "context_run_ref": context_control["run_ref"],
                "context_plan_id": context_control["plan_id"],
                "context_plan_ref": context_control["plan_ref"],
                "compiled_context_ref": context_control["compiled_context_ref"],
                "prototype_handoff_digest": str(
                    dict(session.get("prototype_handoff") or {}).get("digest") or ""
                ) or None,
                "development_session_id": development_session_id or None,
                "development_context_digest": str(
                    (development_context or {}).get("digest") or ""
                ) or None,
                **(
                    dict(session.get("links"))
                    if isinstance(session.get("links"), Mapping)
                    else {}
                ),
            },
            "snapshot_context": {
                "schema": "adaos.skill_factory.task_context.v1",
                "generated_at": _now_iso(),
                "freshness": {
                    "generated_at": _now_iso(),
                    "source_generation": source_snapshot.get("digest"),
                },
                "redaction": {
                    "level": "workspace_governed",
                    "secrets_absent": True,
                    "raw_user_data_absent": True,
                },
                "privacy": {
                    "secrets_absent": True,
                    "raw_user_data_absent": True,
                },
                "provenance": [
                    {"kind": "context_plan", "ref": context_control["plan_ref"]},
                    {"kind": "compiled_context", "ref": context_control["compiled_context_ref"]},
                    {"kind": "source_snapshot", "ref": source_snapshot.get("digest")},
                ],
                "mock_data": {"deterministic": True, "fixture_ids": [], "seed": request_id},
                "byte_budget": 32_768,
            },
        }
        execution_budget = (
            development_context.get("execution_budget")
            if isinstance(development_context, Mapping)
            and isinstance(development_context.get("execution_budget"), Mapping)
            else None
        )
        if execution_budget is None and isinstance(session.get("execution_budget"), Mapping):
            execution_budget = session.get("execution_budget")
        if execution_budget:
            if execution_budget.get("max_wall_seconds"):
                request["timeout_seconds"] = int(execution_budget["max_wall_seconds"])
            request["artifacts"]["execution_budget"] = copy.deepcopy(execution_budget)
        agent_profile = (
            development_context.get("agent_profile")
            if isinstance(development_context, Mapping)
            and isinstance(development_context.get("agent_profile"), Mapping)
            else None
        )
        if agent_profile is None and isinstance(session.get("agent_profile"), Mapping):
            agent_profile = session.get("agent_profile")
        if agent_profile:
            request["artifacts"]["agent_profile"] = copy.deepcopy(agent_profile)
        return self.factory.submit_realize_request(request)

    def _launch_worker(self, session_id: str) -> None:
        if self.background:
            self._launch_worker_process(session_id)
        else:
            self._run_worker(session_id)

    def _launch_worker_process(self, session_id: str) -> dict[str, Any]:
        """Launch a durable worker outside the potentially ephemeral skill call.

        Skill tools are process-isolated and may return before a daemon thread
        has claimed its task.  A detached local worker keeps the existing
        persisted task/session identities and reports progress through the
        normal Skill Factory state watched by :meth:`refresh_session`.
        """

        token = _safe_token(session_id, fallback="automation")
        worker_root = self.state_dir / "builder" / "automation_workers" / token
        worker_root.mkdir(parents=True, exist_ok=True)
        stdout_path = worker_root / "stdout.log"
        stderr_path = worker_root / "stderr.log"
        launch_path = worker_root / "launch.json"
        ready_path = worker_root / "ready.json"
        ready_path.unlink(missing_ok=True)
        repo_python = self.repo_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable = repo_python if repo_python.is_file() else Path(sys.executable)
        command = [
            str(executable),
            "-m",
            "adaos.services.builder.automation_worker",
            "--session-id",
            str(session_id),
        ]
        command, priority_creationflags, resource_policy = _automation_worker_resource_policy(command)
        env = os.environ.copy()
        env["ADAOS_BASE_DIR"] = str(self.state_dir.parent.resolve())
        env["ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC"] = "1"
        env["ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC"] = "1"
        source_root = str((self.repo_root / "src").resolve())
        inherited_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            source_root + os.pathsep + inherited_pythonpath
            if inherited_pythonpath
            else source_root
        )
        popen_kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                | priority_creationflags
            )
            resource_policy["job_breakaway"] = bool(
                getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                **popen_kwargs,
            )
        try:
            process_create_time: float | None = float(psutil.Process(process.pid).create_time())
        except (psutil.Error, OSError):
            process_create_time = None
        launched: dict[str, Any] = {
            "schema": "adaos.builder.automation_worker_launch.v1",
            "session_id": str(session_id),
            "pid": int(process.pid),
            "create_time": process_create_time,
            "status": "starting",
            "repo_root": str(self.repo_root.resolve()),
            "executable": str(executable.resolve()),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "resource_policy": resource_policy,
            "launched_at": _now_iso(),
        }
        _write_json(launch_path, launched)
        try:
            ready_timeout = float(
                os.getenv("ADAOS_BUILDER_WORKER_READY_TIMEOUT_SECONDS", "60")
            )
        except (TypeError, ValueError):
            ready_timeout = 60.0
        ready_timeout = min(180.0, max(5.0, ready_timeout))
        deadline = time.monotonic() + ready_timeout
        ready: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                value = json.loads(ready_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                value = None
            if (
                isinstance(value, Mapping)
                and str(value.get("session_id") or "") == str(session_id)
                and str(value.get("status") or "") == "ready"
            ):
                ready = dict(value)
                break
            poll = getattr(process, "poll", None)
            return_code = poll() if callable(poll) else None
            if return_code is not None:
                launched.update(
                    {
                        "status": "failed",
                        "error": (
                            "automation worker exited before readiness handshake "
                            f"with code {return_code}"
                        ),
                        "failed_at": _now_iso(),
                    }
                )
                _write_json(launch_path, launched)
                raise RuntimeError(str(launched["error"]))
            time.sleep(0.05)
        if ready is None:
            launched.update(
                {
                    "status": "failed",
                    "error": (
                        "automation worker did not publish a readiness handshake "
                        f"within {ready_timeout:.1f} seconds"
                    ),
                    "failed_at": _now_iso(),
                }
            )
            _write_json(launch_path, launched)
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                except OSError:
                    pass
            raise RuntimeError(str(launched["error"]))
        launched.update(
            {
                "status": "ready",
                "worker_pid": ready.get("pid"),
                "ready_at": ready.get("ready_at") or _now_iso(),
            }
        )
        _write_json(launch_path, launched)
        return launched

    def _detached_worker_is_active(self, session_id: str) -> bool:
        """Return whether the durable worker still owns orchestration.

        A Skill Factory task becomes terminal before Builder finishes DEV
        activation, dependency installation, and Forge checkpoints.  API
        readers must therefore fence the whole detached Automation process,
        not only the nested Codex run.  PID creation time prevents a reused
        process id from suppressing legitimate recovery.
        """

        token = _safe_token(session_id, fallback="automation")
        launch_path = self.state_dir / "builder" / "automation_workers" / token / "launch.json"
        try:
            launched = json.loads(launch_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return False
        if str(launched.get("session_id") or "") != str(session_id or ""):
            return False
        return LocalSkillFactoryWorker._process_owner_is_active(launched)

    def _run_worker(self, session_id: str) -> None:
        with _WORKER_LOCK:
            with _LOCK:
                submitted_session = self._find_session_by_id(session_id)
                expected_task_id = str(
                    (submitted_session or {}).get("current_task_id") or ""
                ).strip()
            worker = self.worker_factory() if self.worker_factory else LocalSkillFactoryWorker(
                state_dir=self.state_dir,
                repo_root=self.repo_root,
                dev_skills_root=self.dev_skills_root,
                dev_scenarios_root=self.dev_scenarios_root,
                runs_root=self.runs_root,
                progress_callback=lambda task_id, status, message: self._on_worker_progress(
                    session_id,
                    task_id,
                    status,
                    message,
                ),
            )
            if hasattr(worker, "progress_callback") and getattr(worker, "progress_callback", None) is None:
                worker.progress_callback = lambda task_id, status, message: self._on_worker_progress(
                    session_id,
                    task_id,
                    status,
                    message,
                )
            worker_result = worker.run_once(task_id=expected_task_id or None)
            should_finalize = False
            finalizing_projection: dict[str, Any] | None = None
            with _LOCK:
                session = self._find_session_by_id(session_id)
                if session:
                    session = self.refresh_session(session)
                    if session.get("status") == "failed":
                        pending_transition = str(session.get("pending_workflow_transition") or "").strip()
                        session.pop("pending_workflow_transition", None)
                        self._save_session(session)
                        try:
                            self._workflow().transition(
                                str(session.get("object_type") or ""),
                                str(session.get("object_id") or ""),
                                (
                                    "return_to_prototype_failed"
                                    if pending_transition == "return_to_prototype"
                                    else "automation_failed"
                                ),
                                actor="builder.automation",
                                metadata={
                                    "task_id": session.get("current_task_id"),
                                    "change_id": session.get("change_id"),
                                    "error": (
                                        session.get("last_failure", {}).get("message")
                                        if isinstance(session.get("last_failure"), Mapping)
                                        else "Automation worker failed"
                                    ),
                                },
                            )
                        except Exception:
                            pass
                    should_finalize = bool(
                        isinstance(worker_result, Mapping)
                        and worker_result.get("ok")
                        and session.get("status") in {"completed", "commit_ready"}
                        and self.materialize_on_completion
                    )
                    if should_finalize:
                        session["status"] = "commit_ready"
                        session["finalizing_task_id"] = str(session.get("current_task_id") or "").strip() or None
                        session["progress"] = {
                            "task_id": session.get("current_task_id"),
                            "status": "commit_ready",
                            "message": "Finalizing DEV activation and Forge checkpoints",
                            "updated_at": _now_iso(),
                        }
                        session["updated_at"] = session["progress"]["updated_at"]
                        self._save_session(session)
                        finalizing_projection = self.project_session(session)
            if should_finalize and session:
                if self.event_sink and finalizing_projection:
                    self.event_sink(finalizing_projection)
                self._finalize_completed_session(session)

    def _reconcile_completed_workflow(
        self,
        session: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Recover a session whose canonical workflow already reached checkpoint.

        DEV activation and the Forge write precede the canonical
        ``checkpoint_recorded`` transition.  Therefore an exact task/change
        match at that state is sufficient durable evidence that replaying
        finalization would be both unnecessary and unsafe.
        """

        if str(session.get("pending_workflow_transition") or "").strip():
            return None
        object_type = str(session.get("object_type") or "").strip()
        object_id = str(session.get("object_id") or "").strip()
        task_id = str(session.get("current_task_id") or "").strip()
        change_id = str(session.get("change_id") or "").strip()
        if not object_type or not object_id or not task_id or not change_id:
            return None
        try:
            workflow = self._workflow().describe(object_type, object_id)
        except Exception:
            return None
        automation = (
            workflow.get("automation")
            if isinstance(workflow.get("automation"), Mapping)
            else {}
        )
        delivery = (
            workflow.get("delivery")
            if isinstance(workflow.get("delivery"), Mapping)
            else {}
        )
        if not (
            str(automation.get("status") or "").strip() == "completed"
            and str(automation.get("head_task_id") or "").strip() == task_id
            and str(delivery.get("status") or "").strip() == "checkpoint"
            and str(delivery.get("checkpoint_change_id") or "").strip() == change_id
            and str(delivery.get("package_digest") or "").strip()
            and str(delivery.get("source_revision") or "").strip()
        ):
            return None

        current = dict(session)
        readiness = (
            dict(current.get("completion_readiness"))
            if isinstance(current.get("completion_readiness"), Mapping)
            else {}
        )
        checkpoints = [
            dict(item)
            for item in readiness.get("vcs_checkpoints") or []
            if isinstance(item, Mapping) and bool(item.get("ok"))
        ]
        if not checkpoints:
            checkpoints = [
                {
                    "ok": True,
                    "kind": object_type,
                    "name": object_id,
                    "commit": str(delivery.get("source_revision")),
                    "source_revision": str(delivery.get("source_revision")),
                    "package_digest": str(delivery.get("package_digest")),
                    "version": str(delivery.get("version") or "").strip() or None,
                    "reconciled_from": "canonical_builder_workflow",
                }
            ]
        existing_aprobation = (
            readiness.get("aprobation")
            if isinstance(readiness.get("aprobation"), Mapping)
            else {}
        )
        if (
            self._session_requires_aprobation_overlay(current)
            and not self._aprobation_overlay_ready(existing_aprobation)
        ):
            companion_skill_ids = self._session_changed_companion_skill_ids(current)
            scenario_id = object_id if object_type == "scenario" else None
            if companion_skill_ids or scenario_id:
                readiness["aprobation"] = self._prepare_and_activate_aprobation_overlay(
                    current,
                    skill_ids=companion_skill_ids,
                    scenario_id=scenario_id,
                    webspace_id=str(current.get("webspace_id") or "desktop").strip() or "desktop",
                )
        if self._session_requires_aprobation_overlay(current):
            readiness["aprobation"] = self._ensure_governed_aprobation_trial(
                current,
                readiness.get("aprobation")
                if isinstance(readiness.get("aprobation"), Mapping)
                else existing_aprobation,
            )
        completed_at = str(
            automation.get("completed_at")
            or delivery.get("checkpoint_at")
            or _now_iso()
        )
        reconciled_at = _now_iso()
        readiness.update(
            {
                "ok": True,
                "task_id": task_id,
                "iteration": int(current.get("iteration") or 0),
                "vcs_checkpoints": checkpoints,
                "completed_at": completed_at,
                "workflow_reconciliation": {
                    "status": "already_checkpointed",
                    "generation": workflow.get("generation"),
                    "checkpoint_change_id": change_id,
                    "package_digest": str(delivery.get("package_digest")),
                    "source_revision": str(delivery.get("source_revision")),
                },
            }
        )
        readiness.pop("error", None)
        current["completion_readiness"] = readiness
        current["status"] = "completed"
        current["progress"] = {
            "task_id": task_id,
            "status": "completed",
            "message": "Reconciled terminal session from canonical Builder workflow",
            "updated_at": reconciled_at,
        }
        current["updated_at"] = reconciled_at
        current.pop("finalizing_task_id", None)
        current.pop("reuse_confirmed_checkpoints", None)
        current.pop("last_failure", None)
        self._save_session(current)
        return current

    def _record_finalization_progress(
        self,
        current: dict[str, Any],
        readiness: dict[str, Any],
        stage: str,
        message: str,
        *,
        heartbeat: int = 0,
    ) -> None:
        now = _now_iso()
        started_at = str(
            current.get("finalization_started_at")
            or readiness.get("started_at")
            or now
        )
        current["finalization_started_at"] = started_at
        readiness.update(
            {
                "stage": stage,
                "stage_message": message,
                "started_at": started_at,
                "heartbeat": max(0, int(heartbeat)),
                "updated_at": now,
            }
        )
        current["completion_readiness"] = copy.deepcopy(readiness)
        current["status"] = "commit_ready"
        current["progress"] = {
            "task_id": current.get("current_task_id"),
            "status": "commit_ready",
            "stage": stage,
            "message": message,
            "heartbeat": max(0, int(heartbeat)),
            "started_at": started_at,
            "updated_at": now,
        }
        current["updated_at"] = now
        self._save_session(current)

    @contextmanager
    def _finalization_stage(
        self,
        current: dict[str, Any],
        readiness: dict[str, Any],
        stage: str,
        message: str,
    ):
        """Project one durable finalization substage with a live heartbeat."""

        self._record_finalization_progress(current, readiness, stage, message)
        stopped = threading.Event()

        def heartbeat() -> None:
            sequence = 0
            while not stopped.wait(max(0.05, float(FINALIZATION_HEARTBEAT_SECONDS))):
                sequence += 1
                snapshot = copy.deepcopy(current)
                snapshot_readiness = copy.deepcopy(readiness)
                try:
                    self._record_finalization_progress(
                        snapshot,
                        snapshot_readiness,
                        stage,
                        message,
                        heartbeat=sequence,
                    )
                except Exception as exc:
                    _log.debug("Builder finalization heartbeat failed: %s", exc)

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"builder-finalization-{_safe_token(stage)}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            yield
        finally:
            stopped.set()
            heartbeat_thread.join(timeout=1.0)

    def _finalize_completed_session(self, session: Mapping[str, Any]) -> None:
        """Prepare the DEV runtime, refresh the paired UI, then notify chat."""
        reconciled = self._reconcile_completed_workflow(session)
        if reconciled is not None:
            self._notify_completed_session(reconciled)
            return
        current = dict(session)
        object_type = str(session.get("object_type") or "").strip()
        object_id = str(session.get("object_id") or "").strip()
        webspace_id = str(session.get("webspace_id") or "desktop").strip() or "desktop"
        readiness: dict[str, Any] = {
            "ok": False,
            "task_id": str(session.get("current_task_id") or "").strip() or None,
            "iteration": int(session.get("iteration") or 0),
            "skill": None,
            "skills": [],
            "materialization": None,
            "aprobation": None,
            "vcs_checkpoints": [],
            "completed_at": None,
        }
        failed_checkpoints: list[Mapping[str, Any]] = []
        acceptance_failed = False
        existing_binding: dict[str, Any] = {}
        preview_target: Mapping[str, Any] | None = None
        try:
            pending_transition = str(current.get("pending_workflow_transition") or "").strip()
            with self._finalization_stage(
                current,
                readiness,
                "snapshot",
                "Recording the validated Automation source snapshot",
            ):
                if pending_transition == "return_to_prototype":
                    readiness["workflow_transition"] = self._workflow().snapshot_current_prototype(
                        object_type,
                        object_id,
                        source_task_id=str(current.get("current_task_id") or "").strip() or None,
                        request_text="Safe prototype derived by the built-in LLM from the Automation result",
                    )
                else:
                    readiness["automation_snapshot"] = self._workflow().snapshot_current_automation(
                        object_type,
                        object_id,
                        task_id=str(current.get("current_task_id") or "").strip() or None,
                    )
            companion_skill_ids = self._session_changed_companion_skill_ids(session)
            if companion_skill_ids:
                with self._finalization_stage(
                    current,
                    readiness,
                    "activation",
                    "Packaging, validating and activating the DEV skill runtime",
                ):
                    readiness["skills"] = [
                        self._prepare_and_activate_dev_skill(
                            skill_id,
                            webspace_id=webspace_id,
                        )
                        for skill_id in companion_skill_ids
                    ]
                readiness["skill"] = readiness["skills"][0]

            if pending_transition != "return_to_prototype":
                with self._finalization_stage(
                    current,
                    readiness,
                    "consumer_acceptance",
                    "Running admitted consumer-owned acceptance checks",
                ):
                    readiness["acceptance"] = self._run_development_acceptance(
                        session,
                        activations=list(readiness.get("skills") or []),
                    )
                if not bool(readiness["acceptance"].get("ok")):
                    acceptance_failed = True
                    raise RuntimeError(
                        "Consumer acceptance failed: "
                        + "; ".join(
                            str(item) for item in readiness["acceptance"].get("errors") or []
                        )
                    )

            # Forge checkpoints are durable release inputs.  Create them only
            # after activation and every required consumer-owned acceptance
            # receipt have passed, never merely after candidate-owned tests.
            previous_readiness = (
                session.get("completion_readiness")
                if isinstance(session.get("completion_readiness"), Mapping)
                else {}
            )
            confirmed_checkpoints = [
                dict(item)
                for item in previous_readiness.get("vcs_checkpoints") or []
                if isinstance(item, Mapping) and bool(item.get("ok"))
            ]
            if bool(current.get("reuse_confirmed_checkpoints")) and confirmed_checkpoints:
                readiness["vcs_checkpoints"] = confirmed_checkpoints
            else:
                with self._finalization_stage(
                    current,
                    readiness,
                    "forge_checkpoint",
                    "Creating transactional Forge checkpoints",
                ):
                    readiness["vcs_checkpoints"] = self._checkpoint_completed_artifacts(session)
            failed_checkpoints = [
                item
                for item in readiness["vcs_checkpoints"]
                if not bool(item.get("ok"))
            ]
            if failed_checkpoints:
                failed_refs = ", ".join(
                    f"{item.get('kind') or 'artifact'}:{item.get('name') or '?'}"
                    for item in failed_checkpoints
                )
                raise RuntimeError(f"Forge checkpoint failed for {failed_refs}")

            aprobation_scenario_id = object_id if object_type == "scenario" and object_id else None
            if self._session_requires_aprobation_overlay(current) and (
                companion_skill_ids or aprobation_scenario_id
            ):
                with self._finalization_stage(
                    current,
                    readiness,
                    "aprobation_activation",
                    "Activating the DEV repair as a workspace runtime overlay",
                ):
                    readiness["aprobation"] = self._prepare_and_activate_aprobation_overlay(
                        current,
                        skill_ids=companion_skill_ids,
                        scenario_id=aprobation_scenario_id,
                        webspace_id=webspace_id,
                    )

            if object_type == "scenario" and object_id:
                from adaos.services.builder.workbench import BuilderWorkbenchService

                workbench = BuilderWorkbenchService(state_dir=self.state_dir)
                get_binding = getattr(workbench, "get_workspace_binding", None)
                existing_binding = dict(get_binding(webspace_id) or {}) if callable(get_binding) else {}
                preview_target = (
                    existing_binding.get("preview_target")
                    if isinstance(existing_binding.get("preview_target"), Mapping)
                    else None
                )
                if preview_target:
                    readiness["materialization"] = {
                        "ok": True,
                        "skipped": "explicit_preview_target_preserved",
                        "preview_webspace_id": str(
                            existing_binding.get("preview_webspace_id")
                            or existing_binding.get("dev_webspace_id")
                            or ""
                        ).strip(),
                    }
                else:
                    binding = asyncio.run(
                        workbench.ensure_dev_webspace(
                            webspace_id,
                            runtime_scenario_id=object_id,
                            wait_for_rebuild=True,
                        )
                    )
                    runtime = binding.get("runtime") if isinstance(binding.get("runtime"), Mapping) else {}
                    readiness["materialization"] = {
                        **dict(runtime),
                        "preview_webspace_id": str(
                            binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or ""
                        ).strip(),
                    }
                    if not bool(readiness["materialization"].get("ok", False)):
                        raise RuntimeError(
                            str(readiness["materialization"].get("error") or "dev webspace reload failed")
                        )

            if pending_transition == "return_to_prototype":
                transition_snapshot = (
                    readiness.get("workflow_transition")
                    if isinstance(readiness.get("workflow_transition"), Mapping)
                    else {}
                )
                transition_result = self._workflow().transition(
                    object_type,
                    object_id,
                    "return_to_prototype",
                    actor="builder.automation",
                    reason="safe prototype adaptation completed",
                    metadata={
                        "revision": transition_snapshot.get("revision"),
                        "task_id": current.get("current_task_id"),
                        "change_id": current.get("change_id"),
                    },
                )
                readiness["workflow_transition"] = {
                    **dict(transition_snapshot),
                    "transition": transition_result,
                }
                current.pop("pending_workflow_transition", None)
            else:
                workflow_projection = self._workflow().describe(object_type, object_id)
                if str(workflow_projection.get("active_phase") or "prototype") == "prototype":
                    self._workflow().transition(
                        object_type,
                        object_id,
                        "automation_started",
                        actor="builder.automation.recovery",
                        reason="reconciled a completed legacy Automation session",
                        metadata={
                            "confirmed": True,
                            "source_prototype_revision": current.get("source_prototype_version"),
                            "task_id": current.get("current_task_id"),
                            "change_id": current.get("change_id"),
                        },
                    )
                    workflow_projection = self._workflow().describe(object_type, object_id)
                workflow_automation = (
                    workflow_projection.get("automation")
                    if isinstance(workflow_projection.get("automation"), Mapping)
                    else {}
                )
                if str(workflow_automation.get("status") or "") == "failed":
                    self._workflow().transition(
                        object_type,
                        object_id,
                        "automation_iteration_started",
                        actor="builder.automation.recovery",
                        reason="re-enter Automation after a validated checkpoint reconciliation",
                        metadata={
                            "confirmed": True,
                            "reconciliation": True,
                            "task_id": current.get("current_task_id"),
                            "change_id": current.get("change_id"),
                            "run_id": current.get("change_id"),
                            "context_packet_digest": current.get("context_packet_digest"),
                        },
                    )
                    workflow_projection = self._workflow().describe(object_type, object_id)
                    workflow_automation = (
                        workflow_projection.get("automation")
                        if isinstance(workflow_projection.get("automation"), Mapping)
                        else {}
                    )
                exact_completed_task = bool(
                    str(workflow_automation.get("status") or "") == "completed"
                    and str(workflow_automation.get("head_task_id") or "").strip()
                    == str(current.get("current_task_id") or "").strip()
                )
                if exact_completed_task:
                    readiness["workflow_automation"] = {
                        "status": "reused_completed_task",
                        "task_id": str(current.get("current_task_id") or "").strip(),
                    }
                else:
                    self._workflow().transition(
                        object_type,
                        object_id,
                        "automation_completed",
                        actor="builder.automation",
                        metadata={
                            "task_id": current.get("current_task_id"),
                            "change_id": current.get("change_id"),
                            "version": self._project_version(object_type, object_id),
                            "snapshot_path": (
                                readiness.get("automation_snapshot", {}).get("path")
                                if isinstance(readiness.get("automation_snapshot"), Mapping)
                                else None
                            ),
                        },
                    )
            preview_matches_project = self._preview_target_matches_project(
                preview_target,
                object_type=object_type,
                object_id=object_id,
            )
            if (
                preview_target
                and bool(preview_target.get("follow_active"))
                and not str(preview_target.get("object_type") or "").strip()
                and not str(preview_target.get("object_id") or "").strip()
            ):
                # Compatibility with bindings created before Preview targets
                # carried explicit project identity.
                preview_matches_project = True
            preview_binding_unchanged = self._preview_binding_unchanged(current, existing_binding)
            preview_should_follow = bool(
                preview_matches_project
                and (
                    bool(preview_target.get("follow_active"))
                    or preview_binding_unchanged
                )
            )
            if object_type == "scenario" and object_id and preview_should_follow:
                from adaos.sdk.builder import preview

                target_stage = "prototype" if pending_transition == "return_to_prototype" else "automation"
                readiness["materialization"] = preview.select_target(
                    object_type,
                    object_id,
                    stage=target_stage,
                    source_webspace_id=webspace_id,
                    follow_active=True,
                )
                if not bool(readiness["materialization"].get("ok", False)):
                    raise RuntimeError(
                        str(
                            readiness["materialization"].get("error_detail")
                            or readiness["materialization"].get("error")
                            or "selected preview target materialization failed"
                        )
                    )
                readiness["preview_transition"] = {
                    "status": "followed_completed_work",
                    "stage": target_stage,
                    "reason": (
                        "follow_active"
                        if bool(preview_target.get("follow_active"))
                        else "unchanged_since_submit"
                    ),
                }
            elif object_type == "scenario" and object_id and preview_target:
                readiness["preview_transition"] = {
                    "status": "preserved_user_selection",
                    "stage": str(preview_target.get("stage") or "").strip() or None,
                    "reason": (
                        "project_mismatch"
                        if not preview_matches_project
                        else "changed_since_submit"
                    ),
                }
            if pending_transition != "return_to_prototype":
                primary_checkpoint = next(
                    (
                        item
                        for item in readiness["vcs_checkpoints"]
                        if str(item.get("kind") or "").strip().lower().rstrip("s") == object_type
                        and str(item.get("name") or "").strip() == object_id
                    ),
                    None,
                )
                if readiness["vcs_checkpoints"] and not primary_checkpoint:
                    raise RuntimeError(
                        f"Forge checkpoints do not include the primary {object_type}:{object_id} artifact"
                    )
                if primary_checkpoint:
                    change_id = str(current.get("change_id") or "").strip()
                    package_digest = str(primary_checkpoint.get("package_digest") or "").strip()
                    source_revision = str(
                        primary_checkpoint.get("source_revision")
                        or primary_checkpoint.get("commit")
                        or ""
                    ).strip()
                    if not change_id or not package_digest or not source_revision:
                        raise RuntimeError(
                            "Primary Forge checkpoint is missing change, package, or source identity"
                        )
                    workflow = self._workflow().describe(object_type, object_id)
                    delivery = (
                        dict(workflow.get("delivery"))
                        if isinstance(workflow.get("delivery"), Mapping)
                        else {}
                    )
                    delivery_status = str(delivery.get("status") or "").strip()
                    existing_package_digest = str(delivery.get("package_digest") or "").strip()
                    existing_source_revision = str(delivery.get("source_revision") or "").strip()
                    existing_change_id = str(delivery.get("checkpoint_change_id") or "").strip()
                    exact_checkpoint_in_progress = bool(
                        delivery_status in {"checkpoint", "activating"}
                        and existing_package_digest == package_digest
                        and existing_source_revision == source_revision
                        and existing_change_id == change_id
                    )
                    exact_candidate_in_progress = bool(
                        delivery_status in {"trial", "accepted", "published"}
                        and existing_package_digest == package_digest
                    )
                    if exact_checkpoint_in_progress or exact_candidate_in_progress:
                        readiness["workflow_checkpoint"] = {
                            "ok": True,
                            "duplicate": True,
                            "reconciled_from": "canonical_builder_workflow",
                            "delivery_status": delivery_status,
                            "workflow": workflow,
                        }
                    else:
                        readiness["workflow_checkpoint"] = self._workflow().transition(
                            object_type,
                            object_id,
                            "checkpoint_recorded",
                            actor="builder.automation",
                            reason="Automation result checkpointed in Forge",
                            metadata={
                                # This acknowledges exact Forge identities, not
                                # human Trial approval. Publication still needs
                                # an explicit accept_trial command.
                                "confirmed": True,
                                "change_id": change_id,
                                "package_digest": package_digest,
                                "source_revision": source_revision,
                                "version": self._project_version(object_type, object_id),
                                "task_id": current.get("current_task_id"),
                            },
                        )
                if self._session_requires_aprobation_overlay(current):
                    readiness["aprobation"] = self._ensure_governed_aprobation_trial(
                        current,
                        readiness.get("aprobation")
                        if isinstance(readiness.get("aprobation"), Mapping)
                        else {},
                    )
            readiness["ok"] = True
            readiness["completed_at"] = _now_iso()
            current["completion_readiness"] = readiness
            current["status"] = "completed"
            current["progress"] = {
                "task_id": current.get("current_task_id"),
                "status": "completed",
                "message": "Automation result activated and checkpointed",
                "updated_at": readiness["completed_at"],
            }
            current.pop("finalizing_task_id", None)
            current.pop("reuse_confirmed_checkpoints", None)
            current.pop("rebind_confirmed_checkpoint", None)
            current.pop("last_failure", None)
            current["updated_at"] = readiness["completed_at"]
            self._save_session(current)
        except Exception as exc:
            readiness["error"] = f"{type(exc).__name__}: {exc}"
            readiness["completed_at"] = _now_iso()
            current["completion_readiness"] = readiness
            current["status"] = "failed"
            current["progress"] = {
                "task_id": current.get("current_task_id"),
                "status": "failed",
                "message": readiness["error"],
                "updated_at": readiness["completed_at"],
            }
            current.pop("finalizing_task_id", None)
            current.pop("pending_workflow_transition", None)
            current.pop("rebind_confirmed_checkpoint", None)
            current["last_failure"] = {
                "stage": (
                    "forge_checkpoint"
                    if failed_checkpoints
                    else "consumer_acceptance"
                    if acceptance_failed
                    else "live_readiness"
                ),
                "message": readiness["error"],
                "updated_at": readiness["completed_at"],
            }
            current["updated_at"] = readiness["completed_at"]
            self._save_session(current)
            try:
                self._workflow().transition(
                    object_type,
                    object_id,
                    (
                        "return_to_prototype_failed"
                        if pending_transition == "return_to_prototype"
                        else "automation_failed"
                    ),
                    actor="builder.automation",
                    metadata={
                        "task_id": current.get("current_task_id"),
                        "change_id": current.get("change_id"),
                        "error": readiness["error"],
                    },
                )
            except Exception:
                pass
            if self.event_sink:
                self.event_sink(self.project_session(current))
            return

        self._notify_completed_session(current)

    def _checkpoint_completed_artifacts(self, session: Mapping[str, Any]) -> list[dict[str, Any]]:
        from adaos.services.builder.workspace import BuilderWorkspaceService

        result = session.get("last_result") if isinstance(session.get("last_result"), Mapping) else {}
        message = " ".join(
            str(
                result.get("summary")
                or result.get("message")
                or session.get("implementation_brief")
                or "Builder automation completed"
            ).split()
        )[:240]
        object_type = str(session.get("object_type") or "").strip().lower().rstrip("s")
        object_id = str(session.get("object_id") or "").strip()
        primary_artifact = (object_type, object_id)
        artifacts: list[tuple[str, str]] = [
            ("skill", skill_id)
            for skill_id in self._session_companion_skill_ids(session)
        ]
        if object_type in {"skill", "scenario"} and object_id and (object_type, object_id) not in artifacts:
            artifacts.append((object_type, object_id))

        changed_paths_value = result.get("changed_paths")
        if isinstance(changed_paths_value, list):
            changed_paths = {
                str(path or "").replace("\\", "/").lstrip("./")
                for path in changed_paths_value
                if str(path or "").strip()
            }
            created_artifacts = {
                (
                    str(item.get("kind") or "").strip().lower().rstrip("s"),
                    str(item.get("name") or item.get("id") or "").strip(),
                )
                for item in session.get("created_artifacts") or []
                if isinstance(item, Mapping)
            }
            changed_artifacts = {
                (kind, artifact_id)
                for kind, artifact_id in artifacts
                if any(
                    path == f"{kind}s/{artifact_id}"
                    or path.startswith(f"{kind}s/{artifact_id}/")
                    for path in changed_paths
                )
            }
            artifact_content_changed = changed_artifacts | created_artifacts
            artifacts = [
                (kind, artifact_id)
                for kind, artifact_id in artifacts
                if (kind, artifact_id) in artifact_content_changed
                or (
                    (kind, artifact_id) == primary_artifact
                    and bool(artifact_content_changed)
                )
            ]

        service = BuilderWorkspaceService.from_context()
        checkpoints: list[dict[str, Any]] = []
        change_id = str(session.get("change_id") or "").strip()
        conversation_id = str(session.get("conversation_id") or "").strip()
        topic_id = str(session.get("topic_id") or "").strip()
        metadata = {
            "change_id": change_id,
            "change_set_id": str(session.get("change_set_id") or "").strip(),
            "conversation_id": conversation_id,
            "topic_id": topic_id,
            "thread_id": topic_id,
            "request_id": str(session.get("current_task_id") or "").strip(),
        }
        metadata = {key: value for key, value in metadata.items() if value}
        for kind, artifact_id in artifacts:
            try:
                checkpoint_kwargs: dict[str, Any] = {
                    "kind": kind,
                    "artifact_id": artifact_id,
                    "message": message,
                }
                if metadata:
                    checkpoint_kwargs["metadata"] = metadata
                checkpoints.append(
                    dict(service.checkpoint_artifact(**checkpoint_kwargs) or {})
                )
            except Exception as exc:
                checkpoints.append(
                    {
                        "ok": False,
                        "kind": kind,
                        "name": artifact_id,
                        "message": message,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        if change_id and conversation_id:
            try:
                from adaos.services import conversation_store

                conversation_store.upsert_development_change(
                    change_id=change_id,
                    conversation_id=conversation_id,
                    thread_id=topic_id or None,
                    topic_id=topic_id or None,
                    status="pushed" if checkpoints and all(item.get("ok") for item in checkpoints) else "checkpoint_failed",
                    artifact_refs=[{"kind": kind, "id": artifact_id} for kind, artifact_id in artifacts],
                    commit_refs=[
                        {"kind": item.get("kind"), "id": item.get("name"), "commit": item.get("commit")}
                        for item in checkpoints
                        if item.get("commit")
                    ],
                    request_id=str(session.get("current_task_id") or "").strip() or None,
                    summary=message,
                    meta={
                        "automation_session_id": session.get("session_id"),
                        "change_set_id": session.get("change_set_id"),
                    },
                )
            except Exception:
                pass
        return checkpoints

    def _prepare_and_activate_dev_skill(self, skill_id: str, *, webspace_id: str) -> dict[str, Any]:
        """Run package-external DEV lifecycle steps owned by the orchestrator."""
        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.agent_context import get_ctx
        from adaos.services.builder.workbench import BuilderWorkbenchService
        from adaos.services.skill.manager import SkillManager

        ctx = get_ctx()
        manager = SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )
        # Repeat packaged tests in the exact prepared-slot environment before
        # activation. Worker-side source tests are an early repair rail, but
        # they cannot prove that owner-scoped paths, dependency resolution,
        # and slot metadata behave identically after ProjectRelease.
        prepared = manager.prepare_dev_runtime(skill_id, run_tests=True)
        binding = BuilderWorkbenchService(state_dir=self.state_dir).get_workspace_binding(webspace_id)
        preview_webspace_id = str(
            binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or ""
        ).strip()
        if not preview_webspace_id:
            raise RuntimeError("Builder preview relation is missing")
        slot = manager.activate_for_space(
            skill_id,
            version=prepared.version,
            slot=prepared.slot,
            space="dev",
            webspace_id=preview_webspace_id,
            defer_webspace_rebuild=True,
        )
        status = manager.dev_runtime_status(skill_id)
        if not bool(status.get("ready")) or not bool(status.get("active")):
            raise RuntimeError(f"DEV skill {skill_id!r} did not become active")
        return {
            "ok": True,
            "id": skill_id,
            "version": prepared.version,
            "slot": slot,
            "resolved_manifest": str(prepared.resolved_manifest),
        }

    @staticmethod
    def _session_repair_brief(session: Mapping[str, Any]) -> dict[str, Any]:
        raw = session.get("implementation_brief")
        if isinstance(raw, Mapping):
            return dict(raw)
        try:
            payload = json.loads(str(raw or ""))
        except (TypeError, ValueError):
            return {}
        return dict(payload) if isinstance(payload, Mapping) else {}

    @classmethod
    def _session_requires_aprobation_overlay(cls, session: Mapping[str, Any]) -> bool:
        links = session.get("links") if isinstance(session.get("links"), Mapping) else {}
        ticket_id = str(links.get("development_ticket_id") or "").strip()
        brief = cls._session_repair_brief(session)
        policy = brief.get("policy") if isinstance(brief.get("policy"), Mapping) else {}
        is_autonomous_ticket_repair = (
            str(brief.get("execution_mode") or "").strip() == "surgical_dev_ticket_repair"
        )
        if not ticket_id or policy.get("publication_required") is False:
            return False
        return bool(
            policy.get("publication_required") is True or is_autonomous_ticket_repair
        )

    def _ensure_governed_aprobation_trial(
        self,
        session: Mapping[str, Any],
        overlay_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind the live runtime overlay to Builder's immutable Trial candidate."""

        object_type = str(session.get("object_type") or "").strip()
        object_id = str(session.get("object_id") or "").strip()
        if not object_type or not object_id:
            raise RuntimeError("Builder Trial requires project identity")
        workflow = self._workflow().describe(object_type, object_id)
        delivery = (
            dict(workflow.get("delivery"))
            if isinstance(workflow.get("delivery"), Mapping)
            else {}
        )
        delivery_status = str(delivery.get("status") or "").strip()
        result: dict[str, Any] = {}
        if delivery_status in {"checkpoint", "activating"}:
            from adaos.sdk.builder import lifecycle

            task_id = str(session.get("current_task_id") or "").strip()
            package_digest = str(delivery.get("package_digest") or "").strip()
            result = lifecycle.prepare_trial(
                object_type,
                object_id,
                actor="builder.automation",
                idempotency_key=(
                    f"dev-ticket-trial:{task_id or object_id}:"
                    f"{package_digest[-24:] or 'checkpoint'}"
                ),
                source_webspace_id=str(session.get("webspace_id") or "desktop").strip()
                or "desktop",
                target_webspace_id=str(session.get("webspace_id") or "desktop").strip()
                or "desktop",
            )
            workflow = (
                dict(result.get("workflow"))
                if isinstance(result.get("workflow"), Mapping)
                else self._workflow().describe(object_type, object_id)
            )
            delivery = (
                dict(workflow.get("delivery"))
                if isinstance(workflow.get("delivery"), Mapping)
                else {}
            )
            delivery_status = str(delivery.get("status") or "").strip()
        if delivery_status not in {"trial", "accepted", "published"}:
            raise RuntimeError(
                "Builder Trial did not reach a reviewable state: "
                f"{delivery_status or 'missing'}"
            )

        brief = self._session_repair_brief(session)
        issues = [
            dict(item)
            for item in brief.get("issues") or []
            if isinstance(item, Mapping)
        ]
        if not issues and str(brief.get("summary") or "").strip():
            issues = [{"summary": str(brief.get("summary") or "").strip()}]
        candidate = (
            dict(result.get("candidate"))
            if isinstance(result.get("candidate"), Mapping)
            else {}
        )
        release = (
            dict(result.get("release"))
            if isinstance(result.get("release"), Mapping)
            else {}
        )
        repair_hints = (
            dict(brief.get("repair_hints"))
            if isinstance(brief.get("repair_hints"), Mapping)
            else {}
        )
        change_summary = str(
            brief.get("summary") or repair_hints.get("change_summary") or ""
        ).strip()
        links = (
            dict(session.get("links"))
            if isinstance(session.get("links"), Mapping)
            else {}
        )
        ticket_ids = list(
            dict.fromkeys(
                [
                    str(links.get("development_ticket_id") or "").strip(),
                    *[
                        str(item).strip()
                        for item in links.get("development_ticket_ids") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        ticket_ids = [item for item in ticket_ids if item]
        receipt = {
            **dict(overlay_receipt),
            "ok": bool(overlay_receipt.get("ok")),
            "audience": "alpha",
            "source_kind": "devspace",
            "trial": {
                "schema": "adaos.builder.component_trial.v1",
                "status": delivery_status,
                "candidate_id": str(delivery.get("candidate_id") or candidate.get("candidate_id") or "").strip()
                or None,
                "candidate_digest": str(
                    delivery.get("package_digest")
                    or candidate.get("package_digest")
                    or ""
                ).strip()
                or None,
                "release_digest": str(
                    delivery.get("release_digest")
                    or candidate.get("release_digest")
                    or ""
                ).strip()
                or None,
                "version": str(
                    delivery.get("version")
                    or release.get("version")
                    or ""
                ).strip()
                or None,
                "workflow_generation": workflow.get("generation"),
                "trial_workspace": delivery.get("trial_workspace")
                or result.get("trial_workspace"),
                "started_at": delivery.get("prepared_at") or _now_iso(),
            },
            "changelog": {
                "schema": "adaos.component_changelog.v1",
                "title": f"{object_id} alpha update",
                "summary": change_summary or "Builder prepared an update for review.",
                "changes": [
                    str(item.get("summary") or item.get("title") or "").strip()
                    for item in issues
                    if str(item.get("summary") or item.get("title") or "").strip()
                ][:12],
                "ticket_ids": ticket_ids,
            },
        }
        component_update = self._record_component_update(session, receipt)
        if component_update is None:
            raise RuntimeError("Builder Trial component update notice was not persisted")
        receipt["component_update"] = component_update
        projection = self._refresh_component_update_projection(session, receipt)
        if projection is not None:
            receipt["component_update_projection"] = projection
        return receipt

    def _record_component_update(
        self,
        session: Mapping[str, Any],
        aprobation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        try:
            from adaos.services.component_updates import ComponentUpdateService

            links = session.get("links") if isinstance(session.get("links"), Mapping) else {}
            ticket_ids = list(
                dict.fromkeys(
                    [
                        str(links.get("development_ticket_id") or "").strip(),
                        *[
                            str(item).strip()
                            for item in links.get("development_ticket_ids") or []
                            if str(item).strip()
                        ],
                    ]
                )
            )
            notice = ComponentUpdateService(state_dir=self.state_dir).record_aprobation(
                component_type=str(session.get("object_type") or "").strip(),
                component_id=str(session.get("object_id") or "").strip(),
                aprobation=aprobation,
                webspace_id=str(session.get("webspace_id") or "desktop").strip()
                or "desktop",
                ticket_ids=tuple(item for item in ticket_ids if item),
            )
            return dict(notice) if isinstance(notice, Mapping) else None
        except Exception:
            _log.exception(
                "failed to persist component update notice session=%s",
                session.get("session_id"),
            )
            raise

    def _refresh_component_update_projection(
        self,
        session: Mapping[str, Any],
        aprobation: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Make a persisted review notice visible in the same runtime transition."""

        if str(aprobation.get("mode") or "").strip() != "devspace_to_workspace_runtime_overlay":
            return None
        skills = [
            dict(item)
            for item in aprobation.get("skills") or []
            if isinstance(item, Mapping) and str(item.get("id") or "").strip()
        ]
        scenario = (
            dict(aprobation.get("scenario"))
            if isinstance(aprobation.get("scenario"), Mapping)
            else {}
        )
        if not skills and (not scenario or bool(scenario.get("skipped"))):
            return None

        from adaos.services.runtime_refresh import rebuild_webspace_projection_sync
        from adaos.services.scenario.webspace_runtime import invalidate_webspace_materialization_cache

        webspace_id = str(
            aprobation.get("webspace_id") or session.get("webspace_id") or "desktop"
        ).strip() or "desktop"
        trial = (
            dict(aprobation.get("trial"))
            if isinstance(aprobation.get("trial"), Mapping)
            else {}
        )
        trial_status = str(trial.get("status") or "").strip().lower()
        trial_pending = trial_status == "trial" and not str(trial.get("decision") or "").strip()
        object_type = str(session.get("object_type") or "").strip().lower()
        object_id = str(session.get("object_id") or "").strip()

        invalidate_webspace_materialization_cache(
            webspace_id,
            reason="component_update_notice_changed",
            action="builder_component_update_sync",
            source_of_truth="component_update_notice",
        )
        if trial_pending and object_type == "scenario" and scenario and not bool(scenario.get("skipped")):
            refreshed = self._prepare_and_activate_aprobation_scenario(
                object_id,
                webspace_id=webspace_id,
            )
            projection = (
                dict(refreshed.get("webspace_projection"))
                if isinstance(refreshed.get("webspace_projection"), Mapping)
                else {}
            )
        else:
            projection = rebuild_webspace_projection_sync(
                webspace_id=webspace_id,
                action="builder_component_update_sync",
                source_of_truth=(
                    "devspace_runtime_overlay" if trial_pending else "component_update_notice"
                ),
                scenario_resolution=("builder_aprobation_overlay" if trial_pending else None),
                skill_source_mode=("dev" if trial_pending else "workspace"),
            )
        materialization = (
            dict(projection.get("materialization"))
            if isinstance(projection.get("materialization"), Mapping)
            else {}
        )
        if not bool(projection.get("ok")) or materialization.get("ready") is not True:
            raise RuntimeError(
                str(
                    projection.get("error")
                    or "Component update notice webspace materialization is not ready"
                )
            )
        return {
            "ok": True,
            "webspace_id": webspace_id,
            "stage": "alpha" if trial_pending else "published_or_reverted",
            "materialization": materialization,
        }

    def _rollback_aprobation_overlay(
        self,
        session: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Restore the exact workspace runtime that preceded a DEV overlay."""

        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.agent_context import get_ctx
        from adaos.services.runtime_refresh import rebuild_webspace_projection_sync
        from adaos.services.scenario.webspace_runtime import (
            invalidate_webspace_materialization_cache,
            rebuild_webspace_from_sources,
        )
        from adaos.services.skill.manager import SkillManager
        from adaos.services.skills_loader_importlib import ImportlibSkillsLoader

        ctx = get_ctx()
        manager = SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )
        webspace_id = str(
            receipt.get("webspace_id") or session.get("webspace_id") or "desktop"
        ).strip() or "desktop"
        restored_skills: list[dict[str, Any]] = []
        errors: list[str] = []
        for raw_skill in receipt.get("skills") or []:
            if not isinstance(raw_skill, Mapping):
                continue
            skill = dict(raw_skill)
            skill_id = str(skill.get("id") or "").strip()
            previous = (
                dict(skill.get("previous_runtime"))
                if isinstance(skill.get("previous_runtime"), Mapping)
                else {}
            )
            if not skill_id:
                continue
            try:
                version = str(previous.get("version") or "").strip()
                slot = str(previous.get("slot") or "").strip().upper()
                if version and slot in {"A", "B"}:
                    restored = manager.restore_runtime_selection_exact(
                        skill_id,
                        version=version,
                        slot=slot,
                        previous_deactivation=(
                            previous.get("deactivation")
                            if isinstance(previous.get("deactivation"), Mapping)
                            else None
                        ),
                        webspace_id=webspace_id,
                        emit_activation=False,
                    )
                else:
                    restored_slot = manager.rollback_runtime(skill_id)
                    restored = {
                        "ok": True,
                        "restored_active_slot": restored_slot,
                        "mode": "runtime_history",
                    }
                handlers = asyncio.run(
                    ImportlibSkillsLoader().reload_skill_handlers(
                        ctx.paths.skills_dir(),
                        skill_id,
                    )
                )
                restored_skills.append(
                    {
                        "id": skill_id,
                        "runtime": restored,
                        "handler_reload": handlers,
                    }
                )
            except Exception as exc:
                errors.append(f"{skill_id}: {type(exc).__name__}: {exc}")

        scenario = (
            dict(receipt.get("scenario"))
            if isinstance(receipt.get("scenario"), Mapping)
            else {}
        )
        scenario_id = str(scenario.get("id") or "").strip()
        scenario_projection: dict[str, Any] | None = None
        if scenario_id and not bool(scenario.get("skipped")):

            async def _restore_scenario() -> dict[str, Any]:
                return await rebuild_webspace_from_sources(
                    webspace_id,
                    action="builder_aprobation_rollback",
                    scenario_id=scenario_id,
                    scenario_resolution="workspace_source_restore",
                    source_of_truth="workspace_sources",
                    reseed_from_scenario=False,
                    request_id=f"builder-aprobation-rollback-{time.time_ns()}",
                    switch_mode="materialization_pointer_compat",
                    skill_source_mode="workspace",
                )

            try:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    scenario_projection = asyncio.run(_restore_scenario())
                else:
                    result: dict[str, Any] | None = None
                    error: BaseException | None = None

                    def _thread_main() -> None:
                        nonlocal result
                        nonlocal error
                        try:
                            result = asyncio.run(_restore_scenario())
                        except BaseException as exc:
                            error = exc

                    thread = threading.Thread(
                        target=_thread_main,
                        name="builder-aprobation-scenario-rollback",
                        daemon=True,
                    )
                    thread.start()
                    thread.join()
                    if error is not None:
                        raise error
                    scenario_projection = result if isinstance(result, dict) else {}
                if not bool((scenario_projection or {}).get("ok")):
                    raise RuntimeError(
                        str(
                            (scenario_projection or {}).get("error")
                            or "scenario overlay rollback failed"
                        )
                    )
            except Exception as exc:
                errors.append(f"scenario:{scenario_id}: {type(exc).__name__}: {exc}")
        elif restored_skills:
            try:
                invalidate_webspace_materialization_cache(
                    webspace_id,
                    reason="builder_aprobation_rollback",
                    action="builder_aprobation_skill_rollback",
                    source_of_truth="workspace_sources",
                )
                scenario_projection = rebuild_webspace_projection_sync(
                    webspace_id=webspace_id,
                    action="skill_aprobation_rollback",
                    source_of_truth="workspace_sources",
                    scenario_resolution="workspace_source_restore",
                    skill_source_mode="workspace",
                )
                if not bool(scenario_projection.get("ok")):
                    raise RuntimeError(
                        str(
                            scenario_projection.get("error")
                            or "skill overlay rollback materialization failed"
                        )
                    )
            except Exception as exc:
                errors.append(f"webspace:{webspace_id}: {type(exc).__name__}: {exc}")

        rollback = {
            "schema": "adaos.builder.aprobation_runtime_rollback.v1",
            "ok": not errors,
            "mode": "restore_workspace_runtime",
            "webspace_id": webspace_id,
            "skills": restored_skills,
            "scenario": scenario_projection,
            "errors": errors,
            "recorded_at": _now_iso(),
        }
        if errors:
            raise RuntimeError("; ".join(errors))
        return rollback

    def _prepare_and_activate_aprobation_overlay(
        self,
        session: Mapping[str, Any],
        *,
        skill_ids: Sequence[str],
        scenario_id: str | None = None,
        webspace_id: str,
    ) -> dict[str, Any]:
        """Expose a validated DEV repair to the user without replacing sources.

        The stable workspace source tree remains unchanged. Skill repairs are
        prepared into the default workspace runtime from DEV source. Scenario
        repairs are applied as a materialized runtime overlay in the target
        webspace. A human can then accept/reopen the ticket from the real
        client surface before any source promotion.
        """

        skills: list[dict[str, Any]] = []
        errors: list[str] = []
        for skill_id in dict.fromkeys(str(item).strip() for item in skill_ids if str(item).strip()):
            try:
                skills.append(
                    self._prepare_and_activate_aprobation_skill(
                        skill_id,
                        webspace_id=webspace_id,
                    )
                )
            except Exception as exc:
                errors.append(f"{skill_id}: {type(exc).__name__}: {exc}")
        scenario_receipt: dict[str, Any] | None = None
        scenario_token = str(scenario_id or "").strip()
        if scenario_token:
            try:
                scenario_receipt = self._prepare_and_activate_aprobation_scenario(
                    scenario_token,
                    webspace_id=webspace_id,
                )
            except Exception as exc:
                errors.append(f"scenario:{scenario_token}: {type(exc).__name__}: {exc}")
        applied_count = len(skills) + (
            1
            if isinstance(scenario_receipt, Mapping)
            and not bool(scenario_receipt.get("skipped"))
            else 0
        )
        receipt = {
            "schema": "adaos.builder.aprobation_runtime_overlay.v1",
            "ok": not errors and applied_count > 0,
            "mode": "devspace_to_workspace_runtime_overlay",
            "source_policy": "workspace_sources_preserved",
            "runtime_space": "default",
            "source_space": "dev",
            "webspace_id": webspace_id,
            "skill_count": len(skills),
            "skills": skills,
            "scenario": scenario_receipt,
            "applied_count": applied_count,
            "errors": errors,
            "ticket_id": str(
                (
                    session.get("links")
                    if isinstance(session.get("links"), Mapping)
                    else {}
                ).get("development_ticket_id")
                or ""
            ).strip()
            or None,
            "task_id": str(session.get("current_task_id") or "").strip() or None,
            "created_at": _now_iso(),
        }
        if not errors and applied_count <= 0:
            errors.append("no_aprobation_overlay_applied")
        if errors:
            receipt["ok"] = False
            receipt["errors"] = errors
            raise RuntimeError("; ".join(errors))
        return receipt

    def _prepare_and_activate_aprobation_skill(self, skill_id: str, *, webspace_id: str) -> dict[str, Any]:
        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.agent_context import get_ctx
        from adaos.services.runtime_refresh import rebuild_webspace_projection_sync
        from adaos.services.scenario.webspace_runtime import invalidate_webspace_materialization_cache
        from adaos.services.skill.manager import SkillManager
        from adaos.services.skills_loader_importlib import ImportlibSkillsLoader

        ctx = get_ctx()
        source_path = Path(ctx.paths.dev_skills_dir()) / skill_id
        if not source_path.is_dir():
            raise FileNotFoundError(f"DEV skill source is missing: {source_path}")
        manager = SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )
        try:
            previous_runtime = manager.runtime_status(skill_id)
        except RuntimeError:
            previous_runtime = {}
        prepared = manager.prepare_runtime(skill_id, path=source_path, run_tests=True)
        slot = manager.activate_for_space(
            skill_id,
            version=prepared.version,
            slot=prepared.slot,
            space="default",
            webspace_id=webspace_id,
            defer_webspace_rebuild=True,
            emit_activation=False,
        )
        handlers = asyncio.run(
            ImportlibSkillsLoader().reload_skill_handlers(
                ctx.paths.skills_dir(),
                skill_id,
            )
        )
        cache = invalidate_webspace_materialization_cache(
            webspace_id,
            reason="builder_aprobation_runtime_overlay",
            action="builder_aprobation_skill_runtime",
            source_of_truth="devspace_runtime_overlay",
        )
        projection = rebuild_webspace_projection_sync(
            webspace_id=webspace_id,
            action="skill_aprobation_sync",
            source_of_truth="devspace_runtime_overlay",
            scenario_resolution="builder_aprobation_overlay",
            skill_source_mode="dev",
        )
        materialization = (
            dict(projection.get("materialization"))
            if isinstance(projection.get("materialization"), Mapping)
            else {}
        )
        if not bool(projection.get("ok")) or materialization.get("ready") is not True:
            raise RuntimeError(
                str(projection.get("error") or "DEV skill webspace materialization is not ready")
            )
        cache = {
            **dict(cache),
            "status": "ready",
            "pending": False,
            "finished_at": time.time(),
            "materialization": materialization,
            "error": None,
        }
        return {
            "ok": True,
            "id": skill_id,
            "source_path": str(source_path),
            "source_space": "dev",
            "runtime_space": "default",
            "version": prepared.version,
            "previous_runtime": {
                "version": previous_runtime.get("version"),
                "slot": previous_runtime.get("active_slot"),
                "deactivation": previous_runtime.get("deactivation"),
            }
            if previous_runtime
            else None,
            "prepared_slot": prepared.slot,
            "activated_slot": slot,
            "resolved_manifest": str(prepared.resolved_manifest),
            "tests": {
                str(name): str(result.status)
                for name, result in dict(prepared.tests or {}).items()
            },
            "handler_reload": handlers,
            "materialization_cache": cache,
            "webspace_projection": projection,
        }

    def _prepare_and_activate_aprobation_scenario(self, scenario_id: str, *, webspace_id: str) -> dict[str, Any]:
        from adaos.services.scenario.webspace_runtime import (
            canonical_materialization_identity,
            rebuild_webspace_from_sources,
        )
        from adaos.services.scenarios import loader as scenarios_loader

        source_path = Path(scenarios_loader.scenario_root_for_space(scenario_id, "dev"))
        if not source_path.is_dir():
            return {
                "ok": True,
                "id": scenario_id,
                "source_path": str(source_path),
                "source_space": "dev",
                "runtime_space": "default",
                "skipped": True,
                "reason": "dev_scenario_source_missing",
            }
        content = scenarios_loader.read_content(scenario_id, space="dev")
        if not isinstance(content, Mapping) or not content:
            raise RuntimeError("DEV scenario content is unavailable")
        try:
            source_fingerprint = scenarios_loader.scenario_source_fingerprint(
                scenario_id,
                space="dev",
            )
        except Exception:
            source_fingerprint = ""
        identity = canonical_materialization_identity(
            webspace_id=webspace_id,
            scenario_id=scenario_id,
            source_fingerprint=f"dev:{source_fingerprint or 'current'}",
            user_id="builder",
            roles=["builder", "aprobation"],
            policy_fingerprint="devspace_runtime_overlay",
        )

        async def _apply() -> dict[str, Any]:
            return await rebuild_webspace_from_sources(
                webspace_id,
                action="builder_aprobation_apply",
                scenario_id=scenario_id,
                scenario_resolution="builder_aprobation_overlay",
                source_of_truth="devspace_runtime_overlay",
                reseed_from_scenario=False,
                request_id=(
                    f"builder-aprobation-{identity.get('key_hash') or _safe_token(scenario_id)}-"
                    f"{time.time_ns()}"
                ),
                switch_mode="materialization_pointer_compat",
                materialization_identity=identity,
                scenario_content_override=content,
                skill_source_mode="dev",
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            projection = asyncio.run(_apply())
        else:
            result: dict[str, Any] | None = None
            error: BaseException | None = None

            def _thread_main() -> None:
                nonlocal result
                nonlocal error
                try:
                    result = asyncio.run(_apply())
                except BaseException as exc:
                    error = exc

            thread = threading.Thread(
                target=_thread_main,
                name="builder-aprobation-scenario-runtime",
                daemon=True,
            )
            thread.start()
            thread.join()
            if error is not None:
                raise error
            projection = result if isinstance(result, dict) else {}

        if not bool(projection.get("ok")):
            raise RuntimeError(str(projection.get("error") or "scenario overlay rebuild failed"))
        return {
            "ok": True,
            "id": scenario_id,
            "source_path": str(source_path),
            "source_space": "dev",
            "runtime_space": "default",
            "source_fingerprint": source_fingerprint or None,
            "materialization_identity": identity,
            "webspace_projection": projection,
        }

    def _run_development_acceptance(
        self,
        session: Mapping[str, Any],
        *,
        activations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Run consumer-owned, digest-bound checks after DEV activation.

        Builder deliberately knows nothing about research, media, home control,
        or any other consumer domain.  A Development Session may instead name
        an admitted read-only context skill and one of its public operations.
        The provider receives the exact immutable instructions plus the active
        DEV candidate identity and returns a typed receipt.  Required failures
        stop checkpointing and publication.
        """

        development_session_id = str(session.get("development_session_id") or "").strip()
        if not development_session_id:
            return {
                "schema": "adaos.builder.acceptance_summary.v1",
                "ok": True,
                "profiles": [],
                "requirements": [],
                "receipts": [],
                "errors": [],
            }
        from adaos.sdk.builder import development_sessions

        policy = development_sessions.get(development_session_id)
        requirements = [
            dict(item)
            for item in policy.get("acceptance_requirements") or []
            if isinstance(item, Mapping)
        ]
        profiles = [str(item) for item in policy.get("acceptance_profiles") or []]
        if not requirements:
            return {
                "schema": "adaos.builder.acceptance_summary.v1",
                "ok": True,
                "profiles": profiles,
                "requirements": [],
                "receipts": [],
                "errors": [],
            }
        admitted_context = {
            str(item.get("ref") or ""): dict(item)
            for item in policy.get("context_members") or []
            if isinstance(item, Mapping)
        }
        instructions: dict[str, Any] = {}
        instruction_identities: list[dict[str, Any]] = []
        for descriptor in policy.get("instruction_inputs") or []:
            if not isinstance(descriptor, Mapping):
                continue
            kind = str(descriptor.get("kind") or "").strip()
            loaded = development_sessions.get_instruction(development_session_id, kind)
            instruction_identities.append(
                {
                    "kind": kind,
                    "content_digest": descriptor.get("content_digest"),
                    "media_type": descriptor.get("media_type"),
                }
            )
            if isinstance(loaded.get("value"), Mapping):
                instructions[kind] = dict(loaded["value"])

        candidate_ref = f"{str(session.get('object_type') or '').strip()}:{str(session.get('object_id') or '').strip()}"
        candidate = next(
            (
                dict(item)
                for item in activations
                if f"skill:{str(item.get('id') or '').strip()}" == candidate_ref
            ),
            dict(activations[0]) if activations else {},
        )
        request = {
            "schema": "adaos.builder.acceptance_candidate.v1",
            "development_session_id": development_session_id,
            "project_ref": policy["project_ref"],
            "candidate_ref": candidate_ref,
            "candidate": candidate,
            "subject_refs": copy.deepcopy(list(policy.get("subject_refs") or [])),
            "contract_inputs": copy.deepcopy(list(policy.get("contract_inputs") or [])),
            "instruction_inputs": instruction_identities,
            "instructions": instructions,
        }

        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.agent_context import get_ctx
        from adaos.services.skill.manager import SkillManager

        ctx = get_ctx()
        manager = SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )
        receipts: list[dict[str, Any]] = []
        failures: list[str] = []
        for requirement in requirements:
            requirement_id = str(requirement["id"])
            profile = str(requirement["profile"])
            provider_ref = str(requirement["provider_ref"])
            provider = admitted_context.get(provider_ref)
            if not provider or str(provider.get("relation") or "") != "contract-consumer":
                failures.append(
                    f"{requirement_id}: provider is not an admitted contract-consumer"
                )
                continue
            provider_id = provider_ref.partition(":")[2]
            operation = str(requirement["operation"])
            required = bool(requirement["required"])
            try:
                parameters = requirement.get("parameters") or {}
                if not isinstance(parameters, Mapping):
                    raise ValueError("acceptance requirement parameters must be an object")
                protected = set(request) | {"profile"}
                collisions = sorted(protected.intersection(str(key) for key in parameters))
                if collisions:
                    raise ValueError(
                        "acceptance requirement parameters cannot override the "
                        "digest-bound candidate envelope: " + ", ".join(collisions)
                    )
                raw = manager.run_tool(
                    provider_id,
                    operation,
                    {
                        "request": {
                            **request,
                            "profile": profile,
                            **{str(key): value for key, value in parameters.items()},
                        }
                    },
                    timeout=float(requirement.get("timeout_seconds") or 300),
                )
                if not isinstance(raw, Mapping):
                    raise ValueError("acceptance provider returned a non-object receipt")
                value = dict(raw)
                if value.get("schema") != "adaos.builder.acceptance_receipt.v1":
                    raise ValueError("acceptance provider returned an incompatible receipt schema")
                if str(value.get("profile") or "") != profile:
                    raise ValueError("acceptance receipt profile differs from the requirement")
                provider_ok = bool(value.get("ok"))
                receipt_identity = {
                    **value,
                    "requirement_id": requirement_id,
                    "provider_ref": provider_ref,
                    "operation": operation,
                    "required": required,
                    "candidate_ref": candidate_ref,
                    "development_session_id": development_session_id,
                }
                receipt = {
                    **receipt_identity,
                    "digest": _canonical_digest(receipt_identity),
                }
                receipts.append(receipt)
                if required and not provider_ok:
                    details = "; ".join(str(item) for item in value.get("errors") or [])
                    failures.append(f"{requirement_id}: {details or 'consumer acceptance failed'}")
            except Exception as exc:
                receipt_identity = {
                    "schema": "adaos.builder.acceptance_receipt.v1",
                    "profile": profile,
                    "ok": False,
                    "checks": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "requirement_id": requirement_id,
                    "provider_ref": provider_ref,
                    "operation": operation,
                    "required": required,
                    "candidate_ref": candidate_ref,
                    "development_session_id": development_session_id,
                }
                receipts.append(
                    {**receipt_identity, "digest": _canonical_digest(receipt_identity)}
                )
                if required:
                    failures.append(f"{requirement_id}: {type(exc).__name__}: {exc}")
        identity = {
            "schema": "adaos.builder.acceptance_summary.v1",
            "ok": not failures,
            "profiles": profiles,
            "requirements": requirements,
            "receipts": receipts,
            "errors": failures,
        }
        return {**identity, "digest": _canonical_digest(identity)}

    def _publish_lifecycle_notification(
        self,
        session: Mapping[str, Any],
        *,
        status: str,
        message: str,
    ) -> bool:
        from adaos.services.agent_context import get_ctx

        current = dict(session)
        ctx = get_ctx()
        task_id = str(current.get("current_task_id") or "").strip()
        conversation_id = str(current.get("conversation_id") or "").strip()
        object_type = str(current.get("object_type") or "").strip()
        object_id = str(current.get("object_id") or "").strip()
        webspace_id = str(current.get("webspace_id") or "desktop").strip() or "desktop"
        thread_id = str(current.get("topic_id") or "").strip() or (
            f"prompt-project:scenario:{object_id}" if object_type == "scenario" else None
        )
        meta = {
            "schema": "adaos.builder.automation_notification.v1",
            "automation_session_id": current.get("session_id"),
            "task_id": task_id or None,
            "automation_status": status,
            "object_type": object_type or None,
            "object_id": object_id or None,
            "notification_scope": "subnet",
            "response_idempotency_key": f"builder-automation:{status}:{task_id}",
        }
        delivered = False
        if conversation_id:
            try:
                from adaos.services.conversation_response import materialize_response

                materialize_response(
                    {"message": message, "render_targets": ["text_tail"]},
                    webspace_id=webspace_id,
                    conversation_id=conversation_id,
                    channel_id="builder",
                    owner="skill:builder_skill",
                    bus=ctx.bus,
                    route_id="voice_chat",
                    actor_id="agent:builder_skill:builder",
                    actor_label="Builder",
                    thread_id=thread_id,
                    meta=meta,
                    source="builder.automation",
                )
                delivered = True
            except Exception:
                _log.debug(
                    "failed to materialize Builder lifecycle message task=%s status=%s",
                    task_id,
                    status,
                    exc_info=True,
                )
        try:
            from adaos.services.eventbus import emit

            emit(
                ctx.bus,
                "ui.notify",
                {
                    "text": message,
                    "_meta": {
                        **meta,
                        "skip_voice_chat": True,
                    },
                },
                source="builder.automation",
                schema="adaos.builder.automation_notification.v1",
                version=1,
                generate_event_id=True,
            )
            delivered = True
        except Exception:
            _log.debug(
                "failed to broadcast Builder lifecycle message task=%s status=%s",
                task_id,
                status,
                exc_info=True,
            )
        return delivered

    def _notify_started_session(self, session: Mapping[str, Any]) -> dict[str, Any]:
        """Publish one conversational and subnet-wide start message per task."""
        current = dict(session)
        task_id = str(current.get("current_task_id") or "").strip()
        if not task_id or str(current.get("started_notified_task_id") or "").strip() == task_id:
            return current
        object_id = str(current.get("object_id") or "").strip()
        iteration = int(current.get("iteration") or 0)
        iteration_suffix = f" Итерация {iteration}." if iteration else ""
        message = (
            f"Builder начал доработку {object_id} с помощью локального Codex."
            f"{iteration_suffix}"
        )
        try:
            delivered = self._publish_lifecycle_notification(
                current,
                status="started",
                message=message,
            )
        except Exception:
            delivered = False
        if delivered:
            current["started_notified_task_id"] = task_id
            current["started_notified_at"] = _now_iso()
            self._save_session(current, emit_projection=False)
        return current

    def _notify_completed_session(self, session: Mapping[str, Any]) -> dict[str, Any]:
        """Publish one conversational and subnet-wide terminal message per task."""
        current = self._sync_linked_development_ticket_tasks(dict(session))
        task_id = str(current.get("current_task_id") or "").strip()
        if task_id and str(current.get("completion_notified_task_id") or "").strip() == task_id:
            return current

        try:
            result = current.get("last_result") if isinstance(current.get("last_result"), Mapping) else {}
            object_id = str(current.get("object_id") or "").strip()
            summary = str(result.get("summary") or "").strip()
            message = (
                f"Builder завершил доработку {object_id} с помощью локального Codex. "
                "Проверки пройдены, результат готов к пользовательской апробации."
            )
            if summary:
                message += f" {summary}"
            delivered = self._publish_lifecycle_notification(
                current,
                status="completed",
                message=message,
            )
            if delivered:
                current["completion_notified_task_id"] = task_id or None
                current["completion_notified_at"] = _now_iso()
                self._save_session(current, emit_projection=False)
        except Exception:
            _log.debug("failed to publish Builder completion task=%s", task_id, exc_info=True)
        return current

    def _sync_linked_development_ticket_tasks(
        self,
        session: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = dict(session)
        links = current.get("links") if isinstance(current.get("links"), Mapping) else {}
        ticket_ids = list(
            dict.fromkeys(
                [
                    str(links.get("development_ticket_id") or "").strip(),
                    *[
                        str(item).strip()
                        for item in links.get("development_ticket_ids") or []
                        if str(item).strip()
                    ],
                ]
            )
        )
        ticket_ids = [item for item in ticket_ids if item]
        repair_id = str(links.get("builder_repair_id") or "").strip()
        task_id = str(current.get("current_task_id") or "").strip()
        if not ticket_ids or not repair_id or not task_id:
            return current
        legacy_synced_task_ids = {
            str(item).strip()
            for item in current.get("development_ticket_synced_task_ids") or []
            if str(item).strip()
        }
        synced_refs = {
            str(item).strip()
            for item in current.get("development_ticket_synced_refs") or []
            if str(item).strip()
        }
        if (
            not synced_refs
            and len(ticket_ids) == 1
            and current.get("development_ticket_sync_schema")
            == "adaos.builder.dev_ticket_task_sync.v3"
            and current.get("development_ticket_sync_revision") == 2
        ):
            synced_refs = {
                f"{ticket_ids[0]}:{linked_task_id}"
                for linked_task_id in legacy_synced_task_ids
            }
        historical_task_ids = {
            str(item).strip()
            for item in current.get("task_history") or []
            if str(item).strip()
        }
        expected_task_ids = historical_task_ids | {task_id}
        expected_refs = {
            f"{ticket_id}:{linked_task_id}"
            for ticket_id in ticket_ids
            for linked_task_id in expected_task_ids
        }
        if expected_refs and expected_refs.issubset(synced_refs):
            return current
        try:
            from adaos.services.builder.repair import BuilderRepairService
            from adaos.services.development_tickets import DevelopmentTicketService

            ticket_service = DevelopmentTicketService(state_dir=self.state_dir)
            repair_service = BuilderRepairService(state_dir=self.state_dir)
            ordered_task_ids = list(
                dict.fromkeys(
                    [
                        str(item).strip()
                        for item in current.get("task_history") or []
                        if str(item).strip()
                    ]
                    + [task_id]
                )
            )
            latest_by_ticket: dict[str, Mapping[str, Any]] = {}
            for linked_task_id in ordered_task_ids:
                task_session = self._session_for_linked_task(current, linked_task_id)
                if task_session is None:
                    continue
                task_receipt = task_session.get("codex_usage_accounting")
                if isinstance(task_receipt, Mapping):
                    self._retain_codex_usage_receipt(current, task_receipt)
                status_result = {
                    "ok": True,
                    "session": task_session,
                    "automation": self.project_session(task_session),
                }
                for ticket_id in ticket_ids:
                    ref = f"{ticket_id}:{linked_task_id}"
                    if ref in synced_refs:
                        continue
                    latest_sync = ticket_service.sync_builder_repair(
                        ticket_id,
                        actor="builder.automation",
                        repair_id=repair_id,
                        repair_service=repair_service,
                        automation_result=status_result,
                    )
                    latest_by_ticket[ticket_id] = latest_sync
                    if bool(latest_sync.get("synchronized")):
                        synced_refs.add(ref)
            if synced_refs:
                now = _now_iso()
                current["development_ticket_synced_task_id"] = task_id
                current["development_ticket_synced_task_ids"] = [
                    item
                    for item in ordered_task_ids
                    if all(f"{ticket_id}:{item}" in synced_refs for ticket_id in ticket_ids)
                ]
                current["development_ticket_synced_refs"] = sorted(synced_refs)
                current["development_ticket_sync_schema"] = (
                    "adaos.builder.dev_ticket_task_sync.v4"
                )
                current["development_ticket_sync_revision"] = 3
                current["development_ticket_synced_at"] = now
                ticket_results: list[dict[str, Any]] = []
                for ticket_id in ticket_ids:
                    latest_sync = latest_by_ticket.get(ticket_id) or {}
                    synced_ticket = (
                        latest_sync.get("ticket")
                        if isinstance(latest_sync.get("ticket"), Mapping)
                        else {}
                    )
                    ticket_results.append(
                        {
                            "ticket_id": ticket_id,
                            "status": str(synced_ticket.get("status") or "").strip() or None,
                            "resolved": bool(
                                latest_sync.get("resolved")
                                or str(synced_ticket.get("status") or "").strip()
                                in {"resolved", "verified", "closed"}
                            ),
                        }
                    )
                current["development_ticket_sync"] = {
                    "ticket_id": ticket_ids[0],
                    "ticket_ids": ticket_ids,
                    "repair_id": repair_id,
                    "resolved": bool(ticket_results)
                    and all(item["resolved"] for item in ticket_results),
                    "task_count": len(ordered_task_ids),
                    "ticket_count": len(ticket_ids),
                    "tickets": ticket_results,
                    "updated_at": now,
                }
                current["updated_at"] = now
                self._save_session(current)
        except Exception:
            _log.exception(
                "completed Builder automation failed to synchronize Dev Ticket "
                "tickets=%s repair=%s task=%s",
                ticket_ids,
                repair_id,
                task_id,
            )
        return current

    def _session_for_linked_task(
        self,
        session: Mapping[str, Any],
        task_id: str,
    ) -> dict[str, Any] | None:
        current_task_id = str(session.get("current_task_id") or "").strip()
        if task_id == current_task_id:
            return copy.deepcopy(dict(session))
        try:
            task = self.factory.read_task(task_id)
        except KeyError:
            return None
        snapshot = copy.deepcopy(dict(session))
        snapshot["current_task_id"] = task_id
        snapshot["task"] = task
        snapshot["status"] = str(task.get("status") or "").strip() or "failed"
        snapshot["updated_at"] = task.get("updated_at") or snapshot.get("updated_at")
        run_dir = Path(self.runs_root) / _safe_token(task_id)
        snapshot["local_run"] = {
            "path": str(run_dir),
            "events_path": str(run_dir / "output" / "codex-live.jsonl"),
            "stderr_path": str(run_dir / "output" / "codex-live.stderr.log"),
            "result_path": str(run_dir / "output" / "result.json"),
        }
        snapshot.pop("completion_readiness", None)
        snapshot.pop("codex_usage_accounting", None)
        if isinstance(task.get("result"), Mapping):
            snapshot["last_result"] = copy.deepcopy(task["result"])
            snapshot.pop("last_failure", None)
        else:
            snapshot.pop("last_result", None)
            failures = [
                dict(item)
                for item in task.get("failure_history") or []
                if isinstance(item, Mapping)
            ]
            if failures:
                snapshot["last_failure"] = failures[-1]
        historical_receipt = next(
            (
                dict(item)
                for item in reversed(snapshot.get("codex_usage_history") or [])
                if isinstance(item, Mapping)
                and str(item.get("task_id") or "").strip() == task_id
            ),
            None,
        )
        if historical_receipt:
            snapshot["codex_usage_accounting"] = historical_receipt
        snapshot = self._report_terminal_codex_usage(
            snapshot,
            task_status=str(task.get("status") or ""),
        )
        return snapshot

    def _on_worker_progress(self, session_id: str, task_id: str, status: str, message: str) -> None:
        with _LOCK:
            session = self._find_session_by_id(session_id)
            if not session or str(session.get("current_task_id") or "") != str(task_id or ""):
                return
            session["status"] = str(status or session.get("status") or "in_progress")
            session["progress"] = {
                "task_id": str(task_id or ""),
                "status": session["status"],
                "message": str(message or ""),
                "updated_at": _now_iso(),
            }
            session["updated_at"] = session["progress"]["updated_at"]
            self._save_session(session)

    def _find_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        token = str(session_id or "").strip()
        if not token:
            return None
        for path in self.root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, Mapping) and str(raw.get("session_id") or "") == token:
                return dict(raw)
        return None

    def _project_ref(self, object_type: str, object_id: str) -> tuple[str, str]:
        kind = str(object_type or "").strip().lower()
        if kind not in {"skill", "scenario"}:
            raise ValueError("object_type must be skill or scenario")
        project_id = _safe_token(object_id, fallback="")
        if not project_id:
            raise ValueError("object_id is required")
        return kind, project_id

    def _project_version(self, object_type: str, object_id: str) -> str | None:
        parent = self.dev_scenarios_root if object_type == "scenario" else self.dev_skills_root
        manifest_name = "scenario.yaml" if object_type == "scenario" else "skill.yaml"
        path = parent / object_id / manifest_name
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            return None
        if not isinstance(payload, Mapping):
            return None
        return str(payload.get("version") or "").strip() or None

    def _project_prototype_ref(self, object_type: str, object_id: str) -> str | None:
        try:
            revision = self._workflow().current_prototype_revision(object_type, object_id)
        except Exception:
            revision = None
        if revision and object_type == "scenario" and str(revision).isdigit():
            return f"UI {int(str(revision)):03d}"
        return str(revision or self._project_version(object_type, object_id) or "").strip() or None

    def _session_path(self, object_type: str, object_id: str) -> Path:
        return self.root / f"{_safe_token(object_type)}.{_safe_token(object_id)}.json"

    def _compact_status_path(self, object_type: str, object_id: str) -> Path:
        return self.root / (
            f"{_safe_token(object_type)}.{_safe_token(object_id)}.summary.json"
        )

    def _read_compact_status(
        self,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        try:
            kind, project_id = self._project_ref(object_type, object_id)
        except ValueError:
            return None
        try:
            payload = json.loads(
                self._compact_status_path(kind, project_id).read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return dict(payload) if isinstance(payload, Mapping) else None

    def _compact_status_payload(self, session: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "session": self.compact_session(session),
            "automation": self.project_session(session),
            "detail_available": True,
        }

    def _save_session(
        self,
        session: Mapping[str, Any],
        *,
        emit_projection: bool = True,
    ) -> dict[str, Any]:
        payload = dict(session)
        path = self._session_path(str(payload["object_type"]), str(payload["object_id"]))
        compact_path = self._compact_status_path(
            str(payload["object_type"]),
            str(payload["object_id"]),
        )
        lock_path = self.root / ".mutation.lock"
        with mutation_lock(lock_path, timeout_s=30.0):
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                previous = None
            if isinstance(previous, Mapping) and _prefer_persisted_session(previous, payload):
                persisted = dict(previous)
                _write_json(compact_path, self._compact_status_payload(persisted))
                if isinstance(session, dict):
                    session.clear()
                    session.update(copy.deepcopy(persisted))
                return persisted
            if previous == payload:
                _write_json(compact_path, self._compact_status_payload(payload))
                return payload
            _write_json(path, payload)
            _write_json(compact_path, self._compact_status_payload(payload))
        if emit_projection and self.event_sink is not None:
            self.event_sink(self.project_session(payload))
        return payload


__all__ = [
    "AUTOMATION_PROJECTION_SCHEMA",
    "AUTOMATION_SESSION_SCHEMA",
    "BuilderAutomationService",
    "STANDARD_PROMPT_VERSION",
]
