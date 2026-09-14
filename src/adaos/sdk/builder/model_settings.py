"""Root-advertised execution choices for Builder's separate Codex stage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.sdk.developer import prompt_context
from adaos.sdk.llm.llm_client import list_llm_models
from adaos.services.codex_profiles import normalize_codex_profile


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
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping) or row.get("scope") != "automation":
            continue
        try:
            profile = normalize_codex_profile({"model": row.get("id"), "provider": row.get("provider"),
                                               "reasoning_effort": row.get("default_reasoning_effort")})
        except ValueError:
            continue
        options.append({"id": profile["model"], "label": str(row.get("label") or profile["model"]),
                        **profile, "available": row.get("available") is True,
                        "default": row.get("default") is True,
                        "pricing": row.get("pricing") if isinstance(row.get("pricing"), Mapping) else None})
    return {"ok": bool(options), "status": "ready" if options else "unavailable", "current": current,
            "value": (current or {}).get("model"), "options": options, "source": "root"}


def set_codex_profile(object_type: str, object_id: str, *, model: str, reasoning_effort: str | None = None) -> dict[str, Any]:
    catalog = codex_options(object_type, object_id)
    option = next((row for row in catalog["options"] if row["id"] == model and row["available"]), None)
    if option is None:
        raise ValueError("The selected Codex model is not advertised as available by Root")
    profile = normalize_codex_profile({**option, "reasoning_effort": reasoning_effort or option.get("reasoning_effort")})
    return prompt_context.set_preferences(object_type, object_id, codex_profile=profile)
