"""Capability-gated deterministic validation of bounded DEV projects."""

from __future__ import annotations

from typing import Any

from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.skill_capabilities import require_skill_capability


def validate_skill(
    project_id: str,
    *,
    strict: bool = True,
    probe_tools: bool = True,
    run_tests: bool = True,
) -> dict[str, Any]:
    ctx = require_ctx("sdk.developer.validation.validate_skill")
    require_skill_capability(ctx, "builder.project_validation")
    from adaos.services.developer_project_validation import validate_dev_skill

    return validate_dev_skill(
        ctx,
        project_id,
        strict=strict,
        probe_tools=probe_tools,
        run_packaged_tests=run_tests,
    )


def activate_skill(project_id: str) -> dict[str, Any]:
    ctx = require_ctx("sdk.developer.validation.activate_skill")
    require_skill_capability(ctx, "builder.project_validation")
    from adaos.services.developer_project_validation import activate_dev_skill

    return activate_dev_skill(ctx, project_id)


def invoke_skill(
    project_id: str,
    operation_id: str,
    arguments: dict[str, Any],
    *,
    timeout: float | None = None,
) -> Any:
    ctx = require_ctx("sdk.developer.validation.invoke_skill")
    require_skill_capability(ctx, "builder.project_validation")
    from adaos.services.developer_project_validation import invoke_dev_skill

    return invoke_dev_skill(
        ctx,
        project_id,
        operation_id,
        dict(arguments),
        timeout=timeout,
    )


def execute_spec(
    project_id: str,
    value: dict[str, Any],
    *,
    idempotency_key: str,
    timeout: float | None = None,
) -> dict[str, Any]:
    ctx = require_ctx("sdk.developer.validation.execute_spec")
    require_skill_capability(ctx, "builder.project_validation")
    from adaos.services.developer_project_validation import execute_dev_spec

    return execute_dev_spec(
        ctx,
        project_id,
        dict(value),
        idempotency_key=idempotency_key,
        timeout=timeout,
    )


__all__ = ["activate_skill", "execute_spec", "invoke_skill", "validate_skill"]
