"""SDK facade for interacting with the secrets service."""

from __future__ import annotations

import os
from typing import Optional

from adaos.sdk.core._cap import require_cap


def _active_secrets_service(ctx):
    """Bind production SDK calls to the context-local skill runtime.

    ``AgentContext`` is process-wide while the current skill is a ContextVar.
    Older runtime paths temporarily replace ``ctx.secrets`` around a tool call;
    concurrent calls can therefore observe another skill's backend.  Runtime
    skills use the owner-scoped data root instead.  Lightweight test doubles
    keep their injected backend unchanged.
    """

    from adaos.services.applications.runtime_credentials import ApplicationRuntimeCredentials

    if ApplicationRuntimeCredentials.declared(ctx):
        return ApplicationRuntimeCredentials(ctx)
    service = ctx.secrets
    try:
        from adaos.services.crypto.secrets_service import SecretsService

        if not isinstance(service, SecretsService):
            return service
        current = ctx.skill_ctx.get()
        current_path = getattr(current, "path", None) if current is not None else None
        isolated_data_root = str(
            os.getenv("ADAOS_SKILL_INTERNAL_DATA_ROOT") or ""
        ).strip()
        isolated_skill_name = str(os.getenv("ADAOS_SKILL_NAME") or "").strip()
        if current_path is None and not (isolated_data_root and isolated_skill_name):
            return service

        from adaos.sdk.data.skill_env import skill_data_root_path
        from adaos.services.skill.secrets_backend import SkillSecretsBackend

        expected_path = skill_data_root_path() / "files" / "secrets.json"
        backend = getattr(service, "backend", None)
        backend_path = getattr(backend, "_path", None)
        if isinstance(backend, SkillSecretsBackend) and backend_path is not None:
            try:
                if (
                    backend_path.expanduser().resolve()
                    == expected_path.expanduser().resolve()
                ):
                    return service
            except Exception:
                pass
        return SecretsService(SkillSecretsBackend(expected_path), ctx.caps)
    except Exception:
        return service


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a secret slot or default. Declared configuration.credentials slots
    require a verified local owner and secrets.read; values resolve from the
    node vault, while DEV is isolated. No secret value belongs in model context.
    """

    ctx = require_cap("secrets.read")
    return _active_secrets_service(ctx).get(name, default=default)


def set(name: str, value: str) -> None:
    """Bind a new secret value for the active skill. Declared credential slots
    keep values in the node vault and inherit/adopt bindings across Beta/Stable.
    Requires secrets.write; Beta rebinding preserves the previous Stable value.
    """

    ctx = require_cap("secrets.write")
    _active_secrets_service(ctx).put(name, value)


def delete(name: str) -> None:
    """Revoke the selected credential and remove its binding. A retained snapshot
    cannot resurrect a revoked value. Requires secrets.write for declared slots.
    """

    ctx = require_cap("secrets.write")
    _active_secrets_service(ctx).delete(name)


# Backwards-compatible aliases for older skills.
read = get
write = set


__all__ = ["get", "set", "delete", "read", "write"]
