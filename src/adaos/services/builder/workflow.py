from __future__ import annotations

import copy
import json
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.runtime_paths import current_state_dir


BUILDER_WORKFLOW_SCHEMA = "adaos.builder.workflow.v1"
BUILDER_WORKFLOW_EVENT = "builder.workflow.changed"
_LOCK = threading.RLock()
_MAX_STATE_BYTES = 512 * 1024
_MAX_HISTORY = 50


class BuilderWorkflowError(ValueError):
    """Raised when a Builder lifecycle transition is not permitted."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _legacy_phase(value: Any) -> str:
    token = str(value or "").strip().lower()
    return "automation" if token in {"automation", "publication"} else "prototype"


@dataclass(slots=True)
class BuilderWorkflowService:
    dev_skills_root: Path
    dev_scenarios_root: Path
    state_dir: Path | None = None
    event_sink: Any = None

    def __post_init__(self) -> None:
        self.dev_skills_root = Path(self.dev_skills_root)
        self.dev_scenarios_root = Path(self.dev_scenarios_root)
        self.state_dir = Path(self.state_dir or current_state_dir())

    @classmethod
    def from_context(cls) -> "BuilderWorkflowService":
        ctx = get_ctx()
        return cls(
            dev_skills_root=Path(ctx.paths.dev_skills_dir()),
            dev_scenarios_root=Path(ctx.paths.dev_scenarios_dir()),
            state_dir=current_state_dir(),
            event_sink=cls._publish,
        )

    @staticmethod
    def _publish(projection: Mapping[str, Any]) -> None:
        try:
            from adaos.services.eventbus import emit

            emit(get_ctx().bus, BUILDER_WORKFLOW_EVENT, dict(projection), source="builder.workflow")
        except Exception:
            return

    def project_root(self, object_type: str, object_id: str) -> Path:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        root = (self.dev_scenarios_root if kind == "scenario" else self.dev_skills_root) / project_id
        if not root.is_dir():
            raise FileNotFoundError(f"DEV {kind} project not found: {project_id}")
        return root

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
        temporary.replace(path)

    def _project_version(self, object_type: str, object_id: str) -> str | None:
        kind = _kind(object_type)
        root = self.project_root(kind, object_id)
        path = root / ("scenario.yaml" if kind == "scenario" else "skill.yaml")
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            return None
        return str(value.get("version") or "").strip() or None if isinstance(value, Mapping) else None

    def current_prototype_revision(self, object_type: str, object_id: str) -> str | None:
        kind = _kind(object_type)
        if kind != "scenario":
            return self._project_version(kind, object_id)
        path = self.project_root(kind, object_id) / "ui_revisions" / "current.txt"
        try:
            return str(path.read_text(encoding="utf-8-sig")).strip() or self._project_version(kind, object_id)
        except OSError:
            return self._project_version(kind, object_id)

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
        current_revision = self.current_prototype_revision(object_type, object_id)
        prototype.setdefault("head_revision", current_revision)
        if current_revision and active_phase == "prototype":
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

        if "status" not in publication:
            publication["status"] = "published" if legacy_state == "publication" else "not_started"
        publication.setdefault("current_version", None)
        publication.setdefault("published_at", None)

        return {
            "schema": BUILDER_WORKFLOW_SCHEMA,
            "generation": max(0, int(raw.get("generation") or 0)),
            "active_phase": active_phase,
            "prototype": prototype,
            "automation": automation,
            "publication": publication,
            "pending_transition": _mapping(raw.get("pending_transition")) or None,
            "history": [
                dict(item)
                for item in raw.get("history") or []
                if isinstance(item, Mapping)
            ][-_MAX_HISTORY:],
            "updated_at": str(raw.get("updated_at") or state.get("updated_at") or "").strip() or None,
        }

    @staticmethod
    def _capabilities(workflow: Mapping[str, Any], *, archived: bool, object_type: str) -> dict[str, bool]:
        active = str(workflow.get("active_phase") or "prototype")
        automation = _mapping(workflow.get("automation"))
        automation_status = str(automation.get("status") or "not_started")
        mutable = not archived
        return {
            "can_edit_prototype": mutable and active == "prototype",
            "can_stabilize_prototype": mutable and active == "prototype",
            "can_handoff_to_automation": mutable and active == "prototype",
            "can_edit_automation": mutable and active == "automation" and automation_status != "adapting",
            "can_return_to_prototype": mutable and active == "automation" and automation_status == "completed",
            "can_publish": mutable and active == "automation" and automation_status == "completed",
            "can_preview_prototype": object_type == "scenario",
            "can_preview_automation": object_type == "scenario" and automation_status == "completed",
            "can_preview_publication": object_type == "scenario"
            and str(_mapping(workflow.get("publication")).get("status") or "") == "published",
        }

    def describe(self, object_type: str, object_id: str) -> dict[str, Any]:
        kind = _kind(object_type)
        project_id = _project_id(object_id)
        with _LOCK:
            state = self._read_state(kind, project_id)
            workflow = self._normalized_workflow(state, object_type=kind, object_id=project_id)
        return {
            **copy.deepcopy(workflow),
            "object_type": kind,
            "object_id": project_id,
            "archived": bool(state.get("archived")),
            "capabilities": self._capabilities(workflow, archived=bool(state.get("archived")), object_type=kind),
        }

    def transition(
        self,
        object_type: str,
        object_id: str,
        action: str,
        *,
        actor: str = "builder",
        reason: str | None = None,
        metadata: Mapping[str, Any] | None = None,
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
            before = {
                "active_phase": workflow["active_phase"],
                "prototype_status": workflow["prototype"].get("status"),
                "automation_status": workflow["automation"].get("status"),
                "publication_status": workflow["publication"].get("status"),
            }
            self._apply_transition(workflow, action_token, details, changed_at=changed_at)
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = changed_at
            after = {
                "active_phase": workflow["active_phase"],
                "prototype_status": workflow["prototype"].get("status"),
                "automation_status": workflow["automation"].get("status"),
                "publication_status": workflow["publication"].get("status"),
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
                }
            )
            workflow["history"] = history[-_MAX_HISTORY:]
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
        if callable(self.event_sink):
            self.event_sink(projection)
        return {"ok": True, "action": action_token, "workflow": projection}

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
    ) -> None:
        prototype = workflow["prototype"]
        automation = workflow["automation"]
        publication = workflow["publication"]
        if action == "stabilize_prototype":
            self._require_active(workflow, "prototype", action)
            prototype.update({"status": "working", "stable": True, "stabilized_at": changed_at})
            prototype["head_revision"] = metadata.get("revision") or prototype.get("head_revision")
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
            workflow["pending_transition"] = None
            return
        if action == "automation_iteration_started":
            self._require_active(workflow, "automation", action)
            status = str(automation.get("status") or "")
            next_task_id = str(metadata.get("task_id") or "").strip()
            previous_task_id = str(automation.get("head_task_id") or "").strip()
            reconciles_stale_working_state = bool(
                status == "working" and next_task_id and next_task_id != previous_task_id
            )
            if status not in {"completed", "failed"} and not reconciles_stale_working_state:
                raise BuilderWorkflowError(
                    "a new Automation iteration requires a completed or failed Automation result"
                )
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
            workflow["pending_transition"] = None
            return
        if action == "automation_completed":
            self._require_active(workflow, "automation", action)
            automation.update(
                {
                    "status": "completed",
                    "head_task_id": metadata.get("task_id") or automation.get("head_task_id"),
                    "result_version": metadata.get("version") or automation.get("result_version"),
                    "snapshot_path": metadata.get("snapshot_path") or automation.get("snapshot_path"),
                    "completed_at": changed_at,
                    "error": None,
                }
            )
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
            return
        if action == "request_return_to_prototype":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("return to prototype requires completed automation")
            automation["status"] = "adapting"
            workflow["pending_transition"] = {
                "action": "return_to_prototype",
                "requested_at": changed_at,
                "task_id": metadata.get("task_id"),
            }
            return
        if action == "return_to_prototype":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") not in {"adapting", "completed"}:
                raise BuilderWorkflowError("return to prototype requires completed adaptation")
            automation.update({"status": "frozen", "frozen_at": changed_at})
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
            workflow["pending_transition"] = None
            return
        if action == "publish":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("publication requires completed automation")
            version = str(metadata.get("version") or "").strip()
            if not version:
                raise BuilderWorkflowError("publication version is required")
            publication.update(
                {
                    "status": "published",
                    "current_version": version,
                    "published_at": changed_at,
                    "source_automation_task": metadata.get("task_id") or automation.get("head_task_id"),
                    "release": metadata.get("release"),
                }
            )
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
                snapshot_root.replace(previous)
            temporary.replace(snapshot_root)
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
    "BUILDER_WORKFLOW_EVENT",
    "BUILDER_WORKFLOW_SCHEMA",
    "BuilderWorkflowError",
    "BuilderWorkflowService",
]
