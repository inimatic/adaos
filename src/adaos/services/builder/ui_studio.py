"""Bounded LLM UI Studio experiments over an existing declarative WebUI.

The model owns a compact intent: view roles, relations, and priorities.  Core
owns layout plans, WebUI compilation, contract preservation, scoring, storage,
and staging.  An experiment never advances ``ui_revisions/current.txt``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.artifact_pipeline.storage import (
    atomic_write_json,
    mutation_lock,
    read_atomic_json,
)
from adaos.services.runtime_paths import current_state_dir

from .workflow import BuilderWorkflowError


INTENT_SCHEMA = "adaos.builder.ui_studio_intent.v1"
EXPERIMENT_SCHEMA = "adaos.builder.ui_studio_experiment.v1"
PROFILE_REF = "geo_explorer.v1"
CONTEXT_LIMIT_BYTES = 64 * 1024
EVALUATION_THRESHOLD = 80.0


class BuilderUIStudioError(BuilderWorkflowError):
    """Raised when an experiment cannot be admitted or compiled safely."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _safe_token(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", token):
        raise BuilderUIStudioError(f"invalid {field}")
    return token


@lru_cache(maxsize=4)
def _schema(filename: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _validate(value: Mapping[str, Any], filename: str, label: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_schema(filename)).iter_errors(result),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BuilderUIStudioError(f"invalid {label} at {location}: {error.message}")
    return result


def validate_ui_studio_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a compact intent and its graph-local identity invariants."""

    intent = _validate(
        value,
        "builder.ui_studio_intent.v1.schema.json",
        "UI Studio intent",
    )
    views = [dict(item) for item in intent["views"]]
    refs = [str(item["ref"]) for item in views]
    widget_ids = [str(item["widget_id"]) for item in views]
    if len(refs) != len(set(refs)):
        raise BuilderUIStudioError("UI Studio view refs must be unique")
    if len(widget_ids) != len(set(widget_ids)):
        raise BuilderUIStudioError("UI Studio widgets may have only one declared role")
    declared = set(refs)
    if intent["goal"]["primary_ref"] not in declared:
        raise BuilderUIStudioError("UI Studio primary_ref must name a declared view")
    relation_ids: set[str] = set()
    for relation in intent["relations"]:
        relation_id = str(relation["id"])
        if relation_id in relation_ids:
            raise BuilderUIStudioError("UI Studio relation ids must be unique")
        relation_ids.add(relation_id)
        unknown = (set(relation["source_refs"]) | set(relation["target_refs"])) - declared
        if unknown:
            raise BuilderUIStudioError(
                "UI Studio relation references unknown views: " + ", ".join(sorted(unknown))
            )
    constraint_ids: set[str] = set()
    for constraint in intent["constraints"]:
        constraint_id = str(constraint["id"])
        if constraint_id in constraint_ids:
            raise BuilderUIStudioError("UI Studio constraint ids must be unique")
        constraint_ids.add(constraint_id)
        unknown = set(constraint["refs"]) - declared
        if unknown:
            raise BuilderUIStudioError(
                "UI Studio constraint references unknown views: " + ", ".join(sorted(unknown))
            )
    return intent


def ui_studio_profiles() -> list[dict[str, Any]]:
    """Return compiler-owned profiles; prompts receive only the selected one."""

    return [
        {
            "profile_ref": PROFILE_REF,
            "title": "Geo explorer",
            "status": "experimental",
            "description": (
                "Map-led analytical workspace with a co-visible collection, "
                "selection-driven inspector, bounded controls, and compact disclosure."
            ),
            "required_roles": ["primary", "collection", "detail"],
            "supported_relations": [
                "co_visible",
                "selection_drives",
                "filters",
                "details_for",
                "opens_detail",
            ],
            "options": [
                {
                    "option_id": "atlas",
                    "label": "Атлас",
                    "description": "Карта — основная область; реестр и инспектор справа.",
                },
                {
                    "option_id": "registry",
                    "label": "Реестр",
                    "description": "Реестр — основная область; карта и инспектор справа.",
                },
            ],
            "context_limit_bytes": CONTEXT_LIMIT_BYTES,
        }
    ]


def small_cities_benchmark_intent(project_ref: str) -> dict[str, Any]:
    """Return the first repeatable Studio benchmark, not a production special case."""

    rows = [
        ("heading", "header", 100, []),
        ("navigation", "navigation", 95, []),
        ("theme", "control", 40, []),
        ("indicator", "control", 100, []),
        ("period", "control", 90, []),
        ("filters", "filters", 90, []),
        ("overview-map", "primary", 100, []),
        (
            "overview-table",
            "collection",
            95,
            ["name", "region", "value_label"],
        ),
        ("passport-city", "detail", 90, []),
    ]
    constraints = [
        ("preserve-data", "preserve_data_contracts", "hard", 10, []),
        ("truthful-data", "no_invented_data", "hard", 10, []),
        (
            "selection-in-context",
            "stable_selection",
            "hard",
            9,
            ["overview-map", "overview-table", "passport-city"],
        ),
        (
            "wide-co-visibility",
            "co_visible_primary_views",
            "hard",
            9,
            ["overview-map", "overview-table"],
        ),
        ("compact-access", "compact_reachable", "hard", 8, []),
        (
            "first-viewport",
            "first_viewport_priority",
            "soft",
            7,
            ["overview-map", "overview-table"],
        ),
        ("bounded-model-context", "bounded_context", "hard", 10, []),
    ]
    return validate_ui_studio_intent(
        {
            "schema": INTENT_SCHEMA,
            "intent_id": "ui-intent:small-cities-geo-explorer",
            "project_ref": project_ref,
            "profile_ref": PROFILE_REF,
            "base_revision": None,
            "goal": {
                "kind": "explore",
                "statement": (
                    "Исследовать малые города по карте и реестру, не теряя "
                    "выбранный город и контекст показателя."
                ),
                "primary_ref": "view:overview-map",
            },
            "views": [
                {
                    "ref": f"view:{widget_id}",
                    "widget_id": widget_id,
                    "role": role,
                    "priority": priority,
                    **({"field_refs": fields} if fields else {}),
                }
                for widget_id, role, priority, fields in rows
            ],
            "relations": [
                {
                    "id": "map-and-registry",
                    "kind": "co_visible",
                    "source_refs": ["view:overview-map"],
                    "target_refs": ["view:overview-table"],
                    "strength": "hard",
                },
                {
                    "id": "selection-drives-city",
                    "kind": "selection_drives",
                    "source_refs": ["view:overview-map", "view:overview-table"],
                    "target_refs": ["view:passport-city"],
                    "strength": "hard",
                },
                {
                    "id": "filters-drive-overview",
                    "kind": "filters",
                    "source_refs": ["view:filters", "view:indicator", "view:period"],
                    "target_refs": ["view:overview-map", "view:overview-table"],
                    "strength": "hard",
                },
                {
                    "id": "city-details",
                    "kind": "details_for",
                    "source_refs": ["view:passport-city"],
                    "target_refs": ["view:overview-map", "view:overview-table"],
                    "strength": "hard",
                },
            ],
            "constraints": [
                {
                    "id": constraint_id,
                    "rule": rule,
                    "strength": strength,
                    "weight": weight,
                    "refs": [f"view:{item}" for item in refs],
                }
                for constraint_id, rule, strength, weight, refs in constraints
            ],
            "responsive": {
                "wide_min_px": 1180,
                "compact_strategy": "progressive_disclosure",
            },
            "provenance": {
                "kind": "benchmark",
                "source": "small-cities.v1",
                "model": None,
                "request_id": None,
            },
        }
    )


def _page(webui: Mapping[str, Any]) -> dict[str, Any]:
    try:
        page = webui["ui"]["application"]["desktop"]["pageSchema"]
    except (KeyError, TypeError) as exc:
        raise BuilderUIStudioError("WebUI has no desktop pageSchema") from exc
    if not isinstance(page, Mapping):
        raise BuilderUIStudioError("WebUI desktop pageSchema must be an object")
    return dict(page)


def _walk_widgets(widgets: Any):
    for widget in widgets if isinstance(widgets, list) else []:
        if not isinstance(widget, Mapping):
            continue
        yield widget
        yield from _walk_widgets(widget.get("children"))


def _widget_index(page: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for widget in _walk_widgets(page.get("widgets")):
        widget_id = str(widget.get("id") or "").strip()
        if not widget_id:
            continue
        if widget_id in index:
            raise BuilderUIStudioError(f"duplicate WebUI widget id: {widget_id}")
        index[widget_id] = widget
    return index


def _datasource_signature(value: Any) -> str | None:
    return _digest(value) if isinstance(value, Mapping) else None


def _widget_summary(widget: Mapping[str, Any]) -> dict[str, Any]:
    inputs = widget.get("inputs") if isinstance(widget.get("inputs"), Mapping) else {}
    data_source = (
        widget.get("dataSource")
        if isinstance(widget.get("dataSource"), Mapping)
        else {}
    )
    actions = [
        {
            "on": str(action.get("on") or ""),
            "type": str(action.get("type") or ""),
            "state_keys": sorted(
                str(key)
                for key in (
                    action.get("params")
                    if isinstance(action.get("params"), Mapping)
                    else {}
                )
            ),
        }
        for action in widget.get("actions") or []
        if isinstance(action, Mapping)
    ][:8]
    columns = [
        {
            "key": str(column.get("key") or column.get("path") or ""),
            "label": str(column.get("label") or ""),
        }
        for column in inputs.get("columns") or []
        if isinstance(column, Mapping)
    ][:20]
    return {
        "id": str(widget.get("id") or ""),
        "type": str(widget.get("type") or ""),
        "title": str(widget.get("title") or "")[:240],
        "area": str(widget.get("area") or ""),
        "visible_when": str(widget.get("visibleIf") or "")[:400],
        "data": {
            "kind": data_source.get("kind"),
            "name": data_source.get("name"),
            "result_path": data_source.get("resultPath"),
            "parameter_keys": sorted(
                str(key)
                for key in (
                    data_source.get("params")
                    if isinstance(data_source.get("params"), Mapping)
                    else {}
                )
            ),
        },
        "columns": columns,
        "actions": actions,
    }


def compact_webui_inventory(
    webui: Mapping[str, Any],
    *,
    preferred_widget_ids: list[str] | None = None,
    limit_bytes: int = CONTEXT_LIMIT_BYTES,
) -> dict[str, Any]:
    """Project a bounded structural inventory, never the full WebUI document."""

    page = _page(webui)
    index = _widget_index(page)
    preferred = [item for item in preferred_widget_ids or [] if item in index]
    probable_types = {
        "visual.geoMap": 0,
        "ui.table": 1,
        "item.details": 2,
        "ui.queryToolbar": 3,
        "input.selector": 4,
        "ui.actions": 5,
        "static.markdown": 6,
    }
    remaining = sorted(
        (item for item in index if item not in preferred),
        key=lambda item: (
            probable_types.get(str(index[item].get("type") or ""), 20),
            item,
        ),
    )
    ordered = (preferred + remaining)[:40]
    inventory = {
        "page": {
            "id": str(page.get("id") or ""),
            "title": str(page.get("title") or "")[:240],
            "layout": copy.deepcopy(page.get("layout") or {}),
        },
        "widgets": [_widget_summary(index[item]) for item in ordered],
        "total_widgets": len(index),
        "included_widgets": len(ordered),
        "truncated": len(ordered) < len(index),
    }
    encoded = _canonical(inventory)
    if len(encoded) > limit_bytes:
        for widget in inventory["widgets"]:
            widget["columns"] = [item.get("key") for item in widget["columns"]]
            widget["actions"] = [item.get("type") for item in widget["actions"]]
            widget.pop("visible_when", None)
        inventory["truncated"] = True
        encoded = _canonical(inventory)
    if len(encoded) > limit_bytes:
        raise BuilderUIStudioError(
            f"UI Studio context exceeds {limit_bytes} bytes after bounded projection"
        )
    inventory["input_bytes"] = len(encoded)
    inventory["limit_bytes"] = limit_bytes
    return inventory


def _intent_context_budget(intent: Mapping[str, Any], webui: Mapping[str, Any]) -> dict[str, Any]:
    inventory = compact_webui_inventory(
        webui,
        preferred_widget_ids=[str(item["widget_id"]) for item in intent["views"]],
    )
    return {
        "included_widgets": inventory["included_widgets"],
        "total_widgets": inventory["total_widgets"],
        "input_bytes": inventory["input_bytes"],
        "limit_bytes": inventory["limit_bytes"],
        "truncated": inventory["truncated"],
    }


def _region(
    region_id: str,
    role: str,
    *,
    label: str | None = None,
    compact: str = "stack",
    preferred: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    grow: float | None = None,
    resizable: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": region_id,
        "role": role,
        "priority": 100,
        "scroll": "region" if role not in {"toolbar", "commands", "status"} else "none",
        "presentation": {"wide": "pane", "compact": compact},
    }
    if label:
        result["label"] = label
    sizing = {
        key: value
        for key, value in {
            "minPx": minimum,
            "preferredPx": preferred,
            "maxPx": maximum,
            "grow": grow,
            "resizable": resizable or None,
            "remember": resizable or None,
        }.items()
        if value is not None
    }
    if sizing:
        result["size"] = sizing
    return result


def _role_views(intent: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in intent["views"]:
        result.setdefault(str(item["role"]), []).append(dict(item))
    for rows in result.values():
        rows.sort(key=lambda item: (-int(item["priority"]), str(item["ref"])))
    return result


def _shared_view_scope(*widgets: Mapping[str, Any]) -> str:
    values = [str(widget.get("visibleIf") or "").strip() for widget in widgets]
    matches = [
        re.search(r"\$state\.activeView\s*===\s*(['\"])([^'\"]+)\1", value)
        for value in values
    ]
    names = [match.group(2) for match in matches if match]
    if names and len(set(names)) == 1:
        return f"$state.activeView === '{names[0]}'"
    return values[0] if values and values[0] else "true"


def _project_columns(widget: dict[str, Any], field_refs: list[str]) -> None:
    if not field_refs:
        return
    inputs = widget.get("inputs") if isinstance(widget.get("inputs"), dict) else {}
    columns = [item for item in inputs.get("columns") or [] if isinstance(item, Mapping)]
    by_key = {str(item.get("key") or item.get("path") or ""): item for item in columns}
    selected = [copy.deepcopy(by_key[key]) for key in field_refs if key in by_key]
    if selected:
        width_hints = {
            "name": ("9rem", "truncate", "start"),
            "region": ("12rem", "wrap", "start"),
            "value_label": ("8rem", "truncate", "end"),
        }
        for column in selected:
            hint = width_hints.get(str(column.get("key") or ""))
            if hint is None:
                continue
            width, overflow, align = hint
            column.setdefault("width", width)
            column.setdefault("overflow", overflow)
            column.setdefault("align", align)
        inputs["columns"] = selected
        widget["inputs"] = inputs


def _apply_professional_widget_defaults(page: Mapping[str, Any]) -> None:
    """Make generated candidates useful without relying on weak UI defaults."""

    for widget in _walk_widgets(page.get("widgets")):
        if not isinstance(widget, dict):
            continue
        widget_type = str(widget.get("type") or "")
        inputs = widget.get("inputs") if isinstance(widget.get("inputs"), dict) else {}
        if widget_type == "ui.table":
            for column in inputs.get("columns") or []:
                if not isinstance(column, dict):
                    continue
                if column.get("key") or column.get("path"):
                    column.setdefault("sortable", True)
        elif widget_type == "visual.metricChart":
            # A chart is the summary.  A full point list is an explicit
            # accessibility/detail option, not a useful visual default.
            inputs.setdefault("showSummary", False)
            widget["inputs"] = inputs


def _stable_selection(widget: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep selection in context and return removed navigation effects."""

    removed: list[dict[str, Any]] = []
    actions = widget.get("actions") if isinstance(widget.get("actions"), list) else []
    for action in actions:
        if not isinstance(action, dict) or str(action.get("on") or "") != "select":
            continue
        if str(action.get("type") or "") != "updateState":
            continue
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
        navigation = {
            key: value
            for key, value in params.items()
            if key in {"activeView", "route", "selectedTab"}
        }
        if navigation:
            removed.append(navigation)
            action["params"] = {
                key: value for key, value in params.items() if key not in navigation
            }
    return removed


def _navigation_label(page: Mapping[str, Any], effects: Mapping[str, Any]) -> str:
    target = str(effects.get("activeView") or effects.get("selectedTab") or "").strip()
    for widget in _walk_widgets(page.get("widgets")):
        if str(widget.get("type") or "") != "ui.actions":
            continue
        inputs = widget.get("inputs") if isinstance(widget.get("inputs"), Mapping) else {}
        for button in inputs.get("buttons") or []:
            if isinstance(button, Mapping) and str(button.get("id") or "") == target:
                return "Открыть: " + str(button.get("label") or target)
    return "Открыть подробности"


def _group(
    group_id: str,
    area: str,
    children: list[dict[str, Any]],
    *,
    layout: str,
    min_width: int = 220,
) -> dict[str, Any]:
    return {
        "id": group_id,
        "type": "layout.group",
        "area": area,
        "inputs": {
            "layout": layout,
            "gap": "sm",
            "wrap": True,
            **({"minItemWidthPx": min_width} if layout == "grid" else {}),
        },
        "children": children,
    }


def _consolidated_variant(
    variant: Mapping[str, Any],
    *,
    active_view: str | None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(variant))
    result["regions"] = [
        _region("studio-header", "toolbar"),
        _region("studio-controls", "toolbar"),
        _region("main", "main", grow=1),
    ]
    result["contentWidth"] = "fluid"
    result["maxContentWidthPx"] = 1800
    if active_view == "overview":
        result["regions"].insert(2, _region("studio-filters", "toolbar"))
    return result


def _compile_geo_option(
    base_webui: Mapping[str, Any],
    intent: Mapping[str, Any],
    *,
    option_id: str,
    experiment_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if option_id not in {"atlas", "registry"}:
        raise BuilderUIStudioError(f"unsupported UI Studio option: {option_id}")
    candidate = copy.deepcopy(dict(base_webui))
    page = candidate["ui"]["application"]["desktop"]["pageSchema"]
    if not isinstance(page.get("widgets"), list):
        raise BuilderUIStudioError("WebUI page widgets must be an array")
    _apply_professional_widget_defaults(page)
    original_page = _page(base_webui)
    roles = _role_views(intent)
    missing = [role for role in ("primary", "collection", "detail") if not roles.get(role)]
    if missing:
        raise BuilderUIStudioError(
            "geo_explorer.v1 requires roles: " + ", ".join(missing)
        )
    top_level = {
        str(widget.get("id") or ""): widget
        for widget in page["widgets"]
        if isinstance(widget, dict)
    }
    declared_ids = {str(item["widget_id"]) for item in intent["views"]}
    unknown = declared_ids - set(_widget_index(page))
    if unknown:
        raise BuilderUIStudioError(
            "UI Studio intent references missing widgets: " + ", ".join(sorted(unknown))
        )
    structural_ids = {
        str(item["widget_id"])
        for role in ("header", "navigation", "control")
        for item in roles.get(role, [])
    }
    nested = structural_ids - set(top_level)
    if nested:
        raise BuilderUIStudioError(
            "UI Studio structural widgets must be page-level: " + ", ".join(sorted(nested))
        )

    def take(widget_id: str) -> dict[str, Any]:
        widget = top_level[widget_id]
        page["widgets"].remove(widget)
        return widget

    header_children = [
        take(str(item["widget_id"]))
        for role in ("header", "navigation")
        for item in roles.get(role, [])
    ]
    control_children = [
        take(str(item["widget_id"])) for item in roles.get("control", [])
    ]
    inserted: list[dict[str, Any]] = []
    if header_children:
        inserted.append(
            _group(
                "ui-studio-header",
                "studio-header",
                header_children,
                layout="row",
            )
        )
    if control_children:
        inserted.append(
            _group(
                "ui-studio-controls",
                "studio-controls",
                control_children,
                layout="grid",
                min_width=210,
            )
        )
    for item in roles.get("filters", []):
        widget = top_level.get(str(item["widget_id"]))
        if widget is not None:
            widget["area"] = "studio-filters"

    primary_view = roles["primary"][0]
    collection_view = roles["collection"][0]
    detail_view = roles["detail"][0]
    primary_widget = top_level.get(str(primary_view["widget_id"]))
    collection_widget = top_level.get(str(collection_view["widget_id"]))
    detail_widget = top_level.get(str(detail_view["widget_id"]))
    if not all(isinstance(item, dict) for item in (primary_widget, collection_widget, detail_widget)):
        raise BuilderUIStudioError("geo_explorer.v1 view roles must reference page-level widgets")
    scope = _shared_view_scope(primary_widget, collection_widget)
    primary_area = "studio-primary" if option_id == "atlas" else "studio-secondary"
    collection_area = "studio-secondary" if option_id == "atlas" else "studio-primary"
    primary_widget.update(area=primary_area, visibleIf=scope, sizing="fill")
    collection_widget.update(area=collection_area, visibleIf=scope)
    _project_columns(collection_widget, list(collection_view.get("field_refs") or []))
    navigation_effects = _stable_selection(primary_widget) + _stable_selection(collection_widget)

    inspector = copy.deepcopy(detail_widget)
    inspector["id"] = "ui-studio-selection-inspector"
    inspector["area"] = "studio-inspector"
    inspector["visibleIf"] = scope
    inspector["sizing"] = "content"
    _project_columns(inspector, list(detail_view.get("field_refs") or []))
    inserted.append(inspector)
    if navigation_effects:
        effects = navigation_effects[0]
        inserted.append(
            {
                "id": "ui-studio-selection-actions",
                "type": "ui.actions",
                "area": "studio-inspector",
                "visibleIf": scope,
                "inputs": {
                    "variant": "toolbar",
                    "buttons": [
                        {
                            "id": "open-detail",
                            "label": _navigation_label(original_page, effects),
                        }
                    ],
                },
                "actions": [
                    {
                        "id": "open-detail",
                        "on": "click:open-detail",
                        "type": "updateState",
                        "params": copy.deepcopy(effects),
                    }
                ],
            }
        )
    for widget in page["widgets"]:
        if isinstance(widget, dict) and str(widget.get("id") or "") == "overview-mode":
            widget["visibleIf"] = "false"

    page["widgets"] = inserted + page["widgets"]
    old_layout = page.get("layout") if isinstance(page.get("layout"), Mapping) else {}
    new_variants: list[dict[str, Any]] = []
    overview_found = False
    for variant in old_layout.get("variants") or []:
        if not isinstance(variant, Mapping):
            continue
        match = re.search(
            r"\$state\.activeView\s*===\s*(['\"])([^'\"]+)\1",
            str(variant.get("when") or ""),
        )
        active_view = match.group(2) if match else None
        if active_view != "overview":
            new_variants.append(_consolidated_variant(variant, active_view=active_view))
            continue
        overview_found = True
        new_variants.append(
            {
                "id": str(variant.get("id") or "overview"),
                "when": str(variant.get("when") or scope),
                "pattern": "master-detail",
                "density": "compact",
                "contentWidth": "fluid",
                "maxContentWidthPx": 2000,
                "scroll": "regions",
                "regions": [
                    _region("studio-header", "toolbar"),
                    _region("studio-controls", "toolbar"),
                    _region("studio-filters", "toolbar"),
                    _region("studio-primary", "main", grow=1, minimum=560),
                    _region(
                        "studio-secondary",
                        "detail",
                        label="Города",
                        compact="sheet",
                        preferred=550 if option_id == "atlas" else 560,
                        minimum=480,
                        maximum=620,
                        resizable=True,
                    ),
                    _region(
                        "studio-inspector",
                        "inspector",
                        label="Выбранный город",
                        compact="sheet",
                        preferred=280,
                        minimum=260,
                        maximum=480,
                        resizable=True,
                    ),
                ],
                "interaction": {
                    "selection": "single",
                    "rowActivation": "select",
                    "detail": "inline",
                    "filters": "inline",
                    "actions": "adaptive",
                },
            }
        )
    if not overview_found:
        raise BuilderUIStudioError("geo_explorer.v1 requires an overview layout variant")
    page["layout"] = {
        "version": 2,
        "pattern": "master-detail",
        "density": "compact",
        "contentWidth": "fluid",
        "maxContentWidthPx": 2000,
        "scroll": "regions",
        "regions": [
            _region("studio-header", "toolbar"),
            _region("studio-controls", "toolbar"),
            _region("main", "main", grow=1),
        ],
        "interaction": {
            "selection": "single",
            "rowActivation": "select",
            "filters": "inline",
            "actions": "adaptive",
        },
        "variants": new_variants,
    }
    page.setdefault("meta", {}).setdefault("ui_studio", {}).update(
        {
            "schema": "adaos.builder.ui_studio_candidate.v1",
            "experiment_id": experiment_id,
            "option_id": option_id,
            "profile_ref": PROFILE_REF,
            "intent_digest": _digest(intent),
            "base_digest": _digest(base_webui),
        }
    )
    plan = {
        "option_id": option_id,
        "label": "Атлас" if option_id == "atlas" else "Реестр",
        "profile_ref": PROFILE_REF,
        "primary_ref": (
            str(primary_view["ref"])
            if option_id == "atlas"
            else str(collection_view["ref"])
        ),
        "layout_pattern": "master-detail",
    }
    return candidate, plan


def _webui_schema_findings(candidate: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors = sorted(
        Draft202012Validator(_schema("webui.v1.schema.json")).iter_errors(candidate),
        key=lambda item: list(item.absolute_path),
    )
    messages = []
    for error in errors[:8]:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        messages.append(f"{path}: {error.message}")
    return not errors, messages


def _candidate_evaluation(
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    intent: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    base_page = _page(base)
    candidate_page = _page(candidate)
    base_widgets = _widget_index(base_page)
    candidate_widgets = _widget_index(candidate_page)
    schema_ok, schema_errors = _webui_schema_findings(candidate)
    missing_ids = sorted(set(base_widgets) - set(candidate_widgets))
    changed_types = sorted(
        widget_id
        for widget_id in set(base_widgets) & set(candidate_widgets)
        if base_widgets[widget_id].get("type") != candidate_widgets[widget_id].get("type")
    )
    changed_sources = sorted(
        widget_id
        for widget_id in set(base_widgets) & set(candidate_widgets)
        if _datasource_signature(base_widgets[widget_id].get("dataSource"))
        != _datasource_signature(candidate_widgets[widget_id].get("dataSource"))
    )
    base_source_names = {
        str(source.get("name"))
        for widget in base_widgets.values()
        if isinstance(source := widget.get("dataSource"), Mapping) and source.get("name")
    }
    candidate_source_names = {
        str(source.get("name"))
        for widget in candidate_widgets.values()
        if isinstance(source := widget.get("dataSource"), Mapping) and source.get("name")
    }
    invented_sources = sorted(candidate_source_names - base_source_names)
    roles = _role_views(intent)
    selection_ids = [
        str(item["widget_id"])
        for role in ("primary", "collection")
        for item in roles.get(role, [])[:1]
    ]
    unstable = []
    for widget_id in selection_ids:
        widget = candidate_widgets.get(widget_id) or {}
        for action in widget.get("actions") or []:
            if not isinstance(action, Mapping) or action.get("on") != "select":
                continue
            params = action.get("params") if isinstance(action.get("params"), Mapping) else {}
            if any(key in params for key in ("activeView", "route", "selectedTab")):
                unstable.append(widget_id)
    overview = next(
        (
            item
            for item in candidate_page.get("layout", {}).get("variants", [])
            if isinstance(item, Mapping) and item.get("id") == "overview"
        ),
        {},
    )
    regions = {
        str(item.get("id") or ""): item
        for item in overview.get("regions") or []
        if isinstance(item, Mapping)
    }
    compact_hidden = sorted(
        region_id
        for region_id, region in regions.items()
        if region.get("presentation", {}).get("compact") == "hidden"
    )
    inspector_ok = "ui-studio-selection-inspector" in candidate_widgets
    navigation_ok = "ui-studio-selection-actions" in candidate_widgets
    co_visible_ok = {
        str((roles["primary"][0])["widget_id"]),
        str((roles["collection"][0])["widget_id"]),
    }.issubset(candidate_widgets) and {
        str(candidate_widgets[str(roles["primary"][0]["widget_id"])].get("area") or ""),
        str(candidate_widgets[str(roles["collection"][0]["widget_id"])].get("area") or ""),
    } == {"studio-primary", "studio-secondary"}
    expected_primary = str(intent["goal"]["primary_ref"])
    primary_matches = str(plan["primary_ref"]) == expected_primary
    top_regions = [
        item
        for item in overview.get("regions") or []
        if isinstance(item, Mapping) and item.get("role") == "toolbar"
    ]
    hard_checks = [
        (
            "schema.valid",
            schema_ok,
            "Кандидат соответствует adaos.webui.v1.",
            schema_errors or ["webui.v1"],
        ),
        (
            "contracts.widgets_preserved",
            not missing_ids and not changed_types,
            "Исходные виджеты и их типы сохранены.",
            (missing_ids + changed_types) or [f"widgets:{len(base_widgets)}"],
        ),
        (
            "contracts.data_preserved",
            not changed_sources and not invented_sources,
            "Источники данных не заменены и не изобретены.",
            (changed_sources + invented_sources) or sorted(candidate_source_names),
        ),
        (
            "interaction.stable_selection",
            not unstable and inspector_ok,
            "Выбор не выталкивает пользователя из обзора и обновляет инспектор.",
            unstable or ["ui-studio-selection-inspector"],
        ),
        (
            "interaction.navigation_preserved",
            navigation_ok,
            "Переход к подробностям остался явным действием.",
            ["ui-studio-selection-actions"] if navigation_ok else [],
        ),
        (
            "responsive.reachable",
            not compact_hidden,
            "Основные области достижимы в compact-режиме.",
            compact_hidden or sorted(regions),
        ),
    ]
    soft_checks = [
        (
            "composition.co_visible",
            co_visible_ok,
            "Карта и реестр одновременно видимы на широком экране.",
            ["studio-primary", "studio-secondary"],
        ),
        (
            "composition.primary_alignment",
            primary_matches,
            "Главная область соответствует приоритету intent.",
            [f"intent:{expected_primary}", f"option:{plan['primary_ref']}"],
        ),
        (
            "density.bounded_controls",
            len(top_regions) <= 3,
            "Заголовок, селекторы и фильтры собраны в ограниченные панели.",
            [f"toolbar_regions:{len(top_regions)}"],
        ),
    ]
    findings = [
        {
            "id": finding_id,
            "severity": "hard",
            "passed": passed,
            "message": message,
            "evidence": evidence[:24],
        }
        for finding_id, passed, message, evidence in hard_checks
    ] + [
        {
            "id": finding_id,
            "severity": "soft",
            "passed": passed,
            "message": message,
            "evidence": evidence[:24],
        }
        for finding_id, passed, message, evidence in soft_checks
    ]
    contract_score = 100.0 if all(item[1] for item in hard_checks[:3]) else 0.0
    dimensions = [
        {
            "id": "task_fit",
            "score": 100.0 if primary_matches else 72.0,
            "weight": 3.0,
        },
        {
            "id": "information_architecture",
            "score": 98.0 if co_visible_ok and len(regions) >= 6 else 68.0,
            "weight": 2.0,
        },
        {
            "id": "interaction_continuity",
            "score": 100.0 if not unstable and inspector_ok and navigation_ok else 45.0,
            "weight": 2.0,
        },
        {
            "id": "responsive_reachability",
            "score": 96.0 if not compact_hidden else 40.0,
            "weight": 1.5,
        },
        {
            "id": "contract_preservation",
            "score": contract_score,
            "weight": 3.0,
        },
        {
            "id": "visual_density",
            "score": 94.0 if len(top_regions) <= 3 else 65.0,
            "weight": 1.5,
        },
    ]
    total_weight = sum(item["weight"] for item in dimensions)
    score = round(
        sum(item["score"] * item["weight"] for item in dimensions) / total_weight,
        1,
    )
    hard_failures = sum(1 for item in findings if item["severity"] == "hard" and not item["passed"])
    passed = hard_failures == 0 and score >= EVALUATION_THRESHOLD
    return {
        "passed": passed,
        "score": score,
        "threshold": EVALUATION_THRESHOLD,
        "hard_failures": hard_failures,
        "dimensions": dimensions,
        "findings": findings,
        "summary": (
            "Вариант прошёл структурные и контрактные проверки."
            if passed
            else "Вариант не прошёл обязательные проверки или порог качества."
        ),
    }


def ui_studio_model_contract(widget_ids: list[str]) -> dict[str, Any]:
    """Strict provider output: classification only, never WebUI or code."""

    ids = list(dict.fromkeys(_safe_token(item, field="widget id") for item in widget_ids))
    if not ids:
        raise BuilderUIStudioError("UI Studio model context has no widgets")
    view_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["widget_id", "role", "priority", "field_refs"],
        "properties": {
            "widget_id": {"type": "string", "enum": ids},
            "role": {
                "type": "string",
                "enum": [
                    "header",
                    "navigation",
                    "control",
                    "filters",
                    "primary",
                    "collection",
                    "detail",
                    "inspector",
                    "support",
                ],
            },
            "priority": {"type": "integer", "minimum": 0, "maximum": 100},
            "field_refs": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "maxLength": 128},
            },
        },
    }
    relation_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "kind", "source_widget_ids", "target_widget_ids", "strength"],
        "properties": {
            "id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,128}$"},
            "kind": {
                "type": "string",
                "enum": [
                    "co_visible",
                    "selection_drives",
                    "filters",
                    "details_for",
                    "opens_detail",
                    "compare_with",
                ],
            },
            "source_widget_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {"type": "string", "enum": ids},
            },
            "target_widget_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {"type": "string", "enum": ids},
            },
            "strength": {"type": "string", "enum": ["hard", "soft"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "message",
            "profile_ref",
            "goal_kind",
            "primary_widget_id",
            "views",
            "relations",
        ],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["proposed", "clarification", "unsupported"],
            },
            "message": {"type": "string"},
            "profile_ref": {"type": "string", "enum": [PROFILE_REF]},
            "goal_kind": {
                "type": "string",
                "enum": ["explore", "compare", "monitor", "operate"],
            },
            "primary_widget_id": {"type": ["string", "null"], "enum": ids + [None]},
            "views": {"type": "array", "maxItems": 40, "items": view_item},
            "relations": {"type": "array", "maxItems": 80, "items": relation_item},
        },
    }


