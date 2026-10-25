"""Explicit authoring migration matching the Client's durable-layout upcast.

This never rewrites an installed release. Callers validate and package the
returned document as a new revision; widget identity and actions stay intact.
"""
from copy import deepcopy
import math
from typing import Any


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def _pattern(value):
    return {"split": "workbench", "grid": "dashboard"}.get(value, value if value in {
        "board", "collection", "collection-detail", "master-detail", "dashboard",
        "task-flow", "settings", "workbench"} else "document")


def _layout(raw):
    pattern = _pattern(raw.get("pattern") or raw.get("type"))
    regions = []
    for index, area in enumerate(raw.get("areas", raw.get("regions", []))):
        if not isinstance(area, dict) or not str(area.get("id") or "").strip():
            continue
        role = {"aux": "inspector", "right": "inspector", "sidebar": "navigation",
                "footer": "commands"}.get(area.get("role"), area.get("role"))
        if role not in {"navigation", "toolbar", "collection", "main", "detail", "inspector", "utility", "status", "commands"}:
            role = "main"
        region = {"id": area["id"], "role": role, "optional": area.get("optional") is True,
            "priority": area.get("priority", 100 if role in {"main", "collection"} else max(10, 70-index)),
            "scroll": "none" if area.get("scroll") == "none" else "region" if area.get("scroll") == "region"
                or role in {"navigation", "detail", "inspector"} else "page",
            "presentation": {"wide": "pane", "compact": "drawer" if role == "navigation" else
                "sheet" if role in {"detail", "inspector"} else "stack"}}
        if isinstance(area.get("label"), str):
            region["label"] = area["label"]
        width = _number(area.get("width", raw.get("auxWidth") if role == "inspector" else None))
        if width:
            region["size"] = {"preferredPx": width}
        regions.append(region)
    result = {"version": 2, "pattern": pattern,
        "density": raw.get("density") if raw.get("density") in {"compact", "spacious"} else "comfortable",
        "contentWidth": raw.get("contentWidth") if raw.get("contentWidth") in {"bounded", "reading"} else "fluid",
        "scroll": "regions" if raw.get("scroll") == "regions" or pattern == "workbench" else "page",
        "regions": regions}
    if _number(raw.get("maxContentWidthPx")):
        result["maxContentWidthPx"] = _number(raw["maxContentWidthPx"])
    if isinstance(raw.get("interaction"), dict):
        result["interaction"] = deepcopy(raw["interaction"])
    variants = []
    for variant in raw.get("variants", []):
        if not isinstance(variant, dict) or not variant.get("id"):
            continue
        areas = variant.get("regions") if isinstance(variant.get("regions"), list) else variant.get("areas", [])
        if not areas:
            continue
        migrated = _layout({"areas": areas, "type": variant.get("pattern") or variant.get("type"),
            "density": variant.get("density") or raw.get("density"),
            "contentWidth": variant.get("contentWidth") or raw.get("contentWidth"),
            "maxContentWidthPx": variant.get("maxContentWidthPx", raw.get("maxContentWidthPx")),
            "interaction": variant.get("interaction", raw.get("interaction"))})
        migrated["scroll"] = "regions" if variant.get("scroll") == "regions" or _pattern(variant.get("type")) == "workbench" else "page"
        migrated.pop("version", None)
        migrated["id"] = variant["id"]
        for key in ("when", "default"):
            if key in variant:
                migrated[key] = variant[key]
        variants.append(migrated)
    if variants:
        result["variants"] = variants
    return result


def upgrade_legacy_webui(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a detached revision and exact migrated paths; no runtime effects."""
    result = deepcopy(document)
    changed = []
    def walk(node, path):
        if isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}")
        elif isinstance(node, dict):
            layout = node.get("layout")
            if isinstance(layout, dict) and isinstance(layout.get("areas"), list) and not layout.get("regions"):
                node["layout"] = _layout(layout)
                changed.append(path+"/layout")
            interaction = node.get("interaction")
            if isinstance(interaction, dict) and interaction.get("detail") == "inspector":
                interaction["detail"] = "drawer"
                changed.append(path+"/interaction/detail")
            for key, value in node.items():
                # Semantic authoring metadata is not a renderer declaration.
                # In particular semantic.layout retains its own areas/type ABI.
                if key in {"semantic", "initialState"}:
                    continue
                walk(value, path+"/"+key)
    walk(result, "")
    return result, changed
