from __future__ import annotations

import pytest

from adaos.services.builder.action_contracts import (
    BUILDER_ACTION_RISKS,
    BuilderActionContractError,
    build_builder_action,
    builder_action_risk_policy,
    normalize_builder_action_risk,
)


def test_builder_action_risk_vocabulary_has_explicit_channel_and_recovery_policy() -> None:
    policies = {risk: builder_action_risk_policy(risk) for risk in BUILDER_ACTION_RISKS}

    assert policies["read"]["inline_callback"] == "allowed"
    assert policies["local_reversible"]["inline_callback"] == "allowed_with_precondition"
    assert policies["local_reversible"]["rollback_required"] is True
    assert policies["isolated_write"]["isolation_required"] is True
    assert policies["trial_activation"]["confirmation_required"] is True
    assert policies["workspace_activation"]["approval_required"] is True
    assert policies["publication"]["side_effect_scope"] == "registry"
    assert policies["destructive"]["inline_callback"] == "rich_review_required"


def test_builder_action_risk_aliases_are_normalized_but_unknown_values_fail_closed() -> None:
    assert normalize_builder_action_risk("read-only") == "read"
    assert normalize_builder_action_risk("dev_write") == "isolated_write"

    with pytest.raises(BuilderActionContractError, match="unsupported Builder action risk"):
        normalize_builder_action_risk("probably-safe")


def test_builder_action_builder_requires_namespace_generation_and_bounded_target() -> None:
    action = build_builder_action(
        "builder.preview.prototype",
        "Preview prototype",
        "read",
        expected_generation=7,
        target_ref="prototype:recipes:004",
    )

    assert action["risk"] == "read"
    assert action["risk_policy"]["schema"] == "adaos.builder.action_risk.v1"
    assert action["expected_generation"] == 7

    with pytest.raises(BuilderActionContractError, match="builder.* namespace"):
        build_builder_action("preview.prototype", "Preview", "read", expected_generation=7)
    with pytest.raises(BuilderActionContractError, match="non-negative"):
        build_builder_action("builder.preview.prototype", "Preview", "read", expected_generation=-1)
