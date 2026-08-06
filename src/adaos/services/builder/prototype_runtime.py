"""Deterministic, disposable data runtime for executable Builder prototypes."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError

from .workflow import BuilderWorkflowError


PROTOTYPE_DATA_SCHEMA = "adaos.builder.prototype_data.v1"
PROTOTYPE_TRACE_SCHEMA = "adaos.builder.prototype_trace.v1"


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name: str, value: Mapping[str, Any], *, label: str) -> None:
    try:
        Draft202012Validator(_schema(schema_name)).validate(dict(value))
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        raise BuilderWorkflowError(f"invalid {label}{suffix}: {exc.message}") from exc


def _validate_value(schema: Mapping[str, Any], value: Any, *, label: str) -> None:
    try:
        Draft202012Validator(dict(schema)).validate(value)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        raise BuilderWorkflowError(f"invalid {label}{suffix}: {exc.message}") from exc


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_id(value: Mapping[str, Any]) -> str:
    token = str(value.get("id") or "").strip()
    if not token:
        raise BuilderWorkflowError("prototype record requires a stable id")
    return token


@dataclass(slots=True)
class PrototypeDataRuntime:
    """Execute declared mock activities without touching production state.

    Runtime state is deliberately explicit and serializable. A caller may keep
    one session per Preview, discard it, or share it within one Webspace.
    """

    definition: dict[str, Any]
    records: list[dict[str, Any]]
    generation: int
    entries: list[dict[str, Any]]

    @classmethod
    def start(cls, definition: Mapping[str, Any]) -> "PrototypeDataRuntime":
        value = copy.deepcopy(dict(definition))
        _validate("builder.prototype_data.v1.schema.json", value, label="prototype data definition")
        activities = [str(item["activity_id"]) for item in value["activities"]]
        if len(activities) != len(set(activities)):
            raise BuilderWorkflowError("prototype activity ids must be unique")
        for index, record in enumerate(value["seed"]):
            _validate_value(value["record_schema"], record, label=f"prototype seed[{index}]")
        ids = [_record_id(item) for item in value["seed"]]
        if len(ids) != len(set(ids)):
            raise BuilderWorkflowError("prototype seed record ids must be unique")
        return cls(value, copy.deepcopy(value["seed"]), 0, [])

    @property
    def source_id(self) -> str:
        return str(self.definition["source_id"])

    def activity_requirements(self) -> list[dict[str, Any]]:
        """Return implementation-facing requirements, not backend guesses."""

        return [
            {
                "activity_id": item["activity_id"],
                "input_schema": copy.deepcopy(item["input_schema"]),
                "output_schema": copy.deepcopy(item["output_schema"]),
                "side_effect_class": item["side_effect_class"],
                "implementation_status": item["implementation_status"],
                "implementation_ref": item.get("implementation_ref"),
            }
            for item in self.definition["activities"]
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "mode": self.definition["mode"],
            "generation": self.generation,
            "records": copy.deepcopy(self.records),
            "definition_digest": _digest(self.definition),
        }

    def trace(self, *, trace_id: str = "prototype-session") -> dict[str, Any]:
        value = {
            "schema": PROTOTYPE_TRACE_SCHEMA,
            "trace_id": trace_id,
            "source_id": self.source_id,
            "generation": self.generation,
            "entries": copy.deepcopy(self.entries),
        }
        _validate("builder.prototype_trace.v1.schema.json", value, label="prototype trace")
        return value

    def execute(
        self,
        activity_id: str,
        input_value: Mapping[str, Any] | None = None,
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        activity = next(
            (item for item in self.definition["activities"] if item["activity_id"] == activity_id),
            None,
        )
        if activity is None:
            raise BuilderWorkflowError(f"prototype activity is not declared: {activity_id}")
        payload = copy.deepcopy(dict(input_value or {}))
        _validate_value(activity["input_schema"], payload, label=f"{activity_id} input")
        if expected_generation is not None and expected_generation != self.generation:
            raise BuilderWorkflowError(
                f"stale prototype generation: expected {expected_generation}, current {self.generation}"
            )

        operation = str(activity["operation"])
        if self.definition["mode"] == "static" and operation not in {"list", "get", "fixture"}:
            raise BuilderWorkflowError("static prototype data is read-only")
        result, provenance = self._apply(operation, activity, payload)
        _validate_value(activity["output_schema"], result, label=f"{activity_id} output")
        entry = {
            "sequence": len(self.entries) + 1,
            "activity_id": activity_id,
            "operation": operation,
            "status": "completed",
            "side_effect_class": activity["side_effect_class"],
            "input": payload,
            "result": copy.deepcopy(result),
            "provenance": provenance,
            "reason_code": None,
        }
        self.entries.append(entry)
        return {
            "ok": True,
            "result": copy.deepcopy(result),
            "generation": self.generation,
            "trace_entry": copy.deepcopy(entry),
        }

    def _apply(
        self,
        operation: str,
        activity: Mapping[str, Any],
        payload: dict[str, Any],
    ) -> tuple[Any, dict[str, str]]:
        if operation == "list":
            result: Any = copy.deepcopy(self.records)
        elif operation == "get":
            identifier = str(payload.get("id") or "").strip()
            result = next((copy.deepcopy(item) for item in self.records if _record_id(item) == identifier), None)
        elif operation == "create":
            record = copy.deepcopy(payload.get("record"))
            if not isinstance(record, Mapping):
                raise BuilderWorkflowError("prototype create requires input.record")
            record = dict(record)
            _validate_value(self.definition["record_schema"], record, label="prototype record")
            identifier = _record_id(record)
            if any(_record_id(item) == identifier for item in self.records):
                raise BuilderWorkflowError(f"prototype record already exists: {identifier}")
            self.records.append(record)
            self.generation += 1
            result = copy.deepcopy(record)
        elif operation == "update":
            identifier = str(payload.get("id") or "").strip()
            patch = payload.get("patch")
            if not isinstance(patch, Mapping):
                raise BuilderWorkflowError("prototype update requires input.patch")
            index = next((i for i, item in enumerate(self.records) if _record_id(item) == identifier), None)
            if index is None:
                raise BuilderWorkflowError(f"prototype record is missing: {identifier}")
            record = {**self.records[index], **copy.deepcopy(dict(patch)), "id": identifier}
            _validate_value(self.definition["record_schema"], record, label="prototype record")
            self.records[index] = record
            self.generation += 1
            result = copy.deepcopy(record)
        elif operation == "delete":
            identifier = str(payload.get("id") or "").strip()
            index = next((i for i, item in enumerate(self.records) if _record_id(item) == identifier), None)
            if index is None:
                raise BuilderWorkflowError(f"prototype record is missing: {identifier}")
            result = copy.deepcopy(self.records.pop(index))
            self.generation += 1
        elif operation == "reset":
            self.records = copy.deepcopy(self.definition["seed"])
            self.generation += 1
            result = copy.deepcopy(self.records)
        elif operation == "fixture":
            fixture_key = str(activity.get("fixture_key") or payload.get("fixture_key") or "").strip()
            fixture = dict(self.definition.get("fixtures") or {}).get(fixture_key)
            if not isinstance(fixture, Mapping):
                raise BuilderWorkflowError(f"recorded prototype fixture is missing: {fixture_key}")
            return copy.deepcopy(fixture["result"]), {
                "kind": "recorded_fixture",
                "source_ref": str(fixture.get("provenance") or f"fixture:{fixture_key}"),
            }
        elif operation == "generate":
            seed = {"activity_id": activity["activity_id"], "input": payload}
            result = {"text": f"generated-{_digest(seed)[:12]}", "fixture": True}
            return result, {
                "kind": "deterministic_generator",
                "source_ref": f"sha256:{_digest(seed)}",
            }
        else:  # pragma: no cover - schema closes the enum
            raise BuilderWorkflowError(f"unsupported prototype operation: {operation}")
        return result, {
            "kind": "local_state" if operation not in {"list", "get"} else "seed",
            "source_ref": f"prototype:{self.source_id}:generation:{self.generation}",
        }


__all__ = [
    "PROTOTYPE_DATA_SCHEMA",
    "PROTOTYPE_TRACE_SCHEMA",
    "PrototypeDataRuntime",
]
