from __future__ import annotations

from typing import Any, Callable, Mapping
from pathlib import Path
import asyncio
import json
import threading
import time
import requests
import os
import re
import shutil
import subprocess
import sys
import y_py as Y

from adaos.services.eventbus import LocalEventBus
import logging
from adaos.domain import Event
from adaos.services.agent_context import get_ctx
from adaos.services.node_config import load_config
from .rules_loader import load_rules, watch_rules
from .media_routes import resolve_media_route_intent
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.io_console import print_text
from adaos.services.subnet_alias import display_subnet_alias, load_subnet_alias
from adaos.sdk.data.env import get_tts_backend
from adaos.adapters.audio.tts.native_tts import NativeTTS
from adaos.integrations.rhasspy.tts import RhasspyTTSAdapter
from adaos.services.webspace_id import coerce_webspace_id
from adaos.services.yjs.doc import async_get_ydoc, async_read_ydoc, mutate_live_room
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.scenario.node_data_scope import node_scope_data_path
from adaos.services.scenario.projection_service import _merge_nested_path
from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.skill.manager import SkillManager
from adaos.sdk.io.context import io_meta
from adaos.services import conversation_context, conversation_response, conversation_store, dialog_runtime
from adaos.services.nlu.text_correction import correct_light_text


_log = logging.getLogger("adaos.router.service")


_WEBIO_RECEIVER_METADATA_CACHE_TTL_S = 2.0
_WEBIO_STREAM_GUARD_STATS_LOCK = threading.Lock()
_WEBIO_STREAM_GUARD_STATS: dict[str, dict[str, Any]] = {}
GENERAL_DIALOG_AGENT_ID = "agent:core:general"
GENERAL_DIALOG_AGENT_CONFIGURED_LABEL = os.getenv("ADAOS_GENERAL_ASSISTANT_NAME", "").strip()
GENERAL_DIALOG_AGENT_DEFAULT_LABEL = "Ассистент"
GENERAL_DIALOG_AGENT_GENDER = os.getenv("ADAOS_GENERAL_ASSISTANT_GENDER", "male").strip().lower() or "male"
GENERAL_DIALOG_AGENT_VOICE = os.getenv("ADAOS_GENERAL_ASSISTANT_VOICE", "ru-male").strip() or "ru-male"
GENERAL_DIALOG_AGENT_OWNER = "core:general_assistant"
GENERAL_DIALOG_CHANNEL_ID = "general"
CONVERSATIONAL_DIALOG_CHANNEL_ID = "conversational"
BUILDER_DIALOG_CHANNEL_ID = "builder"
BUILDER_SKILL_ID = "builder_skill"
DIALOG_USER_MESSAGE_EVENT = "dialog.user_message"
VOICE_CHAT_USER_EVENT = "voice.chat.user"
VOICE_CHAT_STREAM_RECEIVER = "voice_chat.messages"
try:
    VOICE_CHAT_VISIBLE_TAIL = max(8, min(int(str(os.getenv("ADAOS_VOICE_CHAT_VISIBLE_TAIL") or "24").strip()), 100))
except Exception:
    VOICE_CHAT_VISIBLE_TAIL = 24
VOICE_CHAT_HISTORY_LIMIT = 200
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


def _webio_receiver_metadata_timeout_s() -> float:
    try:
        return max(0.05, min(float(str(os.getenv("ADAOS_WEBIO_RECEIVER_METADATA_TIMEOUT_S") or "0.75").strip()), 10.0))
    except Exception:
        return 0.75


def _voice_chat_yjs_timeout_s() -> float:
    try:
        return max(0.05, min(float(str(os.getenv("ADAOS_VOICE_CHAT_YJS_TIMEOUT_S") or "0.75").strip()), 5.0))
    except Exception:
        return 0.75


def _voice_chat_persist_debounce_s() -> float:
    try:
        return max(0.0, min(float(str(os.getenv("ADAOS_VOICE_CHAT_PERSIST_DEBOUNCE_S") or "0.05").strip()), 5.0))
    except Exception:
        return 0.05


def _voice_chat_persist_failure_backoff_s() -> float:
    try:
        return max(0.0, min(float(str(os.getenv("ADAOS_VOICE_CHAT_PERSIST_FAILURE_BACKOFF_S") or "2.0").strip()), 60.0))
    except Exception:
        return 2.0


