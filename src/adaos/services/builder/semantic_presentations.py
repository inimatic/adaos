"""Domain-neutral view presentation adapters for the semantic compiler."""

from __future__ import annotations

import copy
from collections.abc import Mapping

from .workflow import BuilderWorkflowError


EXTENDED_PRESENTATIONS = frozenset({"board", "tree", "chart", "accordion"})
VIEW_EXTRAS = frozenset({"surface", "media", "presentation_options", "field_display", "section", "scope_filters"})


def legacy_view(view: Mapping) -> dict:
    """Reuse v1 resource/command lowering without claiming its list is the final view."""
    result = {key: copy.deepcopy(value) for key, value in view.items()
              if key not in VIEW_EXTRAS and key != "resource_ref"}
    if result.get("presentation") in EXTENDED_PRESENTATIONS:
        result["presentation"] = "list"
    return result


def view_extras(view: Mapping) -> dict:
    result = {key: copy.deepcopy(view[key]) for key in VIEW_EXTRAS if key in view}
    if view.get("presentation") in EXTENDED_PRESENTATIONS:
        result["presentation"] = view["presentation"]
    return result


def presentation_findings(document: Mapping) -> list[dict]:
    resources = {resource["id"]: resource for resource in document["resources"]}
    findings = []
    for index, view in enumerate(document["views"]):
        def reject(detail: str) -> None:
            findings.append({"code": "semantic.presentation_invalid", "path": f"$.views[{index}]", "detail": detail})
        fields = {field["id"]: field for field in resources[view["resource_ref"]]["fields"]}
        visible = view["field_refs"]
        presentation = view.get("presentation")
        options = view.get("presentation_options") or {}
        if options and presentation not in EXTENDED_PRESENTATIONS:
            reject("presentation_options require board, tree, chart or accordion")
        if view.get("field_display") and (view["role"] != "collection" or presentation not in {"list", "table", "cards", "accordion"}):
            reject("field_display requires a list, table, cards or accordion collection")
        display_refs = [entry["field_ref"] for entry in view.get("field_display") or []]
        if len(display_refs) != len(set(display_refs)) or any(ref not in visible for ref in display_refs):
            reject("field_display must reference unique visible fields")
        if presentation not in EXTENDED_PRESENTATIONS:
            continue
        if presentation in {'tree', 'chart'} and view.get('media'):
            reject('Tree and chart media need a companion details or card view')
        if view["role"] != "collection":
            reject("Extended presentations require collection role")
            continue
        group, parent, value = (options.get(key) for key in ("group_field_ref", "parent_field_ref", "value_field_ref"))
        required = {"board": [group], "tree": [parent], "chart": [group, value], "accordion": [group]}[presentation]
        if any(not ref or ref not in fields for ref in required):
            reject(f"{presentation} requires declared presentation field references")
            continue
        if presentation in {"board", "accordion", "chart"} and any(ref not in visible for ref in required):
            reject("Grouping and plotted values must be included in field_refs")
        if presentation == "board":
            field = fields[group]
            if field["value_type"] != "choice" or any(not isinstance(option["value"], str) for option in field.get("options") or []):
                reject("Board lanes require a string choice field")
            if options.get("draggable") and not field.get("editable"):
                reject("A draggable board requires an editable lane field")
            if len(visible) > 10:
                reject('Board supports a title, lane and up to eight card fields; use details for additional fields')
        elif options.get("draggable"):
            reject("Only board lane movement is currently executable; other dragging needs a capability gap")
        if presentation == "chart":
            if fields[value]["value_type"] != "number":
                reject("Chart value_field_ref must be numeric")
            if set(visible) != {group, value}:
                reject("A chart renders only its x and y fields; place additional fields in a companion view")
        if presentation == "tree":
            if fields[parent]["value_type"] != "short_text":
                reject("Tree parent ids require a nullable short_text field")
            if len([ref for ref in visible if ref != parent]) > 3:
                reject("Tree renders a title, subtitle and value; use a details view for additional fields")
    return findings


