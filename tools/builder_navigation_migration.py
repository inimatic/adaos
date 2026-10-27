"""Idempotent Builder-owned upgrade of the application address/read contracts.

This operates on a source checkpoint, never an installed immutable Trial.
Release preparation and activation remain ordinary governed lifecycle actions.
"""
from __future__ import annotations

from copy import deepcopy


def upgrade_webui(document: dict) -> dict:
    result = deepcopy(document)
    app = result["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    page["initialStateSource"].update(required=True,
        identityKeys=["selectedProjectKind", "selectedProjectId"])
    bindings = page.setdefault("navigationState", {}).setdefault("bindings", [])
    for key, query in (("selectedProjectKind", "builder_object_type"),
                       ("selectedProjectId", "builder_object_id")):
        existing = next((binding for binding in bindings if binding.get("stateKey") == key), None)
        if existing and existing.get("queryKey") != query:
            raise ValueError(f"Conflicting Builder entity address: {key}")
        if not existing:
            bindings.append({"stateKey": key, "queryKey": query, "history": "push", "hydrate": True})
    picker = next(widget for widget in app["modals"]["project-picker"]["schema"]["widgets"]
        if widget["id"] == "project-picker-table")
    picker["dataSource"]["readPriority"] = "interactive"
    return result


def upgrade_catalog_source(source: str) -> str:
    old = '''    candidates = [item.get("created_at"), state.get("updated_at")]
    for ref in item.get("component_refs") or []:
        identity = _split_component_ref(str(ref))
        if identity:
            candidates.append(_catalog_state(*identity).get("updated_at"))'''
    new = '''    # The governed catalog already projects metadata. Resolving each member
    # here re-enters authoring/access admission per component and can outlive
    # the read timeout, accumulating duplicate workers on retries.
    candidates = [item.get("updated"), item.get("created_at"), state.get("updated_at")]'''
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError("Unknown Builder catalog source; refusing a broad rewrite")
    return source.replace(old, new, 1)
