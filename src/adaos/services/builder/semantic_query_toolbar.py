"""Lower collection queries to one responsive, page-scoped control surface."""

from __future__ import annotations

import copy
from typing import Any, Mapping


def compile_query_toolbars(
    document: Mapping[str, Any], webui: dict[str, Any], source_map: dict[str, list[str]],
) -> None:
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    by_id = {widget["id"]: widget for widget in widgets}
    for view in document["views"]:
        queries = view.get("query_controls") or []
        if not queries:
            continue
        toolbar = {"id": f"queries-{view['id']}", "type": "ui.queryToolbar",
                   "area": view["region_role"], "inputs": {"controls": []}}
        insert_at = min(widgets.index(by_id[f"query-{query['id']}"]) for query in queries)
        widgets.insert(insert_at, toolbar)
        for query in queries:
            old_id = f"query-{query['id']}"
            old_widget = by_id[old_id]
            inputs = old_widget["inputs"]
            control = {
                "id": old_id, "kind": query["kind"],
                "label": old_widget["title"], "label_i18n": old_widget["title_i18n"],
                "stateKey": next(iter(old_widget["actions"][0]["params"])),
                "inputType": "select" if old_widget["type"] == "input.selector" else inputs["inputType"],
            }
            if "options" in inputs:
                control["options"] = copy.deepcopy(inputs["options"])
            toolbar["inputs"]["controls"].append(control)
            widgets.remove(old_widget)
            old = f"ui.application.desktop.pageSchema.widgets.@{old_id}"
            new = f"ui.application.desktop.pageSchema.widgets.@{toolbar['id']}.inputs.controls.@{old_id}"
            for refs in source_map.values():
                refs[:] = [ref.replace(old, new, 1) if ref == old or ref.startswith(old + ".") else ref for ref in refs]
