"""Stable SDK façade for executable Builder prototypes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

_COMPATIBILITY_PORT: Any = None


def _compatibility_execution_port():
    global _COMPATIBILITY_PORT
    from adaos.adapters.builder.legacy_dev_skill import (
        LegacyDevSkillPrototypeExecution,
    )

    if _COMPATIBILITY_PORT is None:
        _COMPATIBILITY_PORT = LegacyDevSkillPrototypeExecution()
    return _COMPATIBILITY_PORT


def submit_request(
    statement: str,
    *,
    webspace_id: str,
    locale: str = "en",
    conversation_context: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source_kind: Literal["chat", "api", "e2e", "unknown"] = "api",
    auto_apply: bool = True,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Submit one Prototype turn through the public SDK contract.

    The current execution port is named in the receipt and remains a
    compatibility backend until generation ownership moves fully into Core.
    """

    from adaos.services.builder.prototype_requests import submit_request as submit

    return submit(
        _compatibility_execution_port(),
        statement,
        webspace_id=webspace_id,
        locale=locale,
        conversation_context=conversation_context,
        metadata=metadata,
        source_kind=source_kind,
        auto_apply=auto_apply,
        timeout_seconds=timeout_seconds,
    )


def candidate_status(
    session_id: str,
    *,
    webspace_id: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Read one Prototype candidate through the public SDK contract."""

    from adaos.services.builder.prototype_requests import candidate_status as status

    return status(
        _compatibility_execution_port(),
        session_id,
        webspace_id=webspace_id,
        timeout_seconds=timeout_seconds,
    )


def model_context(brief: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded generation-stage view of a full Prototype Brief."""

    from adaos.services.builder.prototype_context import (
        compile_prototype_model_context,
    )

    return compile_prototype_model_context(brief)


def merge_briefs(*briefs: Mapping[str, Any]) -> dict[str, Any]:
    """Merge accepted turn Briefs into a cumulative project contract."""

    from adaos.services.builder_intent import merge_prototype_briefs

    return merge_prototype_briefs(*briefs)


def compile_semantic(
    document: Mapping[str, Any],
    *,
    brief: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Compile a semantic Prototype document into canonical runtime artifacts."""

    from adaos.services.builder.semantic_prototype import compile_semantic_prototype

    return compile_semantic_prototype(
        document,
        brief=brief,
        project_ref=project_ref,
    )


def compile_semantic_candidate(
    candidate: Mapping[str, Any],
    *,
    brief: Mapping[str, Any],
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Compile a strict model candidate into canonical runtime artifacts."""

    from adaos.services.builder.semantic_prototype import (
        compile_semantic_prototype_candidate,
    )

    return compile_semantic_prototype_candidate(
        candidate,
        brief=brief,
        project_ref=project_ref,
    )


def normalize_semantic_candidate(
    candidate: Mapping[str, Any], *, brief: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and lower a strict model candidate to the semantic ABI."""

    from adaos.services.builder.semantic_prototype import (
        normalize_semantic_prototype_candidate,
    )

    return normalize_semantic_prototype_candidate(candidate, brief=brief)


def semantic_contract() -> dict[str, Any]:
    """Return the schema used for semantic Prototype generation."""

    from adaos.services.builder.semantic_prototype import semantic_prototype_contract

    return semantic_prototype_contract()


def semantic_candidate_contract() -> dict[str, Any]:
    """Return the strict schema supplied to the Prototype design model."""

    from adaos.services.builder.semantic_prototype import (
        semantic_prototype_candidate_contract,
    )

    return semantic_prototype_candidate_contract()


def semantic_provider_contract() -> dict[str, Any]:
    """Return the OpenAI strict-subset projection of the model contract."""

    from adaos.services.builder.semantic_prototype import (
        semantic_prototype_provider_contract,
    )

    return semantic_prototype_provider_contract()


def start_data_runtime(definition: Mapping[str, Any]):
    from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

    return PrototypeDataRuntime.start(definition)


def composition_slice(
    webui: Mapping[str, Any],
    target_ref: str,
    *,
    source_revision: str,
    acceptance: list[Mapping[str, Any]] | None = None,
    evidence_budget: int = 5,
    renderer_snapshots: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    from adaos.services.builder.composition import extract_composition_slice

    return extract_composition_slice(
        webui,
        target_ref,
        source_revision=source_revision,
        acceptance=acceptance,
        evidence_budget=evidence_budget,
        renderer_snapshots=renderer_snapshots,
    )


def check_spatial_constraint(
    slice_value: Mapping[str, Any], constraint: Mapping[str, Any]
) -> dict[str, Any]:
    from adaos.services.builder.composition import evaluate_spatial_constraint

    return evaluate_spatial_constraint(slice_value, constraint)


def validate_workflow_slice(
    value: Mapping[str, Any], *, source_definition: Mapping[str, Any]
) -> dict[str, Any]:
    from adaos.services.builder.conversational_prototype import (
        validate_conversational_workflow_slice,
    )

    return validate_conversational_workflow_slice(
        value, source_definition=source_definition
    )


def automation_handoff(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.prototype_handoff import build_automation_handoff

    return build_automation_handoff(**kwargs)


__all__ = [
    "candidate_status",
    "check_spatial_constraint",
    "compile_semantic",
    "compile_semantic_candidate",
    "composition_slice",
    "automation_handoff",
    "model_context",
    "merge_briefs",
    "normalize_semantic_candidate",
    "semantic_candidate_contract",
    "semantic_contract",
    "semantic_provider_contract",
    "start_data_runtime",
    "submit_request",
    "validate_workflow_slice",
]
