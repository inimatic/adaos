from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services import conversation_store
from adaos.services.conversation_action_semantics import (
    ActionSemanticsError,
    normalize_action_semantics,
)


INTERACTION_SCHEMA = "adaos.conversation.interaction.v1"
INTERACTION_RESPONSE_SCHEMA = "adaos.conversation.interaction_response.v1"
CAPABILITY_PROFILE_SCHEMA = "adaos.conversation.channel_capability_profile.v1"
INTERACTION_REQUIREMENTS_SCHEMA = "adaos.conversation.interaction_requirements.v1"
INTERACTION_PRESENTATION_SCHEMA = "adaos.conversation.interaction_presentation.v1"
INTERACTION_PRESENTATION_PLAN_SCHEMA = "adaos.conversation.interaction_presentation_plan.v1"
SEMANTIC_MESSAGE_SCHEMA = "adaos.conversation.semantic_message.v1"
INTERACTION_DISPATCH_SCHEMA = "adaos.conversation.interaction_dispatch.v1"
_PENDING_STATUSES = {"created", "projected", "awaiting_input", "partially_answered", "validation_failed"}
_TERMINAL_STATUSES = {"completed", "expired", "cancelled", "superseded"}
_NON_MUTATING_RISK_CLASSES = {"read", "none"}
_STEP_UP_RISK_CLASSES = {"external", "destructive", "admin", "privileged", "registry"}
_MESSAGE_PARAMETER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]{0,63})\}")
_INTERACTION_QUERY_FIELD_MASKS: dict[str, tuple[str, ...]] = {
    "summary": (
        "interaction_id",
        "conversation_id",
        "owner",
        "status",
        "generation",
        "prompt",
        "expires_at",
        "updated_at",
    ),
    "detail": (
        "interaction_id",
        "conversation_id",
        "thread_id",
        "owner",
        "prompt",
        "prompt_message",
        "locale_context",
        "input_spec",
        "actions",
        "status",
        "generation",
        "task_ref",
        "workflow_ref",
        "expires_at",
        "created_at",
        "updated_at",
        "completed_at",
    ),
    "audit": (
        "interaction_id",
        "conversation_id",
        "thread_id",
        "owner",
        "status",
        "generation",
        "task_ref",
        "workflow_ref",
        "expires_at",
        "created_at",
        "updated_at",
        "completed_at",
    ),
}


class ConversationInteractionError(ValueError):
    """Raised when an interaction cannot be safely created or answered."""


@dataclass(frozen=True, slots=True)
class InteractionHandle:
    interaction_id: str
    conversation_id: str
    status: str
    generation: int
    task_ref: dict[str, Any] | None
    workflow_ref: dict[str, Any] | None
    durable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "adaos.conversation.interaction_handle.v1",
            "interaction_id": self.interaction_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "generation": self.generation,
            "task_ref": copy.deepcopy(self.task_ref),
            "workflow_ref": copy.deepcopy(self.workflow_ref),
            "durable": self.durable,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def interaction_semantic_digest(interaction: Mapping[str, Any]) -> str:
    """Digest the material a person reviews, excluding delivery/runtime state."""

    metadata = dict(interaction.get("metadata") or {})
    decision_context = {
        key: copy.deepcopy(metadata.get(key))
        for key in (
            "decision_context", "subject_ref", "domain_ref", "policy_ref",
            "validity_conditions",
        )
        if metadata.get(key) is not None
    }
    actions = []
    for item in interaction.get("actions") or []:
        if not isinstance(item, Mapping):
            continue
        actions.append(
            {
                key: copy.deepcopy(item.get(key))
                for key in (
                    "action_id", "command", "value", "risk",
                    "confirmation_required", "assurance", "semantics",
                    "target_ref", "expected_generation", "principal_scope",
                    "command_context_ref",
                )
            }
        )
    return _canonical_digest(
        {
            "conversation_id": interaction.get("conversation_id"),
            "owner": interaction.get("owner"),
            "prompt": interaction.get("prompt"),
            "prompt_ref": interaction.get("prompt_ref"),
            "prompt_message": interaction.get("prompt_message"),
            "input_spec": interaction.get("input_spec"),
            "actions": actions,
            "requirements": {
                key: copy.deepcopy(dict(interaction.get("requirements") or {}).get(key))
                for key in (
                    "version", "required", "optional", "limits", "fallbacks",
                    "fail_closed", "semantic_equivalence_required",
                    "permission_boundary", "business_availability_boundary",
                )
            },
            "task_ref": interaction.get("task_ref"),
            "workflow_ref": interaction.get("workflow_ref"),
            "expires_at": interaction.get("expires_at"),
            "decision_context": decision_context,
        }
    )