def _voice_chat_persist_stream_snapshots_enabled() -> bool:
    return str(os.getenv("ADAOS_VOICE_CHAT_PERSIST_STREAM_SNAPSHOTS") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _voice_chat_snapshot_republish_interval_s() -> float:
    try:
        return max(0.0, min(float(str(os.getenv("ADAOS_VOICE_CHAT_SNAPSHOT_REPUBLISH_INTERVAL_S") or "30.0").strip()), 300.0))
    except Exception:
        return 30.0


def _webio_stream_guard_enabled() -> bool:
    return str(os.getenv("ADAOS_WEBIO_STREAM_GUARD_ENABLE") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _webio_stream_warn_bytes() -> int:
    try:
        return max(1024, int(str(os.getenv("ADAOS_WEBIO_STREAM_WARN_BYTES") or "65536").strip()))
    except Exception:
        return 65536


def _webio_stream_block_bytes() -> int:
    try:
        return max(_webio_stream_warn_bytes(), int(str(os.getenv("ADAOS_WEBIO_STREAM_BLOCK_BYTES") or "262144").strip()))
    except Exception:
        return max(_webio_stream_warn_bytes(), 262144)


def _webio_stream_payload_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            decoded = to_json()
            if isinstance(decoded, dict):
                return dict(decoded)
        except Exception:
            return {}
    return {}


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


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
    records = [_general_agent_projection()]
    records.extend(_conversation_manifest_agent_records())
    records.extend(_conversation_companion_profile_agent_records())
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        agent_id = str(record.get("id") or "").strip()
        if agent_id:
            unique[agent_id] = record
    records = list(unique.values())
    if len(records) <= 1:
        records = _fallback_agent_registry_records()
    try:
        conversation_store.seed_agents(records, source="router.bootstrap")
    except Exception:
        logging.getLogger("adaos.router.dialog").debug("conversation agent registry seed failed", exc_info=True)


def _agent_registry_records() -> list[dict[str, Any]]:
    try:
        records = conversation_store.list_agents()
    except Exception:
        records = []
    if records:
        general = _general_agent_record()
        merged: list[dict[str, Any]] = []
        has_general = False
        for item in records:
            record = dict(item)
            if str(record.get("id") or "").strip() == GENERAL_DIALOG_AGENT_ID:
                record = {
                    **record,
                    **general,
                }
                has_general = True
            merged.append(record)
        if not has_general:
            return [general, *merged]
        return merged
    return _fallback_agent_registry_records()


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


def _receiver_declared_owner(receiver_meta: dict[str, Any]) -> str:
    origin = str(receiver_meta.get("origin") or "").strip()
    if origin:
        return origin
    route = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    owner = str(route.get("owner") or receiver_meta.get("owner") or "").strip()
    return owner


def _static_webio_receiver_metadata(receiver: str) -> dict[str, Any]:
    receiver_id = str(receiver or "").strip()
    if receiver_id != VOICE_CHAT_STREAM_RECEIVER:
        return {}
    return {
        "origin": "skill:voice_chat_skill",
        "owner": "skill:voice_chat_skill",
        "mode": "stream",
        "snapshotPolicy": "compact_tail",
        "budget": {"maxPayloadBytes": 524288},
        "route": {
            "kind": "stream",
            "surface": "voice_chat",
            "owner": "skill:voice_chat_skill",
        },
    }


def _webio_stream_stats_key(webspace_id: str, receiver: str, owner: str) -> str:
    return "\0".join(
        [
            str(webspace_id or "").strip() or "default",
            str(receiver or "").strip() or "unknown",
            str(owner or "").strip() or "unknown",
        ]
    )


def _record_webio_stream_guard_event(
    *,
    webspace_id: str,
    receiver: str,
    owner: str,
    event: str,
    payload_bytes: int,
    fanout_total: int,
    effective_bytes: int,
    policy_state: str = "ok",
    reason: str = "healthy",
    receiver_meta: dict[str, Any] | None = None,
) -> None:
    receiver_meta = receiver_meta or {}
    route_meta = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    budget = receiver_meta.get("budget") if isinstance(receiver_meta.get("budget"), dict) else {}
    token_event = str(event or "").strip().lower()
    if not token_event:
        return
    token_ws = str(webspace_id or "").strip() or "default"
    token_receiver = str(receiver or "").strip() or "unknown"
    token_owner = str(owner or "").strip() or "unknown"
    now = time.time()
    key = _webio_stream_stats_key(token_ws, token_receiver, token_owner)
    with _WEBIO_STREAM_GUARD_STATS_LOCK:
        current = dict(_WEBIO_STREAM_GUARD_STATS.get(key) or {})
        current["webspace_id"] = token_ws
        current["receiver"] = token_receiver
        current["owner"] = token_owner
        current["last_at"] = now
        current["last_event"] = token_event
        current["last_policy_state"] = str(policy_state or "").strip() or "ok"
        current["last_reason"] = str(reason or "").strip() or None
        current["last_payload_bytes"] = max(0, int(payload_bytes or 0))
        current["last_fanout_total"] = max(1, int(fanout_total or 1))
        current["last_effective_bytes"] = max(0, int(effective_bytes or 0))
        current["surface"] = str(route_meta.get("surface") or "").strip() or None
        current["route_kind"] = str(route_meta.get("kind") or "").strip() or None
        current["receiver_origin"] = str(receiver_meta.get("origin") or "").strip() or None
        current["receiver_mode"] = str(receiver_meta.get("mode") or "").strip() or None
        current["snapshot_policy"] = str(receiver_meta.get("snapshotPolicy") or "").strip() or None
        current["declared_max_payload_bytes"] = _positive_int(
            budget.get("maxPayloadBytes")
            or budget.get("max_payload_bytes")
            or receiver_meta.get("maxPayloadBytes")
        )
        field = f"{token_event}_total"
        current[field] = int(current.get(field) or 0) + 1
        if token_event == "published":
            current["published_fanout_total"] = int(current.get("published_fanout_total") or 0) + max(1, int(fanout_total or 1))
        _WEBIO_STREAM_GUARD_STATS[key] = current


def webio_stream_guard_snapshot(
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    token_ws = str(webspace_id or "").strip()
    token_receiver = str(receiver or "").strip()
    token_owner = str(owner or "").strip()
    try:
        max_items = max(1, min(500, int(limit)))
    except Exception:
        max_items = 50
    with _WEBIO_STREAM_GUARD_STATS_LOCK:
        rows = [dict(item) for item in _WEBIO_STREAM_GUARD_STATS.values()]
    if token_ws:
        rows = [row for row in rows if str(row.get("webspace_id") or "") == token_ws]
    if token_receiver:
        rows = [row for row in rows if str(row.get("receiver") or "") == token_receiver]
    if token_owner:
        rows = [row for row in rows if str(row.get("owner") or "") == token_owner]
    rows.sort(key=lambda item: float(item.get("last_at") or 0.0), reverse=True)
    totals = {
        "attempted": sum(int(row.get("attempted_total") or 0) for row in rows),
        "published": sum(int(row.get("published_total") or 0) for row in rows),
        "suppressed": sum(int(row.get("suppressed_total") or 0) for row in rows),
        "throttled": sum(int(row.get("throttled_total") or 0) for row in rows),
        "published_fanout": sum(int(row.get("published_fanout_total") or 0) for row in rows),
    }
    return {
        "schema": "adaos.webio_stream_guard.v1",
        "webspace_id": token_ws or None,
        "receiver": token_receiver or None,
        "owner": token_owner or None,
        "items": rows[:max_items],
        "total": len(rows),
        "totals": totals,
    }


async def _read_webio_receiver_metadata(webspace_id: str, receiver: str) -> dict[str, Any]:
    try:
        async with async_get_ydoc(
            webspace_id,
            read_only=True,
            prefer_live_room=True,
            load_mark_roots=["data"],
        ) as ydoc:
            data = _as_dict(ydoc.get_map("data"))
            webio = data.get("webio") if isinstance(data.get("webio"), dict) else {}
            receivers = webio.get("receivers") if isinstance(webio.get("receivers"), dict) else {}
            row = receivers.get(receiver) if isinstance(receivers, dict) else None
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        _log.debug(
            "failed to read webio receiver metadata webspace=%s receiver=%s",
            webspace_id,
            receiver,
            exc_info=True,
        )
        return {}


def _webio_stream_owner(payload: dict[str, Any], meta: dict[str, Any]) -> str:
    owner = str(
        payload.get("owner")
        or meta.get("owner")
        or payload.get("skill_owner")
        or meta.get("skill_owner")
        or ""
    ).strip()
    if owner:
        return owner
    skill_name = str(
        payload.get("skill_name")
        or meta.get("skill_name")
        or payload.get("skill")
        or meta.get("skill")
        or ""
    ).strip()
    return f"skill:{skill_name}" if skill_name else ""


def _webio_stream_admit(
    *,
    webspace_id: str,
    receiver: str,
    owner: str,
    payload_bytes: int,
    fanout_total: int = 1,
    receiver_meta: dict[str, Any] | None = None,
) -> bool:
    if not _webio_stream_guard_enabled():
        return True
    receiver_meta = receiver_meta or {}
    route_meta = receiver_meta.get("route") if isinstance(receiver_meta.get("route"), dict) else {}
    budget = receiver_meta.get("budget") if isinstance(receiver_meta.get("budget"), dict) else {}
    declared_max_payload = _positive_int(
        budget.get("maxPayloadBytes")
        or budget.get("max_payload_bytes")
        or receiver_meta.get("maxPayloadBytes")
    )
    warn_bytes = _webio_stream_warn_bytes()
    block_bytes = _webio_stream_block_bytes()
    if declared_max_payload:
        block_bytes = min(block_bytes, declared_max_payload)
        warn_bytes = min(warn_bytes, max(1, int(declared_max_payload * 0.8)))
    effective_bytes = max(0, int(payload_bytes or 0)) * max(1, int(fanout_total or 1))
    policy_state = "ok"
    reason = "healthy"
    if effective_bytes >= block_bytes:
        policy_state = "block"
        reason = (
            "browser_stream_declared_payload_budget_exceeded"
            if declared_max_payload and effective_bytes >= declared_max_payload
            else "browser_stream_payload_blocked"
        )
    elif effective_bytes >= warn_bytes:
        policy_state = "throttle"
        reason = (
            "browser_stream_declared_payload_budget_pressure"
            if declared_max_payload
            else "browser_stream_payload_pressure"
        )
    _record_webio_stream_guard_event(
        webspace_id=webspace_id,
        receiver=receiver,
        owner=owner,
        event="attempted",
        payload_bytes=payload_bytes,
        fanout_total=fanout_total,
        effective_bytes=effective_bytes,
        policy_state=policy_state,
        reason=reason,
        receiver_meta=receiver_meta,
    )
    if policy_state == "ok":
        return True
    if not owner:
        _record_webio_stream_guard_event(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            event="suppressed",
            payload_bytes=payload_bytes,
            fanout_total=fanout_total,
            effective_bytes=effective_bytes,
            policy_state=policy_state,
            reason=reason,
            receiver_meta=receiver_meta,
        )
        _log.warning(
            "webio stream dropped by payload guard webspace=%s receiver=%s surface=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s owner=unknown",
            webspace_id,
            receiver,
            str(route_meta.get("surface") or "").strip() or "-",
            payload_bytes,
            fanout_total,
            effective_bytes,
            declared_max_payload or "-",
            reason,
        )
        return False
    try:
        from adaos.services.yjs.owner_guard import admit_owner_work

        admission = admit_owner_work(
            webspace_id=webspace_id,
            owner=owner,
            root_names=["stream"],
            path=f"stream/{receiver}",
            source="router.webio_stream",
            channel="webio.stream",
            work_kind="browser_stream",
            tool=f"{owner}:stream:{receiver}",
            policy={
                "policy_state": policy_state,
                "reason": reason,
                "observed_state": "critical" if policy_state == "block" else "high",
                "payload_bytes": payload_bytes,
                "fanout_total": fanout_total,
                "effective_bytes": effective_bytes,
                "budget": dict(budget) if budget else {},
                "declared_max_payload_bytes": declared_max_payload,
                "receiver_origin": str(receiver_meta.get("origin") or "").strip() or None,
                "receiver_mode": str(receiver_meta.get("mode") or "").strip() or None,
                "snapshot_policy": str(receiver_meta.get("snapshotPolicy") or "").strip() or None,
                "route": dict(route_meta) if route_meta else {},
                "guard_visibility": receiver_meta.get("guardVisibility"),
                "blocked_roots": ["stream"] if policy_state == "block" else [],
                "throttled_roots": ["stream"] if policy_state == "throttle" else [],
            },
        )
        if not bool(admission.get("allowed", True)):
            _record_webio_stream_guard_event(
                webspace_id=webspace_id,
                receiver=receiver,
                owner=admission.get("owner") or owner,
                event="suppressed",
                payload_bytes=payload_bytes,
                fanout_total=fanout_total,
                effective_bytes=effective_bytes,
                policy_state=policy_state,
                reason=admission.get("reason") or reason,
                receiver_meta=receiver_meta,
            )
            _log.warning(
                "webio stream denied by owner guard webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s retry_after_s=%s",
                webspace_id,
                receiver,
                str(route_meta.get("surface") or "").strip() or "-",
                admission.get("owner") or owner,
                payload_bytes,
                fanout_total,
                effective_bytes,
                declared_max_payload or "-",
                admission.get("reason") or reason,
                admission.get("retry_after_s") or 0,
            )
            return False
        if bool(admission.get("throttled")):
            _record_webio_stream_guard_event(
                webspace_id=webspace_id,
                receiver=receiver,
                owner=admission.get("owner") or owner,
                event="throttled",
                payload_bytes=payload_bytes,
                fanout_total=fanout_total,
                effective_bytes=effective_bytes,
                policy_state=policy_state,
                reason=admission.get("reason") or reason,
                receiver_meta=receiver_meta,
            )
            _log.warning(
                "webio stream allowed under pressure webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s",
                webspace_id,
                receiver,
                str(route_meta.get("surface") or "").strip() or "-",
                admission.get("owner") or owner,
                payload_bytes,
                fanout_total,
                effective_bytes,
                declared_max_payload or "-",
                admission.get("reason") or reason,
            )
        return True
    except Exception:
        _record_webio_stream_guard_event(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            event="suppressed",
            payload_bytes=payload_bytes,
            fanout_total=fanout_total,
            effective_bytes=effective_bytes,
            policy_state=policy_state,
            reason=reason,
            receiver_meta=receiver_meta,
        )
        _log.warning(
            "webio stream dropped after guard failure webspace=%s receiver=%s surface=%s owner=%s bytes=%s fanout=%s effective_bytes=%s budget_max=%s reason=%s",
            webspace_id,
            receiver,
            str(route_meta.get("surface") or "").strip() or "-",
            owner,
            payload_bytes,
            fanout_total,
            effective_bytes,
            declared_max_payload or "-",
            reason,
            exc_info=True,
        )
        return False


class RouterService:
    def __init__(self, eventbus: LocalEventBus, base_dir: Path) -> None:
        self.bus = eventbus
        self.base_dir = base_dir
        self._started = False
        self._stop_watch: Callable[[], None] | None = None
        self._rules: list[dict[str, Any]] = []
        self._subscribed = False
        self._vlog = logging.getLogger("adaos.router.voice_chat")
        self._tg_reply_via_root_http = str(os.getenv("HUB_TG_REPLY_VIA_ROOT_HTTP") or "").strip() == "1"
        self._media_route_webspaces: set[str] = set()
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._voice_chat_append_tasks: set[asyncio.Task[None]] = set()
        self._voice_chat_append_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._voice_chat_persist_tasks: set[asyncio.Task[None]] = set()
        self._voice_chat_persist_tasks_by_key: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._voice_chat_persist_pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._voice_chat_persist_committed_signatures: dict[tuple[str, str], str] = {}
        self._voice_chat_persist_next_allowed_at: dict[tuple[str, str], float] = {}
        self._dialog_state_tasks: dict[str, asyncio.Task[None]] = {}
        self._dialog_state_pending_events: dict[str, str] = {}
        self._webio_receiver_metadata_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    def _router_yjs_write_meta(self):
        return ystore_write_metadata(
            root_names=["data"],
            source="router.service",
            owner="core:router",
            channel="core.router.async",
        )

    async def _webio_receiver_metadata(self, webspace_id: str, receiver: str) -> dict[str, Any]:
        ws = coerce_webspace_id(webspace_id, fallback="default")
        receiver_id = str(receiver or "").strip()
        if not receiver_id:
            return {}
        static_metadata = _static_webio_receiver_metadata(receiver_id)
        if static_metadata:
            return dict(static_metadata)
        now = time.monotonic()
        key = (ws, receiver_id)
        cached = self._webio_receiver_metadata_cache.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])
        try:
            metadata = await asyncio.wait_for(
                _read_webio_receiver_metadata(ws, receiver_id),
                timeout=_webio_receiver_metadata_timeout_s(),
            )
        except asyncio.TimeoutError:
            _log.warning(
                "webio receiver metadata lookup timed out webspace=%s receiver=%s",
                ws,
                receiver_id,
            )
            metadata = {}
        self._webio_receiver_metadata_cache[key] = (
            now + _WEBIO_RECEIVER_METADATA_CACHE_TTL_S,
            dict(metadata),
        )
        return metadata

    def _pick_target_node(self, desired_io: str, this_node: str) -> str:
        node = this_node
        for r in self._rules:
            try:
                target = r.get("target") or {}
                if str(target.get("io_type") or "stdout").lower() == desired_io.lower():
                    nid = target.get("node_id")
                    if nid == "this" or not nid:
                        node = this_node
                    else:
                        node = str(nid)
                    break
            except Exception:
                continue
        return node

    def _has_rule_for(self, desired_io: str) -> bool:
        for r in self._rules:
            try:
                target = r.get("target") or {}
                if str(target.get("io_type") or "").lower() == desired_io.lower():
                    return True
            except Exception:
                continue
        return False

    def _event_targets_local_node(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return True
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        target_node_id = str(
            payload.get("target_node_id")
            or payload.get("node_id")
            or meta.get("target_node_id")
            or meta.get("node_target_id")
            or ""
        ).strip()
        if not target_node_id:
            return True
        try:
            local_node_id = str(get_ctx().config.node_id or "").strip()
        except Exception:
            local_node_id = ""
        return not local_node_id or target_node_id == local_node_id

    def _event_originates_from_remote_member(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        origin_node_id = str(meta.get("subnet_origin_node_id") or "").strip()
        if not origin_node_id:
            return False
        try:
            local_node_id = str(get_ctx().config.node_id or "").strip()
        except Exception:
            local_node_id = ""
        if local_node_id and origin_node_id == local_node_id:
            return False
        return not self._event_targets_local_node(payload)

    async def _on_event(self, ev: Event) -> None:
        try:
            task = asyncio.create_task(self._handle_notify_event(ev), name=f"router-ui-notify:{str(ev.type or 'ui.notify')}")
        except Exception:
            await self._handle_notify_event(ev)
            return
        self._notify_tasks.add(task)

        def _forget(done: asyncio.Task[None]) -> None:
            self._notify_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.getLogger("adaos.router").warning("router: ui.notify background delivery failed", exc_info=True)

        task.add_done_callback(_forget)

    async def _handle_notify_event(self, ev: Event) -> None:
        payload = ev.payload or {}
        text = (payload or {}).get("text")
        if not isinstance(text, str) or not text:
            return
        meta = payload.get("_meta") if isinstance(payload, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        is_tg = str(meta.get("io_type") or "").lower() == "telegram"
        chat_id = meta.get("chat_id") if is_tg else None
        is_tg_chat = isinstance(chat_id, str) and bool(chat_id.strip())

        # If this came from a chat platform (telegram), reply back into that chat via tg.output.*.
        # This path does not depend on route rules and is meant to be "request/response" style.
        try:
            if is_tg and is_tg_chat and not self._tg_reply_via_root_http:
                bot_id = meta.get("bot_id")
                if not isinstance(bot_id, str) or not bot_id.strip():
                    bot_id = "main-bot"
                hub_id = meta.get("hub_id")
                if not isinstance(hub_id, str) or not hub_id.strip():
                    hub_id = get_ctx().config.subnet_id
                out_payload = {
                    "target": {"bot_id": bot_id, "hub_id": hub_id, "chat_id": chat_id.strip()},
                    "messages": [{"type": "text", "text": text}],
                    "options": {"reply_to": meta.get("reply_to")} if meta.get("reply_to") else None,
                }
                self.bus.publish(
                    Event(
                        type=f"tg.output.{bot_id}.chat.{chat_id.strip()}",
                        source="router",
                        ts=time.time(),
                        payload=out_payload,
                    )
                )
        except Exception:
            pass

        # If the notification has an explicit UI route, mirror it into that route.
        # This keeps skills UI-agnostic: they can emit ui.notify and the router
        # decides how to deliver the message to chat/TTS.
        try:
            route_id = meta.get("route_id") or meta.get("route")
            if isinstance(route_id, str) and route_id.strip():
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router",
                        ts=time.time(),
                        payload={
                            "id": "",
                            "from": "hub",
                            "text": text,
                            "ts": time.time(),
                            "_meta": {**meta, "route_id": route_id.strip()},
                        },
                    )
                )
                self.bus.publish(
                    Event(
                        type="io.out.say",
                        source="router",
                        ts=time.time(),
                        payload={
                            "id": "",
                            "text": text,
                            "ts": time.time(),
                            "lang": str(meta.get("lang") or "ru-RU"),
                            "_meta": {**meta, "route_id": route_id.strip()},
                        },
                    )
                )
            elif is_tg and is_tg_chat and self._tg_reply_via_root_http:
                # When using Root HTTP replies, ensure we still emit io.out.chat.append even if
                # the skill didn't provide route_id/route.
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router",
                        ts=time.time(),
                        payload={
                            "id": "",
                            "from": "hub",
                            "text": text,
                            "ts": time.time(),
                            "_meta": dict(meta),
                        },
                    )
                )
        except Exception:
            pass

        conf = get_ctx().config
        this_node = conf.node_id
        if not self._rules:
            try:
                self._rules = load_rules(self.base_dir, this_node)
            except Exception:
                pass
        # Multi-target routing: attempt telegram and stdout independently if rules exist
        did_any = False

        # Telegram route (if configured in rules)
        if self._has_rule_for("telegram") and not is_tg_chat:
            target_node_tg = self._pick_target_node("telegram", this_node)
            try:
                # Resolve hub_id for target node
                if target_node_tg == this_node:
                    hub_id = conf.subnet_id
                else:
                    directory = get_directory()
                    node = directory.get_node(target_node_tg)
                    hub_id = (node or {}).get("subnet_id")
                if not hub_id:
                    raise RuntimeError("hub_id unresolved for telegram routing")
                # Root API base
                from adaos.services.agent_context import get_ctx as _get_ctx

                api_base = getattr(_get_ctx().settings, "api_base", "https://api.inimatic.com")
                url = f"{api_base.rstrip('/')}/io/tg/send"
                # Prefix message with subnet alias (or id) for clarity
                try:
                    alias = display_subnet_alias(
                        load_subnet_alias(subnet_id=conf.subnet_id) or os.getenv("DEFAULT_HUB"),
                        conf.subnet_id,
                    )
                except Exception:
                    alias = conf.subnet_id
                prefixed_text = f"[{alias}]: {text}" if alias else text
                body = {"hub_id": hub_id, "text": prefixed_text}
                try:
                    r = await asyncio.to_thread(
                        requests.post,
                        url,
                        json=body,
                        headers={"Content-Type": "application/json"},
                        timeout=3.0,
                    )
                    if not (200 <= int(r.status_code) < 300):
                        logging.getLogger("adaos.router").warning(
                            "router: telegram send failed",
                            extra={"hub_id": hub_id, "status": r.status_code, "body": (r.text or "")[:300]},
                        )
                    else:
                        logging.getLogger("adaos.router").info(
                            "router: telegram sent", extra={"hub_id": hub_id, "status": r.status_code}
                        )
                except Exception as pe:
                    logging.getLogger("adaos.router").warning("router: telegram request failed", extra={"hub_id": hub_id, "error": str(pe)})
                    raise
                did_any = True
            except Exception:
                # swallow to allow stdout route below
                try:
                    logging.getLogger("adaos.router").warning("router: telegram route failed; will continue with other routes")
                except Exception:
                    pass

        # Stdout route (if configured in rules)
        if self._has_rule_for("stdout"):
            target_node_out = self._pick_target_node("stdout", this_node)
            if target_node_out == this_node:
                print_text(text, node_id=this_node, origin={"source": ev.source})
                did_any = True
            else:
                # Cross-node delivery: resolve base_url and POST
                base_url = await asyncio.to_thread(self._resolve_node_base_url, target_node_out, conf.role, conf.hub_url)
                if not base_url and conf.role == "hub":
                    try:
                        directory = get_directory()
                        candidates = []
                        for n in directory.list_known_nodes():
                            if not n.get("online"):
                                continue
                            for io in (n.get("capacity") or {}).get("io", []):
                                if io.get("io_type") == "stdout":
                                    candidates.append((int(io.get("priority") or 50), n))
                                    break
                        candidates.sort(key=lambda x: x[0], reverse=True)
                        for _, cand in candidates:
                            nid = cand.get("node_id")
                            if not nid:
                                continue
                            base_url = self._resolve_node_base_url(str(nid), conf.role, conf.hub_url)
                            if base_url:
                                break
                    except Exception:
                        base_url = None

                if base_url:
                    url = f"{base_url.rstrip('/')}/api/io/console/print"
                    headers = {"X-AdaOS-Token": conf.token or "dev-local-token", "Content-Type": "application/json"}
                    body = {"text": text, "origin": {"source": ev.source, "from": this_node}}
                    try:
                        await asyncio.to_thread(requests.post, url, json=body, headers=headers, timeout=2.5)
                        did_any = True
                    except Exception:
                        pass
                else:
                    try:
                        logging.getLogger("adaos.router").warning(f"router: stdout target {target_node_out} offline/unresolved; fallback to local print")
                    except Exception:
                        pass
                    print_text(text, node_id=this_node, origin={"source": ev.source})
                    did_any = True

        # If no route matched or everything failed, fallback to local stdout
        if not did_any:
            print_text(text, node_id=this_node, origin={"source": ev.source})

    def _resolve_node_base_url(self, node_id: str, role: str, hub_url: str | None) -> str | None:
        try:
            if role == "hub":
                directory = get_directory()
                if not directory.is_online(node_id):
                    return None
                return directory.get_node_base_url(node_id)
            # member: ask hub
            if not hub_url:
                return None
            url = f"{hub_url.rstrip('/')}/api/subnet/nodes/{node_id}"
            token = load_config().token or "dev-local-token"
            r = requests.get(url, headers={"X-AdaOS-Token": token}, timeout=2.5)
            if r.status_code != 200:
                return None
            data = r.json() or {}
            node = data.get("node") or {}
            return node.get("base_url")
        except Exception:
            return None

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            conversation_store.ensure_schema()
            _seed_conversation_registry()
        except Exception:
            logging.getLogger("adaos.router.dialog").debug("conversation store bootstrap failed", exc_info=True)
        # Subscribe to ui.notify on local event bus
        if not self._subscribed:
            self.bus.subscribe("ui.notify", self._on_event)

            # ui.say routing (TTS)
            def _say_via_system(text: str) -> bool:
                try:
                    if sys.platform.startswith("win"):
                        safe = text.replace("'", "''")
                        cmd = [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            "Add-Type -AssemblyName System.Speech; "
                            "$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                            f"$speak.Speak('{safe}');",
                        ]
                        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
                    if sys.platform == "darwin" and shutil.which("say"):
                        subprocess.run(["say", text], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
                    if shutil.which("espeak"):
                        subprocess.run(["espeak", text], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
                except Exception:
                    return False
                return False

            def _say_sync(ev: Event, text: str, voice: Any) -> None:
                """
                Execute TTS routing in a worker thread so it never blocks the event loop.
                This is important because ui.say can be emitted early during boot and any
                blocking work (subprocess/requests/TTS engines) can stall NATS WS handshakes.
                """
                conf = load_config()
                this_node = conf.node_id
                target_node = self._pick_target_node("say", this_node)
                base_url = self._resolve_node_base_url(target_node, conf.role, conf.hub_url)
                token = conf.token or "dev-local-token"
                if base_url and target_node != this_node:
                    try:
                        requests.post(
                            f"{base_url.rstrip('/')}/api/say",
                            json={"text": text, "voice": voice},
                            headers={"X-AdaOS-Token": token, "Content-Type": "application/json"},
                            timeout=3.0,
                        )
                        return
                    except Exception:
                        pass
                # local fallback via API if self base_url known, else direct adapter
                self_url = os.environ.get("ADAOS_SELF_BASE_URL")
                if self_url:
                    try:
                        requests.post(
                            f"{self_url.rstrip('/')}/api/say",
                            json={"text": text, "voice": voice},
                            headers={"X-AdaOS-Token": token, "Content-Type": "application/json"},
                            timeout=3.0,
                        )
                        return
                    except Exception:
                        pass
                try:
                    mode = get_tts_backend()
                    adapter = NativeTTS() if mode == "native" else RhasspyTTSAdapter()
                    adapter.say(text)
                    return
                except Exception:
                    if not _say_via_system(text):
                        print_text(text, node_id=this_node, origin={"source": ev.source})

            async def _on_say(ev: Event) -> None:
                payload = ev.payload or {}
                text = (payload or {}).get("text")
                if not isinstance(text, str) or not text.strip():
                    return
                voice = (payload or {}).get("voice")
                try:
                    await asyncio.to_thread(_say_sync, ev, text.strip(), voice)
                except Exception:
                    try:
                        conf = get_ctx().config
                        print_text(text.strip(), node_id=conf.node_id, origin={"source": ev.source})
                    except Exception:
                        pass

            self.bus.subscribe("ui.say", _on_say)
            self._subscribed = True

        # ------------------------------------------------------------
        # Web voice chat routing (per-webspace)
        # ------------------------------------------------------------

        def _coerce_y(node: Any) -> Any:
            if isinstance(node, dict):
                return {str(k): _coerce_y(v) for k, v in node.items()}
            if isinstance(node, Y.YMap):
                return {str(k): _coerce_y(node.get(k)) for k in list(node.keys())}
            if isinstance(node, Y.YArray):
                return [_coerce_y(it) for it in node]
            return node

        def _coerce_webspace_id(value: Any) -> str:
            return coerce_webspace_id(value, fallback="default")

        def _resolve_webspace_ids_basic(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return ["default"]

            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            raw_ids = (meta or {}).get("webspace_ids")
            if isinstance(raw_ids, list):
                out: list[str] = []
                for v in raw_ids:
                    s = _coerce_webspace_id(v)
                    if not s:
                        continue
                    if s not in out:
                        out.append(s)
                if out:
                    return out

            raw = (
                (meta or {}).get("webspace_id")
                or (meta or {}).get("workspace_id")
                or payload.get("webspace_id")
                or payload.get("workspace_id")
                or "default"
            )
            return [_coerce_webspace_id(raw)]

        _route_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}

        async def _resolve_webspace_ids(payload: dict | None) -> list[str]:
            base_ids = _resolve_webspace_ids_basic(payload)
            if not isinstance(payload, dict):
                return base_ids

            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            # If explicit targets are provided, keep them authoritative.
            raw_ids = (meta or {}).get("webspace_ids")
            if isinstance(raw_ids, list) and raw_ids:
                return base_ids

            route_id = (meta or {}).get("route_id") or (meta or {}).get("route")
            if not isinstance(route_id, str) or not route_id.strip():
                return base_ids
            route_id = route_id.strip()
            src_ws = base_ids[0] if base_ids else "default"

            cached = _route_cache.get((src_ws, route_id))
            now = time.time()
            if cached and (now - cached[0]) < 1.0:
                return cached[1]

            try:
                async with async_read_ydoc(src_ws) as ydoc:
                    data = ydoc.get_map("data")
                    routing = _coerce_y(data.get("routing")) or {}
                    routes = routing.get("routes") if isinstance(routing, dict) else {}
                    if not isinstance(routes, dict):
                        routes = {}
                    entry = routes.get(route_id)
                    targets: list[str] = []
                    if isinstance(entry, list):
                        targets = [str(x).strip() for x in entry if str(x).strip()]
                    elif isinstance(entry, dict):
                        raw = entry.get("webspace_ids") or entry.get("targets")
                        if isinstance(raw, list):
                            targets = [str(x).strip() for x in raw if str(x).strip()]
                    if targets:
                        # De-dup while preserving order.
                        dedup: list[str] = []
                        for t in targets:
                            if t not in dedup:
                                dedup.append(t)
                        _route_cache[(src_ws, route_id)] = (now, dedup)
                        return dedup
            except Exception:
                pass

            _route_cache[(src_ws, route_id)] = (now, base_ids)
            return base_ids

        _voice_chat_stream_cache: dict[tuple[str, str], dict[str, Any]] = {}
        _voice_chat_snapshot_published: dict[tuple[str, str], tuple[float, str]] = {}

        def _voice_chat_data_path(target_node_id: str | None) -> str:
            return node_scope_data_path("data/voice_chat", str(target_node_id or "").strip())

        def _local_node_id() -> str:
            try:
                return str(get_ctx().config.node_id or "").strip()
            except Exception:
                return ""

        def _resolve_voice_target_node_id(
            payload: Any,
            meta: dict | None = None,
            *,
            default_local: bool = False,
        ) -> str | None:
            payload_dict = payload if isinstance(payload, dict) else {}
            meta_dict = meta if isinstance(meta, dict) else {}
            scope = str(
                payload_dict.get("voice_chat_scope")
                or meta_dict.get("voice_chat_scope")
                or payload_dict.get("scope")
                or meta_dict.get("scope")
                or ""
            ).strip().lower()
            if scope in {"shared", "workspace", "local"}:
                return None
            token = str(
                payload_dict.get("target_node_id")
                or payload_dict.get("node_id")
                or meta_dict.get("target_node_id")
                or meta_dict.get("node_target_id")
                or ""
            ).strip()
            if not token and default_local:
                token = _local_node_id()
            return token or None

        def _store_dialog_channel_projection(webspace_id: str, channel: dict[str, Any]) -> None:
            channel_id = str(channel.get("channel_id") or channel.get("id") or "").strip()
            if not channel_id:
                return
            def _optional_text(value: Any) -> str | None:
                token = str(value or "").strip()
                return token or None
            channel_meta = channel.get("meta") if isinstance(channel.get("meta"), dict) else {}
            meta_payload = {k: v for k, v in channel.items() if k not in {"policy", "meta"}}
            meta_payload.update(channel_meta)
            try:
                conversation_store.upsert_dialog_channel(
                    webspace_id=webspace_id,
                    channel_id=channel_id,
                    label=str(channel.get("label") or channel_id),
                    owner=_optional_text(channel.get("owner")),
                    conversation_id=_optional_text(channel.get("conversation_id")),
                    active_agent_id=_optional_text(channel.get("active_agent_id")),
                    default_skill=_optional_text(channel.get("default_skill")),
                    default_tool=_optional_text(channel.get("default_tool")),
                    route_id=_optional_text(channel.get("route_id")) or "voice_chat",
                    policy=channel.get("policy") if isinstance(channel.get("policy"), dict) else {},
                    meta=meta_payload,
                )
            except Exception:
                logging.getLogger("adaos.router.dialog").debug(
                    "dialog channel store projection failed webspace=%s channel=%s",
                    webspace_id,
                    channel_id,
                    exc_info=True,
                )

        def _restore_active_dialog_channel_from_store(webspace_id: str) -> dialog_runtime.DialogChannelState | None:
            ws = str(webspace_id or "default").strip() or "default"
            try:
                active_row = conversation_store.get_active_dialog_channel(ws)
            except Exception:
                active_row = None
            if not isinstance(active_row, dict):
                try:
                    active_row = conversation_store.latest_dialog_channel_for_webspace(ws)
                except Exception:
                    active_row = None
            if not isinstance(active_row, dict):
                return None
            channel_id = str(active_row.get("channel_id") or active_row.get("id") or "").strip() or GENERAL_DIALOG_CHANNEL_ID
            if channel_id == GENERAL_DIALOG_CHANNEL_ID:
                return None
            try:
                channel = conversation_store.get_dialog_channel(ws, channel_id) or {}
            except Exception:
                channel = {}
            channel_meta = channel.get("meta") if isinstance(channel.get("meta"), dict) else {}
            agent_id = str(
                active_row.get("active_agent_id")
                or channel.get("active_agent_id")
                or channel_meta.get("active_agent_id")
                or ""
            ).strip()
            agent_record = _agent_record_by_id(agent_id) if agent_id else None
            agent_projection = _agent_projection_from_record(agent_record) if agent_record else {}
            owner = str(channel.get("owner") or (agent_record or {}).get("owner") or channel_meta.get("owner") or "").strip()
            default_skill = str(
                channel.get("default_skill")
                or (agent_record or {}).get("skill")
                or channel_meta.get("default_skill")
                or ""
            ).strip()
            if not default_skill and owner.startswith("skill:"):
                default_skill = owner.split(":", 1)[1]
            default_tool = str(
                channel.get("default_tool")
                or (agent_record or {}).get("talk_tool")
                or channel_meta.get("default_tool")
                or "talk"
            ).strip() or "talk"
            conversation_id = str(
                active_row.get("conversation_id")
                or channel.get("conversation_id")
                or channel_meta.get("conversation_id")
                or (_skill_conversation_id(default_skill, ws) if default_skill else f"conv.{channel_id}.{ws}")
            ).strip()
            try:
                return dialog_runtime.activate_channel(
                    webspace_id=ws,
                    channel_id=channel_id,
                    owner=owner or f"channel:{channel_id}",
                    default_skill=default_skill,
                    default_tool=default_tool,
                    conversation_id=conversation_id,
                    active_agent_id=agent_id or str(agent_projection.get("id") or "").strip() or None,
                    active_agent_label=str(
                        channel_meta.get("active_agent_label")
                        or active_row.get("active_agent_label")
                        or agent_projection.get("label")
                        or _agent_label_from_id(agent_id)
                        or ""
                    ).strip()
                    or None,
                    active_agent_owner=str(
                        channel_meta.get("active_agent_owner")
                        or agent_projection.get("owner")
                        or owner
                        or ""
                    ).strip()
                    or None,
                    active_agent_kind=str(channel_meta.get("active_agent_kind") or agent_projection.get("kind") or "skill_agent").strip()
                    or None,
                    active_agent_gender=str(channel_meta.get("active_agent_gender") or agent_projection.get("gender") or "").strip()
                    or None,
                    active_agent_voice=str(channel_meta.get("active_agent_voice") or agent_projection.get("voice") or "").strip()
                    or None,
                    active_agent_icon=str(
                        channel_meta.get("active_agent_icon")
                        or active_row.get("active_agent_icon")
                        or agent_projection.get("icon")
                        or ""
                    ).strip()
                    or None,
                    active_agent_avatar_ref=str(
                        channel_meta.get("active_agent_avatar_ref")
                        or channel_meta.get("agent_avatar_ref")
                        or active_row.get("active_agent_avatar_ref")
                        or agent_projection.get("avatar_ref")
                        or ""
                    ).strip()
                    or None,
                    route_id=str(channel.get("route_id") or channel_meta.get("route_id") or "voice_chat").strip() or "voice_chat",
                    source="router.dialog.restore",
                )
            except Exception:
                logging.getLogger("adaos.router.dialog").debug(
                    "dialog active channel restore failed webspace=%s channel=%s",
                    ws,
                    channel_id,
                    exc_info=True,
                )
                return None

        def _persist_active_dialog_channel(
            webspace_id: str,
            channel_id: str,
            active_channel: dict[str, Any],
            *,
            event: str,
        ) -> None:
            ws = str(webspace_id or "default").strip() or "default"
            cid = str(channel_id or "").strip() or GENERAL_DIALOG_CHANNEL_ID
            try:
                conversation_store.set_active_dialog_channel(
                    webspace_id=ws,
                    channel_id=cid,
                    conversation_id=str(active_channel.get("conversation_id") or "").strip()
                    or (_general_conversation_id(ws) if cid == GENERAL_DIALOG_CHANNEL_ID else None),
                    active_agent_id=str(active_channel.get("active_agent_id") or "").strip()
                    or (GENERAL_DIALOG_AGENT_ID if cid == GENERAL_DIALOG_CHANNEL_ID else None),
                    meta={
                        "event": event,
                        "owner": active_channel.get("owner"),
                        "route_id": active_channel.get("route_id") or "voice_chat",
                        "default_skill": active_channel.get("default_skill"),
                        "default_tool": active_channel.get("default_tool"),
                        "active_agent_label": active_channel.get("active_agent_label"),
                        "active_agent_owner": active_channel.get("active_agent_owner"),
                        "active_agent_kind": active_channel.get("active_agent_kind"),
                        "active_agent_gender": active_channel.get("active_agent_gender"),
                        "active_agent_voice": active_channel.get("active_agent_voice"),
                        "active_agent_icon": active_channel.get("active_agent_icon"),
                        "active_agent_avatar_ref": active_channel.get("active_agent_avatar_ref"),
                    },
                )
            except Exception:
                logging.getLogger("adaos.router.dialog").debug(
                    "dialog active channel persist failed webspace=%s channel=%s",
                    ws,
                    cid,
                    exc_info=True,
                )

        def _persist_general_dialog_channel(webspace_id: str, *, event: str) -> None:
            ws = str(webspace_id or "default").strip() or "default"
            general_agent = _general_agent_projection()
            _persist_active_dialog_channel(
                ws,
                GENERAL_DIALOG_CHANNEL_ID,
                {
                    "webspace_id": ws,
                    "channel_id": GENERAL_DIALOG_CHANNEL_ID,
                    "owner": GENERAL_DIALOG_AGENT_OWNER,
                    "default_skill": "voice_chat_skill",
                    "default_tool": "handle_text",
                    "conversation_id": _general_conversation_id(ws),
                    "active_agent_id": general_agent["id"],
                    "active_agent_label": general_agent["label"],
                    "active_agent_owner": general_agent["owner"],
                    "active_agent_kind": general_agent["kind"],
                    "active_agent_gender": general_agent.get("gender"),
                    "active_agent_voice": general_agent.get("voice"),
                    "active_agent_icon": general_agent.get("icon"),
                    "active_agent_avatar_ref": general_agent.get("avatar_ref"),
                    "route_id": "voice_chat",
                },
                event=event,
            )

        def _voice_message_dialog_context(webspace_id: str, msg: dict[str, Any]) -> dict[str, Any]:
            ws = str(webspace_id or "default").strip() or "default"
            meta = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
            active = dialog_runtime.get_active_channel(ws)
            active_dict = active.as_dict() if active is not None else {}
            requested_channel = str(
                meta.get("dialog_channel_id")
                or msg.get("dialog_channel_id")
                or meta.get("channel_id")
                or ""
            ).strip()
            msg_agent_id = str(msg.get("active_agent_id") or meta.get("active_agent_id") or "").strip()
            registry_agent = _agent_record_by_id(msg_agent_id) if msg_agent_id else None
            if str(msg.get("from") or "").strip() == "user":
                addressed = _extract_addressed_agent(str(msg.get("text") or ""))
                if addressed is not None:
                    addressed_agent = dict(addressed[0])
                    addressed_channel = str(addressed_agent.get("channel_id") or "").strip()
                    if addressed_channel and (not requested_channel or addressed_channel != GENERAL_DIALOG_CHANNEL_ID):
                        registry_agent = addressed_agent
                        requested_channel = addressed_channel
            if not requested_channel and registry_agent:
                requested_channel = str(registry_agent.get("channel_id") or "").strip()
            if not requested_channel and active is not None:
                requested_channel = str(active.channel_id or "").strip()
            channel_id = requested_channel or GENERAL_DIALOG_CHANNEL_ID
            if channel_id == GENERAL_DIALOG_CHANNEL_ID:
                agent = _general_agent_projection()
                conversation_id = str(meta.get("conversation_id") or msg.get("conversation_id") or _general_conversation_id(ws)).strip()
                owner = GENERAL_DIALOG_AGENT_OWNER
                default_tool = "handle_text"
                default_skill = "voice_chat_skill"
            else:
                if active is not None and str(active.channel_id or "").strip() == channel_id:
                    conversation_id = str(
                        meta.get("conversation_id")
                        or msg.get("conversation_id")
                        or active_dict.get("conversation_id")
                        or ""
                    ).strip()
                    owner = str(
                        meta.get("conversation_owner")
                        or active_dict.get("owner")
                        or (registry_agent or {}).get("owner")
                        or ""
                    ).strip()
                    default_tool = str(active_dict.get("default_tool") or (registry_agent or {}).get("talk_tool") or "talk")
                    default_skill = str(active_dict.get("default_skill") or (registry_agent or {}).get("skill") or "")
                else:
                    skill = str((registry_agent or {}).get("skill") or "").strip()
                    conversation_id = str(
                        meta.get("conversation_id")
                        or msg.get("conversation_id")
                        or (_skill_conversation_id(skill, ws) if skill else f"conv.{channel_id}.{ws}")
                    ).strip()
                    owner = str(meta.get("conversation_owner") or (registry_agent or {}).get("owner") or "").strip()
                    default_tool = str((registry_agent or {}).get("talk_tool") or "talk")
                    default_skill = str((registry_agent or {}).get("skill") or "")
                agent = _active_agent_projection(active_dict if active is not None else None, channel_id)
                if registry_agent:
                    agent = _agent_projection_from_record(registry_agent)
                elif msg_agent_id:
                    agent["id"] = msg_agent_id
                    agent["label"] = str(msg.get("active_agent_label") or meta.get("active_agent_label") or agent.get("label") or msg_agent_id)
                    agent["owner"] = str(meta.get("conversation_owner") or owner or agent.get("owner") or "")
                if not owner:
                    owner = str(agent.get("owner") or f"channel:{channel_id}")
            if not conversation_id:
                conversation_id = _general_conversation_id(ws) if channel_id == GENERAL_DIALOG_CHANNEL_ID else f"conv.{channel_id}.{ws}"
            route_id = str(meta.get("route_id") or meta.get("route") or msg.get("route_id") or "voice_chat").strip() or "voice_chat"
            channel = {
                "channel_id": channel_id,
                "id": channel_id,
                "label": "General" if channel_id == GENERAL_DIALOG_CHANNEL_ID else channel_id.title(),
                "owner": owner,
                "conversation_id": conversation_id,
                "active_agent_id": agent.get("id"),
                "default_skill": default_skill,
                "default_tool": default_tool,
                "route_id": route_id,
            }
            return {
                "webspace_id": ws,
                "channel_id": channel_id,
                "conversation_id": conversation_id,
                "owner": owner,
                "route_id": route_id,
                "agent": agent,
                "channel": channel,
            }

        def _active_voice_chat_channel_id(webspace_id: str) -> str:
            ws = str(webspace_id or "default").strip() or "default"
            try:
                active = dialog_runtime.get_active_channel(ws) or _restore_active_dialog_channel_from_store(ws)
                if active is not None:
                    channel_id = str(active.as_dict().get("channel_id") or active.channel_id or "").strip()
                    if channel_id:
                        return channel_id
            except Exception:
                pass
            try:
                active_row = conversation_store.get_active_dialog_channel(ws)
            except Exception:
                active_row = None
            if isinstance(active_row, dict):
                return str(active_row.get("channel_id") or active_row.get("id") or "").strip()
            return ""

        def _voice_chat_message_targets_active_stream(
            webspace_id: str,
            msg: Mapping[str, Any],
            *,
            channel_id: str,
        ) -> bool:
            if str(msg.get("from") or "").strip() == "user":
                return True
            message_channel_id = str(channel_id or "").strip()
            if not message_channel_id:
                return True
            active_channel_id = _active_voice_chat_channel_id(webspace_id)
            if not active_channel_id:
                return True
            return active_channel_id == message_channel_id

        def _record_voice_turn_trace(
            webspace_id: str,
            meta: dict[str, Any],
            *,
            text: str = "",
            message_id: str | None = None,
            selected_tool: str | None = None,
            reason: str = "voice_turn",
            renderer: Mapping[str, Any] | None = None,
            summary: str | None = None,
            status: str | None = None,
            target_node_id: str | None = None,
            extra_policy: Mapping[str, Any] | None = None,
        ) -> str:
            ws = str(webspace_id or "default").strip() or "default"
            trace_id = str(meta.get("turn_trace_id") or "").strip()
            if not trace_id:
                trace_id = _make_id("trace")
                meta["turn_trace_id"] = trace_id
            try:
                context = _voice_message_dialog_context(
                    ws,
                    {"id": message_id or "", "from": "user", "text": text, "_meta": meta},
                )
                channel_id = str(context.get("channel_id") or GENERAL_DIALOG_CHANNEL_ID).strip() or GENERAL_DIALOG_CHANNEL_ID
                conversation_id = str(context.get("conversation_id") or "").strip()
                route_id = str(context.get("route_id") or meta.get("route_id") or "voice_chat").strip() or "voice_chat"
                owner = str(context.get("owner") or "").strip()
                agent = context.get("agent") if isinstance(context.get("agent"), dict) else {}
                channel = context.get("channel") if isinstance(context.get("channel"), dict) else {}
                tool = str(selected_tool or "").strip()
                if not tool:
                    default_skill = str(channel.get("default_skill") or "").strip()
                    default_tool = str(channel.get("default_tool") or "").strip()
                    tool = f"{default_skill}.{default_tool}".strip(".") if default_skill or default_tool else ""
                agent_id = str(meta.get("active_agent_id") or agent.get("id") or "").strip()
                policy: dict[str, Any] = {
                    "reason": str(reason or "voice_turn"),
                    "source": "router.voice",
                    "route_id": route_id,
                    "selected_channel": channel_id,
                    "selected_conversation": conversation_id,
                    "selected_owner": owner,
                    "selected_agent_id": agent_id,
                    "selected_agent_label": str(meta.get("active_agent_label") or agent.get("label") or "").strip(),
                    "requested_channel": str(meta.get("dialog_channel_id") or "").strip(),
                    "target_node_id": str(target_node_id or meta.get("target_node_id") or "").strip(),
                }
                dialog_event_kind = str(
                    meta.get("dialog_event_kind")
                    or meta.get("canonical_event_kind")
                    or meta.get("input_event_kind")
                    or ""
                ).strip()
                input_event_kind = str(meta.get("input_event_kind") or "").strip()
                if dialog_event_kind:
                    policy["dialog_event_kind"] = dialog_event_kind
                elif _is_dialog_surface_route(meta, {"route_id": route_id}, route_id=route_id):
                    policy["dialog_event_kind"] = DIALOG_USER_MESSAGE_EVENT
                    policy["compat_source_event"] = VOICE_CHAT_USER_EVENT
                if input_event_kind:
                    policy["input_event_kind"] = input_event_kind
                compat_source_event = str(meta.get("compat_source_event") or "").strip()
                if compat_source_event:
                    policy["compat_source_event"] = compat_source_event
                if meta.get("original_text") or meta.get("autocorrected_text"):
                    policy["text_correction"] = {
                        "original": meta.get("original_text"),
                        "autocorrected": meta.get("autocorrected_text"),
                    }
                for key in ("addressed_agent_id", "character_id", "conversation_owner"):
                    value = str(meta.get(key) or "").strip()
                    if value:
                        policy[key] = value
                if extra_policy:
                    policy.update(dict(extra_policy))
                trace_renderer = dict(renderer or {"receiver": "voice_chat.messages", "projection": "compact_tail"})
                if status == "tool_ok":
                    try:
                        existing = conversation_store.get_turn_trace(trace_id)
                    except Exception:
                        existing = None
                    if isinstance(existing, dict) and str(existing.get("status") or "") == "materialized":
                        return trace_id
                conversation_store.start_turn_trace(
                    turn_trace_id=trace_id,
                    webspace_id=ws,
                    conversation_id=conversation_id or None,
                    channel_id=channel_id,
                    agent_id=agent_id or None,
                    selected_tool=tool or None,
                    policy_decision=policy,
                    renderer=trace_renderer,
                    message_id=message_id,
                    summary=summary,
                )
                if status:
                    conversation_store.finish_turn_trace(
                        trace_id,
                        status=status,
                        summary=summary,
                        renderer=trace_renderer,
                    )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").debug(
                    "voice turn trace update failed webspace=%s trace_id=%s",
                    ws,
                    trace_id,
                    exc_info=True,
                )
            return trace_id

        def _fallback_publish_voice_chat_message(
            webspace_id: str,
            target_node_id: str | None,
            msg: dict[str, Any],
            *,
            before_cursor: str | None = None,
            has_more_before: bool = False,
        ) -> None:
            cache_key = (str(webspace_id or "").strip(), str(target_node_id or "").strip())
            cached = _voice_chat_stream_cache.get(cache_key) or {}
            cached_raw = cached.get("messages") if isinstance(cached, dict) else None
            messages = [dict(item) for item in cached_raw if isinstance(item, dict)] if isinstance(cached_raw, list) else []
            try:
                previous_total = int(cached.get("total_message_count") or len(messages)) if isinstance(cached, dict) else len(messages)
            except Exception:
                previous_total = len(messages)
            messages.append(dict(msg))
            total_count = max(previous_total + 1, len(messages))
            messages = messages[-VOICE_CHAT_VISIBLE_TAIL:]
            effective_has_more_before = bool(has_more_before) or total_count > len(messages)
            effective_before_cursor = str(before_cursor or "")
            if not effective_before_cursor and effective_has_more_before:
                effective_before_cursor = str(max(0, total_count - len(messages)))
            last_refresh_ts = time.time()
            _publish_voice_chat_stream(
                webspace_id,
                target_node_id,
                messages,
                last_refresh_ts,
                before_cursor=effective_before_cursor,
                has_more_before=effective_has_more_before,
                total_message_count=total_count,
            )
            _schedule_voice_chat_persist(
                webspace_id,
                target_node_id,
                messages,
                last_refresh_ts,
                before_cursor=effective_before_cursor,
                has_more_before=effective_has_more_before,
                total_message_count=total_count,
            )

        def _read_voice_chat_state(data_map: Any, target_node_id: str | None) -> dict:
            current = data_map.to_json() if hasattr(data_map, "to_json") else {}
            if isinstance(current, str):
                try:
                    current = json.loads(current)
                except Exception:
                    current = {}
            if not isinstance(current, dict):
                return {}
            path = _voice_chat_data_path(target_node_id)
            segments = [segment for segment in path.split("/") if segment]
            cursor: Any = current
            for segment in segments[1:]:
                if not isinstance(cursor, dict):
                    return {}
                cursor = cursor.get(segment)
            return dict(cursor) if isinstance(cursor, dict) else {}

        def _write_voice_chat_state(data_map: Any, txn: Any, target_node_id: str | None, value: dict) -> None:
            path = _voice_chat_data_path(target_node_id)
            segments = [segment for segment in path.split("/") if segment]
            if len(segments) < 2:
                return
            top_key = segments[1]
            if len(segments) == 2:
                data_map.set(txn, top_key, value)
                return
            current_top = data_map.get(top_key)
            changed, merged = _merge_nested_path(current_top, segments[2:], value)
            if changed:
                data_map.set(txn, top_key, merged)

        def _dialog_channel_snapshot(webspace_id: str, *, event: str = "snapshot") -> dict[str, Any]:
            ws = str(webspace_id or "default").strip() or "default"
            active = dialog_runtime.get_active_channel(ws)
            if active is None:
                active = _restore_active_dialog_channel_from_store(ws)
            active_dict = active.as_dict() if active is not None else None
            active_id = str(active.channel_id).strip() if active is not None else "general"
            if not active_id:
                active_id = "general"
            now = time.time()
            active_agent = _active_agent_projection(active_dict, active_id)
            general_agent = _general_agent_projection()
            general_channel = {
                "id": "general",
                "label": "General",
                "owner": GENERAL_DIALOG_AGENT_OWNER,
                "route_id": "voice_chat",
                "conversation_id": _general_conversation_id(ws),
                "active_agent_id": general_agent["id"],
                "active_agent_label": general_agent["label"],
                "active_agent_gender": general_agent.get("gender"),
                "active_agent_voice": general_agent.get("voice"),
                "active_agent_icon": general_agent.get("icon"),
                "active_agent_avatar_ref": general_agent.get("avatar_ref"),
                "active_agent": general_agent,
                "policy": _dialog_channel_policy("general", default_tool="voice_chat_skill.handle_text"),
                "active": active_id == "general",
            }
            conversational_channel = {
                "id": "conversational",
                "label": "Conversational",
                "owner": "skill:conversation_companions",
                "route_id": "voice_chat",
                "conversation_id": _skill_conversation_id("conversation_companions", ws),
                "default_skill": "conversation_companions",
                "default_tool": "talk",
                "policy": _dialog_channel_policy("conversational", default_tool="conversation_companions.talk"),
                "active": active_id == "conversational",
            }
            builder_channel = {
                "id": "builder",
                "label": "Builder",
                "owner": f"skill:{BUILDER_SKILL_ID}",
                "route_id": "voice_chat",
                "conversation_id": _skill_conversation_id(BUILDER_SKILL_ID, ws),
                "default_skill": BUILDER_SKILL_ID,
                "default_tool": "chat",
                "policy": _dialog_channel_policy("builder", default_tool=f"{BUILDER_SKILL_ID}.chat"),
                "active": active_id == "builder",
            }
            channels: list[dict[str, Any]] = [
                general_channel,
                conversational_channel,
                builder_channel,
            ]
            try:
                _seed_manifest_dialog_channels(ws)
                persisted_channels = conversation_store.list_dialog_channels(ws)
            except Exception:
                persisted_channels = []
            for persisted in persisted_channels:
                pid = str(persisted.get("id") or persisted.get("channel_id") or "").strip()
                if not pid:
                    continue
                meta = persisted.get("meta") if isinstance(persisted.get("meta"), dict) else {}
                normalized = {
                    "id": pid,
                    "label": str(persisted.get("label") or _dialog_channel_label(pid)).strip() or pid,
                    "owner": persisted.get("owner"),
                    "route_id": persisted.get("route_id") or "voice_chat",
                    "conversation_id": persisted.get("conversation_id"),
                    "active_agent_id": persisted.get("active_agent_id"),
                    "default_skill": persisted.get("default_skill"),
                    "default_tool": persisted.get("default_tool"),
                    "policy": persisted.get("policy")
                    if isinstance(persisted.get("policy"), dict) and persisted.get("policy")
                    else _dialog_channel_policy(pid, default_tool=str(persisted.get("default_tool") or "")),
                    "meta": meta,
                    "active": pid == active_id,
                }
                for key in (
                    "active_agent_label",
                    "active_agent_gender",
                    "active_agent_voice",
                    "active_agent_icon",
                    "active_agent_avatar_ref",
                ):
                    if meta.get(key) and not normalized.get(key):
                        normalized[key] = meta.get(key)
                existing_index = next((idx for idx, item in enumerate(channels) if item.get("id") == pid), -1)
                if existing_index >= 0:
                    channels[existing_index].update({k: v for k, v in normalized.items() if v not in (None, "")})
                else:
                    channels.append(normalized)
            if active_dict and active_id == "conversational":
                channels[1].update(
                    {
                        "conversation_id": active_dict.get("conversation_id"),
                        "active_agent_id": active_dict.get("active_agent_id"),
                        "active_agent_label": active_agent.get("label"),
                        "active_agent_gender": active_agent.get("gender"),
                        "active_agent_voice": active_agent.get("voice"),
                        "active_agent_icon": active_agent.get("icon"),
                        "active_agent_avatar_ref": active_agent.get("avatar_ref"),
                        "active_agent": active_agent,
                        "default_tool": active_dict.get("default_tool"),
                    }
                )
            elif active_dict and active_id not in {"general", "conversational"}:
                active_channel_projection = {
                    "id": active_id,
                    "label": _dialog_channel_label(active_id),
                    "owner": active_dict.get("owner"),
                    "route_id": active_dict.get("route_id") or "voice_chat",
                    "conversation_id": active_dict.get("conversation_id"),
                    "active_agent_id": active_dict.get("active_agent_id"),
                    "active_agent_label": active_agent.get("label"),
                    "active_agent_gender": active_agent.get("gender"),
                    "active_agent_voice": active_agent.get("voice"),
                    "active_agent_icon": active_agent.get("icon"),
                    "active_agent_avatar_ref": active_agent.get("avatar_ref"),
                    "active_agent": active_agent,
                    "default_skill": active_dict.get("default_skill"),
                    "default_tool": active_dict.get("default_tool"),
                    "policy": _dialog_channel_policy(active_id, default_tool=str(active_dict.get("default_tool") or "")),
                    "active": True,
                }
                existing_index = next((idx for idx, item in enumerate(channels) if item.get("id") == active_id), -1)
                if existing_index >= 0:
                    channels[existing_index].update(active_channel_projection)
                else:
                    channels.append(active_channel_projection)
            if active_dict is None:
                active_dict = {
                    "webspace_id": ws,
                    "channel_id": "general",
                    "owner": GENERAL_DIALOG_AGENT_OWNER,
                    "default_skill": "voice_chat_skill",
                    "default_tool": "handle_text",
                    "conversation_id": _general_conversation_id(ws),
                    "active_agent_id": general_agent["id"],
                    "active_agent_label": general_agent["label"],
                    "active_agent_owner": general_agent["owner"],
                    "active_agent_kind": general_agent["kind"],
                    "active_agent_gender": general_agent.get("gender"),
                    "active_agent_voice": general_agent.get("voice"),
                    "active_agent_icon": general_agent.get("icon"),
                    "active_agent_avatar_ref": general_agent.get("avatar_ref"),
                    "route_id": "voice_chat",
                }
            elif active_agent:
                active_dict = dict(active_dict)
                active_dict.setdefault("active_agent_label", active_agent.get("label"))
                active_dict.setdefault("active_agent_owner", active_agent.get("owner"))
                active_dict.setdefault("active_agent_kind", active_agent.get("kind"))
                active_dict.setdefault("active_agent_gender", active_agent.get("gender"))
                active_dict.setdefault("active_agent_voice", active_agent.get("voice"))
                active_dict.setdefault("active_agent_icon", active_agent.get("icon"))
                active_dict.setdefault("active_agent_avatar_ref", active_agent.get("avatar_ref"))
            for channel in channels:
                _store_dialog_channel_projection(ws, channel)
            _persist_active_dialog_channel(ws, active_id, active_dict, event=event)
            memory_owner = str(active_dict.get("owner") or active_agent.get("owner") or "core").strip() or "core"
            agent_owner = str(active_agent.get("owner") or memory_owner).strip() or memory_owner
            active_conversation_id = str(active_dict.get("conversation_id") or "").strip()
            try:
                visible_tail_projection = (
                    conversation_store.recover_projection_from_store(
                        {},
                        conversation_id=active_conversation_id,
                        limit=VOICE_CHAT_VISIBLE_TAIL,
                        max_items=VOICE_CHAT_HISTORY_LIMIT,
                    )
                    if active_conversation_id
                    else {}
                )
            except Exception:
                visible_tail_projection = {}
            visible_tail = {
                "conversation_id": active_conversation_id,
                "dialog_channel_id": active_id,
                "messages": list(visible_tail_projection.get("messages") or [])
                if isinstance(visible_tail_projection, dict)
                else [],
                "before_cursor": str(visible_tail_projection.get("before_cursor") or "")
                if isinstance(visible_tail_projection, dict)
                else "",
                "has_more_before": bool(visible_tail_projection.get("has_more_before"))
                if isinstance(visible_tail_projection, dict)
                else False,
                "total_message_count": int(visible_tail_projection.get("total_message_count") or 0)
                if isinstance(visible_tail_projection, dict)
                else 0,
                "recovery": visible_tail_projection.get("recovery")
                if isinstance(visible_tail_projection, dict)
                else None,
            }
            try:
                memory_preview = conversation_store.list_memory(
                    owner=agent_owner,
                    subject_id=str(active_agent.get("id") or "") or None,
                    limit=5,
                )
            except Exception:
                memory_preview = []
            try:
                last_turn_trace = conversation_store.latest_turn_trace(
                    webspace_id=ws,
                    conversation_id=active_conversation_id or None,
                )
            except Exception:
                last_turn_trace = None
            memory = {
                "status": "node_store_ready" if conversation_store.available() else "projection_only",
                "storage": "node_conversation_store",
                "item_count_preview": len(memory_preview),
                "items_preview": memory_preview,
                "scopes": [
                    {"id": "global_user", "label": "Global user", "owner": "core", "writable_by": ["core"]},
                    {"id": "core", "label": "Core", "owner": "core", "writable_by": ["core"]},
                    {
                        "id": "skill_user",
                        "label": "Skill user",
                        "owner": memory_owner,
                        "writable_by": [memory_owner],
                    },
                    {
                        "id": "agent_user",
                        "label": "Agent user",
                        "owner": agent_owner,
                        "active_agent_id": active_agent.get("id"),
                        "writable_by": [agent_owner],
                    },
                    {
                        "id": "conversation",
                        "label": "Conversation",
                        "conversation_id": active_conversation_id,
                        "owner": memory_owner,
                    },
                ],
                "policy": {
                    "default_consent": "unknown",
                    "cross_owner_reuse": "deny_by_default",
                    "write_requires_owner": True,
                },
            }
            return {
                "active_channel_id": active_id,
                "active_channel": active_dict,
                "active_agent": active_agent,
                "channels": channels,
                "visible_tail": visible_tail,
                "memory": memory,
                "last_turn_trace": last_turn_trace,
                "event": event,
                "webspace_id": ws,
                "updated_at": now,
            }

        async def _mutate_data_map(
            webspace_id: str,
            mutator: Callable[[Any, Any], None],
            *,
            source: str = "router.service",
            channel: str = "core.router.live_room",
            prefer_live_room: bool = True,
        ) -> None:
            def _apply(ydoc: Any, txn: Any) -> None:
                mutator(ydoc.get_map("data"), txn)

            if prefer_live_room and mutate_live_room(
                webspace_id,
                _apply,
                root_names=["data"],
                source=source,
                owner="core:router",
                channel=channel,
            ):
                return
            async with self._router_yjs_write_meta():
                async with async_get_ydoc(
                    webspace_id,
                    publish_live_room=True,
                    load_mark_roots=["data"],
                    write_source=source,
                    write_owner="core:router",
                    write_channel="core.router.async",
                ) as ydoc:
                    with ydoc.begin_transaction() as txn:
                        _apply(ydoc, txn)

        async def _write_dialog_state(webspace_id: str, *, event: str = "snapshot") -> None:
            snapshot = _dialog_channel_snapshot(webspace_id, event=event)

            def _mutator(data_map: Any, txn: Any) -> None:
                data_map.set(txn, "dialog", snapshot)

            try:
                await asyncio.wait_for(
                    _mutate_data_map(
                        webspace_id,
                        _mutator,
                        channel="core.router.dialog.store",
                        prefer_live_room=True,
                    ),
                    timeout=_voice_chat_yjs_timeout_s(),
                )
            except asyncio.TimeoutError:
                logging.getLogger("adaos.router.dialog").warning(
                    "dialog.state yjs write timed out webspace=%s event=%s",
                    webspace_id,
                    event,
                )
            except Exception:
                logging.getLogger("adaos.router.dialog").warning(
                    "dialog.state yjs write failed webspace=%s event=%s",
                    webspace_id,
                    event,
                    exc_info=True,
                )

        def _schedule_dialog_state_write(webspace_id: str, *, event: str = "snapshot") -> None:
            ws = str(webspace_id or "default").strip() or "default"
            event_name = str(event or "snapshot").strip() or "snapshot"
            self._dialog_state_pending_events[ws] = event_name
            existing = self._dialog_state_tasks.get(ws)
            if existing is not None and not existing.done():
                return

            async def _run() -> None:
                try:
                    while True:
                        next_event = self._dialog_state_pending_events.pop(ws, event_name)
                        await _write_dialog_state(ws, event=next_event)
                        if ws not in self._dialog_state_pending_events:
                            break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.getLogger("adaos.router.dialog").warning(
                        "dialog.state background write failed webspace=%s event=%s",
                        ws,
                        event_name,
                        exc_info=True,
                    )

            try:
                task = asyncio.create_task(_run(), name=f"dialog-state:{ws}")
            except RuntimeError:
                return
            self._dialog_state_tasks[ws] = task

            def _forget(done: asyncio.Task[None]) -> None:
                if self._dialog_state_tasks.get(ws) is done:
                    self._dialog_state_tasks.pop(ws, None)
                try:
                    done.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logging.getLogger("adaos.router.dialog").warning(
                        "dialog.state background task failed webspace=%s",
                        ws,
                        exc_info=True,
                    )

            task.add_done_callback(_forget)

        async def _ensure_voice_chat_state(webspace_id: str, target_node_id: str | None = None) -> None:
            def _mutator(data_map: Any, txn: Any) -> None:
                current = _read_voice_chat_state(data_map, target_node_id)
                if isinstance(current, dict) and isinstance(current.get("messages"), list):
                    return
                _write_voice_chat_state(
                    data_map,
                    txn,
                    target_node_id,
                    {
                        "messages": [],
                        "last_refresh_ts": time.time(),
                    },
                )

            try:
                await asyncio.wait_for(
                    _mutate_data_map(
                        webspace_id,
                        _mutator,
                        channel="core.router.voice_chat.live_room",
                        prefer_live_room=True,
                    ),
                    timeout=_voice_chat_yjs_timeout_s(),
                )
            except asyncio.TimeoutError:
                self._vlog.warning(
                    "voice_chat.ensure yjs write timed out webspace=%s node_id=%s",
                    webspace_id,
                    str(target_node_id or "").strip() or None,
                )

        def _voice_chat_topic_id_from_sources(*sources: Any) -> str:
            for source in sources:
                if not isinstance(source, dict):
                    continue
                for key in (
                    "thread_id",
                    "conversation_thread_id",
                    "conversation_topic_id",
                    "topic_id",
                    "conversationTopicId",
                    "topicId",
                ):
                    value = source.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                meta = source.get("_meta") if isinstance(source.get("_meta"), dict) else {}
                if meta:
                    nested = _voice_chat_topic_id_from_sources(meta)
                    if nested:
                        return nested
                params = source.get("params") if isinstance(source.get("params"), dict) else {}
                if params:
                    nested = _voice_chat_topic_id_from_sources(params)
                    if nested:
                        return nested
            return ""

        def _source_mentions_builder_dialog(webspace_id: str, *sources: Any) -> bool:
            ws = str(webspace_id or "default").strip() or "default"
            builder_conversation_id = _skill_conversation_id(BUILDER_SKILL_ID, ws)
            for source in sources:
                if not isinstance(source, dict):
                    continue
                channel_id = str(
                    source.get("dialog_channel_id")
                    or source.get("channel_id")
                    or source.get("dialogChannelId")
                    or source.get("channelId")
                    or ""
                ).strip()
                if channel_id == BUILDER_DIALOG_CHANNEL_ID:
                    return True
                conversation_id = str(
                    source.get("conversation_id")
                    or source.get("conversationId")
                    or ""
                ).strip()
                if conversation_id == builder_conversation_id:
                    return True
                owner = str(source.get("owner") or source.get("conversation_owner") or "").strip()
                if owner == f"skill:{BUILDER_SKILL_ID}":
                    return True
                default_tool = str(source.get("default_tool") or source.get("tool") or "").strip()
                if default_tool.startswith(f"{BUILDER_SKILL_ID}."):
                    return True
                meta = source.get("_meta") if isinstance(source.get("_meta"), dict) else {}
                if meta and _source_mentions_builder_dialog(ws, meta):
                    return True
                params = source.get("params") if isinstance(source.get("params"), dict) else {}
                if params and _source_mentions_builder_dialog(ws, params):
                    return True
            return False

        def _builder_workbench_topic_id(webspace_id: str) -> str:
            try:
                from adaos.services.builder.workbench import BuilderWorkbenchService

                binding = BuilderWorkbenchService().get_workspace_binding(webspace_id)
            except Exception:
                return ""
            if not isinstance(binding, dict):
                return ""
            dialog = binding.get("dialog") if isinstance(binding.get("dialog"), dict) else {}
            dialog_topic = dialog.get("topic") if isinstance(dialog.get("topic"), dict) else {}
            topic_id = str(
                dialog.get("thread_id")
                or dialog.get("topic_id")
                or dialog_topic.get("thread_id")
                or dialog_topic.get("topic_id")
                or ""
            ).strip()
            if topic_id.startswith("prompt-project:scenario:"):
                return topic_id
            return ""

        def _normalize_builder_topic_id(webspace_id: str, topic_id: Any, *sources: Any) -> str:
            token = str(topic_id or "").strip()
            legacy = token.startswith("thread.builder.") or token.startswith("builder:")
            if token and not legacy:
                return token
            if not legacy and not _source_mentions_builder_dialog(webspace_id, *sources):
                return token
            binding_topic = _builder_workbench_topic_id(webspace_id)
            return binding_topic or token

        def _voice_chat_projection_identity(messages: list[dict[str, Any]]) -> tuple[str, str, str]:
            for item in reversed([dict(entry) for entry in messages if isinstance(entry, dict)]):
                conversation_id = str(item.get("conversation_id") or "").strip()
                channel_id = str(item.get("dialog_channel_id") or item.get("channel_id") or "").strip()
                topic_id = _voice_chat_topic_id_from_sources(item)
                if conversation_id or channel_id or topic_id:
                    return conversation_id, channel_id, topic_id
            return "", "", ""

        def _voice_chat_persist_key(webspace_id: str, target_node_id: str | None) -> tuple[str, str]:
            return (
                str(webspace_id or "default").strip() or "default",
                str(target_node_id or "").strip(),
            )

        def _voice_chat_persist_signature(
            messages: list[dict[str, Any]],
            *,
            before_cursor: str | None = None,
            has_more_before: bool = False,
            total_message_count: int | None = None,
        ) -> str:
            try:
                return json.dumps(
                    {
                        "messages": [dict(item) for item in messages if isinstance(item, dict)],
                        "before_cursor": str(before_cursor or ""),
                        "has_more_before": bool(has_more_before),
                        "total_message_count": int(
                            total_message_count if total_message_count is not None else len(messages)
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            except Exception:
                return repr((messages, str(before_cursor or ""), bool(has_more_before), total_message_count))

        def _voice_chat_state_signature(current: dict[str, Any]) -> str:
            raw_messages = current.get("messages") if isinstance(current, dict) else None
            messages = [dict(item) for item in raw_messages if isinstance(item, dict)] if isinstance(raw_messages, list) else []
            try:
                total_count = int(current.get("total_message_count") or len(messages)) if isinstance(current, dict) else len(messages)
            except Exception:
                total_count = len(messages)
            return _voice_chat_persist_signature(
                messages,
                before_cursor=str(current.get("before_cursor") or "") if isinstance(current, dict) else "",
                has_more_before=bool(current.get("has_more_before")) if isinstance(current, dict) else False,
                total_message_count=total_count,
            )

        def _conversation_id_for_dialog_channel(webspace_id: str, channel_id: str) -> str:
            ws = str(webspace_id or "default").strip() or "default"
            cid = str(channel_id or "").strip()
            if cid == GENERAL_DIALOG_CHANNEL_ID:
                return _general_conversation_id(ws)
            if not cid:
                return ""
            try:
                channel = conversation_store.get_dialog_channel(ws, cid)
            except Exception:
                channel = None
            if isinstance(channel, dict):
                conversation_id = str(channel.get("conversation_id") or "").strip()
                if conversation_id:
                    return conversation_id
            if cid == BUILDER_DIALOG_CHANNEL_ID:
                return _skill_conversation_id(BUILDER_SKILL_ID, ws)
            return ""

        def _resolve_voice_chat_conversation_id(
            webspace_id: str,
            *,
            requested_conversation_id: Any = None,
            requested_channel_id: Any = None,
            current: dict[str, Any] | None = None,
            messages: list[dict[str, Any]] | None = None,
        ) -> str:
            ws = str(webspace_id or "default").strip() or "default"
            explicit_conversation_id = str(requested_conversation_id or "").strip()
            if explicit_conversation_id:
                return explicit_conversation_id
            explicit_channel_id = str(requested_channel_id or "").strip()
            if explicit_channel_id:
                resolved = _conversation_id_for_dialog_channel(ws, explicit_channel_id)
                if resolved:
                    return resolved
            try:
                active = dialog_runtime.get_active_channel(ws) or _restore_active_dialog_channel_from_store(ws)
                if active is not None:
                    conversation_id = str(active.as_dict().get("conversation_id") or "").strip()
                    if conversation_id:
                        return conversation_id
            except Exception:
                pass
            try:
                active_row = conversation_store.get_active_dialog_channel(ws)
            except Exception:
                active_row = None
            if isinstance(active_row, dict):
                conversation_id = str(active_row.get("conversation_id") or "").strip()
                if conversation_id:
                    return conversation_id
                channel_id = str(active_row.get("channel_id") or "").strip()
                if channel_id:
                    resolved = _conversation_id_for_dialog_channel(ws, channel_id)
                    if resolved:
                        return resolved
            if isinstance(current, dict):
                conversation_id = str(current.get("conversation_id") or "").strip()
                if conversation_id:
                    return conversation_id
                channel_id = str(current.get("dialog_channel_id") or current.get("channel_id") or "").strip()
                if channel_id:
                    resolved = _conversation_id_for_dialog_channel(ws, channel_id)
                    if resolved:
                        return resolved
            if messages:
                conversation_id, channel_id, _topic_id = _voice_chat_projection_identity(messages)
                if conversation_id:
                    return conversation_id
                if channel_id:
                    resolved = _conversation_id_for_dialog_channel(ws, channel_id)
                    if resolved:
                        return resolved
            return _general_conversation_id(ws)

        def _publish_voice_chat_stream(
            webspace_id: str,
            target_node_id: str | None,
            messages: list[dict[str, Any]],
            last_refresh_ts: float,
            *,
            before_cursor: str | None = None,
            has_more_before: bool = False,
            total_message_count: int | None = None,
            suppress_unchanged: bool = False,
        ) -> str:
            # Keep the browser stream as a compact tail. Voice must never wait
            # on heavier YJS history writes before dispatching NLU.
            cached_messages = [dict(item) for item in messages if isinstance(item, dict)]
            total_count = int(total_message_count if total_message_count is not None else len(cached_messages))
            conversation_id, dialog_channel_id, topic_id = _voice_chat_projection_identity(cached_messages)
            signature = _voice_chat_persist_signature(
                cached_messages,
                before_cursor=str(before_cursor or ""),
                has_more_before=bool(has_more_before),
                total_message_count=total_count,
            )
            cache_key = (str(webspace_id or "").strip(), str(target_node_id or "").strip())
            current_cache = _voice_chat_stream_cache.get(cache_key) or {}
            if suppress_unchanged and str(current_cache.get("stream_signature") or "") == signature:
                return signature
            stream_params = {
                key: value
                for key, value in {
                    "conversation_id": conversation_id,
                    "dialog_channel_id": dialog_channel_id,
                    "conversation_topic_id": topic_id,
                    "thread_id": topic_id,
                }.items()
                if str(value or "").strip()
            }
            _voice_chat_stream_cache[cache_key] = {
                "messages": cached_messages,
                "last_refresh_ts": last_refresh_ts,
                "message_count": len(cached_messages),
                "total_message_count": total_count,
                "has_more_before": bool(has_more_before),
                "before_cursor": str(before_cursor or ""),
                "history_mode": "compact_tail",
                "conversation_id": conversation_id,
                "dialog_channel_id": dialog_channel_id,
                "conversation_topic_id": topic_id,
                "thread_id": topic_id,
                "stream_signature": signature,
            }
            payload: dict[str, Any] = {
                "receiver": "voice_chat.messages",
                "webspace_id": webspace_id,
                "owner": "skill:voice_chat_skill",
                "data": {
                    "messages": cached_messages,
                    "last_refresh_ts": last_refresh_ts,
                    "message_count": len(cached_messages),
                    "total_message_count": total_count,
                    "has_more_before": bool(has_more_before),
                    "before_cursor": str(before_cursor or ""),
                    "history_mode": "compact_tail",
                    "conversation_id": conversation_id,
                    "dialog_channel_id": dialog_channel_id,
                    "conversation_topic_id": topic_id,
                    "thread_id": topic_id,
                },
                "_meta": {
                    "webspace_id": webspace_id,
                    "route_id": "voice_chat",
                    "conversation_id": conversation_id,
                    "dialog_channel_id": dialog_channel_id,
                    "conversation_topic_id": topic_id,
                    "thread_id": topic_id,
                },
            }
            if stream_params:
                payload["params"] = dict(stream_params)
                payload["_meta"]["params"] = dict(stream_params)
            if target_node_id:
                payload["node_id"] = target_node_id
                payload["source_node_id"] = target_node_id
                payload["_meta"]["target_node_id"] = target_node_id
                payload["_meta"]["node_id"] = target_node_id
                payload["_meta"]["source_node_id"] = target_node_id
            try:
                self.bus.publish(
                    Event(
                        type="io.out.stream.publish",
                        source="router.voice_chat",
                        ts=time.time(),
                        payload=payload,
                    )
                )
            except Exception:
                pass
            return signature

        def _schedule_voice_chat_persist(
            webspace_id: str,
            target_node_id: str | None,
            messages: list[dict[str, Any]],
            last_refresh_ts: float,
            *,
            before_cursor: str | None = None,
            has_more_before: bool = False,
            total_message_count: int | None = None,
        ) -> None:
            snapshot = [dict(item) for item in messages[-VOICE_CHAT_VISIBLE_TAIL:] if isinstance(item, dict)]
            if not snapshot:
                return
            conversation_id, dialog_channel_id, topic_id = _voice_chat_projection_identity(snapshot)
            key = _voice_chat_persist_key(webspace_id, target_node_id)
            signature = _voice_chat_persist_signature(
                snapshot,
                before_cursor=before_cursor,
                has_more_before=has_more_before,
                total_message_count=total_message_count,
            )
            if self._voice_chat_persist_committed_signatures.get(key) == signature and key not in self._voice_chat_persist_pending:
                return
            current_pending = self._voice_chat_persist_pending.get(key)
            if isinstance(current_pending, dict) and str(current_pending.get("signature") or "") == signature:
                return
            self._voice_chat_persist_pending[key] = {
                "webspace_id": str(webspace_id or "default").strip() or "default",
                "target_node_id": str(target_node_id or "").strip(),
                "snapshot": snapshot,
                "last_refresh_ts": float(last_refresh_ts or time.time()),
                "before_cursor": str(before_cursor or ""),
                "has_more_before": bool(has_more_before),
                "total_message_count": int(total_message_count if total_message_count is not None else len(snapshot)),
                "conversation_id": conversation_id,
                "dialog_channel_id": dialog_channel_id,
                "topic_id": topic_id,
                "signature": signature,
            }

            async def _persist_payload(payload: dict[str, Any]) -> bool:
                payload_snapshot = [
                    dict(item)
                    for item in (payload.get("snapshot") if isinstance(payload.get("snapshot"), list) else [])
                    if isinstance(item, dict)
                ]
                if not payload_snapshot:
                    return True
                payload_signature = str(payload.get("signature") or "")
                payload_webspace_id = str(payload.get("webspace_id") or "default").strip() or "default"
                payload_target_node_id = str(payload.get("target_node_id") or "").strip() or None
                payload_before_cursor = str(payload.get("before_cursor") or "")
                payload_has_more_before = bool(payload.get("has_more_before"))
                payload_total_message_count = int(payload.get("total_message_count") or len(payload_snapshot))
                payload_conversation_id = str(payload.get("conversation_id") or "").strip()
                payload_dialog_channel_id = str(payload.get("dialog_channel_id") or "").strip()
                payload_topic_id = str(payload.get("topic_id") or "").strip()
                payload_last_refresh_ts = float(payload.get("last_refresh_ts") or time.time())

                def _mutator(data_map: Any, txn: Any) -> None:
                    current = _read_voice_chat_state(data_map, payload_target_node_id)
                    if isinstance(current, dict) and _voice_chat_state_signature(current) == payload_signature:
                        return
                    try:
                        current_ts = float(current.get("last_refresh_ts") or 0.0) if isinstance(current, dict) else 0.0
                    except Exception:
                        current_ts = 0.0
                    if current_ts > payload_last_refresh_ts:
                        return
                    _write_voice_chat_state(
                        data_map,
                        txn,
                        payload_target_node_id,
                        {
                            "messages": [dict(item) for item in payload_snapshot],
                            "last_refresh_ts": payload_last_refresh_ts,
                            "message_count": len(payload_snapshot),
                            "total_message_count": payload_total_message_count,
                            "has_more_before": payload_has_more_before,
                            "before_cursor": payload_before_cursor,
                            "history_mode": "compact_tail",
                            "conversation_id": payload_conversation_id,
                            "dialog_channel_id": payload_dialog_channel_id,
                            "conversation_topic_id": payload_topic_id,
                            "thread_id": payload_topic_id,
                        },
                    )

                try:
                    await asyncio.wait_for(
                        _mutate_data_map(
                            payload_webspace_id,
                            _mutator,
                            channel="core.router.voice_chat.live_room",
                            prefer_live_room=True,
                        ),
                        timeout=_voice_chat_yjs_timeout_s(),
                    )
                    return True
                except asyncio.TimeoutError:
                    self._voice_chat_persist_next_allowed_at[key] = time.monotonic() + _voice_chat_persist_failure_backoff_s()
                    self._vlog.warning(
                        "voice_chat.persist yjs write timed out webspace=%s node_id=%s count=%d",
                        payload_webspace_id,
                        payload_target_node_id,
                        len(payload_snapshot),
                    )
                    return False
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._voice_chat_persist_next_allowed_at[key] = time.monotonic() + _voice_chat_persist_failure_backoff_s()
                    self._vlog.warning(
                        "voice_chat.persist yjs write failed webspace=%s node_id=%s",
                        payload_webspace_id,
                        payload_target_node_id,
                        exc_info=True,
                    )
                    return False

            existing = self._voice_chat_persist_tasks_by_key.get(key)
            if existing is not None and not existing.done():
                return

            async def _persist() -> None:
                while True:
                    payload = self._voice_chat_persist_pending.pop(key, None)
                    if not isinstance(payload, dict):
                        return
                    payload_signature = str(payload.get("signature") or "")
                    if self._voice_chat_persist_committed_signatures.get(key) == payload_signature:
                        if key not in self._voice_chat_persist_pending:
                            return
                        continue
                    delay = max(0.0, _voice_chat_persist_debounce_s())
                    next_allowed = float(self._voice_chat_persist_next_allowed_at.get(key) or 0.0)
                    if next_allowed > 0.0:
                        delay = max(delay, max(0.0, next_allowed - time.monotonic()))
                    if delay > 0.0:
                        await asyncio.sleep(delay)
                    ok = await _persist_payload(payload)
                    if ok:
                        self._voice_chat_persist_committed_signatures[key] = payload_signature
                        self._voice_chat_persist_next_allowed_at.pop(key, None)
                    else:
                        self._voice_chat_persist_pending.setdefault(key, payload)
                        return
                    if key not in self._voice_chat_persist_pending:
                        return

            try:
                task = asyncio.create_task(
                    _persist(),
                    name=f"voice-chat-persist:{str(webspace_id or 'default')}:{str(target_node_id or 'shared')}",
                )
            except RuntimeError:
                return
            self._voice_chat_persist_tasks.add(task)
            self._voice_chat_persist_tasks_by_key[key] = task

            def _forget(done: asyncio.Task[None]) -> None:
                self._voice_chat_persist_tasks.discard(done)
                if self._voice_chat_persist_tasks_by_key.get(key) is done:
                    self._voice_chat_persist_tasks_by_key.pop(key, None)
                try:
                    done.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self._vlog.warning("voice_chat.persist background task failed", exc_info=True)

            task.add_done_callback(_forget)

        async def _publish_voice_chat_snapshot(
            webspace_id: str,
            target_node_id: str | None,
            *,
            conversation_id: Any = None,
            dialog_channel_id: Any = None,
            thread_id: Any = None,
            persist: bool = False,
            suppress_unchanged: bool = False,
        ) -> None:
            cache_key = (str(webspace_id or "").strip(), str(target_node_id or "").strip())
            current = _voice_chat_stream_cache.get(cache_key) or {}
            raw_messages = current.get("messages") if isinstance(current, dict) else None
            messages = [dict(item) for item in raw_messages if isinstance(item, dict)] if isinstance(raw_messages, list) else []
            resolved_conversation_id = _resolve_voice_chat_conversation_id(
                webspace_id,
                requested_conversation_id=conversation_id,
                requested_channel_id=dialog_channel_id,
                current=current if isinstance(current, dict) else {},
                messages=messages,
            )
            resolved_topic_id = _voice_chat_topic_id_from_sources(
                {"thread_id": thread_id} if thread_id is not None else {},
                {"conversation_topic_id": thread_id} if thread_id is not None else {},
            )
            resolved_topic_id = _normalize_builder_topic_id(
                webspace_id,
                resolved_topic_id,
                {
                    "conversation_id": conversation_id,
                    "dialog_channel_id": dialog_channel_id,
                },
                current if isinstance(current, dict) else {},
                *messages,
            )
            if not resolved_topic_id:
                resolved_topic_id = _voice_chat_topic_id_from_sources(current if isinstance(current, dict) else {}, *messages)
            ledger_projection: dict[str, Any] = {}
            ledger_messages: list[dict[str, Any]] = []
            try:
                recovered = conversation_store.recover_projection_from_store(
                    current if isinstance(current, dict) else {},
                    conversation_id=resolved_conversation_id,
                    thread_id=resolved_topic_id or None,
                    limit=VOICE_CHAT_VISIBLE_TAIL,
                    max_items=VOICE_CHAT_HISTORY_LIMIT,
                )
                ledger_projection = dict(recovered) if isinstance(recovered, dict) else {}
                raw_ledger_messages = ledger_projection.get("messages")
                if isinstance(raw_ledger_messages, list):
                    ledger_messages = [dict(item) for item in raw_ledger_messages if isinstance(item, dict)]
            except Exception:
                ledger_projection = {}
                ledger_messages = []
            if ledger_messages and (
                not messages
                or bool(
                    isinstance(ledger_projection.get("recovery"), dict)
                    and ledger_projection["recovery"].get("recovered")
                )
            ):
                last_refresh_ts = time.time()
                try:
                    recovery = ledger_projection.get("recovery") if isinstance(ledger_projection.get("recovery"), dict) else {}
                    self._vlog.debug(
                        "voice_chat.snapshot recovered from store webspace=%s target=%s conversation=%s thread=%s messages=%s total=%s reason=%s",
                        webspace_id,
                        str(target_node_id or "").strip(),
                        resolved_conversation_id,
                        resolved_topic_id or "",
                        len(ledger_messages),
                        int(ledger_projection.get("total_message_count") or len(ledger_messages)),
                        str(recovery.get("reason") or ""),
                    )
                except Exception:
                    pass
                published_signature = _publish_voice_chat_stream(
                    webspace_id,
                    target_node_id,
                    ledger_messages,
                    last_refresh_ts,
                    before_cursor=str(ledger_projection.get("before_cursor") or ""),
                    has_more_before=bool(ledger_projection.get("has_more_before")),
                    total_message_count=int(ledger_projection.get("total_message_count") or len(ledger_messages)),
                )
                if suppress_unchanged:
                    _voice_chat_snapshot_published[cache_key] = (time.monotonic(), published_signature)
                if persist:
                    _schedule_voice_chat_persist(
                        webspace_id,
                        target_node_id,
                        ledger_messages,
                        last_refresh_ts,
                        before_cursor=str(ledger_projection.get("before_cursor") or ""),
                        has_more_before=bool(ledger_projection.get("has_more_before")),
                        total_message_count=int(ledger_projection.get("total_message_count") or len(ledger_messages)),
                    )
                return
            if messages:
                cached_conversation_id = str(current.get("conversation_id") or "").strip() if isinstance(current, dict) else ""
                if not cached_conversation_id:
                    cached_conversation_id, _channel_id, _topic_id = _voice_chat_projection_identity(messages)
                cache_matches_request = True
                if cached_conversation_id and cached_conversation_id != resolved_conversation_id:
                    cache_matches_request = False
                cached_topic_id = _voice_chat_topic_id_from_sources(current if isinstance(current, dict) else {}, *messages)
                if resolved_topic_id and cached_topic_id and cached_topic_id != resolved_topic_id:
                    cache_matches_request = False
                if cache_matches_request:
                    before_cursor = str(current.get("before_cursor") or "") if isinstance(current, dict) else ""
                    has_more_before = bool(current.get("has_more_before")) if isinstance(current, dict) else False
                    total_message_count = int(current.get("total_message_count") or len(messages)) if isinstance(current, dict) else len(messages)
                    signature = str(current.get("stream_signature") or "") if isinstance(current, dict) else ""
                    if not signature:
                        signature = _voice_chat_persist_signature(
                            messages,
                            before_cursor=before_cursor,
                            has_more_before=has_more_before,
                            total_message_count=total_message_count,
                        )
                    if suppress_unchanged:
                        last = _voice_chat_snapshot_published.get(cache_key)
                        min_interval = _voice_chat_snapshot_republish_interval_s()
                        now_monotonic = time.monotonic()
                        if last and last[1] == signature and (now_monotonic - float(last[0] or 0.0)) < min_interval:
                            return
                    try:
                        self._vlog.debug(
                            "voice_chat.snapshot publishing cache webspace=%s target=%s conversation=%s thread=%s messages=%s total=%s",
                            webspace_id,
                            str(target_node_id or "").strip(),
                            resolved_conversation_id,
                            resolved_topic_id or cached_topic_id or "",
                            len(messages),
                            total_message_count,
                        )
                    except Exception:
                        pass
                    last_refresh_ts = float(current.get("last_refresh_ts") or time.time()) if isinstance(current, dict) else time.time()
                    published_signature = _publish_voice_chat_stream(
                        webspace_id,
                        target_node_id,
                        messages,
                        last_refresh_ts,
                        before_cursor=before_cursor,
                        has_more_before=has_more_before,
                        total_message_count=total_message_count,
                    )
                    if suppress_unchanged:
                        _voice_chat_snapshot_published[cache_key] = (time.monotonic(), published_signature)
                    return
            try:
                projection = ledger_projection or conversation_store.recover_projection_from_store(
                    current if isinstance(current, dict) else {},
                    conversation_id=resolved_conversation_id,
                    thread_id=resolved_topic_id or None,
                    limit=VOICE_CHAT_VISIBLE_TAIL,
                    max_items=VOICE_CHAT_HISTORY_LIMIT,
                )
            except Exception:
                projection = {}
            store_messages = projection.get("messages") if isinstance(projection, dict) else None
            if isinstance(store_messages, list) and store_messages:
                last_refresh_ts = time.time()
                stream_messages = [dict(item) for item in store_messages if isinstance(item, dict)]
                try:
                    self._vlog.debug(
                        "voice_chat.snapshot recovered from store webspace=%s target=%s conversation=%s thread=%s messages=%s total=%s",
                        webspace_id,
                        str(target_node_id or "").strip(),
                        resolved_conversation_id,
                        resolved_topic_id or "",
                        len(stream_messages),
                        int(projection.get("total_message_count") or len(stream_messages)),
                    )
                except Exception:
                    pass
                _publish_voice_chat_stream(
                    webspace_id,
                    target_node_id,
                    stream_messages,
                    last_refresh_ts,
                    before_cursor=str(projection.get("before_cursor") or ""),
                    has_more_before=bool(projection.get("has_more_before")),
                    total_message_count=int(projection.get("total_message_count") or len(stream_messages)),
                )
                if suppress_unchanged:
                    _voice_chat_snapshot_published[cache_key] = (
                        time.monotonic(),
                        _voice_chat_persist_signature(
                            stream_messages,
                            before_cursor=str(projection.get("before_cursor") or ""),
                            has_more_before=bool(projection.get("has_more_before")),
                            total_message_count=int(projection.get("total_message_count") or len(stream_messages)),
                        ),
                    )
                if persist:
                    _schedule_voice_chat_persist(
                        webspace_id,
                        target_node_id,
                        stream_messages,
                        last_refresh_ts,
                        before_cursor=str(projection.get("before_cursor") or ""),
                        has_more_before=bool(projection.get("has_more_before")),
                        total_message_count=int(projection.get("total_message_count") or len(stream_messages)),
                    )
                return
            if not messages:
                return

        async def _publish_voice_chat_history_more(
            webspace_id: str,
            target_node_id: str | None,
            before_cursor: Any,
            *,
            conversation_id: Any = None,
            dialog_channel_id: Any = None,
            thread_id: Any = None,
        ) -> None:
            cache_key = (str(webspace_id or "").strip(), str(target_node_id or "").strip())
            cached = _voice_chat_stream_cache.get(cache_key) or {}
            cached_raw = cached.get("messages") if isinstance(cached, dict) else None
            cached_messages = [dict(item) for item in cached_raw if isinstance(item, dict)] if isinstance(cached_raw, list) else []
            resolved_conversation_id = _resolve_voice_chat_conversation_id(
                webspace_id,
                requested_conversation_id=conversation_id,
                requested_channel_id=dialog_channel_id,
                current=cached if isinstance(cached, dict) else {},
                messages=cached_messages,
            )
            resolved_topic_id = _voice_chat_topic_id_from_sources(
                {"thread_id": thread_id} if thread_id is not None else {},
            )
            resolved_topic_id = _normalize_builder_topic_id(
                webspace_id,
                resolved_topic_id,
                {
                    "conversation_id": conversation_id,
                    "dialog_channel_id": dialog_channel_id,
                },
                cached if isinstance(cached, dict) else {},
                *cached_messages,
            )
            if not resolved_topic_id:
                resolved_topic_id = _voice_chat_topic_id_from_sources(cached if isinstance(cached, dict) else {}, *cached_messages)
            try:
                projection = conversation_store.list_projection(
                    resolved_conversation_id,
                    thread_id=resolved_topic_id or None,
                    before_cursor=before_cursor,
                    limit=VOICE_CHAT_VISIBLE_TAIL,
                    max_items=VOICE_CHAT_HISTORY_LIMIT,
                )
            except Exception:
                projection = {}
            store_messages = projection.get("messages") if isinstance(projection, dict) else None
            if not isinstance(store_messages, list) or not store_messages:
                await _publish_voice_chat_snapshot(
                    webspace_id,
                    target_node_id,
                    conversation_id=resolved_conversation_id,
                    dialog_channel_id=dialog_channel_id,
                    thread_id=resolved_topic_id,
                )
                return
            window = [dict(item) for item in store_messages if isinstance(item, dict)]
            last_refresh_ts = time.time()
            _publish_voice_chat_stream(
                webspace_id,
                target_node_id,
                window,
                last_refresh_ts,
                before_cursor=str(projection.get("before_cursor") or ""),
                has_more_before=bool(projection.get("has_more_before")),
                total_message_count=int(projection.get("total_message_count") or len(window)),
            )

        async def _append_voice_chat_message(
            webspace_id: str,
            msg: dict,
            target_node_id: str | None = None,
        ) -> None:
            clean_msg = dict(msg)
            context = _voice_message_dialog_context(webspace_id, clean_msg)
            meta = clean_msg.get("_meta") if isinstance(clean_msg.get("_meta"), dict) else {}
            channel_id = str(context.get("channel_id") or GENERAL_DIALOG_CHANNEL_ID)
            conversation_id = str(context.get("conversation_id") or "")
            worker_webspace_id = str(webspace_id or "default").strip() or "default"
            topic_id = _normalize_builder_topic_id(
                worker_webspace_id,
                _voice_chat_topic_id_from_sources(clean_msg, meta),
                clean_msg,
                meta,
                context.get("channel") if isinstance(context.get("channel"), dict) else {},
                context,
            )
            owner = str(context.get("owner") or GENERAL_DIALOG_AGENT_OWNER)
            route_id = str(context.get("route_id") or "voice_chat")
            agent = context.get("agent") if isinstance(context.get("agent"), dict) else {}
            if str(clean_msg.get("from") or "").strip() == "hub":
                clean_msg.setdefault("active_agent_id", str(agent.get("id") or ""))
                clean_msg.setdefault("active_agent_label", str(agent.get("label") or ""))
                if agent.get("gender"):
                    clean_msg.setdefault("active_agent_gender", str(agent.get("gender") or ""))
                    clean_msg.setdefault("voice_gender", str(agent.get("gender") or ""))
                if agent.get("voice"):
                    clean_msg.setdefault("active_agent_voice", str(agent.get("voice") or ""))
                    clean_msg.setdefault("voice", str(agent.get("voice") or ""))
                if agent.get("icon"):
                    clean_msg.setdefault("active_agent_icon", str(agent.get("icon") or ""))
                    clean_msg.setdefault("agent_icon", str(agent.get("icon") or ""))
                if agent.get("avatar_ref"):
                    clean_msg.setdefault("active_agent_avatar_ref", str(agent.get("avatar_ref") or ""))
                    clean_msg.setdefault("agent_avatar_ref", str(agent.get("avatar_ref") or ""))
                if isinstance(agent.get("voice_profile"), dict):
                    clean_msg.setdefault("voice_profile", dict(agent.get("voice_profile") or {}))
            clean_msg["dialog_channel_id"] = channel_id
            clean_msg["conversation_id"] = conversation_id
            if topic_id:
                clean_msg["thread_id"] = topic_id
                clean_msg["conversation_topic_id"] = topic_id
                clean_msg["topic_id"] = topic_id
            turn_trace_id = str(meta.get("turn_trace_id") or clean_msg.get("turn_trace_id") or "").strip()
            if turn_trace_id:
                clean_msg["turn_trace_id"] = turn_trace_id
            worker_msg = dict(clean_msg)
            worker_meta = dict(meta)
            worker_context_channel = dict(context.get("channel") or {})
            worker_agent = dict(agent)
            visible_in_active_stream = _voice_chat_message_targets_active_stream(
                worker_webspace_id,
                clean_msg,
                channel_id=channel_id,
            )
            optimistic_published = False
            if visible_in_active_stream:
                try:
                    _fallback_publish_voice_chat_message(webspace_id, target_node_id, clean_msg)
                    optimistic_published = True
                except Exception:
                    optimistic_published = False

            def _append_voice_chat_message_sync() -> dict[str, Any]:
                local_msg = dict(worker_msg)
                local_meta = dict(worker_meta)
                local_visible_in_active_stream = _voice_chat_message_targets_active_stream(
                    worker_webspace_id,
                    local_msg,
                    channel_id=channel_id,
                )
                local_turn_trace_id = str(local_msg.get("turn_trace_id") or "").strip()
                if not local_turn_trace_id:
                    local_turn_trace_id = turn_trace_id
                local_existing_trace = None
                if local_turn_trace_id:
                    try:
                        local_existing_trace = conversation_store.get_turn_trace(local_turn_trace_id)
                    except Exception:
                        local_existing_trace = None
                if not local_turn_trace_id or local_existing_trace is None:
                    try:
                        local_turn_trace_id = conversation_store.start_turn_trace(
                            turn_trace_id=local_turn_trace_id or None,
                            webspace_id=worker_webspace_id,
                            conversation_id=conversation_id,
                            channel_id=channel_id,
                            agent_id=str(worker_agent.get("id") or "") or None,
                            selected_tool=str(
                                local_meta.get("tool")
                                or local_meta.get("default_tool")
                                or worker_context_channel.get("default_tool")
                                or ""
                            ),
                            policy_decision={
                                "selected_channel": channel_id,
                                "selected_owner": owner,
                                "selected_agent_id": str(worker_agent.get("id") or ""),
                                "route_id": route_id,
                            },
                            renderer={"receiver": "voice_chat.messages", "projection": "compact_tail"},
                            message_id=str(local_msg.get("id") or "") or None,
                        ) or ""
                    except Exception:
                        local_turn_trace_id = ""
                if local_turn_trace_id:
                    local_msg["turn_trace_id"] = local_turn_trace_id

                conversation_store.upsert_conversation(
                    conversation_id=conversation_id,
                    webspace_id=worker_webspace_id,
                    owner=owner,
                    kind="dialog",
                    title="General" if channel_id == GENERAL_DIALOG_CHANNEL_ID else channel_id,
                    active_agent_id=str(worker_agent.get("id") or "") or None,
                    meta={"route_id": route_id, "channel_id": channel_id},
                )
                _store_dialog_channel_projection(worker_webspace_id, worker_context_channel)
                stored = conversation_store.append_message(
                    conversation_id=conversation_id,
                    thread_id=topic_id or None,
                    webspace_id=worker_webspace_id,
                    channel_id=channel_id,
                    owner=owner,
                    role=str(local_msg.get("from") or "hub"),
                    text=str(local_msg.get("text") or ""),
                    payload=local_msg,
                    meta=local_meta,
                    actor_id=str(local_msg.get("active_agent_id") or "") or None,
                    actor_label=str(local_msg.get("active_agent_label") or "") or None,
                    actor_icon=str(local_msg.get("active_agent_icon") or local_msg.get("agent_icon") or "") or None,
                    route_id=route_id,
                    request_id=str(local_meta.get("request_id") or local_msg.get("request_id") or "") or None,
                    turn_trace_id=local_turn_trace_id or None,
                    idempotency_key=str(local_meta.get("idempotency_key") or "") or None,
                    ts=float(local_msg.get("ts") or time.time()),
                )
                projection = conversation_store.list_projection(
                    conversation_id,
                    thread_id=topic_id or None,
                    limit=VOICE_CHAT_VISIBLE_TAIL,
                    max_items=VOICE_CHAT_HISTORY_LIMIT,
                )
                stream_snapshot = [
                    dict(item)
                    for item in (projection.get("messages") if isinstance(projection, dict) else []) or []
                    if isinstance(item, dict)
                ]
                ledger_backed = bool(stream_snapshot)
                if not stream_snapshot and isinstance(stored, dict):
                    stream_snapshot = [dict(stored)]
                    ledger_backed = True
                if not stream_snapshot:
                    stream_snapshot = [dict(local_msg)]
                finished_turn_trace = False
                if local_turn_trace_id and str(local_msg.get("from") or "").strip() == "hub":
                    try:
                        conversation_store.finish_turn_trace(
                            local_turn_trace_id,
                            status="materialized" if local_visible_in_active_stream else "stored",
                            summary=(
                                f"Rendered to voice_chat.messages via {route_id}"
                                if local_visible_in_active_stream
                                else f"Stored inactive dialog message via {route_id}"
                            ),
                            renderer={
                                "receiver": "voice_chat.messages" if local_visible_in_active_stream else "conversation_store",
                                "projection": "compact_tail" if local_visible_in_active_stream else "inactive_dialog_ledger",
                                "message_id": local_msg.get("id"),
                            },
                        )
                        finished_turn_trace = True
                    except Exception:
                        finished_turn_trace = False
                return {
                    "clean_msg": local_msg,
                    "stream_snapshot": stream_snapshot,
                    "before_cursor": str(projection.get("before_cursor") or "") if isinstance(projection, dict) else "",
                    "has_more_before": bool(projection.get("has_more_before")) if isinstance(projection, dict) else False,
                    "total_message_count": int(projection.get("total_message_count") or len(stream_snapshot))
                    if isinstance(projection, dict)
                    else len(stream_snapshot),
                    "turn_trace_id": local_turn_trace_id,
                    "finished_turn_trace": finished_turn_trace,
                    "ledger_backed": ledger_backed,
                    "visible_in_active_stream": local_visible_in_active_stream,
                }

            async def _materialize_voice_chat_append() -> None:
                append_key = (worker_webspace_id, str(target_node_id or "").strip())
                append_lock = self._voice_chat_append_locks.setdefault(append_key, asyncio.Lock())
                async with append_lock:
                    await _materialize_voice_chat_append_locked()

            async def _materialize_voice_chat_append_locked() -> None:
                try:
                    materialized = await asyncio.to_thread(_append_voice_chat_message_sync)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").debug(
                        "voice_chat ledger append failed; using live projection fallback",
                        exc_info=True,
                    )
                    if not optimistic_published and visible_in_active_stream:
                        _fallback_publish_voice_chat_message(webspace_id, target_node_id, clean_msg)
                    return
                if not bool(materialized.get("visible_in_active_stream")):
                    if bool(materialized.get("finished_turn_trace")):
                        _schedule_dialog_state_write(webspace_id, event="inactive_turn_stored")
                    return
                if optimistic_published and not bool(materialized.get("ledger_backed")):
                    return
                materialized_msg = (
                    dict(materialized.get("clean_msg") or {})
                    if isinstance(materialized.get("clean_msg"), dict)
                    else clean_msg
                )
                stream_snapshot = [
                    dict(item)
                    for item in (materialized.get("stream_snapshot") if isinstance(materialized, dict) else []) or []
                    if isinstance(item, dict)
                ]
                if optimistic_published and stream_snapshot:
                    current_cache = _voice_chat_stream_cache.get((str(webspace_id or "").strip(), str(target_node_id or "").strip())) or {}
                    current_raw = current_cache.get("messages") if isinstance(current_cache, dict) else None
                    current_messages = [
                        dict(item)
                        for item in current_raw
                        if isinstance(item, dict)
                    ] if isinstance(current_raw, list) else []
                    if current_messages:
                        def _order_value(item: Mapping[str, Any]) -> float:
                            try:
                                seq = float(item.get("seq") or 0.0)
                            except Exception:
                                seq = 0.0
                            if seq > 0:
                                return seq
                            try:
                                return float(item.get("ts") or 0.0)
                            except Exception:
                                return 0.0

                        current_last = current_messages[-1]
                        next_last = stream_snapshot[-1]
                        current_order = _order_value(current_last)
                        next_order = _order_value(next_last)
                        if current_order > 0 and next_order > 0 and next_order < current_order:
                            return
                        current_last_id = str(current_last.get("id") or "").strip()
                        next_last_id = str(next_last.get("id") or "").strip()
                        if (
                            len(stream_snapshot) < len(current_messages)
                            and current_last_id
                            and current_last_id == next_last_id
                        ):
                            return
                last_refresh_ts = time.time()
                _publish_voice_chat_stream(
                    webspace_id,
                    target_node_id,
                    stream_snapshot,
                    last_refresh_ts,
                    before_cursor=str(materialized.get("before_cursor") or "") if isinstance(materialized, dict) else "",
                    has_more_before=bool(materialized.get("has_more_before")) if isinstance(materialized, dict) else False,
                    total_message_count=int(materialized.get("total_message_count") or len(stream_snapshot))
                    if isinstance(materialized, dict)
                    else len(stream_snapshot),
                    suppress_unchanged=True,
                )
                _schedule_voice_chat_persist(
                    webspace_id,
                    target_node_id,
                    stream_snapshot,
                    last_refresh_ts,
                    before_cursor=str(materialized.get("before_cursor") or "") if isinstance(materialized, dict) else "",
                    has_more_before=bool(materialized.get("has_more_before")) if isinstance(materialized, dict) else False,
                    total_message_count=int(materialized.get("total_message_count") or len(stream_snapshot))
                    if isinstance(materialized, dict)
                    else len(stream_snapshot),
                )
                if bool(materialized.get("finished_turn_trace")):
                    _schedule_dialog_state_write(webspace_id, event="turn_materialized")

                count = len(stream_snapshot)
                try:
                    self._vlog.debug(
                        "voice_chat.append webspace=%s node_id=%s count=%d last_from=%s last_text=%r",
                        webspace_id,
                        str(target_node_id or "").strip() or None,
                        count,
                        materialized_msg.get("from"),
                        materialized_msg.get("text"),
                    )
                except Exception:
                    pass

            try:
                task = asyncio.create_task(
                    _materialize_voice_chat_append(),
                    name=f"voice-chat-append:{str(webspace_id or 'default')}:{str(target_node_id or 'shared')}",
                )
            except RuntimeError:
                await _materialize_voice_chat_append()
                return
            self._voice_chat_append_tasks.add(task)

            def _forget_append_task(done: asyncio.Task[None]) -> None:
                self._voice_chat_append_tasks.discard(done)
                try:
                    done.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self._vlog.warning("voice_chat.append materialization task failed", exc_info=True)

            task.add_done_callback(_forget_append_task)

        def _voice_intent_demo_enabled() -> bool:
            return str(os.getenv("ADAOS_VOICE_CHAT_INTENT_DEMO") or "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        def _voice_intent_locale(meta: dict[str, Any]) -> str:
            for key in ("locale", "request_locale", "language", "lang"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    raw = value.strip()
                    return raw.split("-", 1)[0].split("_", 1)[0] or raw
            return "ru"

        def _format_voice_intent_demo(result: dict[str, Any]) -> str:
            accepted = bool(result.get("accepted"))
            intent = str(result.get("intent") or "not_obtained")
            via = str(result.get("via") or "neural")
            parts = [
                f"Intent detector: {intent}",
                f"via={via}",
                f"accepted={str(accepted).lower()}",
            ]
            confidence = result.get("confidence")
            if isinstance(confidence, (int, float)):
                parts.append(f"confidence={confidence:.3f}")
            slots = result.get("slots")
            if isinstance(slots, dict) and slots:
                parts.append("slots=" + json.dumps(slots, ensure_ascii=False, sort_keys=True, default=str))
            reason = result.get("reason")
            if isinstance(reason, str) and reason.strip() and not accepted:
                parts.append(f"reason={reason.strip()}")
            return " | ".join(parts)

        async def _append_voice_intent_demo(
            webspace_id: str,
            text: str,
            meta: dict[str, Any],
            target_node_id: str | None,
        ) -> None:
            if not _voice_intent_demo_enabled():
                return
            locale = _voice_intent_locale(meta)
            request_id = str(meta.get("message_id") or meta.get("id") or _make_id("intent"))
            demo_meta = {
                **meta,
                "webspace_id": webspace_id,
                "route_id": "voice_chat",
                "voice_chat_intent_demo": True,
            }
            try:
                from adaos.services.nlu import neural_service_bridge

                result = await neural_service_bridge.parse_text(
                    text,
                    webspace_id=webspace_id,
                    request_id=request_id,
                    meta=demo_meta,
                    locale=locale,
                    preferred_locales=[locale],
                    record_usage_stats=False,
                )
                msg_text = _format_voice_intent_demo(result if isinstance(result, dict) else {})
            except Exception as exc:
                msg_text = f"Intent detector unavailable: {type(exc).__name__}: {exc}"
            await _append_voice_chat_message(
                webspace_id,
                {
                    "id": _make_id("intent"),
                    "from": "hub",
                    "text": msg_text,
                    "ts": time.time(),
                },
                target_node_id,
            )

        async def _ensure_tts_state(webspace_id: str) -> None:
            def _mutator(data_map: Any, txn: Any) -> None:
                current = data_map.get("tts")
                if isinstance(current, dict) and isinstance(current.get("queue"), list):
                    return
                data_map.set(txn, "tts", {"queue": []})

            await _mutate_data_map(
                webspace_id,
                _mutator,
                channel="core.router.tts.store",
                prefer_live_room=True,
            )

        async def _append_tts_queue_item(webspace_id: str, item: dict) -> None:
            def _mutator(data_map: Any, txn: Any) -> None:
                current = data_map.get("tts")
                queue = []
                if isinstance(current, dict) and isinstance(current.get("queue"), list):
                    queue = list(current.get("queue") or [])
                queue.append(item)
                if len(queue) > 50:
                    queue = queue[-50:]
                data_map.set(txn, "tts", {"queue": queue})

            await _mutate_data_map(
                webspace_id,
                _mutator,
                channel="core.router.tts.store",
                prefer_live_room=True,
            )

        def _local_stream_node_id() -> str:
            try:
                return str(get_ctx().config.node_id or "").strip()
            except Exception:
                return ""

        def _webio_stream_topics(webspace_id: str, receiver: str, node_id: str) -> list[str]:
            ws = coerce_webspace_id(webspace_id, fallback="default")
            receiver_id = str(receiver or "").strip()
            if not receiver_id:
                return []
            source_node_id = str(node_id or "").strip()
            local_node_id = _local_stream_node_id()
            publish_unqualified = (
                not source_node_id
                or not local_node_id
                or source_node_id == local_node_id
            )
            topics: list[str] = []
            if publish_unqualified:
                topics.append(f"webio.stream.{ws}.{receiver_id}")
            if source_node_id:
                topics.append(f"webio.stream.{ws}.nodes.{source_node_id}.{receiver_id}")
                topics.append(f"webio.stream.nodes.{source_node_id}.{receiver_id}")
            return topics

        def _publish_webio_stream_event(
            webspace_id: str,
            receiver: str,
            payload: dict[str, Any],
            *,
            source: str,
            ts: float,
        ) -> None:
            receiver_id = str(receiver or "").strip()
            if not receiver_id:
                return
            node_id = str(
                payload.get("node_id")
                or payload.get("source_node_id")
                or (
                    payload.get("_meta", {}).get("node_id")
                    if isinstance(payload.get("_meta"), dict)
                    else ""
                )
                or ""
            ).strip()
            for topic in _webio_stream_topics(webspace_id, receiver_id, node_id):
                self.bus.publish(
                    Event(
                        type=topic,
                        source=source,
                        ts=ts,
                        payload=payload,
                    )
                )

        def _coerce_bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return False

        def _coerce_int(value: Any) -> int:
            try:
                return int(value or 0)
            except Exception:
                return 0

        def _coerce_float(value: Any) -> float | None:
            try:
                if value is None or value == "":
                    return None
                return float(value)
            except Exception:
                return None

        async def _ensure_media_state(webspace_id: str) -> None:
            def _mutator(data_map: Any, txn: Any) -> None:
                current = _coerce_y(data_map.get("media"))
                if isinstance(current, dict) and isinstance(current.get("route"), dict):
                    return
                next_state = dict(current) if isinstance(current, dict) else {}
                next_state.setdefault("route", {})
                data_map.set(txn, "media", next_state)

            await _mutate_data_map(webspace_id, _mutator, channel="core.router.media.live_room")

        async def _set_media_route_state(webspace_id: str, route_state: dict[str, Any]) -> None:
            def _mutator(data_map: Any, txn: Any) -> None:
                current = _coerce_y(data_map.get("media"))
                next_state = dict(current) if isinstance(current, dict) else {}
                next_state["route"] = route_state
                data_map.set(txn, "media", next_state)

            await _mutate_data_map(webspace_id, _mutator, channel="core.router.media.live_room")

        async def _get_media_route_state(webspace_id: str) -> dict[str, Any] | None:
            async with async_read_ydoc(webspace_id) as ydoc:
                data_map = ydoc.get_map("data")
                current = _coerce_y(data_map.get("media"))
                if not isinstance(current, dict):
                    return None
                route = current.get("route")
                return dict(route) if isinstance(route, dict) else None

        def _remember_media_webspaces(webspace_ids: list[str] | None) -> None:
            for item in list(webspace_ids or []):
                token = str(item or "").strip()
                if token:
                    self._media_route_webspaces.add(token)

        def _active_browser_session_totals() -> tuple[int, int]:
            try:
                from adaos.services.yjs.gateway_ws import active_browser_session_snapshot

                snapshot = active_browser_session_snapshot()
            except Exception:
                return (0, 0)
            peers = snapshot.get("peers") if isinstance(snapshot.get("peers"), list) else []
            total = 0
            connected = 0
            for item in peers:
                if not isinstance(item, dict):
                    continue
                total += 1
                if str(item.get("connection_state") or "").strip().lower() == "connected":
                    connected += 1
            return (total, connected)

        def _route_ability_available(route_state: dict[str, Any], topology_id: str) -> bool:
            capabilities = route_state.get("capabilities") if isinstance(route_state.get("capabilities"), dict) else {}
            abilities = capabilities.get("ability") if isinstance(capabilities.get("ability"), dict) else {}
            entry = abilities.get(topology_id) if isinstance(abilities.get(topology_id), dict) else {}
            return _coerce_bool(entry.get("available"))

        def _route_target_member_id(route_state: dict[str, Any]) -> str:
            preferred_member_id = str(route_state.get("preferred_member_id") or "").strip()
            if preferred_member_id:
                return preferred_member_id
            producer_target = route_state.get("producer_target") if isinstance(route_state.get("producer_target"), dict) else {}
            return str(producer_target.get("member_id") or "").strip()

        def _route_signature(route_state: dict[str, Any] | None) -> tuple[str, str, str, str, str]:
            state = route_state if isinstance(route_state, dict) else {}
            producer_target = state.get("producer_target") if isinstance(state.get("producer_target"), dict) else {}
            return (
                str(state.get("active_route") or "").strip(),
                str(state.get("delivery_topology") or "").strip(),
                _route_target_member_id(state),
                str(producer_target.get("kind") or "").strip(),
                str(producer_target.get("webspace_id") or "").strip(),
            )

        def _build_media_route_attempt(
            previous_route_state: dict[str, Any] | None,
            normalized_route_state: dict[str, Any],
            *,
            cause: str,
            ts: float,
            observed_failure: str | None = None,
        ) -> dict[str, Any]:
            previous = previous_route_state if isinstance(previous_route_state, dict) else {}
            previous_attempt = _coerce_y(previous.get("attempt"))
            previous_attempt = dict(previous_attempt) if isinstance(previous_attempt, dict) else {}
            previous_signature = _route_signature(previous)
            next_signature = _route_signature(normalized_route_state)
            has_previous_selection = any(previous_signature)
            route_changed = next_signature != previous_signature
            sequence = _coerce_int(previous_attempt.get("sequence"))
            if sequence <= 0:
                sequence = 1
            elif route_changed and has_previous_selection:
                sequence += 1
            switch_total = _coerce_int(previous_attempt.get("switch_total"))
            if route_changed and has_previous_selection:
                switch_total += 1
            selected_at = _coerce_float(previous_attempt.get("selected_at"))
            if selected_at is None or (route_changed and has_previous_selection):
                selected_at = ts
            last_switch_at = _coerce_float(previous_attempt.get("last_switch_at"))
            if route_changed and has_previous_selection:
                last_switch_at = ts
            previous_route = str(previous.get("active_route") or "").strip()
            previous_delivery_topology = str(previous.get("delivery_topology") or "").strip()
            previous_member_id = _route_target_member_id(previous)
            producer_target = (
                normalized_route_state.get("producer_target")
                if isinstance(normalized_route_state.get("producer_target"), dict)
                else {}
            )
            current_failure = str(observed_failure or "").strip() or None
            if current_failure is None:
                current_failure = str(previous_attempt.get("observed_failure") or "").strip() or None

            attempt = {
                "sequence": sequence,
                "state": "selected" if str(normalized_route_state.get("active_route") or "").strip() else "unavailable",
                "active_route": normalized_route_state.get("active_route"),
                "delivery_topology": normalized_route_state.get("delivery_topology"),
                "preferred_route": normalized_route_state.get("preferred_route"),
                "preferred_member_id": normalized_route_state.get("preferred_member_id"),
                "producer_target": dict(producer_target) if producer_target else None,
                "selection_reason": normalized_route_state.get("selection_reason"),
                "degradation_reason": normalized_route_state.get("degradation_reason"),
                "refresh_cause": cause,
                "observed_failure": current_failure,
                "switch_total": switch_total,
                "selected_at": selected_at,
                "last_switch_at": last_switch_at,
            }
            if route_changed and has_previous_selection:
                if previous_route:
                    attempt["previous_route"] = previous_route
                if previous_delivery_topology:
                    attempt["previous_delivery_topology"] = previous_delivery_topology
                if previous_member_id:
                    attempt["previous_member_id"] = previous_member_id
            else:
                prior_route = str(previous_attempt.get("previous_route") or "").strip()
                prior_topology = str(previous_attempt.get("previous_delivery_topology") or "").strip()
                prior_member = str(previous_attempt.get("previous_member_id") or "").strip()
                if prior_route:
                    attempt["previous_route"] = prior_route
                if prior_topology:
                    attempt["previous_delivery_topology"] = prior_topology
                if prior_member:
                    attempt["previous_member_id"] = prior_member
            return attempt

        def _refresh_media_route_payload(route_state: dict[str, Any], *, cause: str, observed_failure: str | None = None) -> dict[str, Any]:
            member_browser = (
                route_state.get("member_browser_direct")
                if isinstance(route_state.get("member_browser_direct"), dict)
                else {}
            )
            browser_session_total, connected_browser_session_total = _active_browser_session_totals()
            payload: dict[str, Any] = {
                "need": str(route_state.get("route_intent") or "scenario_response_media"),
                "producer_preference": str(route_state.get("producer_preference") or ""),
                "direct_local_ready": _route_ability_available(route_state, "local_http"),
                "root_routed_ready": _route_ability_available(route_state, "root_media_relay"),
                "hub_webrtc_ready": _route_ability_available(route_state, "hub_webrtc_loopback"),
                "browser_session_total": browser_session_total,
                "connected_browser_session_total": connected_browser_session_total,
                "refresh_cause": cause,
            }
            if member_browser:
                payload["member_browser_direct"] = {}
                if "admitted" in member_browser:
                    payload["member_browser_direct"]["admitted"] = _coerce_bool(member_browser.get("admitted"))
            monitoring = route_state.get("monitoring") if isinstance(route_state.get("monitoring"), dict) else {}
            existing_failure = str(monitoring.get("observed_failure") or "").strip()
            if observed_failure:
                payload["observed_failure"] = observed_failure
            elif existing_failure:
                payload["observed_failure"] = existing_failure
            return payload

        async def _refresh_media_route_for_webspace(
            webspace_id: str,
            *,
            cause: str,
            observed_failure: str | None = None,
        ) -> bool:
            route_state = await _get_media_route_state(webspace_id)
            if not isinstance(route_state, dict):
                return False
            if str(route_state.get("route_administrator") or "router").strip().lower() not in {"", "router"}:
                return False
            payload = _refresh_media_route_payload(
                route_state,
                cause=cause,
                observed_failure=observed_failure,
            )
            payload["ts"] = time.time()
            next_route_state = _resolve_media_route_state(
                payload,
                webspace_id=webspace_id,
                previous_route_state=route_state,
            )
            if not isinstance(next_route_state, dict):
                return False
            monitoring = next_route_state.get("monitoring") if isinstance(next_route_state.get("monitoring"), dict) else {}
            if monitoring:
                monitoring = dict(monitoring)
                monitoring["refresh_cause"] = cause
                next_route_state["monitoring"] = monitoring
            await _set_media_route_state(webspace_id, next_route_state)
            return True

        async def _refresh_media_routes(
            *,
            webspace_ids: list[str] | None = None,
            cause: str,
            observed_failure: str | None = None,
        ) -> None:
            targets = [
                str(item or "").strip()
                for item in list(webspace_ids or self._media_route_webspaces)
                if str(item or "").strip()
            ]
            if not targets:
                return
            _remember_media_webspaces(targets)
            for ws in targets:
                try:
                    await _refresh_media_route_for_webspace(
                        ws,
                        cause=cause,
                        observed_failure=observed_failure,
                    )
                except Exception:
                    continue

        def _resolve_media_route_state(
            payload: dict[str, Any],
            *,
            webspace_id: str,
            previous_route_state: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            raw_route = payload.get("route")
            if not isinstance(raw_route, dict) and isinstance(payload.get("route_intent"), dict):
                raw_route = payload.get("route_intent")

            route_state = _coerce_y(raw_route) if isinstance(raw_route, dict) else None
            member_browser = payload.get("member_browser_direct")
            member_browser = member_browser if isinstance(member_browser, dict) else {}
            current_browser_session_total, current_connected_browser_session_total = _active_browser_session_totals()
            route_producer_target = (
                route_state.get("producer_target")
                if isinstance(route_state, dict) and isinstance(route_state.get("producer_target"), dict)
                else {}
            )
            preferred_member_id = str(payload.get("preferred_member_id") or "").strip()
            if not preferred_member_id and isinstance(route_state, dict):
                preferred_member_id = str(route_state.get("preferred_member_id") or "").strip()
            if not preferred_member_id:
                preferred_member_id = str(route_producer_target.get("member_id") or "").strip()
            raw_candidate_members = (
                member_browser.get("candidate_members")
                if isinstance(member_browser.get("candidate_members"), list)
                else payload.get("candidate_member_ids")
            )
            candidate_member_ids = (
                [
                    str(item or "").strip()
                    for item in raw_candidate_members
                    if str(item or "").strip()
                ]
                if isinstance(raw_candidate_members, list)
                else []
            )
            admitted_member_browser = (
                _coerce_bool(member_browser.get("admitted"))
                if member_browser and "admitted" in member_browser
                else _coerce_bool(payload.get("member_browser_direct_admitted"))
            )
            auto_member_browser: dict[str, Any] = {}
            if not preferred_member_id or not candidate_member_ids:
                try:
                    from adaos.services.media_capability import member_browser_direct_foundation

                    auto_member_browser = member_browser_direct_foundation(
                        browser_session_total=(
                            _coerce_int(member_browser.get("browser_session_total"))
                            if member_browser and "browser_session_total" in member_browser
                            else (
                                _coerce_int(payload.get("browser_session_total"))
                                if "browser_session_total" in payload
                                else current_browser_session_total
                            )
                        ),
                        connected_browser_session_total=(
                            _coerce_int(member_browser.get("connected_browser_session_total"))
                            if member_browser and "connected_browser_session_total" in member_browser
                            else (
                                _coerce_int(payload.get("connected_browser_session_total"))
                                if "connected_browser_session_total" in payload
                                else current_connected_browser_session_total
                            )
                        ),
                        admitted=admitted_member_browser,
                    )
                except Exception:
                    auto_member_browser = {}
            if not preferred_member_id:
                preferred_member_id = str(auto_member_browser.get("preferred_member_id") or "").strip()
            if not candidate_member_ids:
                candidate_member_ids = [
                    str(item or "").strip()
                    for item in list(auto_member_browser.get("candidate_members") or [])
                    if str(item or "").strip()
                ]

            if route_state is None:
                route_state = resolve_media_route_intent(
                    need=str(payload.get("need") or payload.get("route_intent") or "scenario_response_media"),
                    target_webspace_id=webspace_id,
                    producer_preference=str(payload.get("producer_preference") or ""),
                    preferred_member_id=preferred_member_id or None,
                    candidate_member_ids=candidate_member_ids,
                    direct_local_ready=_coerce_bool(payload.get("direct_local_ready")),
                    root_routed_ready=_coerce_bool(payload.get("root_routed_ready")),
                    hub_webrtc_ready=_coerce_bool(payload.get("hub_webrtc_ready")),
                    member_browser_direct_possible=(
                        _coerce_bool(member_browser.get("possible"))
                        if member_browser and "possible" in member_browser
                        else (
                            _coerce_bool(payload.get("member_browser_direct_possible"))
                            if "member_browser_direct_possible" in payload
                            else _coerce_bool(auto_member_browser.get("possible"))
                        )
                    ),
                    member_browser_direct_admitted=(
                        _coerce_bool(member_browser.get("admitted"))
                        if member_browser and "admitted" in member_browser
                        else (
                            _coerce_bool(payload.get("member_browser_direct_admitted"))
                            if "member_browser_direct_admitted" in payload
                            else _coerce_bool(auto_member_browser.get("admitted"))
                        )
                    ),
                    member_browser_direct_reason=(
                        str(member_browser.get("reason") or "").strip()
                        or str(payload.get("member_browser_direct_reason") or "").strip()
                        or str(auto_member_browser.get("reason") or "").strip()
                        or None
                    ),
                    candidate_member_total=(
                        _coerce_int(member_browser.get("candidate_member_total"))
                        if member_browser and "candidate_member_total" in member_browser
                        else (
                            _coerce_int(payload.get("candidate_member_total"))
                            if "candidate_member_total" in payload
                            else _coerce_int(auto_member_browser.get("candidate_member_total"))
                        )
                    ),
                    browser_session_total=(
                        _coerce_int(member_browser.get("browser_session_total"))
                        if member_browser and "browser_session_total" in member_browser
                        else (
                            _coerce_int(payload.get("browser_session_total"))
                            if "browser_session_total" in payload
                            else _coerce_int(auto_member_browser.get("browser_session_total"))
                        )
                    ),
                    observed_failure=str(payload.get("observed_failure") or "").strip() or None,
                )

            if not isinstance(route_state, dict):
                return None

            monitoring = _coerce_y(route_state.get("monitoring"))
            monitoring = dict(monitoring) if isinstance(monitoring, dict) else {}
            observed_failure = str(payload.get("observed_failure") or "").strip()
            if observed_failure and not monitoring.get("observed_failure"):
                monitoring["observed_failure"] = observed_failure

            normalized = dict(route_state)
            normalized_member_browser = _coerce_y(normalized.get("member_browser_direct"))
            normalized_member_browser = dict(normalized_member_browser) if isinstance(normalized_member_browser, dict) else {}
            if preferred_member_id and not normalized.get("preferred_member_id"):
                normalized["preferred_member_id"] = preferred_member_id
            if candidate_member_ids and not isinstance(normalized_member_browser.get("candidate_members"), list):
                normalized_member_browser["candidate_members"] = list(candidate_member_ids)
            if preferred_member_id and not normalized_member_browser.get("preferred_member_id"):
                normalized_member_browser["preferred_member_id"] = preferred_member_id
            if candidate_member_ids and not normalized_member_browser.get("candidate_member_total"):
                normalized_member_browser["candidate_member_total"] = len(candidate_member_ids)
            if normalized_member_browser:
                normalized["member_browser_direct"] = normalized_member_browser
            refresh_cause = str(payload.get("refresh_cause") or "io.out.media.route").strip() or "io.out.media.route"
            updated_at = float(payload.get("ts") or time.time())
            effective_observed_failure = str(monitoring.get("observed_failure") or "").strip() or None
            attempt = _build_media_route_attempt(
                previous_route_state,
                normalized,
                cause=refresh_cause,
                ts=updated_at,
                observed_failure=effective_observed_failure,
            )
            normalized["attempt"] = attempt
            normalized["target_webspace_id"] = webspace_id
            normalized["route_administrator"] = "router"
            normalized["updated_at"] = updated_at
            monitoring["refresh_cause"] = refresh_cause
            monitoring["attempt_sequence"] = attempt.get("sequence")
            monitoring["switch_total"] = attempt.get("switch_total")
            monitoring["last_switch_at"] = attempt.get("last_switch_at")
            if monitoring:
                normalized["monitoring"] = monitoring
            return normalized

        def _now_ms() -> int:
            return int(time.time() * 1000)

        def _make_id(prefix: str) -> str:
            return f"{prefix}.{_now_ms()}"

        async def _on_voice_open(ev: Event) -> None:
            payload = ev.payload or {}
            if self._event_originates_from_remote_member(payload):
                return
            if not self._event_targets_local_node(payload):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            target_node_id = _resolve_voice_target_node_id(payload, meta)
            for ws in await _resolve_webspace_ids(payload):
                await _ensure_voice_chat_state(ws, target_node_id)
                await _ensure_tts_state(ws)
                await _write_dialog_state(ws, event="voice_open")
                await _publish_voice_chat_snapshot(
                    ws,
                    target_node_id,
                    conversation_id=payload.get("conversation_id") or meta.get("conversation_id"),
                    dialog_channel_id=payload.get("dialog_channel_id") or meta.get("dialog_channel_id"),
                    thread_id=_voice_chat_topic_id_from_sources(payload, meta),
                    persist=True,
                )

        async def _on_io_out_chat_append(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            if self._event_originates_from_remote_member(payload):
                try:
                    self._vlog.debug(
                        "skip mirrored remote member chat append origin=%s target=%s",
                        meta.get("subnet_origin_node_id"),
                        meta.get("target_node_id") or payload.get("target_node_id"),
                    )
                except Exception:
                    pass
                return
            if isinstance(meta, dict) and meta.get("skip_voice_chat") is True:
                return
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return

            # Optional request/response Telegram delivery via Root HTTP (/io/tg/send).
            #
            # Disabled by default because `tg.output.*` is already bridged to Root via NATS (see bootstrap),
            # and enabling both produces duplicate Telegram messages.
            try:
                if self._tg_reply_via_root_http and str((meta or {}).get("io_type") or "").lower() == "telegram":
                    chat_id = (meta or {}).get("chat_id")
                    if isinstance(chat_id, str) and chat_id.strip():
                        bot_id = (meta or {}).get("bot_id")
                        if not isinstance(bot_id, str) or not bot_id.strip():
                            bot_id = "main-bot"
                        hub_id = (meta or {}).get("hub_id")
                        if not isinstance(hub_id, str) or not hub_id.strip():
                            hub_id = get_ctx().config.subnet_id
                        ctx = get_ctx()
                        api_base = getattr(ctx.settings, "api_base", "https://api.inimatic.com")
                        url = f"{api_base.rstrip('/')}/io/tg/send"
                        body = {"hub_id": hub_id, "bot_id": bot_id, "chat_id": chat_id.strip(), "text": text.strip()}
                        if (meta or {}).get("reply_to"):
                            body["reply_to"] = (meta or {}).get("reply_to")
                        try:
                            r = await asyncio.to_thread(
                                requests.post,
                                url,
                                json=body,
                                headers={"Content-Type": "application/json"},
                                timeout=3.0,
                            )
                            if not (200 <= int(r.status_code) < 300):
                                logging.getLogger("adaos.router").warning(
                                    "router: telegram send failed (chat reply)",
                                    extra={
                                        "hub_id": hub_id,
                                        "chat_id": chat_id.strip(),
                                        "status": r.status_code,
                                        "body": (r.text or "")[:300],
                                    },
                                )
                            else:
                                logging.getLogger("adaos.router").info(
                                    "router: telegram sent (chat reply)",
                                    extra={"hub_id": hub_id, "chat_id": chat_id.strip(), "status": r.status_code},
                                )
                        except Exception as pe:
                            logging.getLogger("adaos.router").warning(
                                "router: telegram request failed (chat reply)",
                                extra={"hub_id": hub_id, "chat_id": chat_id.strip(), "error": str(pe)},
                            )
                        return
            except Exception:
                pass

            msg = {
                "id": str(payload.get("id") or _make_id("m")),
                "from": str(payload.get("from") or "hub"),
                "text": text.strip(),
                "ts": float(payload.get("ts") or time.time()),
                "_meta": dict(meta),
            }
            for key in ("dialog_channel_id", "conversation_id", "turn_trace_id", "request_id"):
                raw_value = payload.get(key) if payload.get(key) is not None else meta.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    msg[key] = raw_value.strip()
            topic_id = _voice_chat_topic_id_from_sources(payload, meta)
            if topic_id:
                msg["thread_id"] = topic_id
                msg["conversation_topic_id"] = topic_id
                msg["topic_id"] = topic_id
            for key in (
                "voice",
                "voice_gender",
                "active_agent_id",
                "active_agent_label",
                "active_agent_gender",
                "active_agent_voice",
                "active_agent_icon",
                "active_agent_avatar_ref",
                "agent_icon",
                "agent_avatar_ref",
                "recipient_label",
                "origin_label",
                "action_source",
            ):
                raw_value = payload.get(key) if payload.get(key) is not None else meta.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    msg[key] = raw_value.strip()
            voice_profile = payload.get("voice_profile") if isinstance(payload.get("voice_profile"), dict) else None
            if voice_profile is None and isinstance(meta.get("voice_profile"), dict):
                voice_profile = meta.get("voice_profile")
            if isinstance(voice_profile, dict):
                msg["voice_profile"] = dict(voice_profile)
            active_agent = payload.get("active_agent") if isinstance(payload.get("active_agent"), dict) else None
            if active_agent is None and isinstance(meta.get("active_agent"), dict):
                active_agent = meta.get("active_agent")
            if isinstance(active_agent, dict):
                if "active_agent_label" not in msg:
                    label = str(active_agent.get("label") or active_agent.get("name") or "").strip()
                    if label:
                        msg["active_agent_label"] = label
                if "active_agent_icon" not in msg:
                    icon = str(active_agent.get("icon") or "").strip()
                    if icon:
                        msg["active_agent_icon"] = icon
                if "active_agent_avatar_ref" not in msg:
                    avatar_ref = str(active_agent.get("avatar_ref") or active_agent.get("avatar") or "").strip()
                    if avatar_ref:
                        msg["active_agent_avatar_ref"] = avatar_ref
            actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
            if actions:
                msg["actions"] = [dict(item) for item in actions if isinstance(item, dict)]
            targets = await _resolve_webspace_ids(payload)
            route_id = str(meta.get("route_id") or meta.get("route") or "").strip()
            target_node_id = _resolve_voice_target_node_id(
                payload,
                meta,
                default_local=route_id == "voice_chat",
            )
            try:
                self._vlog.debug(
                    "io.out.chat.append received text=%r from=%s targets=%s node_id=%s",
                    msg["text"],
                    msg["from"],
                    targets,
                    target_node_id,
                )
            except Exception:
                pass
            for ws in targets:
                await _append_voice_chat_message(ws, msg, target_node_id)

        async def _on_io_out_say(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            if self._event_originates_from_remote_member(payload):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return
            item = {
                "id": str(payload.get("id") or _make_id("t")),
                "text": text.strip(),
                "ts": float(payload.get("ts") or time.time()),
            }
            voice_profile = payload.get("voice_profile") if isinstance(payload.get("voice_profile"), dict) else None
            if voice_profile is None and isinstance(meta.get("voice_profile"), dict):
                voice_profile = meta.get("voice_profile")
            lang = str(
                payload.get("lang")
                or meta.get("lang")
                or ((voice_profile or {}).get("lang") if isinstance(voice_profile, dict) else "")
                or ""
            ).strip()
            if lang:
                item["lang"] = lang
            voice = str(
                payload.get("voice")
                or meta.get("voice")
                or payload.get("active_agent_voice")
                or meta.get("active_agent_voice")
                or ((voice_profile or {}).get("voice") if isinstance(voice_profile, dict) else "")
                or ""
            ).strip()
            voice_hint = _browser_voice_hint(voice=voice)
            inferred_gender = voice_hint if voice_hint in {"female", "male"} else ""
            gender = str(
                payload.get("voice_gender")
                or meta.get("voice_gender")
                or payload.get("active_agent_gender")
                or meta.get("active_agent_gender")
                or ((voice_profile or {}).get("gender") if isinstance(voice_profile, dict) else "")
                or inferred_gender
                or ""
            ).strip()
            if voice:
                item["voice"] = voice
            if gender:
                item["voice_gender"] = gender
            for key in (
                "active_agent_id",
                "active_agent_label",
                "active_agent_gender",
                "active_agent_voice",
                "active_agent_icon",
                "active_agent_avatar_ref",
            ):
                value = payload.get(key) if payload.get(key) is not None else meta.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            if isinstance(voice_profile, dict):
                next_profile = dict(voice_profile)
                if gender and not next_profile.get("gender"):
                    next_profile["gender"] = gender
                if voice and not next_profile.get("voice"):
                    next_profile["voice"] = voice
                if not next_profile.get("browser_voice_hint"):
                    next_profile["browser_voice_hint"] = gender or voice_hint
                item["voice_profile"] = next_profile
            elif voice or gender:
                item["voice_profile"] = {
                    "gender": gender or None,
                    "voice": voice or None,
                    "lang": lang or "ru-RU",
                    "browser_voice_hint": gender or voice_hint,
                }
            if isinstance(payload.get("rate"), (int, float)):
                item["rate"] = float(payload.get("rate"))
            for ws in await _resolve_webspace_ids(payload):
                await _ensure_tts_state(ws)
                await _append_tts_queue_item(ws, item)

        async def _on_io_out_media_route(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            route_payload = dict(payload)
            route_payload["ts"] = float(route_payload.get("ts") or ev.ts or time.time())
            targets = await _resolve_webspace_ids(route_payload)
            _remember_media_webspaces(targets)
            for ws in targets:
                previous_route_state = await _get_media_route_state(ws)
                route_state = _resolve_media_route_state(
                    route_payload,
                    webspace_id=ws,
                    previous_route_state=previous_route_state,
                )
                if not isinstance(route_state, dict):
                    continue
                await _ensure_media_state(ws)
                await _set_media_route_state(ws, route_state)

        async def _on_io_out_stream_publish(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            receiver = str(payload.get("receiver") or "").strip()
            if not receiver:
                return
            event_ts = float(payload.get("ts") or ev.ts or time.time())
            data = payload.get("data")
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            owner = _webio_stream_owner(payload, meta)
            payload_bytes = _webio_stream_payload_bytes(
                {
                    "receiver": receiver,
                    "data": data,
                    "_meta": meta,
                }
            )
            node_id = str(
                payload.get("node_id")
                or payload.get("source_node_id")
                or meta.get("node_id")
                or meta.get("source_node_id")
                or ""
            ).strip()
            targets = await _resolve_webspace_ids(payload)
            for ws in targets:
                fanout_total = len(_webio_stream_topics(ws, receiver, node_id)) or 1
                receiver_meta = await self._webio_receiver_metadata(ws, receiver)
                effective_owner = owner or _receiver_declared_owner(receiver_meta)
                if not _webio_stream_admit(
                    webspace_id=ws,
                    receiver=receiver,
                    owner=effective_owner,
                    payload_bytes=payload_bytes,
                    fanout_total=fanout_total,
                    receiver_meta=receiver_meta,
                ):
                    continue
                event_payload = {
                    "receiver": receiver,
                    "webspace_id": ws,
                    "data": data,
                    "ts": event_ts,
                }
                if node_id:
                    event_payload["node_id"] = node_id
                    event_payload["source_node_id"] = node_id
                if meta:
                    event_payload["_meta"] = {
                        **meta,
                        "webspace_id": ws,
                        **({"node_id": node_id, "source_node_id": node_id} if node_id else {}),
                    }
                _publish_webio_stream_event(
                    ws,
                    receiver,
                    event_payload,
                    source=str(ev.source or "router"),
                    ts=event_ts,
                )
                _record_webio_stream_guard_event(
                    webspace_id=ws,
                    receiver=receiver,
                    owner=effective_owner,
                    event="published",
                    payload_bytes=payload_bytes,
                    fanout_total=fanout_total,
                    effective_bytes=payload_bytes * fanout_total,
                    policy_state="ok",
                    reason="published",
                    receiver_meta=receiver_meta,
                )

        async def _on_voice_chat_stream_snapshot(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            receiver = str(payload.get("receiver") or "").strip()
            try:
                self._vlog.debug(
                    "voice_chat.snapshot requested type=%s receiver=%s",
                    ev.type,
                    receiver,
                )
            except Exception:
                pass
            if receiver != "voice_chat.messages":
                return
            if ev.type == "webio.stream.subscription.changed":
                action = str(payload.get("action") or "").strip().lower()
                if action == "unsubscribed":
                    return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            stream_params: dict[str, Any] = {}
            if isinstance(meta.get("params"), dict):
                stream_params.update(dict(meta.get("params") or {}))
            if isinstance(payload.get("params"), dict):
                stream_params.update(dict(payload.get("params") or {}))
            target_node_id = _resolve_voice_target_node_id(payload, meta, default_local=False)
            conversation_id = (
                payload.get("conversation_id")
                or meta.get("conversation_id")
                or stream_params.get("conversation_id")
                or stream_params.get("conversationId")
            )
            dialog_channel_id = (
                payload.get("dialog_channel_id")
                or payload.get("channel_id")
                or meta.get("dialog_channel_id")
                or meta.get("channel_id")
                or stream_params.get("dialog_channel_id")
                or stream_params.get("dialogChannelId")
                or stream_params.get("channel_id")
                or stream_params.get("channelId")
            )
            thread_id = _voice_chat_topic_id_from_sources(payload, meta, stream_params)
            targets = await _resolve_webspace_ids(payload)
            for ws in targets:
                await _publish_voice_chat_snapshot(
                    ws,
                    target_node_id,
                    conversation_id=conversation_id,
                    dialog_channel_id=dialog_channel_id,
                    thread_id=thread_id,
                    persist=_voice_chat_persist_stream_snapshots_enabled(),
                    suppress_unchanged=ev.type == "webio.stream.snapshot.requested",
                )

        async def _on_conversation_history_more(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            if self._event_originates_from_remote_member(payload):
                return
            if not self._event_targets_local_node(payload):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            stream_params: dict[str, Any] = {}
            if isinstance(meta.get("params"), dict):
                stream_params.update(dict(meta.get("params") or {}))
            if isinstance(payload.get("params"), dict):
                stream_params.update(dict(payload.get("params") or {}))
            target_node_id = _resolve_voice_target_node_id(payload, meta, default_local=False)
            before_cursor = payload.get("before_cursor") or payload.get("older_cursor")
            conversation_id = (
                payload.get("conversation_id")
                or meta.get("conversation_id")
                or stream_params.get("conversation_id")
                or stream_params.get("conversationId")
            )
            dialog_channel_id = (
                payload.get("dialog_channel_id")
                or payload.get("channel_id")
                or meta.get("dialog_channel_id")
                or meta.get("channel_id")
                or stream_params.get("dialog_channel_id")
                or stream_params.get("dialogChannelId")
                or stream_params.get("channel_id")
                or stream_params.get("channelId")
            )
            thread_id = _voice_chat_topic_id_from_sources(payload, meta, stream_params)
            targets = await _resolve_webspace_ids(payload)
            for ws in targets:
                await _publish_voice_chat_history_more(
                    ws,
                    target_node_id,
                    before_cursor,
                    conversation_id=conversation_id,
                    dialog_channel_id=dialog_channel_id,
                    thread_id=thread_id,
                )

        async def _on_browser_session_changed(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            targets = _resolve_webspace_ids_basic(payload)
            tracked_targets = [ws for ws in targets if ws in self._media_route_webspaces]
            if not tracked_targets:
                return
            observed_failure = None
            if str(payload.get("connection_state") or "").strip().lower() in {"failed", "closed", "disconnected"}:
                observed_failure = f"browser_session_{str(payload.get('connection_state') or '').strip().lower()}"
            await _refresh_media_routes(
                webspace_ids=tracked_targets,
                cause="browser.session.changed",
                observed_failure=observed_failure,
            )

        async def _on_member_media_inventory_changed(ev: Event) -> None:
            if not self._media_route_webspaces:
                return
            payload = ev.payload or {}
            observed_failure = None
            if isinstance(payload, dict) and ev.type == "subnet.member.link.down":
                node_id = str(payload.get("node_id") or "").strip()
                observed_failure = f"member_link_down:{node_id}" if node_id else "member_link_down"
            await _refresh_media_routes(
                cause=ev.type,
                observed_failure=observed_failure,
            )

        def _dialog_latency_warn_ms() -> float:
            try:
                value = float(os.getenv("ADAOS_ROUTER_DIALOG_LATENCY_WARN_MS") or "5000")
            except Exception:
                value = 5000.0
            return max(100.0, min(value, 120000.0))

        def _dialog_timing_level(duration_ms: float, *, failed: bool = False) -> int:
            if failed or duration_ms >= _dialog_latency_warn_ms():
                return logging.WARNING
            return logging.DEBUG

        def _call_runtime_skill_tool(skill: str, tool: str, payload: dict[str, Any], meta: dict) -> Any:
            log = logging.getLogger("adaos.router.voice_chat")
            route_meta = dict(meta)
            scheduled_raw = route_meta.pop("_router_tool_scheduled_at", None)
            worker_started = time.perf_counter()
            queue_ms: float | None = None
            try:
                scheduled_at = float(scheduled_raw)
                queue_ms = max(0.0, (worker_started - scheduled_at) * 1000.0)
            except Exception:
                queue_ms = None
            log.debug(
                "dialog runtime tool thread started skill=%s tool=%s webspace=%s queue_ms=%s",
                skill,
                tool,
                str(route_meta.get("webspace_id") or "").strip(),
                f"{queue_ms:.1f}" if queue_ms is not None else "-",
            )
            ctx = get_ctx()
            prev = ctx.skill_ctx.get()
            manager_started = time.perf_counter()
            mgr = SkillManager(
                repo=ctx.skills_repo,
                registry=SqliteSkillRegistry(ctx.sql),
                git=ctx.git,
                paths=ctx.paths,
                bus=getattr(ctx, "bus", None),
                caps=ctx.caps,
                settings=ctx.settings,
            )
            manager_ms = (time.perf_counter() - manager_started) * 1000.0
            try:
                tool_payload: dict[str, Any] = dict(payload)
                tool_payload["_meta"] = route_meta
                webspace_id = str(route_meta.get("webspace_id") or "").strip()
                if webspace_id:
                    tool_payload.setdefault("webspace_id", webspace_id)
                target_node_id = str(route_meta.get("target_node_id") or "").strip()
                if target_node_id:
                    tool_payload.setdefault("target_node_id", target_node_id)
                with io_meta(route_meta):
                    run_started = time.perf_counter()
                    try:
                        result = mgr.run_tool(skill, tool, tool_payload, bypass_yjs_guard=True)
                        run_ms = (time.perf_counter() - run_started) * 1000.0
                        total_ms = (time.perf_counter() - worker_started) * 1000.0
                        log.log(
                            _dialog_timing_level(total_ms),
                            "dialog runtime tool run completed skill=%s tool=%s webspace=%s fallback=workspace manager_ms=%.1f run_ms=%.1f total_ms=%.1f",
                            skill,
                            tool,
                            webspace_id,
                            manager_ms,
                            run_ms,
                            total_ms,
                        )
                        return result
                    except Exception as workspace_exc:
                        if skill != BUILDER_SKILL_ID or not hasattr(mgr, "run_dev_tool"):
                            log.warning(
                                "dialog runtime tool run failed skill=%s tool=%s webspace=%s fallback=none manager_ms=%.1f run_ms=%.1f total_ms=%.1f",
                                skill,
                                tool,
                                webspace_id,
                                manager_ms,
                                (time.perf_counter() - run_started) * 1000.0,
                                (time.perf_counter() - worker_started) * 1000.0,
                            )
                            raise
                        dev_root_attr = getattr(ctx.paths, "dev_skills_dir", None)
                        dev_root = dev_root_attr() if callable(dev_root_attr) else dev_root_attr
                        dev_skill_dir = (Path(dev_root) / skill) if dev_root else None
                        if dev_skill_dir is None or not dev_skill_dir.exists():
                            log.warning(
                                "dialog runtime tool run failed skill=%s tool=%s webspace=%s fallback=dev_missing manager_ms=%.1f run_ms=%.1f total_ms=%.1f",
                                skill,
                                tool,
                                webspace_id,
                                manager_ms,
                                (time.perf_counter() - run_started) * 1000.0,
                                (time.perf_counter() - worker_started) * 1000.0,
                            )
                            raise workspace_exc
                        logging.getLogger("adaos.router.voice_chat").info(
                            "workspace builder skill unavailable; trying dev runtime tool=%s",
                            tool,
                            exc_info=True,
                        )
                        dev_run_started = time.perf_counter()
                        result = mgr.run_dev_tool(skill, tool, tool_payload)
                        dev_run_ms = (time.perf_counter() - dev_run_started) * 1000.0
                        total_ms = (time.perf_counter() - worker_started) * 1000.0
                        log.log(
                            _dialog_timing_level(total_ms),
                            "dialog runtime tool run completed skill=%s tool=%s webspace=%s fallback=dev manager_ms=%.1f workspace_run_ms=%.1f dev_run_ms=%.1f total_ms=%.1f",
                            skill,
                            tool,
                            webspace_id,
                            manager_ms,
                            (dev_run_started - run_started) * 1000.0,
                            dev_run_ms,
                            total_ms,
                        )
                        return result
            finally:
                if prev is None:
                    try:
                        ctx.skill_ctx.clear()
                    except Exception:
                        pass
                else:
                    try:
                        ctx.skill_ctx.set(prev.name, prev.path)
                    except Exception:
                        pass

        def _call_dialog_surface_fallback_tool(text: str, meta: dict, policy: Mapping[str, Any]) -> Any:
            skill = str(policy.get("skill") or "voice_chat_skill").strip() or "voice_chat_skill"
            tool = str(policy.get("tool") or "handle_text").strip() or "handle_text"
            return _call_runtime_skill_tool(skill, tool, {"text": text}, meta)

        def _chat_append_matches_dialog_action(
            payload: Mapping[str, Any],
            *,
            text: str,
            meta: Mapping[str, Any],
            webspace_id: str,
            route_id: str,
        ) -> bool:
            emitted_text = payload.get("text")
            if not isinstance(emitted_text, str) or not emitted_text.strip():
                return False
            payload_meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
            emitted_webspace = str(payload.get("webspace_id") or payload_meta.get("webspace_id") or "").strip()
            if emitted_webspace and emitted_webspace != webspace_id:
                return False
            expected_route = str(route_id or meta.get("route_id") or meta.get("route") or "").strip()
            emitted_route = str(payload.get("route_id") or payload_meta.get("route_id") or payload_meta.get("route") or "").strip()
            if expected_route and emitted_route and emitted_route != expected_route:
                return False
            trace_id = str(meta.get("turn_trace_id") or "").strip()
            emitted_trace_id = str(payload.get("turn_trace_id") or payload_meta.get("turn_trace_id") or "").strip()
            if trace_id and emitted_trace_id == trace_id:
                return True
            if text and emitted_text.strip() == text.strip():
                return True
            return bool(expected_route and emitted_route == expected_route)

        def _subscribe_dialog_materialization_probe() -> tuple[list[dict[str, Any]], Any | None]:
            materialized: list[dict[str, Any]] = []

            def _capture(ev: Event) -> None:
                payload = ev.payload if isinstance(ev.payload, dict) else {}
                text = payload.get("text")
                if isinstance(text, str) and text.strip():
                    materialized.append(dict(payload))

            try:
                self.bus.subscribe("io.out.chat.append", _capture)
            except Exception:
                return materialized, None
            return materialized, _capture

        def _unsubscribe_dialog_materialization_probe(handler: Any | None) -> None:
            if handler is None:
                return
            try:
                unsubscribe = getattr(self.bus, "unsubscribe", None)
                if callable(unsubscribe):
                    unsubscribe("io.out.chat.append", handler)
            except Exception:
                logging.getLogger("adaos.router.voice_chat").debug(
                    "dialog materialization probe unsubscribe failed",
                    exc_info=True,
                )

        def _attach_dialog_context_payload(
            payload: dict[str, Any],
            *,
            webspace_id: str,
            channel_id: str,
            conversation_id: str,
            owner: str,
            agent_id: str | None,
            route_id: str,
        ) -> None:
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            payload["_meta"] = {
                **dict(meta),
                "webspace_id": webspace_id,
                "dialog_channel_id": channel_id,
                "conversation_id": conversation_id,
                "conversation_owner": owner,
                "route_id": route_id,
            }
            thread_id = _voice_chat_topic_id_from_sources(payload, meta)
            if thread_id:
                payload["_meta"].setdefault("thread_id", thread_id)
                payload["_meta"].setdefault("conversation_topic_id", thread_id)
                payload.setdefault("thread_id", thread_id)
                payload.setdefault("conversation_topic_id", thread_id)
            if agent_id:
                payload["_meta"].setdefault("active_agent_id", agent_id)
            payload.setdefault("conversation_id", conversation_id)
            payload.setdefault("dialog_channel_id", channel_id)
            payload.setdefault("conversation_owner", owner)
            try:
                payload["conversation_context"] = conversation_context.build_context_packet(
                    conversation_id=conversation_id,
                    requester_owner=owner,
                    channel_id=channel_id,
                    thread_id=thread_id or None,
                    agent_id=agent_id,
                    budgets={
                        "max_tokens": 4_000,
                        "max_messages": 20,
                        "max_memory_items": 12,
                        "timeout_ms": 250,
                    },
                )
            except Exception as exc:
                payload["conversation_context"] = {
                    "schema": "adaos.context.packet.v1",
                    "conversation_id": conversation_id,
                    "requester_owner": owner,
                    "channel_id": channel_id,
                    "agent_id": agent_id,
                    "messages": [],
                    "memory": [],
                    "diagnostics": {
                        "fallbacks": ["context_packet_unavailable"],
                        "error": type(exc).__name__,
                    },
                }

        async def _handle_dialog_action(
            *,
            dialog_action: dict[str, Any],
            webspace_id: str,
            meta: dict[str, Any],
            request_id: str | None = None,
            route_id: str = "voice_chat",
            mark_request: bool = False,
        ) -> bool:
            kind = str(dialog_action.get("kind") or "").strip()
            if kind == "exit":
                channel = dialog_action.get("channel") if isinstance(dialog_action.get("channel"), dict) else {}
                _record_voice_turn_trace(
                    webspace_id,
                    meta,
                    selected_tool="router.voice.dialog_exit",
                    reason="dialog_exit",
                    status="completed",
                    summary="Dialog channel exited",
                    extra_policy={
                        "dialog_action_kind": "exit",
                        "previous_channel": str(channel.get("channel_id") or "").strip(),
                    },
                )
                dialog_runtime.deactivate_channel(
                    webspace_id=webspace_id,
                    channel_id=str(channel.get("channel_id") or "").strip() or None,
                    bus=self.bus,
                    source="router.voice_chat",
                    reason="voice_exit",
                )
                _persist_general_dialog_channel(webspace_id, event="exit")
                exit_text = str(dialog_action.get("message") or "").strip()
                if exit_text:
                    try:
                        self.bus.publish(
                            Event(
                                type="io.out.chat.append",
                                source="router.voice_chat",
                                ts=time.time(),
                                payload={
                                    "id": _make_id("m"),
                                    "from": "hub",
                                    "text": exit_text,
                                    "ts": time.time(),
                                    "_meta": {**meta, "route_id": route_id},
                                },
                            )
                        )
                    except Exception:
                        pass
                if mark_request:
                    try:
                        from adaos.services.nlu.dispatcher import mark_dispatched_request

                        mark_dispatched_request(
                            request_id=request_id,
                            webspace_id=webspace_id,
                            route_id=route_id,
                        )
                    except Exception:
                        pass
                await _write_dialog_state(webspace_id, event="exit")
                return True

            if kind != "skill_tool":
                return False
            skill = str(dialog_action.get("skill") or "").strip()
            tool = str(dialog_action.get("tool") or "").strip()
            action_payload = dialog_action.get("payload") if isinstance(dialog_action.get("payload"), dict) else {}
            action_meta = action_payload.get("_meta") if isinstance(action_payload.get("_meta"), dict) else meta
            if not skill or not tool:
                return False
            action_channel = dialog_action.get("channel") if isinstance(dialog_action.get("channel"), dict) else {}
            conversation_id = str(
                action_meta.get("conversation_id")
                or action_payload.get("conversation_id")
                or action_channel.get("conversation_id")
                or ""
            ).strip()
            channel_id = str(
                action_meta.get("dialog_channel_id")
                or action_meta.get("channel_id")
                or action_payload.get("dialog_channel_id")
                or action_channel.get("channel_id")
                or CONVERSATIONAL_DIALOG_CHANNEL_ID
            ).strip() or CONVERSATIONAL_DIALOG_CHANNEL_ID
            owner = str(
                action_meta.get("conversation_owner")
                or action_payload.get("conversation_owner")
                or action_channel.get("owner")
                or f"skill:{skill}"
            ).strip() or f"skill:{skill}"
            agent_id = str(
                action_meta.get("active_agent_id")
                or action_payload.get("active_agent_id")
                or action_channel.get("active_agent_id")
                or ""
            ).strip() or None
            if not conversation_id:
                conversation_id = _skill_conversation_id(skill, webspace_id)
            _attach_dialog_context_payload(
                action_payload,
                webspace_id=webspace_id,
                channel_id=channel_id,
                conversation_id=conversation_id,
                owner=owner,
                agent_id=agent_id,
                route_id=route_id,
            )
            action_meta = action_payload.get("_meta") if isinstance(action_payload.get("_meta"), dict) else action_meta
            _record_voice_turn_trace(
                webspace_id,
                action_meta,
                text=str(action_payload.get("text") or ""),
                selected_tool=f"{skill}.{tool}",
                reason=str(action_meta.get("dialog_policy_reason") or meta.get("dialog_policy_reason") or "dialog_followup"),
                renderer={"receiver": "skill_runtime", "tool": f"{skill}.{tool}", "projection": "tool_result"},
                summary=f"Routed dialog turn to {skill}.{tool}",
                extra_policy={
                    "dialog_action_kind": "skill_tool",
                    "mark_request": bool(mark_request),
                    "request_id": str(request_id or "").strip(),
                },
            )
            target_node_id = str(action_meta.get("target_node_id") or meta.get("target_node_id") or "").strip() or None
            materialized_chat_appends, materialization_probe = _subscribe_dialog_materialization_probe()
            tool_started = time.perf_counter()
            tool_meta = dict(action_meta)
            tool_meta["_router_tool_scheduled_at"] = tool_started
            try:
                result = await asyncio.to_thread(
                    _call_runtime_skill_tool,
                    skill,
                    tool,
                    dict(action_payload),
                    tool_meta,
                )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "dialog follow-up tool failed timing skill=%s tool=%s webspace=%s took_ms=%.1f",
                    skill,
                    tool,
                    webspace_id,
                    (time.perf_counter() - tool_started) * 1000.0,
                )
                logging.getLogger("adaos.router.voice_chat").warning(
                    "dialog follow-up tool failed skill=%s tool=%s",
                    skill,
                    tool,
                    exc_info=True,
                )
                _record_voice_turn_trace(
                    webspace_id,
                    action_meta,
                    text=str(action_payload.get("text") or ""),
                    selected_tool=f"{skill}.{tool}",
                    reason=str(action_meta.get("dialog_policy_reason") or meta.get("dialog_policy_reason") or "dialog_followup"),
                    status="failed",
                    renderer={"receiver": "skill_runtime", "tool": f"{skill}.{tool}", "projection": "exception"},
                    summary=f"{skill}.{tool} raised during dialog turn",
                    extra_policy={"result_status": "exception"},
                )
                try:
                    await _append_dialog_tool_unavailable(webspace_id, channel_id, action_meta, target_node_id)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").debug(
                        "dialog unavailable fallback failed webspace=%s channel=%s skill=%s tool=%s",
                        webspace_id,
                        channel_id,
                        skill,
                        tool,
                        exc_info=True,
                    )
                return True
            finally:
                _unsubscribe_dialog_materialization_probe(materialization_probe)
            tool_ms = (time.perf_counter() - tool_started) * 1000.0
            logging.getLogger("adaos.router.voice_chat").log(
                _dialog_timing_level(tool_ms),
                "dialog follow-up tool completed skill=%s tool=%s webspace=%s took_ms=%.1f ok=%s status=%s",
                skill,
                tool,
                webspace_id,
                tool_ms,
                bool(isinstance(result, dict) and result.get("ok")),
                str(result.get("status") or "") if isinstance(result, dict) else type(result).__name__,
            )
            if not isinstance(result, dict) or not bool(result.get("ok")):
                logging.getLogger("adaos.router.voice_chat").warning(
                    "dialog follow-up tool returned non-ok skill=%s tool=%s result=%r",
                    skill,
                    tool,
                    result,
                )
                _record_voice_turn_trace(
                    webspace_id,
                    action_meta,
                    text=str(action_payload.get("text") or ""),
                    selected_tool=f"{skill}.{tool}",
                    reason=str(action_meta.get("dialog_policy_reason") or meta.get("dialog_policy_reason") or "dialog_followup"),
                    status="failed",
                    renderer={"receiver": "skill_runtime", "tool": f"{skill}.{tool}", "projection": "tool_result"},
                    summary=f"{skill}.{tool} returned non-ok dialog result",
                    extra_policy={"result_ok": False, "result_status": "non_ok"},
                )
                try:
                    await _append_dialog_tool_unavailable(webspace_id, channel_id, action_meta, target_node_id)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").debug(
                        "dialog non-ok fallback failed webspace=%s channel=%s skill=%s tool=%s",
                        webspace_id,
                        channel_id,
                        skill,
                        tool,
                        exc_info=True,
                    )
                return True
            try:
                dialog_runtime.apply_tool_result(
                    result,
                    webspace_id=webspace_id,
                    target=f"{skill}.{tool}",
                    raw_meta=meta,
                    payload_meta=action_meta,
                    bus=self.bus,
                    source="router.voice_chat",
                )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").debug(
                    "dialog follow-up result state update failed",
                    exc_info=True,
                )
            materialization = conversation_response.materialize_tool_result(
                result,
                webspace_id=webspace_id,
                conversation_id=conversation_id,
                channel_id=channel_id,
                owner=owner,
                bus=self.bus,
                route_id=route_id,
                actor_id=agent_id,
                actor_label=str(action_meta.get("active_agent_label") or "").strip() or None,
                actor_icon=str(action_meta.get("active_agent_icon") or action_meta.get("agent_icon") or "").strip() or None,
                actor_avatar_ref=str(action_meta.get("active_agent_avatar_ref") or action_meta.get("agent_avatar_ref") or "").strip()
                or None,
                request_id=str(request_id or action_meta.get("request_id") or "").strip() or None,
                turn_trace_id=str(action_meta.get("turn_trace_id") or "").strip() or None,
                thread_id=str(action_meta.get("thread_id") or action_payload.get("thread_id") or "").strip() or None,
                raw_meta=meta,
                payload_meta=action_meta,
                source="router.voice_chat",
                materialized_chat_appends=materialized_chat_appends,
            )
            result_message = str(result.get("message") or "").strip()
            suppress_visible_result_message = conversation_response.tool_result_suppresses_visible_message(result)
            materialized_payload = None
            if result_message:
                materialized_payload = next(
                    (
                        item
                        for item in materialized_chat_appends
                        if _chat_append_matches_dialog_action(
                            item,
                            text=result_message,
                            meta=action_meta,
                            webspace_id=webspace_id,
                            route_id=route_id,
                        )
                    ),
                    None,
                )
            if materialized_payload is None:
                published = materialization.get("published_chat") if isinstance(materialization, dict) else None
                if isinstance(published, list) and published:
                    materialized_payload = next((item for item in published if isinstance(item, dict)), None)
            trace_status = "tool_ok"
            trace_renderer: dict[str, Any] = {"receiver": "voice_chat.messages", "projection": "pending_materialization"}
            trace_summary = f"{skill}.{tool} returned ok; waiting for visible output"
            trace_policy: dict[str, Any] = {
                "result_ok": True,
                "result_status": "ok",
                "result_has_message": bool(result_message),
                "result_message_receipt_only": bool(suppress_visible_result_message),
                "response_envelope_materialized": bool(
                    isinstance(materialization, dict) and materialization.get("materialized")
                ),
            }
            if materialized_payload is not None:
                trace_status = "materialized"
                trace_renderer = {
                    "receiver": "voice_chat.messages",
                    "projection": "skill_emitted_message" if result_message else "response_envelope",
                    "message_id": materialized_payload.get("id"),
                }
                trace_summary = f"{skill}.{tool} returned ok and materialized visible output"
                trace_policy["materialization_status"] = "materialized"
            elif result_message and suppress_visible_result_message:
                trace_renderer = {
                    "receiver": "skill_runtime",
                    "projection": "receipt_only",
                }
                trace_summary = f"{skill}.{tool} returned receipt-only message; visible output suppressed"
                trace_policy["materialization_status"] = "suppressed"
                trace_policy["diagnostic"] = "skill_result_message_receipt_only"
            elif result_message:
                trace_status = "unmaterialized"
                trace_renderer = {
                    "receiver": "voice_chat.messages",
                    "projection": "missing_materialization",
                }
                trace_summary = f"{skill}.{tool} returned ok message but no visible output was observed"
                trace_policy["materialization_status"] = "missing"
                trace_policy["diagnostic"] = "skill_result_message_not_visible"
                logging.getLogger("adaos.router.voice_chat").warning(
                    "dialog follow-up returned message without visible output skill=%s tool=%s webspace=%s trace_id=%s",
                    skill,
                    tool,
                    webspace_id,
                    str(action_meta.get("turn_trace_id") or "").strip(),
                )
                fallback_payload = {
                    "id": _make_id("m"),
                    "from": "hub",
                    "text": result_message,
                    "ts": time.time(),
                    "active_agent_id": agent_id,
                    "active_agent_label": str(action_meta.get("active_agent_label") or "").strip() or None,
                    "active_agent_icon": str(action_meta.get("active_agent_icon") or action_meta.get("agent_icon") or "").strip() or None,
                    "active_agent_avatar_ref": str(
                        action_meta.get("active_agent_avatar_ref") or action_meta.get("agent_avatar_ref") or ""
                    ).strip()
                    or None,
                    "_meta": {**action_meta, "materialization_fallback": "surface_missing_visible_output"},
                }
                try:
                    await _append_voice_chat_message(webspace_id, fallback_payload, target_node_id)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").debug(
                        "dialog visible-output fallback failed skill=%s tool=%s webspace=%s",
                        skill,
                        tool,
                        webspace_id,
                        exc_info=True,
                    )
                else:
                    trace_status = "materialized"
                    trace_renderer = {
                        "receiver": "voice_chat.messages",
                        "projection": "surface_fallback",
                        "message_id": fallback_payload.get("id"),
                    }
                    trace_summary = f"{skill}.{tool} returned ok and router materialized fallback output"
                    trace_policy["materialization_status"] = "fallback_materialized"
            _record_voice_turn_trace(
                webspace_id,
                action_meta,
                text=str(action_payload.get("text") or ""),
                selected_tool=f"{skill}.{tool}",
                reason=str(action_meta.get("dialog_policy_reason") or meta.get("dialog_policy_reason") or "dialog_followup"),
                status=trace_status,
                renderer=trace_renderer,
                summary=trace_summary,
                extra_policy=trace_policy,
            )
            _schedule_dialog_state_write(webspace_id, event="turn")
            if mark_request:
                try:
                    from adaos.services.nlu.dispatcher import mark_dispatched_request

                    mark_dispatched_request(
                        request_id=request_id,
                        webspace_id=webspace_id,
                        route_id=route_id,
                    )
                except Exception:
                    pass
            return True

        async def _on_dialog_channel_event(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            webspace_id = str(payload.get("webspace_id") or "default").strip() or "default"
            event_name = str(ev.type or "").rsplit(".", 1)[-1] or "changed"
            if event_name == "deactivated":
                _persist_general_dialog_channel(webspace_id, event=event_name)
            await _write_dialog_state(webspace_id, event=event_name)

        async def _on_dialog_channel_select(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            if self._event_originates_from_remote_member(payload):
                return
            if not self._event_targets_local_node(payload):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            channel_id = str(payload.get("channel_id") or payload.get("id") or payload.get("value") or "").strip().lower()
            if channel_id in {"", "default"}:
                channel_id = "general"
            targets = await _resolve_webspace_ids(payload)
            for ws in targets:
                route_meta = {**meta, "webspace_id": ws, "route_id": str(meta.get("route_id") or "voice_chat")}
                current = dialog_runtime.get_active_channel(ws)
                current_id = str(current.channel_id).strip().lower() if current is not None else "general"
                if channel_id == "general":
                    _persist_general_dialog_channel(ws, event="manual_select_general")
                    if current is not None:
                        general_meta = _general_agent_metadata()
                        dialog_runtime.deactivate_channel(
                            webspace_id=ws,
                            channel_id=current.channel_id,
                            bus=self.bus,
                            source="router.dialog",
                            reason="manual_select_general",
                        )
                        await _append_voice_chat_message(
                            ws,
                            {
                                "id": _make_id("m"),
                                "from": "hub",
                                "text": _general_agent_transition_text("manual_select_general"),
                                "ts": time.time(),
                                **general_meta,
                            },
                            _resolve_voice_target_node_id(payload, route_meta, default_local=False),
                        )
                    await _write_dialog_state(ws, event="selected")
                    continue
                if current_id == channel_id:
                    await _write_dialog_state(ws, event="selected")
                    continue
                if channel_id != "conversational":
                    try:
                        _seed_manifest_dialog_channels(ws)
                        channel = conversation_store.get_dialog_channel(ws, channel_id)
                    except Exception:
                        channel = None
                    if not isinstance(channel, dict) and channel_id == "builder":
                        channel = {
                            "id": "builder",
                            "channel_id": "builder",
                            "label": "Builder",
                            "owner": f"skill:{BUILDER_SKILL_ID}",
                            "conversation_id": _skill_conversation_id(BUILDER_SKILL_ID, ws),
                            "default_skill": BUILDER_SKILL_ID,
                            "default_tool": "chat",
                            "route_id": "voice_chat",
                        }
                    if not isinstance(channel, dict):
                        logging.getLogger("adaos.router.dialog").warning(
                            "unsupported dialog channel selected: %r",
                            channel_id,
                        )
                        await _write_dialog_state(ws, event="select_failed")
                        continue
                    default_skill = str(channel.get("default_skill") or "").strip()
                    owner = str(channel.get("owner") or "").strip()
                    if not default_skill and owner.startswith("skill:"):
                        default_skill = owner.split(":", 1)[1]
                    default_skill = default_skill or channel_id
                    default_tool = str(channel.get("default_tool") or "chat").strip() or "chat"
                    conversation_id = str(channel.get("conversation_id") or _skill_conversation_id(default_skill, ws)).strip()
                    owner = owner or f"skill:{default_skill}"
                    try:
                        conversation_store.upsert_conversation(
                            conversation_id=conversation_id,
                            webspace_id=ws,
                            owner=owner,
                            kind="dialog",
                            title=str(channel.get("label") or _dialog_channel_label(channel_id)),
                            active_agent_id=str(channel.get("active_agent_id") or "").strip() or None,
                            meta={"route_id": channel.get("route_id") or "voice_chat", "channel_id": channel_id},
                        )
                        conversation_store.upsert_dialog_channel(
                            webspace_id=ws,
                            channel_id=channel_id,
                            label=str(channel.get("label") or _dialog_channel_label(channel_id)),
                            owner=owner,
                            conversation_id=conversation_id,
                            active_agent_id=str(channel.get("active_agent_id") or "").strip() or None,
                            default_skill=default_skill,
                            default_tool=default_tool,
                            route_id=str(channel.get("route_id") or "voice_chat"),
                            policy=channel.get("policy") if isinstance(channel.get("policy"), dict) else {},
                            meta=channel.get("meta") if isinstance(channel.get("meta"), dict) else {},
                        )
                    except Exception:
                        logging.getLogger("adaos.router.dialog").debug(
                            "failed to persist selected dialog channel webspace=%s channel=%s",
                            ws,
                            channel_id,
                            exc_info=True,
                        )
                    dialog_runtime.activate_channel(
                        webspace_id=ws,
                        channel_id=channel_id,
                        owner=owner,
                        default_skill=default_skill,
                        default_tool=default_tool,
                        conversation_id=conversation_id,
                        active_agent_id=str(channel.get("active_agent_id") or "").strip() or None,
                        active_agent_label=str(channel.get("active_agent_label") or "").strip() or None,
                        active_agent_owner=str(channel.get("active_agent_owner") or channel.get("owner") or "").strip() or None,
                        active_agent_kind=str(channel.get("active_agent_kind") or "").strip() or None,
                        active_agent_gender=str(channel.get("active_agent_gender") or "").strip() or None,
                        active_agent_voice=str(channel.get("active_agent_voice") or "").strip() or None,
                        active_agent_icon=str(channel.get("active_agent_icon") or channel.get("agent_icon") or "").strip() or None,
                        active_agent_avatar_ref=str(
                            channel.get("active_agent_avatar_ref") or channel.get("agent_avatar_ref") or ""
                        ).strip()
                        or None,
                        route_id=str(channel.get("route_id") or "voice_chat"),
                        source_request_id=str(route_meta.get("request_id") or "").strip() or None,
                        bus=self.bus,
                        source="router.dialog",
                    )
                    await _write_dialog_state(ws, event="selected")
                    continue
                try:
                    result = await asyncio.to_thread(
                        _call_runtime_skill_tool,
                        "conversation_companions",
                        "start",
                        {"webspace_id": ws},
                        route_meta,
                    )
                except Exception:
                    logging.getLogger("adaos.router.dialog").warning(
                        "conversation_companions start failed during channel select webspace=%s",
                        ws,
                        exc_info=True,
                    )
                    await _write_dialog_state(ws, event="select_failed")
                    continue
                if isinstance(result, dict) and bool(result.get("ok")):
                    try:
                        dialog_runtime.apply_tool_result(
                            result,
                            webspace_id=ws,
                            target="conversation_companions.start",
                            raw_meta=route_meta,
                            payload_meta=route_meta,
                            bus=self.bus,
                            source="router.dialog",
                        )
                    except Exception:
                        logging.getLogger("adaos.router.dialog").debug(
                            "conversation_companions start result state update failed",
                            exc_info=True,
                        )
                await _write_dialog_state(ws, event="selected")

        async def _activate_requested_dialog_channel(
            webspace_id: str,
            channel_id: str,
            meta: Mapping[str, Any],
        ) -> bool:
            ws = str(webspace_id or "default").strip() or "default"
            cid = str(channel_id or "").strip().lower()
            if not cid or cid == GENERAL_DIALOG_CHANNEL_ID:
                return False
            try:
                active = dialog_runtime.get_active_channel(ws)
            except Exception:
                active = None
            if active is not None and str(active.channel_id or "").strip().lower() == cid:
                return True
            try:
                channel = next(
                    (
                        item
                        for item in _conversation_manifest_channel_records(ws)
                        if str(item.get("id") or item.get("channel_id") or "").strip().lower() == cid
                    ),
                    None,
                )
            except Exception:
                channel = None
            if not isinstance(channel, dict):
                return False
            try:
                dialog_runtime.activate_channel(
                    webspace_id=ws,
                    channel_id=cid,
                    owner=str(channel.get("owner") or f"channel:{cid}").strip() or f"channel:{cid}",
                    default_skill=str(channel.get("default_skill") or "").strip(),
                    default_tool=str(channel.get("default_tool") or "").strip(),
                    conversation_id=str(channel.get("conversation_id") or f"conv.{cid}.{ws}").strip(),
                    active_agent_id=str(meta.get("active_agent_id") or "").strip() or None,
                    active_agent_label=str(meta.get("active_agent_label") or "").strip() or None,
                    active_agent_owner=str(meta.get("active_agent_owner") or channel.get("owner") or "").strip() or None,
                    active_agent_kind=str(meta.get("active_agent_kind") or "skill_agent").strip() or None,
                    active_agent_gender=str(meta.get("active_agent_gender") or meta.get("voice_gender") or "").strip() or None,
                    active_agent_voice=str(meta.get("active_agent_voice") or meta.get("voice") or "").strip() or None,
                    active_agent_icon=str(meta.get("active_agent_icon") or meta.get("agent_icon") or "").strip() or None,
                    active_agent_avatar_ref=str(meta.get("active_agent_avatar_ref") or meta.get("agent_avatar_ref") or "").strip()
                    or None,
                    route_id=str(channel.get("route_id") or meta.get("route_id") or "voice_chat").strip() or "voice_chat",
                    bus=self.bus,
                    source="router.voice.requested_channel",
                )
                await _write_dialog_state(ws, event="requested_channel")
            except Exception:
                logging.getLogger("adaos.router.dialog").debug(
                    "requested dialog channel activation failed webspace=%s channel=%s",
                    ws,
                    cid,
                    exc_info=True,
                )
                return False
            return True

        async def _append_dialog_tool_unavailable(
            webspace_id: str,
            channel_id: str,
            meta: Mapping[str, Any],
            target_node_id: str | None,
        ) -> None:
            label = str(meta.get("active_agent_label") or _dialog_channel_label(channel_id)).strip()
            if str(channel_id or "").strip().lower() == "builder":
                text = (
                    "Builder channel selected, but builder_skill.chat is not available in runtime yet. "
                    "Install/activate builder_skill, then repeat the request."
                )
            else:
                text = f"{label} channel selected, but its runtime tool is not available yet."
            await _append_voice_chat_message(
                webspace_id,
                {
                    "id": _make_id("m"),
                    "from": "hub",
                    "text": text,
                    "ts": time.time(),
                    "active_agent_id": str(meta.get("active_agent_id") or "").strip() or None,
                    "active_agent_label": label or None,
                    "_meta": dict(meta),
                },
                target_node_id,
            )

        async def _on_voice_user(ev: Event) -> None:
            voice_started = time.perf_counter()
            phase_started = voice_started
            voice_ws = "-"

            def _log_voice_phase(phase: str, *, level: int = logging.DEBUG, extra: str = "") -> None:
                nonlocal phase_started
                now = time.perf_counter()
                logging.getLogger("adaos.router.voice_chat").log(
                    level,
                    "voice chat user phase=%s webspace=%s phase_ms=%.1f total_ms=%.1f%s",
                    phase,
                    voice_ws,
                    (now - phase_started) * 1000.0,
                    (now - voice_started) * 1000.0,
                    f" {extra}" if extra else "",
                )
                phase_started = now

            payload = ev.payload or {}
            if self._event_originates_from_remote_member(payload):
                return
            if not self._event_targets_local_node(payload):
                return
            try:
                target_webspaces = await _resolve_webspace_ids(payload)
            except Exception:
                target_webspaces = []
            ws = target_webspaces[0] if target_webspaces else "default"
            voice_ws = ws
            _log_voice_phase("resolve_webspace", extra=f"targets={len(target_webspaces)}")
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return
            text = text.strip()
            event_kind = str(getattr(ev, "type", "") or VOICE_CHAT_USER_EVENT).strip() or VOICE_CHAT_USER_EVENT

            try:
                self._vlog.info("%s received webspace=%s text=%r", event_kind, ws, text)
            except Exception:
                pass
            try:
                logging.getLogger("adaos.router.voice_chat").info("%s -> append+nlp webspace=%s", event_kind, ws)
            except Exception:
                pass

            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            target_node_id = _resolve_voice_target_node_id(payload, meta, default_local=True)

            meta = {**meta, "webspace_id": ws}
            if event_kind == DIALOG_USER_MESSAGE_EVENT:
                meta.setdefault("dialog_event_kind", DIALOG_USER_MESSAGE_EVENT)
                meta.setdefault("canonical_event_kind", DIALOG_USER_MESSAGE_EVENT)
                meta.setdefault("input_event_kind", DIALOG_USER_MESSAGE_EVENT)
            if len(target_webspaces) > 1:
                meta["webspace_ids"] = list(target_webspaces)
            if target_node_id:
                meta.setdefault("target_node_id", target_node_id)
            meta.setdefault("turn_trace_id", _make_id("trace"))
            requested_dialog_channel_id = str(meta.get("dialog_channel_id") or payload.get("dialog_channel_id") or "").strip().lower()
            try:
                pre_addressed_agent = _extract_addressed_agent(text)
            except Exception:
                pre_addressed_agent = None
            if pre_addressed_agent is not None:
                pre_agent = pre_addressed_agent[0]
                pre_channel_id = str(pre_agent.get("channel_id") or "").strip()
                if pre_channel_id and pre_channel_id != GENERAL_DIALOG_CHANNEL_ID:
                    requested_dialog_channel_id = pre_channel_id
                    meta["dialog_channel_id"] = pre_channel_id
                    meta["active_agent_id"] = str(pre_agent.get("id") or "").strip()
                    meta["active_agent_label"] = str(pre_agent.get("label") or "").strip()
                    meta["active_agent_gender"] = str(pre_agent.get("gender") or "").strip()
                    meta["active_agent_voice"] = str(pre_agent.get("voice") or "").strip()
                    meta["active_agent_icon"] = str(pre_agent.get("icon") or "").strip()
                    meta["active_agent_avatar_ref"] = str(pre_agent.get("avatar_ref") or "").strip()
                    meta["voice_gender"] = str(pre_agent.get("gender") or "").strip()
                    meta["voice"] = str(pre_agent.get("voice") or "").strip()
                    meta["voice_profile"] = _agent_voice_profile(pre_agent)
            _log_voice_phase(
                "metadata_prepared",
                extra=(
                    f"event_kind={event_kind} requested_channel={requested_dialog_channel_id or '-'} "
                    f"pre_addressed={bool(pre_addressed_agent)}"
                ),
            )
            if requested_dialog_channel_id == GENERAL_DIALOG_CHANNEL_ID:
                _persist_general_dialog_channel(ws, event="general_channel_requested")
                current_dialog_channel = dialog_runtime.get_active_channel(ws)
                if current_dialog_channel is not None:
                    dialog_runtime.deactivate_channel(
                        webspace_id=ws,
                        channel_id=current_dialog_channel.channel_id,
                        bus=self.bus,
                        source="router.voice",
                        reason="general_channel_requested",
                    )
                meta["dialog_channel_id"] = GENERAL_DIALOG_CHANNEL_ID
                _apply_general_agent_metadata(meta)
                try:
                    await _write_dialog_state(ws, event="general_channel_requested")
                except Exception:
                    pass
                _log_voice_phase("general_channel_requested")
            # Ensure voice chat history is updated even if io.out.chat.append routing breaks.
            msg = {
                "id": _make_id("m"),
                "from": "user",
                "text": text,
                "ts": time.time(),
                "_meta": dict(meta),
            }
            _record_voice_turn_trace(
                ws,
                meta,
                text=text,
                message_id=str(msg["id"]),
                selected_tool="router.voice.receive",
                reason="voice_user_received",
                renderer={"receiver": "voice_chat.messages", "projection": "user_append"},
                target_node_id=target_node_id,
            )
            msg["_meta"] = dict(meta)
            try:
                await _append_voice_chat_message(ws, msg, target_node_id)
            except Exception:
                pass
            _log_voice_phase("append_user_message")
            try:
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router",
                        ts=time.time(),
                        payload={
                            "id": msg["id"],
                            "from": msg["from"],
                            "text": msg["text"],
                            "ts": msg["ts"],
                            "_meta": {**meta, "route_id": "voice_chat", "skip_voice_chat": True},
                          },
                      )
                  )
            except Exception:
                pass
            _log_voice_phase("publish_user_echo")
            try:
                from adaos.services.nlu.teacher_confirmation_runtime import (
                    should_consume_voice_confirmation_answer,
                    should_suppress_voice_text_for_confirmation,
                )

                if await should_consume_voice_confirmation_answer(ws, text):
                    _log_voice_phase("teacher_confirmation_consumed")
                    try:
                        logging.getLogger("adaos.router.voice_chat").debug(
                            "voice.chat.user consumed as NLU Teacher confirmation answer webspace=%s text=%r",
                            ws,
                            text,
                        )
                    except Exception:
                        pass
                    return
                if await should_suppress_voice_text_for_confirmation(ws, text):
                    _log_voice_phase("teacher_confirmation_suppressed")
                    try:
                        logging.getLogger("adaos.router.voice_chat").debug(
                            "voice.chat.user suppressed during active NLU Teacher confirmation webspace=%s text=%r",
                            ws,
                            text,
                        )
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            _log_voice_phase("teacher_confirmation_checked")
            try:
                correction = correct_light_text(text)
                if correction.text and correction.text != text:
                    meta["original_text"] = text
                    meta["autocorrected_text"] = correction.text
                    meta["text_corrections"] = [dict(item) for item in correction.corrections]
                    text = correction.text
            except Exception:
                pass
            _log_voice_phase("text_correction")
            addressed_agent = _extract_addressed_agent(text)
            _log_voice_phase("addressed_agent_extracted", extra=f"addressed={bool(addressed_agent)}")
            if addressed_agent is not None and str(addressed_agent[0].get("channel_id") or "") == GENERAL_DIALOG_CHANNEL_ID:
                addressed_general_text = addressed_agent[1]
                general_meta = _general_agent_metadata()
                _persist_general_dialog_channel(ws, event="general_agent_addressed")
                _record_voice_turn_trace(
                    ws,
                    meta,
                    text=text,
                    selected_tool="router.voice.general_agent",
                    reason="general_agent_addressed",
                    renderer={"receiver": "voice_chat.messages", "projection": "general_agent"},
                    target_node_id=target_node_id,
                )
                current_dialog_channel = dialog_runtime.get_active_channel(ws)
                if current_dialog_channel is not None:
                    dialog_runtime.deactivate_channel(
                        webspace_id=ws,
                        channel_id=current_dialog_channel.channel_id,
                        bus=self.bus,
                        source="router.voice",
                        reason="general_agent_address",
                    )
                    try:
                        transition_msg = {
                            "id": _make_id("m"),
                            "from": "hub",
                            "text": _general_agent_transition_text(text),
                            "ts": time.time(),
                            **general_meta,
                            "_meta": dict(meta),
                        }
                        self.bus.publish(
                            Event(
                                type="io.out.chat.append",
                                source="router.voice",
                                ts=time.time(),
                                payload={
                                    **transition_msg,
                                    "_meta": {**dict(meta), "route_id": "voice_chat"},
                                },
                            )
                        )
                    except Exception:
                        pass
                meta["dialog_channel_id"] = GENERAL_DIALOG_CHANNEL_ID
                _apply_general_agent_metadata(meta)
                if not addressed_general_text:
                    try:
                        await _write_dialog_state(ws, event="general_agent_addressed")
                    except Exception:
                        pass
                    try:
                        await _append_voice_chat_message(
                            ws,
                            {
                                "id": _make_id("m"),
                                "from": "hub",
                                "text": _general_agent_ready_text(text),
                                "ts": time.time(),
                                **general_meta,
                                "_meta": dict(meta),
                            },
                            target_node_id,
                        )
                    except Exception:
                        pass
                    try:
                        await _ensure_tts_state(ws)
                    except Exception:
                        pass
                    return
                if _is_agent_roster_question(addressed_general_text):
                    try:
                        await _write_dialog_state(ws, event="general_agent_addressed")
                    except Exception:
                        pass
                    _record_voice_turn_trace(
                        ws,
                        meta,
                        text=text,
                        selected_tool="router.voice.agent_roster",
                        reason="agent_roster_question",
                        renderer={"receiver": "voice_chat.messages", "projection": "agent_roster"},
                        target_node_id=target_node_id,
                    )
                    try:
                        await _append_voice_chat_message(
                            ws,
                            {
                                "id": _make_id("m"),
                                "from": "hub",
                                "text": _agent_roster_text(),
                                "ts": time.time(),
                                **general_meta,
                                "_meta": dict(meta),
                            },
                            target_node_id,
                        )
                    except Exception:
                        pass
                    try:
                        await _ensure_tts_state(ws)
                    except Exception:
                        pass
                    return
                text = addressed_general_text
                _log_voice_phase("general_agent_forwarded")
            elif addressed_agent is not None:
                agent, agent_rest = addressed_agent
                channel_id = str(agent.get("channel_id") or "").strip()
                skill = str(agent.get("skill") or "").strip()
                talk_tool = str(agent.get("talk_tool") or "talk").strip()
                switch_tool = str(agent.get("switch_tool") or "").strip()
                character_id = str(agent.get("character_id") or "").strip()
                if channel_id and channel_id != GENERAL_DIALOG_CHANNEL_ID and skill:
                    action_meta = {
                        **meta,
                        "webspace_id": ws,
                        "route_id": "voice_chat",
                        "dialog_policy_reason": "addressed_agent",
                        "addressed_agent_id": str(agent.get("id") or "").strip(),
                        "dialog_channel_id": channel_id,
                        "active_agent_id": str(agent.get("id") or "").strip(),
                        "active_agent_label": str(agent.get("label") or "").strip(),
                        "active_agent_gender": str(agent.get("gender") or "").strip(),
                        "active_agent_voice": str(agent.get("voice") or "").strip(),
                        "active_agent_icon": str(agent.get("icon") or "").strip(),
                        "active_agent_avatar_ref": str(agent.get("avatar_ref") or "").strip(),
                        "voice_gender": str(agent.get("gender") or "").strip(),
                        "voice": str(agent.get("voice") or "").strip(),
                        "voice_profile": _agent_voice_profile(agent),
                    }
                    if character_id:
                        action_meta["character_id"] = character_id
                    try:
                        dialog_runtime.activate_channel(
                            webspace_id=ws,
                            channel_id=channel_id,
                            owner=str(agent.get("owner") or f"skill:{skill}").strip(),
                            default_skill=skill,
                            default_tool=talk_tool,
                            conversation_id=f"conv.skill.{skill}.default.{ws}",
                            active_agent_id=str(agent.get("id") or "").strip(),
                            active_agent_label=str(agent.get("label") or "").strip(),
                            active_agent_owner=str(agent.get("owner") or f"skill:{skill}").strip(),
                            active_agent_kind=str(agent.get("kind") or "skill_agent").strip(),
                            active_agent_gender=str(agent.get("gender") or "").strip(),
                            active_agent_voice=str(agent.get("voice") or "").strip(),
                            active_agent_icon=str(agent.get("icon") or "").strip(),
                            active_agent_avatar_ref=str(agent.get("avatar_ref") or "").strip() or None,
                            route_id="voice_chat",
                            bus=self.bus,
                            source="router.voice.addressed_agent",
                        )
                        await _write_dialog_state(ws, event="agent_addressed")
                    except Exception:
                        logging.getLogger("adaos.router.dialog").debug(
                            "addressed agent state update failed webspace=%s agent=%s",
                            ws,
                            agent.get("id"),
                            exc_info=True,
                        )
                    _log_voice_phase(
                        "addressed_agent_channel_activated",
                        extra=f"channel={channel_id} skill={skill} tool={talk_tool}",
                    )
                    if agent_rest or channel_id != CONVERSATIONAL_DIALOG_CHANNEL_ID:
                        forwarded_text = text if channel_id == CONVERSATIONAL_DIALOG_CHANNEL_ID else (agent_rest or text)
                        action_payload = {
                            "text": forwarded_text,
                            "webspace_id": ws,
                            "_meta": action_meta,
                        }
                        if agent_rest:
                            action_meta["addressed_agent_text"] = agent_rest
                        action_tool = talk_tool
                    else:
                        action_payload = {
                            "character_id": character_id,
                            "webspace_id": ws,
                            "_meta": action_meta,
                        }
                        action_tool = switch_tool or talk_tool
                    dialog_action = {
                        "kind": "skill_tool",
                        "skill": skill,
                        "tool": action_tool,
                        "payload": action_payload,
                    }
                    if await _handle_dialog_action(
                        dialog_action=dialog_action,
                        webspace_id=ws,
                        meta=action_meta,
                        route_id="voice_chat",
                        mark_request=False,
                    ):
                        _log_voice_phase(
                            "addressed_agent_action_handled",
                            extra=f"channel={channel_id} skill={skill} tool={action_tool}",
                        )
                        try:
                            await _ensure_tts_state(ws)
                        except Exception:
                            pass
                        _log_voice_phase("ensure_tts_after_addressed")
                        return
            try:
                current_channel_for_roster = dialog_runtime.get_active_channel(ws)
            except Exception:
                current_channel_for_roster = None
            current_channel_id_for_roster = (
                str(current_channel_for_roster.channel_id or "").strip().lower()
                if current_channel_for_roster is not None
                else GENERAL_DIALOG_CHANNEL_ID
            )
            if current_channel_id_for_roster == GENERAL_DIALOG_CHANNEL_ID and _is_agent_roster_question(text):
                general_meta = _general_agent_metadata()
                _record_voice_turn_trace(
                    ws,
                    meta,
                    text=text,
                    selected_tool="router.voice.agent_roster",
                    reason="agent_roster_question",
                    renderer={"receiver": "voice_chat.messages", "projection": "agent_roster"},
                    target_node_id=target_node_id,
                )
                try:
                    await _append_voice_chat_message(
                        ws,
                        {
                            "id": _make_id("m"),
                            "from": "hub",
                            "text": _agent_roster_text(),
                            "ts": time.time(),
                            **general_meta,
                            "_meta": dict(meta),
                        },
                        target_node_id,
                    )
                except Exception:
                    pass
                try:
                    await _ensure_tts_state(ws)
                except Exception:
                    pass
                return
            try:
                requested_non_general_channel = (
                    requested_dialog_channel_id
                    if requested_dialog_channel_id and requested_dialog_channel_id != GENERAL_DIALOG_CHANNEL_ID
                    else ""
                )
                if requested_non_general_channel:
                    await _activate_requested_dialog_channel(ws, requested_non_general_channel, meta)
                    _log_voice_phase("requested_dialog_channel_activated", extra=f"channel={requested_non_general_channel}")
                dialog_action = dialog_runtime.resolve_followup_action(
                    webspace_id=ws,
                    text=text,
                    route_id="voice_chat",
                    meta={**meta, "route_id": "voice_chat", "dialog_policy_reason": "active_dialog_followup"},
                )
            except Exception:
                dialog_action = None
            _log_voice_phase("followup_action_resolved", extra=f"has_action={isinstance(dialog_action, dict)}")
            if isinstance(dialog_action, dict) and await _handle_dialog_action(
                dialog_action=dialog_action,
                webspace_id=ws,
                meta={**meta, "route_id": "voice_chat", "dialog_policy_reason": "active_dialog_followup"},
                route_id="voice_chat",
                mark_request=False,
            ):
                _log_voice_phase("followup_action_handled")
                try:
                    await _ensure_tts_state(ws)
                except Exception:
                    pass
                _log_voice_phase("ensure_tts_after_followup")
                return
            if requested_dialog_channel_id and requested_dialog_channel_id != GENERAL_DIALOG_CHANNEL_ID:
                _record_voice_turn_trace(
                    ws,
                    meta,
                    text=text,
                    selected_tool="dialog.requested_channel.unavailable",
                    reason="requested_dialog_tool_unavailable",
                    renderer={"receiver": "voice_chat.messages", "projection": "tool_unavailable"},
                    status="failed",
                    target_node_id=target_node_id,
                )
                try:
                    await _append_dialog_tool_unavailable(ws, requested_dialog_channel_id, meta, target_node_id)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").debug(
                        "requested dialog tool unavailable message failed webspace=%s channel=%s",
                        ws,
                        requested_dialog_channel_id,
                        exc_info=True,
                    )
                try:
                    await _ensure_tts_state(ws)
                except Exception:
                    pass
                return
            # Fire-and-forget NLU detection so that text commands can be
            # mapped to scenario/skill actions via an external interpreter.
            _record_voice_turn_trace(
                ws,
                meta,
                text=text,
                selected_tool="nlp.intent.detect.request",
                reason="nlu_fallback",
                renderer={"receiver": "nlp.intent.detect.request", "projection": "event_bus"},
                summary="Published voice text to NLU interpreter",
                status="routed",
                target_node_id=target_node_id,
            )
            try:
                self.bus.publish(
                    Event(
                        type="nlp.intent.detect.request",
                        source="router.voice",
                        ts=time.time(),
                        payload={
                            "text": text,
                            "webspace_id": ws,
                            "request_id": meta.get("message_id") or meta.get("id") or _make_id("nlu"),
                            "_meta": {**meta, "route_id": "voice_chat"},
                        },
                    )
                )
            except Exception:
                pass
            _log_voice_phase("nlu_request_published")
            try:
                await _append_voice_intent_demo(ws, text, meta, target_node_id)
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "voice.chat intent demo failed",
                    exc_info=True,
                )
            _log_voice_phase("nlu_demo_appended")
            try:
                await _ensure_tts_state(ws)
            except Exception:
                pass
            _log_voice_phase("ensure_tts_after_nlu")
            # NLU pipeline + dispatcher + skills are responsible for producing
            # responses via io.out.chat.append / io.out.say.

        async def _on_nlp_intent_not_obtained(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            if self._event_originates_from_remote_member(payload):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            route_id = meta.get("route_id") or meta.get("route")
            if not isinstance(route_id, str) or not route_id.strip():
                return
            request_id = str(payload.get("request_id") or "").strip()
            webspace_id = str(meta.get("webspace_id") or payload.get("webspace_id") or "desktop").strip() or "desktop"
            try:
                from adaos.services.nlu.dispatcher import has_dispatched_request

                if has_dispatched_request(request_id=request_id, webspace_id=webspace_id, route_id=route_id.strip()):
                    return
            except Exception:
                pass
            try:
                allow_teacher = bool(getattr(getattr(get_ctx().config, "root_settings", None), "llm", None).allow_nlu_teacher)  # type: ignore[attr-defined]
            except Exception:
                allow_teacher = True
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                text = ""
            fallback_policy = _dialog_surface_fallback_policy(meta, payload, route_id=route_id.strip())
            if fallback_policy and text:
                dialog_action = dialog_runtime.resolve_followup_action(
                    webspace_id=webspace_id,
                    text=text,
                    route_id=route_id.strip(),
                    meta=meta,
                )
                if isinstance(dialog_action, dict) and await _handle_dialog_action(
                    dialog_action=dialog_action,
                    webspace_id=webspace_id,
                    meta=meta,
                    request_id=request_id,
                    route_id=route_id.strip(),
                    mark_request=True,
                ):
                    return
                try:
                    result = await asyncio.to_thread(_call_dialog_surface_fallback_tool, text, meta, fallback_policy)
                except Exception:
                    logging.getLogger("adaos.router.voice_chat").warning(
                        "dialog surface fallback tool failed policy=%r",
                        fallback_policy,
                        exc_info=True,
                    )
                else:
                    if isinstance(result, dict) and bool(result.get("ok")):
                        try:
                            from adaos.services.nlu.dispatcher import mark_dispatched_request

                            mark_dispatched_request(request_id=request_id, webspace_id=webspace_id, route_id=route_id.strip())
                        except Exception:
                            pass
                        return
                    logging.getLogger("adaos.router.voice_chat").warning(
                        "voice.chat fallback tool returned non-ok result=%r",
                        result,
                    )
                if allow_teacher:
                    try:
                        from adaos.services.nlu.teacher_confirmation_runtime import (
                            request_existing_candidate_confirmation,
                        )

                        if await request_existing_candidate_confirmation(
                            meta.get("webspace_id") or payload.get("webspace_id") or "desktop",
                            text,
                            request_id=str(payload.get("request_id") or ""),
                            meta=meta,
                        ):
                            return
                    except Exception:
                        logging.getLogger("adaos.router.voice_chat").warning(
                            "voice.chat existing teacher confirmation failed",
                            exc_info=True,
                        )
                if _voice_intent_demo_enabled():
                    return
            reason = payload.get("reason")
            msg_text = "Я пока не понял запрос."
            if isinstance(reason, str) and reason:
                msg_text = f"{msg_text} ({reason})"
            if text:
                msg_text = f"{msg_text} Вы сказали: «{text}»."
            if allow_teacher:
                msg_text = f"{msg_text} Я записал запрос для обучения. Открой «NLU Teacher» в Apps, чтобы посмотреть детали."
            try:
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router.nlu",
                        ts=time.time(),
                        payload={
                            "id": "",
                            "from": "hub",
                            "text": msg_text,
                            "ts": time.time(),
                            "_meta": {**meta, "route_id": route_id.strip()},
                        },
                    )
                )
            except Exception:
                pass

        async def _on_nlp_teacher_candidate_proposed(ev: Event) -> None:
            payload = ev.payload or {}
            if not isinstance(payload, dict):
                return
            meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
            route_id = meta.get("route_id") or meta.get("route")
            if not isinstance(route_id, str) or not route_id.strip():
                return

            cand = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
            req_text = cand.get("text") if isinstance(cand.get("text"), str) else ""
            kind = cand.get("kind") if isinstance(cand.get("kind"), str) else "skill"
            status = cand.get("status") if isinstance(cand.get("status"), str) else ""
            if _is_dialog_surface_route(meta, payload, route_id=route_id.strip()) and kind == "regex_rule" and cand.get("status") == "pending":
                return
            cdef = cand.get("candidate") if isinstance(cand.get("candidate"), dict) else {}
            name = cdef.get("name") if isinstance(cdef.get("name"), str) else ""
            desc = cdef.get("description") if isinstance(cdef.get("description"), str) else ""
            preview = cand.get("preview") if isinstance(cand.get("preview"), dict) else {}
            preview_status = preview.get("status") if isinstance(preview.get("status"), str) else ""

            if kind == "regex_rule":
                label_kind = "правило regex"
            else:
                label_kind = "навык" if kind == "skill" else "сценарий"
            msg = "Я подготовил предложение для обучения NLU."
            if req_text:
                msg = f"Вы просили: «{req_text}».\n\nЯ подумал и добавил в план разработки кандидат: {label_kind}."
            if status == "quarantined":
                reason = preview_status or "preview_failed"
                msg = (
                    (f"Вы просили: «{req_text}».\n\n" if req_text else "")
                    + f"Я нашел гипотезу обучения, но не могу применить ее без исправления: проверка дала {reason}. "
                    "Кандидат помещен в карантин в NLU Teacher."
                )
            if name:
                msg += f"\nНазвание: {name}"
            if desc:
                msg += f"\nОписание: {desc}"
            msg += "\n\nОткрой «NLU Teacher» (Apps) — там лог запроса/ответа и список кандидатов."

            try:
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router.nlu",
                        ts=time.time(),
                        payload={
                            "id": "",
                            "from": "hub",
                            "text": msg,
                            "ts": time.time(),
                            "_meta": {**meta, "route_id": route_id.strip()},
                        },
                    )
                )
            except Exception:
                pass


        self.bus.subscribe("voice.chat.open", _on_voice_open)
        self.bus.subscribe("voice.chat.user", _on_voice_user)
        self.bus.subscribe("dialog.user_message", _on_voice_user)
        self.bus.subscribe("dialog.channel.select", _on_dialog_channel_select)
        self.bus.subscribe("dialog.channel.activated", _on_dialog_channel_event)
        self.bus.subscribe("dialog.channel.deactivated", _on_dialog_channel_event)
        self.bus.subscribe("io.out.chat.append", _on_io_out_chat_append)
        self.bus.subscribe("io.out.say", _on_io_out_say)
        self.bus.subscribe("io.out.media.route", _on_io_out_media_route)
        self.bus.subscribe("io.out.stream.publish", _on_io_out_stream_publish)
        self.bus.subscribe("webio.stream.snapshot.requested", _on_voice_chat_stream_snapshot)
        self.bus.subscribe("webio.stream.subscription.changed", _on_voice_chat_stream_snapshot)
        self.bus.subscribe("conversation.history.more", _on_conversation_history_more)
        self.bus.subscribe("browser.session.changed", _on_browser_session_changed)
        self.bus.subscribe("subnet.member.snapshot.changed", _on_member_media_inventory_changed)
        self.bus.subscribe("subnet.member.link.up", _on_member_media_inventory_changed)
        self.bus.subscribe("subnet.member.link.down", _on_member_media_inventory_changed)
        self.bus.subscribe("capacity.changed", _on_member_media_inventory_changed)
        self.bus.subscribe("nlp.intent.not_obtained", _on_nlp_intent_not_obtained)
        self.bus.subscribe("nlp.teacher.candidate.proposed", _on_nlp_teacher_candidate_proposed)

        # Watch rules file
        def _reload(rules: list[dict]):
            self._rules = rules or []

        # Preload rules and start watcher
        try:
            node_id = get_ctx().config.node_id
        except Exception:
            # fallback: do not crash router if config is not ready yet
            node_id = ""
        self._rules = load_rules(self.base_dir, node_id)
        self._stop_watch = watch_rules(self.base_dir, node_id, _reload)

    async def stop(self) -> None:
        if self._stop_watch:
            try:
                self._stop_watch()
            except Exception:
                pass
            self._stop_watch = None
        if self._notify_tasks:
            try:
                timeout_s = max(0.0, float(os.getenv("ADAOS_ROUTER_NOTIFY_DRAIN_TIMEOUT_S") or "1.0"))
            except Exception:
                timeout_s = 1.0
            pending = list(self._notify_tasks)
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s)
            except asyncio.TimeoutError:
                for task in pending:
                    if not task.done():
                        task.cancel()
            except Exception:
                pass
            self._notify_tasks.clear()
        if self._voice_chat_append_tasks:
            try:
                timeout_s = max(0.0, float(os.getenv("ADAOS_VOICE_CHAT_APPEND_DRAIN_TIMEOUT_S") or "1.0"))
            except Exception:
                timeout_s = 1.0
            pending = list(self._voice_chat_append_tasks)
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s)
            except asyncio.TimeoutError:
                for task in pending:
                    if not task.done():
                        task.cancel()
            except Exception:
                pass
            self._voice_chat_append_tasks.clear()
            self._voice_chat_append_locks.clear()
        if self._voice_chat_persist_tasks:
            try:
                timeout_s = max(0.0, float(os.getenv("ADAOS_VOICE_CHAT_PERSIST_DRAIN_TIMEOUT_S") or "1.0"))
            except Exception:
                timeout_s = 1.0
            pending = list(self._voice_chat_persist_tasks)
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s)
            except asyncio.TimeoutError:
                for task in pending:
                    if not task.done():
                        task.cancel()
            except Exception:
                pass
            self._voice_chat_persist_tasks.clear()
            self._voice_chat_persist_tasks_by_key.clear()
            self._voice_chat_persist_pending.clear()
            self._voice_chat_persist_next_allowed_at.clear()
        if self._dialog_state_tasks:
            try:
                timeout_s = max(0.0, float(os.getenv("ADAOS_DIALOG_STATE_DRAIN_TIMEOUT_S") or "1.0"))
            except Exception:
                timeout_s = 1.0
            pending = [task for task in self._dialog_state_tasks.values() if not task.done()]
            try:
                if pending:
                    await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s)
            except asyncio.TimeoutError:
                for task in pending:
                    if not task.done():
                        task.cancel()
            except Exception:
                pass
            self._dialog_state_tasks.clear()
            self._dialog_state_pending_events.clear()
        self._media_route_webspaces.clear()
        self._started = False
