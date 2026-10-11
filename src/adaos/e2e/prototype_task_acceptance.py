"""Independent task-level admission; never included in generation context.

Command counts cannot establish that an operation acts on the intended object.
Use typed ownership and explicit task contracts, not resource-name heuristics.
These are benchmark contracts, not universal constraints on every application.
"""
from collections.abc import Mapping, Sequence


def mutation_contract(document: Mapping, operations: Sequence[Mapping], *,
                      same_resource_groups: Sequence[Sequence[str]] = ()) -> dict:
    views = {view["id"]: view for view in document.get("views", [])}
    commands = {command["id"]: command for command in document.get("commands", [])}
    bindings = {binding["requirement_ref"]: set(binding.get("semantic_refs", []))
                for binding in document.get("requirement_bindings", [])}
    findings, resources_by_operation = [], {}
    for operation in operations:
        kind = operation.get("kind")
        if kind not in {"create", "update", "delete", "transition"}:
            continue
        reference = operation["id"]
        owned = set()
        for command_id, command in commands.items():
            if f"command:{command_id}" not in bindings.get(reference, set()) or command.get("kind") != kind:
                continue
            view = views.get(command.get("view_ref"), {})
            if view.get("role") == "editor" and view.get("resource_ref"):
                owned.add(view["resource_ref"])
        resources_by_operation[reference] = sorted(owned)
        if not owned:
            findings.append({"code": "task.operation_command_missing", "requirement_refs": [reference],
                             "detail": f"{kind} must bind an executable command on an owned editor"})
    for group in same_resource_groups:
        if len(group) < 2 or len(set(group)) != len(group) or any(ref not in resources_by_operation for ref in group):
            raise ValueError("same-resource group must name at least two distinct accepted mutation operations")
        common = set(resources_by_operation[group[0]])
        for reference in group[1:]:
            common.intersection_update(resources_by_operation[reference])
        if not common:
            findings.append({"code": "task.operation_resource_mismatch", "requirement_refs": list(group),
                             "detail": "These task operations must be executable on the same object; a child-line create is not a parent-request create"})
    return {"ok": not findings, "findings": findings, "resources_by_operation": resources_by_operation,
            "scope": "independent explicit task contract, not semantic similarity or a universal platform rule"}


def task_mutation_contract(document: Mapping, brief: Mapping, *, task: str) -> dict:
    operations = brief.get("operations", [])
    groups = []
    if task == "inventory-procurement-ru":
        create = [item["id"] for item in operations if item.get("kind") == "create"]
        transition = [item["id"] for item in operations if item.get("kind") == "transition"]
        if len(create) != 1 or len(transition) != 1:
            raise ValueError("Frozen procurement task requires exactly one create and one approval operation")
        groups = [create + transition]
    elif task != "support-desk-ru":
        raise ValueError("Unknown benchmark task")
    return mutation_contract(document, operations, same_resource_groups=groups)