def _schema(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    if name == CAPABILITY_PROFILE_SCHEMA:
        record.setdefault("permission_boundary", "separate")
        record.setdefault("business_availability_boundary", "separate")
    elif name == INTERACTION_SCHEMA:
        if "requirements" not in record:
            record["requirements"] = {
                "schema": INTERACTION_REQUIREMENTS_SCHEMA,
                "requirements_id": f"requirements:{record.get('interaction_id') or 'unknown'}",
                "version": 1,
                "required": list(record.get("required_capabilities") or []),
                "optional": list(record.get("optional_capabilities") or []),
                "limits": {},
                "fallbacks": list(record.get("fallbacks") or []),
                "fail_closed": True,
                "semantic_equivalence_required": True,
                "permission_boundary": "separate",
                "business_availability_boundary": "separate",
            }
        # Persisted v1 records created before action semantics remain readable,
        # but are deliberately classified as custom.  A standard preset is
        # never inferred from an old label, id, or command.
        for action in record.get("actions") or []:
            if isinstance(action, dict) and "semantics" not in action:
                action["semantics"] = normalize_action_semantics(action)
            elif isinstance(action, dict) and isinstance(action.get("semantics"), dict):
                # ``schedule`` was added to the v1 action-semantics envelope
                # after the other fields had already shipped.  Preserve
                # readability of those persisted records without inferring a
                # snooze policy that the producer never declared.
                action["semantics"].setdefault("schedule", None)
    elif name == INTERACTION_PRESENTATION_SCHEMA and "plan" not in record:
        record["plan"] = {
            "schema": INTERACTION_PRESENTATION_PLAN_SCHEMA,
            "plan_id": f"plan:{record.get('presentation_id') or 'unknown'}",
            "interaction_id": str(record.get("interaction_id") or "unknown"),
            "interaction_generation": int(record.get("interaction_generation") or 0),
            "profile_id": str(record.get("profile_id") or "unknown"),
            "profile_version": max(1, int(record.get("profile_version") or 1)),
            "requirements_id": f"requirements:{record.get('interaction_id') or 'unknown'}",
            "selected_mode": str(record.get("mode") or "unsupported"),
            "supported": bool(record.get("supported")),
            "reason_code": str(record.get("reason_code") or "legacy_presentation"),
            "missing_required": [],
            "fallback_used": None,
            "semantic_equivalent": bool(record.get("supported")),
            "limits_applied": {},
            "renegotiate_on_profile_change": True,
        }
    errors = sorted(
        Draft202012Validator(_schema(name)).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise ConversationInteractionError(
            f"{name} validation failed at {location}: {errors[0].message}"
        )
    return record


def _workflow_command_executor_ready(command: Mapping[str, Any]) -> bool:
    risk = dict(command.get("risk") or {})
    risk_class = str(risk.get("class") or "read").strip()
    if risk_class in _NON_MUTATING_RISK_CLASSES:
        return True
    executor = command.get("executor")
    return isinstance(executor, Mapping) and executor.get("available") is True


def _action_assurance(action: Mapping[str, Any]) -> dict[str, Any]:
    """Return the effective assurance floor; publishers may only strengthen it."""

    risk = str(action.get("risk") or "read").strip().lower()
    declared = dict(action.get("assurance") or {})
    mutating = risk not in {"read", "none", "read_only"}
    step_up = risk in _STEP_UP_RISK_CLASSES or bool(declared.get("step_up_required"))
    trusted = (
        step_up
        or mutating
        or bool(action.get("confirmation_required"))
        or bool(declared.get("trusted_interface_required"))
    )
    voice = bool(declared.get("voice_permitted", not trusted)) and not trusted
    mode = "step_up_required" if step_up else "trusted_interface_required" if trusted else "voice_permitted"
    return {
        "mode": mode,
        "voice_permitted": voice,
        "trusted_interface_required": trusted,
        "step_up_required": step_up,
    }


def _assurance_admission(
    assurance: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> dict[str, Any]:
    mode = str(assurance.get("mode") or "trusted_interface_required")
    if mode == "step_up_required":
        required = ["trusted_interface", "step_up"]
    elif mode == "trusted_interface_required":
        required = ["trusted_interface"]
    else:
        required = ["bound_dialog_response"]
    missing = [name for name in required if capabilities.get(name) is not True]
    return {
        "admitted": not missing,
        "mode": mode,
        "required_capabilities": required,
        "missing_capabilities": missing,
    }


def _unsupported_reason(reason: str, missing: Sequence[str]) -> str:
    if reason in {"assurance_handoff_required", "localized_material_unavailable"}:
        return f"unsupported:{reason}"
    detail = ",".join(str(item) for item in list(missing)[:3]) or str(reason)
    value = f"unsupported:{detail}"
    return value[:160]


def _normalize_semantic_message(
    value: Mapping[str, Any] | None,
    *,
    key: str | None,
    fallback: str,
    locale_context: Mapping[str, Any] | None,
    critical: bool,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) and not str(key or "").strip():
        return None
    supplied = copy.deepcopy(dict(value or {}))
    fallback_value = supplied.get("fallback")
    if not isinstance(fallback_value, Mapping):
        fallback_value = {"visual": fallback, "spoken": fallback}
    source_locale = str(
        supplied.get("source_locale")
        or dict(locale_context or {}).get("source_locale")
        or dict(locale_context or {}).get("locale")
        or "en"
    ).strip().lower()
    translations = {
        str(locale).strip().lower(): copy.deepcopy(dict(variant))
        for locale, variant in dict(supplied.get("translations") or {}).items()
        if str(locale).strip() and isinstance(variant, Mapping)
    }
    record = _validate(
        SEMANTIC_MESSAGE_SCHEMA,
        {
            "schema": SEMANTIC_MESSAGE_SCHEMA,
            "key": str(supplied.get("key") or key or "").strip(),
            "version": int(supplied.get("version") or 1),
            "params": copy.deepcopy(dict(supplied.get("params") or {})),
            "fallback": copy.deepcopy(dict(fallback_value)),
            "translations": translations,
            "catalog_ref": (
                copy.deepcopy(dict(supplied["catalog_ref"]))
                if isinstance(supplied.get("catalog_ref"), Mapping)
                else None
            ),
            "source_locale": source_locale,
            "critical": bool(supplied.get("critical", critical)),
            "fallback_policy": str(supplied.get("fallback_policy") or "allow"),
        },
    )
    for name, parameter in record["params"].items():
        kind = str(parameter["type"])
        parameter_value = parameter["value"]
        valid = (
            kind in {"text", "timestamp", "date", "duration", "unit", "identifier"}
            and isinstance(parameter_value, str)
        ) or (
            kind in {"number", "amount"}
            and isinstance(parameter_value, (int, float))
            and not isinstance(parameter_value, bool)
        ) or (
            kind == "integer"
            and isinstance(parameter_value, int)
            and not isinstance(parameter_value, bool)
        ) or (kind == "boolean" and isinstance(parameter_value, bool))
        if not valid:
            raise ConversationInteractionError(
                f"semantic message parameter {name} does not match declared type {kind}"
            )
    return record


def _materialize_message_template(template: str, params: Mapping[str, Any]) -> str:
    source = str(template)
    if "{" in _MESSAGE_PARAMETER.sub("", source) or "}" in _MESSAGE_PARAMETER.sub("", source):
        raise ConversationInteractionError("semantic message contains an unsafe placeholder")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise ConversationInteractionError(f"semantic message parameter is missing: {name}")
        return str(dict(params[name])["value"])

    return _MESSAGE_PARAMETER.sub(replace, source)


def _resolve_semantic_message(
    message: Mapping[str, Any],
    *,
    requested_locale: str,
) -> dict[str, Any]:
    semantic = _validate(SEMANTIC_MESSAGE_SCHEMA, message)
    requested = str(requested_locale or semantic["source_locale"]).strip().lower()
    language = requested.split("-", 1)[0]
    translations = dict(semantic["translations"])
    locale_candidates = [requested, language]
    if semantic["fallback_policy"] == "allow":
        locale_candidates.append(str(semantic["source_locale"]).lower())
    selected_locale = next(
        (
            candidate
            for candidate in locale_candidates
            if candidate in translations
        ),
        None,
    )
    used_fallback = selected_locale is None
    if (
        used_fallback
        and semantic["critical"] is True
        and semantic["fallback_policy"] == "require_locale"
    ):
        return {
            "available": False,
            "reason_code": "critical_locale_unavailable",
            "key": semantic["key"],
            "requested_locale": requested,
            "catalog_ref": copy.deepcopy(semantic["catalog_ref"]),
        }
    variant = dict(translations[selected_locale]) if selected_locale else dict(semantic["fallback"])
    params = dict(semantic["params"])
    return {
        "available": True,
        "key": semantic["key"],
        "version": semantic["version"],
        "requested_locale": requested,
        "resolved_locale": selected_locale or semantic["source_locale"],
        "used_fallback": used_fallback,
        "catalog_ref": copy.deepcopy(semantic["catalog_ref"]),
        "visual": _materialize_message_template(str(variant["visual"]), params),
        "spoken": _materialize_message_template(str(variant["spoken"]), params),
    }


def interaction_requirements(
    interaction_id: str,
    *,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
    limits: Mapping[str, Any] | None = None,
    fallbacks: Sequence[str] = ("numbered_text", "plain_text", "unsupported"),
    version: int = 1,
    fail_closed: bool = True,
    semantic_equivalence_required: bool = True,
) -> dict[str, Any]:
    """Build the channel-neutral requirements contract for one interaction."""

    return _validate(
        INTERACTION_REQUIREMENTS_SCHEMA,
        {
            "schema": INTERACTION_REQUIREMENTS_SCHEMA,
            "requirements_id": f"requirements:{str(interaction_id or '').strip()}",
            "version": int(version),
            "required": list(dict.fromkeys(str(item) for item in required if str(item))),
            "optional": list(dict.fromkeys(str(item) for item in optional if str(item))),
            "limits": copy.deepcopy(dict(limits or {})),
            "fallbacks": list(dict.fromkeys(str(item) for item in fallbacks if str(item))),
            "fail_closed": bool(fail_closed),
            "semantic_equivalence_required": bool(semantic_equivalence_required),
            "permission_boundary": "separate",
            "business_availability_boundary": "separate",
        },
    )


def _is_expired(value: str | None, *, now: str | None = None) -> bool:
    if not value:
        return False
    try:
        expires = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(now or _now()).replace("Z", "+00:00"))
        return expires <= current
    except ValueError:
        return True


def channel_capability_profile(
    profile_id: str,
    *,
    transport: str,
    client: str,
    surface: str,
    capabilities: Mapping[str, Any],
    limits: Mapping[str, Any] | None = None,
    locale: str = "en",
    accessibility: Mapping[str, Any] | None = None,
    handoff: Mapping[str, Any] | None = None,
    acknowledgement: str = "delivery",
    version: int = 1,
    fresh_until: str | None = None,
    updated_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    record = _validate(
        CAPABILITY_PROFILE_SCHEMA,
        {
            "schema": CAPABILITY_PROFILE_SCHEMA,
            "profile_id": str(profile_id or "").strip(),
            "version": int(version),
            "transport": str(transport or "").strip(),
            "client": str(client or "").strip(),
            "surface": str(surface or "").strip(),
            "capabilities": {str(key): bool(value) for key, value in dict(capabilities or {}).items()},
            "limits": copy.deepcopy(dict(limits or {})),
            "locale": str(locale or "en").strip(),
            "accessibility": copy.deepcopy(dict(accessibility or {})),
            "handoff": {
                "reconnect": True,
                "resume": True,
                "cross_channel": False,
                **copy.deepcopy(dict(handoff or {})),
            },
            "acknowledgement": str(acknowledgement or "delivery").strip(),
            "permission_boundary": "separate",
            "business_availability_boundary": "separate",
            "fresh_until": fresh_until,
            "updated_at": updated_at or _now(),
            "metadata": copy.deepcopy(dict(metadata or {})),
        },
    )
    if persist and conversation_store.save_channel_capability_profile(record) is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return record


def standard_capability_profile(
    transport: str,
    *,
    client: str | None = None,
    surface: str = "chat",
    locale: str = "en",
    persist: bool = True,
) -> dict[str, Any]:
    channel = str(transport or "text").strip().lower()
    if channel == "web":
        capabilities = {
            "text": True,
            "buttons": True,
            "forms": True,
            "rich_view": True,
            "deep_link": True,
            "progress": True,
            "cancel": True,
            "message_edit": True,
            "secure_input": True,
            "file_upload": True,
            "web_view": True,
            "miniapp": True,
            "pagination": True,
            "bound_dialog_response": True,
            "trusted_interface": True,
            "step_up": False,
        }
        limits = {"actions": 30, "text_chars": 12000, "button_text_chars": 240, "files": 20}
    elif channel == "telegram":
        capabilities = {
            "text": True,
            "buttons": True,
            "forms": False,
            "rich_view": False,
            "deep_link": True,
            "progress": True,
            "cancel": True,
            "message_edit": True,
            "secure_input": False,
            "file_upload": True,
            "web_view": True,
            "miniapp": True,
            "pagination": True,
            "bound_dialog_response": True,
            "trusted_interface": True,
            "step_up": False,
        }
        limits = {"actions": 8, "text_chars": 3500, "button_text_chars": 64, "files": 10}
    else:
        capabilities = {
            "text": True,
            "buttons": False,
            "forms": False,
            "rich_view": False,
            "deep_link": False,
            "progress": False,
            "cancel": True,
            "message_edit": False,
            "secure_input": False,
            "file_upload": False,
            "web_view": False,
            "miniapp": False,
            "pagination": True,
            "bound_dialog_response": True,
            "trusted_interface": False,
            "step_up": False,
        }
        limits = {"actions": 0, "text_chars": 2000, "button_text_chars": 0, "files": 0}
    return channel_capability_profile(
        f"{channel}:{client or channel}:{surface}",
        transport=channel,
        client=client or channel,
        surface=surface,
        capabilities=capabilities,
        limits=limits,
        locale=locale,
        handoff={
            "reconnect": True,
            "resume": True,
            "cross_channel": channel in {"web", "telegram"},
        },
        acknowledgement="action" if channel in {"web", "telegram"} else "none",
        persist=persist,
    )


def create_interaction(
    *,
    conversation_id: str,
    owner: str,
    prompt: str,
    prompt_ref: str | None = None,
    prompt_message: Mapping[str, Any] | None = None,
    locale_context: Mapping[str, Any] | None = None,
    input_spec: Mapping[str, Any] | None = None,
    actions: Sequence[Mapping[str, Any]] | None = None,
    required_capabilities: Sequence[str] = (),
    optional_capabilities: Sequence[str] = (),
    fallbacks: Sequence[str] = ("numbered_text", "plain_text", "unsupported"),
    interaction_id: str | None = None,
    thread_id: str | None = None,
    task_ref: Mapping[str, Any] | None = None,
    workflow_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
    content_retention_until_epoch: float | None = None,
    audit_retention_until_epoch: float | None = None,
    metadata: Mapping[str, Any] | None = None,
    turn_trace_id: str | None = None,
    trace: Mapping[str, Any] | None = None,
    now: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    timestamp = now or _now()
    if (
        content_retention_until_epoch is not None
        and audit_retention_until_epoch is not None
        and float(content_retention_until_epoch) > float(audit_retention_until_epoch)
    ):
        raise ConversationInteractionError("content retention cannot outlive audit retention")
    spec = {
        "kind": "text",
        "required_fields": [],
        "choices": [],
        "sensitive": False,
        **copy.deepcopy(dict(input_spec or {})),
    }
    normalized_actions = []
    for item in actions or []:
        action = {
            "action_id": str(item.get("action_id") or item.get("id") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "label_ref": str(item.get("label_ref") or "").strip() or None,
            "label_message": _normalize_semantic_message(
                item.get("label_message") if isinstance(item.get("label_message"), Mapping) else None,
                key=str(item.get("label_ref") or "").strip() or None,
                fallback=str(item.get("label") or "").strip(),
                locale_context=locale_context,
                critical=True,
            ),
            "command": str(item.get("command") or "").strip(),
            "value": copy.deepcopy(item.get("value")),
            "risk": str(item.get("risk") or "read").strip(),
            "confirmation_required": bool(item.get("confirmation_required")),
            "assurance": _action_assurance(item),
            "target_ref": copy.deepcopy(item.get("target_ref")) if isinstance(item.get("target_ref"), Mapping) else copy.deepcopy(dict(workflow_ref)) if workflow_ref is not None else None,
            "expected_generation": max(
                0,
                int(
                    item.get("expected_generation")
                    if item.get("expected_generation") is not None
                    else dict(workflow_ref or {}).get("generation") or 0
                ),
            ),
            "principal_scope": [
                str(value).strip()
                for value in item.get("principal_scope") or ["user", "transport"]
                if str(value).strip()
            ],
            "command_context_ref": copy.deepcopy(item.get("command_context_ref")) if isinstance(item.get("command_context_ref"), Mapping) else None,
        }
        try:
            action["semantics"] = normalize_action_semantics(
                {
                    **dict(item),
                    **action,
                    "_interaction_expires_at": expires_at,
                    "_now": timestamp,
                }
            )
        except ActionSemanticsError as exc:
            raise ConversationInteractionError(str(exc)) from exc
        normalized_actions.append(action)
    required = list(dict.fromkeys(str(item).strip() for item in required_capabilities if str(item).strip()))
    if bool(spec.get("sensitive")) and "secure_input" not in required:
        required.append("secure_input")
    selected_interaction_id = str(
        interaction_id or f"interaction.{uuid.uuid4().hex}"
    ).strip()
    requirements = interaction_requirements(
        selected_interaction_id,
        required=required,
        optional=optional_capabilities,
        fallbacks=fallbacks,
    )
    record = _validate(
        INTERACTION_SCHEMA,
        {
            "schema": INTERACTION_SCHEMA,
            "interaction_id": selected_interaction_id,
            "conversation_id": str(conversation_id or "").strip(),
            "thread_id": str(thread_id).strip() if thread_id else None,
            "owner": str(owner or "").strip(),
            "prompt": str(prompt or "").strip(),
            "prompt_ref": str(prompt_ref or "").strip() or None,
            "prompt_message": _normalize_semantic_message(
                prompt_message,
                key=str(prompt_ref or "").strip() or None,
                fallback=str(prompt or "").strip(),
                locale_context=locale_context,
                critical=True,
            ),
            "locale_context": copy.deepcopy(dict(locale_context or {})) or None,
            "input_spec": spec,
            "actions": normalized_actions,
            "requirements": requirements,
            "required_capabilities": required,
            "optional_capabilities": list(
                dict.fromkeys(str(item).strip() for item in optional_capabilities if str(item).strip())
            ),
            "fallbacks": list(dict.fromkeys(str(item).strip() for item in fallbacks if str(item).strip())),
            "status": "created",
            "generation": 0,
            "task_ref": copy.deepcopy(dict(task_ref)) if task_ref is not None else None,
            "workflow_ref": copy.deepcopy(dict(workflow_ref)) if workflow_ref is not None else None,
            "reply_route_ref": copy.deepcopy(dict(reply_route_ref)) if reply_route_ref is not None else None,
            "expires_at": expires_at,
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": None,
            "metadata": copy.deepcopy(dict(metadata or {})),
            "turn_trace_id": (
                str(turn_trace_id or dict(metadata or {}).get("turn_trace_id") or "").strip()
                or None
            ),
            "trace": copy.deepcopy(
                dict(trace or dict(metadata or {}).get("trace") or {})
            ),
        },
    )
    record["metadata"] = {
        **dict(record.get("metadata") or {}),
        "semantic_digest": interaction_semantic_digest(record),
    }
    record = _validate(INTERACTION_SCHEMA, record)
    if not persist:
        return record
    stored = conversation_store.save_interaction(
        record,
        create_only=True,
        content_retention_until=content_retention_until_epoch,
        audit_retention_until=audit_retention_until_epoch,
    )
    if stored is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return _validate(INTERACTION_SCHEMA, stored)


def interaction_from_workflow_description(
    description: Mapping[str, Any],
    *,
    conversation_id: str,
    owner: str,
    prompt: str | None = None,
    prompt_ref: str | None = None,
    prompt_message: Mapping[str, Any] | None = None,
    locale_context: Mapping[str, Any] | None = None,
    interaction_id: str | None = None,
    thread_id: str | None = None,
    task_ref: Mapping[str, Any] | None = None,
    workflow_ref: Mapping[str, Any] | None = None,
    command_context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
    content_retention_until_epoch: float | None = None,
    audit_retention_until_epoch: float | None = None,
    metadata: Mapping[str, Any] | None = None,
    turn_trace_id: str | None = None,
    trace: Mapping[str, Any] | None = None,
    action_labels: Mapping[str, str] | None = None,
    action_semantics: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    snapshot = copy.deepcopy(dict(description or {}))
    if snapshot.get("schema") != "adaos.workflow.description.v1":
        raise ConversationInteractionError("workflow description must use adaos.workflow.description.v1")
    generation = int(snapshot.get("generation") or 0)
    commands = [dict(item) for item in snapshot.get("allowed_commands") or [] if isinstance(item, Mapping)]
    if not commands:
        raise ConversationInteractionError("workflow description has no allowed commands")
    required: list[str] = []
    optional: list[str] = []
    fallbacks: list[str] = []
    actions: list[dict[str, Any]] = []
    labels = {str(key): str(value) for key, value in dict(action_labels or {}).items() if str(value).strip()}
    declared_semantics = {
        str(key): copy.deepcopy(dict(value))
        for key, value in dict(action_semantics or {}).items()
        if isinstance(value, Mapping)
    }
    for command in commands:
        if not _workflow_command_executor_ready(command):
            raise ConversationInteractionError(
                f"executor_unavailable: workflow command {command.get('command')} cannot be presented"
            )
        capabilities = dict(command.get("capability_requirements") or {})
        for capability in capabilities.get("required") or []:
            if str(capability) not in required:
                required.append(str(capability))
        for capability in capabilities.get("optional") or []:
            if str(capability) not in optional:
                optional.append(str(capability))
        fallback = str(capabilities.get("fallback") or "numbered_text")
        if fallback not in fallbacks:
            fallbacks.append(fallback)
        risk = dict(command.get("risk") or {})
        authority = dict(command.get("authority") or {})
        command_id = str(command["command"])
        transition_id = str(command["transition_id"])
        confirmation_required = str(risk.get("confirmation") or "none") != "none"
        semantics = copy.deepcopy(
            declared_semantics.get(command_id)
            or declared_semantics.get(transition_id)
            or {
                "preset": "custom",
                "effect_class": "workflow",
                "operation": command_id,
                "executor": "workflow",
                "mutates_domain": str(risk.get("class") or "read").lower()
                not in {"read", "none", "read_only"},
                "records_consent": confirmation_required,
                "terminal": True,
                "effect_ref": None,
                "assertion_required": False,
            }
        )
        actions.append(
            {
                "action_id": transition_id,
                "label": labels.get(
                    command_id,
                    str(command.get("explanation") or command["command"]),
                ),
                "command": command_id,
                "value": command_id,
                "risk": str(risk.get("class") or "read"),
                "confirmation_required": confirmation_required,
                "preset": str(semantics.get("preset") or "custom"),
                "semantics": semantics,
                "target_ref": copy.deepcopy(command.get("target_ref") or snapshot.get("target")),
                "expected_generation": generation,
                "principal_scope": [str(item) for item in authority.get("actors") or ["user"]],
                "command_context_ref": copy.deepcopy(command_context_ref),
            }
        )
    if "numbered_text" not in fallbacks:
        fallbacks.append("numbered_text")
    if "unsupported" not in fallbacks:
        fallbacks.append("unsupported")
    return create_interaction(
        interaction_id=interaction_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        owner=owner,
        prompt=prompt or f"State: {snapshot.get('state')}. Choose the next action.",
        prompt_ref=prompt_ref,
        prompt_message=prompt_message,
        locale_context=locale_context,
        input_spec={
            "kind": "choice",
            "required_fields": [],
            "choices": [
                {"value": item["command"], "label": item["label"], "description": None}
                for item in actions
            ],
            "sensitive": False,
        },
        actions=actions,
        required_capabilities=required,
        optional_capabilities=optional,
        fallbacks=fallbacks,
        task_ref=task_ref,
        workflow_ref=workflow_ref,
        reply_route_ref=reply_route_ref,
        expires_at=expires_at,
        content_retention_until_epoch=content_retention_until_epoch,
        audit_retention_until_epoch=audit_retention_until_epoch,
        metadata={
            "source": "workflow_description",
            "workflow_type": snapshot.get("workflow_type"),
            "definition_version": snapshot.get("definition_version"),
            "state": snapshot.get("state"),
            **copy.deepcopy(dict(metadata or {})),
        },
        turn_trace_id=turn_trace_id,
        trace=trace,
        now=now,
        persist=persist,
    )


def _action_token(
    interaction_id: str,
    generation: int,
    action_id: str,
    presentation_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{interaction_id}:{generation}:{action_id}:{presentation_id}".encode("utf-8")
    ).hexdigest()[:32]
    return f"ia:{generation}:{digest}"


def negotiate_presentation(
    interaction: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    deep_link_base: str | None = None,
    persist: bool = True,
    now: str | None = None,
) -> dict[str, Any]:
    semantic = _validate(INTERACTION_SCHEMA, interaction)
    channel = _validate(CAPABILITY_PROFILE_SCHEMA, profile)
    capabilities = dict(channel["capabilities"])
    requirements = _validate(
        INTERACTION_REQUIREMENTS_SCHEMA,
        dict(semantic.get("requirements") or {}),
    )
    required = set(requirements["required"])
    actions = copy.deepcopy(list(semantic["actions"]))
    locale = str(channel.get("locale") or "en").strip().lower()
    message_receipts: dict[str, Any] = {"prompt": None, "actions": {}}
    prompt_resolution: dict[str, Any] | None = None
    if isinstance(semantic.get("prompt_message"), Mapping):
        prompt_resolution = _resolve_semantic_message(
            dict(semantic["prompt_message"]),
            requested_locale=locale,
        )
        message_receipts["prompt"] = {
            key: copy.deepcopy(value)
            for key, value in prompt_resolution.items()
            if key not in {"visual", "spoken"}
        }
    localized_actions: list[dict[str, Any]] = []
    i18n_missing: list[str] = []
    if prompt_resolution is not None and prompt_resolution.get("available") is not True:
        i18n_missing.append(f"i18n:{prompt_resolution['key']}")
    for action in actions:
        localized = copy.deepcopy(action)
        label_message = action.get("label_message")
        if isinstance(label_message, Mapping):
            resolution = _resolve_semantic_message(label_message, requested_locale=locale)
            message_receipts["actions"][str(action["action_id"])] = {
                key: copy.deepcopy(value)
                for key, value in resolution.items()
                if key not in {"visual", "spoken"}
            }
            if resolution.get("available") is True:
                localized["label"] = str(resolution["visual"])
                localized["spoken_label"] = str(resolution["spoken"])
            else:
                i18n_missing.append(f"i18n:{resolution['key']}")
        localized_actions.append(localized)
    actions = localized_actions
    action_limit = int(dict(channel.get("limits") or {}).get("actions") or 0)
    buttons_usable = bool(capabilities.get("buttons")) and (
        not actions or action_limit <= 0 or len(actions) <= action_limit
    )
    missing = sorted(item for item in required if not capabilities.get(item, False))
    if "buttons" in required and not buttons_usable and "buttons" not in missing:
        missing.append("buttons:limit")
    fallbacks = list(semantic["fallbacks"])
    input_kind = str(semantic["input_spec"]["kind"])
    mode = "unsupported"
    supported = False
    reason = "required_capability_missing" if missing else "native_capabilities"
    deep_link: str | None = None

    if missing:
        unsafe_text = bool(semantic["input_spec"].get("sensitive")) or "secure_input" in missing
        if "deep_link" in fallbacks and capabilities.get("deep_link") and deep_link_base:
            mode = "deep_link"
            supported = True
            reason = "required_capability_handoff"
            deep_link = f"{deep_link_base.rstrip('/')}?interaction={semantic['interaction_id']}"
        elif not unsafe_text and "numbered_text" in fallbacks and capabilities.get("text") and actions:
            mode = "numbered_text"
            supported = True
            reason = "required_capability_numbered_fallback"
        elif not unsafe_text and "plain_text" in fallbacks and capabilities.get("text"):
            mode = "plain_text"
            supported = True
            reason = "required_capability_text_fallback"
    elif input_kind == "form" and capabilities.get("forms"):
        mode, supported = "rich_form", True
    elif actions and buttons_usable:
        mode, supported = "buttons", True
    elif actions and capabilities.get("text") and "numbered_text" in fallbacks:
        mode, supported, reason = (
            "numbered_text",
            True,
            "action_limit_numbered_fallback" if capabilities.get("buttons") and not buttons_usable else "numbered_fallback",
        )
    elif capabilities.get("text"):
        mode, supported = "plain_text", True
    elif "deep_link" in fallbacks and capabilities.get("deep_link") and deep_link_base:
        mode, supported, reason = "deep_link", True, "deep_link_fallback"
        deep_link = f"{deep_link_base.rstrip('/')}?interaction={semantic['interaction_id']}"

    if i18n_missing:
        missing.extend(item for item in i18n_missing if item not in missing)
        mode = "unsupported"
        supported = False
        reason = "localized_material_unavailable"

    action_admissions = [
        _assurance_admission(_action_assurance(action), capabilities)
        for action in actions
    ]
    assurance_missing = sorted(
        {
            f"assurance:{action['action_id']}:{capability}"
            for action, admission in zip(actions, action_admissions, strict=True)
            for capability in admission["missing_capabilities"]
        }
    )
    if actions and assurance_missing and not i18n_missing:
        missing.extend(item for item in assurance_missing if item not in missing)
        admitted_actions = sum(1 for admission in action_admissions if admission["admitted"])
        if admitted_actions == 0:
            mode = "unsupported"
            supported = False
            reason = "assurance_handoff_required"
        elif supported:
            reason = "partial_assurance_handoff"

    prompt = (
        str(prompt_resolution["visual"])
        if prompt_resolution is not None and prompt_resolution.get("available") is True
        else str(semantic["prompt"])
    )
    spoken_prompt = (
        str(prompt_resolution["spoken"])
        if prompt_resolution is not None and prompt_resolution.get("available") is True
        else str(semantic["prompt"])
    )
    timestamp = now or _now()
    fallback_used = (
        mode
        if mode in {"numbered_text", "plain_text", "deep_link", "web_view", "miniapp"}
        and reason != "native_capabilities"
        else None
    )
    semantic_equivalent = bool(
        supported
        and (
            not actions
            or mode in {"buttons", "numbered_text", "deep_link", "rich_form", "web_view", "miniapp"}
        )
        and not (semantic["input_spec"].get("sensitive") and mode in {"plain_text", "numbered_text"})
    )
    if requirements["semantic_equivalence_required"] and supported and not semantic_equivalent:
        mode = "unsupported"
        supported = False
        reason = "semantic_equivalence_unavailable"
        fallback_used = None
    presentation_id = "presentation." + hashlib.sha256(
        f"{semantic['interaction_id']}:{semantic['generation']}:{channel['profile_id']}:{channel['version']}:{mode}".encode("utf-8")
    ).hexdigest()[:32]
    unsupported_reason = _unsupported_reason(reason, missing)
    tokens: dict[str, str] = {}
    projected_actions: list[dict[str, Any]] = []
    for index, (action, admission) in enumerate(
        zip(actions, action_admissions, strict=True),
        start=1,
    ):
        token = None
        if supported and admission["admitted"]:
            token = _action_token(
                semantic["interaction_id"],
                int(semantic["generation"]),
                str(action["action_id"]),
                presentation_id,
            )
            tokens[token] = str(action["action_id"])
        projected_actions.append(
            {
                **copy.deepcopy(action),
                "assurance": _action_assurance(action),
                "assurance_admission": copy.deepcopy(admission),
                "enabled": token is not None,
                "token": token,
                "index": index,
            }
        )
    if mode == "numbered_text" and projected_actions:
        prompt += "\n" + "\n".join(
            f"{item['index']}. {item['label']}"
            for item in projected_actions
            if item["enabled"]
        )
    if mode == "deep_link" and deep_link:
        prompt += f"\n{deep_link}"
    plan = _validate(
        INTERACTION_PRESENTATION_PLAN_SCHEMA,
        {
            "schema": INTERACTION_PRESENTATION_PLAN_SCHEMA,
            "plan_id": f"plan:{presentation_id}",
            "interaction_id": semantic["interaction_id"],
            "interaction_generation": semantic["generation"],
            "profile_id": channel["profile_id"],
            "profile_version": channel["version"],
            "requirements_id": requirements["requirements_id"],
            "selected_mode": mode,
            "supported": supported,
            "reason_code": reason if supported else unsupported_reason,
            "missing_required": missing,
            "fallback_used": fallback_used,
            "semantic_equivalent": semantic_equivalent,
            "limits_applied": copy.deepcopy(dict(channel.get("limits") or {})),
            "renegotiate_on_profile_change": True,
        },
    )
    presentation = _validate(
        INTERACTION_PRESENTATION_SCHEMA,
        {
            "schema": INTERACTION_PRESENTATION_SCHEMA,
            "presentation_id": presentation_id,
            "interaction_id": semantic["interaction_id"],
            "interaction_generation": semantic["generation"],
            "profile_id": channel["profile_id"],
            "profile_version": channel["version"],
            "plan": plan,
            "mode": mode,
            "supported": supported,
            "reason_code": reason if supported else unsupported_reason,
            "prompt": prompt,
            "spoken_prompt": spoken_prompt,
            "actions": projected_actions,
            "action_tokens": tokens,
            "deep_link": deep_link,
            "created_at": timestamp,
            "metadata": {
                "missing_capabilities": missing,
                "transport": channel["transport"],
                "client": channel["client"],
                "surface": channel["surface"],
                "locale": locale,
                "assurance_capabilities": {
                    name: capabilities.get(name) is True
                    for name in ("bound_dialog_response", "trusted_interface", "step_up")
                },
                "message_receipts": message_receipts,
            },
        },
    )
    if persist:
        if conversation_store.append_interaction_presentation(presentation) is None:
            raise ConversationInteractionError("durable conversation store is unavailable")
        if semantic["status"] in {"created", "projected"}:
            updated = copy.deepcopy(semantic)
            updated["status"] = "awaiting_input" if supported else "projected"
            updated["updated_at"] = timestamp
            stored = conversation_store.save_interaction(
                updated,
                expected_generation=int(semantic["generation"]),
            )
            if stored is None:
                raise ConversationInteractionError("durable conversation store is unavailable")
    return presentation


def _validate_response_values(
    interaction: Mapping[str, Any],
    values: Mapping[str, Any],
) -> tuple[bool, list[str], str | None]:
    spec = dict(interaction["input_spec"])
    kind = str(spec["kind"])
    missing = [field for field in spec["required_fields"] if values.get(field) in (None, "", [])]
    if missing:
        return True, missing, "partial_response"
    if kind in {"choice", "multi_choice"} and spec["choices"]:
        allowed = {str(item["value"]) for item in spec["choices"]}
        selected = values.get("choice") if kind == "choice" else values.get("choices")
        selected_values = [selected] if kind == "choice" else list(selected or [])
        if any(str(item) not in allowed for item in selected_values):
            return False, [], "invalid_choice"
    if kind == "confirmation" and not isinstance(values.get("confirmed"), bool):
        return False, [], "confirmation_boolean_required"
    if kind == "text" and not str(values.get("text") or "").strip():
        return False, [], "text_required"
    return True, [], None


def _principal_in_scope(actor_id: str, principal_scope: Sequence[str]) -> bool:
    actor = str(actor_id or "").strip()
    scopes = {str(item or "").strip() for item in principal_scope if str(item or "").strip()}
    if not actor or not scopes:
        return False
    namespace = actor.split(":", 1)[0]
    return "*" in scopes or actor in scopes or namespace in scopes


def _principal_can_read_interaction(
    interaction: Mapping[str, Any],
    principal: Mapping[str, Any],
) -> bool:
    kind = str(principal.get("kind") or "").strip()
    principal_id = str(principal.get("id") or "").strip()
    actor_id = str(principal.get("actor_id") or "").strip()
    if not actor_id and kind and principal_id:
        actor_id = f"{kind}:{principal_id}"
    application_id = str(principal.get("application_id") or "").strip()
    identities = {
        value
        for value in (
            actor_id,
            principal_id,
            application_id,
            f"application:{application_id}" if application_id else "",
            f"skill:{application_id}" if application_id else "",
        )
        if value
    }
    owner = str(interaction.get("owner") or "").strip()
    if owner in identities:
        return True
    participants = {
        str(item).strip()
        for item in dict(interaction.get("metadata") or {}).get("participants") or []
        if str(item).strip()
    }
    if identities.intersection(participants):
        return True
    if actor_id:
        return any(
            _principal_in_scope(actor_id, action.get("principal_scope") or [])
            for action in interaction.get("actions") or []
            if isinstance(action, Mapping)
        )
    return False


def principal_can_read_interaction(
    interaction: Mapping[str, Any],
    principal: Mapping[str, Any],
) -> bool:
    """Evaluate canonical Interaction read scope for a verified principal."""

    return bool(principal) and _principal_can_read_interaction(interaction, principal)


def query_interactions(
    *,
    principal: Mapping[str, Any],
    conversation_id: str | None = None,
    statuses: Sequence[str] | None = None,
    owners: Sequence[str] | None = None,
    active_only: bool = False,
    field_mask: str = "summary",
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return an ACL-filtered page of canonical interactions.

    The signed cursor is bound to the query, caller identity, field mask, and
    exact selected content digest. The bounded snapshot refuses an oversized
    query instead of silently omitting unresolved obligations.
    """

    from adaos.services.root_mcp.opaque_cursor import (
        decode_opaque_cursor,
        encode_opaque_cursor,
    )

    if not isinstance(principal, Mapping) or not dict(principal):
        raise ConversationInteractionError("verified interaction query principal is required")
    mask_name = str(field_mask or "summary").strip()
    fields = _INTERACTION_QUERY_FIELD_MASKS.get(mask_name)
    if fields is None:
        raise ConversationInteractionError(f"unsupported interaction field mask: {mask_name}")
    page_size = max(1, min(int(limit), 100))
    selected_statuses = sorted(
        {
            str(item).strip()
            for item in (
                _PENDING_STATUSES if active_only else statuses or ()
            )
            if str(item).strip()
        }
    )
    if any(status not in _PENDING_STATUSES | _TERMINAL_STATUSES | {"answered", "accepted"} for status in selected_statuses):
        raise ConversationInteractionError("unsupported interaction status filter")
    selected_owners = sorted({str(item).strip() for item in owners or () if str(item).strip()})
    normalized_principal = {
        key: str(principal.get(key) or "").strip()
        for key in ("kind", "id", "actor_id", "application_id")
        if str(principal.get(key) or "").strip()
    }
    query = {
        "conversation_id": str(conversation_id or "").strip() or None,
        "statuses": selected_statuses,
        "owners": selected_owners,
        "active_only": bool(active_only),
        "principal": normalized_principal,
    }
    records = conversation_store.list_interactions(
        conversation_id=query["conversation_id"],
        statuses=selected_statuses,
        limit=1000,
    )
    if len(records) >= 1000:
        raise ConversationInteractionError(
            "interaction query exceeds the bounded snapshot; narrow the filters"
        )
    selected = [
        item
        for item in records
        if (not selected_owners or str(item.get("owner") or "") in selected_owners)
        and _principal_can_read_interaction(item, principal)
    ]
    selected.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            str(item.get("interaction_id") or ""),
        ),
        reverse=True,
    )
    content_digest = _canonical_digest(selected)
    mask_contract = {"name": mask_name, "fields": list(fields)}
    try:
        offset = decode_opaque_cursor(
            cursor,
            namespace="conversation.interactions.query.v1",
            query=query,
            field_mask=mask_contract,
            content_digest=content_digest,
        )
    except ValueError as exc:
        raise ConversationInteractionError(str(exc)) from exc
    page = selected[offset : offset + page_size]
    next_offset = offset + len(page)
    next_cursor = (
        encode_opaque_cursor(
            namespace="conversation.interactions.query.v1",
            offset=next_offset,
            query=query,
            field_mask=mask_contract,
            content_digest=content_digest,
        )
        if next_offset < len(selected)
        else None
    )
    return {
        "schema": "adaos.conversation.interaction_query_page.v1",
        "items": [
            {field: copy.deepcopy(item[field]) for field in fields if field in item}
            for item in page
        ],
        "next_cursor": next_cursor,
        "limit": page_size,
        "field_mask": mask_name,
        "content_digest": content_digest,
        "has_more": next_cursor is not None,
    }


def submit_response(
    interaction_id: str,
    *,
    actor_id: str,
    expected_generation: int,
    idempotency_key: str,
    values: Mapping[str, Any] | None = None,
    original_text: str | None = None,
    action_token: str | None = None,
    presentation_id: str | None = None,
    proposed_action_id: str | None = None,
    intent_proposal: Mapping[str, Any] | None = None,
    supersedes_response_id: str | None = None,
    response_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    interaction = conversation_store.get_interaction(interaction_id)
    if interaction is None:
        raise ConversationInteractionError(f"interaction not found: {interaction_id}")
    semantic = _validate(INTERACTION_SCHEMA, interaction)
    timestamp = now or _now()
    request_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            {
                "actor_id": str(actor_id or "").strip(),
                "expected_generation": int(expected_generation),
                "values": dict(values or {}),
                "original_text": original_text,
                "action_token": action_token,
                "presentation_id": presentation_id,
                "proposed_action_id": proposed_action_id,
                "intent_proposal": dict(intent_proposal) if intent_proposal is not None else None,
                "supersedes_response_id": supersedes_response_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing_response = conversation_store.get_interaction_response_by_idempotency(
        interaction_id,
        idempotency_key,
    )
    if existing_response is not None:
        existing_digest = str(dict(existing_response.get("metadata") or {}).get("request_digest") or "")
        if existing_digest != request_digest:
            raise ConversationInteractionError("interaction response idempotency conflict")
        duplicate = copy.deepcopy(existing_response)
        duplicate["duplicate"] = True
        existing_dispatch = conversation_store.get_interaction_dispatch(
            response_id=str(existing_response["response_id"])
        )
        return {
            "interaction": semantic,
            "response": duplicate,
            "dispatch": (
                _validate(INTERACTION_DISPATCH_SCHEMA, existing_dispatch)
                if existing_dispatch is not None
                else None
            ),
            "duplicate": True,
        }
    if _is_expired(semantic.get("expires_at"), now=timestamp):
        expired = copy.deepcopy(semantic)
        expired["status"] = "expired"
        expired["generation"] = int(semantic["generation"]) + 1
        expired["updated_at"] = timestamp
        expired["completed_at"] = timestamp
        conversation_store.save_interaction(expired, expected_generation=int(semantic["generation"]))
        raise ConversationInteractionError("interaction expired")
    if semantic["status"] in _TERMINAL_STATUSES:
        raise ConversationInteractionError(f"interaction is terminal: {semantic['status']}")
    if int(expected_generation) != int(semantic["generation"]):
        raise ConversationInteractionError(
            f"stale interaction generation: expected {expected_generation}, current {semantic['generation']}"
        )
    responses = conversation_store.list_interaction_responses(interaction_id)
    if semantic["status"] == "answered" and not supersedes_response_id:
        raise ConversationInteractionError("answered interaction requires an explicit correction reference")
    if supersedes_response_id and not any(
        item.get("response_id") == supersedes_response_id for item in responses
    ):
        raise ConversationInteractionError("superseded interaction response not found")

    response_values = copy.deepcopy(dict(values or {}))
    source = "form" if values else "text"
    presentation = None
    resolved_action: dict[str, Any] | None = None
    assurance_receipt: dict[str, Any] | None = None
    if action_token:
        presentation = conversation_store.find_interaction_presentation_by_action_token(action_token)
        presentation_generation = (
            int(presentation.get("interaction_generation"))
            if presentation is not None and presentation.get("interaction_generation") is not None
            else -1
        )
        if presentation is None or presentation_generation != int(expected_generation):
            raise ConversationInteractionError("action presentation is stale or unavailable")
        if str(presentation.get("interaction_id") or "") != semantic["interaction_id"]:
            raise ConversationInteractionError("action presentation belongs to another interaction")
        if presentation_id and str(presentation.get("presentation_id") or "") != str(presentation_id):
            raise ConversationInteractionError("action presentation binding does not match")
        if presentation.get("supported") is not True:
            raise ConversationInteractionError("action presentation is not admitted")
        action_id = dict(presentation.get("action_tokens") or {}).get(action_token)
        action = next((item for item in semantic["actions"] if item["action_id"] == action_id), None)
        if action is None:
            raise ConversationInteractionError("invalid interaction action token")
        projected_action = next(
            (
                item
                for item in presentation.get("actions") or []
                if isinstance(item, Mapping)
                and item.get("action_id") == action_id
                and item.get("token") == action_token
            ),
            None,
        )
        admission = dict((projected_action or {}).get("assurance_admission") or {})
        if projected_action is None or projected_action.get("enabled") is not True or admission.get("admitted") is not True:
            raise ConversationInteractionError("interaction action assurance is not admitted")
        if not _principal_in_scope(actor_id, action.get("principal_scope") or []):
            raise ConversationInteractionError("interaction action principal is not authorized")
        resolved_action = dict(action)
        assurance = _action_assurance(action)
        assurance_receipt = {
            "mode": assurance["mode"],
            "profile_id": str(presentation["profile_id"]),
            "profile_version": int(presentation["profile_version"]),
            "presentation_id": str(presentation["presentation_id"]),
            "admitted": True,
            "capabilities": copy.deepcopy(
                dict(dict(presentation.get("metadata") or {}).get("assurance_capabilities") or {})
            ),
        }
        response_values.update(
            {
                "action_id": action["action_id"],
                "command": action["command"],
                "value": copy.deepcopy(action["value"]),
            }
        )
        # A capability-negotiated action token is an exact, durable user
        # gesture bound to one authoritative action.  For actions whose
        # workflow policy requires confirmation, clicking that explicitly
        # labelled action is the confirmation.  Free-form text and inferred
        # NLU proposals do not pass through this branch and therefore cannot
        # acquire confirmation implicitly.
        if bool(action.get("confirmation_required")):
            response_values.setdefault("confirmed", True)
        input_kind = str(semantic["input_spec"]["kind"])
        if input_kind == "choice":
            response_values["choice"] = copy.deepcopy(action["value"])
        elif input_kind == "multi_choice":
            response_values["choices"] = copy.deepcopy(action["value"])
        elif input_kind == "confirmation":
            response_values["confirmed"] = bool(action["value"])
        source = "action"
    elif proposed_action_id:
        if intent_proposal is None:
            raise ConversationInteractionError("proposed action requires an intent proposal")
        action = next(
            (item for item in semantic["actions"] if item["action_id"] == proposed_action_id),
            None,
        )
        if action is None:
            raise ConversationInteractionError("intent proposal action is no longer allowed")
        if not _principal_in_scope(actor_id, action.get("principal_scope") or []):
            raise ConversationInteractionError("interaction action principal is not authorized")
        if _action_assurance(action)["mode"] != "voice_permitted":
            raise ConversationInteractionError("interaction action requires a trusted presentation")
        resolved_action = dict(action)
        response_values.update(
            {
                "action_id": action["action_id"],
                "command": action["command"],
                "value": copy.deepcopy(action["value"]),
            }
        )
        input_kind = str(semantic["input_spec"]["kind"])
        if input_kind == "choice":
            response_values["choice"] = copy.deepcopy(action["value"])
        elif input_kind == "multi_choice":
            response_values["choices"] = copy.deepcopy(action["value"])
        elif input_kind == "confirmation":
            response_values["confirmed"] = bool(action["value"])
        if original_text is not None:
            response_values.setdefault("text", str(original_text))
        source = "intent"
    elif original_text is not None:
        response_values.setdefault("text", str(original_text))
        source = "intent" if intent_proposal is not None else "text"

    valid, missing, reason = _validate_response_values(semantic, response_values)
    response_status = "partial" if valid and missing else ("answered" if valid else "rejected")
    response_metadata = copy.deepcopy(dict(metadata or {}))
    proposal_trace = (
        dict(intent_proposal.get("trace") or {})
        if isinstance(intent_proposal, Mapping)
        else {}
    )
    proposal_turn_trace_id = (
        intent_proposal.get("turn_trace_id")
        if isinstance(intent_proposal, Mapping)
        else None
    )
    selected_turn_trace_id = str(
        response_metadata.get("turn_trace_id")
        or proposal_turn_trace_id
        or semantic.get("turn_trace_id")
        or ""
    ).strip() or None
    selected_trace = copy.deepcopy(
        dict(response_metadata.get("trace") or proposal_trace or semantic.get("trace") or {})
    )
    response = _validate(
        INTERACTION_RESPONSE_SCHEMA,
        {
            "schema": INTERACTION_RESPONSE_SCHEMA,
            "response_id": str(response_id or f"response.{uuid.uuid4().hex}").strip(),
            "interaction_id": semantic["interaction_id"],
            "interaction_generation": int(semantic["generation"]),
            "actor_id": str(actor_id or "").strip(),
            "source": source,
            "values": response_values,
            "original_text": str(original_text) if original_text is not None else None,
            "action_token": str(action_token) if action_token else None,
            "intent_proposal": copy.deepcopy(dict(intent_proposal)) if intent_proposal is not None else None,
            "presentation_id": str((presentation or {}).get("presentation_id") or "").strip() or None,
            "target_ref": copy.deepcopy(resolved_action.get("target_ref")) if resolved_action else None,
            "source_message_ref": copy.deepcopy(dict(metadata or {}).get("source_message_ref")) if isinstance(dict(metadata or {}).get("source_message_ref"), Mapping) else None,
            "consumed_command": (
                {
                    "action_id": resolved_action["action_id"],
                    "label": resolved_action["label"],
                    "command": resolved_action["command"],
                    "value": copy.deepcopy(resolved_action.get("value")),
                    "target_ref": copy.deepcopy(resolved_action.get("target_ref")),
                    "expected_generation": int(resolved_action["expected_generation"]),
                    "risk": resolved_action["risk"],
                    "confirmation_required": bool(resolved_action["confirmation_required"]),
                    "assurance": _action_assurance(resolved_action),
                    "semantics": copy.deepcopy(resolved_action["semantics"]),
                }
                if resolved_action and valid and not missing
                else None
            ),
            "assurance_receipt": assurance_receipt,
            "rejection_reason": reason if not valid else None,
            "status": response_status,
            "validation": {"valid": valid, "reason_code": reason, "missing_fields": missing},
            "supersedes_response_id": str(supersedes_response_id) if supersedes_response_id else None,
            "idempotency_key": str(idempotency_key or "").strip(),
            "created_at": timestamp,
            "metadata": {**response_metadata, "request_digest": request_digest},
            "turn_trace_id": selected_turn_trace_id,
            "trace": selected_trace,
        },
    )
    updated = copy.deepcopy(semantic)
    updated["generation"] = int(semantic["generation"]) + 1
    updated["status"] = (
        "partially_answered" if response_status == "partial" else
        "answered" if response_status == "answered" else
        "validation_failed"
    )
    updated["updated_at"] = timestamp
    updated["metadata"] = {
        **dict(updated.get("metadata") or {}),
        "latest_response_id": response["response_id"],
    }
    try:
        committed = conversation_store.commit_interaction_response(
            response,
            updated,
            expected_generation=int(semantic["generation"]),
        )
    except ValueError as exc:
        raise ConversationInteractionError(str(exc)) from exc
    if committed is None or committed.get("interaction") is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return {
        "interaction": _validate(INTERACTION_SCHEMA, committed["interaction"]),
        "response": committed["response"],
        "dispatch": (
            _validate(INTERACTION_DISPATCH_SCHEMA, committed["dispatch"])
            if committed.get("dispatch") is not None
            else None
        ),
        "duplicate": bool(committed.get("duplicate")),
    }


def submit_action_token(
    action_token: str,
    *,
    actor_id: str,
    idempotency_key: str,
    values: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    presentation = conversation_store.find_interaction_presentation_by_action_token(action_token)
    if presentation is None:
        raise ConversationInteractionError("interaction action token is unknown or expired")
    return submit_response(
        str(presentation["interaction_id"]),
        actor_id=actor_id,
        expected_generation=int(presentation["interaction_generation"]),
        idempotency_key=idempotency_key,
        values=values,
        action_token=action_token,
        presentation_id=str(presentation["presentation_id"]),
        metadata=metadata,
        now=now,
    )


def _normalized_action_label(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = " ".join(text.casefold().strip().split())
    return text.strip(" \t\r\n:;,.!?()[]{}\"'«»")


def submit_exact_action_label(
    conversation_id: str,
    text: str,
    *,
    actor_id: str,
    idempotency_key: str,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Resolve an exact visible action label against the latest live interaction.

    This is the deterministic text fallback for channels where buttons are not
    displayed or a user types the button label manually.  It deliberately does
    not perform fuzzy NLU: only the newest live interaction in the bound
    conversation is eligible, and exactly one action label must match.
    """

    normalized = _normalized_action_label(text)
    if not normalized:
        return {"status": "unbound", "reason_code": "empty_action_label", "candidates": []}
    pending = conversation_store.list_interactions(
        conversation_id=str(conversation_id or "").strip(),
        statuses=sorted(_PENDING_STATUSES),
        limit=20,
    )
    live = [item for item in pending if not _is_expired(item.get("expires_at"), now=now)]
    if not live:
        return {"status": "unbound", "reason_code": "no_pending_interaction", "candidates": []}
    interaction = live[0]
    presentation = conversation_store.latest_interaction_presentation(str(interaction["interaction_id"]))
    if (
        presentation is None
        or int(presentation.get("interaction_generation") or 0) != int(interaction.get("generation") or 0)
    ):
        return {
            "status": "unbound",
            "reason_code": "latest_presentation_unavailable",
            "candidates": [],
        }
    matches = [
        item
        for item in presentation.get("actions") or []
        if isinstance(item, Mapping) and _normalized_action_label(item.get("label")) == normalized
    ]
    if not matches:
        return {"status": "unbound", "reason_code": "action_label_not_available", "candidates": []}
    if len(matches) != 1:
        return {
            "status": "ambiguous",
            "reason_code": "duplicate_action_label",
            "candidates": [str(item.get("action_id") or "") for item in matches],
        }
    token = str(matches[0].get("token") or "").strip()
    if not token:
        return {"status": "unbound", "reason_code": "action_token_unavailable", "candidates": []}
    result = submit_action_token(
        token,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        metadata={
            **dict(metadata or {}),
            "text_fallback": True,
            "matched_action_label": str(matches[0].get("label") or ""),
        },
        now=now,
    )
    return {"status": "resolved", **result}


def accept_response(
    interaction_id: str,
    response_id: str,
    *,
    expected_generation: int,
    now: str | None = None,
) -> dict[str, Any]:
    interaction = conversation_store.get_interaction(interaction_id)
    if interaction is None:
        raise ConversationInteractionError(f"interaction not found: {interaction_id}")
    semantic = _validate(INTERACTION_SCHEMA, interaction)
    if semantic["status"] != "answered":
        raise ConversationInteractionError("only an answered interaction can be accepted")
    if int(semantic["generation"]) != int(expected_generation):
        raise ConversationInteractionError(
            f"stale interaction generation: expected {expected_generation}, current {semantic['generation']}"
        )
    response = next(
        (item for item in conversation_store.list_interaction_responses(interaction_id) if item.get("response_id") == response_id),
        None,
    )
    if response is None or response.get("status") != "answered":
        raise ConversationInteractionError("answered response not found")
    timestamp = now or _now()
    updated = copy.deepcopy(semantic)
    updated["status"] = "accepted"
    updated["generation"] = int(semantic["generation"]) + 1
    updated["updated_at"] = timestamp
    updated["metadata"] = {
        **dict(updated.get("metadata") or {}),
        "accepted_response_id": response_id,
    }
    stored = conversation_store.save_interaction(updated, expected_generation=int(semantic["generation"]))
    if stored is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return _validate(INTERACTION_SCHEMA, stored)


def transition_interaction(
    interaction_id: str,
    action: str,
    *,
    expected_generation: int,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    interaction = conversation_store.get_interaction(interaction_id)
    if interaction is None:
        raise ConversationInteractionError(f"interaction not found: {interaction_id}")
    semantic = _validate(INTERACTION_SCHEMA, interaction)
    if int(semantic["generation"]) != int(expected_generation):
        raise ConversationInteractionError(
            f"stale interaction generation: expected {expected_generation}, current {semantic['generation']}"
        )
    command = str(action or "").strip().lower()
    allowed: dict[str, tuple[set[str], str]] = {
        "resume": ({"partially_answered", "validation_failed", "projected"}, "awaiting_input"),
        "complete": ({"accepted"}, "completed"),
        "cancel": (_PENDING_STATUSES | {"answered", "accepted"}, "cancelled"),
        "expire": (_PENDING_STATUSES | {"answered", "accepted"}, "expired"),
        "supersede": (_PENDING_STATUSES | {"answered", "accepted"}, "superseded"),
    }
    if command not in allowed:
        raise ConversationInteractionError(f"unsupported interaction transition: {command}")
    sources, target = allowed[command]
    if semantic["status"] not in sources:
        raise ConversationInteractionError(
            f"{command} is not allowed from interaction status {semantic['status']}"
        )
    timestamp = now or _now()
    updated = copy.deepcopy(semantic)
    updated["status"] = target
    updated["generation"] = int(semantic["generation"]) + 1
    updated["updated_at"] = timestamp
    if target in _TERMINAL_STATUSES:
        updated["completed_at"] = timestamp
    updated["metadata"] = {
        **dict(updated.get("metadata") or {}),
        "last_transition": command,
        "last_transition_reason": str(reason or "").strip(),
    }
    stored = conversation_store.save_interaction(
        updated,
        expected_generation=int(semantic["generation"]),
    )
    if stored is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return _validate(INTERACTION_SCHEMA, stored)


def supersede_interaction(
    interaction_id: str,
    replacement: Mapping[str, Any],
    *,
    expected_generation: int,
    reason: str,
    now: str | None = None,
) -> dict[str, Any]:
    """Atomically replace materially changed decision semantics."""

    current_value = conversation_store.get_interaction(interaction_id)
    if current_value is None:
        raise ConversationInteractionError(f"interaction not found: {interaction_id}")
    current = _validate(INTERACTION_SCHEMA, current_value)
    candidate = _validate(INTERACTION_SCHEMA, replacement)
    if int(current["generation"]) != int(expected_generation):
        raise ConversationInteractionError(
            f"stale interaction generation: expected {expected_generation}, current {current['generation']}"
        )
    if current["status"] in _TERMINAL_STATUSES:
        raise ConversationInteractionError(f"interaction is terminal: {current['status']}")
    if str(candidate["interaction_id"]) == str(current["interaction_id"]):
        raise ConversationInteractionError("superseding interaction requires a new interaction_id")
    if str(candidate["conversation_id"]) != str(current["conversation_id"]):
        raise ConversationInteractionError("superseding interaction must remain in the same conversation")
    current_digest = str(dict(current.get("metadata") or {}).get("semantic_digest") or "")
    if not current_digest:
        current_digest = interaction_semantic_digest(current)
    candidate_digest = interaction_semantic_digest(candidate)
    if current_digest == candidate_digest:
        return {
            "superseded": False,
            "reason_code": "semantic_digest_unchanged",
            "interaction": current,
            "replacement": None,
        }
    timestamp = now or _now()
    updated = copy.deepcopy(current)
    updated["status"] = "superseded"
    updated["generation"] = int(current["generation"]) + 1
    updated["updated_at"] = timestamp
    updated["completed_at"] = timestamp
    updated["metadata"] = {
        **dict(updated.get("metadata") or {}),
        "last_transition": "supersede",
        "last_transition_reason": str(reason or "semantic_change").strip(),
        "superseded_by": str(candidate["interaction_id"]),
        "superseded_by_semantic_digest": candidate_digest,
    }
    candidate = copy.deepcopy(candidate)
    candidate["metadata"] = {
        **dict(candidate.get("metadata") or {}),
        "semantic_digest": candidate_digest,
        "supersedes_interaction_id": str(current["interaction_id"]),
        "supersedes_semantic_digest": current_digest,
    }
    try:
        committed = conversation_store.commit_interaction_supersession(
            updated,
            candidate,
            expected_generation=int(current["generation"]),
            expected_current=current,
        )
    except ValueError as exc:
        raise ConversationInteractionError(str(exc)) from exc
    return {
        "superseded": True,
        "reason_code": "semantic_digest_changed",
        "interaction": _validate(INTERACTION_SCHEMA, committed["interaction"]),
        "replacement": _validate(INTERACTION_SCHEMA, committed["replacement"]),
    }


def expire_due_interactions(*, now: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    timestamp = now or _now()
    expired: list[dict[str, Any]] = []
    for interaction in conversation_store.list_interactions(
        statuses=sorted(_PENDING_STATUSES | {"answered", "accepted"}),
        due_before=timestamp,
        limit=limit,
    ):
        if not _is_expired(interaction.get("expires_at"), now=timestamp):
            continue
        response_id = str(dict(interaction.get("metadata") or {}).get("latest_response_id") or "")
        dispatch = (
            conversation_store.get_interaction_dispatch(response_id=response_id)
            if response_id
            else None
        )
        if dispatch is not None and str(dispatch.get("status") or "") not in {"pending", "rejected"}:
            # Work that may already have crossed the effect boundary is not
            # relabelled as expired. Its executor/reconciler owns the outcome.
            continue
        try:
            expired_record = transition_interaction(
                str(interaction["interaction_id"]),
                "expire",
                expected_generation=int(interaction["generation"]),
                reason="deadline_reached",
                now=timestamp,
            )
            expired.append(expired_record)
            if response_id:
                conversation_store.reject_interaction_dispatch_if_pending(
                    response_id,
                    reason_code="interaction_expired",
                    now_iso=timestamp,
                )
        except ConversationInteractionError:
            continue
    return expired


def reconcile_interactions_after_restart(
    *,
    now: str | None = None,
    batch_size: int = 250,
    max_batches: int = 40,
) -> dict[str, Any]:
    """Bounded restart reconciliation for expired canonical decisions."""

    timestamp = now or _now()
    size = max(1, min(int(batch_size), 1000))
    batches = max(1, min(int(max_batches), 100))
    expired_ids: list[str] = []
    for _ in range(batches):
        batch = expire_due_interactions(now=timestamp, limit=size)
        expired_ids.extend(str(item["interaction_id"]) for item in batch)
        if len(batch) < size:
            break
    remaining_due = any(
        _is_expired(item.get("expires_at"), now=timestamp)
        for item in conversation_store.list_interactions(
            statuses=sorted(_PENDING_STATUSES | {"answered", "accepted"}),
            due_before=timestamp,
            limit=1,
        )
    )
    return {
        "schema": "adaos.conversation.interaction_reconciliation.v1",
        "observed_at": timestamp,
        "expired_total": len(expired_ids),
        "expired_interaction_ids": expired_ids,
        "complete": not remaining_due,
    }


def resolve_unbound_text(
    conversation_id: str,
    text: str,
    *,
    actor_id: str,
    idempotency_key: str,
    intent_proposal: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    pending = conversation_store.list_interactions(
        conversation_id=conversation_id,
        statuses=sorted(_PENDING_STATUSES),
    )
    live = [item for item in pending if not _is_expired(item.get("expires_at"), now=now)]
    if not live:
        return {"status": "unbound", "reason_code": "no_pending_interaction", "candidates": []}
    if len(live) > 1:
        return {
            "status": "ambiguous",
            "reason_code": "multiple_pending_interactions",
            "candidates": [
                {
                    "interaction_id": item["interaction_id"],
                    "prompt": item["prompt"],
                    "generation": item["generation"],
                }
                for item in live
            ],
        }
    selected = live[0]
    result = submit_response(
        selected["interaction_id"],
        actor_id=actor_id,
        expected_generation=int(selected["generation"]),
        idempotency_key=idempotency_key,
        original_text=text,
        intent_proposal=intent_proposal,
        now=now,
    )
    return {"status": "resolved", **result}


def interaction_handle(interaction: Mapping[str, Any]) -> InteractionHandle:
    semantic = _validate(INTERACTION_SCHEMA, interaction)
    return InteractionHandle(
        interaction_id=str(semantic["interaction_id"]),
        conversation_id=str(semantic["conversation_id"]),
        status=str(semantic["status"]),
        generation=int(semantic["generation"]),
        task_ref=copy.deepcopy(semantic.get("task_ref")),
        workflow_ref=copy.deepcopy(semantic.get("workflow_ref")),
    )
