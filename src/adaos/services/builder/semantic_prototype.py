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


def _brief_requirement_ids(brief: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in (
        "principal_jobs",
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
    regions = _unique(document["layout"]["regions"], "region")

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
        if view["region_ref"] not in regions:
            _fail(
                f"view {view['id']!r} references unknown region {view['region_ref']!r}"
            )
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
                if fields[field_ref]["value_type"] != "choice":
                    _fail(f"filter query control {query_id!r} requires a choice field")

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
    resource_type = _runtime_resource_type(str(resource["id"]), project_ref)
    selection_ref = f"selected_{resource['id']}_id"
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
            "area": str(view["region_ref"]),
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
                "area": str(view["region_ref"]),
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
            else:
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
                    "id": str(region["id"]),
                    "role": "main" if region["role"] == "primary" else "auxiliary",
                }
                for region in document["layout"]["regions"]
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
        "source_map": source_map,
        "requirement_runtime_map": requirement_map,
        "binding_expansions": binding_expansions,
        "capability_gaps": copy.deepcopy(document["capability_gaps"]),
        "validation": validation,
    }


__all__ = [
    "SEMANTIC_COMPILE_RESULT_SCHEMA",
    "SEMANTIC_PROTOTYPE_SCHEMA",
    "compile_semantic_prototype",
    "semantic_prototype_contract",
    "validate_semantic_prototype",
]
