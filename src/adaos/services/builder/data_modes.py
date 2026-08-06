from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from jsonschema import Draft202012Validator

BINDING_PROFILE_SCHEMA = "adaos.builder.binding_profile.v1"
_MODES = {"mock", "fixture", "sandbox", "live_readonly", "live"}


class BuilderDataModeError(ValueError):
    """Raised when a Preview binding would cross the declared data boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _schema() -> dict[str, Any]:
    from pathlib import Path
    import json

    path = Path(__file__).resolve().parents[2] / "abi" / "builder.binding_profile.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    profile = copy.deepcopy(dict(value))
    profile.setdefault("schema", BINDING_PROFILE_SCHEMA)
    profile.setdefault("capabilities", [])
    profile.setdefault("sensitivity", "internal")
    profile.setdefault("read_policy", "fixture" if profile.get("mode") in {"mock", "fixture"} else "sandbox")
    profile.setdefault("write_policy", "none")
    profile.setdefault("expires_at", None)
    profile.setdefault("redaction", "policy")
    profile.setdefault("implementation_mappings", [])
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(profile),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise BuilderDataModeError(f"invalid binding profile at {location}: {errors[0].message}")
    mode = str(profile["mode"])
    if mode not in _MODES:
        raise BuilderDataModeError(f"unsupported binding mode: {mode}")
    if mode in {"mock", "fixture", "live_readonly"} and profile["write_policy"] != "none":
        raise BuilderDataModeError(f"{mode} binding must not permit writes")
    if mode == "live_readonly" and profile["read_policy"] != "scoped_live":
        raise BuilderDataModeError("live_readonly binding requires scoped_live read policy")
    if mode == "live" and profile["write_policy"] != "scoped_live":
        raise BuilderDataModeError("live binding requires an explicit scoped_live write policy")
    return profile


def normalize_binding_state(value: Any, *, project_ref: str) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    profiles: dict[str, dict[str, Any]] = {}
    for item in raw.get("profiles") or []:
        if isinstance(item, Mapping):
            profile = normalize_profile(item)
            profiles[profile["profile_id"]] = profile
    if not profiles:
        default = normalize_profile(
            {
                "profile_id": "default-mock",
                "mode": "mock",
                "logical_schema_ref": f"schema:{project_ref}:prototype",
                "source_ref": f"mock:{project_ref}:generated",
                "owner": "builder",
            }
        )
        profiles[default["profile_id"]] = default
    selected = str(raw.get("selected_profile_id") or "").strip()
    if selected not in profiles:
        selected = next(iter(profiles))
    return {
        "schema": "adaos.builder.binding_state.v1",
        "selected_profile_id": selected,
        "selected_mode": profiles[selected]["mode"],
        "profiles": list(profiles.values())[:100],
        "generation": max(0, int(raw.get("generation") or 0)),
        "updated_at": str(raw.get("updated_at") or _now()),
    }


def put_profile(state: Mapping[str, Any], value: Mapping[str, Any], *, now: str | None = None) -> dict[str, Any]:
    result = copy.deepcopy(dict(state))
    profile = normalize_profile(value)
    profiles = [dict(item) for item in result.get("profiles") or [] if isinstance(item, Mapping)]
    existing = next((item for item in profiles if item.get("profile_id") == profile["profile_id"]), None)
    if existing is None:
        profiles.append(profile)
    else:
        existing.clear()
        existing.update(profile)
    result["profiles"] = profiles[:100]
    result["generation"] = int(result.get("generation") or 0) + 1
    result["updated_at"] = now or _now()
    return result


def select_profile(
    state: Mapping[str, Any],
    profile_id: str,
    *,
    phase: str,
    confirmed: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(state))
    target = next(
        (item for item in result.get("profiles") or [] if item.get("profile_id") == profile_id),
        None,
    )
    if not isinstance(target, Mapping):
        raise BuilderDataModeError(f"unknown binding profile: {profile_id}")
    mode = str(target.get("mode") or "")
    if phase == "prototype" and mode == "live":
        raise BuilderDataModeError("live modifying data is forbidden in Prototype")
    if mode in {"sandbox", "live_readonly"} and not confirmed:
        raise BuilderDataModeError(f"{mode} Preview requires explicit confirmation")
    result["selected_profile_id"] = profile_id
    result["selected_mode"] = mode
    result["generation"] = int(result.get("generation") or 0) + 1
    result["updated_at"] = now or _now()
    return result


def implementation_mapping_report(state: Mapping[str, Any]) -> dict[str, Any]:
    profile = next(
        (
            dict(item)
            for item in state.get("profiles") or []
            if item.get("profile_id") == state.get("selected_profile_id")
        ),
        {},
    )
    mappings = [dict(item) for item in profile.get("implementation_mappings") or [] if isinstance(item, Mapping)]
    missing = [item["logical_ref"] for item in mappings if item.get("status") == "missing"]
    return {
        "schema": "adaos.builder.implementation_mapping_report.v1",
        "profile_id": profile.get("profile_id"),
        "mode": profile.get("mode"),
        "mapping_count": len(mappings),
        "missing": missing,
        "ready": not missing,
    }
