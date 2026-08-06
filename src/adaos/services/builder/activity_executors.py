from __future__ import annotations

from adaos.services.workflow_execution import WorkflowExecutorRegistration
from adaos.services.workflow_registry import platform_workflow_adapter_registry


BUILDER_LIFECYCLE_EXECUTOR_ID = "adaos.builder.lifecycle"
BUILDER_LIFECYCLE_ACTIVITIES = (
    "builder.codex.run",
    "builder.prototype.derive",
    "builder.trial.activate",
    "builder.publication.publish",
)


def builder_lifecycle_executor_registrations() -> tuple[WorkflowExecutorRegistration, ...]:
    """Return immutable readiness bindings for the in-process Builder bridge."""

    adapters = platform_workflow_adapter_registry()
    registrations: list[WorkflowExecutorRegistration] = []
    for adapter_id in BUILDER_LIFECYCLE_ACTIVITIES:
        contract = adapters.get("activity", adapter_id)
        if contract is None:
            continue
        registrations.append(
            WorkflowExecutorRegistration(
                adapter_id=adapter_id,
                contract_digest=str(contract["contract_digest"]),
                executor_id=BUILDER_LIFECYCLE_EXECUTOR_ID,
            )
        )
    return tuple(registrations)


__all__ = [
    "BUILDER_LIFECYCLE_ACTIVITIES",
    "BUILDER_LIFECYCLE_EXECUTOR_ID",
    "builder_lifecycle_executor_registrations",
]
