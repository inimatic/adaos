from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services.ui_resource_queries import widget_resource_queries
from adaos.services.webui_layout import layout_v2_findings

from adaos.services.builder_intent import capture_intent, compile_prototype_brief, partition_intent_scope
from adaos.services.builder_domain_packs import (
    domain_pack_receipts,
    resolve_domain_packs,
    ui_domain_adapter,
)


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
def _generic_ui_capability_catalog() -> dict[str, Any]:
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


def ui_capability_catalog(
    *, domain_packs: Sequence[str] | None = None
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        return copy.deepcopy(adapter.ui_capability_catalog())
    result = copy.deepcopy(_generic_ui_capability_catalog())
    recipes = [
        copy.deepcopy(dict(recipe))
        for pack in resolve_domain_packs(domain_packs)
        for recipe in pack.get("ui_recipes") or []
        if isinstance(recipe, Mapping)
    ]
    if recipes:
        result["recipes"] = [*result.get("recipes", []), *recipes]
        result["catalog_digest"] = _digest(
            {key: value for key, value in result.items() if key != "catalog_digest"}
        )
    return result


def _domain_metadata(domain_packs: Sequence[str] | None) -> dict[str, Any]:
    packs = resolve_domain_packs(domain_packs)
    policies = [
        copy.deepcopy(dict(pack["prototype_policy"]))
        for pack in packs
        if isinstance(pack.get("prototype_policy"), Mapping)
    ]
    guidance = [
        copy.deepcopy(dict(pack["repair_guidance"]))
        for pack in packs
        if isinstance(pack.get("repair_guidance"), Mapping)
    ]
    return {
        "input_attribution": {
            "profile": "domain_pack" if packs else "generic",
            "domain_packs": domain_pack_receipts(domain_packs),
        },
        "domain_policy": policies[0] if len(policies) == 1 else {"policies": policies},
        "repair_guidance": guidance[0]
        if len(guidance) == 1
        else {"guidance": guidance},
    }


def search_ui_capabilities(
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 8,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        return copy.deepcopy(
            adapter.search_ui_capabilities(query, kinds=kinds, limit=limit)
        )
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


def get_ui_capability(
    item_id: str, *, domain_packs: Sequence[str] | None = None
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        return copy.deepcopy(adapter.get_ui_capability(item_id))
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


def _contains_any_term(text: str, values: Iterable[str]) -> bool:
    """Match complete intent terms instead of substrings inside other words."""

    normalized = _normalized_text(text)
    return any(
        re.search(
            rf"(?<!\w){re.escape(_normalized_text(value))}(?!\w)",
            normalized,
            flags=re.IGNORECASE,
        )
        for value in values
        if _normalized_text(value)
    )


def _prototype_resource_signal(operation: Mapping[str, Any]) -> bool | None:
    """Return a conservative persistence signal for a deterministic Brief row.

    ``True`` is reserved for explicit end-user data mutation. ``False`` means
    the row is clearly UI authoring/local state. ``None`` keeps ambiguous
    wording for model interpretation instead of inventing a CRUD obligation.
    """

    kind = str(operation.get("kind") or "").strip()
    if kind not in {"create", "update", "assign", "transition", "delete", "archive"}:
        return False
    statement = _normalized_text(operation.get("statement"))
    source_clause = _normalized_text(operation.get("source_clause"))
    # Declarative navigation and local view state are interactions, not an
    # application resource model.
    if re.search(
        r"\b(?:bounded\s+)?local\s+(?:prototype\s+)?state\b|"
        r"\b(?:ui|interface|layout|widget|navigation)\s+state\b",
        statement,
        flags=re.IGNORECASE,
    ):
        return False
    if statement in {"update", "change", "edit", "add", "create"}:
        return False
    authoring_text = f"{statement} {source_clause}".strip()
    if re.search(
        r"\b(?:prototype|revision|webui|layout|region|widget|viewport|"
        r"presentation|toolbar|navigation|ui|interface|screen|responsive|"
        r"compact|modal|dialog|sheet|tabs?|cards?|grid|table)\b|"
        r"\b(?:ui|visual|navigation|layout)\.[a-z0-9_.-]+\b|"
        r"\$event(?:\.[a-z0-9_.-]+)?\b",
        authoring_text,
        flags=re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:field|label|key|metadata|wording|icon|badge|action|command|"
        r"filter(?:ing)?|details?|section|catalog|readme|about|rating)\b",
        authoring_text,
        flags=re.IGNORECASE,
    ):
        return False

    # These are deliberately narrow positive signals. Broader domain meaning
    # belongs to the model-produced Prototype Brief, not to lexical expansion.
    if re.search(
        r"\b(?:crud|resourceoperation|persistent\s+(?:data|records?)|"
        r"persist(?:ed|ing)?\s+(?:data|records?))\b",
        authoring_text,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:allow|let)\s+(?:an?\s+|the\s+)?[a-z][\w-]*\s+to\s+"
        r"(?:create|add|edit|update|assign|move|close|complete|archive|delete)\b|"
        r"\b(?:users?|members?|operators?|coordinators?|managers?|admins?|staff)\s+"
        r"(?:(?:can|may|must|need\s+to|should\s+be\s+able\s+to)\s+.{0,120}?)?"
        r"(?:create|add|edit|update|assign|move|close|complete|archive|delete)\b",
        authoring_text,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:create|add|edit|update|assign|move|close|complete|archive|delete)\w*"
        r"(?:\s+[a-z][\w-]*){0,8}\s+"
        r"(?:records?|entries|tasks?|appointments?|inspections?|tickets?)\b",
        authoring_text,
        flags=re.IGNORECASE,
    ):
        return True
    return None


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


def qualify_ui_request(
    request: str, *, domain_packs: Sequence[str] | None = None
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        result = copy.deepcopy(adapter.qualify_ui_request(request))
        result.update(_domain_metadata(domain_packs))
        return result
    intent = capture_intent(request)
    prototype_brief = compile_prototype_brief(intent)
    brief_operation_kinds = list(dict.fromkeys(
        str(item.get("kind") or "")
        for item in prototype_brief.get("operations") or []
        if isinstance(item, Mapping) and str(item.get("kind") or "")
    ))
    brief_information_kinds = sorted(
        {
            str(item.get("kind") or "").strip()
            for item in prototype_brief.get("information_requirements") or []
            if isinstance(item, Mapping)
            and str(item.get("interaction") or "") == "capture"
            and str(item.get("kind") or "").strip()
        }
    )
    brief_collection_requirements = [
        {
            key: copy.deepcopy(item.get(key))
            for key in ("id", "kind", "interaction", "statement")
        }
        for item in prototype_brief.get("collection_requirements") or []
        if isinstance(item, Mapping)
    ]
    brief_operations = set(brief_operation_kinds)
    scope_text = str(request or "")
    has_synthetic_prototype_data = bool(
        re.search(
            r"\bsynthetic\s+(?:records|data|fixtures)\b",
            scope_text,
            flags=re.IGNORECASE,
        )
    )
    has_bounded_local_state = bool(
        re.search(
            r"\bbounded\s+local(?:[\s-]+prototype)?[\s-]+state\b|"
            r"\bbounded\s+local[\s-]+state\s+prototype\b",
            scope_text,
            flags=re.IGNORECASE,
        )
    )
    explicit_local_prototype_scope = bool(
        has_bounded_local_state
        and (
            has_synthetic_prototype_data
            or re.search(r"\bprototype\b", scope_text, flags=re.IGNORECASE)
        )
    )
    resource_persistence_excluded = bool(
        re.search(
            r"\b(?:do\s+not|don't|without|no)\s+(?:create\s+)?(?:domain\s+"
            r"persistence|prototype\s+resources?)\b",
            scope_text,
            flags=re.IGNORECASE,
        )
    )
    resource_signals = [
        _prototype_resource_signal(item)
        for item in prototype_brief.get("operations") or []
        if isinstance(item, Mapping)
    ]
    resource_mutations = not (
        explicit_local_prototype_scope or resource_persistence_excluded
    ) and any(signal is True for signal in resource_signals)
    resource_scope_needs_interpretation = not (
        explicit_local_prototype_scope or resource_persistence_excluded
    ) and any(signal is None for signal in resource_signals)
    prototype_resource_required = bool(
        resource_mutations
        and brief_operations & {"inspect", "list", "search", "filter"}
    )
    included_request, _ = partition_intent_scope(request)
    text = _normalized_text(included_request)
    literal_text_change = _literal_text_change(included_request)
    prototype_iteration = _prototype_iteration(included_request)
    board = _contains_any_term(text, _BOARD_TERMS) or bool(
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
                "layout_id": "layout.board",
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
    if prototype_iteration:
        requirements["prototype_iteration"] = prototype_iteration
    requirements["prototype_brief_ref"] = prototype_brief["brief_id"]
    requirements["brief_operation_kinds"] = brief_operation_kinds
    requirements["brief_information_kinds"] = brief_information_kinds
    requirements["brief_collection_requirements"] = brief_collection_requirements
    requirements["resource_mutations"] = resource_mutations
    requirements["resource_scope_needs_interpretation"] = (
        resource_scope_needs_interpretation
    )
    requirements["prototype_resource"] = prototype_resource_required
    gaps: list[dict[str, Any]] = []
    return {
        "schema": QUALIFICATION_SCHEMA,
        "request_digest": _digest({"request": request}),
        "surface_kind": (
            "board"
            if board
            else "ui"
            if literal_text_change
            else "interactive_collection"
            if brief_operation_kinds
            else "unspecified"
        ),
        "concepts": concepts,
        "requirements": requirements,
        "capability_gaps": gaps,
        "ready": not gaps,
        "intent": intent,
        "prototype_brief": prototype_brief,
        "input_attribution": {"profile": "generic", "domain_packs": []},
    }


def selected_ui_capabilities(
    request: str,
    *,
    limit: int = 8,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        result = copy.deepcopy(adapter.selected_ui_capabilities(request, limit=limit))
        result.update(_domain_metadata(domain_packs))
        return result
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
    brief_operation_kinds = {
        str(value or "").strip()
        for value in requirements.get("brief_operation_kinds") or []
        if str(value or "").strip()
    }
    for key in ("recipe_id", "component_type", "layout_id"):
        value = str(requirements.get(key) or "").strip()
        if value and value not in selected_ids:
            selected_ids.append(value)
    if (
        requirements.get("component_type") == "collection.board"
        and (
            requirements.get("resource_query")
            or requirements.get("operation_kinds")
            or requirements.get("prototype_resource")
        )
        and "recipe.resource_board_workbench" not in selected_ids
    ):
        selected_ids.append("recipe.resource_board_workbench")
    if (
        requirements.get("component_type") != "collection.board"
        and requirements.get("prototype_resource")
        and "recipe.resource_collection_workbench" not in selected_ids
    ):
        selected_ids.append("recipe.resource_collection_workbench")
    if (
        requirements.get("prototype_resource")
        and
        brief_operation_kinds & {"inspect", "list"}
        and brief_operation_kinds
        & {"create", "update", "assign", "transition", "delete", "archive"}
        and "recipe.master_detail" not in selected_ids
    ):
        selected_ids.append("recipe.master_detail")
    if (
        requirements.get("prototype_resource")
        and "create" in brief_operation_kinds
        and "recipe.data_entry" not in selected_ids
    ):
        selected_ids.append("recipe.data_entry")
    if (
        requirements.get("brief_information_kinds")
        and "recipe.data_entry" not in selected_ids
    ):
        selected_ids.append("recipe.data_entry")
    explicit_layouts = (
        (
            "layout.collection-detail",
            r"\b(?:collection[- ]detail|collection\s+and\s+detail|list[- ]detail|table[- ]detail)\b",
        ),
        ("layout.master-detail", r"\bmaster[- ]detail\b"),
        ("layout.dashboard", r"\bdashboard\b"),
        ("layout.workbench", r"\bworkbench\b"),
        ("layout.settings", r"\bsettings\s+(?:layout|surface|section)\b"),
        ("layout.task-flow", r"\btask[- ]flow\b"),
    )
    for layout_id, pattern in explicit_layouts:
        if re.search(pattern, str(request or ""), flags=re.IGNORECASE) and layout_id not in selected_ids:
            selected_ids.append(layout_id)
    if re.search(
        r"\b(?:chat|conversation|conversation\s+history|message\s+composer)\w*\b",
        str(request or ""),
        flags=re.IGNORECASE,
    ) and "ui.chat" not in selected_ids:
        selected_ids.append("ui.chat")
    if re.search(
        r"\b(?:metric|status|health)[- ]?(?:tile|informer|summary)\w*\b|"
        r"\b(?:tile|informer)\w*\s+(?:for\s+)?(?:metric|status|health)\w*\b|"
        r"\b(?:информер|плитк)\w*\s+(?:метрик|статус|состояни|здоров)\w*\b",
        str(request or ""),
        flags=re.IGNORECASE,
    ) and "visual.metricTile" not in selected_ids:
        selected_ids.append("visual.metricTile")
    if brief_operation_kinds & {"search", "filter"}:
        for component_id in ("input.text", "input.selector"):
            if component_id not in selected_ids:
                selected_ids.append(component_id)
    request_text = str(request or "")
    if brief_operation_kinds & {"inspect", "list"}:
        for component_id in ("ui.list", "item.details"):
            if component_id not in selected_ids:
                selected_ids.append(component_id)
    if brief_operation_kinds & {"search", "filter"}:
        if "ui.queryToolbar" not in selected_ids:
            selected_ids.append("ui.queryToolbar")
    interaction_text = _normalized_text(request_text)
    if re.search(
        r"\b(?:typed\s+forms?|data[- ]entry\s+forms?|forms?|"
        r"\u0444\u043e\u0440\u043c\w*|\u0430\u043d\u043a\u0435\u0442\w*)\b",
        interaction_text,
        flags=re.IGNORECASE,
    ) and "ui.form" not in selected_ids:
        selected_ids.append("ui.form")
    if re.search(
        r"\b(?:navigation|destinations?|sections?|tabs?|"
        r"\u043d\u0430\u0432\u0438\u0433\u0430\u0446|\u0440\u0430\u0437\u0434\u0435\u043b|\u0432\u043a\u043b\u0430\u0434)\w*\b",
        interaction_text,
        flags=re.IGNORECASE,
    ) and "navigation.tabs" not in selected_ids:
        selected_ids.append("navigation.tabs")
    if re.search(
        r"\b(?:actions?|commands?|open(?:ing)?|launch|"
        r"\u0434\u0435\u0439\u0441\u0442\u0432|\u043a\u043e\u043c\u0430\u043d\u0434|\u043e\u0442\u043a\u0440)\w*\b",
        interaction_text,
        flags=re.IGNORECASE,
    ) and "ui.actions" not in selected_ids:
        selected_ids.append("ui.actions")
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
        "repair_guidance": {
            "general": [],
            "by_postcondition": {
                "resource.prototype_source": [
                    "Use one prototype.<resource_name> resourceType per independently materialized resource. Every projection and mutation must target one declared type, and the prototype_resources sidecar must name the same complete set."
                ],
                "resource.persistence_operations": [
                    "Use widget.actions entries with type=resourceOperation, target equal to the queried prototype resourceType, and params.operation_id=create/update/delete as required; localCreate, localUpdate, and updateState are not persistence operations."
                ],
                "resource.prototype_records": [
                    "Return bounded direct records for every declared Prototype resource in prototype_resources. A legacy single-resource result may use prototype_records. AdaOS derives schemas and provider bindings."
                ],
                "ui.information_capture": [
                    "Represent every expected semantic information kind as an editable ui.form field. For attachment capture use a fileUpload field rather than static explanatory text."
                ],
            },
        },
        "input_attribution": {"profile": "generic", "domain_packs": []},
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
    schemas = _page_schemas(webui)
    desktop_state = next((page.get("initialState") for path, page in schemas if path == "ui.application.desktop.pageSchema"), {})
    shared_initial_state = desktop_state if isinstance(desktop_state, Mapping) else {}
    for schema_path, page in schemas:
        findings.extend(layout_v2_findings(page, schema_path=schema_path))
        widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
        query_controls_by_state: dict[str, Mapping[str, Any]] = {}
        for candidate in widgets:
            if not isinstance(candidate, Mapping) or candidate.get("type") != "ui.queryToolbar":
                continue
            candidate_inputs = (
                candidate.get("inputs")
                if isinstance(candidate.get("inputs"), Mapping)
                else {}
            )
            controls = (
                candidate_inputs.get("controls")
                if isinstance(candidate_inputs.get("controls"), list)
                else []
            )
            for control in controls:
                if not isinstance(control, Mapping):
                    continue
                state_key = str(control.get("stateKey") or "").strip()
                if state_key:
                    query_controls_by_state[state_key] = control
        initial_state = (
            page.get("initialState")
            if isinstance(page.get("initialState"), Mapping)
            else {}
        )
        controlled_state_values: dict[str, set[str]] = {}
        static_event_state_values: dict[str, set[str]] = {}
        for candidate in widgets:
            if not isinstance(candidate, Mapping):
                continue
            candidate_inputs = (
                candidate.get("inputs")
                if isinstance(candidate.get("inputs"), Mapping)
                else {}
            )
            selected_state_key = str(
                candidate_inputs.get("selectedStateKey") or ""
            ).strip()
            if selected_state_key.startswith("$state."):
                selected_state_key = selected_state_key[7:]
            choices = candidate_inputs.get("buttons")
            if not isinstance(choices, list):
                choices = candidate_inputs.get("options")
            if selected_state_key and isinstance(choices, list):
                values = {
                    str(item.get("value") or item.get("id") or "").strip()
                    for item in choices
                    if isinstance(item, Mapping)
                    and str(item.get("value") or item.get("id") or "").strip()
                }
                if values:
                    controlled_state_values.setdefault(selected_state_key, set()).update(
                        values
                    )
            candidate_actions = (
                candidate.get("actions")
                if isinstance(candidate.get("actions"), list)
                else []
            )
            candidate_data_source = (
                candidate.get("dataSource")
                if isinstance(candidate.get("dataSource"), Mapping)
                else {}
            )
            candidate_rows = (
                candidate_data_source.get("value")
                if str(candidate_data_source.get("kind") or "") == "static"
                and isinstance(candidate_data_source.get("value"), list)
                else []
            )
            for action in candidate_actions:
                if (
                    not isinstance(action, Mapping)
                    or str(action.get("type") or "") != "updateState"
                    or not isinstance(action.get("params"), Mapping)
                ):
                    continue
                for state_key, value in action["params"].items():
                    event_field = (
                        str(value)[7:]
                        if isinstance(value, str) and str(value).startswith("$event.")
                        else ""
                    )
                    if event_field and candidate_rows:
                        values = {
                            str(_read_path(row, event_field))
                            for row in candidate_rows
                            if isinstance(row, Mapping)
                            and _read_path(row, event_field) not in {None, ""}
                        }
                        if values:
                            static_event_state_values.setdefault(
                                str(state_key), set()
                            ).update(values)
                    if isinstance(value, (str, int, float, bool)) and not str(
                        value
                    ).startswith("$"):
                        controlled_state_values.setdefault(str(state_key), set()).add(
                            str(value)
                        )
        layout = page.get("layout") if isinstance(page.get("layout"), Mapping) else {}
        variants = layout.get("variants") if isinstance(layout.get("variants"), list) else []
        simple_variant_guard = re.compile(
            r"^\s*\$state\.([A-Za-z_][A-Za-z0-9_.-]*)\s*===\s*(['\"])(.*?)\2\s*$"
        )
        for variant_index, variant in enumerate(variants):
            if not isinstance(variant, Mapping):
                continue
            match = simple_variant_guard.match(str(variant.get("when") or ""))
            if not match:
                continue
            state_key, expected = match.group(1), match.group(3)
            reachable_values = controlled_state_values.get(state_key)
            if reachable_values and expected not in reachable_values:
                findings.append(
                    {
                        "code": "ui.layout.variant_state_unreachable",
                        "severity": "error",
                        "path": f"{schema_path}.layout.variants[{variant_index}].when",
                        "message": (
                            f"Layout variant expects {state_key!r}={expected!r}, but the "
                            "declared controls/actions can only write: "
                            + ", ".join(sorted(reachable_values))
                        ),
                        "state_key": state_key,
                        "expected": expected,
                        "reachable_values": sorted(reachable_values),
                    }
                )
        visible_state_guard = re.compile(
            r"\$state\.([A-Za-z_][A-Za-z0-9_.-]*)\s*===\s*(['\"])(.*?)\2"
        )
        for widget_index, candidate in enumerate(widgets):
            if not isinstance(candidate, Mapping):
                continue
            for match in visible_state_guard.finditer(
                str(candidate.get("visibleIf") or "")
            ):
                state_key, expected = match.group(1), match.group(3)
                reachable_values = controlled_state_values.get(state_key)
                if reachable_values and expected not in reachable_values:
                    findings.append(
                        {
                            "code": "ui.component.visible_state_unreachable",
                            "severity": "error",
                            "path": f"{schema_path}.widgets[{widget_index}].visibleIf",
                            "message": (
                                f"Widget visibility expects {state_key!r}={expected!r}, but "
                                "the declared controls/actions can only write: "
                                + ", ".join(sorted(reachable_values))
                            ),
                            "state_key": state_key,
                            "expected": expected,
                            "reachable_values": sorted(reachable_values),
                        }
                    )
        if schema_path.startswith("ui.application.modals."):
            initial_state = {**shared_initial_state, **initial_state}
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
            inputs = (
                widget.get("inputs")
                if isinstance(widget.get("inputs"), Mapping)
                else {}
            )
            misplaced_structural = sorted(
                key
                for key in ("actions", "area", "dataSource", "enabledIf", "visibleIf")
                if key in inputs
            )
            if misplaced_structural:
                findings.append(
                    {
                        "code": "ui.component.structural_input_misplaced",
                        "severity": "error",
                        "path": f"{widget_path}.inputs",
                        "message": (
                            "Widget structural properties belong on the widget, not under inputs: "
                            + ", ".join(misplaced_structural)
                        ),
                    }
                )
            if widget_type == "desktop.widgets" and not (
                str(data_source.get("kind") or "") == "y"
                and str(data_source.get("transform") or "") == "desktop.widgets"
            ):
                findings.append(
                    {
                        "code": "ui.desktop_widgets.source_invalid",
                        "severity": "error",
                        "path": f"{widget_path}.dataSource",
                        "message": (
                            "desktop.widgets is the system projection for installed desktop widgets; "
                            "it is not a generic content container. Use ui.list, item.details, "
                            "ui.actions, or another selected universal component for authored content."
                        ),
                    }
                )
            if widget_type in {"ui.list", "item.details"} and not data_source:
                findings.append(
                    {
                        "code": "ui.component.source_missing",
                        "severity": "error",
                        "path": f"{widget_path}.dataSource",
                        "message": (
                            f"{widget_type} requires a resolvable widget.dataSource. "
                            "For synthetic prototype content use "
                            "dataSource={kind:'static',value:...}."
                        ),
                    }
                )
            if widget_type == "ui.list" and any(
                key in inputs for key in ("rows", "items")
            ):
                findings.append(
                    {
                        "code": "ui.list.inline_source_unsupported",
                        "severity": "error",
                        "path": f"{widget_path}.inputs",
                        "message": (
                            "ui.list does not read inputs.rows or inputs.items. Put static "
                            "records in widget.dataSource={kind:'static',value:[...]} instead."
                        ),
                    }
                )
            if widget_type == "item.details" and "title" in inputs:
                findings.append(
                    {
                        "code": "ui.details.title_input_misplaced",
                        "severity": "error",
                        "path": f"{widget_path}.inputs.title",
                        "message": (
                            "item.details renders its heading from the top-level widget.title. "
                            "Move inputs.title to widget.title; $state references are supported there."
                        ),
                    }
                )
            dynamic_title = str(widget.get("title") or "").strip()
            if (
                widget_type == "item.details"
                and widget.get("title_i18n") is not None
                and (
                    "$state." in dynamic_title
                    or bool(re.search(r"\{[^{}]+\}", dynamic_title))
                )
            ):
                findings.append(
                    {
                        "code": "ui.details.dynamic_title_i18n_conflict",
                        "severity": "error",
                        "path": f"{widget_path}.title_i18n",
                        "message": (
                            "item.details title_i18n is localized before the selected record "
                            "or page-state title is resolved, so it overrides a dynamic widget.title. "
                            "Remove title_i18n when widget.title contains $state references or "
                            "record templates."
                        ),
                    }
                )
            actions = (
                widget.get("actions") if isinstance(widget.get("actions"), list) else []
            )
            state_writes_by_event: dict[str, dict[str, list[int]]] = {}
            for action_index, action in enumerate(actions):
                if (
                    not isinstance(action, Mapping)
                    or str(action.get("type") or "") != "updateState"
                ):
                    continue
                event = str(action.get("on") or "").strip()
                params = (
                    action.get("params")
                    if isinstance(action.get("params"), Mapping)
                    else {}
                )
                for state_key in params:
                    state_writes_by_event.setdefault(event, {}).setdefault(
                        str(state_key), []
                    ).append(action_index)
            for event, state_writes in state_writes_by_event.items():
                for state_key, action_indexes in state_writes.items():
                    if event and len(action_indexes) > 1:
                        findings.append(
                            {
                                "code": "ui.action.conflicting_state_writes",
                                "severity": "error",
                                "path": f"{widget_path}.actions",
                                "message": (
                                    f"Multiple updateState actions for event {event!r} write "
                                    f"state key {state_key!r}. Use one event action and derive "
                                    "the value from $event, or use distinct button/event ids."
                                ),
                                "action_indexes": action_indexes,
                            }
                        )
            if widget_type == "ui.list":
                action_events = {
                    str(action.get("on") or "").strip()
                    for action in actions
                    if isinstance(action, Mapping)
                }
                buttons = (
                    inputs.get("buttons")
                    if isinstance(inputs.get("buttons"), list)
                    else []
                )
                for button_index, button in enumerate(buttons):
                    button_id = (
                        str(button.get("id") or "").strip()
                        if isinstance(button, Mapping)
                        else ""
                    )
                    if button_id and f"click:{button_id}" not in action_events:
                        findings.append(
                            {
                                "code": "ui.list.button_action_missing",
                                "severity": "error",
                                "path": f"{widget_path}.inputs.buttons[{button_index}]",
                                "message": (
                                    f"ui.list button {button_id!r} requires a top-level "
                                    f"widget.actions entry with on='click:{button_id}'."
                                ),
                            }
                        )
                rows = (
                    data_source.get("value")
                    if str(data_source.get("kind") or "") == "static"
                    and isinstance(data_source.get("value"), list)
                    else []
                )
                filters = (
                    inputs.get("filters")
                    if isinstance(inputs.get("filters"), list)
                    else []
                )
                for filter_index, filter_spec in enumerate(filters):
                    if (
                        not isinstance(filter_spec, Mapping)
                        or str(filter_spec.get("operator") or "equals") != "equals"
                    ):
                        continue
                    state_key = str(filter_spec.get("stateKey") or "").strip()
                    field_key = str(filter_spec.get("key") or "").strip()
                    control = query_controls_by_state.get(state_key)
                    options = (
                        control.get("options")
                        if isinstance(control, Mapping)
                        and isinstance(control.get("options"), list)
                        else []
                    )
                    actual_values = {
                        str(_read_path(row, field_key)).strip().lower()
                        for row in rows
                        if isinstance(row, Mapping)
                        and _read_path(row, field_key) is not None
                    }
                    option_values = {
                        str(option.get("value")).strip().lower()
                        for option in options
                        if isinstance(option, Mapping)
                        and option.get("value") is not None
                        and str(option.get("value")).strip().lower() not in {"", "all"}
                    }
                    if actual_values and option_values and actual_values.isdisjoint(option_values):
                        findings.append(
                            {
                                "code": "ui.list.filter_options_nonmatching",
                                "severity": "error",
                                "path": f"{widget_path}.inputs.filters[{filter_index}]",
                                "message": (
                                    f"Filter options for state {state_key!r} cannot match any "
                                    f"static value at field {field_key!r}. Use option values "
                                    "that equal the record values; labels may remain user-facing."
                                ),
                                "actual_values": sorted(actual_values),
                                "option_values": sorted(option_values),
                            }
                        )
            if (
                widget_type == "item.details"
                and str(data_source.get("kind") or "") == "static"
            ):
                selected_state_key = str(inputs.get("selectedStateKey") or "").strip()
                selected_value = (
                    _read_path(initial_state, selected_state_key)
                    if selected_state_key
                    else None
                )
                static_value = data_source.get("value")
                if (
                    selected_state_key
                    and selected_value not in {None, ""}
                    and (
                        not isinstance(static_value, Mapping)
                        or str(selected_value) not in static_value
                    )
                ):
                    findings.append(
                        {
                            "code": "ui.details.static_selection_unresolvable",
                            "severity": "error",
                            "path": f"{widget_path}.dataSource.value",
                            "message": (
                                f"item.details selectedStateKey {selected_state_key!r} resolves "
                                f"to {selected_value!r}, but the static value is not a direct "
                                "map containing that key. Put keyed records directly in "
                                "dataSource.value."
                            ),
                        }
                    )
                expected_static_keys = static_event_state_values.get(
                    selected_state_key, set()
                )
                if expected_static_keys and isinstance(static_value, Mapping):
                    missing_static_keys = sorted(
                        expected_static_keys - {str(key) for key in static_value}
                    )
                    if missing_static_keys:
                        findings.append(
                            {
                                "code": "ui.details.static_selection_keys_missing",
                                "severity": "error",
                                "path": f"{widget_path}.dataSource.value",
                                "message": (
                                    f"item.details selectedStateKey {selected_state_key!r} is "
                                    "written from static collection rows, but the keyed detail "
                                    "source is missing selectable values: "
                                    + ", ".join(missing_static_keys)
                                ),
                                "missing_keys": missing_static_keys,
                            }
                        )
            for action_index, action in enumerate(actions):
                params = (
                    action.get("params")
                    if isinstance(action, Mapping)
                    and isinstance(action.get("params"), Mapping)
                    else {}
                )
                expression_strings: list[str] = []

                def _collect_expression_strings(value: Any) -> None:
                    if isinstance(value, str):
                        if re.search(
                            r"\$(?:event|state)\.[A-Za-z0-9_.-]+\s*(?:===|!==|==|!=|&&|\|\||[<>]=?)",
                            value,
                        ):
                            expression_strings.append(value)
                        return
                    if isinstance(value, Mapping):
                        for nested in value.values():
                            _collect_expression_strings(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            _collect_expression_strings(nested)

                _collect_expression_strings(params)
                if expression_strings:
                    findings.append(
                        {
                            "code": "ui.action.expression_string_unsupported",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}].params",
                            "message": (
                                "Action params do not evaluate JavaScript-like expression strings. "
                                "Use a direct $event/$state reference, a bounded structured "
                                "{kind:'expression',op:...} value, or expose the computed value "
                                "as an event field."
                            ),
                            "actual": expression_strings,
                        }
                    )
                invalid_expressions: list[dict[str, Any]] = []

                def _collect_invalid_expressions(value: Any, path: str) -> None:
                    if isinstance(value, Mapping):
                        if value.get("kind") == "expression":
                            op = str(value.get("op") or "").strip()
                            args = value.get("args")
                            if "left" in value or "right" in value:
                                invalid_expressions.append(
                                    {
                                        "path": path,
                                        "op": op,
                                        "reason": "left_right_unsupported",
                                    }
                                )
                            elif op in {"equals", "gt", "gte", "lt", "lte"} and (
                                not isinstance(args, list) or len(args) != 2
                            ):
                                invalid_expressions.append(
                                    {
                                        "path": path,
                                        "op": op,
                                        "reason": "binary_args_required",
                                    }
                                )
                        for nested_key, nested in value.items():
                            _collect_invalid_expressions(nested, f"{path}.{nested_key}")
                    elif isinstance(value, list):
                        for nested_index, nested in enumerate(value):
                            _collect_invalid_expressions(
                                nested, f"{path}[{nested_index}]"
                            )

                _collect_invalid_expressions(params, "params")
                if invalid_expressions:
                    findings.append(
                        {
                            "code": "ui.action.expression_shape_invalid",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}].params",
                            "message": (
                                "Declarative binary expressions use args, for example "
                                "{kind:'expression',op:'equals',args:['$event.status','ready']}. "
                                "The runtime does not read left/right. Prefer a direct $event "
                                "boolean field when one is available."
                            ),
                            "actual": invalid_expressions,
                        }
                    )
                if (
                    not isinstance(action, Mapping)
                    or str(action.get("type") or "") != "resourceOperation"
                ):
                    continue
                if (
                    not str(action.get("target") or "").strip()
                    or not str(params.get("operation_id") or "").strip()
                ):
                    findings.append(
                        {
                            "code": "ui.resource_operation.identity_missing",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}]",
                            "message": (
                                "resourceOperation requires a non-empty target and "
                                "params.operation_id."
                            ),
                        }
                    )
                payload = params.get("payload")
                if isinstance(payload, Mapping) and payload.get("__noop") is True:
                    findings.append(
                        {
                            "code": "ui.resource_operation.noop_payload",
                            "severity": "error",
                            "path": f"{widget_path}.actions[{action_index}].params.payload",
                            "message": (
                                "A declared resource mutation cannot use a no-op "
                                "placeholder payload."
                            ),
                        }
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
                                    "A resourceOperation move requires the board dataSource to use "
                                    "kind=resourceQuery with a non-empty resourceType. The action target "
                                    "must equal that resourceType and use operation_id=update, "
                                    "record_id=$event.id, and payload=$event.patch."
                                ),
                                "expected": {
                                    "dataSource.kind": "resourceQuery",
                                    "dataSource.resourceType": "<same non-empty value as action.target>",
                                    "action.type": "resourceOperation",
                                    "action.params.operation_id": "update",
                                    "action.params.record_id": "$event.id",
                                    "action.params.payload": "$event.patch",
                                },
                                "actual": {
                                    "dataSource.kind": str(
                                        data_source.get("kind") or ""
                                    ),
                                    "dataSource.resourceType": resource_type,
                                    "action.target": str(action.get("target") or ""),
                                    "action.params.operation_id": str(
                                        params.get("operation_id") or ""
                                    ),
                                    "action.params.record_id": str(
                                        params.get("record_id") or ""
                                    ),
                                    "action.params.payload": str(
                                        params.get("payload") or ""
                                    ),
                                },
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
    prototype_resources: Sequence[Mapping[str, Any]] | None = None,
    locale_dictionaries: Mapping[str, Mapping[str, Any]] | None = None,
    domain_packs: Sequence[str] | None = None,
) -> dict[str, Any]:
    adapter = ui_domain_adapter(domain_packs)
    if adapter is not None:
        result = copy.deepcopy(
            adapter.evaluate_ui_request(
                request,
                webui,
                prototype_records=prototype_records,
                locale_dictionaries=locale_dictionaries,
            )
        )
        result.update(_domain_metadata(domain_packs))
        return result
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
    expected_information_kinds = {
        str(item or "").strip()
        for item in requirements.get("brief_information_kinds") or []
        if str(item or "").strip()
    }
    if expected_information_kinds:
        field_kind_map = {
            "attachment": {"fileupload", "file_upload", "file"},
            "text": {"text", "shorttext", "longtext", "textarea"},
            "number": {"number", "numeric", "slider", "rating", "linearscale"},
            "choice": {
                "select",
                "dropdown",
                "combobox",
                "singlechoice",
                "multichoice",
                "checkboxes",
                "radio",
            },
            "boolean": {"boolean", "toggle", "switch"},
            "date": {"date", "daterange", "date_range"},
            "time": {"time", "timerange", "time_range"},
        }
        actual_field_types = {
            str(field.get("type") or "").strip().lower()
            for _, page in _page_schemas(webui)
            for widget in page.get("widgets") or []
            if isinstance(widget, Mapping)
            and str(widget.get("type") or "") == "ui.form"
            and isinstance(widget.get("inputs"), Mapping)
            for field in widget.get("inputs", {}).get("fields") or []
            if isinstance(field, Mapping) and str(field.get("type") or "").strip()
        }
        actual_information_kinds = {
            kind
            for kind, field_types in field_kind_map.items()
            if actual_field_types & field_types
        }
        postconditions.append(
            {
                "id": "ui.information_capture",
                "ok": expected_information_kinds.issubset(actual_information_kinds),
                "expected": sorted(expected_information_kinds),
                "actual": sorted(actual_information_kinds),
            }
        )
    if requirements.get("prototype_resource") is True:
        resource_types = {
            str(data_source.get("resourceType") or "").strip()
            for _, page in _page_schemas(webui)
            for widget in page.get("widgets") or []
            if isinstance(widget, Mapping)
            for data_source in widget_resource_queries(widget)
            if data_source["resourceType"].startswith("prototype.")
        }
        resource_actions = [
            action
            for _, page in _page_schemas(webui)
            for widget in page.get("widgets") or []
            if isinstance(widget, Mapping)
            for action in widget.get("actions") or []
            if isinstance(action, Mapping)
            and str(action.get("type") or "") == "resourceOperation"
            and str(action.get("target") or "") in resource_types
        ]
        brief_operations = {
            str(item or "").strip()
            for item in requirements.get("brief_operation_kinds") or []
            if str(item or "").strip()
        }
        expected_operations = set(requirements.get("operation_kinds") or [])
        if "create" in brief_operations:
            expected_operations.add("create")
        if brief_operations & {"update", "transition", "archive"}:
            expected_operations.add("update")
        if "delete" in brief_operations:
            expected_operations.add("delete")
        actual_operations = {
            str(dict(action.get("params") or {}).get("operation_id") or "").strip()
            for action in resource_actions
        }
        prototype_record_count = (
            len(prototype_records)
            if isinstance(prototype_records, Sequence)
            and not isinstance(prototype_records, (str, bytes, bytearray))
            else None
        )
        direct_prototype_records = bool(
            prototype_record_count
            and all(isinstance(item, Mapping) for item in prototype_records or ())
            and not any(
                "resourceType" in item and isinstance(item.get("records"), Sequence)
                for item in prototype_records or ()
                if isinstance(item, Mapping)
            )
        )
        prototype_records_bounded = bool(
            prototype_record_count is not None and prototype_record_count <= 1000
        )
        resource_sidecars = (
            [dict(item) for item in prototype_resources]
            if isinstance(prototype_resources, Sequence)
            and not isinstance(prototype_resources, (str, bytes, bytearray))
            and all(isinstance(item, Mapping) for item in prototype_resources)
            else []
        )
        assignment_ok = "update" in actual_operations
        resource_runtime_types = {item.get("resource_ref"): item.get("resource_type") for item in resource_sidecars}
        for _, page in _page_schemas(webui):
            metadata = dict(dict(page.get("meta") or {}).get("builder") or {})
            relationships = metadata.get("relationships") or []
            assignment_fields = {
                (resource_runtime_types.get(relation.get("from_resource_ref")), relation.get("from_field_ref"))
                for relation in relationships if isinstance(relation, Mapping)
            }
            # Persisted snapshots have runtime identities, not compiler-only aliases.
            for target, policy in dict(metadata.get("prototype_resource_policies") or {}).items():
                if target not in resource_types or not isinstance(policy, Mapping):
                    continue
                assignment_fields.update(
                    (target, relation.get("field_ref"))
                    for relation in policy.get("relationships") or []
                    if isinstance(relation, Mapping) and relation.get("target_resource_type") in resource_types
                )
            for target, field_ref in assignment_fields:
                if not target or not field_ref:
                    continue
                for _, form_page in _page_schemas(webui):
                    for widget in form_page.get("widgets") or []:
                        if widget.get("type") != "ui.form":
                            continue
                        editable_fields = {field.get("id") for field in dict(widget.get("inputs") or {}).get("fields") or [] if not field.get("disabled") and not field.get("readOnly")}
                        if field_ref not in editable_fields:
                            continue
                        assignment_ok |= any(action.get("target") == target and action in resource_actions and dict(action.get("params") or {}).get("operation_id") == "create" for action in widget.get("actions") or [])
        if "assign" in brief_operations:
            postconditions.append({
                "id": "resource.assignment_operation", "ok": assignment_ok,
                "expected": "update a selected record or create a relationship through an editable reference field",
                "actual": sorted(actual_operations),
            })
        sidecar_resource_types = {
            str(item.get("resource_type") or "").strip()
            for item in resource_sidecars
            if str(item.get("resource_type") or "").strip()
        }
        sidecar_records = [item.get("records") for item in resource_sidecars]
        multi_resource_records = bool(
            resource_sidecars
            and sidecar_resource_types == resource_types
            and len(sidecar_resource_types) == len(resource_sidecars)
            and all(
                isinstance(records, list)
                and len(records) <= 1000
                and all(isinstance(record, Mapping) for record in records)
                for records in sidecar_records
            )
            and any(sidecar_records)
        )
        prototype_source_ok = bool(
            resource_types
            and (
                sidecar_resource_types == resource_types
                if resource_sidecars
                else len(resource_types) == 1
            )
        )
        prototype_data_ok = bool(
            multi_resource_records
            or (direct_prototype_records and prototype_records_bounded)
        )
        postconditions.extend(
            [
                {
                    "id": "resource.prototype_source",
                    "ok": prototype_source_ok,
                    "expected": "all queried Prototype resource types match their materialization sidecars",
                    "actual": {
                        "queried": sorted(resource_types),
                        "sidecars": sorted(sidecar_resource_types),
                    },
                },
                {
                    "id": "resource.persistence_operations",
                    "ok": expected_operations.issubset(actual_operations),
                    "expected": sorted(expected_operations),
                    "actual": sorted(actual_operations),
                },
                {
                    "id": "resource.prototype_records",
                    "ok": prototype_data_ok,
                    "expected": "bounded direct records for every Prototype resource",
                    "actual": {
                        "count": prototype_record_count,
                        "bounded": prototype_records_bounded,
                        "direct_records": direct_prototype_records,
                        "resource_count": len(resource_sidecars),
                        "resource_records": [
                            len(records) if isinstance(records, list) else None
                            for records in sidecar_records
                        ],
                    },
                },
            ]
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
                toolbar_state_keys = {
                    control['stateKey']
                    for _, page in _page_schemas(webui)
                    for widget in page.get('widgets') or []
                    if isinstance(widget, Mapping) and widget.get('type') == 'ui.queryToolbar'
                    for control in (widget.get('inputs') or {}).get('controls') or []
                    if isinstance(control, Mapping) and isinstance(control.get('stateKey'), str)
                    and control.get('kind') in {'search', 'filter'}
                    and control.get('inputType') in {'search', 'text', 'date', 'number', 'select'}
                }
                state_writes.update(toolbar_state_keys)
                executable_query_refs.update(query_state_refs.intersection(toolbar_state_keys))
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
                    # A selection-backed details/toolbar command is a distinct valid path.
                    selected_editor_entries = [
                        action for _, candidate_page in _page_schemas(webui)
                        for candidate in candidate_page.get('widgets') or []
                        if isinstance(candidate, Mapping) and candidate is not board
                        and candidate.get('type') in {'ui.actions', 'item.details'}
                        for action in candidate.get('actions') or []
                        if isinstance(action, Mapping) and action.get('type') == 'openModal'
                        and str(action.get('on', '')).startswith('click:')
                        and (action.get('params') or {}).get('modalId') in modal_edit_ids
                        and any(
                            action.get('enabledIf') == f"$state.{ref} !== ''"
                            or any(button.get('id') == str(action['on'])[6:]
                                   and button.get('enabledIf') == f"$state.{ref} !== ''"
                                   for button in (candidate.get('inputs') or {}).get('buttons') or []
                                   if isinstance(button, Mapping))
                            for ref in edit_state_refs
                        )
                    ]
                    edit_selection_ok = bool(
                        edit_state_refs
                        and (
                            (inline_edit and edit_selection_events)
                            or (modal_events & edit_selection_events)
                            or ('select' in edit_selection_events and selected_editor_entries)
                        )
                    )
                    postconditions.append(
                        {
                            "id": "kanban.edit_selection",
                            "ok": edit_selection_ok,
                            "expected": (
                                "the board writes the edited record id from $event.id on the same event that opens "
                                "a modal editor, or on selection for an inline editor or guarded details/toolbar entry"
                            ),
                            "actual": {
                                "stateRefs": sorted(edit_state_refs),
                                "modalEvents": sorted(modal_events),
                                "selectionEvents": sorted(edit_selection_events),
                                "inline": inline_edit,
                                "selectedEditorEntries": len(selected_editor_entries),
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
        "input_attribution": {"profile": "generic", "domain_packs": []},
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
