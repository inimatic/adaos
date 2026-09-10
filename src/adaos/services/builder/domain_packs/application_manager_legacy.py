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

_ABI_ROOT = Path(__file__).resolve().parents[3] / "abi"
_CATALOG_PATH = _ABI_ROOT / "ui.capability_catalog.v1.json"
_CATALOG_SCHEMA_PATH = _ABI_ROOT / "ui.capability_catalog.v1.schema.json"
_WEBUI_SCHEMA_PATH = _ABI_ROOT / "webui.v1.schema.json"
_DOMAIN_PACK_PATH = Path(__file__).with_name("applications.compatibility.v1.json")
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
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
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
        *(
            alias
            for rows in aliases.values()
            if isinstance(rows, list)
            for alias in rows
        ),
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
    domain_pack = _read_json(_DOMAIN_PACK_PATH)
    catalog["recipes"] = [
        *list(catalog.get("recipes") or []),
        *list(domain_pack.get("ui_recipes") or []),
    ]
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
            "postconditions": [
                "The widget type resolves to a registered client renderer."
            ],
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
        str(item or "").strip().lower()
        for item in kinds or ()
        if str(item or "").strip()
    }
    rows: list[tuple[int, int, dict[str, Any]]] = []
    ordinal = 0
    catalog = ui_capability_catalog()
    for key, kind in (
        ("layouts", "layout"),
        ("components", "component"),
        ("recipes", "recipe"),
    ):
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
                if kind
                in {"колонку", "колонка", "столбец", "дорожку", "column", "lane"}
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


def _contains_shape(actual: Any, expected: Any) -> bool:
    """Compare a required declarative shape while allowing adjacent metadata."""
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _contains_shape(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _contains_shape(actual_value, expected_value)
                for actual_value, expected_value in zip(actual, expected, strict=True)
            )
        )
    return actual == expected


