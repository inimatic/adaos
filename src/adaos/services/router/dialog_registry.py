from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.services.agent_context import get_ctx
from adaos.services.node_config import load_config
from adaos.services import conversation_store
from adaos.services.subnet_alias import display_subnet_alias, load_subnet_alias


GENERAL_DIALOG_AGENT_ID = "agent:core:general"
GENERAL_DIALOG_AGENT_CONFIGURED_LABEL = os.getenv("ADAOS_GENERAL_ASSISTANT_NAME", "").strip()
GENERAL_DIALOG_AGENT_DEFAULT_LABEL = "?????????"
GENERAL_DIALOG_AGENT_GENDER = os.getenv("ADAOS_GENERAL_ASSISTANT_GENDER", "male").strip().lower() or "male"
GENERAL_DIALOG_AGENT_VOICE = os.getenv("ADAOS_GENERAL_ASSISTANT_VOICE", "ru-male").strip() or "ru-male"
GENERAL_DIALOG_AGENT_OWNER = "core:general_assistant"
GENERAL_DIALOG_CHANNEL_ID = "general"
CONVERSATIONAL_DIALOG_CHANNEL_ID = "conversational"
BUILDER_DIALOG_CHANNEL_ID = "builder"
BUILDER_SKILL_ID = "builder_skill"
CONVERSATION_COMPANIONS_SKILL_ID = "conversation_companions"
DIALOG_USER_MESSAGE_EVENT = "dialog.user_message"
VOICE_CHAT_USER_EVENT = "voice.chat.user"


def _dialog_ingress_route_id(meta: Mapping[str, Any] | None, event_kind: str) -> str:
    metadata = meta if isinstance(meta, Mapping) else {}
    requested = str(metadata.get("route_id") or metadata.get("route") or "").strip()
    if str(event_kind or "").strip() == DIALOG_USER_MESSAGE_EVENT and requested:
        return requested
    return "voice_chat"


def _builder_transport_integrity_error(
    text: Any,
    *,
    meta: Mapping[str, Any] | None = None,
    dialog_channel_id: str | None = None,
) -> str | None:
    """Return a rejection reason before a Builder chat turn is persisted.

    Builder requests can create durable change evidence or launch an LLM job, so
    text whose Unicode code points were already lost must be rejected at the
    voice-chat ingress boundary.  Other dialog channels keep their existing
    punctuation semantics.
    """

    metadata = dict(meta or {})
    channel = str(
        dialog_channel_id
        or metadata.get("dialog_channel_id")
        or metadata.get("conversation_channel_id")
        or ""
    ).strip().lower()
    agent_id = str(metadata.get("active_agent_id") or "").strip().lower()
    if channel != BUILDER_DIALOG_CHANNEL_ID and agent_id not in {
        "agent:builder_skill:builder",
        "agent:builder_skill",
    }:
        return None
    token = str(text or "")
    if "\ufffd" in token:
        return "replacement_character"
    if "????" in token:
        return "suspicious_question_mark_run"
    return None

