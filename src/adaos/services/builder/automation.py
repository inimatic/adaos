from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.runtime_paths import current_repo_root, current_state_dir
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_sources import capture_source_snapshot
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


AUTOMATION_SESSION_SCHEMA = "adaos.builder.automation_session.v1"
STANDARD_PROMPT_VERSION = "adaos-skill-realization/0.1.0"
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any, *, fallback: str = "project") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or fallback


def _reject_transport_corruption(value: Any, *, field: str) -> None:
    """Reject new durable Automation text after Unicode code points were lost."""

    token = str(value or "")
    if "\ufffd" in token or "????" in token:
        raise ValueError(
            f"{field} appears transport-corrupted; submit the original text as UTF-8"
        )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(path, payload)


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
    if terminal_readiness and not (
        incoming_status == "completed"
        and incoming_readiness.get("ok")
        and str(incoming_readiness.get("task_id") or "").strip() == incoming_task
    ):
        return True

    previous_rank = _STATUS_RANK.get(previous_status, -1)
    incoming_rank = _STATUS_RANK.get(incoming_status, -1)
    if previous_rank > incoming_rank:
        task = incoming.get("task") if isinstance(incoming.get("task"), Mapping) else {}
        explicit_finalization = bool(
            incoming_status == "commit_ready"
            and str(incoming.get("finalizing_task_id") or "").strip() == incoming_task
            and str(task.get("status") or "").strip() == "completed"
            and isinstance(incoming.get("last_result"), Mapping)
            and not terminal_readiness
        )
        if not explicit_finalization:
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
            "agent_profile": copy.deepcopy(session["handoff"].get("agent_profile")),
            "artifact_inputs": artifact_receipts,
            "instruction_inputs": instruction_receipts,
            "prohibited_actions": list(session["handoff"]["prohibited_actions"]),
        }
        return {**identity, "digest": _canonical_digest(identity)}, attachments

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
    ) -> dict[str, Any]:
        kind, project_id = self._project_ref(object_type, object_id)
        brief = str(implementation_brief or "").strip()
        if not brief:
            raise ValueError("implementation_brief is required after Prompt IDE Execute")
        _reject_transport_corruption(brief, field="implementation_brief")
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
            planned = self._workflow().transition(
                kind,
                project_id,
                "plan_change_set",
                actor="builder.automation.compat",
                reason="direct Automation entry projected into canonical Change",
                metadata={
                    "change_set_id": f"CH-automation-{request_digest}",
                    "run_id": f"RUN-plan-{request_digest}",
                    "request": brief,
                    "issues": [
                        {
                            "issue_id": f"automation-{request_digest}",
                            "title": " ".join(brief.split())[:240],
                            "lane": "automation",
                            "status": "open",
                            "acceptance_criteria": [
                                f"The implementation and its tests satisfy: {' '.join(brief.split())}"[:500]
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
            if str(workflow_before.get("active_phase") or "prototype") != "prototype":
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
        self._launch_worker(session["session_id"])
        return {
            "ok": True,
            "duplicate": False,
            "session": session,
            "task": submitted["task"],
            "automation": self.project_session(session),
        }

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

    def submit_turn(
        self,
        *,
        text: str,
        object_type: str | None = None,
        object_id: str | None = None,
        webspace_id: str | None = None,
        conversation_id: str | None = None,
        workflow_transition: str | None = None,
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
            transition_token = str(workflow_transition or "").strip() or None
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
            ):
                session.pop(stale_key, None)
            self._refresh_session_companion_skill_ids(session)
            self._capture_preview_binding(session)
            submitted = self._submit(session, iteration_instruction=instruction)
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
                    "automation_iteration_started",
                    actor="builder.automation",
                    reason="a new Automation iteration was queued",
                    metadata={
                        "confirmed": True,
                        "task_id": session.get("current_task_id"),
                        "change_id": session.get("change_id"),
                        "run_id": session.get("change_id"),
                        "context_packet_digest": session.get("context_packet_digest"),
                    },
                )
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
                and not validated_activation_pending
            ):
                raise ValueError("validated result recovery requires a failed Automation task")
            failure_stage = str(failure.get("stage") or "")
            if (
                recovered_transition_pending
                or workflow_checkpoint_pending
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
                        else "validated_activation"
                        if validated_activation_pending
                        else "live_readiness"
                    ),
                }
                if not recovered_transition_pending and not validated_activation_pending:
                    current["reuse_confirmed_checkpoints"] = True
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

    def status(self, *, object_type: str, object_id: str) -> dict[str, Any]:
        session = self.get_session(object_type, object_id)
        if not session:
            return {"ok": False, "error": "automation_session_not_found"}
        current = self.refresh_session(session)
        if current.get("status") == "completed":
            current = self._notify_completed_session(current)
        return {"ok": True, "session": current, "automation": self.project_session(current)}

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
        if current.get("status") == "completed":
            current = self._notify_completed_session(current)
        return {"ok": True, "session": current, "automation": self.project_session(current)}

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
            "error": error,
            "failure_id": str(failure.get("failure_id") or "").strip() or None,
            "failure_stage": str(failure.get("stage") or "").strip() or None,
            "retryable": bool(failure.get("retryable")) if failure else None,
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
        return {
            "declared": declared,
            "observed": {
                "model_tokens": int(usage.get("model_tokens") or 0),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "attempts": int(usage.get("attempts") or 0),
                "wall_seconds": wall_seconds,
                "terminal": status in _TERMINAL_STATUSES,
            },
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
                for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                    try:
                        values[key] = max(values.get(key, 0), int(usage.get(key) or 0))
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
    def _codex_run_usage(local_run: Mapping[str, Any]) -> dict[str, int]:
        run_root = Path(str(local_run.get("path") or "").strip())
        runtime_root = run_root / "runtime"
        paths = sorted(runtime_root.glob("codex-events*.jsonl")) if runtime_root.is_dir() else []
        if not paths:
            event_path = str(local_run.get("events_path") or "").strip()
            paths = [Path(event_path)] if event_path else []
        total: dict[str, int] = {}
        for path in paths:
            usage = BuilderAutomationService._codex_journal_usage(str(path))
            if usage:
                total["attempts"] = int(total.get("attempts") or 0) + 1
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
        snapshot = self.factory.snapshot(include_tasks=True)
        task = next((item for item in snapshot.get("tasks", []) if item.get("task_id") == task_id), None)
        if not task:
            return current
        recovered = self._recover_orphaned_task(current, task)
        if recovered is not None:
            return recovered
        # A failed one-shot reconciliation updates the authoritative factory
        # task before returning.  Refresh the snapshot so this read exposes the
        # failure instead of preserving the stale in-progress projection.
        snapshot = self.factory.snapshot(include_tasks=True)
        task = next((item for item in snapshot.get("tasks", []) if item.get("task_id") == task_id), task)
        task_status = task.get("status")
        current["status"] = (
            "commit_ready"
            if task_status == "completed"
            and str(current.get("finalizing_task_id") or "").strip() == task_id
            else task_status
        )
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
        readiness = current.get("completion_readiness")
        if (
            task_status == "completed"
            and isinstance(readiness, Mapping)
            and str(readiness.get("task_id") or "").strip() == task_id
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
        task_progress = task.get("progress") if isinstance(task.get("progress"), list) else []
        finalizing = str(current.get("finalizing_task_id") or "").strip() == task_id
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
            current["updated_at"] = current["progress"]["updated_at"]
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

            snapshot = self.factory.snapshot(include_tasks=True)
            completed_task = next(
                (item for item in snapshot.get("tasks", []) if item.get("task_id") == task_id),
                None,
            )
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
        acceptance_checks = [
            str(criterion).strip()
            for issue in change_set.get("issues") or []
            if isinstance(issue, Mapping) and issue.get("status") != "deferred"
            for criterion in issue.get("acceptance_criteria") or []
            if str(criterion).strip()
        ]
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
                "development_context": development_context,
                "prototype_handoff": copy.deepcopy(session.get("prototype_handoff")),
            },
            "repo": {
                "sparse_paths": sparse_paths,
                "base_branch": "dev/local",
                "base_revision": source_snapshot["digest"],
                "source_snapshot": source_snapshot,
            },
            "constraints": {
                "no_external_api": True,
                "no_secrets": True,
                "must_add_tests": True,
                "must_update_manifest": True,
                "local_process_debug": True,
            },
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
                "prototype_handoff_digest": str(
                    dict(session.get("prototype_handoff") or {}).get("digest") or ""
                ) or None,
                "development_session_id": development_session_id or None,
                "development_context_digest": str(
                    (development_context or {}).get("digest") or ""
                ) or None,
            },
        }
        execution_budget = (
            development_context.get("execution_budget")
            if isinstance(development_context, Mapping)
            and isinstance(development_context.get("execution_budget"), Mapping)
            else None
        )
        if execution_budget:
            request["timeout_seconds"] = int(execution_budget["max_wall_seconds"])
            request["artifacts"]["execution_budget"] = copy.deepcopy(execution_budget)
        agent_profile = (
            development_context.get("agent_profile")
            if isinstance(development_context, Mapping)
            and isinstance(development_context.get("agent_profile"), Mapping)
            else None
        )
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
        repo_python = self.repo_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable = repo_python if repo_python.is_file() else Path(sys.executable)
        command = [
            str(executable),
            "-m",
            "adaos.services.builder.automation_worker",
            "--session-id",
            str(session_id),
        ]
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
        launched = {
            "schema": "adaos.builder.automation_worker_launch.v1",
            "session_id": str(session_id),
            "pid": int(process.pid),
            "status": "launched",
            "repo_root": str(self.repo_root.resolve()),
            "executable": str(executable.resolve()),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "launched_at": _now_iso(),
        }
        _write_json(launch_path, launched)
        return launched

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
                        and session.get("status") == "completed"
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

    def _finalize_completed_session(self, session: Mapping[str, Any]) -> None:
        """Prepare the DEV runtime, refresh the paired UI, then notify chat."""
        current = dict(session)
        object_type = str(session.get("object_type") or "").strip()
        object_id = str(session.get("object_id") or "").strip()
        webspace_id = str(session.get("webspace_id") or "desktop").strip() or "desktop"
        readiness: dict[str, Any] = {
            "ok": False,
            "task_id": str(session.get("current_task_id") or "").strip() or None,
            "iteration": int(session.get("iteration") or 0),
            "skill": None,
            "materialization": None,
            "vcs_checkpoints": [],
            "completed_at": None,
        }
        failed_checkpoints: list[Mapping[str, Any]] = []
        existing_binding: dict[str, Any] = {}
        preview_target: Mapping[str, Any] | None = None
        try:
            pending_transition = str(current.get("pending_workflow_transition") or "").strip()
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
            companion_skill_ids = self._session_companion_skill_ids(session)
            if companion_skill_ids:
                readiness["skills"] = [
                    self._prepare_and_activate_dev_skill(
                        skill_id,
                        webspace_id=webspace_id,
                    )
                    for skill_id in companion_skill_ids
                ]
                readiness["skill"] = readiness["skills"][0]

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
                    readiness["workflow_checkpoint"] = self._workflow().transition(
                        object_type,
                        object_id,
                        "checkpoint_recorded",
                        actor="builder.automation",
                        reason="Automation result checkpointed in Forge",
                        metadata={
                            # This is a system-authored acknowledgement of the exact
                            # Forge checkpoint identities below, not a human Trial
                            # approval.  The workflow still requires an explicit
                            # accept_trial command before publication can proceed.
                            "confirmed": True,
                            "change_id": change_id,
                            "package_digest": package_digest,
                            "source_revision": source_revision,
                            "version": self._project_version(object_type, object_id),
                            "task_id": current.get("current_task_id"),
                        },
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
            current["last_failure"] = {
                "stage": "forge_checkpoint" if failed_checkpoints else "live_readiness",
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
        prepared = manager.prepare_dev_runtime(skill_id, run_tests=False)
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

    def _notify_completed_session(self, session: Mapping[str, Any]) -> dict[str, Any]:
        """Publish one idempotent terminal Builder message for a local task."""
        current = dict(session)
        task_id = str(current.get("current_task_id") or "").strip()
        if task_id and str(current.get("completion_notified_task_id") or "").strip() == task_id:
            return current

        conversation_id = str(current.get("conversation_id") or "").strip()
        if not conversation_id:
            return current
        try:
            from adaos.services.agent_context import get_ctx
            from adaos.services.conversation_response import materialize_response

            result = current.get("last_result") if isinstance(current.get("last_result"), Mapping) else {}
            object_type = str(current.get("object_type") or "").strip()
            object_id = str(current.get("object_id") or "").strip()
            webspace_id = str(current.get("webspace_id") or "desktop").strip() or "desktop"
            summary = str(result.get("summary") or "").strip()
            message = f"Локальный Codex завершил работу над {object_id}. Проверки пройдены."
            if summary:
                message += f" {summary}"
            materialize_response(
                {"message": message, "render_targets": ["text_tail"]},
                webspace_id=webspace_id,
                conversation_id=conversation_id,
                channel_id="builder",
                owner="skill:builder_skill",
                bus=get_ctx().bus,
                route_id="voice_chat",
                actor_id="agent:builder_skill:builder",
                actor_label="Конструктор",
                thread_id=f"prompt-project:scenario:{object_id}" if object_type == "scenario" else None,
                meta={
                    "automation_session_id": current.get("session_id"),
                    "task_id": task_id or None,
                    "automation_status": "completed",
                },
                source="builder.automation",
            )
            current["completion_notified_task_id"] = task_id or None
            current["completion_notified_at"] = _now_iso()
            self._save_session(current)
        except Exception:
            pass
        return current

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

    def _save_session(self, session: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(session)
        path = self._session_path(str(payload["object_type"]), str(payload["object_id"]))
        lock_path = self.root / ".mutation.lock"
        with mutation_lock(lock_path, timeout_s=30.0):
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                previous = None
            if isinstance(previous, Mapping) and _prefer_persisted_session(previous, payload):
                persisted = dict(previous)
                if isinstance(session, dict):
                    session.clear()
                    session.update(copy.deepcopy(persisted))
                return persisted
            if previous == payload:
                return payload
            _write_json(path, payload)
        if self.event_sink is not None:
            self.event_sink(self.project_session(payload))
        return payload


__all__ = [
    "AUTOMATION_PROJECTION_SCHEMA",
    "AUTOMATION_SESSION_SCHEMA",
    "BuilderAutomationService",
    "STANDARD_PROMPT_VERSION",
]