def _prototype_iteration(request: str) -> dict[str, Any] | None:
    match = re.search(
        r"(?:prototype\s+(?:phase|iteration)|builder\s+phase)\s*[:#-]?\s*(\d+)\s*/\s*(\d+)",
        str(request or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    index = int(match.group(1))
    total = int(match.group(2))
    if index < 1 or total < 1 or index > total:
        return None
    return {
        "index": index,
        "total": total,
        "completion_required": index == total,
    }


def _recipe_iteration_phase(
    recipe: Mapping[str, Any], iteration: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    if not isinstance(iteration, Mapping):
        return None
    workflow = (
        recipe.get("implementation_workflow")
        if isinstance(recipe.get("implementation_workflow"), Mapping)
        else {}
    )
    phases = workflow.get("phases") if isinstance(workflow.get("phases"), list) else []
    index = int(iteration.get("index") or 0)
    total = int(iteration.get("total") or 0)
    if len(phases) != total or not 1 <= index <= len(phases):
        return None
    phase = phases[index - 1]
    return copy.deepcopy(dict(phase)) if isinstance(phase, Mapping) else None


def _focused_recipe_for_iteration(
    recipe: Mapping[str, Any], iteration: Mapping[str, Any] | None
) -> dict[str, Any]:
    result = copy.deepcopy(dict(recipe))
    phase = _recipe_iteration_phase(recipe, iteration)
    if not phase:
        return result
    composition = (
        recipe.get("composition")
        if isinstance(recipe.get("composition"), Mapping)
        else {}
    )
    composition_keys = {
        str(value or "").strip()
        for value in phase.get("composition_keys") or []
        if str(value or "").strip()
    }
    result["composition"] = {
        key: copy.deepcopy(value)
        for key, value in composition.items()
        if key in composition_keys
    }
    result["active_implementation_phase"] = phase
    workflow = (
        result.get("implementation_workflow")
        if isinstance(result.get("implementation_workflow"), Mapping)
        else {}
    )
    result["implementation_workflow"] = {
        "status_vocabulary": workflow.get("status_vocabulary"),
        "current_phase": phase.get("id"),
        "current_phase_index": iteration.get("index"),
        "total_phases": iteration.get("total"),
        "completion_required": iteration.get("completion_required"),
        "phases": [
            {
                key: copy.deepcopy(item.get(key))
                for key in ("id", "title", "outcome", "required_postconditions")
                if item.get(key) is not None
            }
            for item in workflow.get("phases") or []
            if isinstance(item, Mapping)
        ],
    }
    return result


def _required_recipe_postconditions(
    recipe_id: str, iteration: Mapping[str, Any] | None
) -> set[str] | None:
    if not isinstance(iteration, Mapping):
        return None
    if bool(iteration.get("completion_required")):
        return None
    try:
        recipe = get_ui_capability(recipe_id)
    except (KeyError, ValueError):
        return None
    phase = _recipe_iteration_phase(recipe, iteration)
    if not phase:
        return None
    required: set[str] = set()
    phase_index = int(iteration.get("index") or 0)
    workflow = recipe.get("implementation_workflow")
    phases = workflow.get("phases") if isinstance(workflow, Mapping) else []
    for item in phases[:phase_index]:
        if not isinstance(item, Mapping):
            continue
        required.update(
            str(value or "").strip()
            for value in item.get("required_postconditions") or []
            if str(value or "").strip()
        )
    return required


def qualify_ui_request(request: str) -> dict[str, Any]:
    text = _normalized_text(request)
    literal_text_change = _literal_text_change(request)
    prototype_iteration = _prototype_iteration(request)
    application_manager = "recipe.application_manager" in text or (
        "applications" in text
        and _contains_any(
            text,
            {"application", "mcp", "market", "installed", "extensions", "lifecycle"},
        )
    )
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
    if application_manager:
        concepts.append("application_manager")
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
    if application_manager:
        requirements.update(
            {
                "application_manager": True,
                "recipe_id": "recipe.application_manager",
                "mcp_read_tools": [
                    "applications.list",
                    "applications.show",
                    "applications.list_releases",
                    "applications.list_operations",
                    "applications.list_development_reports",
                ],
                "mcp_mutation_tools": ["applications.plan", "applications.apply"],
                "plan_kinds": ["install", "update", "select_track", "remove"],
                "tabs": ["details", "versions", "operations", "reports"],
            }
        )
    if prototype_iteration:
        requirements["prototype_iteration"] = prototype_iteration
    gaps: list[dict[str, Any]] = []
    return {
        "schema": QUALIFICATION_SCHEMA,
        "request_digest": _digest({"request": request}),
        "surface_kind": "application_manager"
        if application_manager
        else "board"
        if board
        else "ui"
        if literal_text_change
        else "unspecified",
        "concepts": concepts,
        "requirements": requirements,
        "capability_gaps": gaps,
        "ready": not gaps,
    }


def selected_ui_capabilities(request: str, *, limit: int = 8) -> dict[str, Any]:
    qualification = qualify_ui_request(request)
    catalog = ui_capability_catalog()
    index = {
        str(item.get("id") or ""): item
        for key in ("layouts", "components", "recipes")
        for item in catalog.get(key) or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    selected_ids: list[str] = []
    requirements = qualification.get("requirements") or {}
    if requirements.get("application_manager"):
        selected_ids.append("recipe.application_manager")
    for key in ("recipe_id", "component_type", "layout_id"):
        value = str(requirements.get(key) or "").strip()
        if value and value not in selected_ids:
            selected_ids.append(value)
    if (
        requirements.get("resource_query") or requirements.get("operation_kinds")
    ) and "recipe.resource_board_workbench" not in selected_ids:
        selected_ids.append("recipe.resource_board_workbench")
    request_text = str(request or "")
    for item_id in index:
        if (
            re.search(
                rf"(?<![\w.-]){re.escape(item_id)}(?![\w.-])",
                request_text,
                flags=re.IGNORECASE,
            )
            and item_id not in selected_ids
        ):
            selected_ids.append(item_id)
    if not selected_ids and str(request or "").strip():
        selected_ids.extend(
            str(item.get("id") or "")
            for item in search_ui_capabilities(request, limit=limit).get("items") or []
            if str(item.get("id") or "")
        )
    root_ids = selected_ids[: max(1, limit)]
    expanded_ids = list(root_ids)
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
    iteration = (
        requirements.get("prototype_iteration")
        if isinstance(requirements.get("prototype_iteration"), Mapping)
        else None
    )
    items = [
        _focused_recipe_for_iteration(get_ui_capability(item_id), iteration)
        if item_id in root_ids
        else get_ui_capability(item_id)
        for item_id in expanded_ids
    ]
    return {
        "schema": "adaos.ui.capability_selection.v1",
        "status": "present",
        "catalog_ref": "descriptor:ui_capability_catalog",
        "catalog_version": catalog["catalog_version"],
        "catalog_digest": catalog["catalog_digest"],
        "qualification": qualification,
        "root_item_ids": root_ids,
        "dependency_closure": [
            item_id for item_id in expanded_ids if item_id not in root_ids
        ],
        "items": items,
    }


def _page_schemas(webui: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    ui = webui.get("ui") if isinstance(webui.get("ui"), Mapping) else {}
    application = (
        ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
    )
    result: list[tuple[str, Mapping[str, Any]]] = []
    desktop = (
        application.get("desktop")
        if isinstance(application.get("desktop"), Mapping)
        else {}
    )
    page = (
        desktop.get("pageSchema")
        if isinstance(desktop.get("pageSchema"), Mapping)
        else None
    )
    if page is not None:
        result.append(("ui.application.desktop.pageSchema", page))
    modals = (
        application.get("modals")
        if isinstance(application.get("modals"), Mapping)
        else {}
    )
    for modal_id, modal in modals.items():
        if not isinstance(modal, Mapping):
            continue
        schema = (
            modal.get("schema") if isinstance(modal.get("schema"), Mapping) else None
        )
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
        if layout_type not in {
            "single",
            "stack",
            "split",
            "grid",
            "custom",
            "responsive",
        }:
            findings.append(
                {
                    "code": "ui.layout.type_unsupported",
                    "severity": "error",
                    "path": f"{schema_path}.layout.type",
                    "message": f"Unsupported layout type {layout_type!r}",
                }
            )
        widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
        initial_state = (
            page.get("initialState")
            if isinstance(page.get("initialState"), Mapping)
            else {}
        )
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
            data_source = (
                widget.get("dataSource")
                if isinstance(widget.get("dataSource"), Mapping)
                else {}
            )
            if str(data_source.get("kind") or "") == "resourceQuery":
                query = (
                    data_source.get("query")
                    if isinstance(data_source.get("query"), Mapping)
                    else {}
                )
                state_refs = sorted(
                    set(
                        re.findall(
                            r"\$state\.([A-Za-z0-9_.-]+)",
                            json.dumps(query, ensure_ascii=False, sort_keys=True),
                        )
                    )
                )
                missing_refs = [
                    ref for ref in state_refs if not _has_path(initial_state, ref)
                ]
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
            inputs = (
                widget.get("inputs")
                if isinstance(widget.get("inputs"), Mapping)
                else {}
            )
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
            rows = (
                data_source.get("value")
                if str(data_source.get("kind") or "") == "static"
                else None
            )
            if isinstance(rows, list) and lane_key and lane_ids:
                unknown = sorted(
                    {
                        str(_read_path(row, lane_key) or "").strip()
                        for row in rows
                        if isinstance(row, Mapping)
                        and str(_read_path(row, lane_key) or "").strip()
                        not in set(lane_ids)
                    }
                )
                if unknown:
                    findings.append(
                        {
                            "code": "ui.board.item_lane_unknown",
                            "severity": "error",
                            "path": f"{widget_path}.dataSource.value",
                            "message": "Board items reference undeclared lanes: "
                            + ", ".join(unknown),
                        }
                    )
            if inputs.get("dragDrop") is True:
                actions = (
                    widget.get("actions")
                    if isinstance(widget.get("actions"), list)
                    else []
                )
                if not any(
                    isinstance(action, Mapping)
                    and str(action.get("on") or "") == "move"
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
                    if (
                        not isinstance(action, Mapping)
                        or str(action.get("on") or "") != "move"
                    ):
                        continue
                    if str(action.get("type") or "") != "resourceOperation":
                        continue
                    params = (
                        action.get("params")
                        if isinstance(action.get("params"), Mapping)
                        else {}
                    )
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
            actions = (
                widget.get("actions") if isinstance(widget.get("actions"), list) else []
            )
            for action_index, action in enumerate(actions):
                if (
                    not isinstance(action, Mapping)
                    or str(action.get("type") or "") != "resourceOperation"
                ):
                    continue
                params = (
                    action.get("params")
                    if isinstance(action.get("params"), Mapping)
                    else {}
                )
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
    locale_dictionaries: Mapping[str, Mapping[str, Any]] | None = None,
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
    if requirements.get("application_manager"):
        application_recipe = next(
            (
                item
                for item in ui_capability_catalog().get("recipes") or []
                if isinstance(item, Mapping)
                and str(item.get("id") or "") == "recipe.application_manager"
            ),
            {},
        )
        application_composition = (
            application_recipe.get("composition")
            if isinstance(application_recipe.get("composition"), Mapping)
            else {}
        )
        localization_contract = (
            application_composition.get("localization")
            if isinstance(application_composition.get("localization"), Mapping)
            else {}
        )
        localization_glossary = {
            str(source): str(target)
            for source, target in (localization_contract.get("glossary") or {}).items()
            if str(source).strip() and str(target).strip()
        }
        pages = [page for _, page in _page_schemas(webui)]
        widgets = [
            widget
            for page in pages
            for widget in page.get("widgets") or []
            if isinstance(widget, Mapping)
        ]

        fixed_text_fields = {
            "title",
            "label",
            "searchPlaceholder",
            "emptyText",
            "loadingText",
            "trueLabel",
            "falseLabel",
            "addItemLabel",
            "moveItemLabel",
        }
        fixture_text_fields = {"title", "summary", "review_summary"}
        resolved_locale_dictionaries: dict[str, dict[str, str]] = {
            "en": {},
            "ru": {},
        }
        for locale, dictionary in (locale_dictionaries or {}).items():
            normalized_locale = str(locale).strip().lower()
            if normalized_locale not in resolved_locale_dictionaries or not isinstance(
                dictionary, Mapping
            ):
                continue
            resolved_locale_dictionaries[normalized_locale].update(
                {
                    str(key): str(value)
                    for key, value in dictionary.items()
                    if str(key).strip() and str(value).strip()
                }
            )
        application = (
            webui.get("ui", {}).get("application", {})
            if isinstance(webui.get("ui"), Mapping)
            else {}
        )
        resource_collections = [webui.get("resources")]
        if isinstance(application, Mapping):
            resource_collections.append(application.get("resources"))
        for collection in resource_collections:
            if not isinstance(collection, Mapping):
                continue
            for descriptor in collection.values():
                if not isinstance(descriptor, Mapping):
                    continue
                locale = str(descriptor.get("locale") or "").strip().lower()
                if locale not in resolved_locale_dictionaries:
                    continue
                if str(descriptor.get("role") or "").strip().lower() != "i18n":
                    continue
                dictionary = (
                    descriptor.get("dictionary")
                    or descriptor.get("messages")
                    or descriptor.get("value")
                )
                if not isinstance(dictionary, Mapping):
                    continue
                resolved_locale_dictionaries[locale].update(
                    {
                        str(key): str(value)
                        for key, value in dictionary.items()
                        if str(key).strip() and str(value).strip()
                    }
                )
        missing_localizations: list[str] = []
        invalid_localizations: list[dict[str, Any]] = []
        locale_value_mismatches: list[dict[str, str]] = []

        def localized_text(spec: Any, locale: str) -> str:
            if isinstance(spec, str):
                return (
                    resolved_locale_dictionaries[locale].get(spec.strip(), "").strip()
                )
            if not isinstance(spec, Mapping):
                return ""
            translations = spec.get("translations") or spec.get("locales")
            if isinstance(translations, Mapping):
                inline = str(translations.get(locale) or "").strip()
                if inline:
                    return inline
            inline = str(spec.get(locale) or "").strip()
            if inline:
                return inline
            key_value = str(spec.get("key") or "").strip()
            return resolved_locale_dictionaries[locale].get(key_value, "").strip()

        def localization_key(spec: Any) -> str:
            if isinstance(spec, str):
                return spec.strip()
            if isinstance(spec, Mapping):
                return str(spec.get("key") or "").strip()
            return ""

        def inspect_localizations(
            value: Any,
            *,
            path: tuple[str, ...],
            inside_fixtures: bool = False,
        ) -> None:
            if isinstance(value, Mapping):
                nested_inside_fixtures = inside_fixtures or (
                    bool(path) and path[-1] == "prototypeFixtures"
                )
                for key, raw in value.items():
                    key_text = str(key)
                    child_path = (*path, key_text)
                    inspect_localizations(
                        raw,
                        path=child_path,
                        inside_fixtures=nested_inside_fixtures,
                    )
                    required = key_text in fixed_text_fields or (
                        nested_inside_fixtures
                        and "when" not in path
                        and key_text in fixture_text_fields
                    )
                    if not required or key_text.endswith("_i18n"):
                        continue
                    if isinstance(raw, str):
                        fallback = raw.strip()
                    elif key_text == "categories" and isinstance(raw, list):
                        fallback = ", ".join(
                            str(item).strip() for item in raw if str(item).strip()
                        )
                    else:
                        continue
                    if (
                        not fallback
                        or fallback.startswith("$state.")
                        or (fallback.startswith("{") and fallback.endswith("}"))
                    ):
                        continue
                    sibling = value.get(f"{key_text}_i18n")
                    location = ".".join(child_path)
                    if not isinstance(sibling, (str, Mapping)):
                        missing_localizations.append(location)
                        continue
                    key_value = localization_key(sibling)
                    localized = {
                        locale: localized_text(sibling, locale)
                        for locale in ("en", "ru")
                    }
                    missing_locales = [
                        locale for locale in ("en", "ru") if not localized[locale]
                    ]
                    english = localized["en"]
                    if not key_value or missing_locales or english != fallback:
                        invalid_localizations.append(
                            {
                                "path": location,
                                "key": key_value,
                                "missingLocales": missing_locales,
                                "englishMatchesFallback": english == fallback,
                            }
                        )
                    expected_russian = localization_glossary.get(fallback)
                    if expected_russian and localized["ru"] != expected_russian:
                        locale_value_mismatches.append(
                            {
                                "path": location,
                                "key": key_value,
                                "locale": "ru",
                                "expected": expected_russian,
                                "actual": localized["ru"],
                            }
                        )
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    inspect_localizations(
                        item,
                        path=(*path, str(index)),
                        inside_fixtures=inside_fixtures,
                    )

        for index, page in enumerate(pages):
            inspect_localizations(page, path=("pages", str(index)))

        placeholder_markers = {"placeholder", "todo", "tbd", "translate me"}
        invalid_locale_entries = [
            {
                "key": key,
                "locales": [
                    locale
                    for locale in ("en", "ru")
                    if str(resolved_locale_dictionaries[locale].get(key) or "")
                    .strip()
                    .casefold()
                    in placeholder_markers
                ],
            }
            for key in sorted(
                set(resolved_locale_dictionaries["en"])
                & set(resolved_locale_dictionaries["ru"])
            )
            if any(
                str(resolved_locale_dictionaries[locale].get(key) or "")
                .strip()
                .casefold()
                in placeholder_markers
                for locale in ("en", "ru")
            )
        ]

        expected_value_prefixes = {
            "application.lifecycle": "applications.lifecycle.",
            "application.visibility": "applications.visibility.",
            "application.display.categories": "applications.category.",
            "installation.status": "applications.installation.status.",
            "subscription.update_track": "applications.update_track.",
            "subscription.update_policy": "applications.update_policy.",
            "local_development.phase": "applications.development.phase.",
            "local_development.status": "applications.development.status.",
            "local_development.publication_status": (
                "applications.development.publication_status."
            ),
            "operation.kind": "applications.operation.kind.",
            "operation.status": "applications.operation.status.",
            "lifecycle": "applications.release.lifecycle.",
            "kind": "applications.operation.kind.",
            "status": "applications.operation.status.",
        }
        missing_value_prefixes: list[dict[str, str]] = []
        for widget_index, widget in enumerate(widgets):
            inputs = widget.get("inputs")
            if not isinstance(inputs, Mapping):
                continue
            for key_name, prefix_name in (
                ("titleKey", "titleI18nPrefix"),
                ("subtitleKey", "subtitleI18nPrefix"),
                ("previewKey", "previewI18nPrefix"),
                ("badgeKey", "badgeI18nPrefix"),
            ):
                path_value = str(inputs.get(key_name) or "").strip()
                expected_prefix = expected_value_prefixes.get(path_value)
                if not expected_prefix:
                    continue
                actual_prefix = str(inputs.get(prefix_name) or "").strip()
                if actual_prefix != expected_prefix:
                    missing_value_prefixes.append(
                        {
                            "path": f"pages.0.widgets.{widget_index}.inputs.{prefix_name}",
                            "field": path_value,
                            "expected": expected_prefix,
                            "actual": actual_prefix,
                        }
                    )
            for collection_name in ("meta", "fields"):
                values = inputs.get(collection_name)
                if not isinstance(values, list):
                    continue
                for value_index, field in enumerate(values):
                    if not isinstance(field, Mapping):
                        continue
                    path_value = str(
                        field.get("path") or field.get("key") or ""
                    ).strip()
                    expected_prefix = expected_value_prefixes.get(path_value)
                    if not expected_prefix:
                        continue
                    actual_prefix = str(field.get("valueI18nPrefix") or "").strip()
                    if actual_prefix != expected_prefix:
                        missing_value_prefixes.append(
                            {
                                "path": (
                                    f"pages.0.widgets.{widget_index}.inputs."
                                    f"{collection_name}.{value_index}.valueI18nPrefix"
                                ),
                                "field": path_value,
                                "expected": expected_prefix,
                                "actual": actual_prefix,
                            }
                        )
        postconditions.append(
            {
                "id": "applications.localization",
                "ok": (
                    not missing_localizations
                    and not invalid_localizations
                    and not locale_value_mismatches
                    and not invalid_locale_entries
                    and not missing_value_prefixes
                ),
                "expected": {
                    "locales": ["en", "ru"],
                    "fallbackLocale": "en",
                    "stableKeys": True,
                    "prototypeDictionariesOrInlineTranslations": True,
                    "canonicalGlossary": True,
                    "noPlaceholderEntries": True,
                    "canonicalValuePrefixes": True,
                },
                "actual": {
                    "missing": sorted(missing_localizations),
                    "invalid": invalid_localizations,
                    "localeValueMismatches": locale_value_mismatches,
                    "invalidLocaleEntries": invalid_locale_entries,
                    "missingValuePrefixes": missing_value_prefixes,
                },
            }
        )

        def widget_actions(widget: Mapping[str, Any]) -> list[Mapping[str, Any]]:
            raw = widget.get("actions")
            if isinstance(raw, Mapping):
                return [raw]
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, Mapping)]
            return []

        application_layouts = [
            layout
            for page in pages
            if isinstance((layout := page.get("layout")), Mapping)
            and layout.get("type") == "split"
            and layout.get("pattern") == "sidebar-content"
            and layout.get("sidebarWidth") == 380
            and layout.get("auxWidth") == 300
            and {
                str(area.get("role") or "")
                for area in layout.get("areas") or []
                if isinstance(area, Mapping)
            }
            == {"sidebar", "main", "aux"}
        ]
        postconditions.append(
            {
                "id": "applications.sidebar_layout",
                "ok": len(application_layouts) == 1,
                "expected": "one 380px/300px sidebar-content split with sidebar, main, and metadata areas",
                "actual": len(application_layouts),
            }
        )

        mcp_widgets = [
            widget
            for widget in widgets
            if isinstance(widget.get("dataSource"), Mapping)
            and str(widget.get("dataSource", {}).get("kind") or "") == "mcp"
        ]
        mcp_sources = [widget.get("dataSource") for widget in mcp_widgets]
        expected_sources = {
            "applications.show": (
                "response.result.application",
                {"application_id": "$state.selectedApplicationId"},
            ),
            "applications.list_releases": (
                "response.result.releases",
                {"application_id": "$state.selectedApplicationId"},
            ),
            "applications.list_operations": (
                "response.result.operations",
                {"application_id": "$state.selectedApplicationId"},
            ),
            "applications.list_development_reports": (
                "response.result.reports",
                {},
            ),
        }
        required_reads = set(requirements.get("mcp_read_tools") or [])
        exact_reads = {
            tool_id
            for tool_id, (result_path, arguments) in expected_sources.items()
            if any(
                source.get("dryRun") is True
                and str(source.get("toolId") or "") == tool_id
                and str(source.get("resultPath") or "") == result_path
                and source.get("arguments") == arguments
                for source in mcp_sources
            )
        }
        catalog_arguments = {
            json.dumps(source.get("arguments") or {}, sort_keys=True)
            for source in mcp_sources
            if source.get("dryRun") is True
            and str(source.get("toolId") or "") == "applications.list"
            and str(source.get("resultPath") or "") == "response.result.applications"
        }
        expected_catalog_arguments = {
            json.dumps(
                {
                    "available_only": True,
                    "installed_only": "$state.installedOnly",
                },
                sort_keys=True,
            ),
            json.dumps({"developed_only": True}, sort_keys=True),
        }
        if catalog_arguments == expected_catalog_arguments:
            exact_reads.add("applications.list")
        postconditions.append(
            {
                "id": "applications.mcp_reads",
                "ok": required_reads.issubset(exact_reads),
                "expected": sorted(required_reads),
                "actual": sorted(exact_reads),
            }
        )
        actions = [action for widget in widgets for action in widget_actions(widget)]
        plan_actions = [
            action
            for action in actions
            if str(action.get("type") or "") == "callMcp"
            and str(action.get("target") or "") == "applications.plan"
        ]
        apply_actions = [
            action
            for action in actions
            if str(action.get("type") or "") == "callMcp"
            and str(action.get("target") or "") == "applications.apply"
        ]
        required_plan_params = {
            "install": {
                "on": "click:install",
                "expected_revision": "$state.installationRevision",
                "release_digest": "$state.selectedReleaseDigest",
                "data_policy": "retain",
            },
            "update": {
                "on": "click:update",
                "expected_revision": "$state.installationRevision",
                "release_digest": "$state.selectedReleaseDigest",
            },
            "select_track": {
                "on": "click:select-track",
                "expected_revision": "$state.subscriptionRevision",
                "update_track": "$state.updateTrack",
                "update_policy": "$state.updatePolicy",
                "paused": False,
            },
            "remove": {
                "on": "click:remove",
                "expected_revision": "$state.installationRevision",
                "data_policy": "$state.removeDataPolicy",
            },
        }
        valid_plan_kinds: set[str] = set()
        for action in plan_actions:
            params = (
                action.get("params")
                if isinstance(action.get("params"), Mapping)
                else {}
            )
            kind = str(params.get("kind") or "")
            expected = required_plan_params.get(kind)
            if not expected:
                continue
            if (
                action.get("idempotencyKey") == "auto"
                and action.get("resultStateKey") == "reviewedPlan"
                and action.get("on") == expected["on"]
                and action.get("enabledIf") == "$state.selectedApplicationId"
                and params.get("application_id") == "$state.selectedApplicationId"
                and all(
                    params.get(key) == value
                    for key, value in expected.items()
                    if key != "on"
                )
            ):
                valid_plan_kinds.add(kind)
        required_plan_kinds = set(requirements.get("plan_kinds") or [])
        valid_apply_actions = [
            action
            for action in apply_actions
            if (
                action.get("idempotencyKey") == "auto"
                and action.get("resultStateKey") == "reviewedPlan"
                and "$state.reviewedPlan.operation.operation_id"
                in json.dumps(action.get("params") or {}, sort_keys=True)
                and "$state.reviewedPlan.operation.plan_digest"
                in json.dumps(action.get("params") or {}, sort_keys=True)
                and str(action.get("enabledIf") or "").strip()
            )
        ]
        valid_apply_kinds = {
            match.group(1)
            for action in valid_apply_actions
            if (
                match := re.search(
                    r"reviewedPlan\.operation\.kind\s*={2,3}\s*['\"]([^'\"]+)['\"]",
                    str(action.get("enabledIf") or ""),
                )
            )
        }
        apply_receipts = {"reviewedPlan"} if valid_apply_actions else set()
        postconditions.append(
            {
                "id": "applications.reviewed_plan_apply",
                "ok": (
                    required_plan_kinds == valid_plan_kinds
                    and len(valid_apply_actions)
                    == len(apply_actions)
                    == len(required_plan_kinds)
                    and valid_apply_kinds == required_plan_kinds
                ),
                "expected": {
                    "planKinds": sorted(required_plan_kinds),
                    "receiptStateKey": "reviewedPlan",
                    "separateApply": True,
                },
                "actual": {
                    "planActions": len(plan_actions),
                    "applyActions": len(apply_actions),
                    "validApplyActions": len(valid_apply_actions),
                    "validApplyKinds": sorted(valid_apply_kinds),
                    "validPlanKinds": sorted(valid_plan_kinds),
                    "boundReceiptStateKeys": sorted(apply_receipts),
                },
            }
        )
        lifecycle_widget = next(
            (
                widget
                for widget in widgets
                if any(action in plan_actions for action in widget_actions(widget))
            ),
            {},
        )
        lifecycle_inputs = (
            lifecycle_widget.get("inputs")
            if isinstance(lifecycle_widget, Mapping)
            and isinstance(lifecycle_widget.get("inputs"), Mapping)
            else {}
        )
        lifecycle_buttons = {
            str(button.get("id") or ""): str(button.get("label") or "")
            for button in lifecycle_inputs.get("buttons", [])
            if isinstance(button, Mapping) and str(button.get("id") or "")
        }
        expected_lifecycle_labels = {
            "install": "Install",
            "update": "Update",
            "select-track": "Save update settings",
            "remove": "Uninstall",
        }
        expected_ru_lifecycle_labels = {
            "install": "Установить",
            "update": "Обновить",
            "select-track": "Сохранить настройки обновлений",
            "remove": "Удалить",
            "preview": "Предпросмотр",
            "open-builder": "Открыть в Builder",
        }
        lifecycle_labels_ok = all(
            lifecycle_buttons.get(button_id) == label
            for button_id, label in expected_lifecycle_labels.items()
        )
        localized_lifecycle_buttons: dict[str, str] = {}
        locale_value_mismatches: list[dict[str, str]] = []
        lifecycle_widget_index = next(
            (
                index
                for index, widget in enumerate(widgets)
                if widget is lifecycle_widget
            ),
            -1,
        )
        for button_index, button in enumerate(lifecycle_inputs.get("buttons", [])):
            if not isinstance(button, Mapping):
                continue
            button_id = str(button.get("id") or "").strip()
            expected_ru = expected_ru_lifecycle_labels.get(button_id)
            if not expected_ru:
                continue
            descriptor = button.get("label_i18n")
            actual_ru = localized_text(descriptor, "ru")
            localized_lifecycle_buttons[button_id] = actual_ru
            if actual_ru != expected_ru:
                locale_value_mismatches.append(
                    {
                        "path": (
                            f"pages.0.widgets.{lifecycle_widget_index}.inputs."
                            f"buttons.{button_index}.label"
                        ),
                        "key": localization_key(descriptor),
                        "locale": "ru",
                        "expected": expected_ru,
                        "actual": actual_ru,
                    }
                )
        technical_lifecycle_labels = sorted(
            label
            for label in lifecycle_buttons.values()
            if re.search(r"\b(?:plan|apply)\b", label, flags=re.IGNORECASE)
        )
        apply_widgets = [
            widget
            for widget in widgets
            if any(action in apply_actions for action in widget_actions(widget))
        ]
        apply_separate = bool(apply_widgets) and all(
            widget is not lifecycle_widget for widget in apply_widgets
        )
        expected_confirmation_labels = {
            "install": "Install",
            "update": "Update",
            "select_track": "Save settings",
            "remove": "Uninstall",
        }
        valid_confirmation_kinds: set[str] = set()
        cancel_review = False
        for widget in apply_widgets:
            inputs = (
                widget.get("inputs")
                if isinstance(widget.get("inputs"), Mapping)
                else {}
            )
            buttons = {
                str(button.get("id") or ""): str(button.get("label") or "")
                for button in inputs.get("buttons", [])
                if isinstance(button, Mapping) and str(button.get("id") or "")
            }
            for action in widget_actions(widget):
                event = str(action.get("on") or "")
                button_id = event.split(":", 1)[1] if event.startswith("click:") else ""
                if (
                    str(action.get("type") or "") == "updateState"
                    and action.get("params") == {"reviewedPlan": {}}
                    and buttons.get(button_id) == "Cancel"
                ):
                    cancel_review = True
                if action not in apply_actions:
                    continue
                enabled = str(action.get("enabledIf") or "")
                kind_match = re.search(
                    r"reviewedPlan\.operation\.kind\s*={2,3}\s*['\"]([^'\"]+)['\"]",
                    enabled,
                )
                if not kind_match:
                    continue
                kind = kind_match.group(1)
                if buttons.get(button_id) == expected_confirmation_labels.get(kind):
                    valid_confirmation_kinds.add(kind)
        review_widget = next(
            (
                widget
                for widget in widgets
                if str(widget.get("id") or "") == "reviewed-plan"
            ),
            {},
        )
        review_inputs = (
            review_widget.get("inputs")
            if isinstance(review_widget, Mapping)
            and isinstance(review_widget.get("inputs"), Mapping)
            else {}
        )
        review_paths = {
            str(field.get("path") or "")
            for field in review_inputs.get("fields", [])
            if isinstance(field, Mapping)
        }
        review_visible = (
            str(review_widget.get("title") or "") == "Review"
            and "operation.plan.review_summary" in review_paths
            and "operation.plan.permissions" in review_paths
        )
        review_composition_ok = (
            lifecycle_labels_ok
            and not locale_value_mismatches
            and not technical_lifecycle_labels
            and apply_separate
            and valid_confirmation_kinds == set(expected_confirmation_labels)
            and cancel_review
            and review_visible
        )
        postconditions.append(
            {
                "id": "applications.review_composition",
                "ok": review_composition_ok,
                "expected": {
                    "lifecycleLabels": expected_lifecycle_labels,
                    "localizedLifecycleLabels": {
                        "ru": expected_ru_lifecycle_labels,
                    },
                    "technicalLabels": [],
                    "reviewTitle": "Review",
                    "reviewPermissionPath": "operation.plan.permissions",
                    "confirmationKinds": sorted(expected_confirmation_labels),
                    "confirmationSeparated": True,
                    "cancelReview": True,
                },
                "actual": {
                    "lifecycleLabels": lifecycle_buttons,
                    "localizedLifecycleLabels": {
                        "ru": localized_lifecycle_buttons,
                    },
                    "localeValueMismatches": locale_value_mismatches,
                    "technicalLabels": technical_lifecycle_labels,
                    "reviewTitle": str(review_widget.get("title") or ""),
                    "reviewPaths": sorted(review_paths),
                    "confirmationKinds": sorted(valid_confirmation_kinds),
                    "confirmationSeparated": apply_separate,
                    "cancelReview": cancel_review,
                },
            }
        )
        prototype_fixtures = next(
            (
                page.get("initialState", {}).get("prototypeFixtures")
                for page in pages
                if isinstance(page.get("initialState"), Mapping)
                and isinstance(
                    page.get("initialState", {}).get("prototypeFixtures"), Mapping
                )
            ),
            {},
        )
        required_fixture_profiles = {
            "applications",
            "developments",
            "application",
            "releases",
            "operations",
            "reports",
            "plan",
            "apply",
        }
        required_fixture_refs = {
            f"$state.prototypeFixtures.{profile}"
            for profile in required_fixture_profiles
        }
        source_fixture_refs = {
            str(source.get("prototypeFixture") or "")
            for source in mcp_sources
            if str(source.get("prototypeFixture") or "")
        }
        action_fixture_refs = {
            str(action.get("prototypeFixture") or "")
            for action in [*plan_actions, *apply_actions]
            if str(action.get("prototypeFixture") or "")
        }
        expected_source_fixture_refs = {
            "applications.show": "$state.prototypeFixtures.application",
            "applications.list_releases": "$state.prototypeFixtures.releases",
            "applications.list_operations": "$state.prototypeFixtures.operations",
            "applications.list_development_reports": "$state.prototypeFixtures.reports",
        }
        source_fixture_diagnostics: list[dict[str, Any]] = []
        for widget in mcp_widgets:
            source = widget.get("dataSource") or {}
            tool_id = str(source.get("toolId") or "")
            expected_ref = expected_source_fixture_refs.get(tool_id, "")
            if tool_id == "applications.list":
                expected_ref = (
                    "$state.prototypeFixtures.developments"
                    if source.get("arguments") == {"developed_only": True}
                    else "$state.prototypeFixtures.applications"
                )
            actual_ref = str(source.get("prototypeFixture") or "")
            if expected_ref and actual_ref != expected_ref:
                source_fixture_diagnostics.append(
                    {
                        "widgetId": str(widget.get("id") or ""),
                        "toolId": tool_id,
                        "expected": expected_ref,
                        "actual": actual_ref,
                    }
                )
        representative_state_ids: set[str] = set()

        def collect_representative_state_ids(value: Any) -> None:
            if isinstance(value, Mapping):
                state_id = str(value.get("prototype_state_id") or "").strip()
                if state_id:
                    representative_state_ids.add(state_id)
                for nested in value.values():
                    collect_representative_state_ids(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_representative_state_ids(nested)

        collect_representative_state_ids(prototype_fixtures)
        development_examples: dict[str, Mapping[str, Any]] = {}

        def collect_development_examples(value: Any) -> None:
            if isinstance(value, Mapping):
                development = value.get("local_development")
                application = value.get("application")
                if (
                    value.get("prototype_state_id") == "local-development"
                    and isinstance(development, Mapping)
                    and development.get("exists") is True
                    and isinstance(application, Mapping)
                ):
                    application_id = str(
                        application.get("application_id") or ""
                    ).strip()
                    if application_id:
                        development_examples[application_id] = value
                for nested in value.values():
                    collect_development_examples(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_development_examples(nested)

        collect_development_examples(prototype_fixtures)
        samples = (
            prototype_fixtures.get("samples")
            if isinstance(prototype_fixtures.get("samples"), Mapping)
            else {}
        )
        non_default_installed_fixtures: set[str] = set()

        def inspect_installed_fixture_defaults(value: Any) -> None:
            if isinstance(value, Mapping):
                if value.get("installed") is True:
                    application = value.get("application")
                    application_id = (
                        str(
                            application.get("application_id")
                            if isinstance(application, Mapping)
                            else ""
                        ).strip()
                        or "<unknown>"
                    )
                    subscription = (
                        value.get("subscription")
                        if isinstance(value.get("subscription"), Mapping)
                        else {}
                    )
                    expected_track = (
                        "prerelease"
                        if value.get("prerelease_following") is True
                        else "stable"
                    )
                    if (
                        value.get("auto_update_enabled") is not True
                        or subscription.get("update_policy") != "auto_compatible"
                        or subscription.get("update_track") != expected_track
                    ):
                        non_default_installed_fixtures.add(application_id)
                for nested in value.values():
                    inspect_installed_fixture_defaults(nested)
            elif isinstance(value, list):
                for nested in value:
                    inspect_installed_fixture_defaults(nested)

        inspect_installed_fixture_defaults(prototype_fixtures)
        sample_ref_prefix = "$state.prototypeFixtures.samples."
        invalid_selectable_fixture_refs: set[str] = set()

        def resolve_sample_ref(value: Any) -> Any:
            if not isinstance(value, str) or not value.startswith(sample_ref_prefix):
                return value
            sample_key = value[len(sample_ref_prefix) :]
            if sample_key in samples:
                return samples[sample_key]
            invalid_selectable_fixture_refs.add(value)
            return None

        def collect_application_ids(value: Any, target: set[str]) -> None:
            resolved = resolve_sample_ref(value)
            if isinstance(resolved, Mapping):
                application = resolved.get("application")
                if isinstance(application, Mapping):
                    application_id = str(
                        application.get("application_id") or ""
                    ).strip()
                    if application_id:
                        target.add(application_id)
            elif isinstance(resolved, list):
                for nested in resolved:
                    collect_application_ids(nested, target)

        selectable_application_ids: set[str] = set()
        for profile_name in ("applications", "developments"):
            profile = prototype_fixtures.get(profile_name)
            if not isinstance(profile, Mapping):
                continue
            collect_application_ids(profile.get("result"), selectable_application_ids)
            for case in (
                profile.get("cases") if isinstance(profile.get("cases"), list) else []
            ):
                if isinstance(case, Mapping):
                    collect_application_ids(
                        case.get("result"), selectable_application_ids
                    )

        application_fixture = prototype_fixtures.get("application")
        covered_application_ids: set[str] = set()
        mismatched_application_cases: list[dict[str, str]] = []
        for case in (
            application_fixture.get("cases")
            if isinstance(application_fixture, Mapping)
            and isinstance(application_fixture.get("cases"), list)
            else []
        ):
            if not isinstance(case, Mapping) or not isinstance(
                case.get("when"), Mapping
            ):
                continue
            expected_id = str(case.get("when", {}).get("application_id") or "").strip()
            if not expected_id:
                continue
            resolved_result = resolve_sample_ref(case.get("result"))
            application = (
                resolved_result.get("application")
                if isinstance(resolved_result, Mapping)
                and isinstance(resolved_result.get("application"), Mapping)
                else {}
            )
            actual_id = str(application.get("application_id") or "").strip()
            if actual_id == expected_id:
                covered_application_ids.add(expected_id)
            elif expected_id in selectable_application_ids:
                mismatched_application_cases.append(
                    {
                        "expectedApplicationId": expected_id,
                        "actualApplicationId": actual_id,
                    }
                )
        uncovered_application_ids = selectable_application_ids - covered_application_ids
        required_development_states = {
            ("prototype", "working", "not_started"),
            ("automation", "working", "not_started"),
            ("automation", "completed", "published"),
        }
        actual_development_states = {
            (
                str((value.get("local_development") or {}).get("phase") or ""),
                str((value.get("local_development") or {}).get("status") or ""),
                str(
                    (value.get("local_development") or {}).get("publication_status")
                    or ""
                ),
            )
            for value in development_examples.values()
        }
        valid_development_examples = {
            application_id
            for application_id, value in development_examples.items()
            if str(
                ((value.get("application") or {}).get("display") or {}).get("summary")
                or ""
            ).strip()
            and all(
                str(
                    ((value.get("local_development") or {}).get("builder") or {}).get(
                        key
                    )
                    or ""
                ).strip()
                for key in (
                    "selected_object_type",
                    "selected_object_id",
                    "source_webspace_id",
                    "preview_webspace_id",
                )
            )
        }
        required_representative_states = {
            "marketplace-uninstalled",
            "installed-current",
            "installed-update",
            "prerelease-following",
            "local-development",
            "protected-system",
            "operation-recovery",
        }
        actual_fixture_profiles = {
            str(key) for key in prototype_fixtures if str(key).strip()
        }
        executable_fixture_profiles = {
            str(key)
            for key, value in prototype_fixtures.items()
            if key in required_fixture_profiles
            and isinstance(value, Mapping)
            and ("result" in value or isinstance(value.get("cases"), list))
        }
        plan_fixture = prototype_fixtures.get("plan")
        plan_fixture_cases = (
            plan_fixture.get("cases")
            if isinstance(plan_fixture, Mapping)
            and isinstance(plan_fixture.get("cases"), list)
            else []
        )
        valid_plan_fixture_kinds = {
            str((case.get("when") or {}).get("kind") or "")
            for case in plan_fixture_cases
            if isinstance(case, Mapping)
            and isinstance(case.get("when"), Mapping)
            and isinstance(case.get("result"), Mapping)
            and isinstance(case.get("result", {}).get("operation"), Mapping)
            and case.get("result", {}).get("operation", {}).get("kind")
            == case.get("when", {}).get("kind")
            and str(
                case.get("result", {}).get("operation", {}).get("operation_id") or ""
            ).strip()
            and str(
                case.get("result", {}).get("operation", {}).get("plan_digest") or ""
            ).strip()
            and str(
                case.get("result", {}).get("operation", {}).get("application_id") or ""
            ).strip()
            and str(
                (case.get("result", {}).get("operation", {}).get("plan", {}) or {}).get(
                    "review_summary"
                )
                or ""
            ).strip()
            and isinstance(
                (case.get("result", {}).get("operation", {}).get("plan", {}) or {}).get(
                    "permissions"
                ),
                list,
            )
            and bool(
                (case.get("result", {}).get("operation", {}).get("plan", {}) or {}).get(
                    "permissions"
                )
            )
        }
        invalid_plan_fixture_kinds = sorted(
            required_plan_kinds - valid_plan_fixture_kinds
        )
        plan_fixture_case_issues: list[dict[str, Any]] = []
        for kind in invalid_plan_fixture_kinds:
            matching_case = next(
                (
                    case
                    for case in plan_fixture_cases
                    if isinstance(case, Mapping)
                    and isinstance(case.get("when"), Mapping)
                    and str(case.get("when", {}).get("kind") or "") == kind
                ),
                None,
            )
            if not isinstance(matching_case, Mapping):
                plan_fixture_case_issues.append({"kind": kind, "missing": ["case"]})
                continue
            operation = (
                matching_case.get("result", {}).get("operation", {})
                if isinstance(matching_case.get("result"), Mapping)
                and isinstance(
                    matching_case.get("result", {}).get("operation"), Mapping
                )
                else {}
            )
            plan = (
                operation.get("plan")
                if isinstance(operation.get("plan"), Mapping)
                else {}
            )
            missing = [
                name
                for name, present in (
                    ("matching operation.kind", operation.get("kind") == kind),
                    (
                        "operation_id",
                        bool(str(operation.get("operation_id") or "").strip()),
                    ),
                    (
                        "plan_digest",
                        bool(str(operation.get("plan_digest") or "").strip()),
                    ),
                    (
                        "application_id",
                        bool(str(operation.get("application_id") or "").strip()),
                    ),
                    (
                        "plan.review_summary",
                        bool(str(plan.get("review_summary") or "").strip()),
                    ),
                    (
                        "non-empty plan.permissions",
                        isinstance(plan.get("permissions"), list)
                        and bool(plan.get("permissions")),
                    ),
                )
                if not present
            ]
            plan_fixture_case_issues.append({"kind": kind, "missing": missing})
        representative_plan_permissions = any(
            bool(
                (case.get("result", {}).get("operation", {}).get("plan", {}) or {}).get(
                    "permissions"
                )
            )
            for case in plan_fixture_cases
            if isinstance(case, Mapping)
        )
        postconditions.append(
            {
                "id": "applications.prototype_fixtures",
                "ok": (
                    required_fixture_profiles.issubset(executable_fixture_profiles)
                    and (
                        required_fixture_refs
                        - {
                            "$state.prototypeFixtures.plan",
                            "$state.prototypeFixtures.apply",
                        }
                    ).issubset(source_fixture_refs)
                    and "$state.prototypeFixtures.plan" in action_fixture_refs
                    and "$state.prototypeFixtures.apply" in action_fixture_refs
                    and required_representative_states.issubset(
                        representative_state_ids
                    )
                    and len(valid_development_examples) >= 3
                    and required_development_states.issubset(actual_development_states)
                    and not source_fixture_diagnostics
                    and not invalid_selectable_fixture_refs
                    and not uncovered_application_ids
                    and not mismatched_application_cases
                    and not non_default_installed_fixtures
                    and required_plan_kinds.issubset(valid_plan_fixture_kinds)
                    and representative_plan_permissions
                ),
                "expected": {
                    "profiles": sorted(required_fixture_profiles),
                    "refs": sorted(required_fixture_refs),
                    "stateIds": sorted(required_representative_states),
                    "developmentExampleCount": 3,
                    "developmentStates": sorted(required_development_states),
                    "selectableDetailCoverage": "all",
                    "installedDefaults": {
                        "prereleaseFollowing": False,
                        "automaticUpdates": True,
                        "updateTrack": "stable",
                        "updatePolicy": "auto_compatible",
                    },
                    "planReview": {
                        "applicationId": True,
                        "summary": True,
                        "permissions": "non-empty list for every plan kind",
                    },
                    "scope": "development Webspace only",
                },
                "actual": {
                    "profiles": sorted(actual_fixture_profiles),
                    "executableProfiles": sorted(executable_fixture_profiles),
                    "sourceRefs": sorted(source_fixture_refs),
                    "actionRefs": sorted(action_fixture_refs),
                    "stateIds": sorted(representative_state_ids),
                    "developmentExamples": sorted(valid_development_examples),
                    "developmentStates": sorted(actual_development_states),
                    "invalidSourceFixtures": source_fixture_diagnostics,
                    "invalidSelectableFixtureRefs": sorted(
                        invalid_selectable_fixture_refs
                    ),
                    "selectableApplicationIds": sorted(selectable_application_ids),
                    "coveredApplicationIds": sorted(covered_application_ids),
                    "uncoveredApplicationIds": sorted(uncovered_application_ids),
                    "mismatchedApplicationCases": mismatched_application_cases,
                    "nonDefaultInstalledApplications": sorted(
                        non_default_installed_fixtures
                    ),
                    "planKinds": sorted(valid_plan_fixture_kinds),
                    "invalidPlanCases": plan_fixture_case_issues,
                    "representativePlanPermissions": representative_plan_permissions,
                    "missingRequirements": [
                        "make every required plan fixture case valid; fix exactly these cases: "
                        + json.dumps(plan_fixture_case_issues, ensure_ascii=False)
                    ]
                    if plan_fixture_case_issues
                    else [],
                },
            }
        )
        tab_ids = set(requirements.get("tabs") or [])
        tab_controls = []
        for widget in widgets:
            inputs = (
                widget.get("inputs")
                if isinstance(widget.get("inputs"), Mapping)
                else {}
            )
            buttons = (
                inputs.get("buttons") if isinstance(inputs.get("buttons"), list) else []
            )
            ids = {
                str(button.get("id") or "").strip()
                for button in buttons
                if isinstance(button, Mapping)
            }
            if (
                str(widget.get("type") or "") == "input.commandBar"
                and inputs.get("variant") == "segmented"
                and inputs.get("selectedStateKey") == "activeTab"
                and tab_ids.issubset(ids)
                and any(
                    action.get("on") == "click"
                    and action.get("type") == "updateState"
                    and action.get("params") == {"activeTab": "$event.id"}
                    for action in widget_actions(widget)
                )
            ):
                tab_controls.append(widget)
        postconditions.append(
            {
                "id": "applications.tabs",
                "ok": len(tab_controls) == 1,
                "expected": sorted(tab_ids),
                "actual": len(tab_controls),
            }
        )
        catalog_inputs = {
            "variant": "list",
            "itemIdKey": "application.application_id",
            "search": True,
            "titleKey": "application.display.title",
        }
        catalog_row_inputs = {
            "applications": {
                "subtitleKey": "application.publisher.display_name",
                "previewKey": "application.display.summary",
                "meta": [
                    {
                        "key": "installed",
                        "label": "Installation",
                        "kind": "boolean",
                        "trueLabel": "Installed",
                        "falseLabel": "Not installed",
                    },
                    {
                        "key": "update_available",
                        "label": "Update",
                        "kind": "boolean",
                        "trueLabel": "Update available",
                        "falseLabel": "Current",
                    },
                ],
            },
            "developments": {
                "subtitleKey": "application.display.summary",
                "previewKey": "application.publisher.display_name",
                "meta": [
                    {
                        "key": "local_development.phase",
                        "label": "Phase",
                        "kind": "badge",
                    },
                    {
                        "key": "local_development.status",
                        "label": "Status",
                        "kind": "badge",
                    },
                    {
                        "key": "local_development.publication_status",
                        "label": "Publication",
                        "kind": "badge",
                    },
                ],
            },
        }
        selectable_catalogs: dict[str, Mapping[str, Any]] = {}
        catalog_diagnostics: dict[str, list[dict[str, Any]]] = {}
        catalog_sections = {
            "applications": {
                "available_only": True,
                "installed_only": "$state.installedOnly",
            },
            "developments": {"developed_only": True},
        }
        for section, arguments in catalog_sections.items():
            candidates = [
                widget
                for widget in widgets
                if str(widget.get("type") or "") == "ui.list"
                and isinstance(widget.get("dataSource"), Mapping)
                and widget.get("dataSource", {}).get("toolId") == "applications.list"
            ]
            catalog_diagnostics[section] = []
            for widget in candidates:
                inputs = widget.get("inputs") or {}
                mismatches: list[str] = []
                if widget.get("dataSource", {}).get("arguments") != arguments:
                    mismatches.append("dataSource.arguments")
                if str(widget.get("visibleIf") or "") != (
                    f"$state.catalogSection == '{section}'"
                ):
                    mismatches.append("visibleIf")
                mismatches.extend(
                    f"inputs.{key}"
                    for key, value in catalog_inputs.items()
                    if inputs.get(key) != value
                )
                mismatches.extend(
                    f"inputs.{key}"
                    for key, value in catalog_row_inputs[section].items()
                    if not _contains_shape(inputs.get(key), value)
                )
                if not any(
                    action.get("on") == "select"
                    and action.get("type") == "updateState"
                    and action.get("params")
                    == {
                        "selectedApplicationId": "$event.application.application_id",
                        "selectedReleaseDigest": "",
                        "reviewedPlan": {},
                    }
                    for action in widget_actions(widget)
                ):
                    mismatches.append("actions.select.selectedApplicationId")
                catalog_diagnostics[section].append(
                    {
                        "widgetId": str(widget.get("id") or ""),
                        "mismatches": mismatches,
                    }
                )
            matches = [
                widget
                for widget in candidates
                if not next(
                    item["mismatches"]
                    for item in catalog_diagnostics[section]
                    if item["widgetId"] == str(widget.get("id") or "")
                )
            ]
            if len(matches) == 1:
                selectable_catalogs[section] = matches[0]
        catalog_section_controls = [
            widget
            for widget in widgets
            if widget.get("type") == "input.commandBar"
            and (widget.get("inputs") or {}).get("variant") == "segmented"
            and (widget.get("inputs") or {}).get("size") == "small"
            and (widget.get("inputs") or {}).get("stretch") is True
            and (widget.get("inputs") or {}).get("selectedStateKey") == "catalogSection"
            and {
                str(button.get("id") or "")
                for button in (widget.get("inputs") or {}).get("buttons") or []
                if isinstance(button, Mapping)
            }
            == set(catalog_sections)
            and any(
                action.get("on") == "click"
                and action.get("type") == "updateState"
                and _contains_shape(
                    action.get("params"), {"catalogSection": "$event.id"}
                )
                for action in widget_actions(widget)
            )
        ]
        postconditions.append(
            {
                "id": "applications.catalog_sections",
                "ok": len(catalog_section_controls) == 1
                and set(selectable_catalogs) == set(catalog_sections),
                "expected": sorted(catalog_sections),
                "actual": {
                    "controls": len(catalog_section_controls),
                    "selectableSections": sorted(selectable_catalogs),
                    "sectionCandidates": catalog_diagnostics,
                },
            }
        )
        serialized = json.dumps(webui, ensure_ascii=False, sort_keys=True)
        postconditions.append(
            {
                "id": "applications.master_selection",
                "ok": len(selectable_catalogs) == 2,
                "expected": "Applications and My developments masters write selectedApplicationId",
                "actual": sorted(selectable_catalogs),
            }
        )
        expected_detail_bindings = {
            "installationRevision": {"path": "installation.revision", "default": 0},
            "subscriptionRevision": {"path": "subscription.revision", "default": 0},
            "applicationInstalled": "installed",
            "applicationRemovable": "application.protection.active_installation_removable",
            "updateAvailable": "update_available",
            "prereleaseFollowing": {"path": "prerelease_following", "default": False},
            "automaticUpdates": {"path": "auto_update_enabled", "default": True},
            "updateTrack": {"path": "subscription.update_track", "default": "stable"},
            "updatePolicy": {
                "path": "subscription.update_policy",
                "default": "auto_compatible",
            },
            "effectiveReleaseDigest": {
                "path": "effective_release.release_digest",
                "default": "",
            },
            "localDevelopmentAvailable": {
                "path": "local_development.exists",
                "default": False,
            },
            "developmentObjectType": {
                "path": "local_development.builder.selected_object_type",
                "default": "",
            },
            "developmentObjectId": {
                "path": "local_development.builder.selected_object_id",
                "default": "",
            },
            "developmentSourceWebspaceId": {
                "path": "local_development.builder.source_webspace_id",
                "default": "",
            },
            "developmentPreviewWebspaceId": {
                "path": "local_development.builder.preview_webspace_id",
                "default": "",
            },
        }
        application_detail_sources = [
            widget
            for widget in widgets
            if widget.get("type") == "item.details"
            and (widget.get("dataSource") or {}).get("toolId") == "applications.show"
        ]
        application_details = [
            widget
            for widget in application_detail_sources
            if "$state.selectedApplicationId" in str(widget.get("visibleIf") or "")
            and _contains_shape(
                (widget.get("inputs") or {}).get("stateBindings"),
                expected_detail_bindings,
            )
            and (widget.get("inputs") or {}).get("stateOnly") is True
        ]
        area_roles = {
            str(area.get("id") or ""): str(area.get("role") or "")
            for layout in application_layouts
            for area in layout.get("areas") or []
            if isinstance(area, Mapping)
        }
        expected_header_fields = [
            {"label": "Summary", "path": "application.display.summary"},
            {"label": "Publisher", "path": "application.publisher.display_name"},
            {"label": "Installed", "path": "installed_release.version"},
            {"label": "Marketplace", "path": "marketplace_release.version"},
        ]
        application_headers = [
            widget
            for widget in application_detail_sources
            if str(widget.get("title") or "") == "{application.display.title}"
            and (widget.get("inputs") or {}).get("presentation") == "header"
            and _contains_shape(
                (widget.get("inputs") or {}).get("fields"), expected_header_fields
            )
            and area_roles.get(str(widget.get("area") or "")) == "main"
            and "$state.selectedApplicationId" in str(widget.get("visibleIf") or "")
        ]
        expected_detail_sections = {
            "Details": [
                {"label": "Identifier", "path": "application.application_id"},
                {"label": "Publisher", "path": "application.publisher.display_name"},
                {"label": "Lifecycle", "path": "application.lifecycle"},
            ],
            "Installation": [
                {"label": "Installed version", "path": "installed_release.version"},
                {"label": "Status", "path": "installation.status"},
                {
                    "label": "Updated",
                    "path": "installation.updated_at",
                    "format": "datetime",
                },
                {"label": "Update track", "path": "subscription.update_track"},
                {"label": "Update policy", "path": "subscription.update_policy"},
            ],
            "Marketplace": [
                {"label": "Stable version", "path": "marketplace_release.version"},
                {"label": "Pre-release version", "path": "prerelease_release.version"},
                {
                    "label": "Last released",
                    "path": "marketplace_release.published_at",
                    "format": "datetime",
                },
                {"label": "Visibility", "path": "application.visibility"},
            ],
            "Categories": [
                {"label": "Categories", "path": "application.display.categories"},
            ],
            "My development": [
                {"label": "Phase", "path": "local_development.phase"},
                {"label": "Status", "path": "local_development.status"},
                {
                    "label": "Publication",
                    "path": "local_development.publication_status",
                },
                {"label": "Revision", "path": "local_development.revision"},
                {
                    "label": "Updated",
                    "path": "local_development.updated_at",
                    "format": "datetime",
                },
            ],
        }
        expected_detail_roles = {
            "Details": "main",
            "Installation": "aux",
            "Marketplace": "aux",
            "Categories": "aux",
            "My development": "aux",
        }
        detail_sections: dict[str, Mapping[str, Any]] = {}
        expected_detail_empty_titles = {"Installation", "Categories"}
        for title, fields in expected_detail_sections.items():
            matches = [
                widget
                for widget in application_detail_sources
                if str(widget.get("title") or "") == title
                and (widget.get("inputs") or {}).get("stateOnly") is not True
                and (widget.get("inputs") or {}).get("presentation") == "section"
                and _contains_shape((widget.get("inputs") or {}).get("fields"), fields)
                and area_roles.get(str(widget.get("area") or ""))
                == expected_detail_roles[title]
                and (
                    title not in expected_detail_empty_titles
                    or bool(
                        str((widget.get("inputs") or {}).get("emptyText") or "").strip()
                    )
                )
                and "$state.activeTab == 'details'"
                in str(widget.get("visibleIf") or "")
                and "$state.selectedApplicationId" in str(widget.get("visibleIf") or "")
                and (
                    title != "My development"
                    or "$state.localDevelopmentAvailable == true"
                    in str(widget.get("visibleIf") or "")
                )
            ]
            if len(matches) == 1:
                detail_sections[title] = matches[0]
        release_selectors = [
            widget
            for widget in widgets
            if widget.get("type") == "ui.list"
            and (widget.get("dataSource") or {}).get("toolId")
            == "applications.list_releases"
            and (widget.get("inputs") or {}).get("itemIdKey") == "release_digest"
            and (widget.get("inputs") or {}).get("titleKey") == "version"
            and any(
                action.get("on") == "select"
                and action.get("type") == "updateState"
                and action.get("params")
                == {
                    "selectedReleaseDigest": "$event.release_digest",
                    "reviewedPlan": {},
                }
                for action in widget_actions(widget)
            )
        ]
        expected_operation_inputs = {
            "itemIdKey": "operation_id",
            "titleKey": "summary",
            "subtitleKey": "kind",
            "previewKey": "kind",
            "meta": [
                {"key": "status", "label": "Status", "kind": "badge"},
            ],
            "emptyText": "No operations yet.",
        }
        operation_lists = [
            widget
            for widget in widgets
            if widget.get("type") == "ui.list"
            and (widget.get("dataSource") or {}).get("toolId")
            == "applications.list_operations"
            and all(
                _contains_shape((widget.get("inputs") or {}).get(key), value)
                for key, value in expected_operation_inputs.items()
            )
        ]
        expected_report_inputs = {
            "itemIdKey": "report_id",
            "titleKey": "title",
            "subtitleKey": "summary",
            "previewKey": "summary",
            "meta": [
                {"key": "status", "label": "Status", "kind": "badge"},
            ],
            "emptyText": "No reports yet.",
        }
        reports = [
            widget
            for widget in widgets
            if widget.get("type") == "ui.list"
            and (widget.get("dataSource") or {}).get("toolId")
            == "applications.list_development_reports"
            and all(
                _contains_shape((widget.get("inputs") or {}).get(key), value)
                for key, value in expected_report_inputs.items()
            )
            and any(
                item.get("key") == "application_id"
                and item.get("stateKey") == "selectedApplicationId"
                for item in (widget.get("inputs") or {}).get("filters") or []
                if isinstance(item, Mapping)
            )
        ]
        empty_state_tools = {
            str((widget.get("dataSource") or {}).get("toolId") or "")
            for widget in widgets
            if str((widget.get("inputs") or {}).get("emptyText") or "").strip()
        }
        required_empty_state_tools = {
            "applications.list_releases",
            "applications.list_operations",
            "applications.list_development_reports",
        }
        lifecycle_buttons = {
            str(button.get("id") or ""): button
            for widget in widgets
            if widget.get("type") == "ui.actions"
            for button in (widget.get("inputs") or {}).get("buttons") or []
            if isinstance(button, Mapping)
        }
        remove_visibility = str(
            (lifecycle_buttons.get("remove") or {}).get("visibleIf") or ""
        )
        install_visibility = str(
            (lifecycle_buttons.get("install") or {}).get("visibleIf") or ""
        )
        update_visibility = str(
            (lifecycle_buttons.get("update") or {}).get("visibleIf") or ""
        )
        builder_visibility = str(
            (lifecycle_buttons.get("open-builder") or {}).get("visibleIf") or ""
        )
        preview_visibility = str(
            (lifecycle_buttons.get("preview") or {}).get("visibleIf") or ""
        )
        review_action_visibility = " ".join(
            str(widget.get("visibleIf") or "") for widget in apply_widgets
        )
        expected_lifecycle_icons = {
            "install": "download-outline",
            "update": "refresh-outline",
            "select-track": "options-outline",
            "remove": "trash-outline",
            "preview": "open-outline",
            "open-builder": "construct-outline",
        }
        lifecycle_widgets = [
            widget
            for widget in widgets
            if widget.get("type") == "ui.actions"
            and set(expected_lifecycle_icons).issubset(
                {
                    str(button.get("id") or "")
                    for button in (widget.get("inputs") or {}).get("buttons") or []
                    if isinstance(button, Mapping)
                }
            )
            and (widget.get("inputs") or {}).get("variant") == "toolbar"
            and all(
                (lifecycle_buttons.get(button_id) or {}).get("icon") == icon
                for button_id, icon in expected_lifecycle_icons.items()
            )
            and "$state.selectedApplicationId" in str(widget.get("visibleIf") or "")
        ]
        lifecycle_before_details = bool(
            len(lifecycle_widgets) == 1
            and len(application_headers) == 1
            and len(detail_sections) == len(expected_detail_sections)
            and widgets.index(lifecycle_widgets[0])
            == widgets.index(application_headers[0]) + 1
            and all(
                widgets.index(lifecycle_widgets[0]) < widgets.index(section)
                for section in [
                    *tab_controls,
                    *release_selectors,
                    *operation_lists,
                    *reports,
                    *detail_sections.values(),
                ]
            )
        )
        page_schemas = list(_page_schemas(webui))
        page_initial_states = [
            page.get("initialState")
            if isinstance(page.get("initialState"), Mapping)
            else {}
            for _, page in page_schemas
        ]
        shadow_state_pages = [path for path, page in page_schemas if "state" in page]
        exact_initial_state = any(
            state.get("installationRevision") == 0
            and state.get("subscriptionRevision") == 0
            and state.get("selectedReleaseDigest") == ""
            and state.get("effectiveReleaseDigest") == ""
            and state.get("catalogSection") == "applications"
            and state.get("installedOnly") is False
            and state.get("activeTab") == "details"
            and state.get("prereleaseFollowing") is False
            and state.get("automaticUpdates") is True
            and state.get("updateTrack") == "stable"
            and state.get("updatePolicy") == "auto_compatible"
            and state.get("removeDataPolicy") == "retain"
            and state.get("reviewedPlan") == {}
            for state in page_initial_states
        )
        cas_defaults = exact_initial_state and not shadow_state_pages
        selector_contracts = {
            "removeDataPolicy": {"retain", "delete", "snapshot_then_delete"},
        }
        valid_selectors: set[str] = set()
        for widget in widgets:
            if widget.get("type") != "input.selector":
                continue
            inputs = (
                widget.get("inputs")
                if isinstance(widget.get("inputs"), Mapping)
                else {}
            )
            option_values = {
                str(option.get("value", option.get("id")) or "")
                for option in inputs.get("options") or []
                if isinstance(option, Mapping)
            }
            for state_key, expected_values in selector_contracts.items():
                if option_values != expected_values:
                    continue
                if inputs.get("label") != "Data on uninstall":
                    continue
                if area_roles.get(str(widget.get("area") or "")) != "aux":
                    continue
                if any(
                    action.get("on") == "change"
                    and action.get("type") == "updateState"
                    and action.get("params")
                    == {
                        state_key: "$event.value",
                        "reviewedPlan": {},
                    }
                    for action in widget_actions(widget)
                ):
                    valid_selectors.add(state_key)
        expected_toggles = {
            "prereleaseFollowing": {
                "state_value": "$state.prereleaseFollowing",
                "state_key": "updateTrack",
                "then": "prerelease",
                "else": "stable",
            },
            "automaticUpdates": {
                "state_value": "$state.automaticUpdates",
                "state_key": "updatePolicy",
                "then": "auto_compatible",
                "else": "notify",
            },
        }
        valid_toggles: set[str] = set()
        for widget in widgets:
            if widget.get("type") != "input.toggle":
                continue
            source = widget.get("dataSource") or {}
            for state_key, contract in expected_toggles.items():
                expected_params = {
                    state_key: "$event.checked",
                    contract["state_key"]: {
                        "kind": "expression",
                        "op": "if",
                        "condition": "$event.checked",
                        "then": contract["then"],
                        "else": contract["else"],
                    },
                    "reviewedPlan": {},
                }
                if (
                    source.get("kind") == "static"
                    and source.get("value") == contract["state_value"]
                    and "$state.applicationInstalled == true"
                    in str(widget.get("visibleIf") or "")
                    and any(
                        action.get("on") == "change"
                        and action.get("type") == "updateState"
                        and action.get("params") == expected_params
                        for action in widget_actions(widget)
                    )
                ):
                    valid_toggles.add(state_key)
        installed_filters = [
            widget
            for widget in widgets
            if widget.get("type") == "input.toggle"
            and (widget.get("dataSource") or {}).get("kind") == "static"
            and (widget.get("dataSource") or {}).get("value") == "$state.installedOnly"
            and (widget.get("inputs") or {}).get("label") == "Installed only"
            and "$state.catalogSection == 'applications'"
            in str(widget.get("visibleIf") or "")
            and any(
                action.get("on") == "change"
                and action.get("type") == "updateState"
                and _contains_shape(
                    action.get("params"), {"installedOnly": "$event.checked"}
                )
                for action in widget_actions(widget)
            )
        ]
        builder_actions = [
            action
            for action in actions
            if action.get("type") == "openWorkspace"
            and action.get("on") == "click:open-builder"
            and action.get("params")
            == {
                "ensureBuilderWorkbench": True,
                "newWindow": True,
                "selectedObjectType": "$state.developmentObjectType",
                "selectedObjectId": "$state.developmentObjectId",
                "sourceWebspaceId": "$state.developmentSourceWebspaceId",
            }
        ]
        preview_actions = [
            action
            for action in actions
            if action.get("type") == "openWorkspace"
            and action.get("on") == "click:preview"
            and action.get("params")
            == {
                "newWindow": True,
                "workspaceId": "$state.developmentPreviewWebspaceId",
                "expectedScenarioId": "$state.developmentObjectId",
            }
        ]
        review_surfaces = [
            widget
            for widget in widgets
            if widget.get("type") == "item.details"
            and (widget.get("dataSource") or {}).get("kind") == "static"
            and (widget.get("dataSource") or {}).get("value") == "$state.reviewedPlan"
            and "$state.reviewedPlan.operation.operation_id"
            in str(widget.get("visibleIf") or "")
            and "$state.reviewedPlan.status" in str(widget.get("visibleIf") or "")
        ]
        detail_state_binding_ok = bool(
            len(application_details) == 1
            and release_selectors
            and operation_lists
            and reports
            and len(installed_filters) == 1
        )
        lifecycle_controls_ok = bool(
            detail_state_binding_ok
            and lifecycle_before_details
            and "$state.applicationRemovable" in remove_visibility
            and "$state.selectedReleaseDigest" in install_visibility
            and "$state.effectiveReleaseDigest" in install_visibility
            and "$state.updateAvailable" in update_visibility
            and len(lifecycle_widgets) == 1
            and cas_defaults
            and set(selector_contracts) == valid_selectors
            and set(expected_toggles) == valid_toggles
            and len(builder_actions) == 1
            and len(preview_actions) == 1
            and "$state.localDevelopmentAvailable" in builder_visibility
            and "$state.developmentObjectId" in builder_visibility
            and "$state.localDevelopmentAvailable" in preview_visibility
            and "$state.developmentPreviewWebspaceId" in preview_visibility
            and "$state.developmentObjectId" in preview_visibility
        )
        lifecycle_missing_requirements = [
            detail
            for ok, detail in (
                (
                    detail_state_binding_ok,
                    "preserve the qualified selected detail, release, operation, report, and Installed-only bindings",
                ),
                (
                    lifecycle_before_details,
                    "place the lifecycle toolbar immediately after the Application header and before tabs or detail content",
                ),
                (
                    exact_initial_state,
                    "merge every exact recipe.initial_state lifecycle and CAS default into page initialState",
                ),
                (
                    not shadow_state_pages,
                    "remove pageSchema.state; runtime defaults have one authority at pageSchema.initialState",
                ),
                (
                    len(lifecycle_widgets) == 1,
                    "create one toolbar with all six recipe.canonical_commands buttons and their exact icons",
                ),
                (
                    set(selector_contracts) == valid_selectors,
                    "add the exact removeDataPolicy selector and state update",
                ),
                (
                    set(expected_toggles) == valid_toggles,
                    "add the exact prereleaseFollowing and automaticUpdates toggles and state updates",
                ),
                (
                    len(builder_actions) == 1 and len(preview_actions) == 1,
                    "add exact Preview and Open in Builder actions for the existing local development target",
                ),
                (
                    "$state.applicationRemovable" in remove_visibility,
                    "guard Uninstall with applicationRemovable",
                ),
                (
                    "$state.selectedReleaseDigest" in install_visibility
                    and "$state.effectiveReleaseDigest" in install_visibility
                    and "$state.updateAvailable" in update_visibility,
                    "guard Install and Update with effective release and update state",
                ),
            )
            if not ok
        ]
        postconditions.append(
            {
                "id": "applications.detail_state_binding",
                "ok": detail_state_binding_ok,
                "expected": "one selected applications.show state resolver plus operable release, operation, report, and Installed-only controls",
                "actual": {
                    "details": len(application_details),
                    "detailSources": len(application_detail_sources),
                    "detailVisibleOnSelection": sum(
                        "$state.selectedApplicationId"
                        in str(widget.get("visibleIf") or "")
                        for widget in application_detail_sources
                    ),
                    "detailLifecycleBindings": sum(
                        _contains_shape(
                            (widget.get("inputs") or {}).get("stateBindings"),
                            expected_detail_bindings,
                        )
                        for widget in application_detail_sources
                    ),
                    "detailStateOnly": sum(
                        (widget.get("inputs") or {}).get("stateOnly") is True
                        for widget in application_detail_sources
                    ),
                    "releaseSelectors": len(release_selectors),
                    "operationLists": len(operation_lists),
                    "filteredReports": len(reports),
                    "installedFilters": len(installed_filters),
                },
            }
        )
        postconditions.append(
            {
                "id": "applications.concise_operable_detail",
                "ok": bool(
                    len(application_headers) == 1
                    and len(detail_sections) == len(expected_detail_sections)
                    and required_empty_state_tools.issubset(empty_state_tools)
                ),
                "expected": "one visible Application header, main Details, and unframed Installation, Marketplace, Categories, and My development metadata sections",
                "actual": {
                    "headers": len(application_headers),
                    "detailSections": sorted(detail_sections),
                    "lifecycleBeforeDetails": lifecycle_before_details,
                    "emptyStateTools": sorted(
                        empty_state_tools & required_empty_state_tools
                    ),
                },
            }
        )
        postconditions.append(
            {
                "id": "applications.lifecycle_controls",
                "ok": lifecycle_controls_ok,
                "expected": {
                    "lifecycleBeforeDetails": True,
                    "casDefaults": True,
                    "noShadowState": True,
                    "sixCommandToolbar": True,
                    "reviewedSettings": True,
                    "existingDevelopmentActions": True,
                    "protectedRemoveGuard": True,
                    "releaseGuards": True,
                },
                "actual": {
                    "lifecycleBeforeDetails": lifecycle_before_details,
                    "casDefaults": cas_defaults,
                    "shadowStatePages": shadow_state_pages,
                    "selectedLifecycleSurface": len(lifecycle_widgets),
                    "lifecycleSelectors": sorted(valid_selectors),
                    "lifecycleToggles": sorted(valid_toggles),
                    "builderActions": len(builder_actions),
                    "previewActions": len(preview_actions),
                    "removeProtected": "$state.applicationRemovable"
                    in remove_visibility,
                    "installReleaseGuarded": (
                        "$state.selectedReleaseDigest" in install_visibility
                        and "$state.effectiveReleaseDigest" in install_visibility
                    ),
                    "updateAvailableGuarded": "$state.updateAvailable"
                    in update_visibility,
                    "missingRequirements": lifecycle_missing_requirements,
                },
            }
        )
        postconditions.append(
            {
                "id": "applications.detail_lifecycle_binding",
                "ok": bool(
                    lifecycle_controls_ok
                    and len(apply_widgets) == 1
                    and "$state.reviewedPlan.operation.operation_id"
                    in review_action_visibility
                    and "$state.reviewedPlan.operation.plan_digest"
                    in review_action_visibility
                    and len(review_surfaces) == 1
                ),
                "expected": {
                    "detailStateBinding": True,
                    "lifecycleBeforeDetails": True,
                    "casDefaults": True,
                    "releaseSelector": True,
                    "operationList": True,
                    "filteredReports": True,
                    "protectedRemoveGuard": True,
                    "releaseGuards": True,
                    "lifecycleToolbar": True,
                    "reviewedSettings": True,
                    "existingDevelopmentActions": True,
                    "reviewReceiptGuard": True,
                },
                "actual": {
                    "details": len(application_details),
                    "lifecycleBeforeDetails": lifecycle_before_details,
                    "detailSources": len(application_detail_sources),
                    "detailVisibleOnSelection": sum(
                        "$state.selectedApplicationId"
                        in str(widget.get("visibleIf") or "")
                        for widget in application_detail_sources
                    ),
                    "detailLifecycleBindings": sum(
                        _contains_shape(
                            (widget.get("inputs") or {}).get("stateBindings"),
                            expected_detail_bindings,
                        )
                        for widget in application_detail_sources
                    ),
                    "detailStateOnly": sum(
                        (widget.get("inputs") or {}).get("stateOnly") is True
                        for widget in application_detail_sources
                    ),
                    "releaseSelectors": len(release_selectors),
                    "operationLists": len(operation_lists),
                    "filteredReports": len(reports),
                    "removeProtected": "$state.applicationRemovable"
                    in remove_visibility,
                    "installReleaseGuarded": (
                        "$state.selectedReleaseDigest" in install_visibility
                        and "$state.effectiveReleaseDigest" in install_visibility
                    ),
                    "updateAvailableGuarded": "$state.updateAvailable"
                    in update_visibility,
                    "selectedLifecycleSurface": len(lifecycle_widgets),
                    "casDefaults": cas_defaults,
                    "lifecycleSelectors": sorted(valid_selectors),
                    "lifecycleToggles": sorted(valid_toggles),
                    "installedFilters": len(installed_filters),
                    "builderActions": len(builder_actions),
                    "previewActions": len(preview_actions),
                    "builderExistingDevelopmentGuarded": (
                        "$state.localDevelopmentAvailable" in builder_visibility
                        and "$state.developmentObjectId" in builder_visibility
                    ),
                    "lifecycleToolbar": len(lifecycle_widgets) == 1,
                    "applyHiddenWithoutReceipt": (
                        "$state.reviewedPlan.operation.operation_id"
                        in review_action_visibility
                        and "$state.reviewedPlan.operation.plan_digest"
                        in review_action_visibility
                    ),
                    "reviewSurfaces": len(review_surfaces),
                },
            }
        )
        postconditions.append(
            {
                "id": "applications.no_prototype_store",
                "ok": "prototype_items" not in serialized,
                "expected": "no generic prototype datasource",
                "actual": "prototype_items" in serialized,
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
            inputs = (
                board.get("inputs") if isinstance(board.get("inputs"), Mapping) else {}
            )
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
            data_source = (
                board.get("dataSource")
                if isinstance(board.get("dataSource"), Mapping)
                else {}
            )
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
                        and str(_read_path(row, lane_key) or "")
                        == str(lane.get("id") or "")
                    )
                    for lane in lanes
                    if isinstance(lane, Mapping)
                }
                postconditions.append(
                    {
                        "id": "kanban.items_per_lane",
                        "ok": bool(counts)
                        and all(count == expected_items for count in counts.values()),
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
                actions = (
                    board.get("actions")
                    if isinstance(board.get("actions"), list)
                    else []
                )
                move_action = any(
                    isinstance(action, Mapping)
                    and str(action.get("on") or "") == "move"
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
                data_source = (
                    board.get("dataSource")
                    if isinstance(board.get("dataSource"), Mapping)
                    else {}
                )
                postconditions.append(
                    {
                        "id": "kanban.resource_query",
                        "ok": str(data_source.get("kind") or "") == "resourceQuery",
                        "expected": "dataSource.kind=resourceQuery",
                        "actual": data_source.get("kind"),
                    }
                )
                query = (
                    data_source.get("query")
                    if isinstance(data_source.get("query"), Mapping)
                    else {}
                )
                serialized_query = json.dumps(query, ensure_ascii=False, sort_keys=True)
                query_state_refs = set(
                    re.findall(r"\$state\.([A-Za-z0-9_.-]+)", serialized_query)
                )
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
                        and str(action.get("params", {}).get("operation_id") or "")
                        == "create"
                        and "$event.values"
                        in json.dumps(
                            action.get("params", {}).get("payload"), sort_keys=True
                        )
                    ]
                    title_key = str(inputs.get("titleKey") or "title").strip()
                    required_create_fields = {title_key, lane_key}
                    complete_create_forms = [
                        (page_path, widget, action)
                        for page_path, widget, action in create_forms
                        if required_create_fields.issubset(
                            {
                                str(field.get("id") or "").strip()
                                for field in (widget.get("inputs") or {}).get(
                                    "fields", []
                                )
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
                    board_actions = (
                        board.get("actions")
                        if isinstance(board.get("actions"), list)
                        else []
                    )
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
                        and str(action.get("params", {}).get("operation_id") or "")
                        == "update"
                        and str(action.get("params", {}).get("record_id") or "").strip()
                        and "$event.values"
                        in json.dumps(
                            action.get("params", {}).get("payload"), sort_keys=True
                        )
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
                    board_actions = (
                        board.get("actions")
                        if isinstance(board.get("actions"), list)
                        else []
                    )
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
                            str(_read_path(action.get("params", {}), ref) or "")
                            == "$event.id"
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
    iteration = (
        requirements.get("prototype_iteration")
        if isinstance(requirements.get("prototype_iteration"), Mapping)
        else None
    )
    required_recipe_postconditions = _required_recipe_postconditions(
        str(requirements.get("recipe_id") or ""), iteration
    )
    if required_recipe_postconditions is not None:
        for item in postconditions:
            identifier = str(item.get("id") or "")
            if identifier.startswith("applications."):
                item["required"] = identifier in required_recipe_postconditions
    failures = [
        item
        for item in postconditions
        if not item.get("ok") and item.get("required") is not False
    ]
    outstanding = [
        str(item.get("id") or "")
        for item in postconditions
        if not item.get("ok") and item.get("required") is False
    ]
    gaps = list(qualification.get("capability_gaps") or [])
    return {
        "schema": "adaos.ui.request_evaluation.v1",
        "qualification": qualification,
        "capability_validation": capability_validation,
        "postconditions": postconditions,
        "outstanding_postconditions": outstanding,
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