def compile_presentations(document: Mapping, webui: dict, source_map: dict) -> None:
    findings = presentation_findings(document)
    if findings:
        raise BuilderWorkflowError("; ".join(item["detail"] for item in findings))
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    widgets = {widget["id"]: widget for widget in page["widgets"]}
    fields = {resource["id"]: {field["id"]: field for field in resource["fields"]} for resource in document["resources"]}
    for view in document["views"]:
        if view["role"] != "collection":
            continue
        widget = widgets[view["id"]]
        inputs = widget["inputs"]
        root = f"ui.application.desktop.pageSchema.widgets.@{view['id']}"
        refs = view["field_refs"]
        presentation = view.get("presentation")
        options = view.get("presentation_options") or {}
        group, parent, value = (options.get(key) for key in ("group_field_ref", "parent_field_ref", "value_field_ref"))
        resource_fields = fields[view["resource_ref"]]
        if presentation in EXTENDED_PRESENTATIONS:
            field_paths = {}
            title_key = inputs.get("titleKey", refs[0])
            if presentation == "board":
                field = resource_fields[group]
                # Labels are already localized in the v1 display dictionary.
                prefix = next((item.get("valueI18nPrefix") for item in inputs["meta"] if item["key"] == group), None)
                lanes = [{"id": item["value"], "label": next(text for locale, text in item["label"].items() if locale != "key"),
                          **({"label_i18n": {"key": f"{prefix}{item['value']}"}} if prefix else {})} for item in field["options"]]
                new_inputs = {"laneKey": group, "itemIdKey": "id", "titleKey": title_key,
                              "lanes": lanes, "dragDrop": bool(options.get("draggable")), "reorderWithinLane": False, "cardFields": []}
                field_paths[title_key] = root + ".inputs.titleKey"
                field_paths[group] = root + ".inputs.laneKey"
                for ref in refs:
                    if ref in {title_key, group}:
                        continue
                    meta = next(item for item in inputs["meta"] if item["key"] == ref)
                    new_inputs["cardFields"].append({key: item for key, item in meta.items() if key in {"key", "label", "label_i18n"}} | {"presentation": "text"})
                    field_paths[ref] = root + f".inputs.cardFields.{len(new_inputs['cardFields']) - 1}.key"
                widget.update(type="collection.board", inputs=new_inputs)
                if inputs.get('imageKey'):
                    new_inputs['imageKey'] = inputs['imageKey']
                if options.get("draggable"):
                    widget["actions"].append({"on": "move", "type": "resourceOperation",
                        "target": widget["dataSource"]["resourceType"], "params": {
                        "operation_id": "update",
                        "record_id": "$event.id", "payload": "$event.patch",
                    }})
            elif presentation == "chart":
                widget.update(type="visual.metricChart", inputs={"pointsKey": "items", "xKey": group, "yKey": value,
                                                                 "showSummary": True})
                widget.pop("actions", None)
                field_paths = {group: root + ".inputs.xKey", value: root + ".inputs.yKey"}
            elif presentation == "tree":
                visible = [ref for ref in refs if ref != parent]
                selection = next((key for action in widget.get('actions', [])
                                  if action.get('on') == 'select' and action.get('type') == 'updateState'
                                  for key, expression in action.get('params', {}).items() if expression == '$event.id'), None)
                new_inputs = {"parentIdKey": parent, "idKey": "id", "hideRoot": True,
                              "selectionMode": "all", "wrapTitles": True}
                if selection:
                    new_inputs['selectedStateKey'] = selection
                for ref, key in zip(visible, ("titleKey", "subtitleKey", "valueKey")):
                    new_inputs[key] = ref
                    field_paths[ref] = root + ".inputs." + key
                field_paths[parent] = root + ".inputs.parentIdKey"
                widget.update(type="collection.tree", inputs=new_inputs)
            else:
                inputs.update(variant="cards", groupBy=group, groupDisplay="accordion")
            if field_paths:
                for paths in source_map.values():
                    paths[:] = [path for path in paths if not path.startswith(root + ".inputs.")]
                for ref, path in field_paths.items():
                    source_map.setdefault(f"field:{ref}", []).append(path)
            if presentation != 'accordion' and inputs.get('emptyState'):
                empty = inputs['emptyState']
                widget['inputs'].update(emptyText=empty['title'], emptyText_i18n=empty['title_i18n'])
        for display in view.get("field_display") or []:
            inputs = widget["inputs"]
            ref = display["field_ref"]
            if widget["type"] == "ui.table":
                target = next(column for column in inputs["columns"] if column["key"] == ref)
            elif inputs.get("titleKey") == ref:
                inputs.update(titleOverflow=display["overflow"], titleAlign=display["align"])
                continue
            else:
                target = next(meta for meta in inputs["meta"] if meta["key"] == ref)
            target.update(overflow=display["overflow"], align=display["align"])
