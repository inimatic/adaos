"""SDK facade for interacting with the secrets service."""

from __future__ import annotations

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

    service = ctx.secrets
    try:
        from adaos.services.crypto.secrets_service import SecretsService

        if not isinstance(service, SecretsService):
            return service
        current = ctx.skill_ctx.get()
        current_path = getattr(current, "path", None) if current is not None else None
        if current_path is None:
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
    """Return a secret by name or the provided default when missing."""

    ctx = require_cap("secrets.read")
    return _active_secrets_service(ctx).get(name, default=default)


def set(name: str, value: str) -> None:
    """Store or update a secret value for the active skill."""

    ctx = require_cap("secrets.write")
    _active_secrets_service(ctx).put(name, value)


def delete(name: str) -> None:
    """Remove a stored secret value for the active skill."""

    ctx = require_cap("secrets.write")
    _active_secrets_service(ctx).delete(name)


# Backwards-compatible aliases for older skills.
read = get
write = set


__all__ = ["get", "set", "delete", "read", "write"]
