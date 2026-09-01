from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


REPAIR_TASK_SCHEMA = "adaos.builder.repair_task.v1"
ACTIVE_REPAIR_STATES = {"open", "in_progress"}
WORK_ITEM_STATES = {
    "planned",
    "claimed",
    "in_progress",
    "validating",
    "published",
    "blocked",
    "failed",
    "completed",
    "superseded",
}
ACTIVE_WORK_ITEM_STATES = WORK_ITEM_STATES - {"completed", "superseded"}
WORK_ITEM_TRANSITIONS = {
    "planned": {"claimed", "blocked", "superseded"},
    "claimed": {"in_progress", "blocked", "failed", "superseded"},
    "in_progress": {"validating", "blocked", "failed", "superseded"},
    "validating": {"published", "in_progress", "blocked", "failed", "superseded"},
    "published": {"completed", "in_progress", "blocked", "failed", "superseded"},
    "blocked": {"planned", "claimed", "superseded"},
    "failed": {"claimed", "in_progress", "superseded"},
    "completed": {"in_progress", "superseded"},
    "superseded": set(),
}
TASK_EVIDENCE_SIGNAL_MAP = {
    "failed_tests": "test_failure",
    "import_errors": "import_error",
    "route_pressure": "route_pressure",
    "memory_growth": "memory_growth",
    "nlu_misses": "nlu_miss",
}
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _ticket_ids(source_refs: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        {
            str(ref.get("ticket_id") or ref.get("id") or "").strip()
            for ref in source_refs
            if str(ref.get("type") or "").strip() in {"dev_ticket", "development_ticket"}
            and str(ref.get("ticket_id") or ref.get("id") or "").strip()
        }
    )


def _work_status_for_legacy(status: Any) -> str:
    return {
        "open": "planned",
        "in_progress": "in_progress",
        "resolved": "completed",
        "superseded": "superseded",
        "not_design_time_fixable": "blocked",
    }.get(str(status or "").strip(), "planned")


