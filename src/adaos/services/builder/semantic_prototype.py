"""Validate and compile Builder semantic Prototype documents."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.ui_capabilities import validate_webui_capabilities

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
    return result


def _semantic_refs(
    *,
    resource_id: str,
    fields: Mapping[str, Any],
    views: Mapping[str, Any],
    commands: Mapping[str, Any],
    states: Mapping[str, Any],
) -> set[str]:
    return {
        f"resource:{resource_id}",
        *(f"field:{identifier}" for identifier in fields),
        *(f"view:{identifier}" for identifier in views),
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
    commands = _unique(document["commands"], "command")
    states = _unique(document["representative_states"], "state")
    regions = _unique(document["layout"]["regions"], "region")

    for field in fields.values():
        options = field.get("options")
        if field["value_type"] == "choice" and not options:
            _fail(f"choice field {field['id']!r} requires options")
        if field["value_type"] != "choice" and options:
            _fail(f"non-choice field {field['id']!r} cannot declare options")
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
    for index, record in enumerate(resource["records"]):
        record_id = str(record.get("id") or "").strip()
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
        unknown_commands = sorted(set(view["command_refs"]) - set(commands))
        if unknown_commands:
            _fail(f"view {view['id']!r} references unknown commands {unknown_commands}")
        filter_value = view.get("filter")
        if (
            isinstance(filter_value, Mapping)
            and filter_value["field_ref"] not in fields
        ):
            _fail(
                f"view {view['id']!r} references unknown filter field "
                f"{filter_value['field_ref']!r}"
            )
        if view["role"] == "details" and not view.get("selection_state_ref"):
            _fail(f"details view {view['id']!r} requires selection_state_ref")
        if view["role"] == "editor" and not any(
            fields[field_id]["editable"] for field_id in view["field_refs"]
        ):
            _fail(f"editor view {view['id']!r} has no editable fields")

    for command in commands.values():
        if command["view_ref"] not in views:
            _fail(
                f"command {command['id']!r} references unknown view {command['view_ref']!r}"
            )
        if command["id"] not in views[command["view_ref"]]["command_refs"]:
            _fail(
                f"command {command['id']!r} is not owned by view {command['view_ref']!r}"
            )
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
        if command["kind"] in {"update", "transition", "delete"} and not command.get(
            "selected_state_ref"
        ):
            _fail(f"command {command['id']!r} requires selected_state_ref")
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
        editable_fields_by_view = {
            f"view:{view['id']}": {
                f"field:{field_id}"
                for field_id in view["field_refs"]
                if fields[field_id]["editable"]
            }
            for view in views.values()
            if view["role"] == "editor"
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
                if not any(
                    bound & editable_fields_by_view[editor_ref]
                    for editor_ref in bound_editors
                ):
                    _fail(
                        f"capture_each requirement {requirement_id!r} must bind an "
                        "editable item field exposed by its editor"
                    )

    return document


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
    value: Mapping[str, Any], *, brief: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Compile a validated semantic document into canonical Prototype artifacts."""

    document = validate_semantic_prototype(value, brief=brief)
    dictionaries: dict[str, dict[str, str]] = {"en": {}, "ru": {}}
    resource = dict(document["resource"])
    fields = {str(item["id"]): dict(item) for item in resource["fields"]}
    commands = {str(item["id"]): dict(item) for item in document["commands"]}
    resource_type = f"prototype.{resource['id']}"
    initial_state: dict[str, Any] = {}
    widgets: list[dict[str, Any]] = []
    source_map: dict[str, list[str]] = {
        f"resource:{resource['id']}": [
            "ui.application.desktop.pageSchema.widgets[*].dataSource.resourceType"
        ]
    }

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
        filter_value = view.get("filter")
        if isinstance(filter_value, Mapping):
            state_ref = str(filter_value["state_ref"])
            initial_state.setdefault(state_ref, "")
            widget["dataSource"]["query"][str(filter_value["field_ref"])] = (
                f"$state.{state_ref}"
            )
        selection_ref = str(view.get("selection_state_ref") or "").strip()
        if selection_ref:
            initial_state.setdefault(selection_ref, "")

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
            if selection_ref:
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
            if selection_ref:
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
            if selection_ref:
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
            for command_id in view["command_refs"]:
                command = commands[command_id]
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
                selected_ref = str(command.get("selected_state_ref") or "").strip()
                if selected_ref:
                    initial_state.setdefault(selected_ref, "")
                    action["params"]["record_id"] = f"$state.{selected_ref}"
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
        for command_id in view["command_refs"]:
            source_map[f"command:{command_id}"] = [
                f"ui.application.desktop.pageSchema.widgets.@{view_id}.actions.@{command_id}"
            ]

    layout_type = {
        "flow": "stack",
        "split": "split",
        "grid": "grid",
        "focus_detail": "split",
    }[str(document["layout"]["pattern"])]
    page_schema = {
        "id": str(document["document_id"]),
        "title": title,
        "title_i18n": title_i18n,
        "layout": {
            "type": layout_type,
            "pattern": str(document["layout"]["pattern"]),
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

    requirement_map = {
        str(item["requirement_ref"]): [
            runtime_ref
            for semantic_ref in item["semantic_refs"]
            for runtime_ref in source_map.get(str(semantic_ref), [])
        ]
        for item in document["requirement_bindings"]
    }
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
        "prototype_records": copy.deepcopy(resource["records"]),
        "source_map": source_map,
        "requirement_runtime_map": requirement_map,
        "capability_gaps": copy.deepcopy(document["capability_gaps"]),
        "validation": validation,
    }


__all__ = [
    "SEMANTIC_COMPILE_RESULT_SCHEMA",
    "SEMANTIC_PROTOTYPE_SCHEMA",
    "compile_semantic_prototype",
    "validate_semantic_prototype",
]
