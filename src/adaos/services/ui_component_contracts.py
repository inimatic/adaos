"""Owned collection/editor declaration and deterministic consumer projections.

This is a bounded C2 contract, not a second capability registry. Component ids
describe renderer roles; concrete tools never establish a semantic identity.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping
from jsonschema import Draft202012Validator

from adaos.domain.artifact_release import canonical_payload_digest

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "abi" / "ui.collection_editor.contract.v1.json"
_DECLARATION_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("component_declaration_scope", default=None)


def load_component_contract() -> dict[str, Any]:
    selected = _DECLARATION_SCOPE.get()
    if selected is not None:
        return copy.deepcopy(selected)
    content = CONTRACT_PATH.read_text(encoding="utf-8")
    schema_content = CONTRACT_PATH.with_name("ui.component_contract.v1.schema.json").read_text(encoding="utf-8")
    return copy.deepcopy(_validated_source(content, schema_content))


@contextmanager
def component_contract_scope(declaration: Mapping[str, Any]):
    """Core-owned compilation scope; never exposed as model/tool write authority.

    Qualification can evolve an owned declaration through the actual projectors
    without rebasing another webspace's running jobs or changing global files.
    """
    selected = validate_component_contract(declaration)
    baseline = load_component_contract()
    if selected["owner"] != baseline["owner"] or selected["contract_id"] != baseline["contract_id"]:
        raise ValueError("component_contract.scope_owner_incompatible")
    token = _DECLARATION_SCOPE.set(selected)
    try:
        yield project_component_contract(selected)
    finally:
        _DECLARATION_SCOPE.reset(token)


@lru_cache(maxsize=16)
def _validated_source(content: str, schema_content: str) -> dict[str, Any]:
    value = json.loads(content)
    Draft202012Validator(json.loads(schema_content)).validate(value)
    return validate_component_contract(value)


def validate_component_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    schema = json.loads(CONTRACT_PATH.with_name("ui.component_contract.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)
    if result.get("schema") != "adaos.ui.component_contract.v1":
        raise ValueError("component_contract.schema_invalid")
    for name in ("contract_id", "owner", "version", "projection_version", "applicability", "field_types",
                 "components", "mappings", "constraints", "state_ownership", "discovery_policy", "fixtures", "obligations"):
        if not result.get(name):
            raise ValueError("component_contract.missing:" + name)
    ids = [item["id"] for item in result["components"]]
    if len(ids) != len(set(ids)):
        raise ValueError("component_contract.duplicate_component")
    for item in result["components"]:
        if not set(item["dependencies"]) <= set(ids) or not set(item["required_inputs"]) <= set(item["properties"]):
            raise ValueError("component_contract.unresolved_dependency")
        shape_refs = result["shape_refs"].get(item["id"], {})
        if not set(shape_refs) <= set(item["properties"]) or not set(shape_refs.values()) <= set(result["input_shapes"]):
            raise ValueError("component_contract.unresolved_input_shape")
    for shape in result["input_shapes"].values():
        Draft202012Validator.check_schema(shape)
    mapping_ids = [item["id"] for item in result["mappings"]]
    if len(mapping_ids) != len(set(mapping_ids)):
        raise ValueError("component_contract.duplicate_mapping")
    for mapping in result["mappings"]:
        if mapping["relation"] not in {"projection", "refinement", "alternative"}:
            raise ValueError("component_contract.relation_invalid")
        if not mapping["required"] or not set(mapping["required"]) <= set(ids) | set(result.get("realizations") or {}):
            raise ValueError("component_contract.incomplete_realization")
        if not set(mapping["obligations"]) <= set(result["obligations"]):
            raise ValueError("component_contract.obligation_unknown")
        if (mapping["relation"] == "alternative") != bool(mapping["alternatives"]):
            raise ValueError("component_contract.alternative_mismatch")
    return result


def project_component_contract(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    contract = validate_component_contract(value) if value is not None else load_component_contract()
    source_digest = canonical_payload_digest(contract)
    provenance = {"owner": contract["owner"], "contract_id": contract["contract_id"],
                  "version": contract["version"], "source_digest": source_digest,
                  "projection_version": contract["projection_version"],
                  "applicability": contract["applicability"]}
    units = []
    for component in contract["components"]:
        identity = component["id"]
        content = {**copy.deepcopy(component), "constraints": contract["constraints"],
                   "state_ownership": contract["state_ownership"],
                   "mappings": [item for item in contract["mappings"] if identity in item["required"]]}
        if component["role"] == "editor":
            content["field_types"] = contract["field_types"]
        realization_refs = {ref for mapping in content["mappings"] for ref in mapping["required"]}
        content["realizations"] = {ref: item for ref, item in contract["realizations"].items() if ref in realization_refs}
        content["input_shapes"] = {name: contract["input_shapes"][ref] for name, ref in contract["shape_refs"].get(identity, {}).items()}
        locator = {"descriptor_id": "ui_capability_catalog", "item_id": identity,
                   "expected_source_digest": source_digest}
        units.append({"id": identity, "provenance": copy.deepcopy(provenance), "locator": locator,
                      "dependencies": list(component["dependencies"]), "content": content,
                      "content_digest": canonical_payload_digest(content)})
    return {"schema": "adaos.ui.component_projections.v1", "provenance": provenance,
            "field_types": contract["field_types"], "validation_index": {unit["id"]: unit["content"] for unit in units},
            "retrieval_units": units, "mappings": contract["mappings"], "fixtures": contract["fixtures"],
            "discovery_policy": contract["discovery_policy"]}


def component_registration_types(value: Mapping[str, Any] | None = None) -> str:
    projection = project_component_contract(value)
    lines = ["// Generated by scripts/generate_ui_component_contract.py; do not edit.",
             "import type { WidgetRegistryEntry, WidgetRegistryOwnership } from './page-widget-extension'", ""]
    components = list(projection["validation_index"].values())
    contract = validate_component_contract(value) if value is not None else load_component_contract()
    for item in components:
        lines.append(f"import {{ {item['implementation']} }} from '{item['module']}'")
    def ts_type(schema: Mapping[str, Any]) -> str:
        if "enum" in schema:
            return " | ".join(json.dumps(item) for item in schema["enum"])
        kind = schema.get("type")
        if isinstance(kind, list):
            return " | ".join(ts_type({"type": member}) for member in kind)
        if kind == "object":
            required = set(schema.get("required") or [])
            return "{ " + "; ".join(f"{name}{'' if name in required else '?'}: {ts_type(prop)}" for name, prop in schema["properties"].items()) + " }"
        if kind == "array":
            return "readonly (" + ts_type(schema["items"]) + ")[]"
        if kind in {"string", "boolean", "number", "null"}:
            return kind
        raise ValueError("component_contract.input_type_not_supported")
    for name, shape in contract["input_shapes"].items():
        lines.append("export type " + name + " = " + ts_type(shape))
    lines.extend(["", "export const COMPONENT_CONTRACT_PROVENANCE = " + json.dumps(projection["provenance"], separators=(",", ":")) + " as const", "",
                  "export type ContractWidgetType = " + " | ".join(repr(item["id"]) for item in components),
                  "export type ContractWidgetInputs = {"])
    for item in components:
        types = {"array": "readonly unknown[]", "boolean": "boolean", "string": "string", "number": "number"}
        refs = contract["shape_refs"].get(item["id"], {})
        props = "; ".join(f"{name}{'' if name in item['required_inputs'] else '?'}: {('readonly ' + refs[name] + '[]') if name in refs else types[kind]}" for name, kind in item["properties"].items())
        lines.append(f"  '{item['id']}': {{ {props} }}")
    lines.extend(["}", "", "export const CONTRACT_WIDGET_OWNERSHIP: Record<ContractWidgetType, WidgetRegistryOwnership> = {"])
    for item in components:
        lines.append(f"  '{item['id']}': {{ owner: '{projection['provenance']['owner']}', classification: 'generic', loading: '{item['loading']}' }},")
    lines.extend(["}", "", "export const CONTRACT_WIDGET_REGISTRY: Record<ContractWidgetType, WidgetRegistryEntry> = {"])
    for item in components:
        lines.append(f"  '{item['id']}': {{ component: {item['implementation']} }},")
    return "\n".join([*lines, "}", ""])


def discovery_policy_text(*, available: bool | None) -> str:
    policy = load_component_contract()["discovery_policy"]
    if available is False:
        return policy["inline"] + " No retrieval tools are available. " + policy["unavailable"]
    return " ".join([policy["inline"], policy["known_locator"], policy["unknown_identity"], policy["unavailable"],
        "If retrieval tools are supplied, " if available is None else "",
        "Read only relevant missing rules; do not reread inline facts without a conflict. "
        "For Prototype guidance pass library_digest as expected_digest; known ids can be read directly in batches of 1–8. "
        "Do not search for an id already listed. Retrieval is read-only and cannot change project state."])


def component_contract_findings(webui: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate owned shapes, without claiming coverage of other renderers."""
    index = project_component_contract()["validation_index"]
    findings = []
    declared = webui.get("ui", {}).get("application", {}).get("desktop", {}).get("pageSchema", {}).get("meta", {}).get("builder", {}).get("component_contract")
    if declared is not None and declared != project_component_contract()["provenance"]:
        findings.append({"code": "component.projection_snapshot_incompatible", "severity": "error", "path": "$.ui.application.desktop.pageSchema.meta.builder.component_contract", "message": "Rebuild this projection under a new explicit admission; retain the running snapshot."})
    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            component = index.get(value.get("type")) if isinstance(value.get("type"), str) else None
            if component is not None and "id" in value:
                inputs = value.get("inputs") or {}
                for name in component["required_inputs"]:
                    if name not in inputs:
                        findings.append({"code": "component.required_input_missing", "severity": "error", "path": path + ".inputs." + name, "message": "Required by the owned component declaration."})
                for name, kind in component["properties"].items():
                    if name in inputs and not Draft202012Validator({"type": kind}).is_valid(inputs[name]):
                        findings.append({"code": "component.input_shape_incompatible", "severity": "error", "path": path + ".inputs." + name, "message": "Input disagrees with the owned component declaration."})
                for name, schema in component["input_shapes"].items():
                    if name in inputs and not Draft202012Validator({"type": "array", "items": schema}).is_valid(inputs[name]):
                        findings.append({"code": "component.item_shape_incompatible", "severity": "error", "path": path + ".inputs." + name, "message": "Item disagrees with the owned component declaration."})
            for key, child in value.items():
                visit(child, path + "." + str(key))
        elif isinstance(value, list):
            for number, child in enumerate(value):
                visit(child, f"{path}[{number}]")
    visit(webui, "$")
    return findings


