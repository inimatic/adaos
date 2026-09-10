"""SDK facade for disposable executable Builder Prototype resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.resources.prototype import (
    PrototypeResourceService,
    prototype_webui_digest,
)


def _read_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for token in str(path or "").split("."):
        if not token:
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(token)
    return current


def _page_widgets(webui: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ui = webui.get("ui") if isinstance(webui.get("ui"), Mapping) else {}
    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    desktop = application.get("desktop") if isinstance(application.get("desktop"), Mapping) else {}
    page = desktop.get("pageSchema") if isinstance(desktop.get("pageSchema"), Mapping) else {}
    return [item for item in page.get("widgets") or [] if isinstance(item, Mapping)]


def _surface_widgets(webui: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    widgets = list(_page_widgets(webui))
    ui = webui.get("ui") if isinstance(webui.get("ui"), Mapping) else {}
    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    modals = application.get("modals") if isinstance(application.get("modals"), Mapping) else {}
    for modal in modals.values():
        if not isinstance(modal, Mapping):
            continue
        schema = modal.get("schema") if isinstance(modal.get("schema"), Mapping) else {}
        widgets.extend(
            item for item in schema.get("widgets") or [] if isinstance(item, Mapping)
        )
    return widgets


def _resource_query_widgets(
    webui: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    result: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for widget in _surface_widgets(webui):
        data_source = (
            widget.get("dataSource")
            if isinstance(widget.get("dataSource"), Mapping)
            else {}
        )
        if str(data_source.get("kind") or "") != "resourceQuery":
            continue
        resource_type = str(data_source.get("resourceType") or "").strip()
        if resource_type.startswith("prototype."):
            result.append((widget, data_source))
    return result


def _resource_action_operations(webui: Mapping[str, Any], resource_type: str) -> set[str]:
    return {
        str(dict(action.get("params") or {}).get("operation_id") or "").strip()
        for widget in _surface_widgets(webui)
        for action in widget.get("actions") or []
        if isinstance(action, Mapping)
        and str(action.get("type") or "") == "resourceOperation"
        and str(action.get("target") or "") == resource_type
        and str(dict(action.get("params") or {}).get("operation_id") or "").strip()
    }


def _json_type(values: Sequence[Any]) -> dict[str, Any]:
    observed = set()
    for value in values:
        if value is None:
            observed.add("null")
            continue
        if isinstance(value, bool):
            observed.add("boolean")
        elif isinstance(value, int):
            observed.add("integer")
        elif isinstance(value, float):
            observed.add("number")
        elif isinstance(value, str):
            observed.add("string")
        elif isinstance(value, list):
            observed.add("array")
        elif isinstance(value, Mapping):
            observed.add("object")
    if not observed:
        return {}
    if observed == {"integer", "number"}:
        return {"type": "number"}
    if len(observed) == 1:
        return {"type": next(iter(observed))}
    return {"anyOf": [{"type": item} for item in sorted(observed)]}


def _activity(activity_id: str, operation: str, record_schema: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "list":
        input_schema = {"type": "object", "additionalProperties": False}
        output_schema = {"type": "array", "items": dict(record_schema)}
        side_effect = "read_only"
    elif operation == "get":
        input_schema = {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
        }
        output_schema = {"oneOf": [dict(record_schema), {"type": "null"}]}
        side_effect = "read_only"
    elif operation == "create":
        input_schema = {
            "type": "object",
            "required": ["record"],
            "properties": {"record": dict(record_schema)},
            "additionalProperties": False,
        }
        output_schema = dict(record_schema)
        side_effect = "local_reversible"
    elif operation == "update":
        input_schema = {
            "type": "object",
            "required": ["id", "patch"],
            "properties": {"id": {"type": "string"}, "patch": {"type": "object"}},
            "additionalProperties": False,
        }
        output_schema = dict(record_schema)
        side_effect = "local_reversible"
    elif operation == "delete":
        input_schema = {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
        }
        output_schema = dict(record_schema)
        side_effect = "local_reversible"
    else:
        input_schema = {"type": "object", "additionalProperties": False}
        output_schema = {"type": "array", "items": dict(record_schema)}
        side_effect = "local_reversible"
    return {
        "activity_id": activity_id,
        "operation": operation,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "side_effect_class": side_effect,
        "implementation_status": "prototype_only",
        "implementation_ref": None,
    }


def derive_record_resource_spec(
    webui: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive one typed local CRUD resource from generic resource projections."""

    projections = _resource_query_widgets(webui)
    resource_types = {
        str(data_source.get("resourceType") or "").strip()
        for _, data_source in projections
    }
    if len(resource_types) != 1:
        raise ValueError(
            "Prototype records require exactly one prototype resourceQuery resourceType"
        )
    resource_type = next(iter(resource_types))
    normalized = [dict(item) for item in records if isinstance(item, Mapping)]
    if len(normalized) != len(records) or len(normalized) > 1000:
        raise ValueError("Prototype records must be a bounded object array")

    item_id_keys = {
        str(dict(widget.get("inputs") or {}).get("itemIdKey") or "id").strip()
        for widget, _ in projections
    }
    if len(item_id_keys) != 1:
        raise ValueError("Prototype projections must agree on one itemIdKey")
    item_id_key = next(iter(item_id_keys)) or "id"
    if "." in item_id_key:
        raise ValueError("Prototype itemIdKey must be a direct named resource property")
    for index, record in enumerate(normalized, start=1):
        record.setdefault(item_id_key, f"record-{index}")
        record.setdefault("revision", 1)
        if not str(record.get(item_id_key) or "").strip():
            raise ValueError("Prototype records require stable ids")

    form_fields: dict[str, Mapping[str, Any]] = {}
    for widget in _surface_widgets(webui):
        actions = [item for item in widget.get("actions") or [] if isinstance(item, Mapping)]
        if not any(
            str(action.get("type") or "") == "resourceOperation"
            and str(action.get("target") or "") == resource_type
            for action in actions
        ):
            continue
        inputs = widget.get("inputs") if isinstance(widget.get("inputs"), Mapping) else {}
        for field in inputs.get("fields") or []:
            if isinstance(field, Mapping) and str(field.get("id") or "").strip():
                form_fields[str(field.get("id"))] = field

    fields = sorted(
        {key for record in normalized for key in record}
        | set(form_fields)
        | {item_id_key, "revision"}
    )
    properties = {
        key: _json_type([record[key] for record in normalized if key in record])
        for key in fields
    }
    properties[item_id_key] = {"type": "string", "minLength": 1}
    properties["revision"] = {"type": "integer", "minimum": 1}
    # Form submission requirements are UI constraints, not persistence
    # invariants: a saved draft may legitimately omit an answer that is
    # required only when the user submits the form.
    required = [item_id_key, "revision"]
    record_schema = {
        "type": "object",
        "required": list(dict.fromkeys(required)),
        "properties": properties,
        "additionalProperties": False,
    }

    action_operations = _resource_action_operations(webui, resource_type)
    mutable_operations = [
        operation
        for operation in ("create", "update", "delete", "reset")
        if operation in action_operations
    ]
    operation_ids = ["list", "show", *mutable_operations]
    operations = [
        {
            "id": operation,
            "kind": {"show": "show", "list": "list"}.get(operation, operation),
            "risk": (
                "read"
                if operation in {"list", "show"}
                else "medium" if operation == "delete" else "low"
            ),
            "prototype_activity_id": "get" if operation == "show" else operation,
        }
        for operation in operation_ids
    ]
    widget_types = {str(widget.get("type") or "") for widget, _ in projections}
    widget_types.update(
        str(widget.get("type") or "")
        for widget in _surface_widgets(webui)
        if any(
            str(action.get("type") or "") == "resourceOperation"
            and str(action.get("target") or "") == resource_type
            for action in widget.get("actions") or []
            if isinstance(action, Mapping)
        )
    )
    view_kinds = [
        kind
        for component_type, kind in (
            ("ui.list", "list"),
            ("ui.table", "table"),
            ("collection.board", "board"),
            ("item.details", "detail"),
            ("ui.form", "form"),
        )
        if component_type in widget_types
    ]
    title_key = next(
        (
            key
            for key in ("title", "name", "label", "summary")
            if key in properties
        ),
        item_id_key,
    )
    source_id = resource_type.removeprefix("prototype.")
    definition = {
        "schema": "adaos.resource.definition.v1",
        "resource_type": resource_type,
        "version": "0.0.0-prototype",
        "title": str(projections[0][0].get("title") or "Prototype records"),
        "description": "Disposable typed records for Builder Prototype review.",
        "authority": {
            "provider": "prototype",
            "binding": source_id,
            "writes": "local_reversible",
            "source_of_truth": "builder_preview",
        },
        "record_schema_ref": f"inline:{resource_type}",
        "record_schema": record_schema,
        "query": {
            "default": str(projections[0][1].get("queryId") or "all"),
            "filters": [item_id_key, "search"],
            "sort": [title_key],
            "cursor": False,
            "include": [],
        },
        "operations": operations,
        "views": [
            {"id": kind, "kind": kind, "title": kind.replace("_", " ").title()}
            for kind in dict.fromkeys(view_kinds or ["list"])
        ],
        "events": {
            "emits": [
                "resource.record.created",
                "resource.record.updated",
                "resource.record.deleted",
            ]
        },
        "i18n": {"default_locale": "en", "locales": ["en", "ru"]},
        "access": {
            "role_fixtures": {
                "owner": {operation: "allowed" for operation in operation_ids},
                "guest": {
                    operation: "denied"
                    for operation in mutable_operations
                },
            }
        },
        "privacy": {
            "sensitivity": "synthetic",
            "retention": "preview",
            "external_export": "denied",
        },
        "readiness": {"states": ["ready", "empty", "validation_error"]},
    }
    data_definition = {
        "schema": "adaos.builder.prototype_data.v1",
        "source_id": source_id,
        "mode": "local_crud",
        "record_schema": record_schema,
        "seed": normalized,
        "activities": [
            _activity(activity, activity, record_schema)
            for activity in ["list", "get", *mutable_operations]
        ],
    }
    return {"resource_definition": definition, "data_definition": data_definition}


