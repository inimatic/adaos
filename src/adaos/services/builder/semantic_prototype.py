"""Validate and compile Builder semantic Prototype documents."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.ui_capabilities import validate_webui_capabilities

from .prototype_context import prototype_state_requirements
from .workflow import BuilderWorkflowError


SEMANTIC_PROTOTYPE_SCHEMA = "adaos.webui.semantic.v1"
SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA = (
    "adaos.builder.semantic_prototype_candidate.v1"
)
SEMANTIC_COMPILE_RESULT_SCHEMA = "adaos.builder.semantic_compile_result.v1"
_ABI_ROOT = Path(__file__).resolve().parents[2] / "abi"
_FIELD_TYPES = {
    "short_text": "shortText",
    "long_text": "longText",
    "number": "number",
    "date": "date",
    "boolean": "boolean",
    "choice": "singleChoice",
    "attachment": "fileUpload",
}


@lru_cache(maxsize=2)
def _validator(filename: str = "webui.semantic.v1.schema.json") -> Draft202012Validator:
    value = json.loads((_ABI_ROOT / filename).read_text(encoding="utf-8"))
    return Draft202012Validator(value)


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _runtime_resource_type(resource_id: str, project_ref: str | None) -> str:
    if not project_ref:
        return f"prototype.{resource_id}"
    kind, separator, identifier = str(project_ref).strip().partition(":")
    if not separator or not kind or not identifier:
        _fail("project_ref must be a typed reference")
    scope = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{kind}.{identifier}").strip(".-")
    if not scope:
        _fail("project_ref does not provide a runtime resource namespace")
    if len(scope) > 120:
        suffix = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
        scope = f"{scope[:103].rstrip('.-')}-{suffix}"
    return f"prototype.{scope}.{resource_id}"


def _fail(detail: str) -> None:
    raise BuilderWorkflowError(f"invalid semantic Prototype: {detail}")


def _unique(
    values: Sequence[Mapping[str, Any]], label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        identifier = str(item.get("id") or "")
        if identifier in result:
            _fail(f"duplicate {label} id {identifier!r}")
        result[identifier] = dict(item)
    return result


def _state_predicate_matches(
    record: Mapping[str, Any], predicate: Mapping[str, Any]
) -> bool:
    actual = record.get(str(predicate["field_ref"]))
    compare_field_ref = predicate.get("compare_field_ref")
    expected = (
        record.get(str(compare_field_ref))
        if compare_field_ref is not None
        else predicate.get("value")
    )
    operator = str(predicate["operator"])
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    if actual is None or expected is None:
        return False
    try:
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
    except TypeError:
        return False
    return False


def _matching_state_records(
    records: Sequence[Mapping[str, Any]], predicates: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    return [
        record
        for record in records
        if all(_state_predicate_matches(record, predicate) for predicate in predicates)
    ]


def _brief_requirement_ids(brief: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in (
        "principal_jobs",
        "residual_requirements",
        "information_requirements",
        "collection_requirements",
        "operations",
    ):
        result.update(
            str(item.get("id") or "")
            for item in brief.get(key) or []
            if isinstance(item, Mapping) and str(item.get("id") or "")
        )
    result.update(
        str(item["id"])
        for item in prototype_state_requirements(brief)
        if str(item.get("id") or "")
    )
    return result


def _semantic_refs(
    *,
    resource_id: str,
    fields: Mapping[str, Any],
    views: Mapping[str, Any],
    queries: Mapping[str, Any],
    commands: Mapping[str, Any],
    states: Mapping[str, Any],
) -> set[str]:
    return {
        f"resource:{resource_id}",
        *(f"field:{identifier}" for identifier in fields),
        *(f"view:{identifier}" for identifier in views),
        *(f"query:{identifier}" for identifier in queries),
        *(f"command:{identifier}" for identifier in commands),
        *(f"state:{identifier}" for identifier in states),
    }


def validate_semantic_prototype(
    value: Mapping[str, Any], *, brief: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate schema, references, and accepted-requirement coverage."""

    document = copy.deepcopy(dict(value))
    try:
        _validator().validate(document)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    resource = dict(document["resource"])
    fields = _unique(resource["fields"], "field")
    views = _unique(document["views"], "view")
    query_controls: dict[str, dict[str, Any]] = {}
    for view in views.values():
        search_controls = [
            control
            for control in view.get("query_controls") or []
            if control.get("kind") == "search"
        ]
        if len(search_controls) > 1:
            _fail(f"collection view {view['id']!r} has more than one search control")
        filter_fields: set[str] = set()
        for control in view.get("query_controls") or []:
            identifier = str(control.get("id") or "")
            if identifier in query_controls:
                _fail(f"duplicate query control id {identifier!r}")
            if control.get("kind") == "filter":
                field_ref = str(control.get("field_ref") or "")
                if field_ref in filter_fields:
                    _fail(
                        f"collection view {view['id']!r} has duplicate filter for "
                        f"field {field_ref!r}"
                    )
                filter_fields.add(field_ref)
            query_controls[identifier] = dict(control)
    commands = _unique(document["commands"], "command")
    states = _unique(document["representative_states"], "state")
    region_roles = {str(view["region_role"]) for view in views.values()}
    if "primary" not in region_roles:
        _fail("semantic Prototype requires at least one view in the primary region")

    for field in fields.values():
        options = field.get("options")
        if field["value_type"] == "choice" and not options:
            _fail(f"choice field {field['id']!r} requires options")
        if field["value_type"] != "choice" and options:
            _fail(f"non-choice field {field['id']!r} cannot declare options")
        if field.get("multiple") and field["value_type"] != "attachment":
            _fail(f"non-attachment field {field['id']!r} cannot be multiple")
        if field.get("max_items") is not None and not field.get("multiple"):
            _fail(f"field {field['id']!r} requires multiple=true with max_items")
        condition = field.get("visible_when")
        if isinstance(condition, Mapping):
            if condition["field_ref"] not in fields:
                _fail(
                    f"field {field['id']!r} references unknown visibility field "
                    f"{condition['field_ref']!r}"
                )
            if (
                condition["operator"] in {"equals", "not_equals"}
                and "value" not in condition
            ):
                _fail(f"field {field['id']!r} visibility comparison requires value")

    record_ids: set[str] = set()
    allowed_record_keys = {"id", "revision", *fields}
    identity_field_refs = [
        str(item) for item in resource.get("identity_field_refs") or []
    ]
    unknown_identity_fields = sorted(set(identity_field_refs) - {"id"} - set(fields))
    if unknown_identity_fields:
        _fail(f"resource identity references unknown fields {unknown_identity_fields}")
    for index, record in enumerate(resource["records"]):
        identity_values = [record.get(field_id) for field_id in identity_field_refs]
        if any(value in (None, "") for value in identity_values):
            _fail(
                f"resource record {index} requires identity fields {identity_field_refs}"
            )
        record_id = "::".join(str(value).strip() for value in identity_values)
        if not record_id:
            _fail(f"resource record {index} requires a stable id")
        if record_id in record_ids:
            _fail(f"duplicate resource record id {record_id!r}")
        record_ids.add(record_id)
        unknown_keys = sorted(set(record) - allowed_record_keys)
        if unknown_keys:
            _fail(f"resource record {record_id!r} has unknown fields {unknown_keys}")
        for field_id, field in fields.items():
            if field_id not in record or record[field_id] is None:
                continue
            field_value = record[field_id]
            kind = str(field["value_type"])
            if kind == "attachment" and field.get("multiple"):
                valid = isinstance(field_value, list) and all(
                    isinstance(item, str) for item in field_value
                )
                if valid and field.get("max_items") is not None:
                    valid = len(field_value) <= int(field["max_items"])
            else:
                valid = (
                    isinstance(field_value, str)
                    if kind in {"short_text", "long_text", "date", "attachment"}
                    else isinstance(field_value, bool)
                    if kind == "boolean"
                    else isinstance(field_value, (int, float))
                    and not isinstance(field_value, bool)
                    if kind == "number"
                    else any(
                        option["value"] == field_value
                        for option in field.get("options") or []
                    )
                )
            if not valid:
                _fail(
                    f"resource record {record_id!r} has invalid {kind} value for "
                    f"field {field_id!r}"
                )

    for view in views.values():
        unknown_fields = sorted(set(view["field_refs"]) - set(fields))
        if unknown_fields:
            _fail(f"view {view['id']!r} references unknown fields {unknown_fields}")
        filter_value = view.get("filter")
        if (
            isinstance(filter_value, Mapping)
            and filter_value["field_ref"] not in fields
        ):
            _fail(
                f"view {view['id']!r} references unknown filter field "
                f"{filter_value['field_ref']!r}"
            )
        if view["role"] == "editor" and not any(
            fields[field_id]["editable"] for field_id in view["field_refs"]
        ):
            _fail(f"editor view {view['id']!r} has no editable fields")
        for control in view.get("query_controls") or []:
            query_id = str(control["id"])
            if view["role"] != "collection":
                _fail(f"query control {query_id!r} must belong to a collection view")
            if control["kind"] == "filter":
                field_ref = str(control["field_ref"])
                if field_ref not in fields:
                    _fail(
                        f"query control {query_id!r} references unknown field "
                        f"{field_ref!r}"
                    )
                if fields[field_ref]["value_type"] not in {
                    "choice",
                    "date",
                    "short_text",
                }:
                    _fail(
                        f"filter query control {query_id!r} requires a choice, "
                        "date, or short_text field"
                    )

    for state in states.values():
        state_id = str(state["id"])
        view_ref = str(state["view_ref"])
        if view_ref not in views or views[view_ref]["role"] != "collection":
            _fail(
                f"representative state {state_id!r} requires a collection view_ref"
            )
        filters = [dict(item) for item in state.get("filters") or []]
        unknown_state_fields = sorted(
            {
                field_ref
                for predicate in filters
                for field_ref in (
                    str(predicate["field_ref"]),
                    str(predicate.get("compare_field_ref") or ""),
                )
                if field_ref and field_ref not in fields
            }
        )
        if unknown_state_fields:
            _fail(
                f"representative state {state_id!r} filters unknown fields "
                f"{unknown_state_fields}"
            )
        for predicate in filters:
            field_ref = str(predicate["field_ref"])
            operator = str(predicate["operator"])
            expected_value = predicate.get("value")
            field = fields[field_ref]
            compare_field_ref = str(predicate.get("compare_field_ref") or "")
            compare_field = fields.get(compare_field_ref)
            if compare_field is not None and (
                compare_field["value_type"] != field["value_type"]
            ):
                _fail(
                    f"representative state {state_id!r} compares incompatible "
                    f"fields {field_ref!r} and {compare_field_ref!r}"
                )
            if operator in {"lt", "lte", "gt", "gte"} and field[
                "value_type"
            ] not in {"date", "number"}:
                _fail(
                    f"representative state {state_id!r} uses range operator "
                    f"{operator!r} on non-orderable field {field_ref!r}"
                )
            if (
                compare_field is None
                and field["value_type"] == "choice"
                and operator in {"eq", "neq"}
                and not any(
                option["value"] == expected_value for option in field.get("options") or []
                )
            ):
                _fail(
                    f"representative state {state_id!r} has invalid choice filter "
                    f"for field {field_ref!r}"
                )
        minimum = int(state["min_items"])
        maximum = (
            int(state["max_items"])
            if state.get("max_items") is not None
            else None
        )
        if maximum is not None and maximum < minimum:
            _fail(
                f"representative state {state_id!r} max_items is below min_items"
            )
        if filters and minimum < 1:
            _fail(
                f"filtered representative state {state_id!r} requires min_items>=1"
            )
        empty_fixture = not filters and minimum == 0 and maximum == 0
        if empty_fixture and not isinstance(views[view_ref].get("empty_state"), Mapping):
            _fail(
                f"representative state {state_id!r} requires an empty_state "
                f"on collection view {view_ref!r}"
            )
        matching_records = (
            []
            if empty_fixture
            else _matching_state_records(resource["records"], filters)
        )
        count = len(matching_records)
        if count < minimum or (maximum is not None and count > maximum):
            expected_range = (
                f">={minimum}" if maximum is None else f"{minimum}..{maximum}"
            )
            _fail(
                f"representative state {state_id!r} expected {expected_range} "
                f"matching records but found {count}"
            )

    for command in commands.values():
        if command["view_ref"] not in views:
            _fail(
                f"command {command['id']!r} references unknown view {command['view_ref']!r}"
            )
        if views[command["view_ref"]]["role"] != "editor":
            _fail(f"command {command['id']!r} must be owned by an editor view")
        unknown_fields = sorted(set(command["input_field_refs"]) - set(fields))
        if unknown_fields:
            _fail(
                f"command {command['id']!r} references unknown fields {unknown_fields}"
            )
        unknown_fixed_fields = sorted(
            set(command.get("fixed_values") or {}) - set(fields)
        )
        if unknown_fixed_fields:
            _fail(
                f"command {command['id']!r} sets unknown fields {unknown_fixed_fields}"
            )
        guard = command.get("guard")
        if isinstance(guard, Mapping):
            guard_fields = {
                str(guard["when"]["field_ref"]),
                *(str(item) for item in guard["require_nonempty"]),
            }
            unknown_guard_fields = sorted(guard_fields - set(fields))
            if unknown_guard_fields:
                _fail(
                    f"command {command['id']!r} guard references unknown fields "
                    f"{unknown_guard_fields}"
                )
            if (
                guard["when"]["operator"] in {"equals", "not_equals"}
                and "value" not in guard["when"]
            ):
                _fail(f"command {command['id']!r} guard comparison requires value")

    refs = _semantic_refs(
        resource_id=str(resource["id"]),
        fields=fields,
        views=views,
        queries=query_controls,
        commands=commands,
        states=states,
    )
    bindings: dict[str, set[str]] = {}
    for binding in document["requirement_bindings"]:
        requirement_ref = str(binding["requirement_ref"])
        if requirement_ref in bindings:
            _fail(f"duplicate requirement binding {requirement_ref!r}")
        semantic_refs = {str(item) for item in binding["semantic_refs"]}
        unknown_refs = sorted(semantic_refs - refs)
        if unknown_refs:
            _fail(f"requirement {requirement_ref!r} has unresolved refs {unknown_refs}")
        bindings[requirement_ref] = semantic_refs

    gaps = {str(item["requirement_ref"]) for item in document["capability_gaps"]}
    overlap = sorted(set(bindings) & gaps)
    if overlap:
        _fail(f"requirements cannot be both bound and capability gaps: {overlap}")
    if brief is not None:
        if document["brief_ref"] != brief.get("brief_id"):
            _fail("brief_ref does not match the supplied Prototype Brief")
        if document["brief_digest"] != brief.get("digest"):
            _fail("brief_digest does not match the supplied Prototype Brief")
        required = _brief_requirement_ids(brief)
        missing = sorted(required - set(bindings) - gaps)
        unexpected = sorted((set(bindings) | gaps) - required)
        if missing:
            _fail(f"accepted requirements have no semantic binding or gap: {missing}")
        if unexpected:
            _fail(f"semantic document references unknown requirements: {unexpected}")
        for operation in brief.get("operations") or []:
            if not isinstance(operation, Mapping):
                continue
            operation_id = str(operation.get("id") or "")
            operation_kind = str(operation.get("kind") or "")
            if operation_kind not in {"search", "filter"} or operation_id in gaps:
                continue
            matching_queries = {
                f"query:{query_id}"
                for query_id, control in query_controls.items()
                if control["kind"] == operation_kind
            }
            if not bindings.get(operation_id, set()) & matching_queries:
                _fail(
                    f"{operation_kind} requirement {operation_id!r} must bind a "
                    f"{operation_kind} query control"
                )
        collection_requirements = {
            str(item["id"]): dict(item)
            for item in brief.get("collection_requirements") or []
            if isinstance(item, Mapping) and str(item.get("id") or "")
        }
        collection_views = {
            f"view:{view['id']}"
            for view in views.values()
            if view["role"] == "collection"
        }
        editor_views = {
            f"view:{view['id']}" for view in views.values() if view["role"] == "editor"
        }
        resource_ref = f"resource:{resource['id']}"
        for requirement_id, requirement in collection_requirements.items():
            if requirement_id in gaps:
                continue
            bound = bindings.get(requirement_id, set())
            if resource_ref not in bound or not (bound & collection_views):
                _fail(
                    f"collection requirement {requirement_id!r} must bind the item "
                    "resource and a collection view"
                )
            if requirement.get("interaction") == "capture_each":
                bound_editors = bound & editor_views
                if not bound_editors:
                    _fail(
                        f"capture_each requirement {requirement_id!r} must bind an editor view"
                    )

    return document


