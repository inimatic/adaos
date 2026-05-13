from __future__ import annotations

import os
from typing import Any


_DEV_ENV_VALUES = {"dev", "development", "local", "debug", "test"}
_PROD_ENV_VALUES = {"prod", "production", "release", "stable"}


def normalize_env_type(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in _DEV_ENV_VALUES:
        return "dev"
    if token in _PROD_ENV_VALUES:
        return "prod"
    return "prod"


def runtime_environment_payload(settings: Any | None = None) -> dict[str, Any]:
    raw_env_type = _resolve_raw_env_type(settings)
    env_type = normalize_env_type(raw_env_type)
    payload: dict[str, Any] = {
        "envType": env_type,
        "mode": env_type,
        "debug": env_type == "dev",
        "source": "settings.env_type" if str(getattr(settings, "env_type", "") or "").strip() else "ENV_TYPE",
    }
    profile = str(getattr(settings, "profile", "") or "").strip()
    if profile:
        payload["profile"] = profile
    raw = str(raw_env_type or "").strip().lower()
    if raw and raw != env_type:
        payload["rawEnvType"] = raw
    return payload


def _resolve_raw_env_type(settings: Any | None = None) -> str:
    if settings is None:
        settings = _get_current_settings()
    candidate = str(getattr(settings, "env_type", "") or "").strip()
    if candidate:
        return candidate
    for key in ("ENV_TYPE", "ADAOS_ENV", "NODE_ENV"):
        candidate = str(os.getenv(key) or "").strip()
        if candidate:
            return candidate
    return "prod"


def _get_current_settings() -> Any | None:
    try:
        from adaos.services.agent_context import get_ctx

        return getattr(get_ctx(), "settings", None)
    except Exception:
        return None
