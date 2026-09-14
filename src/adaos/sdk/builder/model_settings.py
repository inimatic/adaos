"""Root-advertised execution choices for Builder's separate Codex stage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.sdk.developer import prompt_context
from adaos.sdk.llm.llm_client import list_llm_models
from adaos.services.codex_profiles import normalize_codex_profile
from adaos.services.codex_catalog import local_codex_models


def codex_options(object_type: str, object_id: str) -> dict[str, Any]:
    current = prompt_context.get(object_type, object_id).get("builder_codex_profile")
    try:
        payload = list_llm_models(timeout=5, scope="automation")
    except Exception as exc:
        return {"ok": False, "status": "unavailable", "current": current, "options": [],
                "reason": f"codex_catalog_unavailable:{type(exc).__name__}"}
    if not isinstance(payload, Mapping) or payload.get("scope") != "automation":
        return {"ok": False, "status": "unavailable", "current": current, "options": [],
                "reason": "root_codex_catalog_not_supported"}
    options = []
    local = local_codex_models()
    local_options = {row["id"]: row for row in local.get("models") or []}
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping) or row.get("scope") != "automation":
            continue
        try:
            profile = normalize_codex_profile({"model": row.get("id"), "provider": row.get("provider"),
                                               "reasoning_effort": row.get("default_reasoning_effort")})
        except ValueError:
            continue
        local_option = local_options.get(profile["model"])
        options.append({"id": profile["model"], "label": str(row.get("label") or profile["model"]),
                        **profile, "available": row.get("available") is True and local_option is not None,
                        "root_available": row.get("available") is True,
                        "local_available": local_option is not None,
                        "availability_reason": None if local_option else local.get("reason") or "not_available_in_local_codex",
                        "supported_reasoning_efforts": (local_option or {}).get("supported_reasoning_efforts", []),
                        "default": row.get("default") is True,
                        "pricing": row.get("pricing") if isinstance(row.get("pricing"), Mapping) else None})
    available = any(option["available"] for option in options)
    return {"ok": available, "status": "ready" if available else "unavailable", "current": current,
            "value": (current or {}).get("model"), "options": options, "source": "root+codex:model/list",
            "local_discovery": {key: value for key, value in local.items() if key != "models"}}


def set_codex_profile(object_type: str, object_id: str, *, model: str, reasoning_effort: str | None = None) -> dict[str, Any]:
    catalog = codex_options(object_type, object_id)
    option = next((row for row in catalog["options"] if row["id"] == model and row["available"]), None)
    if option is None:
        raise ValueError("The selected Codex model must be allowed by Root and available in the local Codex account")
    profile = normalize_codex_profile({**option, "reasoning_effort": reasoning_effort or option.get("reasoning_effort")})
    supported = option.get("supported_reasoning_efforts")
    if supported and profile.get("reasoning_effort") not in supported:
        raise ValueError("The selected reasoning effort is unavailable for this Codex model")
    return prompt_context.set_preferences(object_type, object_id, codex_profile=profile)


def require_local_profile(profile: Mapping[str, Any]) -> None:
    normalized = normalize_codex_profile(profile)
    catalog = local_codex_models(refresh=True)
    option = next((row for row in catalog["models"] if row["id"] == normalized["model"]), None)
    if not option:
        raise ValueError(f"codex_model_unavailable: {normalized['model']}; {catalog.get('reason') or 'not listed by local Codex'}")
    efforts = option.get("supported_reasoning_efforts")
    if efforts and normalized.get("reasoning_effort") and normalized["reasoning_effort"] not in efforts:
        raise ValueError("codex_reasoning_effort_unavailable")
