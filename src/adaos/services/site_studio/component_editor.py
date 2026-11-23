"""Shared typed property projection for platform-owned static components."""
from __future__ import annotations

from copy import deepcopy

from .authoring import inspect_source_anchor
from .contracts import digest

LABELS = {"title": "Заголовок", "body": "Текст", "label": "Название", "name": "Имя",
          "description": "Описание", "href": "Ссылка", "statement": "Основной текст", "accent": "Акцент",
          "headingHidden": "Скрыть заголовок", "columns": "Колонки", "tone": "Тон", "variant": "Вариант",
          "alt": "Подпись изображения", "caption": "Подпись", "publicKey": "Браузерный ключ карты",
          "mapSaturation": "Насыщенность подложки (-1…1)",
          "presentation": "Оформление", "height": "Высота секции", "paddingTop": "Отступ сверху, px",
          "paddingBottom": "Отступ снизу, px", "noteAlign": "Выравнивание подписи"}


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
            operations.append({"operation_id": f"property-{len(operations) + 1}",
                "kind": "set_section_field" if key in section_paths else "set_resource_field",
                "target": section_paths[key] if key in section_paths else "resources." + editor["resource_id"] + ".data." + editor["field_paths"][key],
                "value": deepcopy(value)})
    return operations
