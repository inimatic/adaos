from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from adaos.domain import normalize_event_envelope
from adaos.sdk.core.decorators import subscribe
from adaos.services.context_control import ContextControlService


_SKILL_TOPICS = {
    "skills.activated",
    "skills.updated",
    "skills.rolledback",
    "skill.uninstalled",
}
_SCENARIO_TOPICS = {
    "scenarios.synced",
    "scenario.installed",
    "scenario.removed",
}
_PROJECT_TOPICS = {
    "project.updated",
    "project.release.changed",
    "project.release.accepted",
    "project.trial.accepted",
    "changeset.accepted",
}
_PLATFORM_TOPICS = {
    "core.update.status",
    "core.updated",
    "sdk.updated",
    "api.updated",
    "abi.updated",
}
_AUTHORITY_TOPICS = {
    "role.changed",
    "role.policy.changed",
    "access.policy.changed",
    "sensitivity.policy.changed",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        token = _text(payload.get(key))
        if token:
            return token
    return ""


def _event_identity(event: Any) -> tuple[str, str, dict[str, Any]]:
    envelope = normalize_event_envelope(event)
    payload = dict(envelope.payload)
    event_id = _text(envelope.event_id)
    if not event_id:
        canonical = json.dumps(
            {
                "type": envelope.type,
                "source": envelope.source,
                "ts": envelope.ts,
                "payload": payload,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        event_id = hashlib.sha256(canonical).hexdigest()
    return envelope.type, f"event:{event_id}", payload


def context_invalidation_subjects(topic: str, payload: Mapping[str, Any]) -> list[str]:
    subjects: list[str] = []
    if topic in _SKILL_TOPICS:
        skill_id = _first(payload, "skill_name", "skill_id", "name", "id")
        if skill_id:
            subjects.append(f"skill:{skill_id}")
    elif topic in _SCENARIO_TOPICS:
        scenario_id = _first(payload, "scenario_id", "scenario", "name", "id")
        if scenario_id:
            subjects.append(f"scenario:{scenario_id}")
    elif topic in _PROJECT_TOPICS:
        project_id = _first(payload, "project_id", "project", "name", "id")
        if project_id:
            subjects.append(f"project:{project_id}")
        component_ref = _first(payload, "component_ref")
        if component_ref:
            subjects.append(component_ref)
    elif topic in _PLATFORM_TOPICS:
        subjects.append("platform:adaos")
        surface_id = _first(payload, "sdk_id", "api_id", "abi_id", "component_ref")
        if surface_id and ":" in surface_id:
            subjects.append(surface_id)
    elif topic in _AUTHORITY_TOPICS:
        role_id = _first(payload, "role_id", "role", "policy_id", "id")
        if role_id:
            prefix = "policy" if "policy" in topic else "role"
            subjects.append(f"{prefix}:{role_id}")
    project_id = _first(payload, "project_id")
    if project_id:
        subjects.append(f"project:{project_id}")
    return list(dict.fromkeys(subjects))


def record_context_invalidation_event(
    event: Any,
    *,
    service: ContextControlService | None = None,
) -> list[dict[str, Any]]:
    topic, event_ref, payload = _event_identity(event)
    subjects = context_invalidation_subjects(topic, payload)
    if not subjects:
        return []
    current_digest = _first(
        payload,
        "release_digest",
        "project_digest",
        "source_digest",
        "sdk_digest",
        "api_digest",
        "abi_digest",
        "policy_digest",
    )
    context = service or ContextControlService()
    return [
        context.invalidate(
            subject_ref=subject_ref,
            reason=topic,
            event_ref=event_ref,
            source_digest=current_digest or None,
            edge_type="audience_view" if topic in _AUTHORITY_TOPICS else None,
        )
        for subject_ref in subjects
    ]


@subscribe("skills.activated")
@subscribe("skills.updated")
@subscribe("skills.rolledback")
@subscribe("skill.uninstalled")
@subscribe("scenarios.synced")
@subscribe("scenario.installed")
@subscribe("scenario.removed")
@subscribe("project.updated")
@subscribe("project.release.changed")
@subscribe("project.release.accepted")
@subscribe("project.trial.accepted")
@subscribe("changeset.accepted")
@subscribe("core.update.status")
@subscribe("core.updated")
@subscribe("sdk.updated")
@subscribe("api.updated")
@subscribe("abi.updated")
@subscribe("role.changed")
@subscribe("role.policy.changed")
@subscribe("access.policy.changed")
@subscribe("sensitivity.policy.changed")
def on_context_source_changed(event: Any) -> None:
    record_context_invalidation_event(event)


__all__ = [
    "context_invalidation_subjects",
    "on_context_source_changed",
    "record_context_invalidation_event",
]