_CONVERSATION_AGENT_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": GENERAL_DIALOG_AGENT_ID,
        "label": GENERAL_DIALOG_AGENT_DEFAULT_LABEL,
        "owner": GENERAL_DIALOG_AGENT_OWNER,
        "kind": "core_agent",
        "channel_id": GENERAL_DIALOG_CHANNEL_ID,
        "gender": GENERAL_DIALOG_AGENT_GENDER,
        "voice": GENERAL_DIALOG_AGENT_VOICE,
        "icon": "sparkles-outline",
        "aliases": (GENERAL_DIALOG_AGENT_DEFAULT_LABEL, "ассистент", "общий ассистент", "general"),
    },
    {
        "id": "agent:conversation_companions:arseni",
        "label": "Арсений",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
        "skill": "conversation_companions",
        "talk_tool": "talk",
        "switch_tool": "switch_character",
        "character_id": "arseni",
        "gender": "male",
        "voice": "ru-male",
        "icon": "male-outline",
        "aliases": ("Арсений", "Arseni", "Arseniy", "советник", "консультант"),
    },
    {
        "id": "agent:conversation_companions:nika",
        "label": "Ника",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
        "skill": "conversation_companions",
        "talk_tool": "talk",
        "switch_tool": "switch_character",
        "character_id": "nika",
        "gender": "female",
        "voice": "ru-female",
        "icon": "female-outline",
        "aliases": ("Ника", "Nika", "скептик"),
    },
    {
        "id": "agent:conversation_companions:mira",
        "label": "Мира",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
        "skill": "conversation_companions",
        "talk_tool": "talk",
        "switch_tool": "switch_character",
        "character_id": "mira",
        "gender": "female",
        "voice": "ru-female",
        "icon": "heart-circle-outline",
        "aliases": ("Мира", "Mira", "собеседник", "рассказчик"),
    },
    {
        "id": "agent:builder_skill:builder",
        "label": "\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c",
        "owner": "skill:builder_skill",
        "kind": "skill_agent",
        "channel_id": BUILDER_DIALOG_CHANNEL_ID,
        "skill": BUILDER_SKILL_ID,
        "talk_tool": "chat",
        "gender": "male",
        "voice": "ru-male",
        "icon": "construct-outline",
        "aliases": (
            "\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c",
            "\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c",
            "Builder",
            "builder",
            "buikder",
            "\u0431\u0438\u043b\u0434\u0435\u0440",
        ),
    },
)


_GENERAL_AGENT_ADDRESS_RE = re.compile(
    r"^\s*(?:general|ассистент|общий\s+ассистент)\s*(?:[,;:.!?]\s*|\s+)(?P<rest>.*)$",
    re.IGNORECASE | re.UNICODE,
)


def _general_conversation_id(webspace_id: str) -> str:
    ws = str(webspace_id or "default").strip() or "default"
    return f"conv.core.general.{ws}"


def _skill_conversation_id(skill_id: str, webspace_id: str) -> str:
    skill = str(skill_id or "skill").strip() or "skill"
    ws = str(webspace_id or "default").strip() or "default"
    return f"conv.skill.{skill}.default.{ws}"


def _dialog_channel_label(channel_id: Any) -> str:
    token = str(channel_id or "").strip()
    if token == "general":
        return "General"
    if token == "conversational":
        return "Conversational"
    if token == "builder":
        return "Builder"
    return token.replace("_", " ").replace("-", " ").title() if token else "Dialog"


def _dialog_runtime_failure_is_unavailable(exc: BaseException) -> bool:
    detail = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in detail
        for marker in (
            "not activated",
            "not prepared",
            "not found",
            "unavailable",
            "deactivated",
            "no versions installed",
            "no default tool",
            "resolved_manifest",
            "manifest",
        )
    )


def _dialog_runtime_dev_fallback_allowed(skill_id: Any, exc: BaseException) -> bool:
    token = str(skill_id or "").strip()
    if token == BUILDER_SKILL_ID:
        return True
    if token != CONVERSATION_COMPANIONS_SKILL_ID:
        return False
    return _dialog_runtime_failure_is_unavailable(exc)


def _dialog_runtime_uses_dev_webspace(webspace_id: Any) -> bool:
    """Resolve dialog runtime authority from persisted webspace metadata."""

    token = str(webspace_id or "").strip()
    if not token:
        return False
    try:
        from adaos.services.workspaces import index as workspace_index

        manifest = workspace_index.get_workspace(token)
    except Exception:
        # Never infer DEV from untrusted text or a naming convention here.  A
        # missing manifest keeps the normal Workspace runtime authoritative.
        return False
    return bool(manifest and manifest.is_dev)


def _dialog_channel_policy(channel_id: Any, *, default_tool: str | None = None) -> dict[str, Any]:
    token = str(channel_id or "").strip() or "general"
    if token == "general":
        return {
            "entry_intents": ["general.request", "general.agent_addressed"],
            "default_tool": default_tool or "voice_chat_skill.handle_text",
            "fallback": "nlu",
            "exit_intents": [],
            "switch_intents": ["conversation.start", "builder.start"],
        }
    if token == "conversational":
        return {
            "entry_intents": ["conversation.start", "conversation.agent_addressed"],
            "default_tool": default_tool or "conversation_companions.talk",
            "fallback": "owner_default_tool",
            "exit_intents": ["conversation.exit", "general.agent_addressed"],
            "switch_intents": ["conversation.switch_character", "general.agent_addressed"],
        }
    if token == "builder":
        return {
            "entry_intents": ["builder.start", "builder.agent_addressed"],
            "default_tool": default_tool or f"{BUILDER_SKILL_ID}.chat",
            "fallback": "owner_default_tool",
            "exit_intents": ["builder.exit", "general.agent_addressed"],
            "switch_intents": ["general.agent_addressed", "conversation.agent_addressed"],
        }
    return {
        "entry_intents": [f"{token}.start", f"{token}.agent_addressed"],
        "default_tool": default_tool or "chat",
        "fallback": "owner_default_tool",
        "exit_intents": [f"{token}.exit", "general.agent_addressed"],
        "switch_intents": ["general.agent_addressed"],
    }


