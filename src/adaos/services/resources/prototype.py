"""Disposable Resource Workbench provider for executable Builder prototypes."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.artifact_pipeline.storage import mutation_lock
from adaos.services.resources.storage import ResourceStorage
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


def prototype_webui_digest(value: Mapping[str, Any]) -> str:
    """Digest executable Prototype UI while ignoring release bookkeeping."""

    normalized = copy.deepcopy(dict(value))
    ui = normalized.get("ui")
    if isinstance(ui, dict):
        ui.pop("version", None)
    return _digest(normalized)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_path(value: Mapping[str, Any], path: str) -> Any:
    # Literal field IDs may contain dots. Resolve the longest own key before
    # descending, matching Client widget and event-envelope path semantics.
    if not isinstance(value, Mapping):
        return None
    path = _text(path)
    if path in value:
        return value[path]
    tokens = path.split(".")
    for boundary in range(len(tokens) - 1, 0, -1):
        key = ".".join(tokens[:boundary])
        if key in value:
            return _read_path(value[key], ".".join(tokens[boundary:]))
    return None


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
        # Keep Builder's orchestration package out of this provider's import
        # graph; Skill Factory imports developer SDK helpers during startup.
        from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

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
        policy = metadata.get("prototype_policy") or {}
        if not isinstance(policy, Mapping):
            raise ValueError("prototype policy must be an object")
        relationships = policy.get("relationships") or []
        commands = policy.get("commands") or {}
        if not isinstance(commands, dict):
            raise ValueError("prototype commands must be an owned mapping")
        semantic_defs = _schema("webui.semantic.v1.schema.json")["$defs"]
        fields = record_schema.get("properties") or {}
        for command_id, command in commands.items():
            Draft202012Validator({"type": "object", "additionalProperties": False,
                "required": ["kind", "input_field_refs", "fixed_values", "required_field_refs"], "properties": {
                    "kind": {"enum": ["create", "update", "delete", "transition"]},
                    "input_field_refs": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "required_field_refs": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "fixed_values": {"type": "object"}, "guard": {"type": "object", "additionalProperties": False,
                        "required": ["when", "require_nonempty"], "properties": {"when": {"$ref": "#/$defs/condition"},
                            "require_nonempty": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}}}}},
                "$defs": semantic_defs}).validate(command)
            refs = set(command["input_field_refs"]) | set(command["fixed_values"])
            if not set(command["required_field_refs"]) <= set(command["input_field_refs"]):
                raise ValueError("prototype required inputs exceed the command write set")
            if command.get("guard"):
                refs |= set(command["guard"]["require_nonempty"]) | {command["guard"]["when"]["field_ref"]}
            if not command_id or not refs <= set(fields):
                raise ValueError("prototype command references an unknown property")
            for field, fixed_value in command["fixed_values"].items():
                try:
                    Draft202012Validator(fields[field]).validate(fixed_value)
                except ValidationError as exc:
                    raise ValueError('Prototype command fixed effect conflicts with its record schema') from exc
        Draft202012Validator({
            "type": "array", "maxItems": 16,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["field_ref", "target_resource_type", "target_field_ref"],
                      "properties": {"field_ref": {"type": "string", "minLength": 1},
                                     "target_resource_type": {"type": "string", "pattern": "^prototype\\."},
                                     "target_field_ref": {"type": "string", "minLength": 1},
                                     "unique_source": {"type": "boolean"}}},
        }).validate(relationships)
        if any(item["field_ref"] not in record_schema.get("properties", {}) for item in relationships):
            raise ValueError("prototype relationship references an unknown record property")
        condition = policy.get("read_only_when")
        if condition is not None:
            Draft202012Validator({"$ref": "#/$defs/condition", "$defs": _schema("webui.semantic.v1.schema.json")["$defs"]}).validate(condition)
            field = condition["field_ref"]
            properties = record_schema.get("properties") or {}
            if field not in properties:
                raise ValueError("prototype read_only_when references an unknown record property")
            if condition["operator"] in {"equals", "not_equals"}:
                if "value" not in condition:
                    raise ValueError("prototype read_only_when comparison requires a value")
                Draft202012Validator(properties[field]).validate(condition["value"])
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
            previous = self._state(resource_type)
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
            self._store().put(resource_type, state)
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

    def snapshot(self, resource_type: str) -> dict[str, Any] | None:
        """Return one isolated Preview state snapshot for CBS projection."""

        state = self._state(resource_type)
        return _clone(state) if state is not None else None

    def acceptance_snapshots(
        self,
        *,
        project_ref: str,
        change_id: str,
        revision: str,
        webui_digest: str,
        resource_types: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Return exact resource state used by fail-closed Prototype acceptance."""

        snapshots: list[dict[str, Any]] = []
        for resource_type in dict.fromkeys(_text(item) for item in resource_types if _text(item)):
            state = self._require_state(resource_type)
            expected = {
                "project_ref": _text(project_ref),
                "change_id": _text(change_id),
                "revision": _text(revision),
                "webui_digest": _text(webui_digest),
            }
            mismatches = [key for key, value in expected.items() if _text(state.get(key)) != value]
            if mismatches:
                raise PrototypeResourceConflict(
                    f"prototype resource {resource_type} is stale: {', '.join(mismatches)}"
                )
            records = [dict(item) for item in state.get("records") or [] if isinstance(item, Mapping)]
            snapshots.append(
                {
                    "resource_type": resource_type,
                    "bundle_digest": _text(state.get("bundle_digest")),
                    "definition_digest": _text(state.get("definition_digest")),
                    "generation": int(state.get("generation") or 0),
                    "record_count": len(records),
                    "records_digest": _digest(records),
                    "records": _clone(records),
                }
            )
        return snapshots

    def evaluation_snapshots(
        self,
        *,
        project_ref: str,
        revision: str,
        webui_digest: str,
        resource_types: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Return records proven to belong to one executable Prototype revision."""

        snapshots: list[dict[str, Any]] = []
        expected = {
            "project_ref": _text(project_ref),
            "revision": _text(revision),
            "webui_digest": _text(webui_digest),
        }
        for resource_type in dict.fromkeys(
            _text(item) for item in resource_types if _text(item)
        ):
            state = self._require_state(resource_type)
            mismatches = [
                key for key, value in expected.items() if _text(state.get(key)) != value
            ]
            if mismatches:
                raise PrototypeResourceConflict(
                    f"prototype resource {resource_type} is not the evaluated revision: "
                    + ", ".join(mismatches)
                )
            records = [
                dict(item)
                for item in state.get("records") or []
                if isinstance(item, Mapping)
            ]
            snapshots.append(
                {
                    "resource_type": resource_type,
                    "definition_digest": _text(state.get("definition_digest")),
                    "bundle_digest": _text(state.get("bundle_digest")),
                    "generation": int(state.get("generation") or 0),
                    "record_count": len(records),
                    "records_digest": _digest(records),
                    "records": _clone(records),
                }
            )
        return snapshots

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
                if key != "search" and expected is not None and expected != ""
            ):
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
                def sort_value(item: Mapping[str, Any]) -> tuple[int, Any, str]:
                    value = _read_path(item, field)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        return 0, float(value), _record_id(item)
                    if value is None:
                        return 2, "", _record_id(item)
                    return 1, str(value).lower(), _record_id(item)

                result.sort(
                    key=sort_value,
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
        command_id: str | None = None,
    ) -> dict[str, Any]:
        from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._state(resource_type)
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
            # Normalize once, by the operation actually executed. An unrelated
            # wrapper must never be used as evidence for a different write.
            wrapper = {"create": "record", "update": "patch"}.get(operation_kind)
            if wrapper and wrapper in payload and not isinstance(payload[wrapper], Mapping):
                raise ValueError(f"prototype {operation_kind} requires an object {wrapper}")
            values = dict(payload[wrapper]) if wrapper and wrapper in payload else dict(payload)
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
            command_policies = (definition.get("metadata", {}).get("prototype_policy") or {}).get("commands") or {}
            if command_policies and operation_kind in {"create", "update", "delete", "reset"}:
                command = command_policies.get(_text(command_id))
                if command is None:
                    raise PrototypeResourceConflict("prototype mutation requires an admitted command identity")
                expected_kind = "update" if command["kind"] == "transition" else command["kind"]
                if operation_kind != expected_kind:
                    raise PrototypeResourceConflict("prototype command operation mismatch")
                provided = set(values) - {"id", "revision"}
                allowed = set(command["input_field_refs"]) | set(command["fixed_values"])
                if not provided <= allowed or any(values.get(key) != value for key, value in command["fixed_values"].items()):
                    raise PrototypeResourceConflict("prototype command payload exceeds its declared effect")
                guard = command.get("guard")
                # Fixed effects do not alter the condition before the guard is
                # evaluated. Writable draft inputs do, exactly as in the editor.
                before_effect = {**dict(current or {}), **{key: value for key, value in values.items()
                    if key in command["input_field_refs"] and key not in command["fixed_values"]}}
                if any(before_effect.get(key) is None or before_effect.get(key) == "" or before_effect.get(key) == []
                       for key in command["required_field_refs"]):
                    raise PrototypeResourceConflict("prototype command required input rejected the mutation")
                if guard and self._condition_matches(before_effect, guard["when"]):
                    if any(before_effect.get(key) is None or before_effect.get(key) == "" or before_effect.get(key) == []
                           for key in guard["require_nonempty"]):
                        raise PrototypeResourceConflict("prototype command guard rejected the mutation")
            condition = (definition.get("metadata", {}).get("prototype_policy") or {}).get("read_only_when")
            if operation_kind in {"update", "delete"} and current is not None and condition and self._condition_matches(current, condition):
                raise PrototypeResourceConflict("prototype record is read-only in its current state")
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
                record = dict(values)
                record.setdefault("id", f"prec.{new_id()}")
                self._default_revision(runtime.definition["record_schema"], record)
                input_value = {"record": record}
            elif operation_kind == "update":
                if not identifier:
                    raise ValueError("prototype resource update requires record_id")
                patch = dict(values)
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
            if operation_kind in {"create", "update", "delete", "reset"}:
                registry = {"resources": self._store().states(project_ref=state["project_ref"], ui_revision=state["webui_digest"])}
                registry["resources"][resource_type] = state
                self._validate_relationships(registry, state)
            self._store().put(resource_type, state)
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

    @staticmethod
    def _validate_relationships(registry: Mapping[str, Any], changed: Mapping[str, Any]) -> None:
        # The existing registry lock makes reference checks and the write atomic,
        # including inverse references when a target is deleted or renamed.
        resources = registry["resources"]
        def reference_key(value: Any) -> tuple[str, Any]:
            if isinstance(value, bool):
                return "boolean", value
            if isinstance(value, (int, float)):
                return "number", value
            if isinstance(value, str):
                return "string", value
            raise PrototypeResourceConflict("prototype relationship requires scalar keys")
        for state in resources.values():
            if state["project_ref"] != changed["project_ref"] or state["webui_digest"] != changed["webui_digest"]:
                continue
            policy = state["definition"].get("metadata", {}).get("prototype_policy") or {}
            for relation in policy.get("relationships") or []:
                target = resources.get(relation["target_resource_type"])
                if not target or target["project_ref"] != state["project_ref"] or target["webui_digest"] != state["webui_digest"]:
                    raise PrototypeResourceConflict("prototype relationship target is not materialized in this revision")
                target_field = relation["target_field_ref"]
                if target_field not in target["definition"]["record_schema"]["properties"]:
                    raise PrototypeResourceConflict("prototype relationship target property is missing")
                values = [_read_path(record, target_field) for record in target["records"]]
                keys = [reference_key(value) for value in values if value is not None and value != ""]
                if len(keys) != len(set(keys)):
                    raise PrototypeResourceConflict("prototype relationship target values must be unique")
                allowed = set(keys)
                source_keys = []
                for record in state["records"]:
                    value = _read_path(record, relation["field_ref"])
                    if value is not None and value != "":
                        key = reference_key(value)
                        if key not in allowed:
                            raise PrototypeResourceConflict("prototype relationship would reference a missing record")
                        source_keys.append(key)
                if relation.get("unique_source") and len(source_keys) != len(set(source_keys)):
                    raise PrototypeResourceConflict("one-to-one prototype relationship source values must be unique")

    def _state(self, resource_type: str) -> dict[str, Any] | None:
        return self._store().get(_text(resource_type))

    def _require_state(self, resource_type: str) -> dict[str, Any]:
        state = self._state(resource_type)
        if state is None:
            raise ValueError(f"unknown prototype resource_type: {resource_type}")
        return state

    def _read_registry(self) -> dict[str, Any]:
        return {
            "schema": "adaos.builder.prototype_resource_registry.v1",
            "resources": self._store().states(),
        }

    def _store(self) -> ResourceStorage:
        store = ResourceStorage(self.root)
        store.import_json(self.registry_path)
        return store

    @staticmethod
    def _condition_matches(record: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
        value = _read_path(record, condition["field_ref"])
        expected = condition.get("value")
        operator = condition["operator"]
        empty = value is None or value == "" or value == []
        if operator in {"empty", "nonempty"}:
            return empty if operator == "empty" else not empty
        equal = isinstance(value, bool) == isinstance(expected, bool) and value == expected
        return equal if operator == "equals" else not equal

    @staticmethod
    def _filter_matches(record: Mapping[str, Any], key: str, expected: Any) -> bool:
        def token(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value).strip()

        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        wanted = {token(value) for value in values if token(value)}
        return not wanted or token(_read_path(record, key)) in wanted

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
