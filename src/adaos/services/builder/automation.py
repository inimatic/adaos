from __future__ import annotations

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
_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any, *, fallback: str = "project") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(slots=True)
class BuilderAutomationService:
    state_dir: Path
    repo_root: Path
    dev_skills_root: Path
    dev_scenarios_root: Path
    runs_root: Path | None = None
    worker_factory: Callable[[], LocalSkillFactoryWorker] | None = None
    background: bool = True
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
                return {"ok": True, "duplicate": True, "session": self.refresh_session(current)}
            session = {
                "schema": AUTOMATION_SESSION_SCHEMA,
                "session_id": f"automation.{kind}.{project_id}",
                "object_type": kind,
                "object_id": project_id,
                "companion_skill_id": f"{project_id}_skill" if kind == "scenario" else project_id,
                "webspace_id": str(webspace_id or "desktop"),
                "conversation_id": str(conversation_id or "").strip() or None,
                "implementation_brief": brief,
                "brief_path": str(brief_path or "").strip() or None,
                "standard_prompt_version": STANDARD_PROMPT_VERSION,
                "status": "starting",
                "iteration": 0,
                "turns": [],
                "task_history": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            submitted = self._submit(session, iteration_instruction="")
            session["status"] = "queued"
            session["current_task_id"] = submitted["task"]["task_id"]
            session["task_history"].append(session["current_task_id"])
            self._save_session(session)
        self._launch_worker(session["session_id"])
        return {"ok": True, "duplicate": False, "session": session, "task": submitted["task"]}

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
                }
            session["iteration"] = int(session.get("iteration") or 0) + 1
            session.setdefault("turns", []).append(
                {"iteration": session["iteration"], "text": instruction, "created_at": _now_iso()}
            )
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
        }

    def status(self, *, object_type: str, object_id: str) -> dict[str, Any]:
        session = self.get_session(object_type, object_id)
        if not session:
            return {"ok": False, "error": "automation_session_not_found"}
        return {"ok": True, "session": self.refresh_session(session)}

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
        current["status"] = task.get("status")
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
            )
            worker.run_once()
            with _LOCK:
                session = next((item for item in [self.find_active_session()] if item and item.get("session_id") == session_id), None)
                if session:
                    self.refresh_session(session)

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
        _write_json(self._session_path(str(session["object_type"]), str(session["object_id"])), dict(session))


__all__ = ["AUTOMATION_SESSION_SCHEMA", "BuilderAutomationService", "STANDARD_PROMPT_VERSION"]
