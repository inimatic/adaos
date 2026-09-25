"""Bounded semantic repairs that cannot rewrite unrelated design decisions."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator

from .semantic_prototype import (
    _canonical_candidate_identifier,
    _canonicalize_semantic_prototype_candidate_v2,
    _matching_state_records,
    semantic_prototype_candidate_contract,
    semantic_prototype_provider_contract,
)
from .semantic_query_scope import scoped_predicates
from .workflow import BuilderWorkflowError


def _digest(candidate: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def prepare_reference_repair(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    import re

    if not findings or any(item.get("code") != "semantic.relationship_reference_missing" for item in findings):
        return None
    relationships = candidate.get("relationships") or []
    resources = {resource["id"]: resource for resource in candidate.get("resources", [])}
    allowed: dict[tuple[str, str], list[str]] = {}
    for finding in findings:
        match = re.fullmatch(r"\$\.relationships\[(\d+)\]\.(from|to)_field_ref", str(finding.get("path") or ""))
        if not match or int(match[1]) >= len(relationships):
            return None
        relationship = relationships[int(match[1])]
        resource = resources.get(relationship.get(f"{match[2]}_resource_ref"))
        if not resource:
            return None
        allowed[(relationship["id"], f"{match[2]}_field_ref")] = sorted({"id", *(field["id"] for field in resource["fields"])})
    variants = [{"type": "object", "additionalProperties": False, "required": ["relationship_ref", "field", "value"],
                 "properties": {"relationship_ref": {"type": "string", "enum": [identity]},
                                "field": {"type": "string", "enum": [field]},
                                "value": {"type": "string", "enum": values}}}
                for (identity, field), values in sorted(allowed.items())]
    digest = _digest(candidate)
    return {"base_sha256": digest,
            "allowed_relationship_fields": [{"relationship_ref": identity, "field": field, "values": values}
                                            for (identity, field), values in sorted(allowed.items())],
            "task": (
                "Correct only the reported relationship field references, using existing fields on the declared resource. "
                "The implicit record identity is the literal field reference id. Select the field whose existing values "
                "actually implement the declared relationship cardinality; do not infer a link from similar labels. "
                "Resource endpoints, cardinality, fixtures, views, commands, states, bindings and Automation obligations "
                "are immutable. If no existing field expresses the intended relation, leave the correction absent; "
                "full validation will report the remaining defect. Return only this patch, not a complete candidate."
            ),
            "output_schema": {"type": "object", "additionalProperties": False,
                              "required": ["schema", "base_sha256", "corrections"],
                              "properties": {"schema": {"type": "string", "enum": ["adaos.builder.reference_repair.v1"]},
                                             "base_sha256": {"type": "string", "enum": [digest]},
                                             "corrections": {"type": "array", "items": {"anyOf": variants}}}}}


def apply_reference_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    plan = prepare_reference_repair(candidate, findings)
    if plan is None:
        raise BuilderWorkflowError("reference repair is not applicable to these findings")
    Draft202012Validator(plan["output_schema"]).validate(repair)
    result = copy.deepcopy(dict(candidate))
    relationships = {item["id"]: item for item in result["relationships"]}
    if len(relationships) != len(result["relationships"]):
        raise BuilderWorkflowError("reference repair cannot resolve duplicate relationship identities")
    changed: set[tuple[str, str]] = set()
    for patch in repair["corrections"]:
        identity = (patch["relationship_ref"], patch["field"])
        if identity in changed:
            raise BuilderWorkflowError("reference repair contains duplicate corrections")
        changed.add(identity)
        relationships[identity[0]][identity[1]] = patch["value"]
    return result


def prepare_binding_repair(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    codes = {"requirement.coverage_missing", "semantic.requirement_binding_incomplete"}
    if not findings or any(item.get("code") not in codes for item in findings):
        return None
    requirements = sorted({str(ref) for finding in findings
                           for ref in ([finding["requirement_ref"]] if finding.get("requirement_ref") else finding.get("requirement_refs", []))})
    if not requirements:
        return None
    identities = {kind: [item["id"] for item in candidate.get(key, [])]
                  for kind, key in (("resource", "resources"), ("relationship", "relationships"),
                                    ("view", "views"), ("command", "commands"), ("state", "representative_states"))}
    identities["field"] = [field["id"] for resource in candidate.get("resources", []) for field in resource["fields"]]
    identities["query"] = [query["id"] for view in candidate.get("views", []) for query in view.get("query_controls", [])]
    variants = [{"type": "object", "additionalProperties": False, "required": ["kind", "id"],
                 "properties": {"kind": {"type": "string", "enum": [kind]},
                                "id": {"type": "string", "enum": sorted(set(ids))}}}
                for kind, ids in identities.items() if ids]
    if not variants:
        return None
    digest = _digest(candidate)
    return {
        "base_sha256": digest,
        "allowed_requirement_refs": requirements,
        "task": (
            "Add evidence references only for the reported missing or incomplete requirement bindings. "
            "Choose existing semantic identities that actually demonstrate the requested behavior, not merely related names. "
            "Each patch's add_semantic_refs augments the original binding; all existing references are preserved. "
            "Resources, fixtures, views, states, commands, capability gaps and automation obligations are immutable. "
            "Do not claim Automation behavior is implemented by a visible Prototype example. "
            "If the needed evidence does not exist, leave that binding unchanged; full validation will report the remaining defect. "
            "Return only the bounded patch, not a new candidate. The merged candidate undergoes full compilation and acceptance."
        ),
        "output_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["schema", "base_sha256", "bindings"],
            "properties": {
                "schema": {"type": "string", "enum": ["adaos.builder.binding_repair.v1"]},
                "base_sha256": {"type": "string", "enum": [digest]},
                "bindings": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["requirement_ref", "add_semantic_refs"],
                    "properties": {
                        "requirement_ref": {"type": "string", "enum": requirements},
                        "add_semantic_refs": {"type": "array", "items": {"anyOf": variants}},
                    },
                }},
            },
        },
    }


def apply_binding_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    plan = prepare_binding_repair(candidate, findings)
    if plan is None:
        raise BuilderWorkflowError("binding repair is not applicable to these findings")
    Draft202012Validator(plan["output_schema"]).validate(repair)
    result = copy.deepcopy(dict(candidate))
    bindings = {}
    for item in result["requirement_bindings"]:
        # Compilation unions repeated evidence. Append to its first occurrence
        # without discarding any original references or changing the base digest.
        bindings.setdefault(item["requirement_ref"], item)
    changed: set[str] = set()
    for patch in repair["bindings"]:
        requirement = patch["requirement_ref"]
        if requirement in changed:
            raise BuilderWorkflowError("binding repair contains duplicate requirements")
        changed.add(requirement)
        if not patch["add_semantic_refs"]:
            continue
        if requirement not in bindings:
            bindings[requirement] = {"requirement_ref": requirement, "semantic_refs": []}
            result["requirement_bindings"].append(bindings[requirement])
        refs = bindings[requirement]["semantic_refs"]
        for ref in patch["add_semantic_refs"]:
            if ref not in refs:
                refs.append(copy.deepcopy(ref))
    return result


def _state_fixture_scope(candidate, findings):
    mismatches = {ref for finding in findings if finding.get("code") == "semantic.state_fixture_mismatch"
                  for ref in finding.get("semantic_refs") or [] if str(ref).startswith("state:")}
    other_defects = {ref for finding in findings if finding.get("code") != "semantic.state_fixture_mismatch"
                     for ref in finding.get("semantic_refs") or []}
    if not mismatches - other_defects:
        return []
    # Use the compiler's normalization and predicates, including owner-qualified fields.
    normalized, _ = _canonicalize_semantic_prototype_candidate_v2(candidate)
    views = {view["id"]: view for view in normalized["views"]}
    resources = {resource["id"]: resource for resource in normalized["resources"]}
    original_resources = {item["id"]: original for item, original in zip(normalized["resources"], candidate["resources"], strict=True)}
    original_states = {item["id"]: original for item, original in zip(normalized["representative_states"], candidate["representative_states"], strict=True)}
    limit = semantic_prototype_candidate_contract(version="v2")["$defs"]["resource"]["properties"]["records"]["maxItems"]
    scope = {}
    for state in normalized["representative_states"]:
        if f"state:{state['id']}" not in mismatches - other_defects or state["proof"]["kind"] != "field_predicate":
            continue
        view = views[state["view_ref"]]
        resource = resources[view["resource_ref"]]
        fields = [field["id"] for field in resource["fields"]]
        records = [{**dict(zip(fields, record["values"], strict=True)), "id": record["id"]} for record in resource["records"]]
        predicates = [{"field_ref": predicate["field_ref"], "operator": predicate["operator"],
                       ("compare_field_ref" if predicate["operand"]["kind"] == "field" else "value"):
                           predicate["operand"]["field_ref" if predicate["operand"]["kind"] == "field" else "value"]}
                      for predicate in state["filters"]]
        missing = state["min_items"] - len(_matching_state_records(records, scoped_predicates({"filters": predicates}, view)))
        capacity = limit - len(resource["records"])
        if missing <= 0 or missing > capacity:
            continue
        original = original_resources[resource["id"]]
        entry = scope.setdefault(original["id"], {"resource_ref": original["id"], "max_add_records": 0,
                                                 "field_refs": [field["id"] for field in original["fields"]], "state_ids": []})
        entry["max_add_records"] = min(capacity, entry["max_add_records"] + missing)
        entry["state_ids"].append(original_states[state["id"]]["id"])
    return list(scope.values())


def _state_repair_contexts(
    candidate: Mapping[str, Any],
    states: Sequence[Mapping[str, Any]],
    views: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose only the exact typed data needed for a bounded state repair."""

    view_by_id = {str(view["id"]): view for view in views}
    resource_by_id = {
        str(resource["id"]): resource for resource in candidate.get("resources") or []
    }
    contexts: list[dict[str, Any]] = []
    for state in states:
        view = view_by_id.get(str(state.get("view_ref") or ""))
        if view is None:
            continue
        resource = resource_by_id.get(str(view.get("resource_ref") or ""))
        if resource is None:
            continue
        fields = list(resource.get("fields") or [])
        field_ids = [str(field["id"]) for field in fields]
        field_contexts = []
        for field in fields:
            options = field.get("options") or []
            field_contexts.append(
                {
                    "id": field["id"],
                    "value_type": field.get("value_type"),
                    "option_values": [
                        option.get("value")
                        for option in options
                        if isinstance(option, Mapping) and "value" in option
                    ],
                }
            )
        fixtures = []
        for record in resource.get("records") or []:
            values = list(record.get("values") or [])
            fixtures.append(
                {
                    "id": record.get("id"),
                    "values": {
                        field_id: values[index]
                        for index, field_id in enumerate(field_ids)
                        if index < len(values)
                    },
                }
            )
        contexts.append(
            {
                "state_id": state["id"],
                "view_id": view["id"],
                "resource_id": resource["id"],
                "fields": field_contexts,
                "fixtures": fixtures,
                "view_field_refs": list(view.get("field_refs") or []),
                "view_query_controls": [
                    {
                        "id": control.get("id"),
                        "kind": control.get("kind"),
                        "field_ref": control.get("field_ref"),
                    }
                    for control in view.get("query_controls") or []
                ],
                "view_has_empty_state": view.get("empty_state") is not None,
            }
        )
    return contexts


