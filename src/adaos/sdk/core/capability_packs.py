"""Versioned, machine-readable capability packs for SDK authoring."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


HUMAN_DECISION_PACK_ID = "adaos.sdk.capability.human_decision.v1"

_HUMAN_DECISION_MEMBERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "adaos.sdk.workflow.create_interaction",
        (
            "explicit_action_semantics",
            "semantic_digest",
            "verified_responder",
            "exact_generation",
            "executor_readiness",
            "durable_dispatch",
            "effect_assertion",
            "durable_outcome",
        ),
    ),
    (
        "adaos.sdk.chat.request",
        (
            "standard_action_preset_or_custom_semantics",
            "semantic_digest",
            "expiry",
            "presentation_bound_action_token",
            "per_choice_assurance",
            "principal_scope",
            "generation_cas",
        ),
    ),
    (
        "adaos.sdk.chat.respond",
        (
            "presentation_bound_action_token",
            "per_choice_assurance",
            "principal_scope",
            "verified_session_principal",
            "semantic_digest",
            "expiry",
            "generation_cas",
            "idempotency_key",
            "durable_dispatch",
        ),
    ),
    (
        "adaos.sdk.workflow.invoke_interaction_response",
        (
            "accepted_interaction_response",
            "semantic_digest",
            "exact_generation",
            "idempotency_key",
            "durable_dispatch",
            "effect_binding",
            "effect_assertion",
            "outcome_receipt",
        ),
    ),
)

_FULL_CYCLE_STAGES: tuple[dict[str, Any], ...] = (
    {
        "stage": "publish",
        "api": "adaos.sdk.workflow.create_interaction",
        "input": {
            "definition": "$workflow_definition",
            "instance_id": "change:42",
            "actor_id": "user:owner",
            "conversation_id": "conversation:change:42",
            "owner": "skill:example",
            "command_context_id": "command-context:change:42",
            "expires_at": "2026-10-08T12:00:00+00:00",
            "action_semantics": "$declared_action_semantics",
            "executor_registrations": "$admitted_executors",
        },
        "retains": ["interaction_id", "generation", "semantic_digest"],
    },
    {
        "stage": "present",
        "api": "adaos.sdk.chat.request",
        "input": {
            "interaction": "$publish.result",
            "conversation_id": "conversation:change:42",
            "owner": "skill:example",
            "capability_profile": "$verified_channel_profile",
        },
        "retains": ["presentation_id", "action_token", "assurance"],
    },
    {
        "stage": "verified_answer",
        "api": "adaos.sdk.chat.respond",
        "input": {
            "interaction_id": "$publish.interaction_id",
            "actor_id": "user:owner",
            "expected_generation": "$publish.generation",
            "idempotency_key": "browser-page:decision:42",
            "action_token": "$present.action_token",
            "presentation_id": "$present.presentation_id",
        },
        "retains": ["response_id", "dispatch_id", "assurance_receipt"],
    },
    {
        "stage": "dispatch_effect_outcome",
        "api": "adaos.sdk.workflow.invoke_interaction_response",
        "input": {
            "definition": "$workflow_definition",
            "instance_id": "change:42",
            "response": "$verified_answer.response",
            "actor_id": "user:owner",
            "executor_registrations": "$admitted_executors",
        },
        "retains": ["effect_assertion", "outcome_receipt"],
    },
)


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def human_decision_capability_pack() -> dict[str, Any]:
    """Return the canonical SDK-only authoring pack for governed decisions."""

    members = [
        {"api": api, "required_action_closure": list(requirements)}
        for api, requirements in _HUMAN_DECISION_MEMBERS
    ]
    examples = [
        {
            "id": "human_decision.full_cycle.v1",
            "kind": "positive",
            "stages": copy.deepcopy(list(_FULL_CYCLE_STAGES)),
            "expected": {
                "decision_is_bound_to_exact_generation": True,
                "effect_is_observed_not_inferred": True,
                "outcome_is_durable": True,
            },
        },
        {
            "id": "human_decision.refusal.v1",
            "kind": "negative",
            "rule": "A refusal is terminal and never invokes the mutating executor.",
            "expected": {"executor_invocations": 0, "outcome": "refused"},
        },
        {
            "id": "human_decision.expiry.v1",
            "kind": "negative",
            "rule": "An expired or superseded interaction cannot create a decision or dispatch.",
            "expected_error": "interaction_expired",
        },
        {
            "id": "human_decision.executor_unavailable.v1",
            "kind": "negative",
            "rule": "Missing current executor readiness disables mutation without inventing a callback.",
            "expected_error": "executor_unavailable",
        },
        {
            "id": "human_decision.effect_assertion_missing.v1",
            "kind": "negative",
            "rule": "A successful callback without the declared effect assertion is outcome_unknown.",
            "expected_error": "outcome_unknown",
        },
        {
            "id": "human_decision.preview_read_only.v1",
            "kind": "negative",
            "rule": "Preview and evidence inspection never record consent or dispatch an effect.",
            "expected": {"decisions": 0, "dispatches": 0},
        },
    ]
    contract: dict[str, Any] = {
        "capabilities": [
            "human_decision.publish",
            "human_decision.present",
            "human_decision.respond",
            "human_decision.execute",
        ],
        "permissions": [],
        "effects": ["declared_workflow_effect", "durable_write"],
        "errors": [
            "assurance_not_admitted",
            "executor_unavailable",
            "interaction_expired",
            "outcome_unknown",
            "principal_denied",
            "stale_generation",
        ],
        "boundedness": {"kind": "exact_capability_pack"},
        "pagination": {"supported": False, "arguments": []},
        "stability": "beta",
        "since": "1.5.0",
        "deprecated": False,
        "removedIn": None,
        "replacement": None,
        "migration_recipe": None,
        "runtime_support": {
            "owners": ["conversation_runtime", "governed_workflow_runtime"],
            "min_contract": 1,
        },
        "action_closure": {
            "members": [api for api, _requirements in _HUMAN_DECISION_MEMBERS],
            "requires": sorted(
                {
                    requirement
                    for _api, requirements in _HUMAN_DECISION_MEMBERS
                    for requirement in requirements
                }
            ),
        },
        "authoring_visibility": "default",
        "schema_refs": {"origin": "capability_pack.v1"},
    }
    contract["digest"] = _digest(contract)
    return {
        "kind": "sdk_capability_pack",
        "name": HUMAN_DECISION_PACK_ID,
        "module": "adaos.sdk.workflow",
        "summary": (
            "Complete governed human-decision lifecycle: publish, present, "
            "verify, dispatch, observe the effect, and persist the outcome."
        ),
        "description": (
            "Use this pack when an Application must wait for a person's decision. "
            "It is independent of whether the Application also exposes chat."
        ),
        "meta": {
            "stability": "beta",
            "side_effects": "declared_workflow_effect",
            "pack_version": 1,
        },
        "contract": contract,
        "members": members,
        "examples": examples,
        "validators": [
            "member_exists",
            "required_input_coverage",
            "action_closure_coverage",
            "positive_full_cycle",
            "negative_lifecycle_matrix",
        ],
    }


def validate_human_decision_capability_pack(
    pack: Mapping[str, Any],
    sdk_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate pack references and example closure against generated SDK metadata."""

    findings: list[dict[str, Any]] = []
    by_name = {
        str(item.get("name") or "").strip(): item
        for item in sdk_items
        if str(item.get("name") or "").strip()
    }
    declared_members = {
        str(item.get("api") or ""): set(item.get("required_action_closure") or [])
        for item in pack.get("members") or []
        if isinstance(item, Mapping)
    }
    for api, required_closure in _HUMAN_DECISION_MEMBERS:
        item = by_name.get(api)
        if item is None:
            findings.append({"code": "member_missing", "api": api})
            continue
        declared = declared_members.get(api, set())
        actual = set(
            dict(dict(item.get("contract") or {}).get("action_closure") or {}).get(
                "requires"
            )
            or []
        )
        expected = set(required_closure)
        if not expected.issubset(declared):
            findings.append(
                {
                    "code": "pack_action_closure_incomplete",
                    "api": api,
                    "missing": sorted(expected - declared),
                }
            )
        if not expected.issubset(actual):
            findings.append(
                {
                    "code": "sdk_action_closure_incomplete",
                    "api": api,
                    "missing": sorted(expected - actual),
                }
            )

    examples = [
        item for item in pack.get("examples") or [] if isinstance(item, Mapping)
    ]
    full_cycle = next(
        (
            item
            for item in examples
            if str(item.get("id") or "") == "human_decision.full_cycle.v1"
        ),
        None,
    )
    expected_stage_names = [stage["stage"] for stage in _FULL_CYCLE_STAGES]
    stages = (
        [item for item in full_cycle.get("stages") or [] if isinstance(item, Mapping)]
        if isinstance(full_cycle, Mapping)
        else []
    )
    if [str(stage.get("stage") or "") for stage in stages] != expected_stage_names:
        findings.append({"code": "positive_full_cycle_incomplete"})
    for stage in stages:
        api = str(stage.get("api") or "")
        sdk_item = by_name.get(api)
        if sdk_item is None:
            continue
        supplied = set(dict(stage.get("input") or {}))
        input_schema = (
            dict(sdk_item.get("input_schema") or {})
            if isinstance(sdk_item.get("input_schema"), Mapping)
            else {}
        )
        required = {str(name) for name in input_schema.get("required") or []}
        if not required.issubset(supplied):
            findings.append(
                {
                    "code": "example_required_input_missing",
                    "api": api,
                    "missing": sorted(required - supplied),
                }
            )

    expected_negative_ids = {
        "human_decision.refusal.v1",
        "human_decision.expiry.v1",
        "human_decision.executor_unavailable.v1",
        "human_decision.effect_assertion_missing.v1",
        "human_decision.preview_read_only.v1",
    }
    negative_ids = {
        str(item.get("id") or "")
        for item in examples
        if str(item.get("kind") or "") == "negative"
    }
    if not expected_negative_ids.issubset(negative_ids):
        findings.append(
            {
                "code": "negative_lifecycle_matrix_incomplete",
                "missing": sorted(expected_negative_ids - negative_ids),
            }
        )

    return {
        "schema": "adaos.sdk.capability_pack_validation.v1",
        "pack_id": str(pack.get("name") or ""),
        "pack_digest": str(dict(pack.get("contract") or {}).get("digest") or ""),
        "ok": not findings,
        "findings": findings,
        "coverage": {
            "members": len(_HUMAN_DECISION_MEMBERS),
            "positive_examples": sum(
                1 for item in examples if str(item.get("kind") or "") == "positive"
            ),
            "negative_examples": len(negative_ids),
        },
    }


__all__ = [
    "HUMAN_DECISION_PACK_ID",
    "human_decision_capability_pack",
    "validate_human_decision_capability_pack",
]
