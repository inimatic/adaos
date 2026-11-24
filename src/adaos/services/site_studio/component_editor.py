"""Shared typed property projection for platform-owned static components."""
from __future__ import annotations

from copy import deepcopy
import re

from .authoring import inspect_source_anchor
from .contracts import digest

LABELS = {"title": "Заголовок", "body": "Текст", "label": "Название", "name": "Имя",
          "description": "Описание", "href": "Ссылка", "statement": "Основной текст", "accent": "Акцент",
          "headingHidden": "Скрыть заголовок", "columns": "Колонки", "tone": "Тон", "variant": "Вариант",
          "alt": "Подпись изображения", "caption": "Подпись", "publicKey": "Браузерный ключ карты",
          "mapSaturation": "Насыщенность подложки (-1…1)",
          "mapLightness": "Яркость геометрии подложки (-1…1)",
          "markerOpacity": "Непрозрачность маркеров (0…1)", "markerStrokeColor": "Цвет контура маркеров",
          "presentation": "Оформление", "height": "Высота секции", "paddingTop": "Отступ сверху, px",
          "paddingBottom": "Отступ снизу, px", "noteAlign": "Выравнивание подписи",
          "intro": "Введение", "map": "Карта", "colors": "Цвета обозначений", "filters": "Фильтры",
          "metrics": "Показатели", "layers": "Слои типологии", "distributions": "Статистика"}


