"""Provider port for durable execution attempts."""

from __future__ import annotations

from typing import Protocol

from adaos.domain.execution import ExecutionAttempt, ExecutionSpec


class ExecutorProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def submit(self, spec: ExecutionSpec, *, idempotency_key: str) -> ExecutionAttempt: ...

    def reconcile(self, attempt_id: str, *, owner_ref: str) -> ExecutionAttempt: ...

    def cancel(self, attempt_id: str, *, owner_ref: str) -> ExecutionAttempt: ...


__all__ = ["ExecutorProvider"]
