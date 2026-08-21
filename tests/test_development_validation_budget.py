from __future__ import annotations

import pytest

from adaos.domain.development_validation import (
    derive_validation_budget,
    normalize_validation_budget,
)


def test_validation_budget_is_derived_once_from_execution_authority() -> None:
    assert derive_validation_budget(
        {"max_wall_seconds": 10800},
        source="development_session.execution_budget",
    ) == {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": 180,
        "source": "development_session.execution_budget",
        "execution_max_wall_seconds": 10800,
    }


def test_validation_budget_defaults_and_clamps_at_platform_boundary() -> None:
    assert derive_validation_budget(None)["packaged_pytest_wall_seconds"] == 60
    assert derive_validation_budget({"max_wall_seconds": 86400})[
        "packaged_pytest_wall_seconds"
    ] == 300


def test_validation_budget_normalization_rejects_expanded_timeout() -> None:
    with pytest.raises(ValueError, match="outside platform bounds"):
        normalize_validation_budget(
            {
                "schema": "adaos.builder.validation_budget.v1",
                "packaged_pytest_wall_seconds": 301,
                "source": "test",
                "execution_max_wall_seconds": 10800,
            }
        )