def semantic_prototype_contract() -> dict[str, Any]:
    """Return the immutable model-facing semantic document contract."""

    return copy.deepcopy(_validator().schema)


def semantic_prototype_candidate_contract() -> dict[str, Any]:
    """Return the strict, bounded provider-output contract."""

    return copy.deepcopy(
        _validator("builder.semantic_prototype_candidate.v1.schema.json").schema
    )


def semantic_prototype_provider_contract() -> dict[str, Any]:
    """Return the candidate schema projected to the provider strict subset."""

    contract = semantic_prototype_candidate_contract()
    unsupported_validation_keywords = {
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "uniqueItems",
    }
    schema_map_keys = {"$defs", "definitions", "properties"}
    schema_list_keys = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_value_keys = {
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
    }

    def project(node: Any) -> None:
        if isinstance(node, dict):
            for keyword in unsupported_validation_keywords:
                node.pop(keyword, None)
            for key, child in node.items():
                if key in schema_map_keys and isinstance(child, dict):
                    for property_schema in child.values():
                        project(property_schema)
                elif key in schema_list_keys and isinstance(child, list):
                    for branch in child:
                        project(branch)
                elif key in schema_value_keys and isinstance(child, dict):
                    project(child)

    project(contract)
    return contract


def _field_entries(
    entries: Sequence[Mapping[str, Any]], *, owner: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in entries:
        field_ref = str(entry.get("field_ref") or "")
        if field_ref in result:
            _fail(f"{owner} repeats field {field_ref!r}")
        result[field_ref] = copy.deepcopy(entry.get("value"))
    return result


def _canonical_candidate_identifier(value: Any, *, namespace: str) -> str:
    raw = str(value or "").strip()
    canonical = re.sub(r"[^A-Za-z0-9_.-]+", ".", raw)
    canonical = re.sub(r"[.]+", ".", canonical).strip(".-")
    if not canonical:
        canonical = namespace
    if not canonical[0].isalpha() or not canonical[0].isascii():
        canonical = f"{namespace}.{canonical}"
    return canonical


def _runtime_state_identifier(value: Any, *, fallback: str) -> str:
    raw = str(value or "").strip()
    canonical = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not canonical:
        canonical = fallback
    if not canonical[0].isalpha() or not canonical[0].isascii():
        canonical = f"{fallback}_{canonical}"
    return canonical


def _candidate_identifier_map(
    values: Sequence[Any],
    *,
    namespace: str,
    normalizations: list[dict[str, str]],
    targets: Sequence[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    owners: dict[str, str] = {}
    for raw_value, target in zip(values, targets, strict=True):
        raw = str(raw_value or "").strip()
        canonical = _canonical_candidate_identifier(raw, namespace=namespace)
        previous = owners.get(canonical)
        if previous is not None and previous != raw:
            _fail(
                f"candidate {namespace} identifiers {previous!r} and {raw!r} "
                f"normalize to the same id {canonical!r}"
            )
        owners[canonical] = raw
        result[raw] = canonical
        if canonical != raw:
            normalizations.append(
                {
                    "kind": "candidate_identifier",
                    "namespace": namespace,
                    "from": raw,
                    "to": canonical,
                    "target": target,
                }
            )
    return result


def _mapped_candidate_ref(
    value: Any,
    *,
    namespace: str,
    identifiers: Mapping[str, str],
    normalizations: list[dict[str, str]],
    target: str,
) -> str:
    raw = str(value or "").strip()
    canonical = identifiers.get(raw)
    if canonical is None:
        canonical = _canonical_candidate_identifier(raw, namespace=namespace)
    if canonical != raw:
        normalizations.append(
            {
                "kind": "candidate_reference",
                "namespace": namespace,
                "from": raw,
                "to": canonical,
                "target": target,
            }
        )
    return canonical


def _normalize_candidate_localization_keys(
    value: Any,
    *,
    normalizations: list[dict[str, str]],
    path: str = "$",
    owners: dict[str, str] | None = None,
) -> None:
    if owners is None:
        owners = {}
    if isinstance(value, dict):
        if {"key", "en", "ru"}.issubset(value):
            raw = str(value.get("key") or "").strip()
            canonical = _canonical_candidate_identifier(raw, namespace="text")
            previous = owners.get(canonical)
            if previous is not None and previous != raw:
                _fail(
                    f"candidate localization keys {previous!r} and {raw!r} "
                    f"normalize to the same id {canonical!r}"
                )
            owners[canonical] = raw
            if canonical != raw:
                value["key"] = canonical
                normalizations.append(
                    {
                        "kind": "candidate_identifier",
                        "namespace": "localized_text",
                        "from": raw,
                        "to": canonical,
                        "target": f"{path}.key",
                    }
                )
        for key, child in value.items():
            _normalize_candidate_localization_keys(
                child,
                normalizations=normalizations,
                path=f"{path}.{key}",
                owners=owners,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _normalize_candidate_localization_keys(
                child,
                normalizations=normalizations,
                path=f"{path}[{index}]",
                owners=owners,
            )


def _canonicalize_semantic_prototype_candidate(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    candidate = copy.deepcopy(dict(value))
    try:
        Draft202012Validator(semantic_prototype_provider_contract()).validate(
            candidate
        )
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    normalizations: list[dict[str, str]] = []
    resource = candidate["resource"]
    fields = resource["fields"]
    views = candidate["views"]
    commands = candidate["commands"]
    states = candidate["representative_states"]

    document_ids = _candidate_identifier_map(
        [candidate["document_id"]],
        namespace="prototype",
        normalizations=normalizations,
        targets=["$.document_id"],
    )
    resource_ids = _candidate_identifier_map(
        [resource["id"]],
        namespace="resource",
        normalizations=normalizations,
        targets=["$.resource.id"],
    )
    field_ids = _candidate_identifier_map(
        [field["id"] for field in fields],
        namespace="field",
        normalizations=normalizations,
        targets=[f"$.resource.fields[{index}].id" for index in range(len(fields))],
    )
    view_ids = _candidate_identifier_map(
        [view["id"] for view in views],
        namespace="view",
        normalizations=normalizations,
        targets=[f"$.views[{index}].id" for index in range(len(views))],
    )
    query_entries = [
        (control, f"$.views[{view_index}].query_controls[{control_index}]")
        for view_index, view in enumerate(views)
        for control_index, control in enumerate(view["query_controls"])
    ]
    query_ids = _candidate_identifier_map(
        [control["id"] for control, _ in query_entries],
        namespace="query",
        normalizations=normalizations,
        targets=[f"{path}.id" for _, path in query_entries],
    )
    command_ids = _candidate_identifier_map(
        [command["id"] for command in commands],
        namespace="command",
        normalizations=normalizations,
        targets=[f"$.commands[{index}].id" for index in range(len(commands))],
    )
    state_ids = _candidate_identifier_map(
        [state["id"] for state in states],
        namespace="state",
        normalizations=normalizations,
        targets=[
            f"$.representative_states[{index}].id" for index in range(len(states))
        ],
    )

    candidate["document_id"] = document_ids[str(candidate["document_id"]).strip()]
    resource["id"] = resource_ids[str(resource["id"]).strip()]
    for index, field in enumerate(fields):
        field["id"] = field_ids[str(field["id"]).strip()]
        condition = field.get("visible_when")
        if isinstance(condition, dict):
            condition["field_ref"] = _mapped_candidate_ref(
                condition["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=f"$.resource.fields[{index}].visible_when.field_ref",
            )
    resource["identity_field_refs"] = [
        _mapped_candidate_ref(
            field_ref,
            namespace="field",
            identifiers=field_ids,
            normalizations=normalizations,
            target=f"$.resource.identity_field_refs[{index}]",
        )
        for index, field_ref in enumerate(resource["identity_field_refs"])
    ]
    for record_index, record in enumerate(resource["records"]):
        for entry_index, entry in enumerate(record["values"]):
            entry["field_ref"] = _mapped_candidate_ref(
                entry["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=(
                    f"$.resource.records[{record_index}].values[{entry_index}].field_ref"
                ),
            )

    runtime_state_ids: dict[str, str] = {}
    runtime_state_owners: dict[str, str] = {}
    for view_index, view in enumerate(views):
        view["id"] = view_ids[str(view["id"]).strip()]
        view["field_refs"] = [
            _mapped_candidate_ref(
                field_ref,
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=f"$.views[{view_index}].field_refs[{field_index}]",
            )
            for field_index, field_ref in enumerate(view["field_refs"])
        ]
        view_filter = view.get("filter")
        if isinstance(view_filter, dict):
            view_filter["field_ref"] = _mapped_candidate_ref(
                view_filter["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=f"$.views[{view_index}].filter.field_ref",
            )
            raw_state_ref = str(view_filter["state_ref"] or "").strip()
            canonical_state_ref = runtime_state_ids.setdefault(
                raw_state_ref,
                _runtime_state_identifier(raw_state_ref, fallback="query_state"),
            )
            previous_state_ref = runtime_state_owners.get(canonical_state_ref)
            if previous_state_ref is not None and previous_state_ref != raw_state_ref:
                _fail(
                    f"candidate query_state identifiers {previous_state_ref!r} and "
                    f"{raw_state_ref!r} normalize to the same id "
                    f"{canonical_state_ref!r}"
                )
            runtime_state_owners[canonical_state_ref] = raw_state_ref
            if canonical_state_ref != raw_state_ref:
                normalizations.append(
                    {
                        "kind": "candidate_reference",
                        "namespace": "query_state",
                        "from": raw_state_ref,
                        "to": canonical_state_ref,
                        "target": f"$.views[{view_index}].filter.state_ref",
                    }
                )
            view_filter["state_ref"] = canonical_state_ref
        for control_index, control in enumerate(view["query_controls"]):
            control["id"] = query_ids[str(control["id"]).strip()]
            if control.get("field_ref") is not None:
                control["field_ref"] = _mapped_candidate_ref(
                    control["field_ref"],
                    namespace="field",
                    identifiers=field_ids,
                    normalizations=normalizations,
                    target=(
                        f"$.views[{view_index}].query_controls[{control_index}].field_ref"
                    ),
                )

    for command_index, command in enumerate(commands):
        command["id"] = command_ids[str(command["id"]).strip()]
        command["view_ref"] = _mapped_candidate_ref(
            command["view_ref"],
            namespace="view",
            identifiers=view_ids,
            normalizations=normalizations,
            target=f"$.commands[{command_index}].view_ref",
        )
        command["input_field_refs"] = [
            _mapped_candidate_ref(
                field_ref,
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=f"$.commands[{command_index}].input_field_refs[{field_index}]",
            )
            for field_index, field_ref in enumerate(command["input_field_refs"])
        ]
        for entry_index, entry in enumerate(command["fixed_values"]):
            entry["field_ref"] = _mapped_candidate_ref(
                entry["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=(
                    f"$.commands[{command_index}].fixed_values[{entry_index}].field_ref"
                ),
            )
        guard = command.get("guard")
        if isinstance(guard, dict):
            guard["when"]["field_ref"] = _mapped_candidate_ref(
                guard["when"]["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=f"$.commands[{command_index}].guard.when.field_ref",
            )
            guard["require_nonempty"] = [
                _mapped_candidate_ref(
                    field_ref,
                    namespace="field",
                    identifiers=field_ids,
                    normalizations=normalizations,
                    target=(
                        f"$.commands[{command_index}].guard.require_nonempty[{field_index}]"
                    ),
                )
                for field_index, field_ref in enumerate(guard["require_nonempty"])
            ]

    for state_index, state in enumerate(states):
        state["id"] = state_ids[str(state["id"]).strip()]
        state["view_ref"] = _mapped_candidate_ref(
            state["view_ref"],
            namespace="view",
            identifiers=view_ids,
            normalizations=normalizations,
            target=f"$.representative_states[{state_index}].view_ref",
        )
        for filter_index, predicate in enumerate(state["filters"]):
            predicate["field_ref"] = _mapped_candidate_ref(
                predicate["field_ref"],
                namespace="field",
                identifiers=field_ids,
                normalizations=normalizations,
                target=(
                    f"$.representative_states[{state_index}].filters[{filter_index}].field_ref"
                ),
            )
            operand = predicate["operand"]
            if operand["kind"] == "field" and operand.get("field_ref") is not None:
                operand["field_ref"] = _mapped_candidate_ref(
                    operand["field_ref"],
                    namespace="field",
                    identifiers=field_ids,
                    normalizations=normalizations,
                    target=(
                        f"$.representative_states[{state_index}].filters[{filter_index}].operand.field_ref"
                    ),
                )

    semantic_namespaces = {
        "resource": resource_ids,
        "field": field_ids,
        "view": view_ids,
        "query": query_ids,
        "command": command_ids,
        "state": state_ids,
    }
    for binding_index, binding in enumerate(candidate["requirement_bindings"]):
        normalized_refs: list[str] = []
        for ref_index, raw_ref in enumerate(binding["semantic_refs"]):
            semantic_kind = str(raw_ref["kind"])
            raw_identifier = str(raw_ref["id"] or "").strip()
            identifiers = semantic_namespaces[semantic_kind]
            identifier_candidates = (raw_identifier, f"{semantic_kind}:{raw_identifier}")
            canonical_identifier = next(
                (
                    identifiers[item]
                    for item in identifier_candidates
                    if item in identifiers
                ),
                _canonical_candidate_identifier(
                    raw_identifier,
                    namespace=semantic_kind,
                ),
            )
            canonical_ref = f"{semantic_kind}:{canonical_identifier}"
            source_ref = f"{semantic_kind}:{raw_identifier}"
            if canonical_ref != source_ref:
                normalizations.append(
                    {
                        "kind": "candidate_semantic_reference",
                        "namespace": semantic_kind,
                        "from": source_ref,
                        "to": canonical_ref,
                        "target": (
                            f"$.requirement_bindings[{binding_index}].semantic_refs[{ref_index}]"
                        ),
                    }
                )
            normalized_refs.append(canonical_ref)
        binding["semantic_refs"] = normalized_refs

    _normalize_candidate_localization_keys(
        candidate,
        normalizations=normalizations,
    )
    return candidate, normalizations


def _lower_semantic_prototype_candidate(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(value))
    candidate["schema"] = SEMANTIC_PROTOTYPE_SCHEMA
    candidate["layout"] = {"pattern": candidate["layout"]}
    resource = dict(candidate["resource"])
    resource["fields"] = []
    for raw_field in candidate["resource"]["fields"]:
        field = dict(raw_field)
        if field["value_type"] == "attachments":
            field["value_type"] = "attachment"
            field["multiple"] = True
        if not field.get("options"):
            field.pop("options", None)
        if field.get("visible_when") is None:
            field.pop("visible_when", None)
        resource["fields"].append(field)
    resource["records"] = [
        _field_entries(record["values"], owner=f"resource record {index}")
        for index, record in enumerate(candidate["resource"]["records"])
    ]
    candidate["resource"] = resource

    views: list[dict[str, Any]] = []
    for raw_view in candidate["views"]:
        view = dict(raw_view)
        if view.get("filter") is None:
            view.pop("filter", None)
        controls: list[dict[str, Any]] = []
        for raw_control in view.get("query_controls") or []:
            control = dict(raw_control)
            if control.get("field_ref") is None:
                control.pop("field_ref", None)
            controls.append(control)
        if controls:
            view["query_controls"] = controls
        else:
            view.pop("query_controls", None)
        if view.get("empty_state") is None:
            view.pop("empty_state", None)
        elif view["empty_state"].get("detail") is None:
            view["empty_state"].pop("detail", None)
        views.append(view)
    candidate["views"] = views

    commands: list[dict[str, Any]] = []
    for raw_command in candidate["commands"]:
        command = dict(raw_command)
        if command.get("confirmation") is None:
            command.pop("confirmation", None)
        fixed_values = _field_entries(
            command.get("fixed_values") or [],
            owner=f"command {command['id']!r} fixed_values",
        )
        if fixed_values:
            command["fixed_values"] = fixed_values
        else:
            command.pop("fixed_values", None)
        if command.get("guard") is None:
            command.pop("guard", None)
        commands.append(command)
    candidate["commands"] = commands

    for state in candidate["representative_states"]:
        normalized_filters: list[dict[str, Any]] = []
        for raw_predicate in state["filters"]:
            predicate = dict(raw_predicate)
            operand = dict(predicate.pop("operand"))
            if operand["kind"] == "field":
                compare_field_ref = operand.get("field_ref")
                if compare_field_ref is None:
                    _fail(
                        f"representative state {state['id']!r} field operand "
                        "requires field_ref"
                    )
                predicate["compare_field_ref"] = compare_field_ref
            else:
                if operand.get("field_ref") is not None:
                    _fail(
                        f"representative state {state['id']!r} value operand "
                        "requires field_ref=null"
                    )
                predicate["value"] = copy.deepcopy(operand.get("value"))
            normalized_filters.append(predicate)
        state["filters"] = normalized_filters
        if state.get("max_items") is None:
            state.pop("max_items", None)
    return candidate


def normalize_semantic_prototype_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Lower a strict provider candidate into the canonical semantic ABI."""

    candidate, _ = _canonicalize_semantic_prototype_candidate(value)
    return validate_semantic_prototype(_lower_semantic_prototype_candidate(candidate))


def compile_semantic_prototype_candidate(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Validate, lower, and compile one strict provider candidate."""

    candidate, normalizations = _canonicalize_semantic_prototype_candidate(value)
    semantic_document = _lower_semantic_prototype_candidate(candidate)
    result = compile_semantic_prototype(
        semantic_document,
        brief=brief,
        project_ref=project_ref,
    )
    result["semantic_document"] = semantic_document
    result["normalizations"] = normalizations
    return result


def _localized(
    value: Mapping[str, Any], dictionaries: dict[str, dict[str, str]]
) -> tuple[str, dict[str, str]]:
    key = str(value["key"])
    dictionaries["en"][key] = str(value["en"])
    dictionaries["ru"][key] = str(value["ru"])
    return str(value["en"]), {"key": key, "fallback": str(value["en"])}


def _condition_expression(condition: Mapping[str, Any]) -> str:
    ref = str(condition["field_ref"])
    operator = str(condition["operator"])
    value = condition.get("value")
    if operator == "nonempty":
        return f"$state.{ref}"
    if operator == "empty":
        return f"!$state.{ref}"
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    token = "===" if operator == "equals" else "!=="
    return f"$state.{ref} {token} {encoded}"


def _guard_expression(guard: Mapping[str, Any]) -> str:
    when = dict(guard["when"])
    requirement = " && ".join(
        f"$state.{field_ref} && $state.{field_ref}.length > 0"
        for field_ref in guard["require_nonempty"]
    )
    condition = _condition_expression(when)
    if when["operator"] == "equals":
        inverse = _condition_expression({**when, "operator": "not_equals"})
    elif when["operator"] == "not_equals":
        inverse = _condition_expression({**when, "operator": "equals"})
    elif when["operator"] == "nonempty":
        inverse = _condition_expression({**when, "operator": "empty"})
    else:
        inverse = _condition_expression({**when, "operator": "nonempty"})
    return f"{inverse} || ({condition} && {requirement})"


def compile_semantic_prototype(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Compile a validated semantic document into canonical Prototype artifacts."""

    document = validate_semantic_prototype(value, brief=brief)
    dictionaries: dict[str, dict[str, str]] = {"en": {}, "ru": {}}
    resource = dict(document["resource"])
    fields = {str(item["id"]): dict(item) for item in resource["fields"]}
    commands = {str(item["id"]): dict(item) for item in document["commands"]}
    region_roles = {str(view["region_role"]) for view in document["views"]}
    resource_type = _runtime_resource_type(str(resource["id"]), project_ref)
    selection_namespace = _runtime_state_identifier(
        resource["id"], fallback="resource"
    )
    selection_ref = f"selected_{selection_namespace}_id"
    initial_state: dict[str, Any] = {selection_ref: ""}
    widgets: list[dict[str, Any]] = []
    source_map: dict[str, list[str]] = {
        f"resource:{resource['id']}": [
            "ui.application.desktop.pageSchema.widgets[*].dataSource.resourceType"
        ]
    }
    prototype_records = []
    for record in resource["records"]:
        runtime_record = copy.deepcopy(dict(record))
        runtime_record["id"] = "::".join(
            str(record[field_id]).strip()
            for field_id in resource["identity_field_refs"]
        )
        prototype_records.append(runtime_record)

    title, title_i18n = _localized(document["title"], dictionaries)
    item_label, _item_label_i18n = _localized(resource["item_label"], dictionaries)
    del item_label

    for view in document["views"]:
        view_id = str(view["id"])
        role = str(view["role"])
        view_title, view_title_i18n = _localized(view["title"], dictionaries)
        widget: dict[str, Any] = {
            "id": view_id,
            "area": str(view["region_role"]),
            "title": view_title,
            "title_i18n": view_title_i18n,
            "dataSource": {
                "kind": "resourceQuery",
                "resourceType": resource_type,
                "query": {},
            },
        }
        for control in view.get("query_controls") or []:
            query_id = str(control["id"])
            state_ref = "query_" + re.sub(r"[^A-Za-z0-9_]+", "_", query_id)
            initial_state.setdefault(state_ref, "")
            query_label, query_label_i18n = _localized(
                control["label"], dictionaries
            )
            query_widget: dict[str, Any] = {
                "id": f"query-{query_id}",
                "area": str(view["region_role"]),
                "title": query_label,
                "title_i18n": query_label_i18n,
                "actions": [
                    {
                        "id": f"set-{query_id}",
                        "on": "change",
                        "type": "updateState",
                        "params": {state_ref: "$event.value"},
                    }
                ],
            }
            if control["kind"] == "search":
                query_widget.update(
                    {
                        "type": "input.text",
                        "inputs": {
                            "label": query_label,
                            "label_i18n": query_label_i18n,
                            "inputType": "search",
                            "initialValue": "",
                            "clearable": True,
                        },
                    }
                )
                widget["dataSource"]["query"]["search"] = f"$state.{state_ref}"
            elif fields[str(control["field_ref"])]["value_type"] == "choice":
                field = fields[str(control["field_ref"])]
                all_label, all_label_i18n = _localized(
                    {
                        "key": f"{control['label']['key']}.all",
                        "en": "All",
                        "ru": "Все",
                    },
                    dictionaries,
                )
                options = [
                    {
                        "value": "",
                        "label": all_label,
                        "label_i18n": all_label_i18n,
                    }
                ]
                for option in field.get("options") or []:
                    option_label, option_label_i18n = _localized(
                        option["label"], dictionaries
                    )
                    options.append(
                        {
                            "value": option["value"],
                            "label": option_label,
                            "label_i18n": option_label_i18n,
                        }
                    )
                query_widget.update(
                    {
                        "type": "input.selector",
                        "inputs": {
                            "label": query_label,
                            "label_i18n": query_label_i18n,
                            "defaultValue": f"$state.{state_ref}",
                            "options": options,
                            "optionValuePath": "value",
                            "optionLabelPath": "label",
                            "searchable": len(options) > 8,
                        },
                    }
                )
                widget["dataSource"]["query"].setdefault("filters", {})[
                    str(control["field_ref"])
                ] = f"$state.{state_ref}"
            else:
                input_type = (
                    "date"
                    if fields[str(control["field_ref"])]["value_type"] == "date"
                    else "text"
                )
                query_widget.update(
                    {
                        "type": "input.text",
                        "inputs": {
                            "label": query_label,
                            "label_i18n": query_label_i18n,
                            "inputType": input_type,
                            "initialValue": "",
                            "clearable": True,
                        },
                    }
                )
                widget["dataSource"]["query"].setdefault("filters", {})[
                    str(control["field_ref"])
                ] = f"$state.{state_ref}"
            widgets.append(query_widget)
            source_map[f"query:{query_id}"] = [
                f"ui.application.desktop.pageSchema.widgets.@query-{query_id}"
            ]
        filter_value = view.get("filter")
        if isinstance(filter_value, Mapping):
            state_ref = str(filter_value["state_ref"])
            initial_state.setdefault(state_ref, "")
            widget["dataSource"]["query"].setdefault("filters", {})[
                str(filter_value["field_ref"])
            ] = f"$state.{state_ref}"
        if role == "collection":
            widget["type"] = "ui.list"
            title_key = next(
                (
                    field_id
                    for field_id in view["field_refs"]
                    if fields[field_id]["value_type"] in {"short_text", "long_text"}
                ),
                "id",
            )
            widget["inputs"] = {
                "variant": "list",
                "itemIdKey": "id",
                "titleKey": title_key,
            }
            if title_key != "id":
                source_map.setdefault(f"field:{title_key}", []).append(
                    f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.titleKey"
                )
            widget["actions"] = [
                {
                    "id": f"select-{view_id}",
                    "on": "select",
                    "type": "updateState",
                    "params": {selection_ref: "$event.id"},
                }
            ]
            empty_state = view.get("empty_state")
            if isinstance(empty_state, Mapping):
                empty_title, empty_title_i18n = _localized(
                    empty_state["title"], dictionaries
                )
                rendered_empty: dict[str, Any] = {
                    "title": empty_title,
                    "title_i18n": empty_title_i18n,
                }
                if isinstance(empty_state.get("detail"), Mapping):
                    detail, detail_i18n = _localized(
                        empty_state["detail"], dictionaries
                    )
                    rendered_empty.update(
                        {"subtitle": detail, "subtitle_i18n": detail_i18n}
                    )
                widget["inputs"]["emptyState"] = rendered_empty
        elif role == "details":
            widget["type"] = "item.details"
            widget["selectedStateKey"] = selection_ref
            widget["inputs"] = {"fields": []}
            widget["dataSource"]["query"]["id"] = f"$state.{selection_ref}"
            for field_id in view["field_refs"]:
                label, label_i18n = _localized(fields[field_id]["label"], dictionaries)
                widget["inputs"]["fields"].append(
                    {"id": field_id, "label": label, "label_i18n": label_i18n}
                )
                source_map.setdefault(f"field:{field_id}", []).append(
                    f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.fields.@{field_id}"
                )
        else:
            widget["type"] = "ui.form"
            widget["inputs"] = {"layout": "responsiveGrid", "fields": [], "buttons": []}
            widget["dataSource"]["query"]["id"] = f"$state.{selection_ref}"
            for field_id in view["field_refs"]:
                field = fields[field_id]
                if not field["editable"]:
                    continue
                label, label_i18n = _localized(field["label"], dictionaries)
                rendered_field: dict[str, Any] = {
                    "id": field_id,
                    "title": label,
                    "title_i18n": label_i18n,
                    "type": _FIELD_TYPES[str(field["value_type"])],
                    "required": bool(field["required"]),
                }
                if field.get("multiple"):
                    rendered_field["multiple"] = True
                    if field.get("max_items") is not None:
                        rendered_field["maxFiles"] = int(field["max_items"])
                if field.get("options"):
                    rendered_field["options"] = []
                    for option in field["options"]:
                        option_label, option_i18n = _localized(
                            option["label"], dictionaries
                        )
                        rendered_field["options"].append(
                            {
                                "value": option["value"],
                                "label": option_label,
                                "label_i18n": option_i18n,
                            }
                        )
                if isinstance(field.get("visible_when"), Mapping):
                    rendered_field["visibleIf"] = _condition_expression(
                        field["visible_when"]
                    )
                widget["inputs"]["fields"].append(rendered_field)
                source_map.setdefault(f"field:{field_id}", []).append(
                    f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.fields.@{field_id}"
                )

            actions: list[dict[str, Any]] = []
            for command_id, command in commands.items():
                if command["view_ref"] != view_id:
                    continue
                label, label_i18n = _localized(command["label"], dictionaries)
                button: dict[str, Any] = {
                    "id": command_id,
                    "label": label,
                    "label_i18n": label_i18n,
                    "kind": "primary"
                    if command["kind"] == "transition"
                    else "secondary",
                }
                action: dict[str, Any] = {
                    "id": command_id,
                    "on": "submit",
                    "type": "resourceOperation",
                    "target": resource_type,
                    "params": {
                        "operation_id": "update"
                        if command["kind"] == "transition"
                        else command["kind"],
                        "payload": {
                            field_id: f"$event.values.{field_id}"
                            for field_id in command["input_field_refs"]
                        },
                    },
                }
                confirmation = command.get("confirmation")
                if not isinstance(confirmation, Mapping) and command["kind"] in {
                    "transition",
                    "delete",
                }:
                    confirmation = {
                        "key": f"{command['label']['key']}.confirmation",
                        "en": f"Confirm {command['label']['en']}?",
                        "ru": f"Подтвердить «{command['label']['ru']}»?",
                    }
                if isinstance(confirmation, Mapping):
                    confirmation_message, confirmation_message_i18n = _localized(
                        confirmation, dictionaries
                    )
                    action["confirmation"] = {
                        "message": confirmation_message,
                        "message_i18n": confirmation_message_i18n,
                        "confirmLabel": label,
                        "confirmLabel_i18n": label_i18n,
                    }
                action["params"]["payload"].update(
                    copy.deepcopy(dict(command.get("fixed_values") or {}))
                )
                if command["kind"] != "create":
                    action["params"]["record_id"] = f"$state.{selection_ref}"
                if command["kind"] == "delete":
                    action["params"].pop("payload", None)
                if isinstance(command.get("guard"), Mapping):
                    expression = _guard_expression(command["guard"])
                    button["enabledIf"] = expression
                    action["enabledIf"] = expression
                widget["inputs"]["buttons"].append(button)
                actions.append(action)
            if actions:
                widget["actions"] = actions

        widgets.append(widget)
        source_map[f"view:{view_id}"] = [
            f"ui.application.desktop.pageSchema.widgets.@{view_id}"
        ]
        for command_id, command in commands.items():
            if command["view_ref"] != view_id:
                continue
            source_map[f"command:{command_id}"] = [
                f"ui.application.desktop.pageSchema.widgets.@{view_id}.actions.@{command_id}"
            ]

    representative_state_checks: list[dict[str, Any]] = []
    for state in document["representative_states"]:
        filters = [dict(item) for item in state.get("filters") or []]
        minimum = int(state["min_items"])
        maximum = (
            int(state["max_items"])
            if state.get("max_items") is not None
            else None
        )
        empty_fixture = not filters and minimum == 0 and maximum == 0
        matching_records = (
            []
            if empty_fixture
            else _matching_state_records(prototype_records, filters)
        )
        state_id = str(state["id"])
        view_ref = str(state["view_ref"])
        source_map[f"state:{state_id}"] = [
            (
                f"ui.application.desktop.pageSchema.widgets.@{view_ref}.inputs.emptyState"
                if empty_fixture
                else f"ui.application.desktop.pageSchema.widgets.@{view_ref}"
            )
        ]
        representative_state_checks.append(
            {
                "state_id": state_id,
                "view_ref": view_ref,
                "filters": filters,
                "matching_record_ids": [record["id"] for record in matching_records],
                "matching_record_count": len(matching_records),
                "min_items": minimum,
                "max_items": maximum,
                "fixture_mode": "empty" if empty_fixture else "filtered_records",
                "ok": True,
            }
        )

    layout_type, layout_pattern = {
        "flow": ("stack", "stack"),
        "split": ("split", "split"),
        "grid": ("grid", "grid"),
        "focus_detail": ("split", "focus-detail"),
    }[str(document["layout"]["pattern"])]
    page_schema = {
        "id": str(document["document_id"]),
        "title": title,
        "title_i18n": title_i18n,
        "layout": {
            "type": layout_type,
            "pattern": layout_pattern,
            "areas": [
                {
                    "id": region_role,
                    "role": "main" if region_role == "primary" else "auxiliary",
                }
                for region_role in ("primary", "supporting", "actions")
                if region_role in region_roles
            ],
        },
        "widgets": widgets,
        "initialState": initial_state,
        "meta": {
            "builder": {
                "semantic_source": SEMANTIC_PROTOTYPE_SCHEMA,
                "semantic_digest": _digest(document),
                "brief_ref": document["brief_ref"],
            }
        },
    }
    webui = {
        "schema": "adaos.webui.v1",
        "generated_by": "builder.semantic_compiler.v1",
        "ui": {"application": {"desktop": {"pageSchema": page_schema}}},
    }
    try:
        _validator("webui.v1.schema.json").validate(webui)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"compiled WebUI is invalid{suffix}: {exc.message}")
    validation = validate_webui_capabilities(webui)
    if not validation.get("ok"):
        _fail(
            "compiled WebUI failed capability validation: "
            + json.dumps(validation.get("findings") or [], ensure_ascii=False)
        )

    views_by_ref = {f"view:{view['id']}": view for view in document["views"]}
    queries_by_view = {
        str(view["id"]): [
            f"query:{control['id']}" for control in view.get("query_controls") or []
        ]
        for view in document["views"]
    }
    binding_expansions: dict[str, list[str]] = {}
    requirement_map: dict[str, list[str]] = {}
    for item in document["requirement_bindings"]:
        requirement_ref = str(item["requirement_ref"])
        explicit_refs = {str(ref) for ref in item["semantic_refs"]}
        expanded_refs = set(explicit_refs)
        for semantic_ref in explicit_refs:
            view = views_by_ref.get(semantic_ref)
            if view is None:
                continue
            expanded_refs.update(f"field:{field_id}" for field_id in view["field_refs"])
            expanded_refs.update(queries_by_view.get(str(view["id"]), []))
            expanded_refs.update(
                f"command:{command_id}"
                for command_id, command in commands.items()
                if command["view_ref"] == view["id"]
            )
        derived_refs = sorted(expanded_refs - explicit_refs)
        if derived_refs:
            binding_expansions[requirement_ref] = derived_refs
        requirement_map[requirement_ref] = sorted(
            {
                runtime_ref
                for semantic_ref in expanded_refs
                for runtime_ref in source_map.get(semantic_ref, [])
            }
        )
    unresolved_runtime = sorted(
        requirement_ref
        for requirement_ref, runtime_refs in requirement_map.items()
        if not runtime_refs
    )
    if unresolved_runtime:
        _fail(f"requirements compile to no runtime nodes: {unresolved_runtime}")

    return {
        "schema": SEMANTIC_COMPILE_RESULT_SCHEMA,
        "semantic_digest": _digest(document),
        "webui": webui,
        "locale_dictionaries": dictionaries,
        "prototype_records": prototype_records,
        "representative_state_checks": representative_state_checks,
        "source_map": source_map,
        "requirement_runtime_map": requirement_map,
        "binding_expansions": binding_expansions,
        "capability_gaps": copy.deepcopy(document["capability_gaps"]),
        "validation": validation,
    }


__all__ = [
    "SEMANTIC_COMPILE_RESULT_SCHEMA",
    "SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA",
    "SEMANTIC_PROTOTYPE_SCHEMA",
    "compile_semantic_prototype_candidate",
    "compile_semantic_prototype",
    "normalize_semantic_prototype_candidate",
    "semantic_prototype_candidate_contract",
    "semantic_prototype_provider_contract",
    "semantic_prototype_contract",
    "validate_semantic_prototype",
]
