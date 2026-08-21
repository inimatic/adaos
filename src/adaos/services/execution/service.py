"""Owner-guarded execution service used by the public skill SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.execution import ExecutionAttempt, ExecutionSpec
from adaos.domain.ownership import OwnershipIsolationError, validate_owner_ref
from adaos.services.execution.local import LocalProcessExecutor
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.skill.data_paths import (
    resolve_installed_skill_data_root,
    resolve_skill_data_root,
)
from adaos.services.skill.runtime import find_skill_dir


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
        owner_ref, current = self._current()
        if spec.owner_ref != owner_ref:
            raise OwnershipIsolationError(
                f"execution spec belongs to {spec.owner_ref!r}, not current owner {owner_ref!r}"
            )
        data_owner_ref = validate_owner_ref(spec.data_owner_ref or owner_ref)
        data_root = resolve_skill_data_root(self._ctx, current)
        skill_root = Path(current.path).expanduser().resolve()
        data_owner_name = str(getattr(current, "name", "") or "").strip()
        if data_owner_ref != owner_ref:
            require_skill_capability(self._ctx, "execution.jobs.delegate_data_owner")
            prefix, separator, target_name = data_owner_ref.partition(":")
            if separator != ":" or prefix != "skill" or not target_name:
                raise OwnershipIsolationError(
                    "delegated execution data owner must identify an installed skill"
                )
            if spec.package_ref is None or spec.package_ref.owner_ref != data_owner_ref:
                raise OwnershipIsolationError(
                    "delegated execution requires a package owned by the data owner"
                )
            data_owner_name = target_name
            data_root = resolve_installed_skill_data_root(self._ctx, target_name)
            skill_root = find_skill_dir(target_name, ctx=self._ctx).resolve()
            cwd = Path(spec.working_directory).expanduser().resolve()
            if not any(self._is_under(cwd, root) for root in (data_root, skill_root)):
                raise OwnershipIsolationError(
                    "delegated execution working directory is outside the data owner's runtime"
                )
        runtime_environment = {
            "ADAOS_SKILL_ENV_PATH": str(data_root / "db" / "skill_env.json"),
            "ADAOS_SKILL_MEMORY_PATH": str(data_root / "db" / "skill_env.json"),
            "ADAOS_SKILL_INTERNAL_DATA_ROOT": str(data_root),
            "ADAOS_SKILL_INTERNAL_ACTIVE_PATH": str(data_root),
            "ADAOS_SKILL_INTERNAL_TARGET_PATH": str(data_root),
            "ADAOS_SKILL_NAME": data_owner_name,
            "ADAOS_SKILL_PACKAGE": f"skills.{data_owner_name}",
            "ADAOS_SKILL_ROOT": str(skill_root),
            "ADAOS_SKILL_MODE": "runtime",
        }
        return self._provider().submit(
            spec,
            idempotency_key=idempotency_key,
            runtime_environment=runtime_environment,
        )

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def capabilities(self) -> dict[str, Any]:
        """Return the active provider contract without exposing provider internals.

        Capability discovery is deliberately owner-scoped even though the current
        payload contains no secrets. This keeps admission checks on the same SDK
        boundary as submit/reconcile when executor brokering is introduced.
        """

        self._current()
        return self._provider().capabilities.to_dict()

    def reconcile(self, attempt_id: str) -> ExecutionAttempt:
        owner_ref, _ = self._current()
        return self._provider().reconcile(attempt_id, owner_ref=owner_ref)

    def cancel(self, attempt_id: str) -> ExecutionAttempt:
        owner_ref, _ = self._current()
        return self._provider().cancel(attempt_id, owner_ref=owner_ref)


__all__ = ["ExecutionService"]