def _intent_from_model(
    proposal: Mapping[str, Any],
    *,
    project_ref: str,
    prompt: str,
    model: str,
    request_id: str,
) -> dict[str, Any]:
    if proposal.get("status") != "proposed":
        raise BuilderUIStudioError(
            str(proposal.get("message") or "The model did not propose a UI intent")
        )
    rows = [dict(item) for item in proposal.get("views") or [] if isinstance(item, Mapping)]
    widget_ids = [str(item.get("widget_id") or "") for item in rows]
    if len(widget_ids) != len(set(widget_ids)):
        raise BuilderUIStudioError("The model assigned more than one role to a widget")
    primary_id = str(proposal.get("primary_widget_id") or "").strip()
    if not primary_id or primary_id not in widget_ids:
        raise BuilderUIStudioError("The model proposal has no declared primary widget")
    views = [
        {
            "ref": f"view:{item['widget_id']}",
            "widget_id": item["widget_id"],
            "role": item["role"],
            "priority": item["priority"],
            **({"field_refs": item["field_refs"]} if item.get("field_refs") else {}),
        }
        for item in rows
    ]
    relations = [
        {
            "id": item["id"],
            "kind": item["kind"],
            "source_refs": [f"view:{widget_id}" for widget_id in item["source_widget_ids"]],
            "target_refs": [f"view:{widget_id}" for widget_id in item["target_widget_ids"]],
            "strength": item["strength"],
        }
        for item in proposal.get("relations") or []
        if isinstance(item, Mapping)
    ]
    default_constraints = [
        ("data-contracts", "preserve_data_contracts", "hard", 10),
        ("truthful-data", "no_invented_data", "hard", 10),
        ("stable-selection", "stable_selection", "hard", 9),
        ("co-visible", "co_visible_primary_views", "hard", 9),
        ("compact", "compact_reachable", "hard", 8),
        ("viewport", "first_viewport_priority", "soft", 7),
        ("context", "bounded_context", "hard", 10),
    ]
    intent_id = "ui-intent:" + hashlib.sha256(
        _canonical([project_ref, prompt, model, proposal])
    ).hexdigest()[:24]
    return validate_ui_studio_intent(
        {
            "schema": INTENT_SCHEMA,
            "intent_id": intent_id,
            "project_ref": project_ref,
            "profile_ref": proposal["profile_ref"],
            "base_revision": None,
            "goal": {
                "kind": proposal["goal_kind"],
                "statement": prompt,
                "primary_ref": f"view:{primary_id}",
            },
            "views": views,
            "relations": relations,
            "constraints": [
                {
                    "id": constraint_id,
                    "rule": rule,
                    "strength": strength,
                    "weight": weight,
                    "refs": [],
                }
                for constraint_id, rule, strength, weight in default_constraints
            ],
            "responsive": {
                "wide_min_px": 1180,
                "compact_strategy": "progressive_disclosure",
            },
            "provenance": {
                "kind": "model",
                "source": "adaos.llm.ui_studio",
                "model": model,
                "request_id": request_id,
            },
        }
    )


