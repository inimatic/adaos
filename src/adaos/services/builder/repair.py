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
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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
                    task["updated_at"] = _now()
                    self._write(state)
                    return {"ok": True, "duplicate": True, "task": _clone(task)}
            now = _now()
            repair_id = f"repair.{new_id()}"
            task = {
                "schema": REPAIR_TASK_SCHEMA,
                "repair_id": repair_id,
                "project_id": project,
                "status": "open" if design_time_fixable else "not_design_time_fixable",
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
            for old_id in task["supersedes"]:
                old = state["tasks"].get(old_id)
                if old and old.get("status") in ACTIVE_REPAIR_STATES:
                    old["status"] = "superseded"
                    old["superseded_by"] = repair_id
                    old["updated_at"] = now
            self._validate(task)
            state["tasks"][repair_id] = task
            self._write(state)
            return {"ok": True, "duplicate": False, "task": _clone(task)}

    def start(self, repair_id: str) -> dict[str, Any]:
        return self._set_status(repair_id, "in_progress")

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
            task["updated_at"] = _now()
            self._validate(task)
            self._write(state)
            return _clone(task)

    def list(self, *, project_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        state = self._read()
        tasks = list(state["tasks"].values())
        if project_id:
            tasks = [item for item in tasks if item.get("project_id") == project_id]
        if status:
            tasks = [item for item in tasks if item.get("status") == status]
        return [_clone(item) for item in sorted(tasks, key=lambda item: item.get("created_at") or "")]

    def _set_status(self, repair_id: str, status: str) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            task = state["tasks"].get(str(repair_id))
            if not task:
                raise KeyError(repair_id)
            if task.get("status") in {"resolved", "superseded", "not_design_time_fixable"}:
                raise ValueError(f"repair task is terminal: {task.get('status')}")
            task["status"] = status
            task["updated_at"] = _now()
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


__all__ = ["ACTIVE_REPAIR_STATES", "BuilderRepairService", "REPAIR_TASK_SCHEMA"]
