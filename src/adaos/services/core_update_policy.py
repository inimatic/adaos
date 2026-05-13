from __future__ import annotations

import os
from typing import Any

from adaos.services.runtime_environment import normalize_env_type


_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def current_env_type(settings: Any | None = None) -> str:
    raw = _resolve_raw_env_type(settings)
    return normalize_env_type(raw)


def core_update_reactions_disabled_reason(settings: Any | None = None) -> str | None:
    if _api_serve_core_update_disabled():
        return "dev_api_serve_core_update_sync_disabled"
    if current_env_type(settings) != "dev":
        return None
    if _dev_core_update_allowed():
        return None
    return "dev_core_update_reactions_disabled"


def _api_serve_core_update_disabled() -> bool:
    launch_mode = str(os.getenv("ADAOS_RUNTIME_LAUNCH_MODE") or "").strip().lower()
    if launch_mode != "api_serve":
        return False
    if _dev_core_update_allowed():
        return False
    return not _truthy(os.getenv("ADAOS_API_SERVE_ALLOW_CORE_UPDATE"))


def _dev_core_update_allowed() -> bool:
    return _truthy(os.getenv("ADAOS_DEV_ALLOW_CORE_UPDATE")) or _truthy(
        os.getenv("ADAOS_API_SERVE_ALLOW_CORE_UPDATE")
    )


def _resolve_raw_env_type(settings: Any | None = None) -> str:
    if settings is not None:
        candidate = str(getattr(settings, "env_type", "") or "").strip()
        if candidate:
            return candidate
    for key in ("ENV_TYPE", "ADAOS_ENV", "NODE_ENV"):
        candidate = str(os.getenv(key) or "").strip()
        if candidate:
            return candidate
    try:
        from adaos.services.agent_context import get_ctx

        ctx_settings = getattr(get_ctx(), "settings", None)
        candidate = str(getattr(ctx_settings, "env_type", "") or "").strip()
        if candidate:
            return candidate
    except Exception:
        pass
    return "prod"


__all__ = ["core_update_reactions_disabled_reason", "current_env_type"]
