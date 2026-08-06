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


__all__ = ["check_spatial_constraint", "composition_slice", "start_data_runtime"]
