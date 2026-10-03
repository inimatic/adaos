"""Compile allowlisted semantic capability surfaces into governed WebUI widgets."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .workflow import BuilderWorkflowError


def compile_capability_surfaces(
    document: Mapping[str, Any],
    webui: dict[str, Any],
    source_map: dict[str, list[str]],
    dictionaries: dict[str, dict[str, str]],
    *,
    localize: Any,
) -> None:
    page = webui["ui"]["application"]["desktop"]["pageSchema"]
    widgets = page["widgets"]
    widget_ids = {str(widget.get("id") or "") for widget in widgets}

    for surface in document.get("capability_surfaces") or []:
        surface_id = str(surface["id"])
        if surface_id in widget_ids:
            raise BuilderWorkflowError(
                f"Capability surface id collides with compiled widget {surface_id!r}"
            )
        capability_ref = str(surface["capability_ref"])
        if capability_ref != "visual.sitePreview":
            raise BuilderWorkflowError(
                f"Unsupported capability surface {capability_ref!r}"
            )
        title, title_i18n = localize(surface["title"], dictionaries)
        widget = {
            "id": surface_id,
            "type": "visual.sitePreview",
            "area": str(surface["region_role"]),
            "title": title,
            "title_i18n": title_i18n,
            "inputs": {
                "siteId": str(surface["site_id"]),
                "route": str(surface["route"]),
                "minHeight": int(surface["min_height_px"]),
            },
        }
        widgets.append(widget)
        widget_ids.add(surface_id)
        source_map[f"surface:{surface_id}"] = [
            f"ui.application.desktop.pageSchema.widgets.@{surface_id}"
        ]

    if document.get("capability_surfaces"):
        page["meta"]["builder"]["capability_surfaces"] = copy.deepcopy(
            document["capability_surfaces"]
        )


__all__ = ["compile_capability_surfaces"]
