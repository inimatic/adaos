from __future__ import annotations

import copy
import hashlib
import json
import uuid
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services import conversation_store


INTERACTION_SCHEMA = "adaos.conversation.interaction.v1"
INTERACTION_RESPONSE_SCHEMA = "adaos.conversation.interaction_response.v1"
CAPABILITY_PROFILE_SCHEMA = "adaos.conversation.channel_capability_profile.v1"
INTERACTION_REQUIREMENTS_SCHEMA = "adaos.conversation.interaction_requirements.v1"
INTERACTION_PRESENTATION_SCHEMA = "adaos.conversation.interaction_presentation.v1"
INTERACTION_PRESENTATION_PLAN_SCHEMA = "adaos.conversation.interaction_presentation_plan.v1"
_PENDING_STATUSES = {"created", "projected", "awaiting_input", "partially_answered", "validation_failed"}
_TERMINAL_STATUSES = {"completed", "expired", "cancelled", "superseded"}
_NON_MUTATING_RISK_CLASSES = {"read", "none"}


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


def _schema(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    if name == CAPABILITY_PROFILE_SCHEMA:
        record.setdefault("permission_boundary", "separate")
        record.setdefault("business_availability_boundary", "separate")
    elif name == INTERACTION_SCHEMA and "requirements" not in record:
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
    metadata: Mapping[str, Any] | None = None,
    turn_trace_id: str | None = None,
    trace: Mapping[str, Any] | None = None,
    now: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    timestamp = now or _now()
    spec = {
        "kind": "text",
        "required_fields": [],
        "choices": [],
        "sensitive": False,
        **copy.deepcopy(dict(input_spec or {})),
    }
    normalized_actions = [
        {
            "action_id": str(item.get("action_id") or item.get("id") or "").strip(),
            "label": str(item.get("label") or "").strip(),
            "label_ref": str(item.get("label_ref") or "").strip() or None,
            "command": str(item.get("command") or "").strip(),
            "value": copy.deepcopy(item.get("value")),
            "risk": str(item.get("risk") or "read").strip(),
            "confirmation_required": bool(item.get("confirmation_required")),
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
        for item in actions or []
    ]
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
    if not persist:
        return record
    stored = conversation_store.save_interaction(record, create_only=True)
    if stored is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return _validate(INTERACTION_SCHEMA, stored)


def interaction_from_workflow_description(
    description: Mapping[str, Any],
    *,
    conversation_id: str,
    owner: str,
    prompt: str | None = None,
    interaction_id: str | None = None,
    thread_id: str | None = None,
    task_ref: Mapping[str, Any] | None = None,
    workflow_ref: Mapping[str, Any] | None = None,
    command_context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    turn_trace_id: str | None = None,
    trace: Mapping[str, Any] | None = None,
    action_labels: Mapping[str, str] | None = None,
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
        actions.append(
            {
                "action_id": str(command["transition_id"]),
                "label": labels.get(
                    str(command["command"]),
                    str(command.get("explanation") or command["command"]),
                ),
                "command": str(command["command"]),
                "value": str(command["command"]),
                "risk": str(risk.get("class") or "read"),
                "confirmation_required": str(risk.get("confirmation") or "none") != "none",
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


def _action_token(interaction_id: str, generation: int, action_id: str) -> str:
    digest = hashlib.sha256(f"{interaction_id}:{generation}:{action_id}".encode("utf-8")).hexdigest()[:32]
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
    actions = list(semantic["actions"])
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

    prompt = str(semantic["prompt"])
    tokens: dict[str, str] = {}
    projected_actions: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        token = _action_token(semantic["interaction_id"], int(semantic["generation"]), action["action_id"])
        tokens[token] = str(action["action_id"])
        projected_actions.append({**copy.deepcopy(action), "token": token, "index": index})
    if mode == "numbered_text" and projected_actions:
        prompt += "\n" + "\n".join(
            f"{item['index']}. {item['label']}" for item in projected_actions
        )
    if mode == "deep_link" and deep_link:
        prompt += f"\n{deep_link}"
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
    if requirements["semantic_equivalence_required"] and not semantic_equivalent:
        mode = "unsupported"
        supported = False
        reason = "semantic_equivalence_unavailable"
        fallback_used = None
    presentation_id = "presentation." + hashlib.sha256(
        f"{semantic['interaction_id']}:{semantic['generation']}:{channel['profile_id']}:{channel['version']}:{mode}".encode("utf-8")
    ).hexdigest()[:32]
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
            "reason_code": reason if supported else f"unsupported:{','.join(missing) or reason}",
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
            "reason_code": reason if supported else f"unsupported:{','.join(missing) or 'no_output_capability'}",
            "prompt": prompt,
            "actions": projected_actions,
            "action_tokens": tokens,
            "deep_link": deep_link,
            "created_at": timestamp,
            "metadata": {
                "missing_capabilities": missing,
                "transport": channel["transport"],
                "client": channel["client"],
                "surface": channel["surface"],
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


def submit_response(
    interaction_id: str,
    *,
    actor_id: str,
    expected_generation: int,
    idempotency_key: str,
    values: Mapping[str, Any] | None = None,
    original_text: str | None = None,
    action_token: str | None = None,
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
        return {"interaction": semantic, "response": duplicate, "duplicate": True}
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
    presentation = conversation_store.latest_interaction_presentation(interaction_id)
    resolved_action: dict[str, Any] | None = None
    if action_token:
        presentation_generation = (
            int(presentation.get("interaction_generation"))
            if presentation is not None and presentation.get("interaction_generation") is not None
            else -1
        )
        if presentation is None or presentation_generation != int(expected_generation):
            raise ConversationInteractionError("action presentation is stale or unavailable")
        action_id = dict(presentation.get("action_tokens") or {}).get(action_token)
        action = next((item for item in semantic["actions"] if item["action_id"] == action_id), None)
        if action is None:
            raise ConversationInteractionError("invalid interaction action token")
        if not _principal_in_scope(actor_id, action.get("principal_scope") or []):
            raise ConversationInteractionError("interaction action principal is not authorized")
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
                }
                if resolved_action and valid and not missing
                else None
            ),
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
    stored_response = conversation_store.append_interaction_response(response)
    if stored_response is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    if stored_response.get("duplicate"):
        return {"interaction": semantic, "response": stored_response, "duplicate": True}

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
    stored_interaction = conversation_store.save_interaction(
        updated,
        expected_generation=int(semantic["generation"]),
    )
    if stored_interaction is None:
        raise ConversationInteractionError("durable conversation store is unavailable")
    return {
        "interaction": _validate(INTERACTION_SCHEMA, stored_interaction),
        "response": stored_response,
        "duplicate": False,
    }


def submit_action_token(
    action_token: str,
    *,
    actor_id: str,
    idempotency_key: str,
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
        action_token=action_token,
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
        "expire": (_PENDING_STATUSES, "expired"),
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


def expire_due_interactions(*, now: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
    timestamp = now or _now()
    expired: list[dict[str, Any]] = []
    for interaction in conversation_store.list_interactions(
        statuses=sorted(_PENDING_STATUSES),
        limit=limit,
    ):
        if not _is_expired(interaction.get("expires_at"), now=timestamp):
            continue
        try:
            expired.append(
                transition_interaction(
                    str(interaction["interaction_id"]),
                    "expire",
                    expected_generation=int(interaction["generation"]),
                    reason="deadline_reached",
                    now=timestamp,
                )
            )
        except ConversationInteractionError:
            continue
    return expired


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
