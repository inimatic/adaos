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


def prepare_state_repair(candidate: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
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
    available = semantic_prototype_provider_contract(version="v2", locales=locales)["$defs"]
    definitions: dict[str, Any] = {}

    def include(name: str) -> None:
        if name in definitions:
            return
        definitions[name] = available[name]
        visit(available[name])

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
    include("view")
    definitions["representativeState"]["properties"]["id"] = {"type": "string", "enum": [state["id"] for state in states]}
    definitions["view"]["properties"]["id"] = {"type": "string", "enum": [view["id"] for view in views]}
    digest = _digest(candidate)
    return {
        "base_sha256": digest,
        "allowed_state_ids": [state["id"] for state in states],
        "allowed_view_ids": [view["id"] for view in views],
        "task": "Return only changed states and/or related views that resolve every reported failure. Unchanged states and views need not be returned: a view-only change can repair a state's visibility or query reachability. Fixtures, commands, bindings and all other states are immutable. First identify the intended state in the original user request and Brief, then choose its proof and counts. A populated condition requires matching records and a visible predicate; do not turn it into an empty state to bypass a mismatch. Empty dataset and zero query matches are different proofs; use either only when it demonstrates the requested meaning. Views may change only empty_state, field_refs or query_controls. Preserve all other properties. The merged candidate is fully validated after this patch.",
        "output_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["schema", "base_sha256", "states", "views"],
            "properties": {
                "schema": {"type": "string", "enum": ["adaos.builder.state_repair.v1"]},
                "base_sha256": {"type": "string", "enum": [digest]},
                "states": {"type": "array", "items": {"$ref": "#/$defs/representativeState"}},
                "views": {"type": "array", "items": {"$ref": "#/$defs/view"}},
            },
            "$defs": definitions,
        },
    }


def apply_state_repair(candidate: Mapping[str, Any], repair: Mapping[str, Any], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    plan = prepare_state_repair(candidate, findings)
    if plan is None:
        raise BuilderWorkflowError("state repair is not applicable to these findings")
    repair = copy.deepcopy(dict(repair))
    for view in repair.get("views") or []:
        view.setdefault("media", None)
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
                original = {**original, "surface": original.get("surface", "inline"), "media": original.get("media")}
                immutable = set(original) | set(replacement)
                immutable -= {"empty_state", "field_refs", "query_controls"}
                if any(original.get(name) != replacement.get(name) for name in immutable):
                    raise BuilderWorkflowError("state repair attempted an unrelated view change")
            result[target][index] = replacement
    return result