@dataclass(slots=True)
class BuilderUIStudioService:
    """Create, evaluate, inspect, and stage isolated UI alternatives."""

    state_dir: Path | None = None
    dev_scenarios_root: Path | None = None
    dev_projects_root: Path | None = None
    submit: Callable[..., Mapping[str, Any]] | None = None
    wait: Callable[..., Mapping[str, Any]] | None = None

    @classmethod
    def from_context(cls) -> "BuilderUIStudioService":
        from .workflow import BuilderWorkflowService

        workflow = BuilderWorkflowService.from_context()
        return cls(
            state_dir=current_state_dir(),
            dev_scenarios_root=Path(workflow.dev_scenarios_root),
            dev_projects_root=Path(workflow.dev_projects_root),
        )

    @property
    def root(self) -> Path:
        return Path(self.state_dir or current_state_dir()) / "builder" / "ui-studio"

    def _project(self, project_ref: str) -> tuple[str, Path]:
        kind, separator, object_id = str(project_ref or "").strip().partition(":")
        if separator != ":" or kind not in {"project", "scenario"}:
            raise BuilderUIStudioError("project_ref must be project:<id> or scenario:<id>")
        object_id = _safe_token(object_id, field="project id")
        scenarios_root = Path(self.dev_scenarios_root or "")
        if kind == "scenario":
            scenario_id = object_id
        else:
            if self.dev_projects_root is None:
                raise BuilderUIStudioError("UI Studio project resolution is unavailable")
            descriptor = Path(self.dev_projects_root) / object_id / "project.yaml"
            try:
                project = yaml.safe_load(descriptor.read_text(encoding="utf-8-sig")) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise BuilderUIStudioError(f"cannot read project:{object_id}") from exc
            owned = (
                project.get("components", {}).get("owned", [])
                if isinstance(project.get("components"), Mapping)
                else []
            )
            primary = next(
                (
                    item
                    for item in owned
                    if isinstance(item, Mapping) and item.get("role") == "primary"
                ),
                owned[0] if owned else {},
            )
            component_kind, separator, scenario_id = str(primary.get("ref") or "").partition(":")
            if separator != ":" or component_kind != "scenario" or not scenario_id:
                raise BuilderUIStudioError(f"project:{object_id} has no primary scenario")
            scenario_id = _safe_token(scenario_id, field="scenario id")
        root = scenarios_root / scenario_id
        if not root.is_dir():
            raise BuilderUIStudioError(f"scenario source not found: {scenario_id}")
        return scenario_id, root

    def _base(self, project_ref: str) -> tuple[str, Path, dict[str, Any], str | None]:
        scenario_id, project_root = self._project(project_ref)
        try:
            webui = json.loads((project_root / "webui.json").read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuilderUIStudioError(f"cannot read WebUI for scenario:{scenario_id}") from exc
        if not isinstance(webui, Mapping):
            raise BuilderUIStudioError("WebUI root must be an object")
        current = project_root / "ui_revisions" / "current.txt"
        try:
            revision = current.read_text(encoding="utf-8-sig").strip() or None
        except OSError:
            revision = None
        return scenario_id, project_root, dict(webui), revision

    def _location(self, experiment_id: str) -> Path:
        token = _safe_token(experiment_id, field="experiment id")
        if not re.fullmatch(r"UIX-[A-F0-9]{12}", token):
            raise BuilderUIStudioError("invalid UI Studio experiment id")
        return self.root / "experiments" / token

    def canonical_project_ref(self, project_ref: str) -> str:
        """Use one experiment identity for project and primary-scenario aliases."""

        scenario_id, _project_root = self._project(project_ref)
        return f"scenario:{scenario_id}"

    def profiles(self) -> dict[str, Any]:
        return {
            "schema": "adaos.builder.ui_studio_profiles.v1",
            "profiles": ui_studio_profiles(),
        }

    def inventory(self, project_ref: str) -> dict[str, Any]:
        project_ref = self.canonical_project_ref(project_ref)
        scenario_id, _root, webui, revision = self._base(project_ref)
        return {
            "schema": "adaos.builder.ui_studio_inventory.v1",
            "project_ref": project_ref,
            "scenario_id": scenario_id,
            "base_revision": revision,
            "base_digest": _digest(webui),
            "inventory": compact_webui_inventory(webui),
        }

    def create_experiment(
        self,
        intent_value: Mapping[str, Any],
        *,
        option_ids: tuple[str, ...] = ("atlas", "registry"),
    ) -> dict[str, Any]:
        intent = validate_ui_studio_intent(intent_value)
        if intent["profile_ref"] != PROFILE_REF:
            raise BuilderUIStudioError(
                f"unsupported UI Studio profile: {intent['profile_ref']}"
            )
        project_ref = self.canonical_project_ref(str(intent["project_ref"]))
        if project_ref != intent["project_ref"]:
            intent = validate_ui_studio_intent({**intent, "project_ref": project_ref})
        _scenario_id, _project_root, base, revision = self._base(project_ref)
        if intent.get("base_revision") not in {None, revision}:
            raise BuilderUIStudioError("UI Studio intent is pinned to another base revision")
        index = _widget_index(_page(base))
        unknown = {
            str(item["widget_id"]) for item in intent["views"]
        } - set(index)
        if unknown:
            raise BuilderUIStudioError(
                "UI Studio intent references missing widgets: " + ", ".join(sorted(unknown))
            )
        experiment_id = "UIX-" + uuid4().hex[:12].upper()
        location = self._location(experiment_id)
        options: list[dict[str, Any]] = []
        with mutation_lock(location.parent / ".lock"):
            if location.exists():
                raise BuilderUIStudioError("UI Studio experiment identity collision")
            for option_id in option_ids:
                candidate, plan = _compile_geo_option(
                    base,
                    intent,
                    option_id=option_id,
                    experiment_id=experiment_id,
                )
                evaluation = _candidate_evaluation(base, candidate, intent, plan)
                candidate_ref = f"candidate:{experiment_id}.{option_id}"
                atomic_write_json(location / "candidates" / f"{option_id}.webui.json", candidate)
                options.append(
                    {
                        **plan,
                        "candidate_digest": _digest(candidate),
                        "candidate_ref": candidate_ref,
                        "evaluation": evaluation,
                    }
                )
            created_at = _now()
            selected = max(
                (item for item in options if item["evaluation"]["passed"]),
                key=lambda item: item["evaluation"]["score"],
                default=None,
            )
            experiment = {
                "schema": EXPERIMENT_SCHEMA,
                "experiment_id": experiment_id,
                "project_ref": project_ref,
                "status": "selected" if selected else "evaluated",
                "base_revision": revision,
                "base_digest": _digest(base),
                "intent": {**intent, "base_revision": revision},
                "intent_digest": _digest({**intent, "base_revision": revision}),
                "context_budget": _intent_context_budget(intent, base),
                "options": options,
                "selected_option_id": selected["option_id"] if selected else None,
                "created_at": created_at,
                "updated_at": created_at,
            }
            validate_ui_studio_intent(experiment["intent"])
            _validate(
                experiment,
                "builder.ui_studio_experiment.v1.schema.json",
                "UI Studio experiment",
            )
            atomic_write_json(location / "experiment.json", experiment)
            pointer = self.root / "by-project" / (
                hashlib.sha256(project_ref.encode("utf-8")).hexdigest() + ".json"
            )
            atomic_write_json(
                pointer,
                {
                    "schema": "adaos.builder.ui_studio_pointer.v1",
                    "project_ref": project_ref,
                    "experiment_id": experiment_id,
                    "updated_at": created_at,
                },
            )
        return copy.deepcopy(experiment)

    def inspect(self, experiment_id: str) -> dict[str, Any]:
        path = self._location(experiment_id) / "experiment.json"
        try:
            value = read_atomic_json(path)
        except FileNotFoundError as exc:
            raise BuilderUIStudioError(
                f"UI Studio experiment not found: {experiment_id}"
            ) from exc
        if not isinstance(value, Mapping):
            raise BuilderUIStudioError("UI Studio experiment record is corrupt")
        validate_ui_studio_intent(value.get("intent") or {})
        return _validate(
            value,
            "builder.ui_studio_experiment.v1.schema.json",
            "UI Studio experiment",
        )

    def latest(self, project_ref: str) -> dict[str, Any]:
        project_ref = self.canonical_project_ref(project_ref)
        pointer = self.root / "by-project" / (
            hashlib.sha256(str(project_ref).encode("utf-8")).hexdigest() + ".json"
        )
        try:
            value = read_atomic_json(pointer)
        except FileNotFoundError:
            return {
                "schema": "adaos.builder.ui_studio_state.v1",
                "project_ref": project_ref,
                "status": "not_started",
                "profiles": ui_studio_profiles(),
                "experiment": None,
            }
        if not isinstance(value, Mapping) or value.get("project_ref") != project_ref:
            raise BuilderUIStudioError("UI Studio project pointer is corrupt")
        return {
            "schema": "adaos.builder.ui_studio_state.v1",
            "project_ref": project_ref,
            "status": "ready",
            "profiles": ui_studio_profiles(),
            "experiment": self.inspect(str(value.get("experiment_id") or "")),
        }

    def candidate(self, experiment_id: str, option_id: str | None = None) -> dict[str, Any]:
        experiment = self.inspect(experiment_id)
        selected_id = str(option_id or experiment.get("selected_option_id") or "").strip()
        option = next(
            (item for item in experiment["options"] if item["option_id"] == selected_id),
            None,
        )
        if option is None:
            raise BuilderUIStudioError(f"unknown UI Studio option: {selected_id}")
        path = self._location(experiment_id) / "candidates" / f"{selected_id}.webui.json"
        value = read_atomic_json(path)
        if not isinstance(value, Mapping) or _digest(value) != option["candidate_digest"]:
            raise BuilderUIStudioError("UI Studio candidate digest mismatch")
        return copy.deepcopy(dict(value))

    def select(self, experiment_id: str, option_id: str) -> dict[str, Any]:
        location = self._location(experiment_id)
        with mutation_lock(location / ".lock"):
            experiment = self.inspect(experiment_id)
            option = next(
                (item for item in experiment["options"] if item["option_id"] == option_id),
                None,
            )
            if option is None:
                raise BuilderUIStudioError(f"unknown UI Studio option: {option_id}")
            if not option["evaluation"]["passed"]:
                raise BuilderUIStudioError("a failed UI Studio option cannot be selected")
            experiment["selected_option_id"] = option_id
            experiment["status"] = "selected"
            experiment.pop("staged_revision", None)
            experiment["updated_at"] = _now()
            _validate(
                experiment,
                "builder.ui_studio_experiment.v1.schema.json",
                "UI Studio experiment",
            )
            atomic_write_json(location / "experiment.json", experiment)
            return experiment

    def stage(self, experiment_id: str, option_id: str | None = None) -> dict[str, Any]:
        """Write an EXP revision without changing the accepted revision pointer."""

        location = self._location(experiment_id)
        with mutation_lock(location / ".lock"):
            experiment = self.inspect(experiment_id)
            selected_id = str(option_id or experiment.get("selected_option_id") or "").strip()
            option = next(
                (item for item in experiment["options"] if item["option_id"] == selected_id),
                None,
            )
            if option is None or not option["evaluation"]["passed"]:
                raise BuilderUIStudioError("only a passed UI Studio option can be staged")
            scenario_id, project_root, base, current_revision = self._base(
                str(experiment["project_ref"])
            )
            if _digest(base) != experiment["base_digest"] or current_revision != experiment["base_revision"]:
                raise BuilderUIStudioError("UI Studio base changed; create a new experiment")
            candidate = self.candidate(experiment_id, selected_id)
            revision = f"EXP-{experiment_id.removeprefix('UIX-')}-{selected_id.upper()}"
            revision_path = project_root / "ui_revisions" / f"{revision}.json"
            record = {
                "schema": "adaos.builder.ui_revision.v1",
                "revision": revision,
                "created_at": _now(),
                "scenario_id": scenario_id,
                "purpose": "ui_studio_experiment",
                "request": {
                    "text": experiment["intent"]["goal"]["statement"],
                    "intent_ref": experiment["intent"]["intent_id"],
                },
                "patch": {
                    "id": f"ui-studio-{experiment_id}-{selected_id}",
                    "target": "ui.application.desktop.pageSchema",
                    "operation": "compile_typed_ui_intent",
                    "profile_ref": experiment["intent"]["profile_ref"],
                    "candidate_digest": option["candidate_digest"],
                    "evaluation_score": option["evaluation"]["score"],
                    "status": "applied",
                },
                "before_webui": base,
                "after_webui": candidate,
                "preview_state": {},
            }
            atomic_write_json(revision_path, record)
            experiment["selected_option_id"] = selected_id
            experiment["status"] = "staged"
            experiment["staged_revision"] = revision
            experiment["updated_at"] = _now()
            _validate(
                experiment,
                "builder.ui_studio_experiment.v1.schema.json",
                "UI Studio experiment",
            )
            atomic_write_json(location / "experiment.json", experiment)
        return {
            "ok": True,
            "experiment": experiment,
            "scenario_id": scenario_id,
            "revision": revision,
            "revision_path": str(revision_path),
            "current_revision": current_revision,
            "current_pointer_unchanged": True,
        }

    def read_staged_revision(
        self,
        scenario_id: str,
        revision: str,
    ) -> dict[str, Any]:
        """Read one admitted EXP snapshot through its experiment authority.

        An EXP file is mutable DEV storage, so its filename alone is never an
        authority.  Preview may consume it only while the owning experiment
        still selects that passed option and every stored digest agrees with
        the compiler-owned candidate.
        """

        scenario_token = _safe_token(scenario_id, field="scenario id")
        revision_token = str(revision or "").strip()
        match = re.fullmatch(
            r"EXP-([A-F0-9]{12})-(ATLAS|REGISTRY)",
            revision_token,
        )
        if match is None:
            raise BuilderUIStudioError("invalid UI Studio revision id")
        experiment_id = f"UIX-{match.group(1)}"
        option_id = match.group(2).lower()
        experiment = self.inspect(experiment_id)
        if (
            experiment.get("status") != "staged"
            or experiment.get("staged_revision") != revision_token
            or experiment.get("selected_option_id") != option_id
        ):
            raise BuilderUIStudioError("UI Studio revision is not the active staged option")
        option = next(
            (item for item in experiment["options"] if item["option_id"] == option_id),
            None,
        )
        if option is None or not option["evaluation"]["passed"]:
            raise BuilderUIStudioError("UI Studio revision did not pass evaluation")
        resolved_scenario, project_root, base, current_revision = self._base(
            str(experiment["project_ref"])
        )
        if resolved_scenario != scenario_token:
            raise BuilderUIStudioError("UI Studio revision belongs to another scenario")
        if (
            current_revision != experiment["base_revision"]
            or _digest(base) != experiment["base_digest"]
        ):
            raise BuilderUIStudioError("UI Studio base changed; create a new experiment")
        candidate = self.candidate(experiment_id, option_id)
        revision_path = project_root / "ui_revisions" / f"{revision_token}.json"
        try:
            value = read_atomic_json(revision_path)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise BuilderUIStudioError("UI Studio revision file is unavailable") from exc
        if not isinstance(value, Mapping):
            raise BuilderUIStudioError("UI Studio revision record is corrupt")
        patch = value.get("patch") if isinstance(value.get("patch"), Mapping) else {}
        if (
            value.get("schema") != "adaos.builder.ui_revision.v1"
            or value.get("purpose") != "ui_studio_experiment"
            or value.get("revision") != revision_token
            or value.get("scenario_id") != scenario_token
            or patch.get("operation") != "compile_typed_ui_intent"
            or patch.get("candidate_digest") != option["candidate_digest"]
            or _digest(value.get("before_webui")) != experiment["base_digest"]
            or _digest(value.get("after_webui")) != option["candidate_digest"]
            or _digest(candidate) != option["candidate_digest"]
        ):
            raise BuilderUIStudioError("UI Studio staged revision authority mismatch")
        return copy.deepcopy(dict(value))

    def propose(
        self,
        *,
        project_ref: str,
        prompt: str,
        model: str,
        reasoning_effort: str = "medium",
        max_tokens: int = 8192,
        timeout_s: float = 300,
    ) -> dict[str, Any]:
        """Use the configured AdaOS LLM, not Codex, to produce a compact intent."""

        project_ref = self.canonical_project_ref(project_ref)
        prompt = str(prompt or "").strip()
        model = str(model or "").strip()
        if not prompt or len(prompt) > 8000:
            raise BuilderUIStudioError("UI Studio prompt must contain 1..8000 characters")
        if not model:
            raise BuilderUIStudioError("UI Studio requires an explicit ordinary LLM model")
        if isinstance(max_tokens, bool) or not 1024 <= int(max_tokens) <= 16384:
            raise BuilderUIStudioError("UI Studio max_tokens must be 1024..16384")
        _scenario_id, _root, webui, _revision = self._base(project_ref)
        inventory = compact_webui_inventory(webui)
        widget_ids = [str(item["id"]) for item in inventory["widgets"]]
        contract = ui_studio_model_contract(widget_ids)
        rules = (
            "Classify an existing AdaOS declarative interface for one bounded UI profile. "
            "Return roles and semantic relations only. Do not emit HTML, CSS, Angular, "
            "WebUI JSON, invented data, new widget ids, or implementation code. A primary "
            "view is the dominant analytical canvas; collection is a co-visible registry; "
            "detail is an existing selection-dependent view that Core may project into an "
            "inspector. Preserve the existing data and navigation contracts. If the inventory "
            "cannot satisfy geo_explorer.v1, return unsupported."
        )
        user_input = {
            "request": prompt,
            "selected_profile": ui_studio_profiles()[0],
            "inventory": inventory,
        }
        from .model_controls import reasoning_for_model

        envelope = {
            "messages": [
                {"role": "system", "content": rules},
                {
                    "role": "user",
                    "content": json.dumps(user_input, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "model": model,
            "reasoning": reasoning_for_model(model, reasoning_effort),
            "max_tokens": int(max_tokens),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "builder_ui_studio_intent",
                    "strict": True,
                    "schema": contract,
                }
            },
            "tools": [],
            "tool_choice": "none",
            "profile_scope": "development",
        }
        input_bytes = len(_canonical(envelope))
        if input_bytes > CONTEXT_LIMIT_BYTES:
            raise BuilderUIStudioError("UI Studio model request exceeds its context budget")
        request_id = "builder-ui-studio:" + hashlib.sha256(
            _canonical([project_ref, prompt, model, _digest(webui)])
        ).hexdigest()[:32]
        model_root = self.root / "model-requests" / request_id.replace(":", "-")
        model_path = model_root / "request.json"
        from adaos.sdk.llm import llm_client

        submit = self.submit or llm_client.submit_response_job
        wait = self.wait or llm_client.wait_response_job
        with mutation_lock(model_root / ".lock"):
            retained = read_atomic_json(model_path) if model_path.is_file() else {}
            if retained and retained.get("request_digest") != _digest(envelope):
                raise BuilderUIStudioError("retained UI Studio request context changed")
            if not retained:
                retained = {
                    "schema": "adaos.builder.ui_studio_model_request.v1",
                    "request_id": request_id,
                    "request_digest": _digest(envelope),
                    "request": envelope,
                    "status": "prepared",
                }
                atomic_write_json(model_path, retained)
            if retained.get("experiment_id"):
                return {
                    "ok": True,
                    "model_calls": 0,
                    "resumed": True,
                    "request_id": request_id,
                    "experiment": self.inspect(str(retained["experiment_id"])),
                }
            if not retained.get("job_id"):
                submitted = submit(
                    **envelope,
                    request_id=request_id,
                    stream=False,
                    provider_max_attempts=1,
                    timeout=30,
                )
                job_id = submitted.get("job_id") or (submitted.get("job") or {}).get("id")
                if not job_id:
                    raise BuilderUIStudioError("LLM returned no durable UI Studio job id")
                retained.update(
                    job_id=job_id,
                    base_url=(submitted.get("_client") or {}).get("base_url"),
                    status="submitted",
                )
                atomic_write_json(model_path, retained)
            job = wait(
                retained["job_id"],
                base_url=retained.get("base_url"),
                timeout_s=timeout_s,
                poll_interval_s=0.5,
            )
            output = job.get("output_text")
            if not output or str(job.get("status") or "") not in {"succeeded", "completed"}:
                retained.update(status="failed", job=dict(job))
                atomic_write_json(model_path, retained)
                raise BuilderUIStudioError("UI Studio LLM job did not complete successfully")
            try:
                proposal = json.loads(str(output))
                Draft202012Validator(contract).validate(proposal)
                intent = _intent_from_model(
                    proposal,
                    project_ref=project_ref,
                    prompt=prompt,
                    model=model,
                    request_id=request_id,
                )
                experiment = self.create_experiment(intent)
            except (json.JSONDecodeError, ValidationError, BuilderWorkflowError) as exc:
                retained.update(status="invalid", job=dict(job))
                atomic_write_json(model_path, retained)
                raise BuilderUIStudioError("LLM returned no valid UI Studio intent") from exc
            retained.update(
                status="completed",
                experiment_id=experiment["experiment_id"],
                proposal=proposal,
                job=dict(job),
            )
            atomic_write_json(model_path, retained)
        return {
            "ok": True,
            "model_calls": 1,
            "resumed": False,
            "request_id": request_id,
            "input_bytes": input_bytes,
            "context_limit_bytes": CONTEXT_LIMIT_BYTES,
            "experiment": experiment,
        }


__all__ = [
    "BuilderUIStudioError",
    "BuilderUIStudioService",
    "CONTEXT_LIMIT_BYTES",
    "EXPERIMENT_SCHEMA",
    "INTENT_SCHEMA",
    "PROFILE_REF",
    "compact_webui_inventory",
    "ui_studio_model_contract",
    "ui_studio_profiles",
    "small_cities_benchmark_intent",
    "validate_ui_studio_intent",
]
