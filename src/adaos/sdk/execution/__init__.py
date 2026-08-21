"""Provider-neutral, owner-scoped execution SDK for skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.domain.execution import (
    AcceleratorAllocation,
    AcceleratorInventory,
    CheckpointManifest,
    ExecutionAttempt,
    ExecutionBudget,
    ExecutionDeterminism,
    ExecutionNetworkPolicy,
    ExecutionResourceRequest,
    ExecutionSpec,
    PreemptionPolicy,
)
from adaos.domain.runtime_bindings import ContentRef
from adaos.sdk.core._ctx import require_ctx
from adaos.services.execution.service import ExecutionService
from adaos.services.policy.skill_capabilities import require_skill_capability


def _admitted() -> Any:
    ctx = require_ctx("sdk.execution")
    require_skill_capability(ctx, "execution.jobs")
    return ctx


def spec(
    spec_id: str,
    command: Sequence[str],
    *,
    working_directory: str | Path | None = None,
    data_owner_ref: str | None = None,
    trial_id: str | None = None,
    run_id: str | None = None,
    sample_generation: int = 0,
    package_ref: ContentRef | None = None,
    code_digest: str | None = None,
    environment_digest: str | None = None,
    environment: Mapping[str, str] | None = None,
    secret_refs: Sequence[str] = (),
    resources: ExecutionResourceRequest | None = None,
    network: ExecutionNetworkPolicy | None = None,
    determinism: ExecutionDeterminism | None = None,
    budget: ExecutionBudget | None = None,
    inputs: Sequence[ContentRef] = (),
    expected_outputs: Sequence[str] = (),
    checkpoint: CheckpointManifest | None = None,
    preemption: PreemptionPolicy | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ExecutionSpec:
    ctx = _admitted()
    current = ctx.skill_ctx.get()
    skill_name = str(getattr(current, "name", "") or "").strip()
    if not skill_name:
        raise RuntimeError("execution SDK requires an active skill context")
    return ExecutionSpec(
        spec_id=spec_id,
        owner_ref=f"skill:{skill_name}",
        data_owner_ref=data_owner_ref,
        command=tuple(str(item) for item in command),
        working_directory=str(working_directory or current.path),
        trial_id=trial_id,
        run_id=run_id,
        sample_generation=sample_generation,
        package_ref=package_ref,
        code_digest=code_digest,
        environment_digest=environment_digest,
        environment=dict(environment or {}),
        secret_refs=tuple(secret_refs),
        resources=resources or ExecutionResourceRequest(),
        network=network or ExecutionNetworkPolicy(),
        determinism=determinism or ExecutionDeterminism(),
        budget=budget or ExecutionBudget(),
        inputs=tuple(inputs),
        expected_outputs=tuple(expected_outputs),
        checkpoint=checkpoint,
        preemption=preemption or PreemptionPolicy(),
        metadata=dict(metadata or {}),
    )


def submit(value: ExecutionSpec, *, idempotency_key: str) -> ExecutionAttempt:
    ctx = _admitted()
    return ExecutionService(ctx).submit(value, idempotency_key=idempotency_key)


def capabilities() -> dict[str, Any]:
    """Describe the executor currently admitted for the calling skill."""

    ctx = _admitted()
    return ExecutionService(ctx).capabilities()


def reconcile(attempt_id: str) -> ExecutionAttempt:
    ctx = _admitted()
    return ExecutionService(ctx).reconcile(attempt_id)


def cancel(attempt_id: str) -> ExecutionAttempt:
    ctx = _admitted()
    return ExecutionService(ctx).cancel(attempt_id)


__all__ = [
    "AcceleratorAllocation",
    "AcceleratorInventory",
    "CheckpointManifest",
    "ContentRef",
    "ExecutionAttempt",
    "ExecutionBudget",
    "ExecutionDeterminism",
    "ExecutionNetworkPolicy",
    "ExecutionResourceRequest",
    "ExecutionSpec",
    "PreemptionPolicy",
    "cancel",
    "capabilities",
    "reconcile",
    "spec",
    "submit",
]
