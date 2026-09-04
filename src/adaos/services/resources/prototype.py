"""Disposable Resource Workbench provider for executable Builder prototypes."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.prototype_runtime import PrototypeDataRuntime
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


PROTOTYPE_RESOURCE_SCHEMA = "adaos.builder.prototype_resource.v1"
PROTOTYPE_RESOURCE_STATE_SCHEMA = "adaos.builder.prototype_resource_state.v1"


class PrototypeResourceConflict(ValueError):
    pass


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name: str, value: Mapping[str, Any], *, label: str) -> None:
    try:
        Draft202012Validator(_schema(schema_name)).validate(dict(value))
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        raise ValueError(f"invalid {label}{suffix}: {exc.message}") from exc


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for token in _text(path).split("."):
        if not token:
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(token)
    return current


def _record_id(value: Mapping[str, Any]) -> str:
    identifier = _text(value.get("id"))
    if not identifier:
        raise ValueError("prototype resource record requires id")
    return identifier


@dataclass(slots=True)
class PrototypeResourceService:
    """Persist one Preview-scoped local CRUD runtime across API requests."""

    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "resources" / "prototypes"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".prototype-resources.lock"

    def materialize(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(bundle))
        _validate(
            "builder.prototype_resource.v1.schema.json",
            value,
            label="prototype resource bundle",
        )
        definition = dict(value["resource_definition"])
        data_definition = dict(value["data_definition"])
        _validate("resource.definition.v1.schema.json", definition, label="resource definition")
        runtime = PrototypeDataRuntime.start(data_definition)
        resource_type = _text(definition.get("resource_type"))
        if not resource_type.startswith("prototype."):
            raise ValueError("prototype resource_type must start with 'prototype.'")
        authority = definition.get("authority") if isinstance(definition.get("authority"), Mapping) else {}
        if _text(authority.get("provider")) != "prototype":
            raise ValueError("prototype resource authority.provider must be 'prototype'")
        if _text(authority.get("binding")) != runtime.source_id:
            raise ValueError("prototype resource authority.binding must equal data source_id")
        record_schema = definition.get("record_schema")
        if not isinstance(record_schema, Mapping):
            raise ValueError("prototype resource definition requires inline record_schema")
        if _digest(record_schema) != _digest(data_definition["record_schema"]):
            raise ValueError("prototype resource record_schema must equal prototype data record_schema")
        metadata = definition.get("metadata") if isinstance(definition.get("metadata"), Mapping) else {}
        definition["metadata"] = {
            **dict(metadata),
            "prototype": True,
            "project_ref": value["project_ref"],
            "change_id": value["change_id"],
            "revision": value["revision"],
            "webui_digest": value["webui_digest"],
        }
        definition_digest = _digest(definition)
        bundle_digest = _digest(
            {
                "project_ref": value["project_ref"],
                "change_id": value["change_id"],
                "revision": value["revision"],
                "webui_digest": value["webui_digest"],
                "definition": definition,
                "data_definition": data_definition,
            }
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read_registry()
            previous = registry["resources"].get(resource_type)
            if isinstance(previous, Mapping) and previous.get("bundle_digest") == bundle_digest:
                return {"ok": True, "duplicate": True, "state": _clone(previous)}
            state = {
                "schema": PROTOTYPE_RESOURCE_STATE_SCHEMA,
                "resource_type": resource_type,
                "project_ref": value["project_ref"],
                "change_id": value["change_id"],
                "revision": value["revision"],
                "webui_digest": value["webui_digest"],
                "definition_digest": definition_digest,
                "bundle_digest": bundle_digest,
                "definition": definition,
                "data_definition": data_definition,
                "records": runtime.records,
                "generation": runtime.generation,
                "trace_entries": runtime.entries,
            }
            registry["resources"][resource_type] = state
            self._write_registry(registry)
        return {"ok": True, "duplicate": False, "state": _clone(state)}

    def definitions(self) -> list[dict[str, Any]]:
        return [
            _clone(item["definition"])
            for item in self._read_registry()["resources"].values()
            if isinstance(item, Mapping) and isinstance(item.get("definition"), Mapping)
        ]

    def definition(self, resource_type: str) -> dict[str, Any] | None:
        state = self._state(resource_type)
        return _clone(state["definition"]) if state else None

    def query(
        self,
        resource_type: str,
        *,
        filters: Mapping[str, Any],
        search: str,
        sort: Sequence[Any] | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        state = self._require_state(resource_type)
        records = [dict(item) for item in state.get("records") or [] if isinstance(item, Mapping)]
        token = _text(search).lower()
        result: list[dict[str, Any]] = []
        for record in records:
            if token and token not in json.dumps(record, ensure_ascii=False, sort_keys=True).lower():
                continue
            if any(not self._filter_matches(record, key, expected) for key, expected in filters.items() if key != "search"):
                continue
            result.append(record)
        for spec in reversed(list(sort or [])):
            if isinstance(spec, Mapping):
                field = _text(spec.get("field") or spec.get("key"))
                descending = _text(spec.get("direction") or spec.get("order")).lower() in {"desc", "descending"}
            else:
                field = _text(spec).lstrip("-")
                descending = _text(spec).startswith("-")
            if field:
                result.sort(
                    key=lambda item: (str(_read_path(item, field) or "").lower(), _record_id(item)),
                    reverse=descending,
                )
        return _clone(result[:limit] if limit is not None else result)

    def operate(
        self,
        resource_type: str,
        operation_id: str,
        *,
        record_id: str,
        payload: Mapping[str, Any],
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read_registry()
            state = registry["resources"].get(resource_type)
            if not isinstance(state, Mapping):
                raise ValueError(f"unknown prototype resource_type: {resource_type}")
            state = copy.deepcopy(dict(state))
            definition = dict(state["definition"])
            operation = next(
                (
                    dict(item)
                    for item in definition.get("operations") or []
                    if isinstance(item, Mapping) and _text(item.get("id")) == _text(operation_id)
                ),
                None,
            )
            if operation is None:
                raise ValueError(f"unsupported prototype resource operation: {resource_type}.{operation_id}")
            operation_kind = _text(operation.get("kind") or operation_id)
            runtime = PrototypeDataRuntime.start(dict(state["data_definition"]))
            runtime.records = copy.deepcopy(list(state.get("records") or []))
            runtime.generation = int(state.get("generation") or 0)
            runtime.entries = copy.deepcopy(list(state.get("trace_entries") or []))
            activity = self._activity(runtime.definition, operation, operation_kind)
            identifier = _text(record_id or payload.get("id"))
            current = next(
                (item for item in runtime.records if _record_id(item) == identifier),
                None,
            )
            if expected_revision is not None and operation_kind in {"update", "delete"}:
                if current is None:
                    raise KeyError(identifier)
                actual_revision = current.get("revision", runtime.generation)
                if _text(expected_revision) != _text(actual_revision):
                    raise PrototypeResourceConflict(
                        f"prototype resource revision conflict: expected {expected_revision}, current {actual_revision}"
                    )
            input_value: dict[str, Any]
            if operation_kind == "create":
                record = dict(payload.get("record")) if isinstance(payload.get("record"), Mapping) else dict(payload)
                record.setdefault("id", f"prec.{new_id()}")
                self._default_revision(runtime.definition["record_schema"], record)
                input_value = {"record": record}
            elif operation_kind == "update":
                if not identifier:
                    raise ValueError("prototype resource update requires record_id")
                patch = dict(payload.get("patch")) if isinstance(payload.get("patch"), Mapping) else dict(payload)
                patch.pop("id", None)
                if isinstance(current, Mapping) and "revision" in current and "revision" not in patch:
                    patch["revision"] = int(current.get("revision") or 0) + 1
                input_value = {"id": identifier, "patch": patch}
            elif operation_kind == "delete":
                if not identifier:
                    raise ValueError("prototype resource delete requires record_id")
                input_value = {"id": identifier}
            elif operation_kind == "show":
                if not identifier:
                    raise ValueError("prototype resource show requires record_id")
                input_value = {"id": identifier}
            elif operation_kind == "reset":
                input_value = {}
            else:
                raise ValueError(f"prototype resource operation kind is not mutable: {operation_kind}")
            execution = runtime.execute(activity["activity_id"], input_value)
            state["records"] = runtime.records
            state["generation"] = runtime.generation
            state["trace_entries"] = runtime.entries[-500:]
            registry["resources"][resource_type] = state
            self._write_registry(registry)
        result = execution["result"]
        return {
            "record": _clone(result) if isinstance(result, Mapping) else None,
            "records": _clone(result) if isinstance(result, list) else None,
            "record_id": _text(_record_id(result) if isinstance(result, Mapping) else identifier),
            "deleted": operation_kind == "delete",
            "generation": execution["generation"],
            "prototype": {
                "project_ref": state["project_ref"],
                "change_id": state["change_id"],
                "revision": state["revision"],
                "webui_digest": state["webui_digest"],
            },
        }

    def _state(self, resource_type: str) -> dict[str, Any] | None:
        state = self._read_registry()["resources"].get(_text(resource_type))
        return _clone(state) if isinstance(state, Mapping) else None

    def _require_state(self, resource_type: str) -> dict[str, Any]:
        state = self._state(resource_type)
        if state is None:
            raise ValueError(f"unknown prototype resource_type: {resource_type}")
        return state

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"schema": "adaos.builder.prototype_resource_registry.v1", "resources": {}}
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid prototype resource registry: {exc}") from exc
        resources = value.get("resources") if isinstance(value, Mapping) else None
        return {
            "schema": "adaos.builder.prototype_resource_registry.v1",
            "resources": dict(resources) if isinstance(resources, Mapping) else {},
        }

    def _write_registry(self, registry: Mapping[str, Any]) -> None:
        atomic_write_json(self.registry_path, dict(registry))

    @staticmethod
    def _filter_matches(record: Mapping[str, Any], key: str, expected: Any) -> bool:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        wanted = {_text(value) for value in values if _text(value)}
        return not wanted or _text(_read_path(record, key)) in wanted

    @staticmethod
    def _activity(
        definition: Mapping[str, Any],
        operation: Mapping[str, Any],
        operation_kind: str,
    ) -> dict[str, Any]:
        activities = [dict(item) for item in definition.get("activities") or [] if isinstance(item, Mapping)]
        requested = _text(operation.get("prototype_activity_id"))
        matches = [
            item
            for item in activities
            if (requested and _text(item.get("activity_id")) == requested)
            or (not requested and _text(item.get("activity_id")) == _text(operation.get("id")))
            or (not requested and _text(item.get("operation")) == operation_kind)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"prototype operation {operation.get('id')} must map to exactly one data activity; found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _default_revision(record_schema: Mapping[str, Any], record: dict[str, Any]) -> None:
        properties = record_schema.get("properties") if isinstance(record_schema.get("properties"), Mapping) else {}
        if "revision" in properties:
            record.setdefault("revision", 1)


__all__ = [
    "PROTOTYPE_RESOURCE_SCHEMA",
    "PROTOTYPE_RESOURCE_STATE_SCHEMA",
    "PrototypeResourceConflict",
    "PrototypeResourceService",
]
