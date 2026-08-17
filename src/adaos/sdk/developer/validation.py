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


__all__ = ["validate_skill"]
