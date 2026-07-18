from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.runtime_paths import current_repo_root, current_state_dir
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


AUTOMATION_SESSION_SCHEMA = "adaos.builder.automation_session.v1"
STANDARD_PROMPT_VERSION = "adaos-skill-realization/0.1.0"
AUTOMATION_PROJECTION_SCHEMA = "adaos.builder.automation_projection.v1"
_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any, *, fallback: str = "project") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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

    def start_from_execute(
        self,
        *,
        object_type: str,
        object_id: str,
        implementation_brief: str,
        webspace_id: str = "desktop",
        conversation_id: str | None = None,
        brief_path: str | None = None,
    ) -> dict[str, Any]:
        kind, project_id = self._project_ref(object_type, object_id)
        brief = str(implementation_brief or "").strip()
        if not brief:
            raise ValueError("implementation_brief is required after Prompt IDE Execute")
        with _LOCK:
            current = self.get_session(kind, project_id)
            if current and current.get("status") in {"queued", "assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready"}:
                refreshed = self.refresh_session(current)
                return {
                    "ok": True,
                    "duplicate": True,
                    "session": refreshed,
                    "automation": self.project_session(refreshed),
                }
            created_artifacts = self._ensure_automation_artifacts_created(
                kind=kind,
                project_id=project_id,
                implementation_brief=brief,
            )
            session = {
                "schema": AUTOMATION_SESSION_SCHEMA,
                "session_id": f"automation.{kind}.{project_id}",
                "object_type": kind,
                "object_id": project_id,
                "companion_skill_id": f"{project_id}_skill" if kind == "scenario" else project_id,
                "webspace_id": str(webspace_id or "desktop"),
                "conversation_id": str(conversation_id or "").strip() or None,
                "topic_id": f"prompt-project:{kind}:{project_id}",
                "implementation_brief": brief,
                "brief_path": str(brief_path or "").strip() or None,
                "standard_prompt_version": STANDARD_PROMPT_VERSION,
                "status": "starting",
                "iteration": 0,
                "turns": [],
                "task_history": [],
                "created_artifacts": created_artifacts,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            session["change_id"] = "builder_change_automation_" + hashlib.sha256(
                f"{session['session_id']}:{session['created_at']}".encode("utf-8")
            ).hexdigest()[:16]
            submitted = self._submit(session, iteration_instruction="")
            session["status"] = "queued"
            session["current_task_id"] = submitted["task"]["task_id"]
            session["task_history"].append(session["current_task_id"])
            self._save_session(session)
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
        implementation_brief: str,
    ) -> list[dict[str, Any]]:
        service = self.workspace_service or BuilderWorkspaceService.from_context()
        artifacts = [(kind, project_id)]
        if kind == "scenario":
            artifacts.append(("skill", f"{project_id}_skill"))

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

    def submit_turn(
        self,
        *,
        text: str,
        object_type: str | None = None,
        object_id: str | None = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        instruction = str(text or "").strip()
        if not instruction:
            raise ValueError("automation chat text is required")
        with _LOCK:
            session = (
                self.get_session(str(object_type), str(object_id))
                if object_type and object_id
                else self.find_active_session(webspace_id=webspace_id)
            )
            if not session:
                return {"ok": False, "handled": False, "error": "automation_session_not_found"}
            session = self.refresh_session(session)
            if session.get("status") in {"queued", "assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready"}:
                return {
                    "ok": True,
                    "handled": True,
                    "status": "automation_busy",
                    "message": "Локальный Codex ещё выполняет предыдущую итерацию. Дождитесь завершения и отправьте уточнение повторно.",
                    "session": session,
                    "automation": self.project_session(session),
                }
            session["iteration"] = int(session.get("iteration") or 0) + 1
            session.setdefault("turns", []).append(
                {"iteration": session["iteration"], "text": instruction, "created_at": _now_iso()}
            )
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
            submitted = self._submit(session, iteration_instruction=instruction)
            session["status"] = "queued"
            session["current_task_id"] = submitted["task"]["task_id"]
            session.setdefault("task_history", []).append(session["current_task_id"])
            session["updated_at"] = _now_iso()
            self._save_session(session)
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
        failure = session.get("last_failure") if isinstance(session.get("last_failure"), Mapping) else {}
        progress = session.get("progress") if isinstance(session.get("progress"), Mapping) else {}
        local_run = session.get("local_run") if isinstance(session.get("local_run"), Mapping) else {}
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
            },
            "iteration": int(session.get("iteration") or 0),
            "task_id": str(session.get("current_task_id") or task.get("task_id") or "") or None,
            "steps": BuilderAutomationService._step_projection(status),
            "progress": dict(progress) if progress else None,
            "summary": str(result.get("summary") or result.get("message") or "").strip() or None,
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
        task_status = task.get("status")
        current["status"] = (
            "commit_ready"
            if task_status == "completed"
            and str(current.get("finalizing_task_id") or "").strip() == task_id
            else task_status
        )
        current["task"] = task
        current["updated_at"] = task.get("updated_at") or _now_iso()
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
        if task.get("failure_history"):
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
        task_progress = task.get("progress") if isinstance(task.get("progress"), list) else []
        finalizing = str(current.get("finalizing_task_id") or "").strip() == task_id
        if task_progress and isinstance(task_progress[-1], Mapping) and not finalizing:
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

    def _submit(self, session: Mapping[str, Any], *, iteration_instruction: str) -> dict[str, Any]:
        kind = str(session["object_type"])
        project_id = str(session["object_id"])
        companion = str(session["companion_skill_id"])
        sparse_paths = [f"{kind}s/{project_id}/" if kind == "scenario" else f"skills/{project_id}/"]
        if kind == "scenario":
            sparse_paths.append(f"skills/{companion}/")
        sparse_paths.append(f"docs/requirements/{project_id}/")
        request = {
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
                "iteration_instruction": iteration_instruction,
                "standard_prompt_version": STANDARD_PROMPT_VERSION,
            },
            "repo": {"sparse_paths": sparse_paths, "base_branch": "dev/local"},
            "constraints": {
                "no_external_api": True,
                "no_secrets": True,
                "must_add_tests": True,
                "must_update_manifest": True,
                "local_process_debug": True,
            },
            "acceptance": {
                "checks": [
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
            },
        }
        return self.factory.submit_realize_request(request)

    def _launch_worker(self, session_id: str) -> None:
        if self.background:
            thread = threading.Thread(
                target=self._run_worker,
                args=(session_id,),
                name=f"adaos-codex-{_safe_token(session_id)}",
                daemon=True,
            )
            thread.start()
        else:
            self._run_worker(session_id)

    def _run_worker(self, session_id: str) -> None:
        with _WORKER_LOCK:
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
            worker_result = worker.run_once()
            should_finalize = False
            finalizing_projection: dict[str, Any] | None = None
            with _LOCK:
                session = self._find_session_by_id(session_id)
                if session:
                    session = self.refresh_session(session)
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
        try:
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
            companion_skill_id = str(session.get("companion_skill_id") or "").strip()
            if companion_skill_id:
                readiness["skill"] = self._prepare_and_activate_dev_skill(
                    companion_skill_id,
                    webspace_id=webspace_id,
                )

            if object_type == "scenario" and object_id:
                from adaos.services.builder.workbench import BuilderWorkbenchService
                from adaos.services.builder.workbench import dev_webspace_id_for_source
                from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario

                binding = asyncio.run(
                    BuilderWorkbenchService(state_dir=self.state_dir).ensure_dev_webspace(
                        webspace_id,
                        runtime_scenario_id=object_id,
                        wait_for_rebuild=True,
                    )
                )
                dev_webspace_id = (
                    str(binding.get("dev_webspace_id") or "").strip()
                    or dev_webspace_id_for_source(webspace_id)
                )
                readiness["materialization"] = asyncio.run(
                    reload_webspace_from_scenario(
                        dev_webspace_id,
                        scenario_id=object_id,
                        action="reload",
                        event_payload={
                            "source": "builder.automation",
                            "_meta": {"cmd_id": f"automation:{session.get('current_task_id') or object_id}"},
                        },
                    )
                )
                if not bool(readiness["materialization"].get("ok", True)):
                    raise RuntimeError(
                        str(readiness["materialization"].get("error") or "dev webspace reload failed")
                    )

            readiness["ok"] = True
            readiness["completed_at"] = _now_iso()
            current["completion_readiness"] = readiness
            current["status"] = "completed"
            current.pop("finalizing_task_id", None)
            current.pop("last_failure", None)
            current["updated_at"] = readiness["completed_at"]
            self._save_session(current)
        except Exception as exc:
            readiness["error"] = f"{type(exc).__name__}: {exc}"
            readiness["completed_at"] = _now_iso()
            current["completion_readiness"] = readiness
            current["status"] = "failed"
            current.pop("finalizing_task_id", None)
            current["last_failure"] = {
                "stage": "live_readiness",
                "message": readiness["error"],
                "updated_at": readiness["completed_at"],
            }
            current["updated_at"] = readiness["completed_at"]
            self._save_session(current)
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
        companion_skill_id = str(session.get("companion_skill_id") or "").strip()
        artifacts: list[tuple[str, str]] = []
        if companion_skill_id:
            artifacts.append(("skill", companion_skill_id))
        if object_type in {"skill", "scenario"} and object_id and (object_type, object_id) not in artifacts:
            artifacts.append((object_type, object_id))

        service = BuilderWorkspaceService.from_context()
        checkpoints: list[dict[str, Any]] = []
        change_id = str(session.get("change_id") or "").strip()
        conversation_id = str(session.get("conversation_id") or "").strip()
        topic_id = str(session.get("topic_id") or "").strip()
        metadata = {
            "change_id": change_id,
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
                    meta={"automation_session_id": session.get("session_id")},
                )
            except Exception:
                pass
        return checkpoints

    def _prepare_and_activate_dev_skill(self, skill_id: str, *, webspace_id: str) -> dict[str, Any]:
        """Run package-external DEV lifecycle steps owned by the orchestrator."""
        from adaos.adapters.db import SqliteSkillRegistry
        from adaos.services.agent_context import get_ctx
        from adaos.services.builder.workbench import dev_webspace_id_for_source
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
        slot = manager.activate_for_space(
            skill_id,
            version=prepared.version,
            slot=prepared.slot,
            space="dev",
            webspace_id=dev_webspace_id_for_source(webspace_id),
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

    def _session_path(self, object_type: str, object_id: str) -> Path:
        return self.root / f"{_safe_token(object_type)}.{_safe_token(object_id)}.json"

    def _save_session(self, session: Mapping[str, Any]) -> None:
        payload = dict(session)
        path = self._session_path(str(payload["object_type"]), str(payload["object_id"]))
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            previous = None
        if previous == payload:
            return
        _write_json(path, payload)
        if self.event_sink is not None:
            self.event_sink(self.project_session(payload))


__all__ = [
    "AUTOMATION_PROJECTION_SCHEMA",
    "AUTOMATION_SESSION_SCHEMA",
    "BuilderAutomationService",
    "STANDARD_PROMPT_VERSION",
]
