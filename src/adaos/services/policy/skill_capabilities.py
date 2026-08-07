"""Manifest/profile-driven admission for skill SDK capabilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator


class SkillCapabilityAdmissionError(PermissionError):
    """Raised when a skill did not declare or was denied a capability."""


def _tokens(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(item).strip().lower() for item in value if str(item).strip())


def _manifest_path(current_skill: Any) -> Path:
    skill_name = str(getattr(current_skill, "name", "") or "").strip()
    root = Path(getattr(current_skill, "path", "")).expanduser().resolve()
    candidates = (root / "skill.yaml", root / "skills" / skill_name / "skill.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SkillCapabilityAdmissionError(
        f"active skill manifest is unavailable for capability admission: {skill_name}"
    )


def _profile_path(ctx: Any) -> Path:
    return (Path(ctx.paths.state_dir()).resolve() / "capabilities" / "skill_grants.json").resolve()


def _validate_profile(value: Any) -> Mapping[str, Any]:
    schema_path = Path(__file__).resolve().parents[2] / "abi" / "skill.capability_grants.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise SkillCapabilityAdmissionError(
            f"skill capability profile is invalid at {location}: {errors[0].message}"
        )
    return value


@dataclass(frozen=True, slots=True)
class SkillCapabilityDecision:
    subject: str
    capability: str
    declared: bool
    profile_allowed: bool
    allowed: bool
    reason_code: str
    manifest_path: str


def decide_skill_capability(ctx: Any, capability: str) -> SkillCapabilityDecision:
    current = ctx.skill_ctx.get()
    skill_name = str(getattr(current, "name", "") or "").strip().lower()
    if not skill_name:
        raise SkillCapabilityAdmissionError("an active skill context is required")
    requested = str(capability or "").strip().lower()
    if not requested:
        raise ValueError("capability must be non-empty")
    manifest_path = _manifest_path(current)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise SkillCapabilityAdmissionError("skill manifest must contain an object")
    declared = requested in _tokens(payload.get("capabilities"))
    subject = f"skill:{skill_name}"
    profile_allowed = True
    profile = _profile_path(ctx)
    if profile.is_file():
        raw = _validate_profile(json.loads(profile.read_text(encoding="utf-8")))
        entry = dict(dict(raw.get("subjects") or {}).get(subject) or {})
        denied = _tokens(entry.get("deny"))
        allowed = _tokens(entry.get("allow"))
        if requested in denied:
            profile_allowed = False
        elif allowed and requested not in allowed:
            profile_allowed = False
    allowed = declared and profile_allowed
    reason = "allowed" if allowed else "not_declared" if not declared else "profile_denied"
    return SkillCapabilityDecision(
        subject=subject,
        capability=requested,
        declared=declared,
        profile_allowed=profile_allowed,
        allowed=allowed,
        reason_code=reason,
        manifest_path=str(manifest_path),
    )


def require_skill_capability(ctx: Any, capability: str) -> SkillCapabilityDecision:
    decision = decide_skill_capability(ctx, capability)
    if not decision.allowed:
        raise SkillCapabilityAdmissionError(
            f"capability {decision.capability!r} denied for {decision.subject!r}: "
            f"{decision.reason_code}"
        )
    return decision


__all__ = [
    "SkillCapabilityAdmissionError",
    "SkillCapabilityDecision",
    "decide_skill_capability",
    "require_skill_capability",
]
