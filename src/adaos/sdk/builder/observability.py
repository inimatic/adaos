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
    from adaos.services.builder.spend_ledger import BuilderSpendLedger
    return BuilderSpendLedger().configure(scope, **kwargs)


def usd_budget(scope: str, *, run_id=None) -> dict[str, Any]:
    """Read commitments, disjoint attempts and unknown charges without inference."""
    from adaos.services.builder.spend_ledger import BuilderSpendLedger
    return BuilderSpendLedger().snapshot(scope, run_id=run_id)


__all__ = ["llm_input_attribution", "retain_llm_evidence", "read_llm_evidence", "execution_metrics_from_llm",
           "configure_usd_budget", "usd_budget"]
