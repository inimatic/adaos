"""Validate and compile Builder semantic Prototype documents."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.ui_capabilities import validate_webui_capabilities

from .prototype_context import prototype_state_requirements, prototype_requirement_inventory
from .prototype_stage import automation_obligations, PROTOTYPE_STAGE_CONTRACT
from .prototype_contracts import STATE_PROOF_RULES
from .workflow import BuilderWorkflowError
from .semantic_presentations import legacy_view, view_extras, presentation_findings, compile_presentations
from .semantic_query_scope import compile_query_scopes, legacy_state, scope_findings, scoped_predicates
from .semantic_selection import compile_selection_filters, selection_findings


FILTER_VALUE_TYPES = frozenset({"boolean", "choice", "date", "number", "short_text"})
SEMANTIC_PROTOTYPE_SCHEMA = "adaos.webui.semantic.v1"
SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA = (
    "adaos.builder.semantic_prototype_candidate.v1"
)
SEMANTIC_PROTOTYPE_V2_SCHEMA = "adaos.webui.semantic.v2"
SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA = (
    "adaos.builder.semantic_prototype_candidate.v2"
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
    "multi_choice": "multiChoice",
    "attachment": "fileUpload",
}


@lru_cache(maxsize=8)
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


class SemanticPrototypeValidationError(BuilderWorkflowError):
    """Semantic validation failure with model-actionable structured findings."""

    def __init__(self, findings: Sequence[Mapping[str, Any]]) -> None:
        self.findings = [copy.deepcopy(dict(item)) for item in findings]
        detail = "; ".join(str(item.get("detail") or "") for item in self.findings)
        super().__init__(f"invalid semantic Prototype: {detail}")


def _requirement_contract_findings(
    value: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> list[dict[str, Any]]:
    binding_refs = [
        str(item.get("requirement_ref") or "")
        for item in value.get("requirement_bindings") or []
        if isinstance(item, Mapping)
    ]
    gap_refs = [
        str(item.get("requirement_ref") or "")
        for item in value.get("capability_gaps") or []
        if isinstance(item, Mapping)
    ]
    bound = {item for item in binding_refs if item}
    gaps = {item for item in gap_refs if item}
    required = _brief_requirement_ids(brief)
    findings: list[dict[str, Any]] = []
    eligible_automation_refs = {
        str(item["id"])
        for group in ("principal_jobs", "residual_requirements")
        for item in brief.get(group) or []
    }
    for index, obligation in enumerate(value.get("automation_requirements") or []):
        reference = str(obligation.get("requirement_ref") or "")
        if reference not in eligible_automation_refs:
            findings.append({
                "code": "requirement.automation_reference_ineligible",
                "path": f"$.automation_requirements[{index}].requirement_ref",
                "requirement_refs": [reference],
                "detail": (
                    f"automation requirement {reference!r} must reference an accepted "
                    "job or residual requirement, not a UI operation. Preserve the "
                    "operation's prototype binding; defer the related business job. "
                    f"Eligible refs: {sorted(eligible_automation_refs)}"
                ),
            })

    overlap = sorted(bound & gaps)
    if overlap:
        findings.append(
            {
                "code": "requirement.binding_and_gap",
                "path": "$.requirement_bindings|$.capability_gaps",
                "requirement_refs": overlap,
                "detail": f"requirements cannot be both bound and capability gaps: {overlap}",
            }
        )
    missing = sorted(required - bound - gaps)
    if missing:
        findings.append(
            {
                "code": "requirement.coverage_missing",
                "path": "$.requirement_bindings|$.capability_gaps",
                "requirement_refs": missing,
                "detail": (
                    "accepted requirements have no semantic binding or gap: "
                    f"{missing}"
                ),
            }
        )
    unexpected = sorted((bound | gaps) - required)
    if unexpected:
        findings.append(
            {
                "code": "requirement.reference_unknown",
                "path": "$.requirement_bindings[*].requirement_ref",
                "requirement_refs": unexpected,
                "detail": (
                    "semantic document references unknown requirements: "
                    f"{unexpected}"
                ),
            }
        )
    duplicate_gaps = sorted(
        {item for item in gap_refs if item and gap_refs.count(item) > 1}
    )
    if duplicate_gaps:
        findings.append(
            {
                "code": "requirement.gap_duplicate",
                "path": "$.capability_gaps",
                "requirement_refs": duplicate_gaps,
                "detail": f"duplicate capability gaps: {duplicate_gaps}",
            }
        )
    return findings


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


def _field_value_is_valid(field: Mapping[str, Any], field_value: Any) -> bool:
    kind = str(field["value_type"])
    if kind == "attachment" and field.get("multiple"):
        valid = isinstance(field_value, list) and all(
            isinstance(item, str) for item in field_value
        )
        if valid and field.get("max_items") is not None:
            valid = len(field_value) <= int(field["max_items"])
        return valid
    if kind in {"short_text", "long_text", "date", "attachment"}:
        return isinstance(field_value, str)
    if kind == "boolean":
        return isinstance(field_value, bool)
    if kind == "number":
        return isinstance(field_value, (int, float)) and not isinstance(
            field_value, bool
        )
    if kind == "multi_choice":
        option_values = {option["value"] for option in field.get("options") or []}
        return (
            isinstance(field_value, list)
            and len(field_value) == len(set(field_value))
            and all(item in option_values for item in field_value)
        )
    return any(
        option["value"] == field_value for option in field.get("options") or []
    )


def _relationship_field_types_compatible(
    from_field: Mapping[str, Any], to_field: Mapping[str, Any] | None
) -> bool:
    target = (
        to_field
        if to_field is not None
        else {"value_type": "short_text", "options": []}
    )
    if str(from_field["value_type"]) == str(target["value_type"]):
        return True
    if str(from_field["value_type"]) != "choice":
        return False
    return all(
        _field_value_is_valid(target, option["value"])
        for option in from_field.get("options") or []
    )


def _candidate_choice_value(
    field: Mapping[str, Any], value: Any
) -> tuple[Any, bool]:
    options = [
        dict(item)
        for item in field.get("options") or []
        if isinstance(item, Mapping)
    ]
    if any(option.get("value") == value for option in options):
        return value, False
    if not isinstance(value, str):
        return value, False
    token = value.strip().casefold()
    matches = [
        option.get("value")
        for option in options
        if any(
            isinstance(option.get("label"), Mapping)
            and str(option["label"].get(locale) or "").strip().casefold() == token
            for locale in ("en", "ru")
        )
    ]
    if len(matches) != 1:
        return value, False
    return matches[0], True


def _normalize_candidate_fixture_values(
    *,
    fields: Sequence[Mapping[str, Any]],
    records: Sequence[dict[str, Any]],
    path: str,
    normalizations: list[dict[str, str]],
) -> None:
    for record_index, record in enumerate(records):
        values = list(record.get("values") or [])
        for field_index, field in enumerate(fields):
            if field_index >= len(values):
                continue
            kind = str(field.get("value_type") or "")
            original = values[field_index]
            normalization_kind = "localized_choice_value"
            if kind in {"attachments", "multi_choice"} and isinstance(original, str):
                try:
                    decoded = json.loads(original)
                except ValueError:
                    decoded = None
                if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
                    continue
                values[field_index] = decoded
                normalizations.append({
                    "kind": "typed_json_array",
                    "from": json.dumps(original, ensure_ascii=False),
                    "to": json.dumps(decoded, ensure_ascii=False),
                    "target": f"{path}[{record_index}].values[{field_index}]",
                })
                original = decoded
            if kind == "choice":
                normalized, changed = _candidate_choice_value(field, original)
            elif kind == "multi_choice" and isinstance(original, list):
                normalized_values: list[Any] = []
                changed = False
                for item in original:
                    normalized_item, item_changed = _candidate_choice_value(field, item)
                    normalized_values.append(normalized_item)
                    changed = changed or item_changed
                normalized = normalized_values
            elif kind in {"number", "boolean"} and isinstance(original, str):
                if original.strip() == "" and not field.get("required"):
                    normalized = None
                else:
                    try:
                        normalized = json.loads(original)
                    except ValueError:
                        continue
                    if kind == "number" and not (isinstance(normalized, (int, float)) and not isinstance(normalized, bool) and math.isfinite(normalized)):
                        continue
                    if kind == "boolean" and not isinstance(normalized, bool):
                        continue
                changed = True
                normalization_kind = "typed_json_scalar"
            else:
                continue
            if not changed:
                continue
            values[field_index] = normalized
            normalizations.append(
                {
                    "kind": normalization_kind,
                    "from": json.dumps(original, ensure_ascii=False),
                    "to": json.dumps(normalized, ensure_ascii=False),
                    "target": f"{path}[{record_index}].values[{field_index}]",
                }
            )
        record["values"] = values


def _brief_requirement_ids(brief: Mapping[str, Any]) -> set[str]:
    return {item["id"] for item in prototype_requirement_inventory(brief)}


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


def _validate_semantic_prototype_v1(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    require_primary: bool = True,
    record_state_ids: frozenset[str] = frozenset(),
    lookup_only: bool = False,
) -> dict[str, Any]:
    """Validate schema, references, and accepted-requirement coverage."""

    document = copy.deepcopy(dict(value))
    try:
        if lookup_only:
            schema = copy.deepcopy(_validator().schema)
            schema["properties"]["views"]["minItems"] = 0
            Draft202012Validator(schema).validate(document)
            if require_primary or document["views"] or document["commands"] or document["representative_states"]:
                _fail("lookup-only validation requires an internal resource slice without views or commands")
        else:
            _validator().validate(document)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    resource = dict(document["resource"])
    fields = _unique(resource["fields"], "field")
    condition = resource.get("read_only_when")
    if isinstance(condition, Mapping):
        if condition["field_ref"] not in fields:
            _fail("resource read_only_when references an unknown field")
        if condition["operator"] in {"equals", "not_equals"}:
            if "value" not in condition or (condition["value"] is not None and not _field_value_is_valid(fields[condition["field_ref"]], condition["value"])):
                _fail("resource read_only_when comparison requires a typed field value")
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
    if require_primary and "primary" not in region_roles:
        _fail("semantic Prototype requires at least one view in the primary region")

    for field in fields.values():
        if field["id"] == "id" and (field["editable"] or field["value_type"] != "short_text"):
            _fail("the explicit id field is read-only string record metadata; use a business field for editable identifiers")
        options = field.get("options")
        if field["value_type"] in {"choice", "multi_choice"} and not options:
            _fail(f"choice field {field['id']!r} requires options")
        if field["value_type"] not in {"choice", "multi_choice"} and options:
            _fail(f"non-choice field {field['id']!r} cannot declare options")
        if field.get("multiple") and field["value_type"] != "attachment":
            _fail(f"non-attachment field {field['id']!r} cannot be multiple")
        if field.get("max_items") is not None and not field.get("multiple"):
            _fail(f"field {field['id']!r} requires multiple=true with max_items")
        if field.get("display_format") == "markdown" and field["value_type"] != "long_text":
            _fail("markdown display requires a long_text field")
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
            if not _field_value_is_valid(field, field_value):
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
            transitions = [command for command in commands.values() if command["view_ref"] == view["id"]]
            if not transitions or any(command["input_field_refs"] or not (
                command["fixed_values"] or command["kind"] == "delete"
            ) for command in transitions):
                _fail(f"editor view {view['id']!r} has no editable fields or fixed transition commands")
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
                if fields[field_ref]["value_type"] not in FILTER_VALUE_TYPES:
                    _fail(
                        f"filter query control {query_id!r} requires a boolean, "
                        "choice, date, number, or short_text field"
                    )

    for state in states.values():
        state_id = str(state["id"])
        view_ref = str(state["view_ref"])
        allowed_roles = {"collection", "details", "editor"} if state_id in record_state_ids else {"collection"}
        if view_ref not in views or views[view_ref]["role"] not in allowed_roles:
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
            if field["value_type"] == "multi_choice":
                _fail(
                    f"representative state {state_id!r} cannot predicate on "
                    f"multi_choice field {field_ref!r}"
                )
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
        if filters and minimum == 0 and maximum != 0:
            _fail(
                f"filtered representative state {state_id!r} with min_items=0 "
                "requires max_items=0"
            )
        empty_fixture = minimum == 0 and maximum == 0
        if empty_fixture and not isinstance(views[view_ref].get("empty_state"), Mapping):
            _fail(
                f"representative state {state_id!r} requires an empty_state "
                f"on collection view {view_ref!r}"
            )
        matching_records = (
            []
            if empty_fixture and not filters
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


def semantic_prototype_contract(*, version: str = "v1") -> dict[str, Any]:
    """Return the immutable model-facing semantic document contract."""

    filename = "webui.semantic.v2.schema.json" if version == "v2" else "webui.semantic.v1.schema.json"
    return copy.deepcopy(_validator(filename).schema)


def semantic_prototype_candidate_contract(*, version: str = "v1") -> dict[str, Any]:
    """Return the strict, bounded provider-output contract."""

    filename = (
        "builder.semantic_prototype_candidate.v2.schema.json"
        if version == "v2"
        else "builder.semantic_prototype_candidate.v1.schema.json"
    )
    return copy.deepcopy(_validator(filename).schema)


_PROVIDER_OMITTED_ASSERTIONS = frozenset({
    "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern", "uniqueItems",
})


def semantic_prototype_provider_contract(*, version: str = "v1", locales: Sequence[str] = ("en", "ru"), brief: Mapping[str, Any] | None = None, _view_variants: bool = True) -> dict[str, Any]:
    """Return the candidate schema projected to the provider strict subset."""

    contract = semantic_prototype_candidate_contract(version=version)
    resource_schema = contract["$defs"]["resource"] if version == "v2" else contract["properties"]["resource"]
    resource_schema["required"].append("read_only_when")
    requested_locales = list(dict.fromkeys(locales))
    if not requested_locales or set(requested_locales) - {"en", "ru"}:
        raise ValueError("Prototype locales must be a nonempty subset of en, ru")
    text_schema = contract["$defs"]["localizedText"]
    text_schema.pop("anyOf", None)
    text_schema["required"] = requested_locales
    text_schema["properties"] = {locale: text_schema["properties"][locale] for locale in requested_locales}
    if version == "v2":
        contract["required"].append("automation_requirements")
        contract["$defs"]["relationship"]["required"].append("label_field_refs")
        contract["$defs"]["view"]["required"].append("surface")
        contract["$defs"]["view"]["required"].append("media")
        contract["$defs"]["view"]["required"].extend(["presentation_options", "field_display", "section", "scope_filters", "selection_filter"])
        if _view_variants:
            # Record projections cannot use collection presentations or query links.
            # Keep retained authoring/patch shapes compatible; constrain fresh output.
            collection = copy.deepcopy(contract["$defs"]["view"])
            collection["properties"]["role"] = {"type": "string", "enum": ["collection"]}
            collection["properties"]["surface"] = {"type": "string", "enum": ["inline"]}
            collection["properties"]["presentation"] = copy.deepcopy(
                collection["properties"]["presentation"]["anyOf"][0]
            )
            record = copy.deepcopy(contract["$defs"]["view"])
            record["properties"]["role"] = {"type": "string", "enum": ["details", "editor"]}
            for name in ("presentation", "presentation_options", "selection_filter", "filter", "empty_state"):
                record["properties"][name] = {"type": "null"}
            contract["$defs"]["view"] = {"anyOf": [collection, record]}
        if brief is not None:
            inventory = prototype_requirement_inventory(brief)
            for name, allowed in (
                ("requirementBinding", [item["id"] for item in inventory]),
                ("capabilityGap", [item["id"] for item in inventory]),
                ("automationRequirement", [item["id"] for item in inventory if item["kind"] in {"job", "residual"}]),
            ):
                if allowed:
                    contract["$defs"][name]["properties"]["requirement_ref"] = {"type": "string", "enum": allowed}
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
            for keyword in _PROVIDER_OMITTED_ASSERTIONS:
                node.pop(keyword, None)
            if "$ref" in node:
                # Provider references cannot carry JSON Schema annotations.
                # Keep their prose in the authoring ABI and generation guidance.
                for keyword in ("description", "title", "$comment", "default", "examples"):
                    node.pop(keyword, None)
                if set(node) != {"$ref"}:
                    _fail("provider schema reference has unsupported assertion siblings")
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


def semantic_prototype_generation_guidance() -> dict[str, Any]:
    contract = semantic_prototype_candidate_contract(version="v2")
    constraints: dict[str, Any] = {}

    def collect_bounds(node: Any, pointer: str) -> None:
        if not isinstance(node, dict):
            return
        bounds = {key: value for key, value in node.items() if key in _PROVIDER_OMITTED_ASSERTIONS}
        if bounds:
            constraints[pointer] = bounds
        for key, value in node.items():
            if isinstance(value, dict):
                collect_bounds(value, f"{pointer}/{key}")
            elif isinstance(value, list):
                for index, branch in enumerate(value):
                    collect_bounds(branch, f"{pointer}/{key}/{index}")

    collect_bounds(contract, "#")
    return {
        "contract": SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA,
        "stage": copy.deepcopy(PROTOTYPE_STAGE_CONTRACT),
        "limits": {
            key: {name: value for name, value in descriptor.items() if name in {"minItems", "maxItems"}}
            for key, descriptor in contract["properties"].items() if descriptor.get("type") == "array"
        },
        "records_per_resource": contract["$defs"]["resource"]["properties"]["records"]["maxItems"],
        "authoring_constraints": constraints,
        "fixture_values": {
            "id": "Record id is implicit read-only string metadata; do not add an id field unless it is needed for display. If declared, it must be noneditable short_text and match record.id. It is not a business field to rename or edit.",
            "number": "JSON number, e.g. 12.5, not the string \"12.5\"; optional missing values use null",
            "attachment": "one string reference, or null when optional; never an array",
            "attachments": "array of string references, [] when empty; never a scalar string",
            "multi_choice": "unique array of option.value; never labels",
            "choice": "one option.value; never a translated label. An unassigned value is null only when required=false; do not put null into a required field or invent an undeclared choice value.",
            "record_order": "values follow fields order exactly; include each field once",
        },
        "relationships": contract["$defs"]["relationship"]["properties"]["to_field_ref"]["description"],
        "modeling": "Use separate resources for independently editable repeated concepts, including links; a fixed vocabulary may use choice options. Field IDs are unique within their resource; Core owner-qualifies repeated names. A field binding with a repeated name needs one owning resource/view/command or the resource.field ID. Each independently browsed resource needs a collection for record selection; details alone cannot select a record. A resource used only by another editor's relationship selector may omit views; declare safe target label_field_refs. Relationship inputs must be editable when creating or changing links. Do not flatten repeated records into numbered fields or long text. Use two to four records per populated resource, fewer when sufficient; no empty placeholder records.",
        "coverage": "Use the Brief required_references once each. Bind local mutations to their command. Ownership edges command -> view -> resource are resolved by Core; for collection requirements Core also includes the unique owned collection/editor. If several views share a role, bind the intended view explicitly. A relationship assignment may create a link or update a foreign key. Bind search/filter operations to exact query IDs. Search uses field_ref=null. Automation defers only a job or residual reference from the inventory, with a visible view/state binding; its related local operation remains executable. Do not defer an operation reference or use a resource alone as visible disclosure.",
        "query_filters": {"field_types": sorted(FILTER_VALUE_TYPES), "operator": "equality",
                          "placement": "query_controls, filter and empty_state belong to collection views only. Details and editors have query_controls=[] and filter=null; put search on their owning collection. Equality filters accept only field_types, not long_text, markdown or array fields. Search has field_ref=null; use it for free text rather than adding an unsupported equality filter."},
        "command_ownership": "Every command, including delete or a fixed-value transition, belongs to an editor view. A collection or details view is not a command owner. For a focused action use an editor with surface=modal/side_sheet and the necessary context fields; Core provides its opener and selected record. Editable inputs must be included in both the editor's field_refs and the command's input_field_refs.",
        "view_roles": "A collection browses repeated records and owns its presentation, query controls, empty state and selection links. Details projects fields from one selected record of the SAME resource; it is not a grouped collection or relationship lookup. An editor owns commands and their inputs. Details/editor have presentation=null, presentation_options=null, selection_filter=null, filter=null, empty_state=null and query_controls=[], field_display=[], scope_filters=[]. Use a collection, not details, for selectable or grouped summaries.",
        "selection_links": "When selecting a row or tree node must change another collection, set that target's selection_filter={field_ref: target field, source_view_ref: source collection id, source_field_ref: selected source field (null means id)}. Parent-to-children uses target FK/source id; selected child-to-parent uses target id/source FK. Both require the declared FK/id relationship, not matching names. Links must be acyclic. Core owns selection state and clears descendant selections when their parent changes; do not guess state_ref names. No selection shows all records. A dropdown is not following a selected row. Do not expose other filters on this linked field. Prefer selection_filter over legacy filter.",
        "deferred_computations": "When a requested computation or rule is deferred, show plausible representative OUTPUT values and their meaning in an inspectable view. A description or raw inputs alone do not illustrate the requested result. Clearly disclose that these values are fixtures, not live calculations. Do not build data concepts used only by future Automation.",
        "command_guards": "Guards reference fields of the command's own editor resource only. A predicate over several related records is not a single-record field guard; preserve such business rules for Automation with visible representative outcomes.",
        "state_proofs": copy.deepcopy(STATE_PROOF_RULES),
        "state_rules": "States are test cases of the same UI, not separate resources. collection_empty runs that collection with an empty response fixture; keep its normal populated records and declare empty_state. Never clone a resource or add a separate Samples collection just to demonstrate emptiness. Other proofs count normal fixtures satisfying ALL predicates. States do not inherit other states' filters; view.filter is a user-controlled value, not a fixed base predicate. query_empty needs a reachable combination of equality filters with zero matches; choice values must be declared options. Predicate fields must be visible. An illustrative result is not a business computation. Choose proofs relevant to the request, not one of each kind.",
        "interactions": "Reuse local CRUD, live relationship selectors, query controls, confirmation and field guards. Commands belong to an editor; selection/details require a collection, but lookup-only resources need no standalone view. Foreign-key collections need a reachable relationship filter when the workflow requires inspecting one selected item's linked records; an unfiltered list of raw IDs does not provide that workflow. resource.read_only_when locks matching stored records against update/delete in the UI and local provider, independently of draft edits. Do not generate implementation code for these primitives. Details-only fields provide on-demand disclosure; markdown fields render sanitized formatted text and are edited as plain Markdown source.",
        "media": "A filename field alone never renders media. Use view.media on details for an actual image/video/audio viewer: source_field_ref, optional kind_field_ref (values image/video/audio), optional poster_field_ref. A collection cover must be an image; mixed-media collections should set poster_field_ref to a cover-image field. Built-in fixture references: sample://image, sample://video, sample://document (downloadable text), sample://unavailable. Do not invent local paths for files that do not exist. attachment/attachments fields capture real local bytes, store references and render download links in details; documents do not require mediaKey or an image viewer. Loading/error are native viewer states, not mandatory collection state predicates; do not invent statuses or a proof for native loading.",
        "ux_recommendations": {
            "collection_presentations": "Use board for lanes of a choice field; presentation_options.draggable enables persisted moves between lanes, not ordering inside a lane. Use tree for nullable parent record ids, accordion for expandable groups, chart for one numeric point per record (group_field_ref=x, value_field_ref=y). Include lane/group/x/y fields in field_refs; a chart has only x and y. Charts do not calculate aggregates. Plain lists/tables/cards remain valid choices. These are capabilities, not a mandatory checklist.",
            "sections": "Plan the requested content partitions before filling views. view.section is a visibility partition, NOT a resource or application category. If the user requests separate views of the same records, give each requested tab its own section.id and title while reusing resource_ref. Views with the SAME section.id are visible TOGETHER; null is visible across ALL tabs. Put contextual details and editor openers in the appropriate section unless intentionally shared. Settings contain real local resources/commands, not automatically implemented external effects; even a single settings record currently needs a collection for selection plus its editor. Do not invent additional sections just to fill the screen.",
            "query_toolbar": "Each collection's query_controls compile into one compact responsive search/filter toolbar with disclosure, active values and reset. Do not create separate resources or views for filter widgets.",
            "text": "Text wraps by default in list/card metadata and table cells. field_display can explicitly request wrap or truncate and start/center/end alignment per visible field on list/table/cards/accordion collections ONLY; other presentations, details and editors use field_display=[]. Keep essential values readable; use truncation only for compact summaries with details available.",
            "query_scope": "scope_filters define permanent equality constraints for a collection, not user filter defaults. They survive reset. Use them when a tab must always show only a subset. Query controls narrow that scope on other fields; never reuse its field for a resettable filter. Representative states count records inside the permanent scope. A section title alone does not filter records.",
            "editor_inputs": "Only fields consumed by this editor's command input_field_refs are writable here. Other listed fields are read-only context; fixed_values are not editable inputs. A field may be writable in one editor and read-only in another.",
            "layout": "layout=flow stacks regions; split/focus_detail places primary beside supporting on desktop, stacked on mobile; grid groups equal-priority regions. region_role is actual placement: primary for the main task, supporting for selected details or secondary work, actions for a footer. Putting every view in primary creates one long column even in split. Prefer one primary collection and contextual details; reserve flow for genuinely linear work. Supporting is a real region, not merely a label.",
            "editor_surface": "Use surface=modal for a short focused create/edit task, side_sheet when surrounding context matters, inline for a persistent work area. Collections and details stay inline. The compiler owns openers, selection, form hydration, save/error and dismissal. No surface is mandatory for acceptance.",
            "progressive_disclosure": "Keep the main screen focused on the user's primary job. Put secondary fields in details and consider an on-demand editor instead of showing every form at once. Do not add hypothetical features or multiply views only to look complete.",
        },
    }


def _validate_candidate_bounds(candidate: Mapping[str, Any]) -> None:
    # Provider projection omits these keywords; Core still enforces authoring limits.
    errors = _validator("builder.semantic_prototype_candidate.v2.schema.json").iter_errors(candidate)
    findings = [
        {"code": "semantic.candidate_bounds", "path": "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path),
         "detail": f"{error.validator}={error.validator_value}, actual length={len(error.instance)}"}
        for error in errors if error.validator in {"minItems", "maxItems", "maxLength", "uniqueItems"}
    ]
    for resource_index, resource in enumerate(candidate.get("resources") or []):
        field_ids = [field["id"] for field in resource["fields"]]
        for record_index, record in enumerate(resource["records"]):
            if len(record["values"]) != len(field_ids):
                findings.append({"code": "semantic.record_arity", "path": f"$.resources[{resource_index}].records[{record_index}].values",
                                 "expected_field_refs": field_ids,
                                 "detail": f"resource {resource['id']!r} record {record_index} has {len(record['values'])} values for {len(field_ids)} fields; expected order: {field_ids}"})
    if findings:
        raise SemanticPrototypeValidationError(findings)


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


def _candidate_localized_text(value: Mapping[str, Any], *, key: str) -> dict[str, str]:
    return {
        "key": _canonical_candidate_identifier(key, namespace="text"),
        **{locale: str(value[locale]) for locale in ("en", "ru") if locale in value},
    }


def _materialize_candidate_localization_keys(candidate: dict[str, Any]) -> None:
    document_id = str(candidate["document_id"])
    candidate["title"] = _candidate_localized_text(
        candidate["title"], key=f"prototype.{document_id}.title"
    )

    resource = candidate["resource"]
    resource_id = str(resource["id"])
    resource["item_label"] = _candidate_localized_text(
        resource["item_label"], key=f"resource.{resource_id}.item"
    )
    for field in resource["fields"]:
        field_id = str(field["id"])
        field["label"] = _candidate_localized_text(
            field["label"], key=f"field.{field_id}.label"
        )
        option_keys: set[str] = set()
        for option in field.get("options") or []:
            option_id = _canonical_candidate_identifier(
                option.get("value"), namespace="option"
            )
            raw_value = str(option.get("value"))
            if raw_value != option_id:
                # Lossy ASCII slugs must not collapse distinct Unicode values.
                option_id += "." + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]
            option_key = f"field.{field_id}.option.{option_id}"
            if option_key in option_keys:
                _fail(
                    f"candidate field {field_id!r} has option values with the "
                    f"same localization key {option_key!r}"
                )
            option_keys.add(option_key)
            option["label"] = _candidate_localized_text(
                option["label"], key=option_key
            )

    for view in candidate["views"]:
        view_id = str(view["id"])
        view["title"] = _candidate_localized_text(
            view["title"], key=f"view.{view_id}.title"
        )
        for control in view["query_controls"]:
            control_id = str(control["id"])
            control["label"] = _candidate_localized_text(
                control["label"], key=f"query.{control_id}.label"
            )
        empty_state = view.get("empty_state")
        if isinstance(empty_state, dict):
            empty_state["title"] = _candidate_localized_text(
                empty_state["title"], key=f"view.{view_id}.empty.title"
            )
            if isinstance(empty_state.get("detail"), dict):
                empty_state["detail"] = _candidate_localized_text(
                    empty_state["detail"], key=f"view.{view_id}.empty.detail"
                )

    for command in candidate["commands"]:
        command_id = str(command["id"])
        command["label"] = _candidate_localized_text(
            command["label"], key=f"command.{command_id}.label"
        )
        if isinstance(command.get("confirmation"), dict):
            command["confirmation"] = _candidate_localized_text(
                command["confirmation"], key=f"command.{command_id}.confirmation"
            )

    for state in candidate["representative_states"]:
        state_id = str(state["id"])
        state["label"] = _candidate_localized_text(
            state["label"], key=f"state.{state_id}.label"
        )


def _canonicalize_semantic_prototype_candidate(
    value: Mapping[str, Any],
    *, field_id_overrides: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    candidate = copy.deepcopy(dict(value))
    candidate["resource"].setdefault("read_only_when", None)
    try:
        Draft202012Validator(semantic_prototype_provider_contract(locales=_text_locales(candidate["title"]))).validate(
            candidate
        )
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    normalizations: list[dict[str, str]] = []
    resource = candidate["resource"]
    fields = resource["fields"]
    records = resource["records"]
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
    for index, field in enumerate(fields):
        raw_id = str(field["id"]).strip()
        qualified = (field_id_overrides or {}).get(raw_id, field_ids[raw_id])
        if qualified != field_ids[raw_id]:
            normalizations.append({"kind": "field_owner_namespace", "from": field_ids[raw_id],
                                   "to": qualified, "resource": str(resource["id"]),
                                   "target": f"$.resource.fields[{index}].id"})
            field_ids[raw_id] = qualified
    field_ids = _candidate_field_reference_ids(str(resource["id"]), field_ids)
    record_ids = _candidate_identifier_map(
        [record["id"] for record in records],
        namespace="record",
        normalizations=normalizations,
        targets=[f"$.resource.records[{index}].id" for index in range(len(records))],
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
    if isinstance(resource.get("read_only_when"), dict):
        resource["read_only_when"]["field_ref"] = _mapped_candidate_ref(
            resource["read_only_when"]["field_ref"], namespace="field", identifiers=field_ids,
            normalizations=normalizations, target="$.resource.read_only_when.field_ref",
        )
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
    identity_index = next((index for index, field in enumerate(fields) if field["id"] == "id"), None)
    identity_findings = []
    for index, record in enumerate(records):
        original_id = str(record["id"])
        record["id"] = record_ids[original_id.strip()]
        if identity_index is not None and identity_index < len(record["values"]):
            declared = record["values"][identity_index]
            if declared not in (None, original_id, original_id.strip(), record["id"]):
                identity_findings.append({"code": "semantic.record_identity_mismatch",
                                          "path": f"$.resource.records[{index}].values[{identity_index}]",
                                          "detail": f"resource {resource['id']!r} record {index} id value {declared!r} does not match fixture id {original_id!r}"})
            elif declared != record["id"]:
                record["values"][identity_index] = record["id"]
                normalizations.append({"kind": "record_identity_value", "from": str(declared),
                                       "to": record["id"], "target": f"$.resource.records[{index}].values[{identity_index}]"})
    if identity_findings:
        raise SemanticPrototypeValidationError(identity_findings)
    _normalize_candidate_fixture_values(
        fields=fields,
        records=records,
        path="$.resource.records",
        normalizations=normalizations,
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

    if identity_index is not None:
        _normalize_relationship_identity_literals(
            resource=resource, field_ref="id", target_ids=record_ids,
            commands=commands, states=states, normalizations=normalizations,
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

    merged_bindings: list[dict[str, Any]] = []
    bindings_by_requirement: dict[str, dict[str, Any]] = {}
    for binding_index, binding in enumerate(candidate["requirement_bindings"]):
        requirement_ref = str(binding["requirement_ref"])
        existing = bindings_by_requirement.get(requirement_ref)
        if existing is None:
            existing = copy.deepcopy(dict(binding))
            bindings_by_requirement[requirement_ref] = existing
            merged_bindings.append(existing)
            continue
        existing_refs = set(existing["semantic_refs"])
        for semantic_ref in binding["semantic_refs"]:
            if semantic_ref not in existing_refs:
                existing["semantic_refs"].append(semantic_ref)
                existing_refs.add(semantic_ref)
        normalizations.append(
            {
                "kind": "duplicate_requirement_binding",
                "from": f"$.requirement_bindings[{binding_index}]",
                "to": requirement_ref,
                "target": "$.requirement_bindings",
            }
        )
    candidate["requirement_bindings"] = merged_bindings

    _materialize_candidate_localization_keys(candidate)
    return candidate, normalizations


def _lower_semantic_prototype_candidate(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(value))
    candidate["schema"] = SEMANTIC_PROTOTYPE_SCHEMA
    candidate["brief_ref"] = str(brief.get("brief_id") or "")
    candidate["brief_digest"] = str(brief.get("digest") or "")
    if not candidate["brief_ref"] or not candidate["brief_digest"]:
        _fail("candidate lowering requires an authoritative Prototype Brief")
    candidate["layout"] = {"pattern": candidate["layout"]}
    resource = dict(candidate["resource"])
    if resource.get("read_only_when") is None:
        resource.pop("read_only_when", None)
    resource["identity_field_refs"] = ["id"]
    resource["fields"] = []
    for raw_field in candidate["resource"]["fields"]:
        field = dict(raw_field)
        if field["value_type"] == "markdown":
            field["value_type"] = "long_text"
            field["display_format"] = "markdown"
        if field["value_type"] == "attachments":
            field["value_type"] = "attachment"
            field["multiple"] = True
        if not field.get("options"):
            field.pop("options", None)
        if field.get("visible_when") is None:
            field.pop("visible_when", None)
        resource["fields"].append(field)
    field_ids = [str(field["id"]) for field in resource["fields"]]
    resource["records"] = []
    for index, record in enumerate(candidate["resource"]["records"]):
        values = list(record["values"])
        if len(values) != len(field_ids):
            _fail(
                f"resource record {index} has {len(values)} values for "
                f"{len(field_ids)} fields"
            )
        record_id = str(record["id"])
        lowered_record = dict(zip(field_ids, copy.deepcopy(values), strict=True))
        declared_id = lowered_record.get("id")
        if declared_id is not None and str(declared_id) != record_id:
            _fail(
                f"resource record {index} id value {declared_id!r} does not "
                f"match fixture id {record_id!r}"
            )
        lowered_record["id"] = record_id
        resource["records"].append(lowered_record)
    candidate["resource"] = resource

    views: list[dict[str, Any]] = []
    for raw_view in candidate["views"]:
        view = dict(raw_view)
        role = str(view["role"])
        presentation = view.get("presentation")
        if role == "collection" and presentation not in {"list", "table", "cards"}:
            _fail(f"collection view {view['id']!r} requires a presentation")
        if role != "collection" and presentation is not None:
            _fail(f"{role} view {view['id']!r} presentation must be null")
        if view.get("presentation") is None:
            view.pop("presentation", None)
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
        if command.get("guard") is None or not command["guard"].get(
            "require_nonempty"
        ):
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


def _normalize_semantic_prototype_candidate_v1(
    value: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    """Lower a strict provider candidate into the canonical semantic ABI."""

    candidate, _ = _canonicalize_semantic_prototype_candidate(value)
    return _validate_semantic_prototype_v1(
        _lower_semantic_prototype_candidate(candidate, brief=brief), brief=brief
    )


def _compile_semantic_prototype_candidate_v1(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Validate, lower, and compile one strict provider candidate."""

    candidate, normalizations = _canonicalize_semantic_prototype_candidate(value)
    requirement_findings = _requirement_contract_findings(candidate, brief=brief)
    try:
        semantic_document = _lower_semantic_prototype_candidate(candidate, brief=brief)
        normalizations.append(
            {
                "kind": "authoritative_brief_provenance",
                "from": "Prototype Brief",
                "to": str(semantic_document["brief_ref"]),
                "target": "$.brief_ref|$.brief_digest",
            }
        )
        result = _compile_semantic_prototype_v1(
            semantic_document,
            brief=brief,
            project_ref=project_ref,
        )
    except BuilderWorkflowError as exc:
        prefix = "invalid semantic Prototype: "
        detail = str(exc)
        if detail.startswith(prefix):
            detail = detail[len(prefix) :]
        matching_finding = next(
            (
                item
                for item in requirement_findings
                if str(item.get("detail") or "") == detail
            ),
            None,
        )
        findings = [
            copy.deepcopy(matching_finding)
            if matching_finding is not None
            else {
                "code": "semantic.compiler_contract_invalid" if detail.startswith("compiled WebUI") else "semantic.validation_failed",
                "path": "$",
                "detail": detail,
            }
        ]
        findings.extend(
            item
            for item in requirement_findings
            if str(item.get("detail") or "") != detail
        )
        raise SemanticPrototypeValidationError(findings) from exc
    if requirement_findings:
        raise SemanticPrototypeValidationError(requirement_findings)
    result["semantic_document"] = semantic_document
    result["normalizations"] = normalizations
    return result