def lowering_receipt(document: Mapping[str, Any], webui: Mapping[str, Any]) -> dict[str, Any]:
    """Retain source/member relations; lowering is not behavioral certification."""
    projection = project_component_contract()
    application = webui["ui"]["application"]
    widgets = application["desktop"]["pageSchema"]["widgets"]
    widgets = [*widgets, *[widget for modal in (application.get("modals") or {}).values()
                          for widget in (modal.get("schema") or {}).get("widgets", [])]]
    by_id = {widget["id"]: widget for widget in widgets}
    mappings = {mapping["id"]: mapping for mapping in projection["mappings"]}
    rows = []
    def record(mapping_id: str, refs: list[str], members: list[dict[str, Any]]) -> None:
        declaration = mappings[mapping_id]
        realized = {member["ref"] for member in members}
        if not set(declaration["required"]) <= realized:
            raise ValueError("component.required_realization_missing:" + mapping_id)
        rows.append({"mapping_id": mapping_id, "relation": declaration["relation"], "source_refs": refs,
                     "required_members": members, "observable_obligations": declaration["obligations"],
                     "status": "lowered_requires_independent_conformance"})
    for view in document["views"]:
        ref = "view:" + view["id"]
        widget = by_id.get(view["id"])
        if view["role"] == "collection" and view.get("presentation") == "table":
            if not widget or widget["type"] != "ui.table":
                raise ValueError("component.table_realization_missing:" + ref)
            record("collection.table", [ref], [{"ref": "ui.table", "widget_id": view["id"]}])
            if view.get("query_controls"):
                toolbar = by_id.get("queries-" + view["id"])
                if not toolbar or toolbar["type"] != "ui.queryToolbar":
                    raise ValueError("component.query_realization_missing:" + ref)
                record("collection.query", [ref, *["query:" + query["id"] for query in view["query_controls"]]],
                    [{"ref": "ui.table", "widget_id": view["id"]}, {"ref": "ui.queryToolbar", "widget_id": toolbar["id"]}])
        elif view["role"] == "editor":
            commands = [command for command in document["commands"] if command["view_ref"] == view["id"]]
            if not widget or widget["type"] != "ui.form":
                raise ValueError("component.editor_realization_missing:" + ref)
            record("editor.surface", [ref], [{"ref": "ui.form", "widget_id": view["id"], "alternative": view.get("surface", "inline")}])
            if commands:
                actions = [action for action in widget.get("actions") or [] if action.get("type") == "resourceOperation"]
                buttons = {button.get("id") for button in widget.get("inputs", {}).get("buttons", [])}
                def realized_actions(command):
                    operation = "update" if command["kind"] == "transition" else command["kind"]
                    payload = {key: "$event.values." + key for key in command.get("input_field_refs", [])}
                    payload.update(command.get("fixed_values") or {})
                    return [action for action in actions if action.get("id") == command["id"]
                        and command["id"] in buttons and action.get("on") == "submit"
                        and action.get("params", {}).get("operation_id") == operation
                        and action.get("params", {}).get("context", {}).get("prototype_command_id") == command["id"]
                        and action.get("target") == widget.get("dataSource", {}).get("resourceType")
                        and action.get("params", {}).get("payload", {}) == payload]
                coverage = []
                for command in commands:
                    matches = realized_actions(command)
                    members = [{"widget_id": widget["id"], "action_id": action["id"]} for action in matches]
                    # A row command may be an entry point composed with a
                    # primary submit command (N:1), not a duplicate mutation.
                    if not members and (command.get("exposure") or {}).get("placement") == "row_action":
                        submits = [other for other in commands if other["kind"] == command["kind"]
                            and (other.get("exposure") or {}).get("placement") == "editor_primary"
                            and command_obligations(other) == command_obligations(command)
                            and realized_actions(other)]
                        for candidate in widgets:
                            event = "click:" + command["id"]
                            sequence = [action for action in candidate.get("actions", []) if action.get("on") == event]
                            exposed = any(button.get("id") == command["id"] for button in candidate.get("inputs", {}).get("buttons", []))
                            selected = any(action.get("type") == "updateState"
                                and action.get("params", {}).get(widget.get("inputs", {}).get("selectedStateKey")) == "$event.id" for action in sequence)
                            opened = view.get("surface", "inline") == "inline" or any(action.get("type") == "openModal"
                                and any(modal.get("id", key) == action.get("params", {}).get("modalId")
                                    and any(item.get("id") == widget["id"] for item in modal.get("schema", {}).get("widgets", []))
                                    for key, modal in (application.get("modals") or {}).items()) for action in sequence)
                            if submits and exposed and selected and opened:
                                members.extend({"widget_id": candidate["id"], "event": event, "submit_command_id": other["id"],
                                    "composition": "editor_entry_point_to_mutation", "obligations": command_obligations(command)} for other in submits)
                    if not members:
                        raise ValueError("component.command_realization_missing:command:" + command["id"])
                    coverage.append({"source_ref": "command:" + command["id"], "members": members})
                record("editor.commands", [ref, *["command:" + command["id"] for command in commands]],
                    [{"ref": "ui.form", "widget_id": view["id"]},
                     {"ref": "client.resourceOperation", "command_ids": [action.get("id") for action in actions], "coverage": coverage},
                     {"ref": "provider.prototype", "resource_ref": view["resource_ref"]}])
    return {"schema": "adaos.ui.contract_lowering_receipt.v1", "source": projection["provenance"], "relations": rows}


