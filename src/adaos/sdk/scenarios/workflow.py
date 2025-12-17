"""SDK facade for applying scenario workflow actions."""

from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk.core.decorators import tool
from adaos.sdk.core.ctx import get_ctx
from adaos.services.scenario.workflow_runtime import ScenarioWorkflowRuntime as ScenarioWorkflowRuntime

__all__ = ["ScenarioWorkflowRuntime", "apply_action"]


@tool(
    "scenarios.workflow.apply_action",
    summary="Apply a workflow action for a scenario in a given webspace.",
    stability="experimental",
    examples=["await scenarios.workflow.apply_action('greet_on_boot', 'default', 'collect')"],
)
async def apply_action(
    scenario_id: str,
    webspace_id: str,
    action_id: str,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    runtime = ScenarioWorkflowRuntime(get_ctx())
    if payload is None:
        return await runtime.apply_action(scenario_id, webspace_id, action_id)
    return await runtime.apply_action(scenario_id, webspace_id, action_id, dict(payload))