def _localized(
    value: Mapping[str, Any], dictionaries: dict[str, dict[str, str]]
) -> tuple[str, dict[str, str]]:
    key = str(value["key"])
    locales = tuple(locale for locale in dictionaries if locale in value)
    if not locales:
        _fail(f"text {key!r} has no requested locale")
    for locale in locales:
        dictionaries[locale][key] = str(value[locale])
    fallback = str(value[locales[0]])
    return fallback, {"key": key, "fallback": fallback}


def _text_locales(value: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(locale for locale in ("en", "ru") if locale in value)


def _choice_display(field: Mapping[str, Any], dictionaries: dict[str, dict[str, str]]) -> dict[str, str]:
    if field["value_type"] not in {"choice", "multi_choice"}:
        return {}
    prefix = f"value.{field['id']}."
    for option in field.get("options") or []:
        for locale in dictionaries:
            if locale in option["label"]:
                dictionaries[locale][prefix + str(option["value"])] = str(option["label"][locale])
    return {"valueI18nPrefix": prefix}


def _nonempty_expression(ref: str, fields: Mapping[str, Any]) -> str:
    if fields.get(ref, {}).get("value_type") in {"number", "boolean"}:
        return f"$state.{ref} != null"
    return f"$state.{ref} != null && $state.{ref}.length > 0"


def _condition_expression(condition: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    ref = str(condition["field_ref"])
    operator = str(condition["operator"])
    value = condition.get("value")
    if operator == "nonempty":
        return f"({_nonempty_expression(ref, fields)})"
    if operator == "empty":
        return f"!({_nonempty_expression(ref, fields)})"
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    token = "===" if operator == "equals" else "!=="
    return f"$state.{ref} {token} {encoded}"


def _guard_expression(guard: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    when = dict(guard["when"])
    requirement = " && ".join(
        _nonempty_expression(field_ref, fields)
        for field_ref in guard["require_nonempty"]
    )
    condition = _condition_expression(when, fields)
    if when["operator"] == "equals":
        inverse = _condition_expression({**when, "operator": "not_equals"}, fields)
    elif when["operator"] == "not_equals":
        inverse = _condition_expression({**when, "operator": "equals"}, fields)
    elif when["operator"] == "nonempty":
        inverse = _condition_expression({**when, "operator": "empty"}, fields)
    else:
        inverse = _condition_expression({**when, "operator": "nonempty"}, fields)
    return f"{inverse} || ({condition} && {requirement})"


def _prototype_record_schema(resource: Mapping[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 1},
    }
    for field in resource["fields"]:
        kind = field["value_type"]
        scalar = "number" if kind == "number" else "boolean" if kind == "boolean" else "string"
        descriptor: dict[str, Any] = {"type": [scalar, "null"]}
        if kind == "multi_choice" or (kind == "attachment" and field.get("multiple")):
            descriptor = {"type": ["array", "null"], "items": {"type": "string"}}
        if kind == "attachment":
            (descriptor["items"] if field.get("multiple") else descriptor)["format"] = "adaos-attachment"
        if kind in {"choice", "multi_choice"} and field.get("options"):
            choices = [option["value"] for option in field["options"]]
            if kind == "multi_choice":
                descriptor["items"] = {"enum": choices}
                descriptor["uniqueItems"] = True
            else:
                descriptor = {"enum": [*choices, None]}
        properties[field["id"]] = descriptor
    # Required input is a form constraint; persistence also admits incomplete drafts.
    return {"type": "object", "properties": properties, "required": ["id", "revision"],
            "additionalProperties": False}


def _compile_semantic_prototype_v1(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
    require_primary: bool = True,
    record_state_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Compile a validated semantic document into canonical Prototype artifacts."""

    document = _validate_semantic_prototype_v1(
        value, brief=brief, require_primary=require_primary, record_state_ids=record_state_ids
    )
    dictionaries: dict[str, dict[str, str]] = {locale: {} for locale in _text_locales(document["title"])}
    resource = dict(document["resource"])
    fields = {str(item["id"]): dict(item) for item in resource["fields"]}
    views = {str(item["id"]): item for item in document["views"]}
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
            elif fields[str(control["field_ref"])]["value_type"] in {
                "boolean",
                "choice",
            }:
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
                field_options = list(field.get("options") or [])
                if field["value_type"] == "boolean":
                    field_options = [
                        {
                            "value": True,
                            "label": {
                                "key": f"{control['label']['key']}.yes",
                                "en": "Yes",
                                "ru": "Да",
                            },
                        },
                        {
                            "value": False,
                            "label": {
                                "key": f"{control['label']['key']}.no",
                                "en": "No",
                                "ru": "Нет",
                            },
                        },
                    ]
                for option in field_options:
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
                    fields[str(control["field_ref"])]["value_type"]
                    if fields[str(control["field_ref"])]["value_type"] in {"date", "number"}
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
            presentation = str(view.get("presentation") or "list")
            if presentation == "table":
                widget["type"] = "ui.table"
                widget["inputs"] = {"columns": []}
                for column_index, field_id in enumerate(view["field_refs"]):
                    label, label_i18n = _localized(
                        fields[field_id]["label"], dictionaries
                    )
                    widget["inputs"]["columns"].append(
                        {
                            "key": field_id,
                            "label": label,
                            "label_i18n": label_i18n,
                            **_choice_display(fields[field_id], dictionaries),
                        }
                    )
                    source_map.setdefault(f"field:{field_id}", []).append(
                        f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.columns.{column_index}"
                    )
            else:
                widget["type"] = "ui.list"
                title_key = next(
                    (
                        field_id
                        for field_id in view["field_refs"]
                        if fields[field_id]["value_type"]
                        in {"short_text", "long_text"}
                    ),
                    "id",
                )
                widget["inputs"] = {
                    "variant": presentation,
                    "itemIdKey": "id",
                    "titleKey": title_key,
                    "meta": [],
                }
                if title_key != "id":
                    source_map.setdefault(f"field:{title_key}", []).append(
                        f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.titleKey"
                    )
                for field_id in view["field_refs"]:
                    if field_id == title_key:
                        continue
                    label, label_i18n = _localized(
                        fields[field_id]["label"], dictionaries
                    )
                    value_type = str(fields[field_id]["value_type"])
                    meta = {
                        "key": field_id,
                        "label": label,
                        "label_i18n": label_i18n,
                        "kind": (
                            "badge"
                            if value_type == "choice"
                            else "boolean"
                            if value_type == "boolean"
                            else "text"
                        ),
                    }
                    meta_index = len(widget["inputs"]["meta"])
                    meta.update(_choice_display(fields[field_id], dictionaries))
                    widget["inputs"]["meta"].append(meta)
                    source_map.setdefault(f"field:{field_id}", []).append(
                        f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.meta.{meta_index}"
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
                if presentation == "table":
                    widget["inputs"]["emptyText"] = rendered_empty["title"]
                    widget["inputs"]["emptyText_i18n"] = rendered_empty[
                        "title_i18n"
                    ]
                else:
                    widget["inputs"]["emptyState"] = rendered_empty
        elif role == "details":
            widget["type"] = "item.details"
            widget["selectedStateKey"] = selection_ref
            widget["inputs"] = {"fields": []}
            widget["dataSource"]["query"]["id"] = f"$state.{selection_ref}"
            for field_id in view["field_refs"]:
                label, label_i18n = _localized(fields[field_id]["label"], dictionaries)
                widget["inputs"]["fields"].append(
                    {"id": field_id, "label": label, "label_i18n": label_i18n, **({"kind": "attachment"} if fields[field_id]["value_type"] == "attachment" else {"kind": "markdown"} if fields[field_id].get("display_format") == "markdown" else {}), **_choice_display(fields[field_id], dictionaries)}
                )
                source_map.setdefault(f"field:{field_id}", []).append(
                    f"ui.application.desktop.pageSchema.widgets.@{view_id}.inputs.fields.@{field_id}"
                )
        else:
            widget["type"] = "ui.form"
            widget["inputs"] = {"layout": "responsiveGrid", "fields": [], "buttons": [], "selectedStateKey": selection_ref}
            if resource.get("read_only_when"):
                widget["inputs"]["readOnlyIf"] = _condition_expression(resource["read_only_when"], fields)
            widget["dataSource"]["query"]["id"] = f"$state.{selection_ref}"
            owned_commands = [command for command in commands.values() if command["view_ref"] == view_id]
            editable_inputs = {ref for command in owned_commands for ref in command["input_field_refs"]
                               if ref not in command.get("fixed_values", {})}
            for field_id in view["field_refs"]:
                field = fields[field_id]
                read_only = not field["editable"] or field_id not in editable_inputs
                label, label_i18n = _localized(field["label"], dictionaries)
                rendered_field: dict[str, Any] = {
                    "id": field_id,
                    "title": label,
                    "title_i18n": label_i18n,
                    "type": _FIELD_TYPES[str(field["value_type"])],
                    "required": bool(field["required"]) and not read_only,
                }
                if read_only:
                    rendered_field["readOnly"] = True
                    fixed = [command.get("fixed_values", {}).get(field_id) for command in owned_commands]
                    if fixed and fixed[0] is not None and all(value == fixed[0] for value in fixed):
                        rendered_field["defaultValue"] = copy.deepcopy(fixed[0])
                if field.get("multiple"):
                    rendered_field["multiple"] = True
                    if field.get("max_items") is not None:
                        rendered_field["maxFiles"] = int(field["max_items"])
                if field["value_type"] == "attachment":
                    rendered_field["fileStorage"] = "prototype"
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
                        field["visible_when"], fields
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
                        **({"en": f"Confirm {command['label']['en']}?"} if "en" in command["label"] else {}),
                        **({"ru": f"Подтвердить «{command['label']['ru']}»?"} if "ru" in command["label"] else {}),
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
                    expression = _guard_expression(command["guard"], fields)
                    button["enabledIf"] = expression
                    action["enabledIf"] = expression
                selection_condition = f"$state.{selection_ref} {'===' if command['kind'] == 'create' else '!=='} ''"
                for target in (button, action):
                    target["enabledIf"] = (
                        f"({selection_condition}) && ({target['enabledIf']})"
                        if target.get("enabledIf") else selection_condition
                    )
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
                f"ui.application.desktop.pageSchema.widgets.@{view_ref}.inputs.{'emptyText' if views[view_ref].get('presentation') == 'table' else 'emptyState'}"
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
                "fixture_mode": "empty" if empty_fixture else "selected_record" if views[view_ref]["role"] != "collection" else "filtered_records",
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
                    "role": {"primary": "main", "supporting": "aux", "actions": "footer"}[region_role],
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
                "capability_gaps": copy.deepcopy(document["capability_gaps"]),
                "prototype_record_schemas": {resource_type: _prototype_record_schema(resource)},
                "prototype_resource_policies": {resource_type: {"read_only_when": resource["read_only_when"]}} if resource.get("read_only_when") else {},
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


def _unique_v2_ids(values: Sequence[Mapping[str, Any]], label: str) -> None:
    seen: set[str] = set()
    for item in values:
        identifier = str(item.get("id") or "")
        if identifier in seen:
            _fail(f"duplicate {label} id {identifier!r}")
        seen.add(identifier)


def _candidate_v2_field_namespaces(resources: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    local_maps: dict[str, dict[str, str]] = {}
    counts: dict[str, int] = {}
    for resource in resources:
        _unique_v2_ids(resource["fields"], "resource-local field")
        mapping = _candidate_identifier_map(
            [field["id"] for field in resource["fields"]], namespace="field", normalizations=[],
            targets=[""] * len(resource["fields"]),
        )
        local_maps[resource["id"]] = mapping
        for value in mapping.values():
            counts[value] = counts.get(value, 0) + 1
    for owner, mapping in local_maps.items():
        for key, value in mapping.items():
            if counts[value] > 1:
                if value == "id":
                    continue
                if value == "revision":
                    _fail(f"field {value!r} conflicts with record metadata; use an explicit business field name")
                mapping[key] = f"{_canonical_candidate_identifier(owner, namespace='resource')}.{value}"
    _unique_v2_ids([{"id": value} for mapping in local_maps.values() for value in mapping.values() if value != "id"], "qualified field")
    return local_maps


def _candidate_field_reference_ids(owner: str, declarations: Mapping[str, str]) -> dict[str, str]:
    """Resolve local, canonical and owner-qualified spellings without renaming data."""
    result: dict[str, str] = {}
    canonical_owner = _canonical_candidate_identifier(owner, namespace="resource")
    for raw, canonical in declarations.items():
        local = _canonical_candidate_identifier(raw, namespace="field")
        for alias in {raw, canonical, f"{owner}.{raw}", f"{canonical_owner}.{local}"}:
            previous = result.get(alias)
            if previous is not None and previous != canonical:
                _fail(f"ambiguous field alias {alias!r} in resource {owner!r}: {previous!r}, {canonical!r}")
            result[alias] = canonical
    return result


def _normalize_relationship_identity_literals(
    *, resource: dict[str, Any], field_ref: str, target_ids: Mapping[str, str],
    commands: Sequence[dict[str, Any]], states: Sequence[dict[str, Any]],
    normalizations: list[dict[str, str]],
    views: Sequence[dict[str, Any]] = (),
) -> None:
    # Record identity normalization must preserve every typed use of the foreign key.
    def normalize(container: dict[str, Any], path: str) -> None:
        original = container.get("value")
        normalized = target_ids.get(original) if isinstance(original, str) else None
        if normalized is not None and normalized != original:
            container["value"] = normalized
            normalizations.append({"kind": "relationship_identity_value", "from": original,
                                   "to": normalized, "target": path})

    for field_index, field in enumerate(resource["fields"]):
        path = f"$.resources.@{resource['id']}.fields[{field_index}]"
        if field["id"] == field_ref:
            for option_index, option in enumerate(field.get("options") or []):
                normalize(option, f"{path}.options[{option_index}].value")
        condition = field.get("visible_when")
        if condition and condition.get("field_ref") == field_ref:
            normalize(condition, f"{path}.visible_when.value")
    for index, command in enumerate(commands):
        for entry_index, entry in enumerate(command.get("fixed_values") or []):
            if entry["field_ref"] == field_ref:
                normalize(entry, f"$.commands[{index}].fixed_values[{entry_index}].value")
        condition = (command.get("guard") or {}).get("when")
        if condition and condition.get("field_ref") == field_ref:
            normalize(condition, f"$.commands[{index}].guard.when.value")
    condition = resource.get("read_only_when")
    if condition and condition.get("field_ref") == field_ref:
        normalize(condition, f"$.resources.@{resource['id']}.read_only_when.value")
    for index, state in enumerate(states):
        for predicate_index, predicate in enumerate(state["filters"]):
            operand = predicate["operand"]
            if predicate["field_ref"] == field_ref and operand["kind"] == "value":
                normalize(operand, f"$.representative_states[{index}].filters[{predicate_index}].operand.value")
    for index, view in enumerate(views):
        if view.get('resource_ref') != resource['id']:
            continue
        for filter_index, predicate in enumerate(view.get('scope_filters') or []):
            if predicate['field_ref'] == field_ref:
                normalize(predicate, f'$.views[{index}].scope_filters[{filter_index}].value')


def _canonicalize_semantic_prototype_candidate_v2(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    candidate = copy.deepcopy(dict(value))
    candidate.setdefault("automation_requirements", [])
    for relationship in candidate.get("relationships") or []:
        relationship.setdefault("label_field_refs", [])
    for resource in candidate.get("resources") or []:
        resource.setdefault("read_only_when", None)
    for view in candidate.get("views") or []:
        if isinstance(view, dict):
            view.setdefault("surface", "inline")
            view.setdefault("presentation_options", None)
            view.setdefault("field_display", [])
            view.setdefault("section", None)
            view.setdefault("scope_filters", [])
            view.setdefault("selection_filter", None)
            view.setdefault("media", None)
    try:
        Draft202012Validator(
            semantic_prototype_provider_contract(version="v2", locales=_text_locales(candidate["title"]), _view_variants=False)
        ).validate(candidate)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    resources = [dict(item) for item in candidate.get("resources") or []]
    _validate_candidate_bounds(candidate)
    if not resources:
        _fail("candidate requires at least one resource")
    _unique_v2_ids(resources, "resource")
    field_ids_by_resource = _candidate_v2_field_namespaces(resources)
    field_refs_by_resource = {
        owner: _candidate_field_reference_ids(owner, mapping)
        for owner, mapping in field_ids_by_resource.items()
    }
    raw_views = [dict(item) for item in candidate.get("views") or []]
    raw_commands = [dict(item) for item in candidate.get("commands") or []]
    raw_states = [dict(item) for item in candidate.get("representative_states") or []]
    normalizations: list[dict[str, str]] = []
    normalized_resources: list[dict[str, Any]] = []
    normalized_views: list[dict[str, Any]] = []
    normalized_commands: list[dict[str, Any]] = []
    normalized_states: list[dict[str, Any]] = []
    resource_ids: dict[str, str] = {}
    field_ids: dict[str, str] = {}
    view_ids: dict[str, str] = {}
    query_ids: dict[str, str] = {}
    command_ids: dict[str, str] = {}
    state_ids: dict[str, str] = {}
    record_ids_by_resource: dict[str, dict[str, str]] = {}

    for resource_index, resource in enumerate(resources):
        raw_resource_id = str(resource.get("id") or "")
        resource_views = [
            item
            for item in raw_views
            if str(item.get("resource_ref") or "") == raw_resource_id
        ]
        raw_view_id_set = {str(item.get("id") or "") for item in resource_views}
        resource_commands = [
            item
            for item in raw_commands
            if str(item.get("view_ref") or "") in raw_view_id_set
        ]
        resource_states = [
            item
            for item in raw_states
            if str(item.get("view_ref") or "") in raw_view_id_set
        ]
        v1_candidate = {
            "schema": SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA,
            "document_id": candidate["document_id"],
            "title": copy.deepcopy(candidate["title"]),
            "layout": candidate["layout"],
            "resource": copy.deepcopy(resource),
            "views": [
                    legacy_view(item)
                for item in resource_views
            ],
            "commands": copy.deepcopy(resource_commands),
            "representative_states": [
                {key: copy.deepcopy(item_value) for key, item_value in item.items() if key != "proof"}
                for item in resource_states
            ],
            "requirement_bindings": [],
            "capability_gaps": [],
        }
        normalized, slice_normalizations = _canonicalize_semantic_prototype_candidate(
            v1_candidate, field_id_overrides=field_ids_by_resource[raw_resource_id],
        )
        normalizations.extend(slice_normalizations)
        normalized_resource = dict(normalized["resource"])
        normalized_resource_id = str(normalized_resource["id"])
        resource_ids[raw_resource_id] = normalized_resource_id
        for raw_field, normalized_field in zip(
            resource.get("fields") or [],
            normalized_resource.get("fields") or [],
            strict=True,
        ):
            field_ids[str(raw_field.get("id") or "")] = str(
                normalized_field["id"]
            )
        normalized_resources.append(normalized_resource)
        record_ids_by_resource[normalized_resource_id] = {
            str(raw_record.get("id") or ""): str(normalized_record["id"])
            for raw_record, normalized_record in zip(
                resource.get("records") or [],
                normalized_resource.get("records") or [],
                strict=True,
            )
        }

        for raw_view, normalized_view in zip(
            resource_views, normalized["views"], strict=True
        ):
            normalized_view = dict(normalized_view)
            role = str(normalized_view["role"])
            presentation = normalized_view.get("presentation")
            expected_presentation = (
                presentation if role == "collection" and presentation else "list"
            )
            if role != "collection":
                expected_presentation = None
            if presentation != expected_presentation:
                normalizations.append(
                    {
                        "kind": "view_presentation_for_role",
                        "from": str(presentation),
                        "to": str(expected_presentation),
                        "target": f"$.views.@{raw_view.get('id')}.presentation",
                    }
                )
                normalized_view["presentation"] = expected_presentation
            normalized_view["resource_ref"] = normalized_resource_id
            normalized_view["surface"] = raw_view.get("surface", "inline")
            normalized_view["media"] = {
                key: field_refs_by_resource[raw_resource_id].get(str(ref), _canonical_candidate_identifier(ref, namespace="field")) if ref else None
                for key, ref in raw_view["media"].items()
            } if raw_view.get("media") else None
            normalized_view["presentation_options"] = {
                key: (field_refs_by_resource[raw_resource_id].get(str(ref), str(ref)) if ref else None)
                if key.endswith("_field_ref") else ref
                for key, ref in raw_view["presentation_options"].items()
            } if raw_view.get("presentation_options") else None
            normalized_view["field_display"] = [
                {**entry, "field_ref": field_refs_by_resource[raw_resource_id].get(entry["field_ref"], entry["field_ref"])}
                for entry in raw_view.get("field_display") or []
            ]
            normalized_view["scope_filters"] = [
                {**entry, "field_ref": field_refs_by_resource[raw_resource_id].get(entry["field_ref"], entry["field_ref"])}
                for entry in raw_view.get("scope_filters") or []
            ]
            link = raw_view.get("selection_filter")
            normalized_view["selection_filter"] = {
                "field_ref": field_refs_by_resource[raw_resource_id].get(link["field_ref"], link["field_ref"]),
                "source_view_ref": _canonical_candidate_identifier(link["source_view_ref"], namespace="view"),
            } if link else None
            if link and "source_field_ref" in link:
                source_view = next((item for item in raw_views if item.get("id") == link["source_view_ref"]), {})
                source_fields = field_refs_by_resource.get(source_view.get("resource_ref"), {})
                normalized_view["selection_filter"]["source_field_ref"] = source_fields.get(
                    link["source_field_ref"], link["source_field_ref"]
                )
            section = raw_view.get("section")
            normalized_view["section"] = {
                "id": _canonical_candidate_identifier(section["id"], namespace="section"),
                "kind": section["kind"],
                "title": _candidate_localized_text(section["title"], key="section." + _canonical_candidate_identifier(section["id"], namespace="section")),
            } if section else None
            if raw_view.get("presentation") in {"board", "tree", "chart", "accordion"} and role == "collection":
                normalized_view["presentation"] = raw_view["presentation"]
            view_ids[str(raw_view.get("id") or "")] = str(normalized_view["id"])
            for raw_control, normalized_control in zip(
                raw_view.get("query_controls") or [],
                normalized_view.get("query_controls") or [],
                strict=True,
            ):
                query_ids[str(raw_control.get("id") or "")] = str(
                    normalized_control["id"]
                )
            normalized_views.append(normalized_view)

        for raw_command, normalized_command in zip(
            resource_commands, normalized["commands"], strict=True
        ):
            command_ids[str(raw_command.get("id") or "")] = str(
                normalized_command["id"]
            )
            normalized_commands.append(dict(normalized_command))

        for raw_state, normalized_state in zip(
            resource_states, normalized["representative_states"], strict=True
        ):
            normalized_state = dict(normalized_state)
            proof = copy.deepcopy(dict(raw_state["proof"]))
            proof["visible_field_refs"] = [
                field_refs_by_resource[raw_resource_id].get(
                    str(field_ref),
                    _canonical_candidate_identifier(field_ref, namespace="field"),
                )
                for field_ref in proof.get("visible_field_refs") or []
            ]
            normalized_state["proof"] = proof
            state_ids[str(raw_state.get("id") or "")] = str(normalized_state["id"])
            normalized_states.append(normalized_state)

        if resource_index:
            normalizations[:] = [
                item
                for item in normalizations
                if not (
                    item.get("target") == "$.document_id"
                    and item.get("kind") == "candidate_identifier"
                )
            ]

    assigned_view_ids = {
        str(item.get("id") or "")
        for resource in resources
        for item in raw_views
        if str(item.get("resource_ref") or "") == str(resource.get("id") or "")
    }
    unassigned_views = sorted(
        str(item.get("id") or "")
        for item in raw_views
        if str(item.get("id") or "") not in assigned_view_ids
    )
    if unassigned_views:
        _fail(f"views reference unknown resources: {unassigned_views}")
    assigned_command_ids = {
        str(item.get("id") or "") for item in raw_commands if str(item.get("view_ref") or "") in view_ids
    }
    unassigned_commands = sorted(
        str(item.get("id") or "")
        for item in raw_commands
        if str(item.get("id") or "") not in assigned_command_ids
    )
    if unassigned_commands:
        _fail(f"commands reference unknown views: {unassigned_commands}")
    assigned_state_ids = {
        str(item.get("id") or "") for item in raw_states if str(item.get("view_ref") or "") in view_ids
    }
    unassigned_states = sorted(
        str(item.get("id") or "")
        for item in raw_states
        if str(item.get("id") or "") not in assigned_state_ids
    )
    if unassigned_states:
        _fail(f"representative states reference unknown views: {unassigned_states}")

    for values, label in (
        (normalized_resources, "resource"),
        ([field for resource in normalized_resources for field in resource["fields"] if field["id"] != "id"], "field"),
        (normalized_views, "view"),
        ([control for view in normalized_views for control in view.get("query_controls") or []], "query control"),
        (normalized_commands, "command"),
        (normalized_states, "representative state"),
    ):
        _unique_v2_ids(values, label)

    relationship_ids = _candidate_identifier_map(
        [item.get("id") for item in candidate.get("relationships") or []],
        namespace="relationship",
        normalizations=normalizations,
        targets=[
            f"$.relationships[{index}].id"
            for index in range(len(candidate.get("relationships") or []))
        ],
    )
    normalized_relationships: list[dict[str, Any]] = []
    for relationship in candidate.get("relationships") or []:
        normalized_relationship = copy.deepcopy(dict(relationship))
        normalized_relationship["id"] = relationship_ids[str(relationship["id"])]
        if "label_field_refs" in relationship:
            normalized_relationship["label_field_refs"] = [
                field_refs_by_resource.get(relationship["to_resource_ref"], {}).get(str(ref), _canonical_candidate_identifier(ref, namespace="field"))
                for ref in relationship["label_field_refs"]
            ]
        for key, namespace in (
            ("from_resource_ref", resource_ids),
            ("to_resource_ref", resource_ids),
            ("from_field_ref", field_refs_by_resource.get(relationship["from_resource_ref"], {})),
            ("to_field_ref", field_refs_by_resource.get(relationship["to_resource_ref"], {})),
        ):
            raw_ref = str(relationship[key])
            normalized_relationship[key] = namespace.get(
                raw_ref,
                _canonical_candidate_identifier(raw_ref, namespace=key),
            )
        resources_by_id = {item["id"]: item for item in normalized_resources}
        reference_findings = []
        for side in ("from", "to"):
            resource_id = normalized_relationship[f"{side}_resource_ref"]
            field_id = normalized_relationship[f"{side}_field_ref"]
            resource = resources_by_id.get(resource_id)
            if (resource and str(relationship[f"{side}_field_ref"]) == f"{relationship[f'{side}_resource_ref']}.id"
                    and not any(field["id"] == field_id for field in resource["fields"])):
                normalized_relationship[f"{side}_field_ref"] = "id"
                normalizations.append({"kind": "qualified_record_identity", "from": field_id, "to": "id",
                                       "target": f"$.relationships[{len(normalized_relationships)}].{side}_field_ref"})
                field_id = "id"
            field_exists = resource is not None and (
                field_id == "id"
                or any(field["id"] == field_id for field in resource["fields"])
            )
            if resource is None or not field_exists:
                key = f"{side}_{'resource' if resource is None else 'field'}_ref"
                reference_findings.append({
                    "code": "semantic.relationship_reference_missing",
                    "path": f"$.relationships[{len(normalized_relationships)}].{key}",
                    "detail": f"relationship {normalized_relationship['id']} references missing {key}: {normalized_relationship[key]}",
                })
        if reference_findings:
            raise SemanticPrototypeValidationError(reference_findings)
        reference = _foreign_key_relationship(normalized_relationship)
        from_resource_id, to_resource_id = reference["from_resource_ref"], reference["to_resource_ref"]
        from_field_id, to_field_id = reference["from_field_ref"], reference["to_field_ref"]
        identity_target_key = "from_field_ref" if normalized_relationship["cardinality"] == "one_to_many" else "to_field_ref"
        from_resource = resources_by_id[from_resource_id]
        to_resource = resources_by_id[to_resource_id]
        target_id_map = record_ids_by_resource.get(to_resource_id, {})
        target_ids = {
            **target_id_map,
            **{value: value for value in target_id_map.values()},
        }
        from_field_index = next((
            index
            for index, field in enumerate(from_resource.get("fields") or [])
            if str(field.get("id") or "") == from_field_id
        ), None)
        source_values = [
            value
            for record in from_resource.get("records") or []
            if (value := record["id"] if from_field_index is None else record["values"][from_field_index]) not in (None, "")
        ]
        if to_field_id == "id":
            target_values = [
                record["id"] for record in to_resource.get("records") or []
            ]
        else:
            to_field_index = next(
                index
                for index, field in enumerate(to_resource.get("fields") or [])
                if str(field.get("id") or "") == to_field_id
            )
            target_values = [
                record["values"][to_field_index]
                for record in to_resource.get("records") or []
                if record["values"][to_field_index] not in (None, "")
            ]
        references_target_ids = bool(source_values) and all(
            isinstance(item, str) and item in target_ids for item in source_values
        )
        references_declared_field = bool(source_values) and all(
            item in target_values for item in source_values
        )
        if references_target_ids and not references_declared_field:
            if to_field_id != "id":
                normalizations.append(
                    {
                        "kind": "relationship_identity_target",
                        "from": to_field_id,
                        "to": "id",
                        "target": (
                            f"$.relationships[{len(normalized_relationships)}]"
                            f".{identity_target_key}"
                        ),
                    }
                )
                normalized_relationship[identity_target_key] = "id"
        if normalized_relationship[identity_target_key] == "id":
            for record_index, record in enumerate(
                from_resource.get("records") or []
            ):
                original = record["id"] if from_field_index is None else record["values"][from_field_index]
                normalized_id = target_ids.get(original) if isinstance(original, str) else None
                if normalized_id is None or normalized_id == original:
                    continue
                if from_field_index is None:
                    record["id"] = normalized_id
                else:
                    record["values"][from_field_index] = normalized_id
                normalizations.append(
                    {
                        "kind": "relationship_identity_value",
                        "from": str(original),
                        "to": normalized_id,
                        "target": (
                            f"$.resources.@{from_resource_id}.records"
                            f"[{record_index}].{from_field_id}"
                        ),
                    }
                )
            _normalize_relationship_identity_literals(
                resource=from_resource, field_ref=from_field_id, target_ids=target_ids,
                commands=normalized_commands, states=normalized_states,
                normalizations=normalizations, views=normalized_views,
            )
        normalized_relationships.append(normalized_relationship)
    _unique_v2_ids(normalized_relationships, "relationship")

    semantic_namespaces = {
        "resource": resource_ids,
        "relationship": relationship_ids,
        "field": field_ids,
        "view": view_ids,
        "query": query_ids,
        "command": command_ids,
        "state": state_ids,
    }
    merged_bindings: list[dict[str, Any]] = []
    bindings_by_requirement: dict[str, dict[str, Any]] = {}
    owners_by_ref = {("resource", resource["id"]): resource["id"] for resource in resources}
    owners_by_ref.update({("view", view["id"]): view["resource_ref"] for view in raw_views})
    owners_by_ref.update({("query", control["id"]): view["resource_ref"]
                         for view in raw_views for control in view.get("query_controls") or []})
    for kind, items in (("command", raw_commands), ("state", raw_states)):
        owners_by_ref.update({(kind, item["id"]): owners_by_ref.get(("view", item["view_ref"])) for item in items})
    for binding in candidate.get("requirement_bindings") or []:
        normalized_refs: list[str] = []
        binding_owners = {owners_by_ref.get((ref["kind"], ref["id"].removeprefix(f"{ref['kind']}:")))
                          for ref in binding.get("semantic_refs") or []} - {None}
        for raw_ref in binding.get("semantic_refs") or []:
            kind = str(raw_ref["kind"])
            raw_identifier = str(raw_ref["id"])
            if kind == "field":
                local_id = raw_identifier.removeprefix("field:").strip()
                matches = {owner: mapping[local_id] for owner, mapping in field_refs_by_resource.items() if local_id in mapping}
                if matches:
                    scoped = list(matches.values()) if len(matches) == 1 else [value for owner, value in matches.items() if owner in binding_owners]
                    if len(scoped) != 1:
                        _fail(f"ambiguous field reference {raw_identifier!r} in requirement {binding['requirement_ref']!r}; use an owner-qualified field id or one owning resource/view/command")
                    if local_id != scoped[0]:
                        normalizations.append({"kind": "candidate_reference", "namespace": "field",
                                               "from": local_id, "to": scoped[0],
                                               "target": f"$.requirement_bindings[{len(merged_bindings)}].semantic_refs[{len(normalized_refs)}]"})
                    normalized_refs.append(f"field:{scoped[0]}")
                    continue
            identifiers = semantic_namespaces[kind]
            canonical_identifier = identifiers.get(
                raw_identifier,
                identifiers.get(
                    raw_identifier.removeprefix(f"{kind}:"),
                    _canonical_candidate_identifier(raw_identifier, namespace=kind),
                ),
            )
            normalized_refs.append(f"{kind}:{canonical_identifier}")
        requirement_ref = str(binding["requirement_ref"])
        existing = bindings_by_requirement.get(requirement_ref)
        if existing is None:
            existing = {
                "requirement_ref": requirement_ref,
                "semantic_refs": list(dict.fromkeys(normalized_refs)),
            }
            bindings_by_requirement[requirement_ref] = existing
            merged_bindings.append(existing)
        else:
            existing["semantic_refs"] = list(
                dict.fromkeys([*existing["semantic_refs"], *normalized_refs])
            )

    capability_gaps = copy.deepcopy(candidate.get("capability_gaps") or [])
    gap_requirements = {
        str(item.get("requirement_ref") or "")
        for item in capability_gaps
        if isinstance(item, Mapping)
    }
    retained_bindings: list[dict[str, Any]] = []
    for binding_index, binding in enumerate(merged_bindings):
        requirement_ref = str(binding["requirement_ref"])
        if requirement_ref not in gap_requirements:
            retained_bindings.append(binding)
            continue
        normalizations.append(
            {
                "kind": "capability_gap_precedence",
                "from": f"$.requirement_bindings[{binding_index}]",
                "to": requirement_ref,
                "target": "$.capability_gaps",
            }
        )

    document_id = _canonical_candidate_identifier(
        candidate["document_id"], namespace="prototype"
    )
    title = _candidate_localized_text(
        candidate["title"], key=f"prototype.{document_id}.title"
    )
    return (
        {
            "schema": SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA,
            "document_id": document_id,
            "title": title,
            "layout": candidate["layout"],
            "resources": normalized_resources,
            "relationships": normalized_relationships,
            "views": normalized_views,
            "commands": normalized_commands,
            "representative_states": normalized_states,
            "requirement_bindings": retained_bindings,
            "capability_gaps": capability_gaps,
            "automation_requirements": copy.deepcopy(candidate["automation_requirements"]),
        },
        normalizations,
    )


def _lower_semantic_prototype_candidate_v2(
    value: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(value))
    lowered_resources: list[dict[str, Any]] = []
    lowered_views: list[dict[str, Any]] = []
    lowered_commands: list[dict[str, Any]] = []
    lowered_states: list[dict[str, Any]] = []
    for resource in candidate["resources"]:
        resource_id = str(resource["id"])
        views = [
            item
            for item in candidate["views"]
            if str(item["resource_ref"]) == resource_id
        ]
        view_ids = {str(item["id"]) for item in views}
        commands = [
            item for item in candidate["commands"] if str(item["view_ref"]) in view_ids
        ]
        states = [
            item
            for item in candidate["representative_states"]
            if str(item["view_ref"]) in view_ids
        ]
        lowered = _lower_semantic_prototype_candidate(
            {
                "schema": SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA,
                "document_id": candidate["document_id"],
                "title": copy.deepcopy(candidate["title"]),
                "layout": candidate["layout"],
                "resource": copy.deepcopy(resource),
                "views": [
                    legacy_view(item)
                    for item in views
                ],
                "commands": copy.deepcopy(commands),
                "representative_states": [
                    {key: copy.deepcopy(item_value) for key, item_value in item.items() if key != "proof"}
                    for item in states
                ],
                "requirement_bindings": [],
                "capability_gaps": [],
            },
            brief=brief,
        )
        lowered_resources.append(dict(lowered["resource"]))
        for original, view in zip(views, lowered["views"], strict=True):
            lowered_views.append({**dict(view), "resource_ref": resource_id, **view_extras(original)})
        lowered_commands.extend(dict(item) for item in lowered["commands"])
        for original, state in zip(states, lowered["representative_states"], strict=True):
            lowered_states.append(
                {**dict(state), "proof": copy.deepcopy(dict(original["proof"]))}
            )
    return {
        "schema": SEMANTIC_PROTOTYPE_V2_SCHEMA,
        "document_id": candidate["document_id"],
        "brief_ref": str(brief.get("brief_id") or ""),
        "brief_digest": str(brief.get("digest") or ""),
        "title": copy.deepcopy(candidate["title"]),
        "layout": {"pattern": candidate["layout"]},
        "resources": lowered_resources,
        "relationships": copy.deepcopy(candidate["relationships"]),
        "views": lowered_views,
        "commands": lowered_commands,
        "representative_states": lowered_states,
        "requirement_bindings": copy.deepcopy(candidate["requirement_bindings"]),
        "capability_gaps": copy.deepcopy(candidate["capability_gaps"]),
        "automation_requirements": [
            {key: copy.deepcopy(item[key]) for key in ("requirement_ref", "reason", "disclosure", "acceptance", "statement")}
            for item in automation_obligations(candidate, brief)
        ],
    }


def _state_proof_findings(
    state: Mapping[str, Any], view: Mapping[str, Any], *, index: int
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    state_id = str(state["id"])

    def add(code: str, detail: str) -> None:
        findings.append({
            "code": code,
            "path": f"$.representative_states[{index}].proof",
            "semantic_refs": [f"state:{state_id}", f"view:{view['id']}"],
            "detail": f"representative state {state_id!r} {detail}",
        })

    proof = state["proof"]
    visible_fields = set(proof["visible_field_refs"])
    rendered_fields = set(view["field_refs"])
    if view["role"] == "details" and view.get("media"):
        rendered_fields.update(ref for key, ref in view["media"].items()
                               if key in {"source_field_ref", "poster_field_ref"} and ref)
    hidden = sorted(visible_fields - rendered_fields)
    if hidden:
        add("semantic.state_proof_hidden", f"claims fields not visible in view {view['id']!r}: {hidden}")
    filters = state.get("filters") or []
    predicate_fields = {
        str(field) for predicate in filters
        for field in (predicate["field_ref"], predicate.get("compare_field_ref"))
        if field
    }
    kind = str(proof["kind"])
    rule = STATE_PROOF_RULES[kind]
    if view["role"] not in rule["view_roles"]:
        add("semantic.state_view_role_invalid", f"{kind} proof requires a view with role in {rule['view_roles']}")
    if ((rule["filters"] == "none" and filters)
        or (rule["filters"] == "required" and not filters)
        or int(state["min_items"]) < rule["min_items"]
        or ("max_items" in rule and (
            state.get("max_items") != rule["max_items"]
            or state["min_items"] != rule["min_items"]
        ))):
        add("semantic.state_proof_invalid", f"{kind} proof requires {rule}")
    if rule.get("visible_predicates"):
        missing = sorted(predicate_fields - visible_fields)
        if missing:
            add("semantic.state_proof_hidden", f"does not expose predicate fields {missing}")
    if rule.get("query_controls"):
        controls = {
            str(control["field_ref"]) for control in view.get("query_controls") or []
            if control["kind"] == "filter"
        }
        if not predicate_fields.issubset(controls) or any(
            item["operator"] != "eq" or item.get("compare_field_ref") for item in filters
        ):
            add("semantic.state_query_unreachable", "query_empty requires matching equality filter controls")
    if rule.get("empty_state") and not view.get("empty_state"):
        add("semantic.state_empty_view_missing", f"requires an explicit empty_state on view {view['id']!r}")
    return findings


def _lookup_only_resource_ids(document: Mapping[str, Any]) -> set[str]:
    views = document.get("views") or []
    resources = {item["id"]: item for item in document.get("resources") or []}
    result: set[str] = set()
    for relation in document.get("relationships") or []:
        relation = _foreign_key_relationship(relation)
        source_id, target_id = relation["from_resource_ref"], relation["to_resource_ref"]
        if any(view.get("resource_ref") == target_id for view in views):
            continue
        field_id = relation["from_field_ref"]
        source = resources.get(source_id, {})
        if not any(field["id"] == field_id and field["editable"] for field in source.get("fields") or []):
            continue
        editors = {view["id"] for view in views if view.get("resource_ref") == source_id
                   and view["role"] == "editor" and field_id in view["field_refs"]}
        if any(command["view_ref"] in editors and field_id in command.get("input_field_refs", [])
               for command in document.get("commands") or []):
            result.add(target_id)
    return result


def _semantic_v2_model_findings(
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Collect independent model-correctable defects before fail-fast compile."""

    findings: list[dict[str, Any]] = []
    resources = {
        str(item.get("id") or ""): dict(item)
        for item in document.get("resources") or []
        if isinstance(item, Mapping)
    }
    views = {
        str(item.get("id") or ""): dict(item)
        for item in document.get("views") or []
        if isinstance(item, Mapping)
    }
    views_by_resource = {
        resource_id: [
            view
            for view in views.values()
            if str(view.get("resource_ref") or "") == resource_id
        ]
        for resource_id in resources
    }
    known_refs = {
        *(f"resource:{key}" for key in resources),
        *(f"view:{key}" for key in views),
        *(f"field:{field['id']}" for resource in resources.values() for field in resource["fields"]),
        *(f"query:{control['id']}" for view in views.values() for control in view.get("query_controls") or []),
        *(f"{kind}:{item['id']}" for kind, key in (("relationship", "relationships"), ("command", "commands"), ("state", "representative_states"))
          for item in document.get(key) or []),
    }
    for index, binding in enumerate(document.get("requirement_bindings") or []):
        unknown = sorted(set(binding["semantic_refs"]) - known_refs)
        if unknown:
            findings.append({"code": "semantic.binding_reference_missing",
                             "path": f"$.requirement_bindings[{index}].semantic_refs",
                             "requirement_refs": [binding["requirement_ref"]],
                             "detail": f"requirement {binding['requirement_ref']!r} has unresolved refs {unknown}"})
    for view_index, view in enumerate(views.values()):
        if view.get("role") != "collection" and view.get("query_controls"):
            findings.append({"code": "semantic.query_view_role", "path": f"$.views[{view_index}].query_controls",
                             "semantic_refs": [f"view:{view['id']}"],
                             "detail": f"query controls in {view['id']!r} must belong to its collection, not a {view['role']} view"})
    lookup_only = _lookup_only_resource_ids(document)
    for resource_index, (resource_id, resource) in enumerate(resources.items()):
        resource_views = views_by_resource[resource_id]
        if not resource_views and resource_id not in lookup_only:
            findings.append(
                {
                    "code": "semantic.resource_view_missing",
                    "path": f"$.resources[{resource_index}]",
                    "semantic_refs": [f"resource:{resource_id}"],
                    "detail": f"resource {resource_id!r} has no inspectable view",
                }
            )
        elif resource_views and not any(view.get("role") == "collection" for view in resource_views):
            findings.append(
                {
                    "code": "semantic.resource_collection_missing",
                    "path": f"$.resources[{resource_index}]",
                    "semantic_refs": [f"resource:{resource_id}"],
                    "detail": f"resource {resource_id!r} requires a collection view",
                }
            )
        fields = {
            str(item.get("id") or ""): dict(item)
            for item in resource.get("fields") or []
            if isinstance(item, Mapping)
        }
        for record_index, record in enumerate(resource.get("records") or []):
            if not isinstance(record, Mapping):
                continue
            record_id = str(record.get("id") or record_index)
            for field_id, field in fields.items():
                if field_id not in record or record[field_id] is None:
                    continue
                if _field_value_is_valid(field, record[field_id]):
                    continue
                findings.append(
                    {
                        "code": "semantic.record_value_invalid",
                        "path": (
                            f"$.resources[{resource_index}].records"
                            f"[{record_index}].{field_id}"
                        ),
                        "semantic_refs": [
                            f"resource:{resource_id}",
                            f"field:{field_id}",
                        ],
                        "detail": (
                            f"resource record {record_id!r} has invalid "
                            f"{field['value_type']} value for field {field_id!r}"
                        ),
                    }
                )

    for command_index, command in enumerate(document.get("commands") or []):
        if not isinstance(command, Mapping):
            continue
        command_id = str(command.get("id") or command_index)
        view_ref = str(command.get("view_ref") or "")
        view = views.get(view_ref)
        if view is None:
            findings.append(
                {
                    "code": "semantic.command_view_missing",
                    "path": f"$.commands[{command_index}].view_ref",
                    "semantic_refs": [f"command:{command_id}"],
                    "detail": (
                        f"command {command_id!r} references unknown view "
                        f"{view_ref!r}"
                    ),
                }
            )
            continue
        if str(view.get("role") or "") != "editor":
            findings.append(
                {
                    "code": "semantic.command_editor_required",
                    "path": f"$.commands[{command_index}].view_ref",
                    "semantic_refs": [
                        f"command:{command_id}",
                        f"view:{view_ref}",
                    ],
                    "detail": (
                        f"command {command_id!r} must be owned by an editor view"
                    ),
                }
            )
        resource = resources.get(str(view.get("resource_ref") or ""))
        if resource:
            local_fields = {field["id"] for field in resource["fields"]}
            guard = command.get("guard") or {}
            referenced = set(command.get("input_field_refs") or []) | set(command.get("fixed_values") or {})
            if guard:
                referenced |= {guard["when"]["field_ref"], *guard["require_nonempty"]}
            unknown = sorted(referenced - local_fields)
            if unknown:
                findings.append({"code": "semantic.command_field_missing", "path": f"$.commands[{command_index}]",
                                 "semantic_refs": [f"command:{command_id}", f"view:{view_ref}"],
                                 "detail": f"command {command_id!r} references fields outside its editor resource: {unknown}"})

    for relationship_index, relationship in enumerate(
        document.get("relationships") or []
    ):
        if not isinstance(relationship, Mapping):
            continue
        relationship = _foreign_key_relationship(relationship)
        relationship_id = str(relationship.get("id") or relationship_index)
        from_resource = resources.get(
            str(relationship.get("from_resource_ref") or "")
        )
        to_resource = resources.get(str(relationship.get("to_resource_ref") or ""))
        if from_resource is None or to_resource is None:
            continue
        from_field = str(relationship.get("from_field_ref") or "")
        to_field = str(relationship.get("to_field_ref") or "")
        from_field_definition = next(
            (
                item
                for item in from_resource.get("fields") or []
                if str(item.get("id") or "") == from_field
            ),
            None,
        )
        to_field_definition = next(
            (
                item
                for item in to_resource.get("fields") or []
                if str(item.get("id") or "") == to_field
            ),
            None,
        )
        if from_field_definition is not None and not (
            _relationship_field_types_compatible(
                from_field_definition, to_field_definition
            )
        ):
            findings.append(
                {
                    "code": "semantic.relationship_type_incompatible",
                    "path": f"$.relationships[{relationship_index}]",
                    "semantic_refs": [f"relationship:{relationship_id}"],
                    "detail": (
                        f"relationship {relationship_id!r} connects incompatible "
                        f"fields {from_field!r} and {to_field!r}"
                    ),
                }
            )
        target_values = [
            record.get(to_field)
            for record in to_resource.get("records") or []
            if isinstance(record, Mapping) and record.get(to_field) not in (None, "")
        ]
        missing_values = list(
            dict.fromkeys(
                str(record[from_field])
                for record in from_resource.get("records") or []
                if isinstance(record, Mapping)
                and record.get(from_field) not in (None, "")
                and record.get(from_field) not in target_values
            )
        )
        if missing_values:
            findings.append(
                {
                    "code": "semantic.relationship_target_missing",
                    "path": f"$.relationships[{relationship_index}]",
                    "semantic_refs": [f"relationship:{relationship_id}"],
                    "detail": (
                        f"relationship {relationship_id!r} has source values with no "
                        f"target {to_field!r}: {missing_values}"
                    ),
                }
            )
        for detail in _relationship_cardinality_errors(relationship, from_resource, to_resource):
            findings.append({"code": "semantic.relationship_cardinality_invalid",
                             "path": f"$.relationships[{relationship_index}]",
                             "semantic_refs": [f"relationship:{relationship_id}"], "detail": detail})

    for state_index, state in enumerate(document.get("representative_states") or []):
        if not isinstance(state, Mapping):
            continue
        state_id = str(state.get("id") or state_index)
        view = views.get(str(state.get("view_ref") or ""))
        if view is None:
            findings.append({
                "code": "semantic.state_view_missing",
                "path": f"$.representative_states[{state_index}].view_ref",
                "semantic_refs": [f"state:{state_id}"],
                "detail": f"representative state {state_id!r} references unknown view {state.get('view_ref')!r}",
            })
            continue
        resource = resources.get(str(view.get("resource_ref") or ""))
        if resource is None:
            continue
        filters = [
            dict(item)
            for item in state.get("filters") or []
            if isinstance(item, Mapping)
        ]
        fields = {str(field["id"]): field for field in resource["fields"]}
        for predicate_index, predicate in enumerate(filters):
            field = fields.get(str(predicate["field_ref"]))
            other_ref = predicate.get("compare_field_ref")
            other = fields.get(str(other_ref)) if other_ref else None
            detail = ""
            if field is None or (other_ref and other is None):
                detail = "predicate references a field absent from this resource"
            elif field["value_type"] == "multi_choice":
                detail = "predicates on multi_choice fields are unsupported"
            elif other and other["value_type"] != field["value_type"]:
                detail = "predicate compares incompatible field types"
            elif predicate["operator"] in {"lt", "lte", "gt", "gte"} and field["value_type"] not in {"date", "number"}:
                detail = "range predicate requires a date or number field"
            elif not other and not _field_value_is_valid(field, predicate.get("value")):
                detail = "predicate literal must match the field type and exact option.value"
            if detail:
                findings.append({
                    "code": "semantic.state_predicate_invalid",
                    "path": f"$.representative_states[{state_index}].filters[{predicate_index}]",
                    "semantic_refs": [f"state:{state_id}", f"view:{state['view_ref']}"],
                    "detail": f"state {state_id!r}, field {predicate['field_ref']!r}: {detail}",
                })
        minimum = int(state.get("min_items") or 0)
        maximum = (
            int(state["max_items"])
            if state.get("max_items") is not None
            else None
        )
        empty_fixture = not filters and minimum == 0 and maximum == 0
        matching_records = (
            []
            if empty_fixture
            else _matching_state_records(resource.get("records") or [], scoped_predicates(state, view))
        )
        count = len(matching_records)
        if count < minimum or (maximum is not None and count > maximum):
            expected_range = (
                f">={minimum}" if maximum is None else f"{minimum}..{maximum}"
            )
            findings.append(
                {
                    "code": "semantic.state_fixture_mismatch",
                    "path": f"$.representative_states[{state_index}]",
                    "semantic_refs": [
                        f"state:{state_id}",
                        f"view:{state.get('view_ref')}",
                    ],
                    "detail": (
                        f"representative state {state_id!r} expected "
                        f"{expected_range} matching records but found {count}"
                    ),
                }
            )
        findings.extend(_state_proof_findings(state, view, index=state_index))
    return findings


def _validate_semantic_prototype_v2(
    value: Mapping[str, Any], *, brief: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    document = copy.deepcopy(dict(value))
    try:
        _validator("webui.semantic.v2.schema.json").validate(document)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        _fail(f"{exc.message}{suffix}")

    _normalize_v2_ownership(document, brief=brief)

    resources = _unique(document["resources"], "resource")
    relationships = _unique(document["relationships"], "relationship")
    views = _unique(document["views"], "view")
    commands = _unique(document["commands"], "command")
    states = _unique(document["representative_states"], "state")
    all_fields: dict[str, dict[str, Any]] = {}
    for resource in resources.values():
        for field in resource["fields"]:
            field_id = str(field["id"])
            if field_id in all_fields and field_id != "id":
                _fail(f"duplicate field id {field_id!r} across resources")
            all_fields[field_id] = dict(field)
    if not any(str(view["region_role"]) == "primary" for view in views.values()):
        _fail("semantic Prototype requires at least one view in the primary region")

    views_by_resource: dict[str, list[dict[str, Any]]] = {
        resource_id: [] for resource_id in resources
    }
    for view in views.values():
        resource_id = str(view["resource_ref"])
        resource = resources.get(resource_id)
        if resource is None:
            _fail(f"view {view['id']!r} references unknown resource {resource_id!r}")
        resource_fields = {str(item["id"]) for item in resource["fields"]}
        unknown_fields = sorted(set(view["field_refs"]) - resource_fields)
        if unknown_fields:
            _fail(
                f"view {view['id']!r} references fields outside resource "
                f"{resource_id!r}: {unknown_fields}"
            )
        views_by_resource[resource_id].append(view)
    lookup_only = _lookup_only_resource_ids(document)
    for resource_id, resource_views in views_by_resource.items():
        if resource_id in lookup_only:
            continue
        if not resource_views:
            _fail(f"resource {resource_id!r} has no inspectable view")
        if not any(str(item["role"]) == "collection" for item in resource_views):
            _fail(f"resource {resource_id!r} requires a collection view")

    scope_errors = scope_findings(document, valid_value=_field_value_is_valid) + selection_findings(document)
    if scope_errors:
        _fail(scope_errors[0]['detail'])

    for relationship in relationships.values():
        target = resources.get(relationship["to_resource_ref"], {})
        target_fields = {field["id"]: field for field in target.get("fields") or []}
        for ref in relationship.get("label_field_refs") or []:
            if ref not in target_fields or target_fields[ref]["value_type"] not in FILTER_VALUE_TYPES:
                _fail(f"relationship {relationship['id']!r} label references an unknown or non-scalar target field {ref!r}")
        for side in ("from", "to"):
            resource_id = str(relationship[f"{side}_resource_ref"])
            field_id = str(relationship[f"{side}_field_ref"])
            resource = resources.get(resource_id)
            if resource is None:
                _fail(
                    f"relationship {relationship['id']!r} references unknown "
                    f"resource {resource_id!r}"
                )
            resource_fields = {str(item["id"]) for item in resource["fields"]}
            if field_id not in resource_fields and field_id != "id":
                _fail(
                    f"relationship {relationship['id']!r} references field "
                    f"{field_id!r} outside resource {resource_id!r}"
                )
        relationship = _foreign_key_relationship(relationship)
        from_resource = resources[str(relationship["from_resource_ref"])]
        to_resource = resources[str(relationship["to_resource_ref"])]
        from_field_id = str(relationship["from_field_ref"])
        to_field_id = str(relationship["to_field_ref"])
        from_field = next(
            (
                item
                for item in from_resource["fields"]
                if str(item["id"]) == from_field_id
            ),
            None,
        )
        to_field = next(
            (
                item
                for item in to_resource["fields"]
                if str(item["id"]) == to_field_id
            ),
            None,
        )
        if from_field is not None and not _relationship_field_types_compatible(
            from_field, to_field
        ):
            _fail(
                f"relationship {relationship['id']!r} connects incompatible "
                f"fields {from_field_id!r} and {to_field_id!r}"
            )
        target_values = [
            record.get(to_field_id)
            for record in to_resource["records"]
            if record.get(to_field_id) not in (None, "")
        ]
        missing_values = sorted(
            {
                str(record[from_field_id])
                for record in from_resource["records"]
                if record.get(from_field_id) not in (None, "")
                and record.get(from_field_id) not in target_values
            }
        )
        if missing_values:
            _fail(
                f"relationship {relationship['id']!r} has source values with no "
                f"target {to_field_id!r}: {missing_values}"
            )
        cardinality_errors = _relationship_cardinality_errors(relationship, from_resource, to_resource)
        if cardinality_errors:
            _fail("; ".join(cardinality_errors))

    for resource_id, resource in resources.items():
        resource_views = views_by_resource[resource_id]
        view_ids = {str(item["id"]) for item in resource_views}
        slice_document = {
            "schema": SEMANTIC_PROTOTYPE_SCHEMA,
            "document_id": document["document_id"],
            "brief_ref": document["brief_ref"],
            "brief_digest": document["brief_digest"],
            "title": copy.deepcopy(document["title"]),
            "layout": copy.deepcopy(document["layout"]),
            "resource": copy.deepcopy(resource),
            "views": [
                    legacy_view(item)
                for item in resource_views
            ],
            "commands": [
                copy.deepcopy(item)
                for item in commands.values()
                if str(item["view_ref"]) in view_ids
            ],
            "representative_states": [
                legacy_state(item, views)
                for item in states.values()
                if str(item["view_ref"]) in view_ids
            ],
            "requirement_bindings": [],
            "capability_gaps": [],
        }
        _validate_semantic_prototype_v1(
            slice_document, brief=None, require_primary=False,
            lookup_only=resource_id in lookup_only,
            record_state_ids=frozenset(str(item["id"]) for item in states.values() if item["proof"]["kind"] == "field_predicate"),
        )

    for index, state in enumerate(states.values()):
        view = views[str(state["view_ref"])]
        findings = _state_proof_findings(state, view, index=index)
        if findings:
            _fail(findings[0]["detail"])

    query_controls = {
        str(control["id"]): dict(control)
        for view in views.values()
        for control in view.get("query_controls") or []
    }
    refs = {
        *(f"resource:{identifier}" for identifier in resources),
        *(f"relationship:{identifier}" for identifier in relationships),
        *(f"field:{identifier}" for identifier in all_fields),
        *(f"view:{identifier}" for identifier in views),
        *(f"query:{identifier}" for identifier in query_controls),
        *(f"command:{identifier}" for identifier in commands),
        *(f"state:{identifier}" for identifier in states),
    }
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
    pending_refs: set[str] = set()
    for item in document.get("automation_requirements") or []:
        requirement_ref = str(item["requirement_ref"])
        if requirement_ref in pending_refs:
            _fail(f"duplicate automation requirement {requirement_ref!r}")
        pending_refs.add(requirement_ref)
        if not requirement_ref.startswith(("job:", "residual:")):
            _fail("automation requirements may only defer jobs or residual requirements, not UI contracts")
        if not any(ref.startswith(("view:", "state:")) for ref in bindings.get(requirement_ref, set())):
            _fail(f"automation requirement {requirement_ref!r} needs a visible prototype binding")
        if brief is not None:
            eligible = {
                str(entry.get("id"))
                for group in ("principal_jobs", "residual_requirements")
                for entry in brief.get(group) or []
            }
            if requirement_ref not in eligible:
                _fail(f"automation requirement {requirement_ref!r} is not an accepted job or residual requirement")

    gap_refs = [str(item["requirement_ref"]) for item in document["capability_gaps"]]
    if len(set(gap_refs)) != len(gap_refs):
        _fail("duplicate capability gap requirement_ref")
    gaps = set(gap_refs)
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
        from .semantic_bindings import binding_findings
        findings = binding_findings(document, brief)
        if findings:
            raise SemanticPrototypeValidationError(findings)
    return document


def _merge_locale_dictionaries(
    target: dict[str, dict[str, str]], source: Mapping[str, Any]
) -> None:
    for locale, raw_dictionary in source.items():
        if not isinstance(raw_dictionary, Mapping):
            continue
        dictionary = target.setdefault(str(locale), {})
        for key, raw_text in raw_dictionary.items():
            text = str(raw_text)
            if key in dictionary and dictionary[key] != text:
                _fail(f"localization key {key!r} has conflicting values")
            dictionary[str(key)] = text


def _relationship_cardinality_errors(
    relationship: Mapping[str, Any], source: Mapping[str, Any], target: Mapping[str, Any]
) -> list[str]:
    errors = []
    for side, resource in (("to", target), ("from", source)):
        if side == "from" and relationship["cardinality"] != "one_to_one":
            continue
        values = [record.get(relationship[f"{side}_field_ref"]) for record in resource.get("records") or []]
        values = [value for value in values if value is not None and value != ""]
        if not all(isinstance(value, (str, int, float, bool)) for value in values):
            continue
        keys = [
            ("number" if isinstance(value, (int, float)) and not isinstance(value, bool) else type(value).__name__, value)
            for value in values
        ]
        if len(keys) != len(set(keys)):
            errors.append(f"relationship {relationship['id']!r} requires unique {'target' if side == 'to' else 'source'} values")
    return errors


def _foreign_key_relationship(relationship: Mapping[str, Any]) -> dict[str, Any]:
    """Orient reference operations by declared cardinality, not fixture contents."""
    result = dict(relationship)
    if result["cardinality"] == "many_to_many":
        _fail("many_to_many relationships require an explicit link resource with scalar foreign keys")
    if result["cardinality"] == "one_to_many":
        for name in ("resource_ref", "field_ref"):
            result[f"from_{name}"], result[f"to_{name}"] = result[f"to_{name}"], result[f"from_{name}"]
        # Authored labels describe the original target, not the referenced parent.
        # Its existing collection remains the safe display fallback.
        result["label_field_refs"] = []
        result["cardinality"] = "many_to_one"
    return result


def _prototype_relation_option_fields(
    *,
    resource_id: str,
    resource: dict[str, Any],
    relationships: Sequence[Mapping[str, Any]],
    resources: Mapping[str, Mapping[str, Any]],
    views: Sequence[Mapping[str, Any]],
    project_ref: str | None = None,
) -> dict[str, dict[str, Any]]:
    lookups: dict[str, dict[str, Any]] = {}
    fields = {
        str(item["id"]): item
        for item in resource.get("fields") or []
        if isinstance(item, dict)
    }
    for relationship in relationships:
        relationship = _foreign_key_relationship(relationship)
        if str(relationship.get("from_resource_ref") or "") != resource_id:
            continue
        field = fields.get(str(relationship.get("from_field_ref") or ""))
        target = resources.get(str(relationship.get("to_resource_ref") or ""))
        if field is None or target is None:
            continue
        target_records = [
            dict(item)
            for item in target.get("records") or []
            if isinstance(item, Mapping)
        ]
        target_field_id = str(relationship.get("to_field_ref") or "")
        target_field = next((item for item in target["fields"] if item["id"] == target_field_id), None)
        if not _relationship_field_types_compatible(field, target_field):
            continue
        target_values = [record.get(target_field_id) for record in target_records]
        if any(value in (None, "") for value in target_values):
            continue

        display_field_ids: list[str] = list(relationship.get("label_field_refs") or [])
        target_resource_id = str(relationship.get("to_resource_ref") or "")
        for view in ([] if display_field_ids else views):
            if (
                str(view.get("resource_ref") or "") == target_resource_id
                and str(view.get("role") or "") == "collection"
            ):
                display_field_ids = [
                    str(item)
                    for item in view.get("field_refs") or []
                    if str(item) != target_field_id
                ]
                break
        selected_display_fields: list[str] = []
        for display_field_id in display_field_ids:
            selected_display_fields.append(display_field_id)
            labels = [
                tuple(str(record.get(item) or "") for item in selected_display_fields)
                for record in target_records
            ]
            if not relationship.get("label_field_refs") and len(labels) == len(set(labels)):
                break

        lookups[str(field["id"])] = {
            "optionsDataSource": {"kind": "resourceQuery", "resourceType": _runtime_resource_type(target_resource_id, project_ref), "query": {"limit": 100}},
            "optionValuePath": target_field_id,
            "optionLabelPaths": selected_display_fields,
        }

        relationship_id = str(relationship.get("id") or "relationship")
        options: list[dict[str, Any]] = []
        for record, target_value in zip(target_records, target_values, strict=True):
            parts = [
                str(record.get(item) or "").strip()
                for item in selected_display_fields
            ]
            label = " / ".join(item for item in parts if item) or str(target_value)
            key_suffix = _runtime_state_identifier(
                str(record.get("id") or target_value), fallback="option"
            )
            options.append(
                {
                    "value": target_value,
                    "label": {
                        "key": f"relationship.{relationship_id}.option.{key_suffix}",
                        **{locale: label for locale in _text_locales(field["label"])},
                    },
                }
            )
        if options:
            field["value_type"] = "choice"
            field["options"] = options
    return lookups


def _normalize_v2_ownership(document: dict[str, Any], *, brief: Mapping[str, Any] | None) -> list[dict]:
    from .semantic_bindings import close_bindings

    resources = {item["id"]: item for item in document["resources"]}
    for resource_id, resource in resources.items():
        _prototype_relation_option_fields(
            resource_id=resource_id, resource=resource, resources=resources,
            relationships=document["relationships"], views=document["views"],
        )
    return close_bindings(document, brief)


def _compile_editor_surfaces(
    document: Mapping[str, Any], webui: dict[str, Any], source_map: dict[str, list[str]],
    dictionaries: dict[str, dict[str, str]],
) -> None:
    application = webui["ui"]["application"]
    widgets = application["desktop"]["pageSchema"]["widgets"]
    for view in document["views"]:
        surface = view.get("surface", "inline")
        if view["role"] != "editor":
            if surface != "inline":
                _fail(f"{view['role']} view {view['id']!r} requires inline surface")
            continue
        editor = next(widget for widget in widgets if widget["id"] == view["id"])
        selection = editor["inputs"]["selectedStateKey"]
        commands = [command for command in document["commands"] if command["view_ref"] == view["id"]]
        toolbar: dict[str, Any] = {
            "id": f"open-{view['id']}", "area": view["region_role"], "type": "ui.actions",
            "inputs": {"variant": "adaptiveToolbar", "buttons": []}, "actions": [],
        }
        if any(command["kind"] == "create" for command in commands):
            label, label_i18n = _localized({"key": "prototype.editor.new", "en": "New", "ru": "Добавить"}, dictionaries)
            toolbar["inputs"]["buttons"].append({"id": "new", "label": label, "label_i18n": label_i18n, "icon": "add-outline"})
            toolbar["actions"].append({"on": "click:new", "type": "updateState", "params": {selection: ""}})
        if surface != "inline":
            modal_id = f"editor-{view['id']}"
            if any(command["kind"] != "create" for command in commands):
                toolbar["inputs"]["buttons"].append({"id": "edit", "label": editor["title"], "label_i18n": editor["title_i18n"], "icon": "create-outline", "enabledIf": f"$state.{selection} !== ''"})
            for button in toolbar["inputs"]["buttons"]:
                toolbar["actions"].append({"on": f"click:{button['id']}", "type": "openModal", "params": {"modalId": modal_id}})
            widgets.remove(editor)
            editor["area"] = "main"
            editor["inputs"]["closeOnSuccess"] = True
            application.setdefault("modals", {})[modal_id] = {
                "title": editor["title"], "title_i18n": editor["title_i18n"],
                "presentation": {"kind": "sideSheet" if surface == "side_sheet" else "modal"},
                "schema": {"id": modal_id, "layout": {"type": "stack", "areas": [{"id": "main"}]}, "widgets": [editor]},
            }
            old = f"ui.application.desktop.pageSchema.widgets.@{view['id']}"
            new = f"ui.application.modals.{modal_id}.schema.widgets.@{view['id']}"
            for refs in source_map.values():
                refs[:] = [ref.replace(old, new) if ref == old or ref.startswith(old + ".") else ref for ref in refs]
        if toolbar["inputs"]["buttons"]:
            collections = [item for item in document["views"] if item["resource_ref"] == view["resource_ref"] and item["role"] == "collection"]
            details = [item for item in document["views"] if item["resource_ref"] == view["resource_ref"] and item["role"] == "details"]
            edit_views = [item for item in document["views"] if item["resource_ref"] == view["resource_ref"] and item["role"] == "editor" and any(command["kind"] != "create" and command["view_ref"] == item["id"] for command in document["commands"])]
            related_context = any(
                item["resource_ref"] != view["resource_ref"]
                and ((item.get("filter") or {}).get("state_ref") == selection
                     or (item.get("selection_filter") or {}).get("source_view_ref") in {collection["id"] for collection in collections})
                for item in document["views"]
            )
            if surface != "inline" and any(button["id"] == "edit" for button in toolbar["inputs"]["buttons"]):
                if details:
                    for detail_view in details:
                        detail_widget = next(widget for widget in widgets if widget["id"] == detail_view["id"])
                        detail_widget.setdefault("actions", []).append({
                            "on": f"click:edit-{view['id']}", "label": editor["title"], "label_i18n": editor["title_i18n"],
                            "type": "openModal", "params": {"modalId": modal_id},
                            "enabledIf": f"$state.{selection} !== ''",
                        })
                elif len(edit_views) == 1 and not related_context:
                    for collection_view in collections:
                        collection_widget = next(widget for widget in widgets if widget["id"] == collection_view["id"])
                        collection_widget["actions"].append({"on": "select", "type": "openModal", "params": {"modalId": modal_id}})
                if details or (collections and len(edit_views) == 1 and not related_context):
                    toolbar["inputs"]["buttons"] = [button for button in toolbar["inputs"]["buttons"] if button["id"] != "edit"]
                    toolbar["actions"] = [action for action in toolbar["actions"] if action["on"] != "click:edit"]
            if not toolbar["inputs"]["buttons"]:
                continue
            if collections:
                first = next(widget for widget in widgets if widget["id"] == collections[0]["id"])
                toolbar["area"] = first["area"]
                widgets.insert(widgets.index(first), toolbar)
            else:
                widgets.append(toolbar)


def _compile_semantic_prototype_v2(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
) -> dict[str, Any]:
    document = _validate_semantic_prototype_v2(value, brief=brief)
    resources = {str(item["id"]): dict(item) for item in document["resources"]}
    views = {str(item["id"]): dict(item) for item in document["views"]}
    commands = {str(item["id"]): dict(item) for item in document["commands"]}
    dictionaries: dict[str, dict[str, str]] = {locale: {} for locale in _text_locales(document["title"])}
    title, title_i18n = _localized(document["title"], dictionaries)
    widgets: list[dict[str, Any]] = []
    initial_state: dict[str, Any] = {}
    source_map: dict[str, list[str]] = {}
    state_checks: list[dict[str, Any]] = []
    prototype_resources: list[dict[str, Any]] = []
    record_schemas: dict[str, Any] = {}
    resource_policies: dict[str, Any] = {}
    references = [_foreign_key_relationship(item) for item in document["relationships"]]

    for resource_id, resource in resources.items():
        resource_views = [
            item
            for item in document["views"]
            if str(item["resource_ref"]) == resource_id
        ]
        view_ids = {str(item["id"]) for item in resource_views}
        resource_states = [
            item
            for item in document["representative_states"]
            if str(item["view_ref"]) in view_ids
        ]
        slice_document = {
            "schema": SEMANTIC_PROTOTYPE_SCHEMA,
            "document_id": document["document_id"],
            "brief_ref": document["brief_ref"],
            "brief_digest": document["brief_digest"],
            "title": copy.deepcopy(document["title"]),
            "layout": copy.deepcopy(document["layout"]),
            "resource": copy.deepcopy(resource),
            "views": [
                    legacy_view(item)
                for item in resource_views
            ],
            "commands": [
                copy.deepcopy(item)
                for item in document["commands"]
                if str(item["view_ref"]) in view_ids
            ],
            "representative_states": [
                legacy_state(item, views)
                for item in resource_states
            ],
            "requirement_bindings": [],
            "capability_gaps": [],
        }
        lookups = _prototype_relation_option_fields(
            resource_id=resource_id,
            resource=slice_document["resource"],
            relationships=document["relationships"],
            resources=resources,
            views=document["views"],
            project_ref=project_ref,
        )
        runtime_type = _runtime_resource_type(resource_id, project_ref)
        policy = {"read_only_when": copy.deepcopy(resource["read_only_when"])} if resource.get("read_only_when") else {}
        relationships = [
            {
                "field_ref": item["from_field_ref"],
                "target_resource_type": _runtime_resource_type(item["to_resource_ref"], project_ref),
                "target_field_ref": item["to_field_ref"],
                **({"unique_source": True} if item["cardinality"] == "one_to_one" else {}),
            }
            for item in references if item["from_resource_ref"] == resource_id
        ]
        if relationships:
            policy["relationships"] = relationships
        if policy:
            resource_policies[runtime_type] = policy
        if not resource_views:
            validated = _validate_semantic_prototype_v1(slice_document, require_primary=False, lookup_only=True)
            record_schemas[runtime_type] = _prototype_record_schema(validated["resource"])
            prototype_resources.append({
                "resource_ref": resource_id, "resource_type": runtime_type,
                "records": [{**copy.deepcopy(record), "id": "::".join(str(record[key]).strip()
                            for key in resource["identity_field_refs"])} for record in resource["records"]],
            })
            continue
        compiled = _compile_semantic_prototype_v1(
            slice_document,
            brief=None,
            project_ref=project_ref,
            require_primary=False,
            record_state_ids=frozenset(str(item["id"]) for item in resource_states if item["proof"]["kind"] == "field_predicate"),
        )
        page = compiled["webui"]["ui"]["application"]["desktop"]["pageSchema"]
        if lookups:
            properties = page["meta"]["builder"]["prototype_record_schemas"][runtime_type]["properties"]
            for field in resource["fields"]:
                if field["id"] in lookups:
                    scalar = "number" if field["value_type"] == "number" else "boolean" if field["value_type"] == "boolean" else "string"
                    properties[field["id"]] = {"type": [scalar, "null"]}
            for widget in page["widgets"]:
                if widget["type"] != "ui.form":
                    continue
                for field in widget["inputs"]["fields"]:
                    if field["id"] in lookups:
                        field.update(type="dropdown", **copy.deepcopy(lookups[field["id"]]))
                        field.pop("options", None)
                        target_type = lookups[field["id"]]["optionsDataSource"]["resourceType"]
                        target_id = next(identifier for identifier in resources if _runtime_resource_type(identifier, project_ref) == target_type)
                        source_map.setdefault(f"resource:{target_id}", []).append(
                            f"ui.application.desktop.pageSchema.widgets.@{widget['id']}.inputs.fields.@{field['id']}.optionsDataSource.resourceType")
        record_schemas.update(page["meta"]["builder"]["prototype_record_schemas"])
        widgets.extend(copy.deepcopy(page["widgets"]))
        initial_state.update(copy.deepcopy(page.get("initialState") or {}))
        _merge_locale_dictionaries(dictionaries, compiled["locale_dictionaries"])
        for semantic_ref, runtime_refs in compiled["source_map"].items():
            source_map.setdefault(str(semantic_ref), []).extend(
                str(item) for item in runtime_refs
            )
        proofs = {str(item["id"]): dict(item["proof"]) for item in resource_states}
        for check in compiled["representative_state_checks"]:
            check = copy.deepcopy(dict(check))
            proof = proofs[str(check["state_id"])]
            check["proof"] = proof
            check["observable_runtime_refs"] = copy.deepcopy(
                source_map.get(f"state:{check['state_id']}") or []
            )
            state_checks.append(check)
        prototype_resources.append(
            {
                "resource_ref": resource_id,
                "resource_type": _runtime_resource_type(resource_id, project_ref),
                "records": copy.deepcopy(compiled["prototype_records"]),
            }
        )

    for relationship in document["relationships"]:
        relationship_ref = f"relationship:{relationship['id']}"
        source_map[relationship_ref] = list(
            dict.fromkeys(
                [
                    *source_map.get(f"field:{relationship['from_field_ref']}", []),
                    *source_map.get(f"field:{relationship['to_field_ref']}", []),
                ]
            )
        )

    region_roles = {str(view["region_role"]) for view in document["views"]}
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
                    "role": {"primary": "main", "supporting": "aux", "actions": "footer"}[region_role],
                }
                for region_role in ("primary", "supporting", "actions")
                if region_role in region_roles
            ],
        },
        "widgets": widgets,
        "initialState": initial_state,
        "meta": {
            "builder": {
                "semantic_source": SEMANTIC_PROTOTYPE_V2_SCHEMA,
                "prototype_record_schemas": record_schemas,
                "prototype_resource_policies": resource_policies,
                "semantic_digest": _digest(document),
                "brief_ref": document["brief_ref"],
                "relationships": copy.deepcopy(document["relationships"]),
                "capability_gaps": copy.deepcopy(document["capability_gaps"]),
                "acceptance_stage": "prototype",
                "automation_requirements": automation_obligations(document, brief),
            }
        },
    }
    webui = {
        "schema": "adaos.webui.v1",
        "generated_by": "builder.semantic_compiler.v2",
        "ui": {"application": {"desktop": {"pageSchema": page_schema}}},
    }
    _compile_editor_surfaces(document, webui, source_map, dictionaries)
    from .semantic_query_toolbar import compile_query_toolbars
    compile_query_toolbars(document, webui, source_map)
    compile_query_scopes(document, webui, source_map)
    compile_selection_filters(document, webui, source_map)
    from .semantic_media import compile_media
    compile_media(document, webui, prototype_resources, source_map)
    compile_presentations(document, webui, source_map)
    from .semantic_sections import compile_sections
    compile_sections(document, webui, source_map, dictionaries, localize=_localized)
    for check in state_checks:
        check["observable_runtime_refs"] = copy.deepcopy(source_map.get(f"state:{check['state_id']}") or [])
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

    views_by_ref = {f"view:{identifier}": view for identifier, view in views.items()}
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

    result = {
        "schema": SEMANTIC_COMPILE_RESULT_SCHEMA,
        "semantic_digest": _digest(document),
        "webui": webui,
        "locale_dictionaries": dictionaries,
        "prototype_resources": prototype_resources,
        "representative_state_checks": state_checks,
        "source_map": source_map,
        "requirement_runtime_map": requirement_map,
        "binding_expansions": binding_expansions,
        "capability_gaps": copy.deepcopy(document["capability_gaps"]),
        "automation_requirements": automation_obligations(document, brief),
        "validation": validation,
    }
    if len(prototype_resources) == 1:
        result["prototype_records"] = copy.deepcopy(
            prototype_resources[0]["records"]
        )
    return result


def validate_semantic_prototype(
    value: Mapping[str, Any], *, brief: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    if value.get("schema") == SEMANTIC_PROTOTYPE_V2_SCHEMA:
        return _validate_semantic_prototype_v2(value, brief=brief)
    return _validate_semantic_prototype_v1(value, brief=brief)


def normalize_semantic_prototype_candidate(
    value: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    if value.get("schema") != SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA:
        return _normalize_semantic_prototype_candidate_v1(value, brief=brief)
    candidate, _ = _canonicalize_semantic_prototype_candidate_v2(value)
    return _validate_semantic_prototype_v2(
        _lower_semantic_prototype_candidate_v2(candidate, brief=brief), brief=brief
    )


def compile_semantic_prototype(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
) -> dict[str, Any]:
    if value.get("schema") == SEMANTIC_PROTOTYPE_V2_SCHEMA:
        return _compile_semantic_prototype_v2(
            value, brief=brief, project_ref=project_ref
        )
    return _compile_semantic_prototype_v1(
        value, brief=brief, project_ref=project_ref
    )


def compile_semantic_prototype_candidate(
    value: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
    project_ref: str | None = None,
) -> dict[str, Any]:
    if value.get("schema") != SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA:
        return _compile_semantic_prototype_candidate_v1(
            value, brief=brief, project_ref=project_ref
        )
    candidate, normalizations = _canonicalize_semantic_prototype_candidate_v2(value)
    requirement_findings = _requirement_contract_findings(candidate, brief=brief)
    try:
        semantic_document = _lower_semantic_prototype_candidate_v2(
            candidate, brief=brief
        )
        normalizations.extend(_normalize_v2_ownership(semantic_document, brief=brief))
        model_findings = _semantic_v2_model_findings(semantic_document)
        model_findings.extend(presentation_findings(semantic_document))
        model_findings.extend(scope_findings(semantic_document, valid_value=_field_value_is_valid))
        model_findings.extend(selection_findings(semantic_document))
        from .semantic_bindings import binding_findings
        model_findings.extend(binding_findings(semantic_document, brief))
        if model_findings or requirement_findings:
            raise SemanticPrototypeValidationError(
                [*model_findings, *requirement_findings]
            )
        normalizations.append(
            {
                "kind": "authoritative_brief_provenance",
                "from": "Prototype Brief",
                "to": str(semantic_document["brief_ref"]),
                "target": "$.brief_ref|$.brief_digest",
            }
        )
        result = _compile_semantic_prototype_v2(
            semantic_document,
            brief=brief,
            project_ref=project_ref,
        )
    except BuilderWorkflowError as exc:
        if isinstance(exc, SemanticPrototypeValidationError):
            raise
        prefix = "invalid semantic Prototype: "
        detail = str(exc)
        if detail.startswith(prefix):
            detail = detail[len(prefix) :]
        matching_finding = next(
            (
                item
                for item in requirement_findings
                if str(item.get("detail") or "") == detail
            ),
            None,
        )
        findings = [
            copy.deepcopy(matching_finding)
            if matching_finding is not None
            else {
                "code": "semantic.compiler_contract_invalid" if detail.startswith("compiled WebUI") else "semantic.validation_failed",
                "path": "$",
                "detail": detail,
            }
        ]
        findings.extend(
            item
            for item in requirement_findings
            if str(item.get("detail") or "") != detail
        )
        raise SemanticPrototypeValidationError(findings) from exc
    result["semantic_document"] = semantic_document
    result["normalizations"] = normalizations
    return result


__all__ = [
    "SEMANTIC_COMPILE_RESULT_SCHEMA",
    "SEMANTIC_PROTOTYPE_CANDIDATE_SCHEMA",
    "SEMANTIC_PROTOTYPE_CANDIDATE_V2_SCHEMA",
    "SEMANTIC_PROTOTYPE_SCHEMA",
    "SEMANTIC_PROTOTYPE_V2_SCHEMA",
    "SemanticPrototypeValidationError",
    "compile_semantic_prototype_candidate",
    "compile_semantic_prototype",
    "normalize_semantic_prototype_candidate",
    "semantic_prototype_candidate_contract",
    "semantic_prototype_provider_contract",
    "semantic_prototype_contract",
    "validate_semantic_prototype",
]
