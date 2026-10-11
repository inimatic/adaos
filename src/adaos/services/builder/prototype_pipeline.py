"""Opt-in Prototype delivery: exact platform kernel and closed-scope repairs.

This is a projection, not a second instruction library or learned memory.
Canonical SDK guidance, the full candidate and validation remain authoritative.
Unknown inputs and unavailable retrieval retain the original full context.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

from adaos.services.builder_context import canonical, digest

KERNEL = frozenset({
    "contract", "stage", "limits", "authoring_constraints", "modeling",
    "command_ownership", "view_roles", "relationships", "selection_links",
    "layout_regions", "coverage", "state_rules", "state_proofs",
    "field_validation", "fixture_values",
})
STRATEGIES = ("full", "hybrid", "hybrid-focused", "core-indexed", "task-aware", "task-aware-compact", "oracle")


def _focused_candidate(candidate: dict, scope: dict, contract: str) -> dict | None:
    """Select complete typed resource closure; never truncate individual facts."""
    if not isinstance(contract, str):
        return None
    family = contract.rsplit(".v", 1)[0]
    keys = {
        "adaos.builder.state_repair": ("allowed_view_ids", "allowed_state_ids"),
        "adaos.builder.collection_repair": (None, None),
        "adaos.builder.record_repair": (None, None),
        "adaos.builder.reference_repair": (None, None),
    }
    supported = {"adaos.builder.state_repair.v" + str(version) for version in (1, 2, 3, 4)} | {
        "adaos.builder.collection_repair.v1", "adaos.builder.record_repair.v1", "adaos.builder.reference_repair.v1"}
    if contract not in supported or scope.get("base_sha256") != digest(candidate).removeprefix("sha256:"):
        return None
    resources = candidate.get("resources")
    if not isinstance(resources, list) or not all(isinstance(item, dict) and item.get("id") for item in resources):
        return None
    view_key, state_key = keys[family]
    view_ids = set(scope.get(view_key) or []) if view_key else set()
    state_ids = set(scope.get(state_key) or []) if state_key else set()
    resource_ids = set()
    for name in ("state_contexts", "record_contexts", "collection_contexts", "fixture_scope"):
        for item in scope.get(name) or []:
            if isinstance(item, dict):
                value = item.get("resource_ref") or item.get("resource_id")
                if value:
                    resource_ids.add(value)
    views = candidate.get("views") or []
    # Raw v2 source-side selection_links can supply a target's filter without
    # target.selection_filter being present. Until that graph is normalized,
    # a target-only projection cannot prove it retained the initial selection.
    if any(view.get("selection_links") for view in views):
        return None
    view_by_id = {view["id"]: view for view in views}
    if not view_ids <= set(view_by_id):
        return None
    if family == "adaos.builder.state_repair":
        selected_states = [state for state in candidate.get("representative_states") or [] if state.get("id") in state_ids]
        if {state.get("id") for state in selected_states} != state_ids or any(
                (state.get("proof") or {}).get("kind") not in {"field_predicate", "query_empty", "collection_empty"}
                for state in selected_states):
            return None  # form/command-dependent proofs need the full interaction graph
    while True:
        ancestors = {((view_by_id[identity].get("selection_filter") or {}).get("source_view_ref")
                      or view_by_id[identity].get("activation_source_view_ref")) for identity in view_ids} - {None}
        if not ancestors <= set(view_by_id):
            return None
        if ancestors <= view_ids:
            break
        view_ids.update(ancestors)
    resource_ids.update(view.get("resource_ref") for view in views if view.get("id") in view_ids)
    if family == "adaos.builder.reference_repair":
        # Reference repair touches relationships, so retain every endpoint resource.
        for relation in candidate.get("relationships") or []:
            resource_ids.update((relation.get("from_resource_ref"), relation.get("to_resource_ref")))
    resource_ids.discard(None)
    if not resource_ids or not resource_ids <= {item["id"] for item in resources}:
        return None
    relations = candidate.get("relationships") or []
    # Append-only fixture repairs can depend on foreign-key targets and incoming
    # constraints. Keep the entire connected data component, including records.
    while True:
        previous = set(resource_ids)
        for relation in relations:
            endpoints = {relation.get("from_resource_ref"), relation.get("to_resource_ref")} - {None}
            if resource_ids & endpoints:
                resource_ids.update(endpoints)
        if previous == resource_ids:
            break
    if not resource_ids <= {item["id"] for item in resources}:
        return None
    if family == "adaos.builder.collection_repair":
        view_ids.update(view["id"] for view in views if view.get("resource_ref") in resource_ids)
    if scope.get("fixture_scope"):
        # Appending records can change unreported counts. Supply those exact
        # constraints as read-only evidence, without expanding patch authority.
        affected_views = {view["id"] for view in views if view.get("resource_ref") in resource_ids}
        state_ids.update(state["id"] for state in candidate.get("representative_states") or []
                         if state.get("view_ref") in affected_views)
        view_ids.update(state.get("view_ref") for state in candidate.get("representative_states") or [] if state.get("id") in state_ids)
    # Collection and fixture expansion can introduce additional selection
    # ancestors, even from an otherwise disconnected data component. Close all
    # dependencies to a fixed point AFTER expansion, not only for initial views.
    while True:
        previous = (set(view_ids), set(resource_ids), set(state_ids))
        if not view_ids <= set(view_by_id):
            return None
        ancestors = {value for identity in view_ids for value in (
            (view_by_id[identity].get("selection_filter") or {}).get("source_view_ref"),
            view_by_id[identity].get("activation_source_view_ref")) if value}
        if not ancestors <= set(view_by_id):
            return None
        view_ids.update(ancestors)
        resource_ids.update(view_by_id[identity].get("resource_ref") for identity in view_ids)
        resource_ids.discard(None)
        for relation in relations:
            endpoints = {relation.get("from_resource_ref"), relation.get("to_resource_ref")} - {None}
            if resource_ids & endpoints:
                resource_ids.update(endpoints)
        if family == "adaos.builder.collection_repair":
            view_ids.update(view["id"] for view in views if view.get("resource_ref") in resource_ids)
        if scope.get("fixture_scope"):
            affected = {view["id"] for view in views if view.get("resource_ref") in resource_ids}
            state_ids.update(state["id"] for state in candidate.get("representative_states") or [] if state.get("view_ref") in affected)
            view_ids.update(state.get("view_ref") for state in candidate.get("representative_states") or [] if state.get("id") in state_ids)
        if previous == (view_ids, resource_ids, state_ids):
            break
    if not resource_ids <= {item["id"] for item in resources}:
        return None
    if any((state.get("proof") or {}).get("kind") not in {"field_predicate", "query_empty", "collection_empty"}
           for state in candidate.get("representative_states") or [] if state.get("id") in state_ids):
        return None
    result = {key: copy.deepcopy(candidate[key]) for key in ("schema", "title", "layout") if key in candidate}
    result.update({
        "resources": [copy.deepcopy(item) for item in resources if item["id"] in resource_ids],
        "relationships": [copy.deepcopy(item) for item in relations
                          if item.get("from_resource_ref") in resource_ids or item.get("to_resource_ref") in resource_ids],
        "views": [copy.deepcopy(item) for item in views if item.get("id") in view_ids],
        "representative_states": [copy.deepcopy(item) for item in candidate.get("representative_states") or []
                                  if item.get("id") in state_ids],
    })
    return result


def compile_messages(messages, *, strategy: str | None = None, retrieval_available: bool | None = None,
                     oracle_fragment_ids=None):
    from adaos.services.builder.prototype_mcp import enabled, retrieval_index
    from adaos.sdk.builder.prototype import semantic_generation_guidance

    strategy = strategy or os.getenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full").strip()
    if strategy not in STRATEGIES:
        raise ValueError("Unsupported Prototype context strategy")
    source = list(messages)
    result = copy.deepcopy(source)
    receipt: dict[str, Any] = {"schema": "adaos.builder.prototype_pipeline_delivery.v1", "strategy": strategy,
        "input_sha256": digest(source), "omitted": [], "focused_repairs": [], "fallbacks": [], "task_selections": []}
    available = enabled() if retrieval_available is None else retrieval_available
    for index, message in enumerate(result):
        if strategy == "full" or not isinstance(message.get("content"), str):
            continue
        try:
            body = json.loads(message["content"])
        except ValueError:
            continue
        if not isinstance(body, dict):
            continue
        changed = False
        stable = body.get("stable_builder_context")
        if isinstance(stable, dict) and "semantic_invariants" in stable:
            invariants = stable["semantic_invariants"]
            if invariants != semantic_generation_guidance() or not available:
                receipt["fallbacks"].append({"message": index, "reason": "unknown_guidance_or_retrieval_unavailable"})
                continue
            if strategy in {"core-indexed", "task-aware", "task-aware-compact", "oracle"}:
                from adaos.services.builder.prototype_task_context import project_task_guidance
                try:
                    projected, retrieval, accents, selection = project_task_guidance(invariants, source,
                        strategy=strategy, oracle_fragment_ids=oracle_fragment_ids)
                except ValueError as exc:
                    if strategy == "oracle":
                        raise
                    receipt["fallbacks"].append({"message": index, "reason": str(exc)})
                    continue
                stable["semantic_invariants"] = projected
                stable["platform_accents"] = accents
                receipt["task_selections"].append(selection)
                receipt["omitted"].extend(selection["retrievable_fragment_ids"])
            else:
                stable["semantic_invariants"] = {key: copy.deepcopy(value) for key, value in invariants.items() if key in KERNEL}
                retrieval = retrieval_index(available=True)
                inline_ids = {key for key in KERNEL if key != "contract"}
                retrieval["dimensions"] = [item for item in retrieval["dimensions"] if item["id"] not in inline_ids]
                retrieval["inline_dimensions"] = sorted(inline_ids)
                retrieval["policy"] += " Known related ids may be read together with fragment_ids (1–8); avoid searching again for an already known id. Inline kernel rules remain authoritative."
                receipt["omitted"].extend(sorted(set(invariants) - KERNEL))
            stable["prototype_retrieval"] = retrieval
            changed = True
        repair = body.get("semantic_repair")
        if strategy in {"hybrid-focused", "core-indexed", "task-aware", "task-aware-compact", "oracle"} and isinstance(repair, dict):
            candidate, scope = repair.get("candidate"), repair.get("repair_scope")
            if isinstance(candidate, dict) and isinstance(scope, dict):
                contract = stable.get("output_contract", "") if isinstance(stable, dict) else ""
                # The scope does not echo output_schema. Resolve the contract from
                # the stable message, without assuming adjacency or message order.
                for other in result:
                    try:
                        context = json.loads(other.get("content", "")).get("stable_builder_context", {})
                        output_contract = context.get("output_contract", contract)
                        contract = output_contract.get("schema", "") if isinstance(output_contract, dict) else output_contract
                    except (ValueError, AttributeError, TypeError):
                        pass
                try:
                    projected = _focused_candidate(candidate, scope, contract)
                except (KeyError, TypeError, AttributeError, ValueError):
                    projected = None  # malformed evidence must retain the full candidate
                if projected is not None:
                    repair.pop("candidate")
                    repair["candidate_context"] = projected
                    repair["candidate_context_policy"] = "Exact repair-local context, not a complete candidate. Return only the digest-bound patch. Core preserves omitted objects and validates the full merged candidate; do not infer absent objects are deleted."
                    receipt["focused_repairs"].append({"message": index, "contract": contract,
                        "candidate_sha256": digest(candidate), "scope_sha256": digest(scope)})
                    changed = True
                else:
                    receipt["fallbacks"].append({"message": index, "reason": "repair_scope_not_proven_closed"})
        if changed:
            message["content"] = canonical(body)
    # A full control must be byte-identical, including whitespace and message order.
    receipt.update(output_sha256=digest(result),
                   input_chars=sum(len(str(item.get("content", ""))) for item in source),
                   output_chars=sum(len(str(item.get("content", ""))) for item in result))
    return result, receipt