def command_obligations(command: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic obligations, independent of labels, tool ids and placements."""
    return {"kind": command["kind"], "view_ref": command["view_ref"],
        "inputs": sorted(set(command.get("input_field_refs") or [])),
        "fixed_effects": copy.deepcopy(command.get("fixed_values") or {}),
        "guard": copy.deepcopy(command.get("guard")), "confirmation": copy.deepcopy(command.get("confirmation")),
        "authority": copy.deepcopy(command.get("authority"))}


class CommandCompositionGap(ValueError):
    def __init__(self, command: Mapping[str, Any], candidates: list[Mapping[str, Any]]):
        self.findings = [{"code": "component.command_composition_incompatible", "source_ref": "command:" + command['id'],
            "candidate_refs": ["command:" + item['id'] for item in candidates],
            "obligations": command_obligations(command)}]
        super().__init__("component.command_composition_incompatible:command:" + command['id'])


def editor_entry_point_target(command: Mapping[str, Any], commands: list[Mapping[str, Any]]) -> str | None:
    """Only lossless N:1 compositions may replace a source mutation action."""
    if (command.get('exposure') or {}).get('placement') != 'row_action':
        return None
    candidates = [other for other in commands if other['id'] != command['id']
        and other.get('view_ref') == command.get('view_ref') and other.get('kind') == command.get('kind')
        and (other.get('exposure') or {}).get('placement') == 'editor_primary']
    if not candidates:
        return None
    matching = [other for other in candidates if command_obligations(other) == command_obligations(command)]
    if not matching:
        raise CommandCompositionGap(command, candidates)
    return matching[0]['id']


def contract_impact(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    before, after = project_component_contract(previous), project_component_contract(current)
    old = {item["id"]: item for item in before["retrieval_units"]}
    new = {item["id"]: item for item in after["retrieval_units"]}
    affected = {identity for identity in set(old) | set(new)
                if old.get(identity, {}).get("content_digest") != new.get(identity, {}).get("content_digest")}
    global_dependencies = [key for key in ("discovery_policy", "projection_version", "applicability", "owner", "contract_id")
                           if previous.get(key) != current.get(key)]
    if global_dependencies:
        affected.update(set(old) | set(new))
    while True:
        expanded = affected | {item["id"] for item in [*before["retrieval_units"], *after["retrieval_units"]] if affected & set(item["dependencies"])}
        if expanded == affected:
            break
        affected = expanded
    return {"schema": "adaos.ui.contract_impact.v1", "previous": before["provenance"], "current": after["provenance"],
            "global_dependencies": global_dependencies,
            "affected_units": sorted(affected), "affected_mappings": sorted({item["id"] for item in [*before["mappings"], *after["mappings"]] if affected & set(item["required"])}),
            "projections": ["client.registration", "client.types", "core.validation", "builder.retrieval", "root.descriptors"],
            "fixtures": after["fixtures"] if affected else [],
            "requires_new_admission": before["provenance"] != after["provenance"] or bool(affected),
            "active_runs": "retain_pinned_snapshot"}