def prepare_state_repair(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]], *, legacy: bool = False, version: int | None = None) -> dict[str, Any] | None:
    if version is not None and version not in (1, 2, 3, 4):
        raise BuilderWorkflowError("unsupported state repair version")
    state_codes = {
        "semantic.state_fixture_mismatch", "semantic.state_proof_hidden",
        "semantic.state_proof_invalid", "semantic.state_query_unreachable",
        "semantic.state_empty_view_missing",
        "semantic.state_predicate_invalid",
    }
    if not findings or any(item.get("code") not in state_codes for item in findings):
        return None
    refs = {str(ref) for finding in findings for ref in finding.get("semantic_refs") or []}
    states = [state for state in candidate.get("representative_states") or []
              if f"state:{_canonical_candidate_identifier(state['id'], namespace='state')}" in refs]
    if not states:
        return None
    view_ids = {state["view_ref"] for state in states}
    views = [view for view in candidate["views"] if view["id"] in view_ids]
    if len(views) != len(view_ids):
        return None
    fixture_scope = _state_fixture_scope(candidate, findings) if not legacy and version in (None, 4) else []
    version = version if version is not None else 1 if legacy else 4 if fixture_scope else 3
    if version == 4 and not fixture_scope:
        return None
    locales = tuple(locale for locale in ("en", "ru") if locale in candidate["title"])
    available = semantic_prototype_provider_contract(version="v2", locales=locales, _view_variants=False)["$defs"]
    definitions: dict[str, Any] = {}

    def include(name: str) -> None:
        if name in definitions:
            return
        definitions[name] = copy.deepcopy(available[name])
        visit(definitions[name])

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            reference = value.get("$ref", "")
            if reference.startswith("#/$defs/"):
                include(reference.removeprefix("#/$defs/"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    # Only the patch's reachable definitions belong in its provider schema.
    include("representativeState")
    if version == 1:
        include("view")
    else:
        properties = {("add_" + name if version >= 3 and name in {"field_refs", "query_controls"} else name):
                      copy.deepcopy(available["view"]["properties"][name])
                      for name in ("id", "empty_state", "field_refs", "query_controls")}
        definitions["view"] = {"type": "object", "additionalProperties": False,
                               "required": list(properties), "properties": properties}
        visit(definitions["view"])
    definitions["representativeState"]["properties"]["id"] = {"type": "string", "enum": [state["id"] for state in states]}
    definitions["view"]["properties"]["id"] = {"type": "string", "enum": [view["id"] for view in views]}
    digest = _digest(candidate)
    state_contexts = _state_repair_contexts(candidate, states, views)
    plan = {
        "base_sha256": digest,
        "allowed_state_ids": [state["id"] for state in states],
        "allowed_view_ids": [view["id"] for view in views],
        "state_contexts": state_contexts,
        "task": (
            "Return only changed states and view patches resolving every reported failure. "
            + ("View add_field_refs and add_query_controls are ADDITIONS: empty arrays preserve all existing fields and controls. Never repeat or replace an existing query ID. empty_state=null preserves the existing empty presentation; an object sets it. " if version >= 3 else
               "View field_refs and query_controls REPLACE their complete original lists; empty arrays clear them. Carry unchanged entries forward. empty_state=null clears the original empty presentation. ")
            + "Core retains the original title, resource, role, surface and media. Existing fixtures, commands, bindings and unreported states are immutable. First identify the intended state in the original request and Brief, then choose its proof and counts. state_contexts is the exact typed scope: use only a declared option_value for a choice literal and count the listed fixture matches before returning the patch. Never concatenate JSON punctuation or multiple values into one literal. A populated condition requires matching records and a visible predicate; do not turn it into an empty state to bypass a mismatch. Empty dataset and zero query matches are different proofs; use either only when it demonstrates the requested meaning. The merged candidate is fully validated after this patch."
        ),
        "output_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["schema", "base_sha256", "states", "views"],
            "properties": {
                "schema": {"type": "string", "enum": [f"adaos.builder.state_repair.v{version}"]},
                "base_sha256": {"type": "string", "enum": [digest]},
                "states": {"type": "array", "items": {"$ref": "#/$defs/representativeState"}},
                "views": {"type": "array", "items": {"$ref": "#/$defs/view"}},
            },
            "$defs": definitions,
        },
    }
    if version == 4:
        include("record")
        plan["fixture_scope"] = fixture_scope
        plan["task"] += (
            " fixture_scope authorizes APPENDING only the missing representative records to the listed resources, "
            "up to each max_add_records budget. Use fresh IDs and values in the exact listed field_refs order, "
            "respecting types, options and existing relationship targets. Do not alter or replace existing records. "
            "The listed state_ids are immutable, including their filters, proof and counts: preserve their intended "
            "condition and supply matching examples. Leave unchanged states/views out of the patch. "
            "Resource schemas and all other resources remain immutable. Full compilation checks every state and relationship."
        )
        plan["output_schema"]["required"].append("fixture_additions")
        plan["output_schema"]["properties"]["fixture_additions"] = {
            "type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["resource_ref", "records"], "properties": {
                    "resource_ref": {"type": "string", "enum": [item["resource_ref"] for item in fixture_scope]},
                    "records": {"type": "array", "items": {"$ref": "#/$defs/record"}},
                }},
        }
    return plan


