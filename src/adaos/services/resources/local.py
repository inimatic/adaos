"""Durable skill-owned CRUD resources declared through Resource Workbench."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


LOCAL_RESOURCE_SCHEMA = "adaos.resource.local_crud.v1"
LOCAL_RESOURCE_STATE_SCHEMA = "adaos.resource.local_crud_state.v1"


class LocalResourceConflict(ValueError):
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
        raise ValueError("local resource record requires id")
    return identifier


def declaration_paths(manifest: Mapping[str, Any]) -> list[str]:
    runtime = manifest.get("resource_runtime")
    if not isinstance(runtime, Mapping):
        return []
    if "declarations" not in runtime:
        return []
    values = runtime.get("declarations")
    if not isinstance(values, list):
        raise ValueError("resource_runtime.declarations must be an array")
    paths: list[str] = []
    for value in values:
        token = _text(value)
        if not token:
            raise ValueError("resource_runtime.declarations entries must be non-empty paths")
        relative = PurePosixPath(token.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".json":
            raise ValueError(f"unsafe resource declaration path: {token}")
        normalized = relative.as_posix()
        if normalized in paths:
            raise ValueError(f"duplicate resource declaration path: {normalized}")
        paths.append(normalized)
    return paths


def validate_local_resource_bundle(
    bundle: Mapping[str, Any],
    *,
    expected_owner_ref: str | None = None,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(bundle))
    _validate("resource.local_crud.v1.schema.json", value, label="local CRUD resource")
    definition = dict(value["resource_definition"])
    _validate("resource.definition.v1.schema.json", definition, label="resource definition")
    owner_ref = _text(value.get("owner_ref"))
    if expected_owner_ref and owner_ref != _text(expected_owner_ref):
        raise ValueError(
            f"local resource owner_ref must be {_text(expected_owner_ref)!r}, got {owner_ref!r}"
        )
    owner_name = owner_ref.removeprefix("skill:")
    resource_type = _text(definition.get("resource_type"))
    namespace = f"skill.{owner_name}."
    if not resource_type.startswith(namespace) or resource_type == namespace:
        raise ValueError(f"local resource_type must start with {namespace!r}")
    authority = (
        dict(definition.get("authority"))
        if isinstance(definition.get("authority"), Mapping)
        else {}
    )
    if _text(authority.get("provider")) != "local_crud":
        raise ValueError("local resource authority.provider must be 'local_crud'")
    if _text(authority.get("binding")) != owner_name:
        raise ValueError("local resource authority.binding must equal the owner skill name")
    record_schema = definition.get("record_schema")
    if not isinstance(record_schema, Mapping):
        raise ValueError("local resource definition requires inline record_schema")
    properties = record_schema.get("properties")
    required = record_schema.get("required")
    if not isinstance(properties, Mapping) or "id" not in properties:
        raise ValueError("local resource record_schema must define id")
    if not isinstance(required, list) or "id" not in required:
        raise ValueError("local resource record_schema must require id")
    operation_kinds = {
        _text(item.get("kind"))
        for item in definition.get("operations") or []
        if isinstance(item, Mapping)
    }
    unsupported = sorted(operation_kinds - {"list", "show", "create", "update", "delete"})
    if unsupported:
        raise ValueError(f"unsupported local resource operation kinds: {', '.join(unsupported)}")
    validator = Draft202012Validator(dict(record_schema))
    seen: set[str] = set()
    for index, raw in enumerate(value.get("seed") or []):
        record = dict(raw)
        try:
            validator.validate(record)
        except ValidationError as exc:
            raise ValueError(f"invalid local resource seed[{index}]: {exc.message}") from exc
        identifier = _record_id(record)
        if identifier in seen:
            raise ValueError(f"duplicate local resource seed id: {identifier}")
        seen.add(identifier)
    metadata = (
        dict(definition.get("metadata"))
        if isinstance(definition.get("metadata"), Mapping)
        else {}
    )
    definition["metadata"] = {
        **metadata,
        "owner_ref": owner_ref,
        "runtime": "local_crud",
    }
    value["resource_definition"] = definition
    value.setdefault("seed_policy", "if_missing")
    return value


@dataclass(slots=True)
class LocalCrudResourceService:
    """Persist typed resource data independently from skill package revisions."""

    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "resources" / "local"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".local-resources.lock"

    def load_manifest(
        self,
        skill_name: str,
        manifest: Mapping[str, Any],
        *,
        artifact_root: Path,
    ) -> dict[str, Any]:
        owner_ref = f"skill:{_text(skill_name)}"
        loaded: list[str] = []
        for relative in declaration_paths(manifest):
            root = Path(artifact_root).resolve()
            path = (root / Path(relative)).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"resource declaration escapes skill root: {relative}") from exc
            if not path.is_file():
                raise ValueError(f"resource declaration not found: {relative}")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid resource declaration {relative}: {exc}") from exc
            if not isinstance(value, Mapping):
                raise ValueError(f"resource declaration must be an object: {relative}")
            result = self.materialize(value, expected_owner_ref=owner_ref)
            loaded.append(_text(result["state"].get("resource_type")))
        return {"loaded_resource_total": len(loaded), "resource_types": loaded}

    def materialize(
        self,
        bundle: Mapping[str, Any],
        *,
        expected_owner_ref: str | None = None,
    ) -> dict[str, Any]:
        value = validate_local_resource_bundle(bundle, expected_owner_ref=expected_owner_ref)
        definition = dict(value["resource_definition"])
        resource_type = _text(definition.get("resource_type"))
        owner_ref = _text(value.get("owner_ref"))
        declaration_digest = _digest(value)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read_registry()
            previous = registry["resources"].get(resource_type)
            if isinstance(previous, Mapping):
                previous = copy.deepcopy(dict(previous))
                if _text(previous.get("owner_ref")) != owner_ref:
                    raise ValueError(f"local resource_type is owned by another skill: {resource_type}")
                records = [
                    dict(item)
                    for item in previous.get("records") or []
                    if isinstance(item, Mapping)
                ]
                validator = Draft202012Validator(dict(definition["record_schema"]))
                for record in records:
                    try:
                        validator.validate(record)
                    except ValidationError as exc:
                        raise ValueError(
                            f"local resource schema change requires migration for {resource_type}: {exc.message}"
                        ) from exc
                duplicate = _text(previous.get("declaration_digest")) == declaration_digest
                state = {
                    **previous,
                    "definition": definition,
                    "definition_digest": _digest(definition),
                    "declaration_digest": declaration_digest,
                }
            else:
                duplicate = False
                records = _clone(value.get("seed") or [])
                state = {
                    "schema": LOCAL_RESOURCE_STATE_SCHEMA,
                    "resource_type": resource_type,
                    "owner_ref": owner_ref,
                    "definition": definition,
                    "definition_digest": _digest(definition),
                    "declaration_digest": declaration_digest,
                    "seed_digest": _digest(value.get("seed") or []),
                    "records": records,
                    "generation": 1,
                }
            registry["resources"][resource_type] = state
            self._write_registry(registry)
        return {"ok": True, "duplicate": duplicate, "state": _clone(state)}

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
            if any(
                not self._filter_matches(record, key, expected)
                for key, expected in filters.items()
                if key != "search"
            ):
                continue
            result.append(record)
        for spec in reversed(list(sort or [])):
            if isinstance(spec, Mapping):
                field = _text(spec.get("field") or spec.get("key"))
                descending = _text(spec.get("direction") or spec.get("order")).lower() in {
                    "desc",
                    "descending",
                }
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
                raise ValueError(f"unknown local resource_type: {resource_type}")
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
                raise ValueError(f"unsupported local resource operation: {resource_type}.{operation_id}")
            operation_kind = _text(operation.get("kind") or operation_id)
            records = [dict(item) for item in state.get("records") or [] if isinstance(item, Mapping)]
            identifier = _text(record_id or payload.get("id"))
            index = next(
                (position for position, item in enumerate(records) if _record_id(item) == identifier),
                None,
            )
            current = records[index] if index is not None else None
            if expected_revision is not None and operation_kind in {"update", "delete"}:
                if current is None:
                    raise KeyError(identifier)
                actual_revision = current.get("revision", state.get("generation"))
                if _text(expected_revision) != _text(actual_revision):
                    raise LocalResourceConflict(
                        f"local resource revision conflict: expected {expected_revision}, current {actual_revision}"
                    )
            validator = Draft202012Validator(dict(definition["record_schema"]))
            deleted = False
            result_record: dict[str, Any] | None = None
            result_records: list[dict[str, Any]] | None = None
            if operation_kind == "list":
                result_records = records
            elif operation_kind == "show":
                if not identifier:
                    raise ValueError("local resource show requires record_id")
                if current is None:
                    raise KeyError(identifier)
                result_record = current
            elif operation_kind == "create":
                record = dict(payload.get("record")) if isinstance(payload.get("record"), Mapping) else dict(payload)
                record.setdefault("id", f"rec.{new_id()}")
                self._default_revision(definition["record_schema"], record)
                if any(_record_id(item) == _record_id(record) for item in records):
                    raise LocalResourceConflict(f"local resource record already exists: {_record_id(record)}")
                self._validate_record(validator, record)
                records.append(record)
                result_record = record
            elif operation_kind == "update":
                if not identifier:
                    raise ValueError("local resource update requires record_id")
                if current is None or index is None:
                    raise KeyError(identifier)
                patch = dict(payload.get("patch")) if isinstance(payload.get("patch"), Mapping) else dict(payload)
                patch.pop("id", None)
                updated = {**current, **patch, "id": identifier}
                if "revision" in current:
                    updated["revision"] = int(current.get("revision") or 0) + 1
                self._validate_record(validator, updated)
                records[index] = updated
                result_record = updated
            elif operation_kind == "delete":
                if not identifier:
                    raise ValueError("local resource delete requires record_id")
                if current is None or index is None:
                    raise KeyError(identifier)
                result_record = records.pop(index)
                deleted = True
            else:
                raise ValueError(f"unsupported local resource operation kind: {operation_kind}")
            if operation_kind in {"create", "update", "delete"}:
                state["records"] = records
                state["generation"] = int(state.get("generation") or 0) + 1
                registry["resources"][resource_type] = state
                self._write_registry(registry)
        return {
            "record": _clone(result_record) if result_record is not None else None,
            "records": _clone(result_records) if result_records is not None else None,
            "record_id": _record_id(result_record) if result_record is not None else identifier,
            "deleted": deleted,
            "generation": int(state.get("generation") or 0),
            "owner_ref": state["owner_ref"],
        }

    def _state(self, resource_type: str) -> dict[str, Any] | None:
        state = self._read_registry()["resources"].get(_text(resource_type))
        return _clone(state) if isinstance(state, Mapping) else None

    def _require_state(self, resource_type: str) -> dict[str, Any]:
        state = self._state(resource_type)
        if state is None:
            raise ValueError(f"unknown local resource_type: {resource_type}")
        return state

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"schema": "adaos.resource.local_crud_registry.v1", "resources": {}}
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid local resource registry: {exc}") from exc
        resources = value.get("resources") if isinstance(value, Mapping) else None
        return {
            "schema": "adaos.resource.local_crud_registry.v1",
            "resources": dict(resources) if isinstance(resources, Mapping) else {},
        }

    def _write_registry(self, registry: Mapping[str, Any]) -> None:
        atomic_write_json(self.registry_path, dict(registry))

    @staticmethod
    def _validate_record(validator: Draft202012Validator, record: Mapping[str, Any]) -> None:
        try:
            validator.validate(dict(record))
        except ValidationError as exc:
            raise ValueError(f"invalid local resource record: {exc.message}") from exc

    @staticmethod
    def _filter_matches(record: Mapping[str, Any], key: str, expected: Any) -> bool:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        wanted = {_text(value) for value in values if _text(value)}
        return not wanted or _text(_read_path(record, key)) in wanted

    @staticmethod
    def _default_revision(record_schema: Mapping[str, Any], record: dict[str, Any]) -> None:
        properties = record_schema.get("properties")
        if isinstance(properties, Mapping) and "revision" in properties:
            record.setdefault("revision", 1)


__all__ = [
    "LOCAL_RESOURCE_SCHEMA",
    "LOCAL_RESOURCE_STATE_SCHEMA",
    "LocalCrudResourceService",
    "LocalResourceConflict",
    "declaration_paths",
    "validate_local_resource_bundle",
]