def _anchor_references(source, route, section):
    """Retain typed link locations so renaming a section keeps navigation valid."""
    anchor = section.get("anchor_id") or section["id"]
    local_resources = {item["resource_ref"] for item in route["sections"]}
    references = []
    for resource in source["resources"]:
        prefixes = [route.get("path", "/"), route.get("canonical_url", "")]
        if resource["id"] in local_resources:
            prefixes.append("")
        links = {prefix + "#" + anchor: prefix + "#" for prefix in prefixes}

        def visit(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = path + "." + key
                    if key in {"href", "target", "primary_target"} and isinstance(child, str) and child in links:
                        references.append({"target": child_path, "prefix": links[child]})
                    else:
                        visit(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, path + "." + str(index))

        visit(resource["data"], "resources." + resource["id"] + ".data")
    return references


def component_editor(source, section_id, *, revision, node_path="", catalog=None):
    route = next(route for route in source["routes"] if any(section["id"] == section_id for section in route["sections"]))
    selected = inspect_source_anchor(source, f"routes.{route['id']}.sections.{section_id}", catalog=catalog)
    resource = selected["resource"]
    # Programming capabilities need not turn into dozens of ordinary controls.
    # Visibility is owned by the same contract that validates chat edits.
    chat_fields = set()
    def collect_chat_fields(schema, prefix=''):
        for key, nested in schema.get('properties', {}).items():
            path = prefix + '.' + key if prefix else key
            if nested.get('x-site-editor') == 'chat':
                chat_fields.add(path)
            else:
                collect_chat_fields(nested, path)
    collect_chat_fields(selected['schema'])
    # A dataset is edited/imported as a whole. Its rows and coordinates are not
    # component settings or reorderable content cards, even for a small dataset.
    if selected["value"]["component"] in {"site.dashboard", "site.map"}:
        chat_fields.add("rows")
    def chat_only(path):
        return any(path == field or path.startswith(field + '.') for field in chat_fields)
    if chat_only(node_path):
        raise ValueError('This presentation field is edited through the site chat')
    value = resource["data"]
    for token in node_path.split(".") if node_path else []:
        value = value[int(token)] if isinstance(value, list) and token.isdigit() else value[token]
    fields, values, paths = [], {}, {}
    section_paths = {}
    if not node_path:
        section = selected["value"]
        anchor = section.get("anchor_id") or section_id
        fields.append({"id": "section_anchor_id", "title": "Якорь раздела", "type": "shortText",
                       "helpText": "Ссылка для меню: #" + anchor + ". При изменении якоря ссылки на этот раздел обновятся."})
        values["section_anchor_id"] = anchor
        section_paths["section_anchor_id"] = f"routes.{route['id']}.sections.{section_id}.anchor_id"
        fields.append({"id": "section_draft", "title": "Черновик", "type": "toggle"})
        values["section_draft"] = bool(section.get("draft", False))
        section_paths["section_draft"] = f"routes.{route['id']}.sections.{section_id}.draft"
    scalars = value.items() if isinstance(value, dict) else [("value", value)] if not isinstance(value, list) else []
    for key, item in scalars:
        if chat_only(node_path + '.' + key if node_path else key):
            continue
        if isinstance(item, (dict, list)) or item is None:
            continue
        path = node_path + "." + key if node_path and isinstance(value, dict) else key if isinstance(value, dict) else node_path
        identity = "field_" + digest(path)[7:23]
        control = "toggle" if isinstance(item, bool) else "number" if isinstance(item, (int, float)) else "longText" if len(str(item)) > 120 else "shortText"
        field = {"id": identity, "title": LABELS.get(key, key), "type": control}
        hint = next((hint for hint in selected["editor"].get("fields", []) if hint["path"] == path), None)
        if hint and hint.get("options"):
            field.update(type="singleChoice", options=[{"value": option, "label": option} for option in hint["options"]])
        fields.append(field)
        values[identity] = deepcopy(item)
        paths[identity] = path
    count = [0]
    def tree(item, path, title):
        count[0] += 1
        if count[0] > 600 or len(path.split(".")) > 10:
            return None
        children = []
        iterable = item.items() if isinstance(item, dict) else enumerate(item) if isinstance(item, list) else []
        for key, child in iterable:
            if chat_only(path + '.' + str(key) if path else str(key)):
                continue
            if not isinstance(child, (dict, list)) and not isinstance(item, list):
                continue
            child_path = path + "." + str(key) if path else str(key)
            child_title = (child.get("title") or child.get("name") or child.get("label") or str(key)) if isinstance(child, dict) else LABELS.get(str(key), str(key))
            node = tree(child, child_path, str(child_title))
            if node:
                children.append(node)
        return {"id": path, "title": title, "children": children}
    root = tree(resource["data"], "", str(resource["data"].get("title") or section_id))
    return {"id": section_id, "revision": revision, "resource_id": resource["id"], "node_path": node_path,
            "source_digest": digest(source), "fields": fields, "field_paths": paths,
            "section_field_paths": section_paths, **values,
            "section_href": "#" + (selected["value"].get("anchor_id") or section_id),
            "anchor_references": _anchor_references(source, route, selected["value"]) if not node_path else [],
            "reserved_anchors": [item.get("anchor_id") or item["id"] for item in route["sections"] if item["id"] != section_id],
            "tree": [root], "component": selected["value"]["component"]}


def component_field_operations(editor, payload):
    section_paths = editor.get("section_field_paths", {})
    if not isinstance(payload, dict) or not payload or set(payload) - (set(editor["field_paths"]) | set(section_paths)):
        raise ValueError("Only displayed typed properties can be saved")
    operations = []
    for key, value in payload.items():
        before = editor[key]
        if type(value) is not type(before) and not (type(value) in {int, float} and type(before) in {int, float}):
            raise ValueError("Property type changed")
        if value != before:
            if key == "section_anchor_id":
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", value) or value in editor.get("reserved_anchors", []):
                    raise ValueError("Якорь должен быть уникальным: латинская буква, затем буквы, цифры, дефис или подчёркивание")
            operations.append({"operation_id": f"property-{len(operations) + 1}",
                "kind": "set_section_field" if key in section_paths else "set_resource_field",
                "target": section_paths[key] if key in section_paths else "resources." + editor["resource_id"] + ".data." + editor["field_paths"][key],
                "value": deepcopy(value)})
            if key == "section_anchor_id":
                for reference in editor.get("anchor_references", []):
                    operations.append({"operation_id": f"property-{len(operations) + 1}", "kind": "set_resource_field",
                                       "target": reference["target"], "value": reference["prefix"] + value})
    return operations
