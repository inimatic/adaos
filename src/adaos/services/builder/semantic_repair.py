"""Bounded state-proof repairs that cannot rewrite unrelated design decisions."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator

from .semantic_prototype import (
    _canonical_candidate_identifier,
    semantic_prototype_provider_contract,
)
from .workflow import BuilderWorkflowError


def _digest(candidate: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def prepare_state_repair(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]], *, legacy: bool = False, version: int | None = None) -> dict[str, Any] | None:
    version = version if version is not None else 1 if legacy else 3
    if version not in (1, 2, 3):
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
        properties = {("add_" + name if version == 3 and name in {"field_refs", "query_controls"} else name):
                      copy.deepcopy(available["view"]["properties"][name])
                      for name in ("id", "empty_state", "field_refs", "query_controls")}
        definitions["view"] = {"type": "object", "additionalProperties": False,
                               "required": list(properties), "properties": properties}
        visit(definitions["view"])
    definitions["representativeState"]["properties"]["id"] = {"type": "string", "enum": [state["id"] for state in states]}
    definitions["view"]["properties"]["id"] = {"type": "string", "enum": [view["id"] for view in views]}
    digest = _digest(candidate)
    return {
        "base_sha256": digest,
        "allowed_state_ids": [state["id"] for state in states],
        "allowed_view_ids": [view["id"] for view in views],
        "task": (
            "Return only changed states and view patches resolving every reported failure. "
            + ("View add_field_refs and add_query_controls are ADDITIONS: empty arrays preserve all existing fields and controls. Never repeat or replace an existing query ID. empty_state=null preserves the existing empty presentation; an object sets it. " if version == 3 else
               "View field_refs and query_controls REPLACE their complete original lists; empty arrays clear them. Carry unchanged entries forward. empty_state=null clears the original empty presentation. ")
            + "Core retains the original title, resource, role, surface and media. Fixtures, commands, bindings and unreported states are immutable. First identify the intended state in the original request and Brief, then choose its proof and counts. A populated condition requires matching records and a visible predicate; do not turn it into an empty state to bypass a mismatch. Empty dataset and zero query matches are different proofs; use either only when it demonstrates the requested meaning. The merged candidate is fully validated after this patch."
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


def apply_state_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    versions = {f"adaos.builder.state_repair.v{version}": version for version in (1, 2, 3)}
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
    Draft202012Validator(plan["output_schema"]).validate(repair)
    result = copy.deepcopy(dict(candidate))
    for key, target, allowed in (("states", "representative_states", plan["allowed_state_ids"]), ("views", "views", plan["allowed_view_ids"])):
        replacements = {item["id"]: copy.deepcopy(dict(item)) for item in repair[key]}
        if len(replacements) != len(repair[key]) or not set(replacements).issubset(allowed):
            raise BuilderWorkflowError("repair contains duplicate or out-of-scope identities")
        for index, original in enumerate(result[target]):
            replacement = replacements.get(original["id"])
            if replacement is None:
                continue
            if key == "views":
                if version == 3:
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
                immutable = set(original) | set(replacement)
                immutable -= {"empty_state", "field_refs", "query_controls"}
                if any(original.get(name) != replacement.get(name) for name in immutable):
                    raise BuilderWorkflowError("state repair attempted an unrelated view change")
            result[target][index] = replacement
    return result
