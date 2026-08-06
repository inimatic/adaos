"""Stable SDK façade for executable Builder prototypes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
) -> dict[str, Any]:
    from adaos.services.builder.composition import extract_composition_slice

    return extract_composition_slice(
        webui,
        target_ref,
        source_revision=source_revision,
        acceptance=acceptance,
        evidence_budget=evidence_budget,
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

    return validate_conversational_workflow_slice(value, source_definition=source_definition)


def automation_handoff(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.prototype_handoff import build_automation_handoff

    return build_automation_handoff(**kwargs)


__all__ = [
    "check_spatial_constraint",
    "composition_slice",
    "automation_handoff",
    "start_data_runtime",
    "validate_workflow_slice",
]
