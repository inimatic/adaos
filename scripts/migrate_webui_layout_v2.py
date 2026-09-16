"""Migrate authoritative WebUI page schemas to Layout & Interaction ABI v2."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


COLLECTION_WIDGETS = {
    "collection.board",
    "collection.grid",
    "collection.tree",
    "navigation.outline",
    "ui.list",
    "ui.table",
    "visual.taigaCollectionGrid",
    "visual.taigaTree",
}
DETAIL_WIDGETS = {"item.details", "item.documentViewer", "item.codeViewer"}
FORM_WIDGETS = {"ui.form", "input.text", "input.selector", "input.toggle"}
EXCLUDED_PATH_PARTS = {
    ".git",
    ".runtime",
    "history",
    "recovery",
    "snapshots",
    "state",
    "ui_revisions",
}


def _widgets_by_area(page: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = {}
    for widget in page.get("widgets") or []:
        if not isinstance(widget, Mapping):
            continue
        result.setdefault(str(widget.get("area") or ""), []).append(widget)
    return result


def _widget_types(widgets: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(widget.get("type") or "") for widget in widgets}


def _pattern(page: Mapping[str, Any], layout: Mapping[str, Any]) -> str:
    area_ids = {
        str(area.get("id") or "")
        for area in layout.get("areas") or []
        if isinstance(area, Mapping)
    }
    types = _widget_types(
        widget
        for area_id, rows in _widgets_by_area(page).items()
        if area_id in area_ids
        for widget in rows
    )
    legacy = str(layout.get("pattern") or layout.get("type") or "").strip()
    has_collection = bool(types & COLLECTION_WIDGETS)
    has_detail = bool(types & DETAIL_WIDGETS)
    if "collection.board" in types:
        return "board"
    if legacy in {"dashboard", "grid", "desktop-zones", "custom"}:
        return "dashboard"
    if has_collection and has_detail:
        return "collection-detail"
    if legacy in {"focus-detail"}:
        return "collection-detail" if has_collection else "master-detail"
    if legacy in {"outline-detail", "sidebar-content"}:
        return "master-detail"
    if legacy in {"split", "responsive"}:
        return "workbench"
    if legacy == "tabs":
        return "workbench"
    if has_collection:
        return "collection"
    if types & FORM_WIDGETS:
        return "task-flow"
    return "document"


def _legacy_role(area: Mapping[str, Any]) -> str:
    value = str(area.get("role") or area.get("id") or "").strip().lower()
    if value in {"sidebar", "nav", "navigation"}:
        return "navigation"
    if value in {"footer", "bottom", "commands"}:
        return "commands"
    if value in {"toolbar", "tools"}:
        return "toolbar"
    if value in {
        "aux",
        "right",
        "aside",
        "detail",
        "details",
        "secondary",
        "secondary-panel",
        "detail-panel",
        "details-panel",
    }:
        return "detail"
    if value in {"status"}:
        return "status"
    if value in {"utility"}:
        return "utility"
    return "main"


def _region_role(
    *,
    area: Mapping[str, Any],
    widgets: list[Mapping[str, Any]],
    pattern: str,
    primary_assigned: bool,
) -> str:
    legacy = _legacy_role(area)
    types = _widget_types(widgets)
    if legacy == "detail" and not types & DETAIL_WIDGETS:
        return "inspector"
    if legacy != "main":
        return legacy
    if primary_assigned:
        return "utility"
    if pattern in {"collection", "collection-detail"}:
        return "collection"
    return "main"


def _presentation(role: str) -> dict[str, str]:
    compact = {
        "navigation": "drawer",
        "detail": "sheet",
        "inspector": "sheet",
        "toolbar": "stack",
        "collection": "stack",
        "main": "stack",
        "utility": "stack",
        "status": "stack",
        "commands": "pane",
    }[role]
    return {"wide": "pane", "compact": compact}


def _size(role: str, preferred: Any) -> dict[str, Any] | None:
    value = int(preferred) if isinstance(preferred, (int, float)) else None
    if role == "navigation":
        value = min(480, max(200, value or 280))
        return {"minPx": 200, "preferredPx": value, "maxPx": 480}
    if role in {"detail", "inspector"}:
        value = min(600, max(220, value or 360))
        return {"minPx": 220, "preferredPx": value, "maxPx": 600}
    return None


def _density(page: Mapping[str, Any]) -> str:
    profiles = (
        page.get("presentation", {}).get("profiles", {})
        if isinstance(page.get("presentation"), Mapping)
        else {}
    )
    desktop = profiles.get("desktop") if isinstance(profiles, Mapping) else {}
    value = str(desktop.get("density") or "") if isinstance(desktop, Mapping) else ""
    if value in {"compact", "comfortable", "spacious"}:
        return value
    if value == "ten_foot":
        return "spacious"
    return "comfortable"


def _interaction(page: Mapping[str, Any], roles: list[str]) -> dict[str, str]:
    widgets = [
        widget
        for widget in page.get("widgets") or []
        if isinstance(widget, Mapping)
    ]
    has_selection = any(
        any(str(action.get("on") or "") == "select" for action in widget.get("actions") or [] if isinstance(action, Mapping))
        for widget in widgets
    )
    has_filters = any(str(widget.get("type") or "") == "ui.queryToolbar" for widget in widgets)
    result = {
        "selection": "single" if has_selection else "none",
        "rowActivation": "select" if has_selection else "none",
        "filters": "disclosure" if has_filters else "inline",
        "actions": "adaptive",
    }
    if "detail" in roles or "inspector" in roles:
        result["detail"] = "inline"
        if has_selection:
            result["rowActivation"] = "open-detail"
    return result


def _migrate_layout(
    page: Mapping[str, Any],
    layout: Mapping[str, Any],
    *,
    include_variants: bool,
) -> dict[str, Any]:
    if layout.get("version") == 2:
        result = dict(layout)
        roles = {
            str(region.get("role") or "")
            for region in result.get("regions") or []
            if isinstance(region, Mapping)
        }
        if result.get("pattern") == "collection-detail" and not {
            "collection",
            "detail",
        }.issubset(roles):
            result["pattern"] = (
                "collection"
                if "collection" in roles
                else "master-detail"
                if "navigation" in roles
                else "workbench"
                if len(roles) > 1
                else "document"
            )
        if include_variants and isinstance(result.get("variants"), list):
            variants = []
            for variant in result["variants"]:
                if not isinstance(variant, Mapping):
                    continue
                candidate = {"version": 2, **dict(variant)}
                repaired = _migrate_layout(page, candidate, include_variants=False)
                repaired.pop("version", None)
                variants.append(repaired)
            result["variants"] = variants
        return result
    areas = layout.get("areas")
    if not isinstance(areas, list) or not areas:
        raise ValueError(f"page {page.get('id')!r} has no legacy layout areas")
    pattern = _pattern(page, layout)
    by_area = _widgets_by_area(page)
    regions: list[dict[str, Any]] = []
    primary_assigned = False
    for area in areas:
        if not isinstance(area, Mapping):
            raise ValueError(f"page {page.get('id')!r} has a non-object layout area")
        area_id = str(area.get("id") or "").strip()
        role = _region_role(
            area=area,
            widgets=by_area.get(area_id, []),
            pattern=pattern,
            primary_assigned=primary_assigned,
        )
        if role in {"main", "collection"}:
            primary_assigned = True
        preferred = area.get("width")
        if preferred is None and role == "navigation":
            preferred = layout.get("sidebarWidth")
        if preferred is None and role in {"detail", "inspector"}:
            preferred = layout.get("auxWidth")
        region: dict[str, Any] = {
            "id": area_id,
            "role": role,
            "priority": {
                "main": 100,
                "collection": 100,
                "toolbar": 90,
                "navigation": 80,
                "detail": 70,
                "inspector": 60,
                "commands": 90,
                "status": 50,
                "utility": 40,
            }[role],
            "scroll": "region" if role in {"navigation", "detail", "inspector"} else "page",
            "presentation": _presentation(role),
        }
        if area.get("label") is not None:
            region["label"] = area["label"]
        size = _size(role, preferred)
        if size:
            region["size"] = size
        regions.append(region)
    if not primary_assigned:
        regions[0]["role"] = "collection" if pattern in {"collection", "collection-detail"} else "main"
        regions[0]["priority"] = 100
        regions[0]["scroll"] = "page"
        regions[0]["presentation"] = _presentation(regions[0]["role"])
    result: dict[str, Any] = {
        "version": 2,
        "pattern": pattern,
        "density": _density(page),
        "contentWidth": "fluid",
        "scroll": "regions" if pattern in {"collection-detail", "master-detail", "workbench"} else "page",
        "regions": regions,
        "interaction": _interaction(page, [str(item["role"]) for item in regions]),
    }
    presentation = page.get("presentation")
    if isinstance(presentation, Mapping):
        profiles = presentation.get("profiles")
        desktop = profiles.get("desktop") if isinstance(profiles, Mapping) else None
        maximum = desktop.get("maxContentWidthPx") if isinstance(desktop, Mapping) else None
        if isinstance(maximum, (int, float)):
            result["contentWidth"] = "bounded"
            result["maxContentWidthPx"] = min(2400, max(480, int(maximum)))
    if include_variants and isinstance(layout.get("variants"), list):
        variants = []
        for variant in layout["variants"]:
            if not isinstance(variant, Mapping):
                continue
            migrated = _migrate_layout(page, variant, include_variants=False)
            migrated.pop("version", None)
            migrated["id"] = str(variant.get("id") or "")
            if variant.get("when") is not None:
                migrated["when"] = variant["when"]
            if variant.get("default") is True:
                migrated["default"] = True
            variants.append(migrated)
        if variants:
            result["variants"] = variants
    return result


def migrate_document(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        layout = value.get("layout")
        widgets = value.get("widgets")
        if isinstance(layout, Mapping) and isinstance(widgets, list) and value.get("id"):
            migrated = _migrate_layout(value, layout, include_variants=True)
            if migrated != layout:
                value["layout"] = migrated
                changed += 1
        for child in value.values():
            changed += migrate_document(child)
    elif isinstance(value, list):
        for child in value:
            changed += migrate_document(child)
    return changed


def migrate_path(path: Path, *, check: bool) -> tuple[int, bool]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = migrate_document(value)
    if changed and not check:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return changed, bool(changed)


def _paths(inputs: list[str]) -> list[Path]:
    result: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            result.extend(
                item
                for item in sorted(path.rglob("*webui.json"))
                if item.name != "semantic.webui.json"
                and not EXCLUDED_PATH_PARTS.intersection(item.parts)
            )
        elif (
            path.name == "webui.json" or path.name.endswith(".webui.json")
        ) and path.name != "semantic.webui.json":
            result.append(path)
    return list(dict.fromkeys(item.resolve() for item in result))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed_files = 0
    changed_pages = 0
    for path in _paths(args.paths):
        pages, changed = migrate_path(path, check=args.check)
        changed_pages += pages
        changed_files += int(changed)
    print(
        json.dumps(
            {
                "files": len(_paths(args.paths)),
                "changed_files": changed_files,
                "changed_pages": changed_pages,
                "check": bool(args.check),
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.check and changed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