def derive_resource_spec(
    webui: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive the local Prototype resource for the selected generic projection."""

    projections = _resource_query_widgets(webui)
    if len(projections) == 1 and str(projections[0][0].get("type") or "") == "collection.board":
        return derive_board_resource_spec(webui, records)
    return derive_record_resource_spec(webui, records)


def derive_board_resource_spec(
    webui: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive a typed local CRUD resource from one declarative board projection."""

    boards = [item for item in _page_widgets(webui) if item.get("type") == "collection.board"]
    if len(boards) != 1:
        raise ValueError("board Prototype resource requires exactly one collection.board")
    board = boards[0]
    inputs = board.get("inputs") if isinstance(board.get("inputs"), Mapping) else {}
    data_source = board.get("dataSource") if isinstance(board.get("dataSource"), Mapping) else {}
    if str(data_source.get("kind") or "") != "resourceQuery":
        raise ValueError("board Prototype resource requires dataSource.kind=resourceQuery")
    resource_type = str(data_source.get("resourceType") or "").strip()
    if not resource_type.startswith("prototype."):
        raise ValueError("board Prototype resourceType must start with 'prototype.'")
    lane_key = str(inputs.get("laneKey") or "").strip()
    title_key = str(inputs.get("titleKey") or "").strip()
    item_id_key = str(inputs.get("itemIdKey") or "id").strip()
    if not lane_key or not title_key or any(
        "." in item for item in (lane_key, title_key, item_id_key)
    ):
        raise ValueError("board Prototype fields must be direct named resource properties")
    normalized = [dict(item) for item in records if isinstance(item, Mapping)]
    if len(normalized) != len(records) or len(normalized) > 1000:
        raise ValueError("board Prototype records must be a bounded object array")
    for index, record in enumerate(normalized, start=1):
        record.setdefault(item_id_key, f"card-{index}")
        record.setdefault("revision", 1)
        if not str(record.get(item_id_key) or "").strip():
            raise ValueError("board Prototype records require stable ids")
        if not str(record.get(title_key) or "").strip():
            raise ValueError(f"board Prototype records require {title_key}")
        if not str(record.get(lane_key) or "").strip():
            raise ValueError(f"board Prototype records require {lane_key}")
    lanes = [item for item in inputs.get("lanes") or [] if isinstance(item, Mapping)]
    lane_ids = [str(item.get("id") or "").strip() for item in lanes]
    unknown = sorted(
        {
            str(_read_path(record, lane_key) or "")
            for record in normalized
            if str(_read_path(record, lane_key) or "") not in set(lane_ids)
        }
    )
    if unknown:
        raise ValueError("board Prototype records use undeclared lanes: " + ", ".join(unknown))
    fields = sorted({key for record in normalized for key in record} | {item_id_key, title_key, lane_key, "revision"})
    properties = {
        key: _json_type([record[key] for record in normalized if key in record])
        for key in fields
    }
    properties[item_id_key] = {"type": "string", "minLength": 1}
    properties[title_key] = {"type": "string", "minLength": 1}
    properties[lane_key] = {"type": "string", "enum": lane_ids}
    properties["revision"] = {"type": "integer", "minimum": 1}
    record_schema = {
        "type": "object",
        "required": list(dict.fromkeys([item_id_key, title_key, lane_key, "revision"])),
        "properties": properties,
        "additionalProperties": False,
    }
    action_operations = {
        str(dict(action.get("params") or {}).get("operation_id") or "").strip()
        for widget in _surface_widgets(webui)
        for action in widget.get("actions") or []
        if isinstance(action, Mapping)
        and str(action.get("type") or "") == "resourceOperation"
        and str(action.get("target") or "") == resource_type
    }
    operation_ids = ["list", "show"]
    operation_ids.extend(
        operation
        for operation in ("create", "update", "delete", "reset")
        if operation in action_operations or (operation == "update" and inputs.get("dragDrop") is True)
    )
    operation_kind = {"show": "show", "list": "list"}
    operations = [
        {
            "id": operation,
            "kind": operation_kind.get(operation, operation),
            "risk": (
                "read"
                if operation in {"list", "show"}
                else "medium" if operation == "delete" else "low"
            ),
            "prototype_activity_id": "get" if operation == "show" else operation,
        }
        for operation in operation_ids
    ]
    activity_operations = ["list", "get"] + [
        item for item in operation_ids if item not in {"list", "show"}
    ]
    source_id = resource_type.removeprefix("prototype.")
    definition = {
        "schema": "adaos.resource.definition.v1",
        "resource_type": resource_type,
        "version": "0.0.0-prototype",
        "title": str(board.get("title") or "Prototype records"),
        "description": "Disposable typed records for Builder Prototype review.",
        "authority": {
            "provider": "prototype",
            "binding": source_id,
            "writes": "local_reversible",
            "source_of_truth": "builder_preview",
        },
        "record_schema_ref": f"inline:{resource_type}",
        "record_schema": record_schema,
        "query": {
            "default": str(data_source.get("queryId") or "all"),
            "filters": list(dict.fromkeys([item_id_key, lane_key, "search"])),
            "sort": [title_key],
            "cursor": False,
            "include": [],
        },
        "operations": operations,
        "views": [
            {"id": "board", "kind": "board", "title": "Board"},
            {"id": "detail", "kind": "detail", "title": "Record"},
            {"id": "form", "kind": "form", "title": "Edit record"},
        ],
        "events": {
            "emits": [
                "resource.record.created",
                "resource.record.updated",
                "resource.record.deleted",
            ]
        },
        "i18n": {"default_locale": "en", "locales": ["en", "ru"]},
        "access": {
            "role_fixtures": {
                "owner": {operation: "allowed" for operation in operation_ids},
                "guest": {operation: "denied" for operation in operation_ids if operation not in {"list", "show"}},
            }
        },
        "privacy": {
            "sensitivity": "synthetic",
            "retention": "preview",
            "external_export": "denied",
        },
        "readiness": {"states": ["ready", "empty", "validation_error"]},
    }
    data_definition = {
        "schema": "adaos.builder.prototype_data.v1",
        "source_id": source_id,
        "mode": "local_crud",
        "record_schema": record_schema,
        "seed": normalized,
        "activities": [
            _activity(activity, activity, record_schema) for activity in activity_operations
        ],
    }
    return {"resource_definition": definition, "data_definition": data_definition}


def materialize_resources(
    *,
    project_ref: str,
    change_id: str,
    revision: str,
    webui: Mapping[str, Any],
    resources: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Materialize bounded LLM resource specs under authoritative revision identity."""

    specs = [dict(item) for item in resources if isinstance(item, Mapping)]
    if len(specs) != len(resources):
        raise ValueError("prototype resources must be objects")
    if len(specs) > 8:
        raise ValueError("one Prototype revision supports at most 8 resources")
    identity = {
        "schema": "adaos.builder.prototype_resource.v1",
        "project_ref": str(project_ref).strip(),
        "change_id": str(change_id).strip(),
        "revision": str(revision).strip(),
        "webui_digest": prototype_webui_digest(webui),
    }
    service = PrototypeResourceService()
    results = []
    for spec in specs:
        if set(spec) != {"resource_definition", "data_definition"}:
            raise ValueError(
                "prototype resource spec must contain only resource_definition and data_definition"
            )
        result = service.materialize({**identity, **spec})
        state = result.get("state") if isinstance(result.get("state"), Mapping) else {}
        results.append(
            {
                "resource_type": state.get("resource_type"),
                "bundle_digest": state.get("bundle_digest"),
                "generation": state.get("generation"),
                "duplicate": bool(result.get("duplicate")),
            }
        )
    return {
        "ok": True,
        "project_ref": identity["project_ref"],
        "change_id": identity["change_id"],
        "revision": identity["revision"],
        "webui_digest": identity["webui_digest"],
        "resources": results,
    }


def validate_resource_spec(
    webui: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive and execute the side-effect-free validation used by materialization."""

    from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

    spec = derive_resource_spec(webui, records)
    PrototypeDataRuntime.start(spec["data_definition"])
    return spec


__all__ = [
    "derive_board_resource_spec",
    "derive_record_resource_spec",
    "derive_resource_spec",
    "materialize_resources",
    "validate_resource_spec",
]
