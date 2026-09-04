from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator


CATALOG_SCHEMA = "adaos.ui.capability_catalog.v1"
QUALIFICATION_SCHEMA = "adaos.ui.request_qualification.v1"
VALIDATION_SCHEMA = "adaos.ui.capability_validation.v1"

_ABI_ROOT = Path(__file__).resolve().parents[1] / "abi"
_CATALOG_PATH = _ABI_ROOT / "ui.capability_catalog.v1.json"
_CATALOG_SCHEMA_PATH = _ABI_ROOT / "ui.capability_catalog.v1.schema.json"
_WEBUI_SCHEMA_PATH = _ABI_ROOT / "webui.v1.schema.json"

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "одна": 1,
    "один": 1,
    "одно": 1,
    "одной": 1,
    "две": 2,
    "два": 2,
    "двух": 2,
    "три": 3,
    "трех": 3,
    "четыре": 4,
    "четырех": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
}
_BOARD_TERMS = {
    "kanban",
    "board",
    "task board",
    "status lane",
    "status column",
    "канбан",
    "доска задач",
    "колонки статусов",
}
_DRAG_TERMS = {
    "drag",
    "drop",
    "drag-and-drop",
    "drag and drop",
    "перетаски",
    "перетаскивание",
    "переносить мышью",
}
_IMAGE_TERMS = {
    "image",
    "images",
    "photo",
    "photos",
    "picture",
    "картинк",
    "изображен",
    "фото",
}
_FILTER_TERMS = {
    "filter",
    "filters",
    "search",
    "query",
    "фильтр",
    "фильтрац",
    "поиск",
}
_CRUD_TERMS = {
    "crud",
    "create edit delete",
    "create update delete",
    "создание редактирование удаление",
    "создавать редактировать удалять",
}
_CREATE_TERMS = {
    "create",
    "allow create",
    "create items",
    "create records",
    "creation",
    "создавать",
    "создание",
    "добавлять",
    "добавление",
}
_UPDATE_TERMS = {"update", "edit", "change", "редакт", "измен"}
_DELETE_TERMS = {"delete", "remove", "archive", "удал", "архив"}
_EDIT_TERMS = {"edit", "editing", "record editor", "редакт"}
_LITERAL_RENAME_PATTERNS = (
    re.compile(
        r"\b(?:переименуй(?:те)?|переименовать)\s+"
        r"(?P<kind>колонку|колонка|столбец|дорожку|заголовок|кнопку|раздел)\s+"
        r"(?P<old>[^.!?]+?)\s+в\s+(?P<new>[^.!?]+)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\brename\s+(?P<kind>column|lane|heading|title|button|section)\s+"
        r"(?P<old>[^.!?]+?)\s+to\s+(?P<new>[^.!?]+)",
        flags=re.IGNORECASE,
    ),
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain an object")
    return dict(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _terms(value: Any) -> set[str]:
    text = _normalized_text(value)
    return {
        token
        for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE)
        if len(token) >= 2
    }


def _capability_search_text(item: Mapping[str, Any]) -> str:
    aliases = item.get("aliases") if isinstance(item.get("aliases"), Mapping) else {}
    values = [
        item.get("id"),
        item.get("title"),
        item.get("summary"),
        *(alias for rows in aliases.values() if isinstance(rows, list) for alias in rows),
    ]
    return _normalized_text(" ".join(str(value or "") for value in values))


def _score(item: Mapping[str, Any], query: str) -> int:
    query_text = _normalized_text(query)
    query_terms = _terms(query)
    searchable = _capability_search_text(item)
    score = 0
    if query_text and query_text in searchable:
        score += 40
    for term in query_terms:
        if term in searchable:
            score += 5
    return score


@lru_cache(maxsize=1)
def ui_capability_catalog() -> dict[str, Any]:
    schema = _read_json(_CATALOG_SCHEMA_PATH)
    catalog = _read_json(_CATALOG_PATH)
    Draft202012Validator(schema).validate(catalog)
    webui_schema = _read_json(_WEBUI_SCHEMA_PATH)
    widget_types = (
        webui_schema.get("$defs", {}).get("widgetType", {}).get("enum", [])
        if isinstance(webui_schema.get("$defs"), Mapping)
        else []
    )
    enriched = {
        str(item.get("id") or "")
        for item in catalog.get("components") or []
        if isinstance(item, Mapping)
    }
    generated = [
        {
            "id": str(widget_type),
            "kind": "component",
            "title": str(widget_type),
            "summary": "Client-supported WebUI renderer without a curated semantic profile yet.",
            "manifest": {"widget_type": str(widget_type)},
            "responsive": {"contract": "renderer-defined"},
            "postconditions": ["The widget type resolves to a registered client renderer."],
            "semantic_profile": "minimal",
        }
        for widget_type in widget_types
        if str(widget_type) not in enriched
    ]
    result = copy.deepcopy(catalog)
    result["components"] = [*result.get("components", []), *generated]
    result["coverage"] = {
        "abi_widget_types": len(widget_types),
        "curated_component_profiles": len(enriched),
        "minimal_component_profiles": len(generated),
        "complete": len(result["components"]) == len(widget_types),
    }
    result["catalog_digest"] = _digest(catalog)
    return result


def search_ui_capabilities(
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    text = str(query or "").strip()
    if not text:
        raise ValueError("UI capability query is required")
    selected_kinds = {
        str(item or "").strip().lower() for item in kinds or () if str(item or "").strip()
    }
    rows: list[tuple[int, int, dict[str, Any]]] = []
    ordinal = 0
    catalog = ui_capability_catalog()
    for key, kind in (("layouts", "layout"), ("components", "component"), ("recipes", "recipe")):
        if selected_kinds and kind not in selected_kinds:
            continue
        for item in catalog.get(key) or []:
            if not isinstance(item, Mapping):
                continue
            score = _score(item, text)
            if score <= 0:
                continue
            rows.append((score, ordinal, copy.deepcopy(dict(item))))
            ordinal += 1
    rows.sort(key=lambda entry: (-entry[0], entry[1], str(entry[2].get("id") or "")))
    bounded = max(1, min(int(limit or 8), 24))
    items = []
    for rank, (score, _, item) in enumerate(rows[:bounded], start=1):
        items.append(
            {
                "id": item.get("id"),
                "kind": item.get("kind") or "recipe",
                "title": item.get("title"),
                "summary": item.get("summary"),
                "score": score,
                "rank": rank,
                "drill_down": {
                    "descriptor_id": "ui_capability_catalog",
                    "item_id": item.get("id"),
                },
            }
        )
    return {
        "schema": "adaos.ui.capability_search.v1",
        "query_digest": _digest({"query": text}),
        "catalog_version": catalog["catalog_version"],
        "count": len(items),
        "items": items,
    }


def get_ui_capability(item_id: str) -> dict[str, Any]:
    token = str(item_id or "").strip()
    if not token:
        raise ValueError("UI capability item_id is required")
    catalog = ui_capability_catalog()
    for key in ("layouts", "components", "recipes"):
        for item in catalog.get(key) or []:
            if isinstance(item, Mapping) and str(item.get("id") or "") == token:
                return copy.deepcopy(dict(item))
    raise KeyError(token)


def _contains_any(text: str, values: Iterable[str]) -> bool:
    return any(value in text for value in values)


def _number(value: str) -> int | None:
    token = _normalized_text(value)
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _nearby_number(text: str, noun_pattern: str, *, prefix: str = "") -> int | None:
    words = "|".join(re.escape(item) for item in _NUMBER_WORDS)
    number_pattern = rf"(\d+|{words})"
    patterns = [
        rf"{prefix}{number_pattern}\s+(?:[^\W_]+\s+){{0,2}}{noun_pattern}",
        rf"{noun_pattern}\s*(?:[:=-]\s*)?{number_pattern}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            for group in match.groups():
                parsed = _number(group)
                if parsed is not None:
                    return parsed
    return None


def _clean_literal(value: Any) -> str:
    return str(value or "").strip().strip("'\"«»“”„`").strip()


def _literal_text_change(request: str) -> dict[str, Any] | None:
    raw = " ".join(str(request or "").split())
    for pattern in _LITERAL_RENAME_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        old = _clean_literal(match.group("old"))
        new = _clean_literal(match.group("new"))
        if not old or not new or old.casefold() == new.casefold():
            return None
        kind = _normalized_text(match.group("kind"))
        return {
            "target_kind": (
                "column"
                if kind in {"колонку", "колонка", "столбец", "дорожку", "column", "lane"}
                else "text"
            ),
            "from": old,
            "to": new,
            "only_change": _contains_any(
                _normalized_text(raw),
                {
                    "больше ничего не меняй",
                    "ничего больше не меняй",
                    "change nothing else",
                    "do not change anything else",
                    "only this",
                },
            ),
        }
    return None


def _count_exact_scalar(value: Any, expected: str) -> int:
    if isinstance(value, str):
        return int(value == expected)
    if isinstance(value, Mapping):
        return sum(_count_exact_scalar(item, expected) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_count_exact_scalar(item, expected) for item in value)
    return 0


def qualify_ui_request(request: str) -> dict[str, Any]:
    text = _normalized_text(request)
    literal_text_change = _literal_text_change(request)
    board = _contains_any(text, _BOARD_TERMS) or bool(
        literal_text_change and literal_text_change.get("target_kind") == "column"
    )
    lane_count = (
        _nearby_number(text, r"(?:колон(?:ка|ки|ок|ку|ках)|columns?|lanes?)")
        if board
        else None
    )
    items_per_lane = (
        _nearby_number(
            text,
            r"(?:карточ(?:ка|ки|ек|ку)|cards?|tasks?)",
            prefix=r"(?:по\s+|per\s+)?",
        )
        if board
        else None
    )
    requires_drag_drop = board and _contains_any(text, _DRAG_TERMS)
    images_requested = _contains_any(text, _IMAGE_TERMS)
    requires_query = board and _contains_any(text, _FILTER_TERMS)
    all_crud = board and _contains_any(text, _CRUD_TERMS)
    operation_kinds = []
    for operation, terms in (
        ("create", _CREATE_TERMS),
        ("update", _UPDATE_TERMS),
        ("delete", _DELETE_TERMS),
    ):
        if all_crud or (board and _contains_any(text, terms)):
            operation_kinds.append(operation)
    concepts = []
    if board:
        concepts.append("kanban_board")
    if requires_drag_drop:
        concepts.append("drag_drop")
    if requires_query:
        concepts.append("resource_query")
    if operation_kinds:
        concepts.append("resource_crud")
    if literal_text_change:
        concepts.append("ui_text_rename")
    requirements: dict[str, Any] = {}
    if board:
        requirements.update(
            {
                "recipe_id": "recipe.kanban_board",
                "component_type": "collection.board",
                "layout_id": "layout.flow",
                "lane_count": lane_count,
                "items_per_lane": items_per_lane,
                "images_requested": images_requested,
                "drag_drop": requires_drag_drop,
                "resource_query": requires_query,
                "operation_kinds": operation_kinds,
                "record_edit": _contains_any(text, _EDIT_TERMS),
            }
        )
    if literal_text_change:
        requirements["literal_text_change"] = literal_text_change
    gaps: list[dict[str, Any]] = []
    return {
        "schema": QUALIFICATION_SCHEMA,
        "request_digest": _digest({"request": request}),
        "surface_kind": "board" if board else "ui" if literal_text_change else "unspecified",
        "concepts": concepts,
        "requirements": requirements,
        "capability_gaps": gaps,
        "ready": not gaps,
    }


def selected_ui_capabilities(request: str, *, limit: int = 8) -> dict[str, Any]:
    qualification = qualify_ui_request(request)
    catalog = ui_capability_catalog()
    selected_ids: list[str] = []
    requirements = qualification.get("requirements") or {}
    for key in ("recipe_id", "component_type", "layout_id"):
        value = str(requirements.get(key) or "").strip()
        if value and value not in selected_ids:
            selected_ids.append(value)
    if (
        requirements.get("resource_query")
        or requirements.get("operation_kinds")
    ) and "recipe.resource_board_workbench" not in selected_ids:
        selected_ids.append("recipe.resource_board_workbench")
    if not selected_ids and str(request or "").strip():
        selected_ids.extend(
            str(item.get("id") or "")
            for item in search_ui_capabilities(request, limit=limit).get("items") or []
            if str(item.get("id") or "")
        )
    root_ids = selected_ids[: max(1, limit)]
    expanded_ids = list(root_ids)
    index = {
        str(item.get("id") or ""): item
        for key in ("layouts", "components", "recipes")
        for item in catalog.get(key) or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    cursor = 0
    while cursor < len(expanded_ids) and len(expanded_ids) < 24:
        item = index.get(expanded_ids[cursor])
        cursor += 1
        requires = item.get("requires") if isinstance(item, Mapping) else None
        if not isinstance(requires, Mapping):
            continue
        for key in ("layouts", "components", "recipes"):
            values = requires.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                dependency_id = str(value or "").strip()
                if dependency_id and dependency_id not in expanded_ids:
                    expanded_ids.append(dependency_id)
                    if len(expanded_ids) >= 24:
                        break
            if len(expanded_ids) >= 24:
                break
    items = [get_ui_capability(item_id) for item_id in expanded_ids]
    return {
        "schema": "adaos.ui.capability_selection.v1",
        "status": "present",
        "catalog_ref": "descriptor:ui_capability_catalog",
        "catalog_version": catalog["catalog_version"],
        "catalog_digest": catalog["catalog_digest"],
        "qualification": qualification,
        "root_item_ids": root_ids,
        "dependency_closure": [item_id for item_id in expanded_ids if item_id not in root_ids],
        "items": items,
    }


def _page_schemas(webui: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    ui = webui.get("ui") if isinstance(webui.get("ui"), Mapping) else {}
    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    result: list[tuple[str, Mapping[str, Any]]] = []
    desktop = application.get("desktop") if isinstance(application.get("desktop"), Mapping) else {}
    page = desktop.get("pageSchema") if isinstance(desktop.get("pageSchema"), Mapping) else None
    if page is not None:
        result.append(("ui.application.desktop.pageSchema", page))
    modals = application.get("modals") if isinstance(application.get("modals"), Mapping) else {}
    for modal_id, modal in modals.items():
        if not isinstance(modal, Mapping):
            continue
        schema = modal.get("schema") if isinstance(modal.get("schema"), Mapping) else None
        if schema is not None:
            result.append((f"ui.application.modals.{modal_id}.schema", schema))
    return result


def _read_path(value: Any, path: str) -> Any:
    current = value
    for token in str(path or "").split("."):
        if not token:
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(token)
    return current


def _has_path(value: Any, path: str) -> bool:
    current = value
    for token in str(path or "").split("."):
        if not token:
            continue
        if not isinstance(current, Mapping) or token not in current:
            return False
        current = current[token]
    return True


def validate_webui_capabilities(webui: Mapping[str, Any]) -> dict[str, Any]:
    catalog = ui_capability_catalog()
    known_types = {
        str(item.get("id") or "")
        for item in catalog.get("components") or []
        if isinstance(item, Mapping)
    }
    findings: list[dict[str, Any]] = []
    for schema_path, page in _page_schemas(webui):
        layout = page.get("layout") if isinstance(page.get("layout"), Mapping) else {}
        layout_type = str(layout.get("type") or "").strip()
        if layout_type not in {"single", "stack", "split", "grid", "custom", "responsive"}:
            findings.append(
                {
                    "code": "ui.layout.type_unsupported",
                    "severity": "error",
                    "path": f"{schema_path}.layout.type",
                    "message": f"Unsupported layout type {layout_type!r}",
                }
            )
        widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
        initial_state = page.get("initialState") if isinstance(page.get("initialState"), Mapping) else {}
        for index, widget in enumerate(widgets):
            if not isinstance(widget, Mapping):
                continue
            widget_type = str(widget.get("type") or "").strip()
            widget_path = f"{schema_path}.widgets[{index}]"
            if widget_type not in known_types:
                findings.append(
                    {
                        "code": "ui.component.type_unsupported",
                        "severity": "error",
                        "path": f"{widget_path}.type",
                        "message": f"Unsupported component type {widget_type!r}",
                    }
                )
                continue
            data_source = widget.get("dataSource") if isinstance(widget.get("dataSource"), Mapping) else {}
            if str(data_source.get("kind") or "") == "resourceQuery":
                query = data_source.get("query") if isinstance(data_source.get("query"), Mapping) else {}
                state_refs = sorted(
                    set(
                        re.findall(
                            r"\$state\.([A-Za-z0-9_.-]+)",
                            json.dumps(query, ensure_ascii=False, sort_keys=True),
                        )
                    )
                )
                missing_refs = [ref for ref in state_refs if not _has_path(initial_state, ref)]
                if missing_refs:
                    findings.append(
                        {
                            "code": "ui.resource_query.state_uninitialized",
                            "severity": "error",
                            "path": f"{widget_path}.dataSource.query",
                            "message": (
                                "Resource query state references require defaults in the owning "
                                "page initialState: " + ", ".join(missing_refs)
                            ),
                        }
                    )
            if widget_type != "collection.board":
                continue
            inputs = widget.get("inputs") if isinstance(widget.get("inputs"), Mapping) else {}
            lanes = inputs.get("lanes") if isinstance(inputs.get("lanes"), list) else []
            lane_ids = [
                str(item.get("id") or "").strip()
                for item in lanes
                if isinstance(item, Mapping) and str(item.get("id") or "").strip()
            ]
            if len(lane_ids) != len(lanes) or len(set(lane_ids)) != len(lane_ids):
                findings.append(
                    {
                        "code": "ui.board.lanes_invalid",
                        "severity": "error",
                        "path": f"{widget_path}.inputs.lanes",
                        "message": "Board lanes require unique non-empty ids.",
                    }
                )
            lane_key = str(inputs.get("laneKey") or "").strip()
            rows = data_source.get("value") if str(data_source.get("kind") or "") == "static" else None
            if isinstance(rows, list) and lane_key and lane_ids:
                unknown = sorted(
                    {
                        str(_read_path(row, lane_key) or "").strip()
                        for row in rows
                        if isinstance(row, Mapping)
                        and str(_read_path(row, lane_key) or "").strip() not in set(lane_ids)
                    }
                )
                if unknown:
                    findings.append(
                        {
                            "code": "ui.board.item_lane_unknown",
                            "severity": "error",
                            "path": f"{widget_path}.dataSource.value",
                            "message": "Board items reference undeclared lanes: " + ", ".join(unknown),
                        }
                    )
            if inputs.get("dragDrop") is True:
                actions = widget.get("actions") if isinstance(widget.get("actions"), list) else []
                if not any(
                    isinstance(action, Mapping) and str(action.get("on") or "") == "move"
                    for action in actions
                ):
                    findings.append(
                        {
                            "code": "ui.board.move_action_missing",
                            "severity": "error",
                            "path": f"{widget_path}.actions",
                            "message": "A draggable board requires an on=move action that persists the lane change.",
                        }
                    )
                resource_type = str(data_source.get("resourceType") or "").strip()
                for action_index, action in enumerate(actions):
                    if not isinstance(action, Mapping) or str(action.get("on") or "") != "move":
                        continue
                    if str(action.get("type") or "") != "resourceOperation":
                        continue
                    params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
                    if (
                        not resource_type
                        or str(action.get("target") or "") != resource_type
                        or str(params.get("operation_id") or "") != "update"
                        or str(params.get("record_id") or "") != "$event.id"
                        or str(params.get("payload") or "") != "$event.patch"
                    ):
                        findings.append(
                            {
                                "code": "ui.board.resource_move_invalid",
                                "severity": "error",
                                "path": f"{widget_path}.actions[{action_index}]",
                                "message": (
                                    "A Resource Workbench move must target the board resourceType "
                                    "and use update with record_id=$event.id and payload=$event.patch."
                                ),
                            }
                        )
            actions = widget.get("actions") if isinstance(widget.get("actions"), list) else []
            for action_index, action in enumerate(actions):
                if not isinstance(action, Mapping) or str(action.get("type") or "") != "resourceOperation":
                    continue
                params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
                payload = params.get("payload")
                if isinstance(payload, Mapping) and payload.get("__noop") is True:
                    findings.append(
                        {
                            "code": "ui.resource_operation.noop_payload",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}].params.payload",
                            "message": "A declared resource mutation cannot use a no-op placeholder payload.",
                        }
                    )
                if (
                    str(action.get("on") or "") == "add"
                    and str(params.get("operation_id") or "") == "create"
                    and str(payload or "") == "$event.payload"
                ):
                    findings.append(
                        {
                            "code": "ui.board.create_event_invalid",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}].params.payload",
                            "message": (
                                "collection.board add emits laneId, laneKey, and defaults; it does not emit payload. "
                                "Use a typed form for required create fields."
                            ),
                        }
                    )
    return {
        "schema": VALIDATION_SCHEMA,
        "catalog_version": catalog["catalog_version"],
        "catalog_digest": catalog["catalog_digest"],
        "ok": not any(item.get("severity") == "error" for item in findings),
        "findings": findings,
    }


def evaluate_ui_request(
    request: str,
    webui: Mapping[str, Any],
    *,
    prototype_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    qualification = qualify_ui_request(request)
    capability_validation = validate_webui_capabilities(webui)
    postconditions: list[dict[str, Any]] = []
    requirements = qualification.get("requirements") or {}
    literal_text_change = (
        requirements.get("literal_text_change")
        if isinstance(requirements.get("literal_text_change"), Mapping)
        else None
    )
    if literal_text_change:
        source_text = str(literal_text_change.get("from") or "")
        target_text = str(literal_text_change.get("to") or "")
        source_count = _count_exact_scalar(webui, source_text)
        target_count = _count_exact_scalar(webui, target_text)
        postconditions.append(
            {
                "id": "ui.literal_text_change",
                "ok": source_count == 0 and target_count > 0,
                "expected": {
                    "absent": source_text,
                    "present": target_text,
                },
                "actual": {
                    "sourceCount": source_count,
                    "targetCount": target_count,
                },
            }
        )
    if requirements.get("component_type") == "collection.board":
        boards: list[Mapping[str, Any]] = []
        for _, page in _page_schemas(webui):
            boards.extend(
                widget
                for widget in page.get("widgets") or []
                if isinstance(widget, Mapping)
                and str(widget.get("type") or "") == "collection.board"
            )
        postconditions.append(
            {
                "id": "kanban.component",
                "ok": len(boards) == 1,
                "expected": "one collection.board",
                "actual": len(boards),
            }
        )
        if len(boards) == 1:
            board = boards[0]
            inputs = board.get("inputs") if isinstance(board.get("inputs"), Mapping) else {}
            lanes = inputs.get("lanes") if isinstance(inputs.get("lanes"), list) else []
            expected_lanes = requirements.get("lane_count")
            if expected_lanes is not None:
                postconditions.append(
                    {
                        "id": "kanban.lane_count",
                        "ok": len(lanes) == expected_lanes,
                        "expected": expected_lanes,
                        "actual": len(lanes),
                    }
                )
            expected_items = requirements.get("items_per_lane")
            data_source = board.get("dataSource") if isinstance(board.get("dataSource"), Mapping) else {}
            rows = (
                data_source.get("value")
                if str(data_source.get("kind") or "") == "static"
                else list(prototype_records or [])
                if str(data_source.get("kind") or "") == "resourceQuery"
                else None
            )
            lane_key = str(inputs.get("laneKey") or "").strip()
            if expected_items is not None and isinstance(rows, list) and lane_key:
                counts = {
                    str(lane.get("id") or ""): sum(
                        1
                        for row in rows
                        if isinstance(row, Mapping)
                        and str(_read_path(row, lane_key) or "") == str(lane.get("id") or "")
                    )
                    for lane in lanes
                    if isinstance(lane, Mapping)
                }
                postconditions.append(
                    {
                        "id": "kanban.items_per_lane",
                        "ok": bool(counts) and all(count == expected_items for count in counts.values()),
                        "expected": expected_items,
                        "actual": counts,
                    }
                )
            if requirements.get("images_requested") is False:
                image_key = str(inputs.get("imageKey") or "").strip()
                postconditions.append(
                    {
                        "id": "kanban.no_unrequested_images",
                        "ok": not image_key,
                        "expected": "no imageKey",
                        "actual": image_key or None,
                    }
                )
            if requirements.get("drag_drop") is True:
                actions = board.get("actions") if isinstance(board.get("actions"), list) else []
                move_action = any(
                    isinstance(action, Mapping) and str(action.get("on") or "") == "move"
                    for action in actions
                )
                postconditions.append(
                    {
                        "id": "kanban.drag_drop",
                        "ok": inputs.get("dragDrop") is True and move_action,
                        "expected": "dragDrop=true with an on=move action",
                        "actual": {
                            "dragDrop": inputs.get("dragDrop") is True,
                            "moveAction": move_action,
                        },
                    }
                )
            if requirements.get("resource_query") is True:
                data_source = board.get("dataSource") if isinstance(board.get("dataSource"), Mapping) else {}
                postconditions.append(
                    {
                        "id": "kanban.resource_query",
                        "ok": str(data_source.get("kind") or "") == "resourceQuery",
                        "expected": "dataSource.kind=resourceQuery",
                        "actual": data_source.get("kind"),
                    }
                )
                query = data_source.get("query") if isinstance(data_source.get("query"), Mapping) else {}
                serialized_query = json.dumps(query, ensure_ascii=False, sort_keys=True)
                query_state_refs = set(re.findall(r"\$state\.([A-Za-z0-9_.-]+)", serialized_query))
                state_write_actions = [
                    action
                    for _, page in _page_schemas(webui)
                    for widget in page.get("widgets") or []
                    if isinstance(widget, Mapping)
                    for action in widget.get("actions") or []
                    if isinstance(action, Mapping)
                    and str(action.get("type") or "") == "updateState"
                    and isinstance(action.get("params"), Mapping)
                ]
                state_writes = {
                    str(key)
                    for action in state_write_actions
                    for key in action.get("params", {})
                }
                executable_query_refs = {
                    ref
                    for ref in query_state_refs
                    if any(
                        isinstance(_read_path(action.get("params", {}), ref), str)
                        and "$event." in _read_path(action.get("params", {}), ref)
                        for action in state_write_actions
                    )
                }
                postconditions.append(
                    {
                        "id": "kanban.query_binding",
                        "ok": bool(executable_query_refs),
                        "expected": (
                            "resourceQuery.query references state written directly from a query-control event"
                        ),
                        "actual": {
                            "queryStateRefs": sorted(query_state_refs),
                            "stateWrites": sorted(state_writes),
                            "executableRefs": sorted(executable_query_refs),
                        },
                    }
                )
            expected_operations = set(requirements.get("operation_kinds") or [])
            if expected_operations:
                actual_operations = {
                    str(action.get("params", {}).get("operation_id") or "")
                    for _, page in _page_schemas(webui)
                    for widget in page.get("widgets") or []
                    if isinstance(widget, Mapping)
                    for action in widget.get("actions") or []
                    if isinstance(action, Mapping)
                    and str(action.get("type") or "") == "resourceOperation"
                    and isinstance(action.get("params"), Mapping)
                }
                postconditions.append(
                    {
                        "id": "kanban.resource_crud",
                        "ok": expected_operations.issubset(actual_operations),
                        "expected": sorted(expected_operations),
                        "actual": sorted(actual_operations),
                    }
                )
                resource_type = str(data_source.get("resourceType") or "").strip()
                resource_actions = [
                    (page_path, widget, action)
                    for page_path, page in _page_schemas(webui)
                    for widget in page.get("widgets") or []
                    if isinstance(widget, Mapping)
                    for action in widget.get("actions") or []
                    if isinstance(action, Mapping)
                    and str(action.get("type") or "") == "resourceOperation"
                    and str(action.get("target") or "") == resource_type
                ]
                if "create" in expected_operations:
                    create_forms = [
                        (page_path, widget, action)
                        for page_path, widget, action in resource_actions
                        if str(widget.get("type") or "") == "ui.form"
                        and str(action.get("on") or "") == "submit"
                        and str(action.get("params", {}).get("operation_id") or "") == "create"
                        and "$event.values"
                        in json.dumps(action.get("params", {}).get("payload"), sort_keys=True)
                    ]
                    title_key = str(inputs.get("titleKey") or "title").strip()
                    required_create_fields = {title_key, lane_key}
                    complete_create_forms = [
                        (page_path, widget, action)
                        for page_path, widget, action in create_forms
                        if required_create_fields.issubset(
                            {
                                str(field.get("id") or "").strip()
                                for field in (widget.get("inputs") or {}).get("fields", [])
                                if isinstance(field, Mapping)
                            }
                        )
                    ]
                    postconditions.append(
                        {
                            "id": "kanban.create_form",
                            "ok": bool(complete_create_forms),
                            "expected": (
                                "ui.form captures title and lane, then creates the board resource from $event.values"
                            ),
                            "actual": {
                                "forms": len(create_forms),
                                "completeForms": len(complete_create_forms),
                                "requiredFields": sorted(required_create_fields),
                            },
                        }
                    )
                    modal_create_ids = {
                        page_path[len("ui.application.modals.") : -len(".schema")]
                        for page_path, _, _ in complete_create_forms
                        if page_path.startswith("ui.application.modals.")
                        and page_path.endswith(".schema")
                    }
                    inline_create = any(
                        page_path == "ui.application.desktop.pageSchema"
                        for page_path, _, _ in complete_create_forms
                    )
                    board_actions = board.get("actions") if isinstance(board.get("actions"), list) else []
                    create_entry_actions = [
                        action
                        for action in board_actions
                        if isinstance(action, Mapping)
                        and str(action.get("on") or "") == "add"
                        and str(action.get("type") or "") == "openModal"
                        and str(
                            (action.get("params") or {}).get("modalId")
                            or (action.get("params") or {}).get("modal_id")
                            or ""
                        )
                        in modal_create_ids
                    ]
                    postconditions.append(
                        {
                            "id": "kanban.create_entry",
                            "ok": inline_create or bool(create_entry_actions),
                            "expected": "board add opens the complete create form, or that form is inline",
                            "actual": {
                                "inline": inline_create,
                                "modalIds": sorted(modal_create_ids),
                                "boardAddOpenModalActions": len(create_entry_actions),
                            },
                        }
                    )
                if requirements.get("record_edit") is True:
                    update_forms = [
                        (page_path, widget, action)
                        for page_path, widget, action in resource_actions
                        if str(widget.get("type") or "") == "ui.form"
                        and str(action.get("on") or "") == "submit"
                        and str(action.get("params", {}).get("operation_id") or "") == "update"
                        and str(action.get("params", {}).get("record_id") or "").strip()
                        and "$event.values"
                        in json.dumps(action.get("params", {}).get("payload"), sort_keys=True)
                    ]
                    postconditions.append(
                        {
                            "id": "kanban.edit_form",
                            "ok": bool(update_forms),
                            "expected": "ui.form submit updates one selected board record from $event.values",
                            "actual": len(update_forms),
                        }
                    )
                    edit_state_refs = {
                        match
                        for _, _, action in update_forms
                        for match in re.findall(
                            r"\$state\.([A-Za-z0-9_.-]+)",
                            str((action.get("params") or {}).get("record_id") or ""),
                        )
                    }
                    modal_edit_ids = {
                        page_path[len("ui.application.modals.") : -len(".schema")]
                        for page_path, _, _ in update_forms
                        if page_path.startswith("ui.application.modals.")
                        and page_path.endswith(".schema")
                    }
                    board_actions = board.get("actions") if isinstance(board.get("actions"), list) else []
                    modal_events = {
                        str(action.get("on") or "")
                        for action in board_actions
                        if isinstance(action, Mapping)
                        and str(action.get("type") or "") == "openModal"
                        and str(
                            (action.get("params") or {}).get("modalId")
                            or (action.get("params") or {}).get("modal_id")
                            or ""
                        )
                        in modal_edit_ids
                    }
                    edit_selection_events = {
                        str(action.get("on") or "")
                        for action in board_actions
                        if isinstance(action, Mapping)
                        and str(action.get("type") or "") == "updateState"
                        and any(
                            str(_read_path(action.get("params", {}), ref) or "") == "$event.id"
                            for ref in edit_state_refs
                        )
                    }
                    inline_edit = any(
                        page_path == "ui.application.desktop.pageSchema"
                        for page_path, _, _ in update_forms
                    )
                    edit_selection_ok = bool(
                        edit_state_refs
                        and (
                            (inline_edit and edit_selection_events)
                            or (modal_events & edit_selection_events)
                        )
                    )
                    postconditions.append(
                        {
                            "id": "kanban.edit_selection",
                            "ok": edit_selection_ok,
                            "expected": (
                                "the board writes the edited record id from $event.id on the same event that opens "
                                "a modal editor, or on selection for an inline editor"
                            ),
                            "actual": {
                                "stateRefs": sorted(edit_state_refs),
                                "modalEvents": sorted(modal_events),
                                "selectionEvents": sorted(edit_selection_events),
                                "inline": inline_edit,
                            },
                        }
                    )
    failures = [item for item in postconditions if not item.get("ok")]
    gaps = list(qualification.get("capability_gaps") or [])
    return {
        "schema": "adaos.ui.request_evaluation.v1",
        "qualification": qualification,
        "capability_validation": capability_validation,
        "postconditions": postconditions,
        "capability_gaps": gaps,
        "ok": capability_validation["ok"] and not failures and not gaps,
    }


__all__ = [
    "CATALOG_SCHEMA",
    "evaluate_ui_request",
    "get_ui_capability",
    "qualify_ui_request",
    "search_ui_capabilities",
    "selected_ui_capabilities",
    "ui_capability_catalog",
    "validate_webui_capabilities",
]
