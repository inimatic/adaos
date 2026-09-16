from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


LAYOUT_VERSION = 2
LAYOUT_PATTERNS = frozenset(
    {
        "document",
        "collection",
        "collection-detail",
        "master-detail",
        "dashboard",
        "board",
        "task-flow",
        "settings",
        "workbench",
    }
)
LAYOUT_REGION_ROLES = frozenset(
    {
        "navigation",
        "toolbar",
        "collection",
        "main",
        "detail",
        "inspector",
        "utility",
        "status",
        "commands",
    }
)
LAYOUT_PRESENTATIONS = frozenset(
    {"pane", "stack", "drawer", "sheet", "route", "overflow", "hidden"}
)


def layout_v2_findings(
    page: Mapping[str, Any], *, schema_path: str
) -> list[dict[str, Any]]:
    layout = page.get("layout") if isinstance(page.get("layout"), Mapping) else {}
    findings: list[dict[str, Any]] = []

    def report(code: str, path: str, message: str) -> None:
        findings.append(
            {
                "code": code,
                "severity": "error",
                "path": f"{schema_path}.layout{path}",
                "message": message,
            }
        )

    if layout.get("version") != LAYOUT_VERSION:
        report(
            "ui.layout.version_unsupported",
            ".version",
            f"Layout version must be {LAYOUT_VERSION!r}",
        )
    pattern = str(layout.get("pattern") or "").strip()
    if pattern not in LAYOUT_PATTERNS:
        report(
            "ui.layout.pattern_unsupported",
            ".pattern",
            f"Unsupported layout pattern {pattern!r}",
        )

    regions = layout.get("regions")
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes)):
        report("ui.layout.regions_missing", ".regions", "Layout regions are required")
        return findings

    ids: set[str] = set()
    roles: list[str] = []
    for index, value in enumerate(regions):
        if not isinstance(value, Mapping):
            report(
                "ui.layout.region_invalid",
                f".regions[{index}]",
                "Layout region must be an object",
            )
            continue
        region_id = str(value.get("id") or "").strip()
        role = str(value.get("role") or "").strip()
        if not region_id:
            report(
                "ui.layout.region_id_missing",
                f".regions[{index}].id",
                "Layout region id is required",
            )
        elif region_id in ids:
            report(
                "ui.layout.region_id_duplicate",
                f".regions[{index}].id",
                f"Layout region id {region_id!r} is duplicated",
            )
        else:
            ids.add(region_id)
        if role not in LAYOUT_REGION_ROLES:
            report(
                "ui.layout.region_role_unsupported",
                f".regions[{index}].role",
                f"Unsupported layout region role {role!r}",
            )
        else:
            roles.append(role)
        presentation = value.get("presentation")
        if not isinstance(presentation, Mapping):
            report(
                "ui.layout.region_presentation_missing",
                f".regions[{index}].presentation",
                "Wide and compact region presentation are required",
            )
            continue
        for viewport in ("wide", "compact"):
            mode = str(presentation.get(viewport) or "").strip()
            if mode not in LAYOUT_PRESENTATIONS:
                report(
                    "ui.layout.region_presentation_unsupported",
                    f".regions[{index}].presentation.{viewport}",
                    f"Unsupported {viewport} presentation {mode!r}",
                )
        if presentation.get("compact") == "hidden" and value.get("optional") is not True:
            report(
                "ui.layout.required_region_hidden",
                f".regions[{index}].presentation.compact",
                "Only an optional region may be hidden on compact viewports",
            )
        size = value.get("size")
        if isinstance(size, Mapping):
            minimum = size.get("minPx")
            preferred = size.get("preferredPx")
            maximum = size.get("maxPx")
            ordered = [item for item in (minimum, preferred, maximum) if isinstance(item, (int, float))]
            if ordered != sorted(ordered):
                report(
                    "ui.layout.region_size_order_invalid",
                    f".regions[{index}].size",
                    "Region size must satisfy minPx <= preferredPx <= maxPx",
                )

    if len([role for role in roles if role == "navigation"]) > 1:
        report(
            "ui.layout.navigation_ambiguous",
            ".regions",
            "At most one navigation region is allowed",
        )
    if len([role for role in roles if role in {"main", "collection"}]) != 1:
        report(
            "ui.layout.primary_region_invalid",
            ".regions",
            "Exactly one main or collection region is required",
        )
    if pattern == "collection" and "collection" not in roles:
        report(
            "ui.layout.collection_region_missing",
            ".regions",
            "Collection pattern requires a collection region",
        )
    if pattern == "collection-detail" and not {"collection", "detail"}.issubset(roles):
        report(
            "ui.layout.collection_detail_regions_missing",
            ".regions",
            "Collection-detail pattern requires collection and detail regions",
        )

    reachable_ids = set(ids)
    for variant in layout.get("variants") or []:
        if not isinstance(variant, Mapping):
            continue
        reachable_ids.update(
            str(region.get("id") or "").strip()
            for region in variant.get("regions") or []
            if isinstance(region, Mapping)
        )
    widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
    for index, widget in enumerate(widgets):
        if not isinstance(widget, Mapping):
            continue
        area = str(widget.get("area") or "").strip()
        if area not in reachable_ids:
            findings.append(
                {
                    "code": "ui.layout.widget_region_missing",
                    "severity": "error",
                    "path": f"{schema_path}.widgets[{index}].area",
                    "message": f"Widget targets unknown layout region {area!r}",
                }
            )
    return findings