def _automation_work_status(automation: Mapping[str, Any]) -> str | None:
    projection = automation.get("automation") if isinstance(automation.get("automation"), Mapping) else automation
    session = automation.get("session") if isinstance(automation.get("session"), Mapping) else {}
    task = session.get("task") if isinstance(session.get("task"), Mapping) else automation.get("task")
    task = task if isinstance(task, Mapping) else {}
    status = str(
        projection.get("status")
        or session.get("status")
        or task.get("status")
        or ""
    ).strip().lower()
    if status in {"queued", "submitted", "waiting", "pending"}:
        return "claimed"
    if status in {"running", "in_progress", "busy"}:
        return "in_progress"
    if status in {"failed", "errored", "cancelled"}:
        return "failed"
    if status in {"completed", "succeeded", "success"}:
        return "validating"
    return None


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "builder.repair_task.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fingerprint(project_id: str, signal_type: str, summary: str, context: Mapping[str, Any]) -> str:
    stable_context = {
        key: context.get(key)
        for key in ("artifact_id", "component", "capability", "route", "test", "intent")
        if context.get(key) is not None
    }
    payload = json.dumps(
        [project_id, signal_type, " ".join(summary.lower().split()), stable_context],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"repair:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


@dataclass(slots=True)
class BuilderRepairService:
    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "builder" / "repairs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".state.lock"

    def report(
        self,
        *,
        project_id: str,
        signal_type: str,
        summary: str,
        source_refs: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any] | None = None,
        design_time_fixable: bool = True,
        dedup_key: str | None = None,
        supersedes: Sequence[str] = (),
    ) -> dict[str, Any]:
        project = str(project_id or "").strip()
        kind = str(signal_type or "other").strip().lower() or "other"
        text = str(summary or "").strip()
        if not project or not text:
            raise ValueError("project_id and summary are required")
        details = dict(context or {})
        key = str(dedup_key or "").strip() or _fingerprint(project, kind, text, details)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for task in state["tasks"].values():
                if task.get("dedup_key") == key and task.get("status") in ACTIVE_REPAIR_STATES:
                    task["occurrence_count"] = int(task.get("occurrence_count") or 1) + 1
                    task["source_refs"] = self._merge_refs(task.get("source_refs") or [], source_refs)
                    task["ticket_ids"] = sorted(
                        set(task.get("ticket_ids") or []) | set(_ticket_ids(source_refs))
                    )
                    task["revision"] = int(task.get("revision") or 1) + 1
                    task["updated_at"] = _now()
                    self._append_work_timeline(
                        task,
                        event="occurrence_recorded",
                        actor="builder.repair_registry",
                        details={"occurrence_count": task["occurrence_count"]},
                    )
                    self._write(state)
                    return {"ok": True, "duplicate": True, "task": _clone(task)}
            now = _now()
            repair_id = f"repair.{new_id()}"
            task = {
                "schema": REPAIR_TASK_SCHEMA,
                "repair_id": repair_id,
                "work_item_id": repair_id,
                "project_id": project,
                "status": "open" if design_time_fixable else "not_design_time_fixable",
                "work_status": "planned" if design_time_fixable else "blocked",
                "revision": 1,
                "ticket_ids": _ticket_ids(source_refs),
                "package_id": str(details.get("package_id") or "").strip() or None,
                "timeline": [],
                "signal_type": kind if kind in {
                    "guard", "quarantine", "post_activation", "test_failure", "import_error",
                    "route_pressure", "memory_growth", "nlu_miss", "conversation_eval", "other",
                } else "other",
                "summary": text,
                "dedup_key": key,
                "occurrence_count": 1,
                "source_refs": self._merge_refs([], source_refs),
                "context": details,
                "supersedes": [str(item) for item in supersedes if str(item).strip()],
                "superseded_by": None,
                "acceptance": {
                    "capability_works": False,
                    "regression_free": False,
                    "evidence_refs": [],
                    "recorded_at": None,
                    "actor": None,
                },
                "created_at": now,
                "updated_at": now,
            }
            self._append_work_timeline(
                task,
                event="created",
                actor="builder.repair_registry",
                details={"work_status": task["work_status"]},
                recorded_at=now,
            )
            for old_id in task["supersedes"]:
                old = state["tasks"].get(old_id)
                if old and old.get("status") in ACTIVE_REPAIR_STATES:
                    old["status"] = "superseded"
                    old["work_status"] = "superseded"
                    old["superseded_by"] = repair_id
                    old["updated_at"] = now
                    old["revision"] = int(old.get("revision") or 1) + 1
                    self._append_work_timeline(
                        old,
                        event="superseded",
                        actor="builder.repair_registry",
                        details={"superseded_by": repair_id},
                        recorded_at=now,
                    )
            self._validate(task)
            state["tasks"][repair_id] = task
            self._write(state)
            return {"ok": True, "duplicate": False, "task": _clone(task)}

    def start(self, repair_id: str) -> dict[str, Any]:
        return self._set_status(repair_id, "in_progress")

    def transition_work_item(
        self,
        repair_id: str,
        *,
        status: str,
        actor: str,
        reason: str = "",
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        target = str(status or "").strip()
        if target not in WORK_ITEM_STATES:
            raise ValueError(f"unsupported Builder work item status: {target or '<missing>'}")
        actor_token = str(actor or "").strip()
        if not actor_token:
            raise ValueError("Builder work item transition requires actor identity")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            task = state["tasks"].get(str(repair_id))
            if not task:
                raise KeyError(repair_id)
            current = str(task.get("work_status") or _work_status_for_legacy(task.get("status"))).strip()
            revision = int(task.get("revision") or 1)
            if expected_revision is not None and int(expected_revision) != revision:
                raise ValueError("Builder work item changed since it was read")
            if current == target:
                return _clone(task)
            if target not in WORK_ITEM_TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid Builder work item transition: {current} -> {target}")
            self._set_work_status_locked(
                task,
                target,
                actor=actor_token,
                reason=reason,
                evidence_refs=evidence_refs,
            )
            self._validate(task)
            self._write(state)
            return _clone(task)

    def claim(self, repair_id: str, *, actor: str, expected_revision: int | None = None) -> dict[str, Any]:
        return self.transition_work_item(
            repair_id,
            status="claimed",
            actor=actor,
            expected_revision=expected_revision,
        )

    def ingest_task_evidence(
        self,
        *,
        project_id: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Normalize runtime/evaluation evidence into bounded repair tasks."""

        created: list[dict[str, Any]] = []
        for source_key, signal_type in TASK_EVIDENCE_SIGNAL_MAP.items():
            raw_items = evidence.get(source_key)
            items = raw_items if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes, Mapping)) else [raw_items]
            for raw in items:
                if not raw:
                    continue
                item = dict(raw) if isinstance(raw, Mapping) else {"summary": str(raw)}
                summary = str(
                    item.get("summary")
                    or item.get("message")
                    or item.get("error")
                    or f"{signal_type} observed"
                ).strip()
                refs = item.get("source_refs") if isinstance(item.get("source_refs"), Sequence) else []
                result = self.report(
                    project_id=project_id,
                    signal_type=signal_type,
                    summary=summary,
                    source_refs=[dict(ref) for ref in refs if isinstance(ref, Mapping)],
                    context={
                        **{key: value for key, value in item.items() if key not in {"summary", "message", "error", "source_refs"}},
                        "evidence_category": source_key,
                    },
                    design_time_fixable=bool(item.get("design_time_fixable", True)),
                    dedup_key=str(item.get("dedup_key") or "").strip() or None,
                )
                created.append(result)
        return {
            "ok": True,
            "project_id": str(project_id),
            "reported_count": len(created),
            "reports": created,
        }

    def task_context(self, project_id: str, *, limit: int = 30) -> dict[str, Any]:
        active = [
            item
            for item in self.list(project_id=project_id)
            if str(item.get("work_status") or _work_status_for_legacy(item.get("status")))
            in ACTIVE_WORK_ITEM_STATES
        ][-max(1, min(int(limit or 30), 100)):]
        return {
            "schema": "adaos.builder.repair_context.v1",
            "status": "present" if active else "missing",
            "project_id": str(project_id),
            "active_count": len(active),
            "tasks": active,
        }

    def record_acceptance(
        self,
        repair_id: str,
        *,
        capability_works: bool,
        regression_free: bool,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
    ) -> dict[str, Any]:
        if not evidence_refs:
            raise ValueError("repair acceptance requires evidence_refs")
        if not actor:
            raise ValueError("repair acceptance requires actor identity")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            task = state["tasks"].get(str(repair_id))
            if not task:
                raise KeyError(repair_id)
            task["acceptance"] = {
                "capability_works": bool(capability_works),
                "regression_free": bool(regression_free),
                "evidence_refs": self._merge_refs([], evidence_refs),
                "recorded_at": _now(),
                "actor": str(actor),
            }
            task["status"] = "resolved" if capability_works and regression_free else "in_progress"
            self._set_work_status_locked(
                task,
                "completed" if capability_works and regression_free else "in_progress",
                actor=str(actor),
                reason="acceptance_passed" if capability_works and regression_free else "acceptance_failed",
                evidence_refs=evidence_refs,
                allow_any=True,
            )
            self._validate(task)
            self._write(state)
            return _clone(task)

    def link_automation(
        self,
        repair_id: str,
        *,
        automation: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Attach one Builder Automation session to this repair task."""

        if not actor:
            raise ValueError("automation link requires actor identity")
        projection = (
            automation.get("automation")
            if isinstance(automation.get("automation"), Mapping)
            else automation
        )
        session = automation.get("session") if isinstance(automation.get("session"), Mapping) else {}
        task = session.get("task") if isinstance(session.get("task"), Mapping) else {}
        if not task and isinstance(automation.get("task"), Mapping):
            task = automation["task"]
        task_id = str(
            projection.get("task_id")
            or session.get("current_task_id")
            or task.get("task_id")
            or ""
        ).strip()
        session_id = str(projection.get("session_id") or session.get("session_id") or "").strip()
        if not session_id and not task_id:
            raise ValueError("automation link requires session_id or task_id")
        status = str(projection.get("status") or session.get("status") or task.get("status") or "linked").strip()
        budget_usage = projection.get("budget_usage") if isinstance(projection.get("budget_usage"), Mapping) else {}
        observed = budget_usage.get("observed") if isinstance(budget_usage.get("observed"), Mapping) else {}
        declared = budget_usage.get("declared") if isinstance(budget_usage.get("declared"), Mapping) else {}
        usage_receipt = (
            session.get("codex_usage_accounting")
            if isinstance(session.get("codex_usage_accounting"), Mapping)
            else {}
        )
        completion_readiness = (
            session.get("completion_readiness")
            if isinstance(session.get("completion_readiness"), Mapping)
            else {}
        )
        trial = (
            completion_readiness.get("aprobation")
            if isinstance(completion_readiness.get("aprobation"), Mapping)
            else {}
        )
        reported_usage = dict(observed) if observed else {}
        if usage_receipt:
            for key in (
                "model_tokens",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "billable_tokens",
            ):
                if usage_receipt.get(key) is not None:
                    reported_usage[key] = usage_receipt.get(key)
            if usage_receipt.get("status"):
                reported_usage["receipt_status"] = usage_receipt.get("status")
            if usage_receipt.get("root_event_id"):
                reported_usage["root_event_id"] = usage_receipt.get("root_event_id")
        link = {
            "schema": "adaos.builder.repair_automation_link.v1",
            "session_id": session_id or None,
            "task_id": task_id or None,
            "status": status or None,
            "phase": projection.get("phase"),
            "terminal": bool(projection.get("terminal")),
            "busy": bool(projection.get("busy")),
            "change_set_id": projection.get("change_set_id"),
            "change_id": projection.get("change_id"),
            "webspace_id": projection.get("webspace_id"),
            "project": projection.get("project") if isinstance(projection.get("project"), Mapping) else {},
            "result_branch": projection.get("result_branch"),
            "summary": projection.get("summary"),
            "error": projection.get("error"),
            "linked_by": str(actor),
            "linked_at": _now(),
        }
        if usage_receipt:
            link["codex_usage_accounting"] = dict(usage_receipt)
        refs = []
        if session_id:
            refs.append({"type": "builder_automation_session", "id": session_id})
        if task_id:
            refs.append({"type": "skill_factory_task", "id": task_id})
        if projection.get("change_id"):
            refs.append({"type": "builder_change", "id": str(projection.get("change_id"))})
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            repair = state["tasks"].get(str(repair_id))
            if not repair:
                raise KeyError(repair_id)
            context = dict(repair.get("context") or {})
            context["automation"] = link
            if declared:
                context["cost_estimate"] = dict(declared)
            if reported_usage:
                context["usage"] = reported_usage
            if trial:
                context["trial"] = dict(trial)
            repair["context"] = context
            repair["source_refs"] = self._merge_refs(repair.get("source_refs") or [], refs)
            if repair.get("status") == "open":
                repair["status"] = "in_progress"
            automation_work_status = _automation_work_status(automation)
            if automation_work_status:
                self._set_work_status_locked(
                    repair,
                    automation_work_status,
                    actor=str(actor),
                    reason=f"automation:{status or 'linked'}",
                    allow_any=True,
                )
            else:
                repair["updated_at"] = _now()
            self._validate(repair)
            self._write(state)
            return _clone(repair)

    def list(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        work_status: str | None = None,
        package_id: str | None = None,
    ) -> list[dict[str, Any]]:
        state = self._read()
        tasks = [self._normalized_task(item) for item in state["tasks"].values()]
        if project_id:
            tasks = [item for item in tasks if item.get("project_id") == project_id]
        if status:
            tasks = [item for item in tasks if item.get("status") == status]
        if work_status:
            tasks = [item for item in tasks if item.get("work_status") == work_status]
        if package_id:
            tasks = [item for item in tasks if item.get("package_id") == package_id]
        return [_clone(item) for item in sorted(tasks, key=lambda item: item.get("created_at") or "")]

    def package_rollup(self, package_id: str) -> dict[str, Any]:
        token = str(package_id or "").strip()
        if not token:
            raise ValueError("package_id is required")
        items = self.list(package_id=token)
        total_tokens = 0
        fresh_tokens = 0
        for item in items:
            context = item.get("context") if isinstance(item.get("context"), Mapping) else {}
            usage = context.get("usage") if isinstance(context.get("usage"), Mapping) else {}
            input_tokens = int(usage.get("input_tokens") or 0)
            cached_tokens = int(usage.get("cached_input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            total_tokens += int(usage.get("total_tokens") or usage.get("model_tokens") or 0)
            fresh_tokens += max(0, input_tokens - cached_tokens) + output_tokens
        return {
            "schema": "adaos.builder.work_package_rollup.v1",
            "package_id": token,
            "work_item_count": len(items),
            "ticket_ids": sorted({ticket_id for item in items for ticket_id in item.get("ticket_ids") or []}),
            "status_counts": {
                status: sum(1 for item in items if item.get("work_status") == status)
                for status in sorted(WORK_ITEM_STATES)
                if any(item.get("work_status") == status for item in items)
            },
            "total_tokens": total_tokens,
            "fresh_plus_output_tokens": fresh_tokens,
        }

    def _set_status(self, repair_id: str, status: str) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            task = state["tasks"].get(str(repair_id))
            if not task:
                raise KeyError(repair_id)
            if task.get("status") in {"resolved", "superseded", "not_design_time_fixable"}:
                raise ValueError(f"repair task is terminal: {task.get('status')}")
            task["status"] = status
            self._set_work_status_locked(
                task,
                "in_progress" if status == "in_progress" else _work_status_for_legacy(status),
                actor="builder.repair_registry",
                reason=f"legacy_status:{status}",
                allow_any=True,
            )
            self._validate(task)
            self._write(state)
            return _clone(task)

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"schema": "adaos.builder.repair_state.v1", "tasks": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping) or not isinstance(value.get("tasks"), Mapping):
            raise ValueError("Builder repair state is corrupt")
        return {"schema": "adaos.builder.repair_state.v1", "tasks": dict(value["tasks"])}

    @staticmethod
    def _normalized_task(task: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(task)
        repair_id = str(item.get("repair_id") or "").strip()
        item.setdefault("work_item_id", repair_id)
        item.setdefault("work_status", _work_status_for_legacy(item.get("status")))
        item.setdefault("revision", 1)
        item.setdefault("ticket_ids", _ticket_ids(item.get("source_refs") or []))
        item.setdefault("package_id", None)
        item.setdefault("timeline", [])
        return item

    @staticmethod
    def _append_work_timeline(
        task: dict[str, Any],
        *,
        event: str,
        actor: str,
        details: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        recorded_at: str | None = None,
    ) -> None:
        timeline = [dict(item) for item in task.get("timeline") or [] if isinstance(item, Mapping)]
        timeline.append(
            {
                "event": str(event),
                "actor": str(actor),
                "details": dict(details or {}),
                "evidence_refs": [dict(ref) for ref in evidence_refs if isinstance(ref, Mapping)],
                "recorded_at": recorded_at or _now(),
            }
        )
        task["timeline"] = timeline[-200:]

    @classmethod
    def _set_work_status_locked(
        cls,
        task: dict[str, Any],
        status: str,
        *,
        actor: str,
        reason: str = "",
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        allow_any: bool = False,
    ) -> None:
        current = str(task.get("work_status") or _work_status_for_legacy(task.get("status"))).strip()
        target = str(status or "").strip()
        if target not in WORK_ITEM_STATES:
            raise ValueError(f"unsupported Builder work item status: {target}")
        if current == target:
            task["updated_at"] = _now()
            return
        if not allow_any and target not in WORK_ITEM_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid Builder work item transition: {current} -> {target}")
        task["work_status"] = target
        task["revision"] = int(task.get("revision") or 1) + 1
        task["updated_at"] = _now()
        cls._append_work_timeline(
            task,
            event="status_changed",
            actor=actor,
            details={"from": current, "to": target, "reason": str(reason or "").strip() or None},
            evidence_refs=evidence_refs,
            recorded_at=task["updated_at"],
        )

    def _write(self, state: Mapping[str, Any]) -> None:
        atomic_write_json(self.state_path, dict(state))

    @staticmethod
    def _merge_refs(current: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in [*current, *incoming]:
            item = dict(raw)
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out[-100:]

    @staticmethod
    def _validate(task: Mapping[str, Any]) -> None:
        errors = sorted(Draft202012Validator(_schema()).iter_errors(task), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"invalid Builder repair task: {errors[0].message}")


__all__ = [
    "ACTIVE_WORK_ITEM_STATES",
    "ACTIVE_REPAIR_STATES",
    "BuilderRepairService",
    "REPAIR_TASK_SCHEMA",
    "TASK_EVIDENCE_SIGNAL_MAP",
    "WORK_ITEM_STATES",
    "WORK_ITEM_TRANSITIONS",
]
