"""Close evidence references over declared, unambiguous ownership edges."""

from collections.abc import Mapping


def close_bindings(document: dict, brief: Mapping | None) -> list[dict]:
    views = {f"view:{item['id']}": item for item in document["views"]}
    commands = {f"command:{item['id']}": item for item in document["commands"]}
    collections = {item["id"]: item for item in (brief or {}).get("collection_requirements", [])}
    operations = {item["id"]: item for item in (brief or {}).get("operations", [])}
    normalizations = []
    for binding in document["requirement_bindings"]:
        explicit = set(binding["semantic_refs"])
        refs = set(explicit)
        for ref in explicit:
            if ref in commands:
                refs.add(f"view:{commands[ref]['view_ref']}")
        for ref in list(refs):
            if ref in views:
                refs.add(f"resource:{views[ref]['resource_ref']}")
        for ref in list(refs):
            if not ref.startswith("resource:"):
                continue
            owned = {key: view for key, view in views.items()
                     if f"resource:{view['resource_ref']}" == ref}
            roles = ["collection"] if binding["requirement_ref"] in collections else []
            if collections.get(binding["requirement_ref"], {}).get("interaction") == "capture_each":
                roles.append("editor")
            for role in roles:
                candidates = [key for key, view in owned.items() if view["role"] == role]
                if len(candidates) == 1:
                    refs.add(candidates[0])
        kind = operations.get(binding["requirement_ref"], {}).get("kind")
        if kind in {"search", "filter"}:
            query_views = [views[ref] for ref in refs if ref in views]
            if not query_views:
                query_views = [view for view in views.values() if f"resource:{view['resource_ref']}" in refs]
            queries = [f"query:{query['id']}" for view in query_views
                       for query in view.get("query_controls", []) if query["kind"] == kind]
            if len(queries) == 1:
                refs.add(queries[0])
        for ref in sorted(refs - explicit):
            normalizations.append({"kind": "binding_ownership", "from": binding["requirement_ref"], "to": ref})
        binding["semantic_refs"] = sorted(refs)
    return normalizations


def binding_findings(document: Mapping, brief: Mapping | None) -> list[dict]:
    if brief is None:
        return []
    views = {f"view:{view['id']}": view for view in document["views"]}
    bindings = {item["requirement_ref"]: set(item["semantic_refs"]) for item in document["requirement_bindings"]}
    gaps = {item["requirement_ref"] for item in document["capability_gaps"]}
    findings = []

    def add(ref, detail):
        findings.append({"code": "semantic.requirement_binding_incomplete", "path": "$.requirement_bindings",
                         "requirement_ref": ref, "detail": detail})

    for operation in brief.get("operations", []):
        ref, kind = operation["id"], operation["kind"]
        if kind not in {"search", "filter"} or ref in gaps:
            continue
        queries = {f"query:{query['id']}" for view in views.values()
                   for query in view.get("query_controls", []) if query["kind"] == kind}
        if not bindings.get(ref, set()) & queries:
            add(ref, f"{kind} requirement {ref!r} must bind a {kind} query control")
    for requirement in brief.get("collection_requirements", []):
        ref = requirement["id"]
        if ref in gaps:
            continue
        bound = bindings.get(ref, set())
        owned = [view for key, view in views.items() if key in bound and f"resource:{view['resource_ref']}" in bound]
        if not any(view["role"] == "collection" for view in owned):
            add(ref, f"collection requirement {ref!r} must bind a matching item resource and collection view")
        if requirement.get("interaction") == "capture_each" and not any(view["role"] == "editor" for view in owned):
            add(ref, f"capture_each requirement {ref!r} must bind a matching editor view")
    return findings