def _dedupe_texts(values: list[Any] | tuple[Any, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def _current_subnet_id() -> str:
    for getter in (
        lambda: getattr(get_ctx(), "config", None),
        load_config,
    ):
        try:
            config = getter()
        except Exception:
            continue
        for attr in ("subnet_id_value", "subnet_id"):
            try:
                token = str(getattr(config, attr, "") or "").strip()
            except Exception:
                token = ""
            if token:
                return token
    return ""


def _is_technical_subnet_label(label: str) -> bool:
    return bool(re.fullmatch(r"sn_[0-9a-f]{8,}", str(label or "").strip(), re.IGNORECASE))


def _general_agent_label() -> str:
    if GENERAL_DIALOG_AGENT_CONFIGURED_LABEL:
        return GENERAL_DIALOG_AGENT_CONFIGURED_LABEL
    subnet_id = _current_subnet_id()
    try:
        label = display_subnet_alias(load_subnet_alias(subnet_id=subnet_id), subnet_id)
    except Exception:
        label = subnet_id
    token = str(label or "").strip()
    if token and not _is_technical_subnet_label(token):
        return token
    return GENERAL_DIALOG_AGENT_DEFAULT_LABEL


def _general_agent_aliases(label: str | None = None) -> list[str]:
    resolved = str(label or _general_agent_label()).strip()
    return _dedupe_texts(
        [
            resolved,
            GENERAL_DIALOG_AGENT_DEFAULT_LABEL,
            "ассистент",
            "общий ассистент",
            "general",
        ]
    )


def _general_agent_record() -> dict[str, Any]:
    label = _general_agent_label()
    return {
        **dict(_CONVERSATION_AGENT_REGISTRY[0]),
        "id": GENERAL_DIALOG_AGENT_ID,
        "label": label,
        "owner": GENERAL_DIALOG_AGENT_OWNER,
        "kind": "core_agent",
        "channel_id": GENERAL_DIALOG_CHANNEL_ID,
        "gender": GENERAL_DIALOG_AGENT_GENDER,
        "voice": GENERAL_DIALOG_AGENT_VOICE,
        "icon": "sparkles-outline",
        "aliases": tuple(_general_agent_aliases(label)),
    }


def _is_dialog_surface_route(
    meta: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None = None,
    *,
    route_id: str | None = None,
) -> bool:
    meta = meta if isinstance(meta, Mapping) else {}
    payload = payload if isinstance(payload, Mapping) else {}
    for source in (meta, payload):
        if str(source.get("dialog_channel_id") or source.get("conversation_id") or "").strip():
            return True
    event_kind = str(
        meta.get("canonical_event_kind")
        or meta.get("input_event_kind")
        or meta.get("dialog_event_kind")
        or payload.get("canonical_event_kind")
        or payload.get("input_event_kind")
        or payload.get("dialog_event_kind")
        or ""
    ).strip()
    if event_kind in {DIALOG_USER_MESSAGE_EVENT, VOICE_CHAT_USER_EVENT}:
        return True
    token = str(route_id or meta.get("route_id") or meta.get("route") or payload.get("route_id") or payload.get("route") or "").strip()
    return token == "voice_chat"


def _dialog_surface_fallback_policy(
    meta: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None = None,
    *,
    route_id: str | None = None,
) -> dict[str, Any]:
    if not _is_dialog_surface_route(meta, payload, route_id=route_id):
        return {}
    meta = meta if isinstance(meta, Mapping) else {}
    payload = payload if isinstance(payload, Mapping) else {}
    channel_id = str(
        meta.get("dialog_channel_id")
        or payload.get("dialog_channel_id")
        or GENERAL_DIALOG_CHANNEL_ID
    ).strip() or GENERAL_DIALOG_CHANNEL_ID
    default_tool = str(
        meta.get("default_tool")
        or payload.get("default_tool")
        or _dialog_channel_policy(channel_id).get("default_tool")
        or "voice_chat_skill.handle_text"
    ).strip()
    skill, _, tool = default_tool.partition(".")
    if not tool:
        if channel_id == GENERAL_DIALOG_CHANNEL_ID:
            skill, tool = "voice_chat_skill", "handle_text"
        else:
            owner = str(meta.get("conversation_owner") or payload.get("conversation_owner") or "").strip()
            skill = owner.removeprefix("skill:") if owner.startswith("skill:") else owner
            tool = default_tool or "chat"
    return {
        "schema": "adaos.dialog.surface_fallback_policy.v1",
        "channel_id": channel_id,
        "skill": str(skill or "voice_chat_skill").strip() or "voice_chat_skill",
        "tool": str(tool or "handle_text").strip() or "handle_text",
        "reason": "nlu_not_obtained_surface_fallback",
        "route_id": str(route_id or meta.get("route_id") or meta.get("route") or "").strip() or None,
    }


def _fallback_agent_registry_records() -> list[dict[str, Any]]:
    records = [_general_agent_record()]
    records.extend(dict(item) for item in _CONVERSATION_AGENT_REGISTRY[1:])
    return records


def _skill_manifest_dirs() -> list[Path]:
    roots: list[Path] = []
    try:
        roots.append(Path(get_ctx().paths.skills_workspace_dir()))
    except Exception:
        pass
    try:
        roots.append(Path(__file__).resolve().parents[2] / "skills_templates")
    except Exception:
        pass
    dirs: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            candidates = sorted(path for path in root.iterdir() if (path / "skill.yaml").exists())
        except Exception:
            candidates = []
        for path in candidates:
            token = str(path.resolve())
            if token in seen:
                continue
            seen.add(token)
            dirs.append(path)
    return dirs


def _read_skill_manifest(skill_dir: Path) -> dict[str, Any]:
    manifest_path = skill_dir / "skill.yaml"
    try:
        import yaml  # type: ignore

        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception:
        manifest = {}
    return manifest if isinstance(manifest, dict) else {}


def _conversation_manifest_agent_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for skill_dir in _skill_manifest_dirs():
        manifest = _read_skill_manifest(skill_dir)
        skill_id = str(manifest.get("name") or skill_dir.name).strip()
        if not skill_id:
            continue
        conv = manifest.get("conversation") if isinstance(manifest, dict) else {}
        declared_agents = conv.get("agents") if isinstance(conv, dict) else []
        if not isinstance(declared_agents, list):
            continue
        for raw in declared_agents:
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            channel_id = str(record.get("channel_id") or "").strip()
            if not channel_id:
                channel = conv.get("dialog_channel") if isinstance(conv.get("dialog_channel"), dict) else {}
                channels = conv.get("channels") if isinstance(conv.get("channels"), list) else []
                if channel.get("id"):
                    channel_id = str(channel.get("id") or "").strip()
                elif channels and isinstance(channels[0], dict):
                    channel_id = str(channels[0].get("id") or "").strip()
            record.setdefault("owner", f"skill:{skill_id}")
            record.setdefault("skill", skill_id)
            record.setdefault("kind", "skill_agent")
            if channel_id:
                record.setdefault("channel_id", channel_id)
            record.setdefault("source", f"skill:{skill_id}.skill_yaml")
            records.append(record)
    return records


def _normalize_manifest_renderer_capabilities(raw: Mapping[str, Any]) -> dict[str, Any]:
    renderer = raw.get("renderer") if isinstance(raw.get("renderer"), dict) else {}
    capabilities = raw.get("renderer_capabilities") or renderer.get("capabilities")
    if isinstance(capabilities, dict):
        result = dict(capabilities)
    elif isinstance(capabilities, list):
        result = {"targets": [str(item).strip() for item in capabilities if str(item).strip()]}
    else:
        result = {}
    targets = result.get("targets")
    if not isinstance(targets, list) or not targets:
        result["targets"] = ["text", "speech", "dialog.visible_tail"]
    result.setdefault("default_projection", "dialog.visible_tail")
    return result


def _conversation_manifest_channel_records(webspace_id: str) -> list[dict[str, Any]]:
    ws = str(webspace_id or "").strip() or "default"
    channels_out: list[dict[str, Any]] = []
    for skill_dir in _skill_manifest_dirs():
        manifest = _read_skill_manifest(skill_dir)
        skill_id = str(manifest.get("name") or skill_dir.name).strip()
        if not skill_id:
            continue
        conv = manifest.get("conversation") if isinstance(manifest, dict) else {}
        if not isinstance(conv, dict):
            continue
        raw_channels: list[dict[str, Any]] = []
        dialog_channel = conv.get("dialog_channel")
        if isinstance(dialog_channel, dict):
            raw_channels.append(dict(dialog_channel))
        channels = conv.get("channels")
        if isinstance(channels, list):
            raw_channels.extend(dict(item) for item in channels if isinstance(item, dict))
        for raw in raw_channels:
            channel_id = str(raw.get("id") or raw.get("channel_id") or "").strip()
            if not channel_id or not re.match(r"^[a-zA-Z0-9_.:-]+$", channel_id):
                continue
            owner = str(raw.get("owner") or f"skill:{skill_id}").strip()
            if not owner.startswith(("skill:", "core:")):
                continue
            default_tool_ref = str(raw.get("default_tool") or raw.get("tool") or "chat").strip() or "chat"
            default_skill, _, default_tool = default_tool_ref.partition(".")
            if default_tool:
                skill = default_skill or skill_id
                tool = default_tool
            else:
                skill = skill_id
                tool = default_tool_ref
            if not skill or not tool:
                continue
            conversation_id = str(raw.get("conversation_id") or _skill_conversation_id(skill, ws)).strip()
            policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else _dialog_channel_policy(channel_id, default_tool=default_tool_ref)
            policy = dict(policy)
            renderer_capabilities = _normalize_manifest_renderer_capabilities(raw)
            policy.setdefault("renderer_capabilities", renderer_capabilities)
            raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            channels_out.append(
                {
                    "id": channel_id,
                    "channel_id": channel_id,
                    "label": str(raw.get("label") or _dialog_channel_label(channel_id)).strip(),
                    "owner": owner,
                    "conversation_id": conversation_id,
                    "default_skill": skill,
                    "default_tool": tool,
                    "route_id": str(raw.get("route_id") or "voice_chat").strip(),
                    "policy": policy,
                    "meta": {
                        "source": f"skill:{skill_id}.skill_yaml",
                        "manifest_validated": True,
                        "renderer_capabilities": renderer_capabilities,
                        **raw_meta,
                    },
                }
            )
    if not any(str(item.get("id") or "").strip() == BUILDER_DIALOG_CHANNEL_ID for item in channels_out):
        renderer_capabilities = _normalize_manifest_renderer_capabilities({})
        channels_out.append(
            {
                "id": BUILDER_DIALOG_CHANNEL_ID,
                "channel_id": BUILDER_DIALOG_CHANNEL_ID,
                "label": "Builder",
                "owner": f"skill:{BUILDER_SKILL_ID}",
                "conversation_id": _skill_conversation_id(BUILDER_SKILL_ID, ws),
                "default_skill": BUILDER_SKILL_ID,
                "default_tool": "chat",
                "route_id": "voice_chat",
                "policy": _dialog_channel_policy(
                    BUILDER_DIALOG_CHANNEL_ID,
                    default_tool=f"{BUILDER_SKILL_ID}.chat",
                ),
                "meta": {
                    "source": "core:builder_dialog",
                    "contract_validated": True,
                    "renderer_capabilities": renderer_capabilities,
                },
            }
        )
    return channels_out


def _seed_manifest_dialog_channels(webspace_id: str) -> None:
    for channel in _conversation_manifest_channel_records(webspace_id):
        try:
            conversation_store.upsert_conversation(
                conversation_id=channel["conversation_id"],
                webspace_id=webspace_id,
                owner=channel["owner"],
                kind="dialog",
                title=channel["label"],
                meta={"channel_id": channel["id"], "source": (channel.get("meta") or {}).get("source")},
            )
            conversation_store.upsert_dialog_channel(
                webspace_id=webspace_id,
                channel_id=channel["id"],
                label=channel["label"],
                owner=channel["owner"],
                conversation_id=channel["conversation_id"],
                default_skill=channel["default_skill"],
                default_tool=channel["default_tool"],
                route_id=channel["route_id"],
                policy=channel["policy"],
                meta=channel["meta"],
            )
        except Exception:
            logging.getLogger("adaos.router.dialog").debug(
                "failed to seed manifest dialog channel webspace=%s channel=%s",
                webspace_id,
                channel.get("id"),
                exc_info=True,
            )


def _conversation_companion_profile_agent_records() -> list[dict[str, Any]]:
    try:
        skills_dir = Path(get_ctx().paths.skills_workspace_dir())
    except Exception:
        return []
    skill_dir = skills_dir / "conversation_companions"
    records: list[dict[str, Any]] = []

    # Compatibility for the existing pilot profile file before every skill has
    # explicit conversation agent declarations in its manifest.
    profile_path = skill_dir / "profiles" / "default_characters.json"
    try:
        profiles = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    except Exception:
        profiles = {}
    if not isinstance(profiles, dict):
        return []
    static_by_character = {
        str(item.get("character_id") or "").strip(): item
        for item in _CONVERSATION_AGENT_REGISTRY
        if str(item.get("character_id") or "").strip()
    }
    for character_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        fallback = dict(static_by_character.get(str(character_id), {}))
        label = str(profile.get("name") or fallback.get("label") or character_id).strip()
        records.append(
            {
                **fallback,
                "id": fallback.get("id") or f"agent:conversation_companions:{character_id}",
                "label": label,
                "owner": "skill:conversation_companions",
                "kind": "skill_agent",
                "channel_id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
                "skill": "conversation_companions",
                "talk_tool": fallback.get("talk_tool") or "talk",
                "switch_tool": fallback.get("switch_tool") or "switch_character",
                "character_id": str(character_id),
                "source": "skill:conversation_companions.profiles",
            }
        )
    return records


def _seed_conversation_registry() -> None:
    records = _fallback_agent_registry_records()
    records.extend(_conversation_manifest_agent_records())
    records.extend(_conversation_companion_profile_agent_records())
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        agent_id = str(record.get("id") or "").strip()
        if agent_id:
            unique[agent_id] = record
    records = list(unique.values())
    try:
        conversation_store.seed_agents(records, source="router.bootstrap")
    except Exception:
        logging.getLogger("adaos.router.dialog").debug("conversation agent registry seed failed", exc_info=True)


def _agent_registry_records() -> list[dict[str, Any]]:
    try:
        records = conversation_store.list_agents()
    except Exception:
        records = []
    merged_by_id = {
        str(item.get("id") or "").strip(): dict(item)
        for item in _fallback_agent_registry_records()
        if str(item.get("id") or "").strip()
    }
    for item in records:
        record = dict(item)
        agent_id = str(record.get("id") or "").strip()
        if not agent_id:
            continue
        merged_by_id[agent_id] = {**merged_by_id.get(agent_id, {}), **record}
    merged_by_id[GENERAL_DIALOG_AGENT_ID] = {
        **merged_by_id.get(GENERAL_DIALOG_AGENT_ID, {}),
        **_general_agent_record(),
    }
    return list(merged_by_id.values())


def _agent_record_by_id(agent_id: Any) -> dict[str, Any] | None:
    token = str(agent_id or "").strip()
    if not token:
        return None
    for item in _agent_registry_records():
        if str(item.get("id") or "").strip() == token:
            return dict(item)
    return None


def _agent_voice_profile(agent: Mapping[str, Any]) -> dict[str, Any]:
    gender = str(agent.get("gender") or "").strip().lower()
    voice = str(agent.get("voice") or "").strip()
    return {
        "gender": gender or None,
        "voice": voice or None,
        "lang": "ru-RU",
        "browser_voice_hint": _browser_voice_hint(voice=voice, gender=gender),
    }


def _agent_avatar_ref(agent: Mapping[str, Any]) -> str | None:
    value = str(agent.get("avatar_ref") or agent.get("avatar") or "").strip()
    return value or None


def _browser_voice_hint(*, voice: Any = None, gender: Any = None) -> str | None:
    gender_token = str(gender or "").strip().lower()
    if gender_token in {"female", "male"}:
        return gender_token
    voice_token = str(voice or "").strip().lower()
    if not voice_token:
        return gender_token or None
    if voice_token in {"female", "ru-female"} or voice_token.endswith("-female") or voice_token.endswith("_female"):
        return "female"
    if voice_token in {"male", "ru-male"} or voice_token.endswith("-male") or voice_token.endswith("_male"):
        return "male"
    return voice_token or gender_token or None


def _agent_projection_from_record(agent: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "id": str(agent.get("id") or "").strip(),
        "label": str(agent.get("label") or agent.get("id") or "").strip(),
        "owner": str(agent.get("owner") or "").strip(),
        "kind": str(agent.get("kind") or "agent").strip(),
        "channel_id": str(agent.get("channel_id") or "").strip(),
        "memory_scope": "global_user" if agent.get("id") == GENERAL_DIALOG_AGENT_ID else "agent_user",
        "gender": str(agent.get("gender") or "").strip() or None,
        "voice": str(agent.get("voice") or "").strip() or None,
        "icon": str(agent.get("icon") or "").strip() or None,
        "avatar_ref": _agent_avatar_ref(agent),
        "voice_profile": _agent_voice_profile(agent),
    }
    if agent.get("skill"):
        projection["skill_id"] = str(agent.get("skill") or "").strip()
    if agent.get("character_id"):
        projection["character_id"] = str(agent.get("character_id") or "").strip()
    aliases = [str(item).strip() for item in agent.get("aliases", ()) if str(item).strip()]
    if aliases:
        projection["aliases"] = aliases
    return projection


def _agent_label_from_id(agent_id: Any) -> str:
    token = str(agent_id or "").strip()
    if not token:
        return ""
    agent = _agent_record_by_id(token)
    if agent is not None:
        return str(agent.get("label") or "").strip() or token
    return token.rsplit(":", 1)[-1] or token


def _general_agent_projection() -> dict[str, Any]:
    return _agent_projection_from_record(_general_agent_record())


def _general_agent_metadata() -> dict[str, Any]:
    agent = _general_agent_projection()
    gender = str(agent.get("gender") or "").strip()
    voice = str(agent.get("voice") or "").strip()
    return {
        "active_agent_id": GENERAL_DIALOG_AGENT_ID,
        "active_agent_label": str(agent.get("label") or "").strip(),
        "active_agent_gender": gender or None,
        "active_agent_voice": voice or None,
        "active_agent_icon": agent.get("icon"),
        "active_agent_avatar_ref": agent.get("avatar_ref"),
        "voice_gender": gender or None,
        "voice": voice or None,
        "voice_profile": agent.get("voice_profile") or _agent_voice_profile({"gender": gender, "voice": voice}),
    }


def _apply_general_agent_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    meta.update(_general_agent_metadata())
    return meta


def _active_agent_projection(active_channel: dict[str, Any] | None, channel_id: str) -> dict[str, Any]:
    if channel_id == GENERAL_DIALOG_CHANNEL_ID or not active_channel:
        return _general_agent_projection()
    agent_id = str(active_channel.get("active_agent_id") or "").strip()
    label = str(active_channel.get("active_agent_label") or "").strip() or _agent_label_from_id(agent_id)
    owner = str(active_channel.get("active_agent_owner") or active_channel.get("owner") or "").strip()
    kind = str(active_channel.get("active_agent_kind") or "").strip() or "skill_agent"
    registry_agent = _agent_record_by_id(agent_id)
    gender = str(active_channel.get("active_agent_gender") or (registry_agent or {}).get("gender") or "").strip()
    voice = str(active_channel.get("active_agent_voice") or (registry_agent or {}).get("voice") or "").strip()
    icon = str(
        active_channel.get("active_agent_icon")
        or active_channel.get("agent_icon")
        or (registry_agent or {}).get("icon")
        or ""
    ).strip()
    avatar_ref = str(
        active_channel.get("active_agent_avatar_ref")
        or active_channel.get("agent_avatar_ref")
        or active_channel.get("active_agent_avatar")
        or (registry_agent or {}).get("avatar_ref")
        or ""
    ).strip()
    return {
        "id": agent_id or f"agent:{channel_id}:active",
        "label": label,
        "owner": owner,
        "kind": kind,
        "channel_id": channel_id,
        "memory_scope": "agent_user",
        "gender": gender or None,
        "voice": voice or None,
        "icon": icon or None,
        "avatar_ref": avatar_ref or None,
        "voice_profile": _agent_voice_profile({"gender": gender, "voice": voice}),
    }


def _extract_general_agent_addressed_text(text: str) -> str | None:
    value = str(text or "").strip()
    if not value:
        return None
    aliases = {item.casefold() for item in _general_agent_aliases() if item}
    lowered = value.casefold()
    if lowered in aliases:
        return ""
    for alias in aliases:
        match = re.match(
            rf"^\s*{re.escape(alias)}\s*(?:[,;:.!?]\s*|\s+)(?P<rest>.*)$",
            value,
            re.IGNORECASE | re.UNICODE,
        )
        if match:
            return str(match.group("rest") or "").strip()
    match = _GENERAL_AGENT_ADDRESS_RE.match(value)
    if not match:
        return None
    return str(match.group("rest") or "").strip()


def _extract_addressed_agent(text: str) -> tuple[dict[str, Any], str] | None:
    value = str(text or "").strip()
    if not value:
        return None
    lowered = value.lower()
    for agent in _agent_registry_records():
        aliases = [str(item or "").strip() for item in agent.get("aliases", ()) if str(item or "").strip()]
        aliases.sort(key=len, reverse=True)
        for alias in aliases:
            alias_lower = alias.lower()
            if lowered == alias_lower:
                return dict(agent), ""
            match = re.match(
                rf"^\s*{re.escape(alias)}\s*(?:[,;:.!?]\s*|\s+)(?P<rest>.*)$",
                value,
                re.IGNORECASE | re.UNICODE,
            )
            if match:
                return dict(agent), str(match.group("rest") or "").strip()
    return None


def _general_agent_transition_text(seed: str = "") -> str:
    label = _general_agent_label()
    variants = [
        f"{label} на связи. Продолжим в общем режиме.",
        f"{label} к вашим услугам. Чем помочь дальше?",
        f"{label} готов помочь. Диалог вернулся в общий режим.",
    ]
    try:
        index = sum(ord(ch) for ch in str(seed or "")) % len(variants)
    except Exception:
        index = 0
    return variants[index]


def _general_agent_ready_text(seed: str = "") -> str:
    label = _general_agent_label()
    variants = [
        f"{label} на связи.",
        f"{label} к вашим услугам.",
        f"{label} готов помочь.",
    ]
    try:
        index = sum(ord(ch) for ch in str(seed or "")) % len(variants)
    except Exception:
        index = 0
    return variants[index]


def _is_agent_roster_question(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    return any(
        token in value
        for token in (
            "агент",
            "ассистент",
            "персонаж",
            "кто у тебя",
            "кто доступен",
            "представь",
            "познакомь",
        )
    )


def _agent_roster_text() -> str:
    general_label = _general_agent_label()
    companions = [
        agent
        for agent in _agent_registry_records()
        if str(agent.get("channel_id") or "").strip() == CONVERSATIONAL_DIALOG_CHANNEL_ID
    ]
    lines = [
        f"{general_label}: в общем режиме я отвечаю как системный ассистент.",
        "Для разговорного режима доступны персонажи:",
    ]
    for agent in companions:
        label = str(agent.get("label") or "").strip()
        role = {
            "agent:conversation_companions:arseni": "спокойный советник",
            "agent:conversation_companions:nika": "скептик для проверки идей",
            "agent:conversation_companions:mira": "теплый собеседник",
        }.get(str(agent.get("id") or "").strip(), "разговорный агент")
        lines.append(f"- {label}: {role}.")
    lines.append(f"Можно обратиться по имени: «{general_label}, ...», «Арсений, ...», «Ника, ...» или «Мира, ...».")
    return "\n".join(lines)
