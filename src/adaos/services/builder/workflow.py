from __future__ import annotations

import copy
import hashlib
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
from adaos.services.builder.action_contracts import build_builder_action
from adaos.services.runtime_paths import current_state_dir


BUILDER_WORKFLOW_SCHEMA = "adaos.builder.workflow.v1"
BUILDER_CHANGE_SET_SCHEMA = "adaos.builder.change_set.v1"
BUILDER_CHANGE_SCHEMA = "adaos.builder.change.v1"
BUILDER_RUN_SCHEMA = "adaos.builder.run.v1"
BUILDER_CONTEXT_PACKET_SCHEMA = "adaos.builder.context_packet.v1"
BUILDER_INTERACTION_FRAME_SCHEMA = "adaos.builder.interaction_frame.v1"
BUILDER_WORKFLOW_EVENT = "builder.workflow.changed"
_LOCK = threading.RLock()
_MAX_STATE_BYTES = 512 * 1024
_MAX_HISTORY = 50
_MAX_CHANGE_ISSUES = 50
_MAX_CHANGE_RUNS = 100
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
    return {
        "schema": BUILDER_RUN_SCHEMA,
        "run_id": run_id,
        "change_id": change_id,
        "activity": str(value.get("activity") or "workflow").strip() or "workflow",
        "executor": str(value.get("executor") or "builder.workflow").strip() or "builder.workflow",
        "status": status,
        "context_packet_digest": str(value.get("context_packet_digest") or "").strip() or None,
        "environment_ref": str(value.get("environment_ref") or "").strip() or None,
        "input_refs": [str(item).strip() for item in value.get("input_refs") or [] if str(item).strip()][-100:],
        "output_refs": [str(item).strip() for item in value.get("output_refs") or [] if str(item).strip()][-100:],
        "evidence_refs": [str(item).strip() for item in value.get("evidence_refs") or [] if str(item).strip()][-100:],
        "started_at": str(value.get("started_at") or "").strip() or None,
        "completed_at": str(value.get("completed_at") or "").strip() or None,
        "error": str(value.get("error") or "").strip() or None,
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
    return {
        **legacy,
        "schema": BUILDER_CHANGE_SCHEMA,
        "change_id": change_id,
        "change_set_id": change_id,
        "project_ref": str(value.get("project_ref") or "").strip() or None,
        "base_ref": copy.deepcopy(value.get("base_ref")) if isinstance(value.get("base_ref"), Mapping) else None,
        "runs": runs[-_MAX_CHANGE_RUNS:],
        "context_packet_digest": str(value.get("context_packet_digest") or "").strip() or None,
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
    value.pop("context_packet_digest", None)
    value.pop("project_ref", None)
    value.pop("base_ref", None)
    value["supersedes_change_set_id"] = str(
        value.pop("supersedes_change_id", None) or value.get("supersedes_change_set_id") or ""
    ).strip() or None
    return value


def _stable_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


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

        return {
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
        return {
            **copy.deepcopy(workflow),
            "object_type": kind,
            "object_id": project_id,
            "archived": bool(state.get("archived")),
            "capabilities": self._capabilities(workflow, archived=bool(state.get("archived")), object_type=kind),
        }

    def interaction_frame(self, object_type: str, object_id: str) -> dict[str, Any]:
        """Project the current workflow into channel-neutral deterministic actions."""

        projection = self.describe(object_type, object_id)
        generation = int(projection.get("generation") or 0)
        project_ref = f"{projection['object_type']}:{projection['object_id']}"
        interaction = _mapping(projection.get("interaction"))
        change = _normalize_change(projection.get("change") or projection.get("change_set"))
        capabilities = _mapping(projection.get("capabilities"))
        active_phase = str(projection.get("active_phase") or "prototype")
        delivery_status = str(_mapping(projection.get("delivery")).get("status") or "idle")
        automation_status = str(
            _mapping(projection.get("automation")).get("status") or "not_started"
        )

        actions: list[dict[str, Any]] = []

        def add_action(
            command: str,
            label: str,
            risk: str,
            *,
            target_ref: str | None = None,
            presentation: str = "button",
            fallback: str = "compact_action",
        ) -> None:
            actions.append(
                build_builder_action(
                    command,
                    label,
                    risk,
                    expected_generation=generation,
                    target_ref=target_ref,
                    presentation=presentation,
                    fallback=fallback,
                )
            )

        add_action(
            "builder.process.inspect",
            "Show process",
            "read",
            target_ref=change and f"change:{change['change_id']}" or project_ref,
            presentation="panel",
            fallback="compact_status",
        )
        if change is None:
            add_action("builder.change.plan", "Plan change", "local_reversible", target_ref=project_ref)
            message = "Describe the requested change to begin."
        else:
            change_ref = f"change:{change['change_id']}"
            gate = str(change.get("gate") or active_phase)
            status = str(change.get("status") or "planned")
            message = f"Change {change['change_id']} is {status}; next gate: {gate}."
            if capabilities.get("can_update_change_set"):
                add_action("builder.change.extend", "Add to change", "local_reversible", target_ref=change_ref)
            if capabilities.get("can_edit_prototype") and gate == "prototype":
                add_action(
                    "builder.prototype.edit",
                    "Refine prototype",
                    "local_reversible",
                    target_ref=change_ref,
                )
                add_action(
                    "builder.prototype.approve",
                    "Approve prototype",
                    "isolated_write",
                    target_ref=change_ref,
                )
            if active_phase == "prototype" and gate == "automation":
                add_action(
                    "builder.implementation.start",
                    "Start implementation",
                    "isolated_write",
                    target_ref=change_ref,
                )
            if active_phase == "automation" and automation_status in {"completed", "failed"}:
                add_action(
                    "builder.implementation.iterate",
                    "Continue implementation",
                    "isolated_write",
                    target_ref=change_ref,
                )
            if capabilities.get("can_return_to_prototype"):
                add_action(
                    "builder.prototype.derive",
                    "Return result to prototype",
                    "isolated_write",
                    target_ref=change_ref,
                )
            if capabilities.get("can_prepare_candidate"):
                add_action(
                    "builder.trial.prepare",
                    "Prepare trial",
                    "trial_activation",
                    target_ref=change_ref,
                )
            if capabilities.get("can_decide_candidate"):
                candidate_id = str(_mapping(projection.get("delivery")).get("candidate_id") or "").strip()
                candidate_ref = f"candidate:{candidate_id}" if candidate_id else change_ref
                add_action(
                    "builder.trial.accept",
                    "Accept trial",
                    "workspace_activation",
                    target_ref=candidate_ref,
                )
                add_action(
                    "builder.trial.reject",
                    "Request changes",
                    "local_reversible",
                    target_ref=candidate_ref,
                )
            if capabilities.get("can_publish"):
                add_action(
                    "builder.publication.publish",
                    "Publish",
                    "publication",
                    target_ref=change_ref,
                )

        if capabilities.get("can_preview_prototype"):
            add_action(
                "builder.preview.prototype",
                "Preview prototype",
                "read",
                target_ref=f"prototype:{projection['object_id']}:{_mapping(projection.get('prototype')).get('head_revision') or 'current'}",
            )
        if capabilities.get("can_preview_automation"):
            add_action(
                "builder.preview.active",
                "Preview implementation",
                "read",
                target_ref=f"implementation:{projection['object_id']}:active",
            )
        if capabilities.get("can_preview_publication"):
            add_action(
                "builder.preview.publication",
                "Preview publication",
                "read",
                target_ref=f"publication:{projection['object_id']}:{_mapping(projection.get('publication')).get('current_version') or 'current'}",
            )

        views = [
            {"kind": "conversation", "presentation": "primary", "fallback": "messages"},
            {"kind": "process", "presentation": "panel", "fallback": "compact_status"},
            {"kind": "overview", "presentation": "panel", "fallback": "deep_link"},
            {"kind": "artifacts", "presentation": "panel", "fallback": "deep_link"},
            {"kind": "preview", "presentation": "adjacent", "fallback": "deep_link"},
        ]
        return {
            "schema": BUILDER_INTERACTION_FRAME_SCHEMA,
            "message": message,
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
            },
            "actions": actions,
            "views": views,
            "generation": generation,
        }

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
            if expected_generation is not None and int(workflow.get("generation") or 0) != int(expected_generation):
                raise BuilderWorkflowError(
                    f"stale Builder action generation: expected {expected_generation}, "
                    f"current {int(workflow.get('generation') or 0)}"
                )
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
            self._record_transition_run(
                workflow,
                action=action_token,
                actor=str(actor or "builder"),
                metadata=details,
                changed_at=changed_at,
                project_ref=f"{kind}:{project_id}",
            )
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
    def _record_transition_run(
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
        }
        runs = [
            _normalize_run(item, change_id=change_id)
            for item in (previous or {}).get("runs") or []
            if isinstance(item, Mapping)
        ]
        run_id = str(metadata.get("run_id") or metadata.get("task_id") or "").strip()
        if not run_id:
            run_id = f"{change_id}:run:{int(workflow.get('generation') or 0):04d}"
        failure = action.endswith("_failed") or action in {"candidate_rejected"}
        running = action.endswith("_started") or action in {"request_return_to_prototype"}
        status = "failed" if failure else ("running" if running else "succeeded")
        context_packet = workflow.get("context_packet") if isinstance(workflow.get("context_packet"), Mapping) else {}
        run = {
            "schema": BUILDER_RUN_SCHEMA,
            "run_id": run_id,
            "change_id": change_id,
            "activity": action,
            "executor": str(metadata.get("executor") or actor or "builder.workflow"),
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
            previous_run = None
            if change.get("runs"):
                previous_run = copy.deepcopy(change["runs"][-1])
            bounded_conversation = _bounded_conversation_context(conversation_context)
            bounded_pending_actions = _bounded_pending_action_refs(pending_action_refs)
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
                    "source_message_ids": copy.deepcopy(change.get("source_message_ids") or []),
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
                "budget": {
                    "max_state_bytes": _MAX_STATE_BYTES,
                    "issue_count": len(change.get("issues") or []),
                    "run_count": len(change.get("runs") or []),
                    "source_message_ref_count": len(change.get("source_message_ids") or []),
                    "conversation_message_count": len((bounded_conversation or {}).get("messages") or []),
                    "conversation_segment_count": len((bounded_conversation or {}).get("segments") or []),
                    "memory_item_count": len((bounded_conversation or {}).get("memory") or []),
                    "pending_action_ref_count": len(bounded_pending_actions),
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
        if action == "change_evidence_recorded":
            require_change_set(metadata.get("change_set_id"))
            change_id = str(metadata.get("change_id") or "").strip()
            if not change_id:
                raise BuilderWorkflowError("change evidence requires change_id")
            add_change_evidence(change_id)
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
