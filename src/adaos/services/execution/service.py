"""Owner-guarded execution service used by the public skill SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.execution import ExecutionAttempt, ExecutionSpec
from adaos.domain.ownership import OwnershipIsolationError, validate_owner_ref
from adaos.services.execution.local import LocalProcessExecutor
from adaos.services.skill.data_paths import resolve_skill_data_root


class ExecutionService:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def _current(self) -> tuple[str, Any]:
        current = self._ctx.skill_ctx.get()
        name = str(getattr(current, "name", "") or "").strip()
        if not name:
            raise OwnershipIsolationError("execution SDK requires an active skill context")
        return validate_owner_ref(f"skill:{name}"), current

    def _provider(self) -> Any:
        provider = getattr(self._ctx, "execution_provider", None)
        if provider is not None:
            return provider
        owner_ref, current = self._current()
        del owner_ref
        data_root = resolve_skill_data_root(self._ctx, current)
        provider = LocalProcessExecutor(
            state_root=data_root / "providers",
            allowed_roots=(Path(current.path), data_root),
        )
        object.__setattr__(self._ctx, "execution_provider", provider)
        return provider

    def submit(self, spec: ExecutionSpec, *, idempotency_key: str) -> ExecutionAttempt:
        owner_ref, _ = self._current()
        if spec.owner_ref != owner_ref:
            raise OwnershipIsolationError(
                f"execution spec belongs to {spec.owner_ref!r}, not current owner {owner_ref!r}"
            )
        return self._provider().submit(spec, idempotency_key=idempotency_key)

    def reconcile(self, attempt_id: str) -> ExecutionAttempt:
        owner_ref, _ = self._current()
        return self._provider().reconcile(attempt_id, owner_ref=owner_ref)

    def cancel(self, attempt_id: str) -> ExecutionAttempt:
        owner_ref, _ = self._current()
        return self._provider().cancel(attempt_id, owner_ref=owner_ref)


__all__ = ["ExecutionService"]