def apply_state_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    versions = {f"adaos.builder.state_repair.v{version}": version for version in (1, 2, 3, 4)}
    version = versions.get(repair.get("schema"))
    if version is None:
        raise BuilderWorkflowError("unsupported state repair version")
    legacy = version == 1
    plan = prepare_state_repair(candidate, findings, version=version)
    if plan is None:
        raise BuilderWorkflowError("state repair is not applicable to these findings")
    repair = copy.deepcopy(dict(repair))
    if legacy:
        for view in repair.get("views") or []:
            view.setdefault("media", None)
            view.setdefault("presentation_options", None)
            view.setdefault("field_display", [])
            view.setdefault("section", None)
            view.setdefault("scope_filters", [])
            view.setdefault("selection_filter", None)
            if view.get("selection_filter"):
                view["selection_filter"].setdefault("source_field_ref", None)
    Draft202012Validator(plan["output_schema"]).validate(repair)
    result = copy.deepcopy(dict(candidate))
    if version == 4:
        scope = {item["resource_ref"]: item for item in plan["fixture_scope"]}
        protected = {identity for item in scope.values() for identity in item["state_ids"]}
        originals = {item["id"]: item for item in candidate["representative_states"]}
        if any(patch["id"] in protected and patch != originals[patch["id"]] for patch in repair["states"]):
            raise BuilderWorkflowError("fixture repair cannot change the intended state, predicate or counts")
        resources = {item["id"]: item for item in result["resources"]}
        seen = set()
        for addition in repair["fixture_additions"]:
            identity = addition["resource_ref"]
            if identity in seen:
                raise BuilderWorkflowError("fixture repair contains duplicate resource additions")
            seen.add(identity)
            records = addition["records"]
            if len(records) > scope[identity]["max_add_records"]:
                raise BuilderWorkflowError("fixture repair exceeds the missing-record budget")
            existing_ids = {_canonical_candidate_identifier(item["id"], namespace="record") for item in resources[identity]["records"]}
            for record in records:
                record_id = _canonical_candidate_identifier(record["id"], namespace="record")
                if record_id in existing_ids:
                    raise BuilderWorkflowError("fixture repair cannot replace or duplicate an existing record")
                existing_ids.add(record_id)
                if len(record["values"]) != len(scope[identity]["field_refs"]):
                    raise BuilderWorkflowError("fixture repair values must follow the complete field order")
            resources[identity]["records"].extend(copy.deepcopy(records))
    for key, target, allowed in (("states", "representative_states", plan["allowed_state_ids"]), ("views", "views", plan["allowed_view_ids"])):
        replacements = {item["id"]: copy.deepcopy(dict(item)) for item in repair[key]}
        if len(replacements) != len(repair[key]) or not set(replacements).issubset(allowed):
            raise BuilderWorkflowError("repair contains duplicate or out-of-scope identities")
        for index, original in enumerate(result[target]):
            replacement = replacements.get(original["id"])
            if replacement is None:
                continue
            if key == "views":
                if version >= 3:
                    updated = copy.deepcopy(original)
                    updated["field_refs"] = list(dict.fromkeys([*original["field_refs"], *replacement["add_field_refs"]]))
                    controls = {item["id"]: item for item in original["query_controls"]}
                    for control in replacement["add_query_controls"]:
                        if control["id"] in controls:
                            raise BuilderWorkflowError("state repair cannot replace or duplicate an existing query control")
                        controls[control["id"]] = control
                    updated["query_controls"] = list(controls.values())
                    if replacement["empty_state"] is not None:
                        updated["empty_state"] = replacement["empty_state"]
                    result[target][index] = updated
                    continue
                if not legacy:
                    result[target][index] = {**original, **replacement}
                    continue
                original = {**original, "surface": original.get("surface", "inline"), "media": original.get("media"),
                            "presentation_options": original.get("presentation_options"),
                            "field_display": original.get("field_display", []), "section": original.get("section"),
                            "scope_filters": original.get("scope_filters", []),
                            "selection_filter": original.get("selection_filter")}
                if original["selection_filter"]:
                    original["selection_filter"] = {"source_field_ref": None, **original["selection_filter"]}
                immutable = set(original) | set(replacement)
                immutable -= {"empty_state", "field_refs", "query_controls"}
                if any(original.get(name) != replacement.get(name) for name in immutable):
                    raise BuilderWorkflowError("state repair attempted an unrelated view change")
            result[target][index] = replacement
    # A field-predicate proof redundantly names the predicate fields that are
    # already visible in its view.  Bounded repair models occasionally add a
    # second predicate to make a fixture unique but omit that existing rendered
    # field from visible_field_refs.  Completing only those visible references
    # is deterministic and cannot broaden the repair: hidden fields still fail
    # normal validation, while unrelated and unreported states remain byte-for-
    # byte unchanged.
    repaired_state_ids = {
        str(item["id"])
        for item in repair.get("states") or []
        if isinstance(item, Mapping)
    }
    views = {str(item["id"]): item for item in result["views"]}
    for state in result["representative_states"]:
        if str(state["id"]) not in repaired_state_ids:
            continue
        proof = state.get("proof") or {}
        if proof.get("kind") != "field_predicate":
            continue
        view = views.get(str(state.get("view_ref") or ""), {})
        rendered = {str(value) for value in view.get("field_refs") or []}
        visible = list(proof.get("visible_field_refs") or [])
        predicate_fields = [
            str(field_ref)
            for predicate in state.get("filters") or []
            for field_ref in (
                predicate.get("field_ref"),
                (predicate.get("operand") or {}).get("field_ref"),
            )
            if field_ref
        ]
        for field_ref in predicate_fields:
            if field_ref in rendered and field_ref not in visible:
                visible.append(field_ref)
        proof["visible_field_refs"] = visible
    return result
