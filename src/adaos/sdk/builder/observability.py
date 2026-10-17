"""Public Builder observability helpers."""

from __future__ import annotations

from typing import Any


def llm_input_attribution(**kwargs: Any) -> dict[str, Any]:
    from adaos.services.builder.llm_input_attribution import (
        build_llm_input_attribution,
    )

    return build_llm_input_attribution(**kwargs)


def retain_llm_evidence(value: Any) -> dict[str, Any]:
    """Retain exact private attempt evidence and verify its durable receipt."""
    from adaos.services.builder.llm_input_attribution import retain_llm_evidence as retain
    return retain(value)


def execution_metrics_from_llm(result: Any, *, reasoning_effort=None) -> dict[str, Any]:
    from adaos.services.builder.run_observability import execution_metrics_from_llm as project
    return project(result, reasoning_effort=reasoning_effort)


def read_llm_evidence(receipt: Any) -> dict[str, Any]:
    """Verify the retained bytes for an acknowledged private attempt input."""
    from adaos.services.builder.llm_input_attribution import read_llm_evidence as read
    return read(receipt)


def configure_usd_budget(scope: str, **kwargs: Any) -> dict[str, Any]:
    """Configure one explicitly authorized project experiment allowance."""
    from adaos.sdk import access
    access.require('applications.develop')
    from adaos.services.builder.spend_ledger import BuilderSpendLedger
    return BuilderSpendLedger().configure(scope, **kwargs)


def usd_budget(scope: str, *, run_id=None) -> dict[str, Any]:
    """Read commitments, disjoint attempts and unknown charges without inference."""
    from adaos.services.builder.spend_ledger import BuilderSpendLedger
    return BuilderSpendLedger().snapshot(scope, run_id=run_id)


def analyze_failure(*, episode, constraints, observations, hypotheses=()) -> dict[str, Any]:
    """Pure guarded-constraint RCA; returns evidence, hypotheses and unknowns.

    This diagnostic does not grant a source rewrite or certify an Application.
    """
    from adaos.services.builder.failure_analysis import analyze_constraints
    return analyze_constraints(episode=episode, constraints=constraints,
                               observations=observations, hypotheses=hypotheses)


def qualify_failure_replay(before, after, *, changed_factor, hypothesis) -> dict[str, Any]:
    """Qualify a retained one-factor replay; never starts a model or a job."""
    from adaos.services.builder.failure_analysis import qualify_replay
    return qualify_replay(before, after, changed_factor=changed_factor, hypothesis=hypothesis)


def analyze_browser_failure(receipt, *, evidence_ref: str) -> dict[str, Any]:
    """Normalize retained browser-owner facts; incomplete legacy evidence fails closed."""
    from adaos.services.builder.browser_failure_analysis import analyze_browser_receipt
    return analyze_browser_receipt(receipt, evidence_ref=evidence_ref)


def application_journey_profiles() -> list[dict[str, Any]]:
    """Describe platform-registered profiles, scopes and explicit TEST boundaries."""
    from adaos.services.builder.journey_profiles import registered_profiles
    return registered_profiles()


__all__ = ["llm_input_attribution", "retain_llm_evidence", "read_llm_evidence", "execution_metrics_from_llm",
           "configure_usd_budget", "usd_budget", "analyze_failure", "qualify_failure_replay", "analyze_browser_failure",
           "application_journey_profiles"]
