from __future__ import annotations

import copy
import json
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.runtime_paths import current_state_dir


BUILDER_WORKFLOW_SCHEMA = "adaos.builder.workflow.v1"
BUILDER_CHANGE_SET_SCHEMA = "adaos.builder.change_set.v1"
BUILDER_WORKFLOW_EVENT = "builder.workflow.changed"
_LOCK = threading.RLock()
_MAX_STATE_BYTES = 512 * 1024
_MAX_HISTORY = 50
_MAX_CHANGE_ISSUES = 50
_CHANGE_SET_TERMINAL_STATES = {"published", "rejected", "superseded"}
_ISSUE_STATES = {"open", "in_progress", "resolved", "deferred"}
_ISSUE_LANES = {"prototype", "automation"}


class BuilderWorkflowError(ValueError):
    """Raised when a Builder lifecycle transition is not permitted."""


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
    return {
        "issue_id": issue_id,
        "title": title,
        "lane": lane,
        "status": status,
        "acceptance_criteria": criteria,
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
        _replace_path(temporary, path)

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
        change_set = _normalize_change_set(raw.get("change_set"))
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

        return {
            "schema": BUILDER_WORKFLOW_SCHEMA,
            "generation": max(0, int(raw.get("generation") or 0)),
            "active_phase": active_phase,
            "prototype": prototype,
            "automation": automation,
            "delivery": delivery,
            "publication": publication,
            "change_set": change_set,
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
        delivery_status = str(_mapping(workflow.get("delivery")).get("status") or "idle")
        retained_automation = bool(str(automation.get("snapshot_path") or "").strip())
        change_set = _normalize_change_set(workflow.get("change_set"))
        change_set_status = str((change_set or {}).get("status") or "")
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
            and delivery_status not in {"trial", "accepted"},
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
            and (not change_set or change_set_status in _CHANGE_SET_TERMINAL_STATES),
            "can_update_change_set": mutable
            and bool(change_set)
            and change_set_status not in _CHANGE_SET_TERMINAL_STATES,
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
                "delivery_status": workflow["delivery"].get("status"),
                "publication_status": workflow["publication"].get("status"),
                "change_set_status": (workflow.get("change_set") or {}).get("status"),
                "change_set_gate": (workflow.get("change_set") or {}).get("gate"),
            }
            self._apply_transition(workflow, action_token, details, changed_at=changed_at)
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = changed_at
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

        def invalidate_delivery(reason: str) -> None:
            if str(delivery.get("status") or "idle") in {"trial", "accepted"}:
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
            }
            return
        if action == "change_issues_added":
            current = require_change_set(metadata.get("change_set_id"))
            raw_issues = metadata.get("issues")
            if not isinstance(raw_issues, (list, tuple)) or not raw_issues:
                raise BuilderWorkflowError("change set extension requires at least one issue")
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
        if action == "change_evidence_recorded":
            require_change_set(metadata.get("change_set_id"))
            change_id = str(metadata.get("change_id") or "").strip()
            if not change_id:
                raise BuilderWorkflowError("change evidence requires change_id")
            add_change_evidence(change_id)
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
        if action == "candidate_prepared":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("candidate preparation requires completed automation")
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
        if action in {"candidate_accepted", "candidate_rejected"}:
            if str(delivery.get("status") or "") != "trial":
                raise BuilderWorkflowError("candidate decision requires an active trial")
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            if candidate_id != str(delivery.get("candidate_id") or ""):
                raise BuilderWorkflowError("candidate decision does not match the active trial")
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
        if action == "publish":
            self._require_active(workflow, "automation", action)
            if str(automation.get("status") or "") != "completed":
                raise BuilderWorkflowError("publication requires completed automation")
            if str(delivery.get("status") or "") != "accepted":
                raise BuilderWorkflowError("publication requires an accepted candidate trial")
            candidate_id = str(metadata.get("candidate_id") or "").strip()
            if candidate_id != str(delivery.get("candidate_id") or ""):
                raise BuilderWorkflowError("publication candidate does not match the accepted trial")
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
            delivery.update({"status": "published", "published_at": changed_at})
            update_change_set(status="published", gate="complete")
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
    "BUILDER_CHANGE_SET_SCHEMA",
    "BUILDER_WORKFLOW_EVENT",
    "BUILDER_WORKFLOW_SCHEMA",
    "BuilderWorkflowError",
    "BuilderWorkflowService",
]
