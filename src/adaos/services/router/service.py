from __future__ import annotations

from typing import Any, Callable, Mapping
from pathlib import Path
import asyncio
import copy
import json
import time
import requests
import os
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
from .media_routes import build_media_route_refresh_payload, resolve_media_route_state
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
from adaos.services import (
    conversation_context,
    conversation_response,
    conversation_store,
    dialog_runtime,
    durable_delivery,
    telegram_delivery,
)
from adaos.services.nlu.text_correction import correct_light_text
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL


_log = logging.getLogger("adaos.router.service")


_WEBIO_RECEIVER_METADATA_CACHE_TTL_S = 2.0

from . import dialog_registry as _dialog_registry
from . import telegram_projection as _telegram_projection
from . import voice_chat_stream as _voice_chat_stream
from . import webio_stream_guard as _webio_stream_guard

GENERAL_DIALOG_AGENT_ID = _dialog_registry.GENERAL_DIALOG_AGENT_ID
GENERAL_DIALOG_AGENT_CONFIGURED_LABEL = _dialog_registry.GENERAL_DIALOG_AGENT_CONFIGURED_LABEL
GENERAL_DIALOG_AGENT_DEFAULT_LABEL = _dialog_registry.GENERAL_DIALOG_AGENT_DEFAULT_LABEL
GENERAL_DIALOG_AGENT_GENDER = _dialog_registry.GENERAL_DIALOG_AGENT_GENDER
GENERAL_DIALOG_AGENT_VOICE = _dialog_registry.GENERAL_DIALOG_AGENT_VOICE
GENERAL_DIALOG_AGENT_OWNER = _dialog_registry.GENERAL_DIALOG_AGENT_OWNER
GENERAL_DIALOG_CHANNEL_ID = _dialog_registry.GENERAL_DIALOG_CHANNEL_ID
CONVERSATIONAL_DIALOG_CHANNEL_ID = _dialog_registry.CONVERSATIONAL_DIALOG_CHANNEL_ID
BUILDER_DIALOG_CHANNEL_ID = _dialog_registry.BUILDER_DIALOG_CHANNEL_ID
BUILDER_SKILL_ID = _dialog_registry.BUILDER_SKILL_ID
CONVERSATION_COMPANIONS_SKILL_ID = _dialog_registry.CONVERSATION_COMPANIONS_SKILL_ID
DIALOG_USER_MESSAGE_EVENT = _dialog_registry.DIALOG_USER_MESSAGE_EVENT
VOICE_CHAT_USER_EVENT = _dialog_registry.VOICE_CHAT_USER_EVENT
VOICE_CHAT_STREAM_RECEIVER = _webio_stream_guard.VOICE_CHAT_STREAM_RECEIVER
VOICE_CHAT_VISIBLE_TAIL = _voice_chat_stream.VOICE_CHAT_VISIBLE_TAIL
VOICE_CHAT_HISTORY_LIMIT = _voice_chat_stream.VOICE_CHAT_HISTORY_LIMIT
VOICE_CHAT_STREAM_TEXT_MAX_CHARS = _voice_chat_stream.VOICE_CHAT_STREAM_TEXT_MAX_CHARS
VOICE_CHAT_STREAM_ACTION_JSON_MAX_CHARS = _voice_chat_stream.VOICE_CHAT_STREAM_ACTION_JSON_MAX_CHARS
VOICE_CHAT_STREAM_ACTIONS_MAX = _voice_chat_stream.VOICE_CHAT_STREAM_ACTIONS_MAX
VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES = _voice_chat_stream.VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES
_WEBIO_STREAM_GUARD_STATS_LOCK = _webio_stream_guard._WEBIO_STREAM_GUARD_STATS_LOCK
_WEBIO_STREAM_GUARD_STATS = _webio_stream_guard._WEBIO_STREAM_GUARD_STATS


def _sync_router_helper_dependencies() -> None:
    _telegram_projection.get_ctx = get_ctx
    _dialog_registry.get_ctx = get_ctx
    _dialog_registry.load_config = load_config
    _dialog_registry.load_subnet_alias = load_subnet_alias
    _dialog_registry.display_subnet_alias = display_subnet_alias
    _dialog_registry.conversation_store = conversation_store
    _webio_stream_guard.async_get_ydoc = async_get_ydoc


def _call_telegram_helper(name: str, *args: Any, **kwargs: Any) -> Any:
    _sync_router_helper_dependencies()
    return getattr(_telegram_projection, name)(*args, **kwargs)


def _call_voice_chat_stream_helper(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(_voice_chat_stream, name)(*args, **kwargs)


def _call_dialog_registry_helper(name: str, *args: Any, **kwargs: Any) -> Any:
    _sync_router_helper_dependencies()
    return getattr(_dialog_registry, name)(*args, **kwargs)


def _call_webio_stream_guard_helper(name: str, *args: Any, **kwargs: Any) -> Any:
    _sync_router_helper_dependencies()
    return getattr(_webio_stream_guard, name)(*args, **kwargs)


def _dialog_ingress_route_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_ingress_route_id", *args, **kwargs)


def _telegram_text_chunks(*args: Any, **kwargs: Any) -> Any:
    return _call_telegram_helper("_telegram_text_chunks", *args, **kwargs)


def _telegram_output_projection(*args: Any, **kwargs: Any) -> Any:
    return _call_telegram_helper("_telegram_output_projection", *args, **kwargs)


def _telegram_interaction_consumed_projection(*args: Any, **kwargs: Any) -> Any:
    return _call_telegram_helper("_telegram_interaction_consumed_projection", *args, **kwargs)


def _rehydrate_durable_interaction_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Replace event projections with their authoritative durable records.

    ``conversation.interaction.responded`` is a notification that a durable
    decision exists, not a second authority-bearing copy of that decision.
    Event transport, presentation retirement, or tool dispatch may add
    delivery metadata to their own envelopes.  Reloading both records here
    keeps those concerns outside the digest-protected business response while
    retaining the existing exact-record check at workflow admission.
    """

    event_payload = copy.deepcopy(dict(payload or {}))
    event_interaction = (
        event_payload.get("interaction")
        if isinstance(event_payload.get("interaction"), Mapping)
        else {}
    )
    event_response = (
        event_payload.get("response")
        if isinstance(event_payload.get("response"), Mapping)
        else {}
    )
    interaction_id = str(event_interaction.get("interaction_id") or "").strip()
    response_id = str(event_response.get("response_id") or "").strip()
    if not interaction_id or not response_id:
        raise ValueError("interaction response event identity is required")

    durable_interaction = conversation_store.get_interaction(interaction_id)
    durable_response = conversation_store.get_interaction_response(response_id)
    if not isinstance(durable_interaction, Mapping) or not isinstance(durable_response, Mapping):
        raise ValueError("durable interaction response event record is unavailable")
    if str(durable_interaction.get("interaction_id") or "").strip() != interaction_id:
        raise ValueError("durable interaction identity differs from the event")
    if str(durable_response.get("response_id") or "").strip() != response_id:
        raise ValueError("durable interaction response identity differs from the event")
    if str(durable_response.get("interaction_id") or "").strip() != interaction_id:
        raise ValueError("durable response belongs to another interaction")
    interaction_generation = durable_interaction.get("generation")
    response_generation = durable_response.get("interaction_generation")
    if interaction_generation is None or response_generation is None:
        raise ValueError("durable interaction response generation is required")
    if int(interaction_generation) != int(response_generation) + 1:
        raise ValueError("durable interaction response is no longer current")
    interaction_meta = (
        durable_interaction.get("metadata")
        if isinstance(durable_interaction.get("metadata"), Mapping)
        else {}
    )
    latest_response_id = str(interaction_meta.get("latest_response_id") or "").strip()
    if latest_response_id and latest_response_id != response_id:
        raise ValueError("durable interaction response was superseded")

    event_payload["interaction"] = copy.deepcopy(dict(durable_interaction))
    event_payload["response"] = copy.deepcopy(dict(durable_response))
    return event_payload


def _builder_transport_integrity_error(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_builder_transport_integrity_error", *args, **kwargs)


def _truncate_voice_chat_stream_text(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_truncate_voice_chat_stream_text", *args, **kwargs)


def _compact_voice_chat_stream_action(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_compact_voice_chat_stream_action", *args, **kwargs)


def _compact_voice_chat_stream_message(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_compact_voice_chat_stream_message", *args, **kwargs)


def _compact_voice_chat_stream_messages(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_compact_voice_chat_stream_messages", *args, **kwargs)


def _voice_chat_stream_json_bytes(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_stream_json_bytes", *args, **kwargs)


def _bound_voice_chat_stream_messages(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_bound_voice_chat_stream_messages", *args, **kwargs)


def _webio_receiver_metadata_timeout_s(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_receiver_metadata_timeout_s", *args, **kwargs)


def _voice_chat_yjs_timeout_s(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_yjs_timeout_s", *args, **kwargs)


def _voice_chat_persist_debounce_s(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_persist_debounce_s", *args, **kwargs)


def _voice_chat_persist_failure_backoff_s(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_persist_failure_backoff_s", *args, **kwargs)


def _voice_chat_persist_stream_snapshots_enabled(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_persist_stream_snapshots_enabled", *args, **kwargs)


def _voice_chat_snapshot_republish_interval_s(*args: Any, **kwargs: Any) -> Any:
    return _call_voice_chat_stream_helper("_voice_chat_snapshot_republish_interval_s", *args, **kwargs)


def _webio_stream_guard_enabled(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_guard_enabled", *args, **kwargs)


def _webio_stream_warn_bytes(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_warn_bytes", *args, **kwargs)


def _webio_stream_block_bytes(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_block_bytes", *args, **kwargs)


def _webio_stream_payload_bytes(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_payload_bytes", *args, **kwargs)


def _as_dict(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_as_dict", *args, **kwargs)


def _positive_int(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_positive_int", *args, **kwargs)


def _general_conversation_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_conversation_id", *args, **kwargs)


def _skill_conversation_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_skill_conversation_id", *args, **kwargs)


def _dialog_channel_label(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_channel_label", *args, **kwargs)


def _dialog_runtime_dev_fallback_allowed(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_runtime_dev_fallback_allowed", *args, **kwargs)


def _dialog_runtime_uses_dev_webspace(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_runtime_uses_dev_webspace", *args, **kwargs)


def _dialog_channel_policy(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_channel_policy", *args, **kwargs)


def _dedupe_texts(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dedupe_texts", *args, **kwargs)


def _current_subnet_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_current_subnet_id", *args, **kwargs)


def _is_technical_subnet_label(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_is_technical_subnet_label", *args, **kwargs)


def _general_agent_label(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_label", *args, **kwargs)


def _general_agent_aliases(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_aliases", *args, **kwargs)


def _general_agent_record(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_record", *args, **kwargs)


def _is_dialog_surface_route(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_is_dialog_surface_route", *args, **kwargs)


def _dialog_surface_fallback_policy(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_dialog_surface_fallback_policy", *args, **kwargs)


def _fallback_agent_registry_records(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_fallback_agent_registry_records", *args, **kwargs)


def _skill_manifest_dirs(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_skill_manifest_dirs", *args, **kwargs)


def _read_skill_manifest(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_read_skill_manifest", *args, **kwargs)


def _conversation_manifest_agent_records(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_conversation_manifest_agent_records", *args, **kwargs)


def _normalize_manifest_renderer_capabilities(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_normalize_manifest_renderer_capabilities", *args, **kwargs)


def _conversation_manifest_channel_records(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_conversation_manifest_channel_records", *args, **kwargs)


def _seed_manifest_dialog_channels(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_seed_manifest_dialog_channels", *args, **kwargs)


def _conversation_companion_profile_agent_records(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_conversation_companion_profile_agent_records", *args, **kwargs)


def _seed_conversation_registry(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_seed_conversation_registry", *args, **kwargs)


def _agent_registry_records(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_registry_records", *args, **kwargs)


def _agent_record_by_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_record_by_id", *args, **kwargs)


def _agent_voice_profile(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_voice_profile", *args, **kwargs)


def _agent_avatar_ref(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_avatar_ref", *args, **kwargs)


def _browser_voice_hint(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_browser_voice_hint", *args, **kwargs)


def _agent_projection_from_record(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_projection_from_record", *args, **kwargs)


def _agent_label_from_id(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_label_from_id", *args, **kwargs)


def _general_agent_projection(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_projection", *args, **kwargs)


def _general_agent_metadata(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_metadata", *args, **kwargs)


def _apply_general_agent_metadata(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_apply_general_agent_metadata", *args, **kwargs)


def _active_agent_projection(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_active_agent_projection", *args, **kwargs)


def _extract_general_agent_addressed_text(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_extract_general_agent_addressed_text", *args, **kwargs)


def _extract_addressed_agent(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_extract_addressed_agent", *args, **kwargs)


def _general_agent_transition_text(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_transition_text", *args, **kwargs)


def _general_agent_ready_text(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_general_agent_ready_text", *args, **kwargs)


def _is_agent_roster_question(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_is_agent_roster_question", *args, **kwargs)


def _agent_roster_text(*args: Any, **kwargs: Any) -> Any:
    return _call_dialog_registry_helper("_agent_roster_text", *args, **kwargs)


def _receiver_declared_owner(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_receiver_declared_owner", *args, **kwargs)


def _static_webio_receiver_metadata(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_static_webio_receiver_metadata", *args, **kwargs)


def _webio_stream_stats_key(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_stats_key", *args, **kwargs)


def _record_webio_stream_guard_event(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_record_webio_stream_guard_event", *args, **kwargs)


def webio_stream_guard_snapshot(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("webio_stream_guard_snapshot", *args, **kwargs)


async def _read_webio_receiver_metadata(*args: Any, **kwargs: Any) -> Any:
    _sync_router_helper_dependencies()
    return await _webio_stream_guard._read_webio_receiver_metadata(*args, **kwargs)


def _webio_stream_owner(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_owner", *args, **kwargs)


def _webio_stream_admit(*args: Any, **kwargs: Any) -> Any:
    return _call_webio_stream_guard_helper("_webio_stream_admit", *args, **kwargs)




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

    async def _on_telegram_delivery_receipt(self, ev: Event) -> None:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        try:
            payload = telegram_delivery.validate_receipt(payload)
        except Exception:
            _log.warning("router: invalid Telegram delivery receipt", exc_info=True)
            return
        attempt_id = str(payload.get("delivery_attempt_id") or "").strip()
        if not attempt_id:
            return
        delivered = bool(payload.get("delivered"))
        error = str(payload.get("error") or "").strip() or None
        receipt = {
            "schema": "adaos.telegram.delivery_receipt.v1",
            "receipt_id": str(payload.get("receipt_id") or "").strip() or None,
            "operation_key": str(payload.get("operation_key") or "").strip() or None,
            "transport": str(payload.get("transport") or "telegram").strip() or "telegram",
            "external_message_ids": [
                str(item) for item in payload.get("external_message_ids") or [] if str(item)
            ],
            "duplicate_suppressed": int(payload.get("duplicate_suppressed") or 0),
            "completed_at": str(payload.get("completed_at") or "").strip() or None,
        }
        try:
            attempt = telegram_delivery.complete_outbound(
                attempt_id,
                delivered=delivered,
                receipt=receipt,
                error=error,
            )
        except Exception:
            _log.warning("router: unknown Telegram delivery receipt attempt=%s", attempt_id, exc_info=True)
            return
        durable_attempt_id = str(attempt.get("durable_attempt_id") or "").strip()
        if durable_attempt_id:
            try:
                durable_delivery.complete_delivery(
                    durable_attempt_id,
                    delivered=delivered,
                    receipt=receipt,
                    error=error,
                )
            except Exception:
                _log.warning(
                    "router: durable response receipt could not be completed attempt=%s",
                    durable_attempt_id,
                    exc_info=True,
                )

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
        telegram_delivery_handled = False

        # If this came from a chat platform (telegram), reply back into that chat via tg.output.*.
        # This path does not depend on route rules and is meant to be "request/response" style.
        try:
            if is_tg and is_tg_chat and not self._tg_reply_via_root_http:
                projection = _telegram_output_projection(
                    {"id": payload.get("id"), "from": "hub", "text": text, "ts": payload.get("ts")},
                    meta,
                )
                if projection is not None:
                    subject, out_payload = projection
                    self.bus.publish(
                        Event(
                            type=subject,
                            source="router",
                            ts=time.time(),
                            payload=out_payload,
                        )
                    )
                    telegram_delivery_handled = True
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
                            "_meta": {
                                **meta,
                                "route_id": route_id.strip(),
                                "telegram_delivery_handled": telegram_delivery_handled,
                            },
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

                api_base = getattr(_get_ctx().settings, "api_base", DEFAULT_PUBLIC_ROOT_BASE_URL)
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

    def _compose_handlers(self) -> list[tuple[str, Callable[[Event], Any]]]:
        if not self._subscribed:
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

            write_task = asyncio.create_task(
                _mutate_data_map(
                    webspace_id,
                    _mutator,
                    channel="core.router.dialog.store",
                    prefer_live_room=True,
                ),
                name=f"dialog-state-projection:{webspace_id}",
            )
            try:
                done, _pending = await asyncio.wait(
                    {write_task},
                    timeout=_voice_chat_yjs_timeout_s(),
                )
                if not done:
                    logging.getLogger("adaos.router.dialog").warning(
                        "dialog.state yjs write exceeded latency budget webspace=%s event=%s; projection continues in background",
                        webspace_id,
                        event,
                    )
                # Do not cancel an in-flight YDoc session when the latency
                # budget is exceeded.  Cancellation can leave native y_py
                # objects in a coroutine cycle which a later skill worker GC
                # then drops on the wrong thread.  The durable conversation
                # store is authoritative; this task only refreshes its compact
                # browser projection and is already isolated by the per-space
                # coalescing worker below.
                await write_task
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
            compact_messages = _compact_voice_chat_stream_messages(
                [dict(item) for item in messages if isinstance(item, dict)]
            )
            cached_messages, omitted_for_budget = _bound_voice_chat_stream_messages(compact_messages)
            total_count = int(total_message_count if total_message_count is not None else len(compact_messages))
            effective_has_more_before = bool(has_more_before) or omitted_for_budget > 0 or total_count > len(cached_messages)
            effective_before_cursor = str(before_cursor or "")
            if omitted_for_budget:
                try:
                    effective_before_cursor = str(int(effective_before_cursor or "0") + omitted_for_budget)
                except (TypeError, ValueError):
                    effective_before_cursor = str(max(0, total_count - len(cached_messages)))
            elif not effective_before_cursor and effective_has_more_before:
                effective_before_cursor = str(max(0, total_count - len(cached_messages)))
            conversation_id, dialog_channel_id, topic_id = _voice_chat_projection_identity(cached_messages)
            signature = _voice_chat_persist_signature(
                cached_messages,
                before_cursor=effective_before_cursor,
                has_more_before=effective_has_more_before,
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
                "has_more_before": effective_has_more_before,
                "before_cursor": effective_before_cursor,
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
                    "has_more_before": effective_has_more_before,
                    "before_cursor": effective_before_cursor,
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
            snapshot = _compact_voice_chat_stream_messages(
                [dict(item) for item in messages[-VOICE_CHAT_VISIBLE_TAIL:] if isinstance(item, dict)]
            )
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
                recovered = await asyncio.to_thread(
                    conversation_store.recover_projection_from_store,
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
                projection = ledger_projection or await asyncio.to_thread(
                    conversation_store.recover_projection_from_store,
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
                projection = await asyncio.to_thread(
                    conversation_store.list_projection,
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
                stored = conversation_store.materialize_message(
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
                    idempotency_key=str(
                        local_meta.get("response_idempotency_key")
                        or local_meta.get("idempotency_key")
                        or ""
                    )
                    or None,
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

        def _refresh_media_route_payload(route_state: dict[str, Any], *, cause: str, observed_failure: str | None = None) -> dict[str, Any]:
            return build_media_route_refresh_payload(
                route_state,
                cause=cause,
                browser_session_totals=_active_browser_session_totals(),
                observed_failure=observed_failure,
            )

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
            return resolve_media_route_state(
                payload,
                webspace_id=webspace_id,
                browser_session_totals=_active_browser_session_totals(),
                previous_route_state=previous_route_state,
                coerce_value=_coerce_y,
            )

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
                _schedule_dialog_state_write(ws, event="voice_open")
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
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return

            try:
                # All dialog replies use the canonical tg.output transport.  The
                # former Root HTTP shortcut discarded interaction actions and
                # returned before local materialization, so a turn could be
                # reported as successful without a Telegram-visible result.
                projection = _telegram_output_projection(payload, meta)
                if projection is not None:
                    subject, out_payload = projection
                    self.bus.publish(
                        Event(
                            type=subject,
                            source="router.dialog.transport",
                            ts=time.time(),
                            payload=out_payload,
                        )
                    )
            except Exception:
                logging.getLogger("adaos.router").warning(
                    "router: telegram dialog projection failed",
                    exc_info=True,
                )

            if isinstance(meta, dict) and meta.get("skip_voice_chat") is True:
                return

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
                "progress_group_id",
                "progress_phase",
                "progress_status",
                "progress_label",
            ):
                raw_value = payload.get(key) if payload.get(key) is not None else meta.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    msg[key] = raw_value.strip()
            progress_seq = payload.get("progress_seq") if payload.get("progress_seq") is not None else meta.get("progress_seq")
            if isinstance(progress_seq, (int, float)):
                msg["progress_seq"] = int(progress_seq)
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
            # Keep the scheduling marker in the tool metadata.  Besides
            # measuring queue latency it is the explicit hand-off contract
            # telling a dialog skill that Router owns fallback
            # materialization.  Removing it here lets the skill emit an
            # eager chat event while the materialization probe is active;
            # the probe can then observe the event before durable storage and
            # suppress Router's reliable fallback (most visible on Telegram).
            scheduled_raw = route_meta.get("_router_tool_scheduled_at")
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
                    if _dialog_runtime_uses_dev_webspace(webspace_id):
                        if not hasattr(mgr, "run_dev_tool"):
                            raise RuntimeError(
                                f"DEV dialog runtime is unavailable for webspace '{webspace_id}'"
                            )
                        try:
                            result = mgr.run_dev_tool(skill, tool, tool_payload)
                        except Exception:
                            log.warning(
                                "dialog runtime tool run failed skill=%s tool=%s webspace=%s runtime=dev_authoritative manager_ms=%.1f run_ms=%.1f total_ms=%.1f",
                                skill,
                                tool,
                                webspace_id,
                                manager_ms,
                                (time.perf_counter() - run_started) * 1000.0,
                                (time.perf_counter() - worker_started) * 1000.0,
                            )
                            raise
                        run_ms = (time.perf_counter() - run_started) * 1000.0
                        total_ms = (time.perf_counter() - worker_started) * 1000.0
                        log.log(
                            _dialog_timing_level(total_ms),
                            "dialog runtime tool run completed skill=%s tool=%s webspace=%s runtime=dev_authoritative manager_ms=%.1f run_ms=%.1f total_ms=%.1f",
                            skill,
                            tool,
                            webspace_id,
                            manager_ms,
                            run_ms,
                            total_ms,
                        )
                        return result
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
                        if not _dialog_runtime_dev_fallback_allowed(skill, workspace_exc) or not hasattr(mgr, "run_dev_tool"):
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
                            "workspace dialog skill unavailable; trying dev runtime skill=%s tool=%s",
                            skill,
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

        async def _consume_voice_chat_interaction_controls(
            interaction: Mapping[str, Any],
            response: Mapping[str, Any],
        ) -> None:
            """Retire a used Web/Voice control in both ledger and live projection."""

            response_meta = response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
            consumed = response.get("consumed_command") if isinstance(response.get("consumed_command"), Mapping) else {}
            source_message_id = str(response_meta.get("source_message_id") or "").strip()
            if (
                str(response_meta.get("io_type") or "").strip() != "web"
                or str(response.get("status") or "").strip() != "answered"
                or not source_message_id
                or not consumed
            ):
                return
            message = await asyncio.to_thread(conversation_store.get_message, source_message_id)
            if not isinstance(message, dict):
                return
            label = str(consumed.get("label") or consumed.get("command") or "").strip()
            locale_context = interaction.get("locale_context") if isinstance(interaction.get("locale_context"), Mapping) else {}
            locale = str(locale_context.get("locale") or "").strip().lower()
            selected_prefix = "✓ Выбрано:" if locale.startswith("ru") else "✓ Selected:"
            suffix = f"{selected_prefix} {label}" if label else selected_prefix.rstrip(":")
            current_text = str(message.get("text") or "")
            updated_text = current_text if suffix in current_text else f"{current_text}\n\n{suffix}".strip()
            consumed_state = {
                "interaction_id": str(interaction.get("interaction_id") or "").strip(),
                "response_id": str(response.get("response_id") or "").strip(),
                "command": str(consumed.get("command") or "").strip(),
                "label": label,
            }
            updated = await asyncio.to_thread(
                conversation_store.update_message,
                source_message_id,
                text=updated_text,
                payload={"actions": [], "interaction_consumed": consumed_state},
            )
            updated_webspace_id = str(updated.get("webspace_id") or response_meta.get("webspace_id") or "").strip()
            for cache_key, cached in list(_voice_chat_stream_cache.items()):
                if updated_webspace_id and cache_key[0] != updated_webspace_id:
                    continue
                raw_messages = cached.get("messages") if isinstance(cached, dict) else None
                if not isinstance(raw_messages, list) or not any(
                    str(item.get("id") or "") == source_message_id
                    for item in raw_messages
                    if isinstance(item, Mapping)
                ):
                    continue
                next_messages = [
                    dict(updated) if str(item.get("id") or "") == source_message_id else dict(item)
                    for item in raw_messages
                    if isinstance(item, Mapping)
                ]
                last_refresh_ts = time.time()
                _publish_voice_chat_stream(
                    cache_key[0],
                    cache_key[1] or None,
                    next_messages,
                    last_refresh_ts,
                    before_cursor=str(cached.get("before_cursor") or ""),
                    has_more_before=bool(cached.get("has_more_before")),
                    total_message_count=int(cached.get("total_message_count") or len(next_messages)),
                )
                _schedule_voice_chat_persist(
                    cache_key[0],
                    cache_key[1] or None,
                    next_messages,
                    last_refresh_ts,
                    before_cursor=str(cached.get("before_cursor") or ""),
                    has_more_before=bool(cached.get("has_more_before")),
                    total_message_count=int(cached.get("total_message_count") or len(next_messages)),
                )

        async def _on_conversation_interaction_responded(ev: Event) -> None:
            raw_payload = ev.payload if isinstance(ev.payload, Mapping) else {}
            if not raw_payload or bool(raw_payload.get("duplicate")):
                return
            try:
                payload = await asyncio.to_thread(
                    _rehydrate_durable_interaction_event,
                    raw_payload,
                )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "interaction response event rejected before dispatch",
                    exc_info=True,
                )
                return
            interaction = payload.get("interaction") if isinstance(payload.get("interaction"), Mapping) else {}
            response = payload.get("response") if isinstance(payload.get("response"), Mapping) else {}
            try:
                await _consume_voice_chat_interaction_controls(interaction, response)
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "voice chat interaction consumption failed interaction_id=%s response_id=%s",
                    interaction.get("interaction_id"),
                    response.get("response_id"),
                    exc_info=True,
                )
            try:
                consumed_projection = _telegram_interaction_consumed_projection(interaction, response)
                if consumed_projection is not None:
                    subject, out_payload = consumed_projection
                    self.bus.publish(
                        Event(
                            type=subject,
                            source="router.interaction.presentation",
                            ts=time.time(),
                            payload=out_payload,
                        )
                    )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "interaction presentation consumption projection failed interaction_id=%s response_id=%s",
                    interaction.get("interaction_id"),
                    response.get("response_id"),
                    exc_info=True,
                )
            interaction_meta = interaction.get("metadata") if isinstance(interaction.get("metadata"), Mapping) else {}
            if str(interaction_meta.get("domain") or "").strip() != "builder":
                return
            response_meta = response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
            topic_ref = interaction_meta.get("topic_ref") if isinstance(interaction_meta.get("topic_ref"), Mapping) else {}
            execution_webspace_id = str(
                interaction_meta.get("execution_webspace_id")
                or response_meta.get("execution_webspace_id")
                or topic_ref.get("dev_webspace_id")
                or response_meta.get("webspace_id")
                or interaction_meta.get("source_webspace_id")
                or "desktop"
            ).strip() or "desktop"
            route_meta = {
                **dict(response_meta),
                "webspace_id": execution_webspace_id,
                "source_webspace_id": str(
                    interaction_meta.get("source_webspace_id")
                    or response_meta.get("source_webspace_id")
                    or execution_webspace_id
                ).strip(),
                "conversation_id": str(
                    interaction.get("conversation_id")
                    or response_meta.get("conversation_id")
                    or ""
                ).strip(),
                "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
                "interaction_id": str(interaction.get("interaction_id") or "").strip(),
                "interaction_response_id": str(response.get("response_id") or "").strip(),
            }
            try:
                await asyncio.to_thread(
                    _call_runtime_skill_tool,
                    BUILDER_SKILL_ID,
                    "handle_interaction_response",
                    {
                        "event": dict(payload),
                        "webspace_id": execution_webspace_id,
                    },
                    route_meta,
                )
            except Exception:
                logging.getLogger("adaos.router.voice_chat").warning(
                    "Builder interaction response dispatch failed interaction_id=%s response_id=%s webspace=%s",
                    interaction.get("interaction_id"),
                    response.get("response_id"),
                    execution_webspace_id,
                    exc_info=True,
                )

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
                _schedule_dialog_state_write(webspace_id, event="exit")
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
                external_transport = str(action_meta.get("io_type") or "").strip().lower()
                trace_status = "delivery_pending" if external_transport == "telegram" else "materialized"
                trace_renderer = {
                    "receiver": "telegram.transport" if external_transport == "telegram" else "voice_chat.messages",
                    "projection": "skill_emitted_message" if result_message else "response_envelope",
                    "message_id": materialized_payload.get("id"),
                }
                if external_transport == "telegram":
                    trace_summary = f"{skill}.{tool} returned ok; Telegram delivery was projected and awaits transport acknowledgement"
                    trace_policy["materialization_status"] = "local_materialized"
                    trace_policy["delivery_status"] = "pending_acknowledgement"
                else:
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
            _schedule_dialog_state_write(webspace_id, event=event_name)

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
                    _schedule_dialog_state_write(ws, event="selected")
                    continue
                if current_id == channel_id:
                    _schedule_dialog_state_write(ws, event="selected")
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
                        _schedule_dialog_state_write(ws, event="select_failed")
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
                    _schedule_dialog_state_write(ws, event="selected")
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
                    _schedule_dialog_state_write(ws, event="select_failed")
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
                _schedule_dialog_state_write(ws, event="selected")

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
            if not isinstance(channel, dict) and cid == CONVERSATIONAL_DIALOG_CHANNEL_ID:
                requested_agent_id = str(meta.get("active_agent_id") or "").strip()
                if not requested_agent_id.startswith("agent:conversation_companions:"):
                    requested_agent_id = "agent:conversation_companions:arseni"
                agent_record = _agent_record_by_id(requested_agent_id) or _agent_record_by_id(
                    "agent:conversation_companions:arseni"
                )
                agent = _agent_projection_from_record(agent_record) if isinstance(agent_record, dict) else {}
                active_agent_id = str(agent.get("id") or requested_agent_id).strip()
                active_agent_label = str(agent.get("label") or meta.get("active_agent_label") or "Arseni").strip()
                active_agent_owner = str(agent.get("owner") or "skill:conversation_companions").strip()
                active_agent_kind = str(agent.get("kind") or "skill_agent").strip()
                active_agent_gender = str(agent.get("gender") or "").strip()
                active_agent_voice = str(agent.get("voice") or "").strip()
                active_agent_icon = str(agent.get("icon") or "").strip()
                active_agent_avatar_ref = str(agent.get("avatar_ref") or "").strip()
                channel = {
                    "id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
                    "channel_id": CONVERSATIONAL_DIALOG_CHANNEL_ID,
                    "label": active_agent_label or "Conversational",
                    "owner": "skill:conversation_companions",
                    "conversation_id": _skill_conversation_id("conversation_companions", ws),
                    "default_skill": "conversation_companions",
                    "default_tool": "talk",
                    "route_id": "voice_chat",
                    "policy": _dialog_channel_policy(
                        CONVERSATIONAL_DIALOG_CHANNEL_ID,
                        default_tool="conversation_companions.talk",
                    ),
                    "active_agent_id": active_agent_id or None,
                    "active_agent_label": active_agent_label or None,
                    "active_agent_owner": active_agent_owner or "skill:conversation_companions",
                    "active_agent_kind": active_agent_kind or "skill_agent",
                    "active_agent_gender": active_agent_gender or None,
                    "active_agent_voice": active_agent_voice or None,
                    "active_agent_icon": active_agent_icon or None,
                    "active_agent_avatar_ref": active_agent_avatar_ref or None,
                    "meta": {"source": "core:conversation_companions_dialog_fallback"},
                }
            if not isinstance(channel, dict) and cid == BUILDER_DIALOG_CHANNEL_ID:
                channel = {
                    "id": BUILDER_DIALOG_CHANNEL_ID,
                    "channel_id": BUILDER_DIALOG_CHANNEL_ID,
                    "label": "Builder",
                    "owner": f"skill:{BUILDER_SKILL_ID}",
                    "conversation_id": _skill_conversation_id(BUILDER_SKILL_ID, ws),
                    "default_skill": BUILDER_SKILL_ID,
                    "default_tool": "chat",
                    "route_id": "voice_chat",
                }
            if not isinstance(channel, dict):
                return False
            default_skill = str(channel.get("default_skill") or "").strip()
            owner = str(channel.get("owner") or f"channel:{cid}").strip() or f"channel:{cid}"
            if not default_skill and owner.startswith("skill:"):
                default_skill = owner.split(":", 1)[1]
            default_tool = str(channel.get("default_tool") or "").strip()
            if "." in default_tool:
                tool_skill, _, tool_name = default_tool.partition(".")
                default_skill = default_skill or tool_skill
                default_tool = tool_name
            try:
                active = dialog_runtime.get_active_channel(ws)
            except Exception:
                active = None
            if (
                active is not None
                and str(active.channel_id or "").strip().lower() == cid
                and str(active.owner or "").strip() == owner
                and str(active.default_skill or "").strip() == default_skill
                and str(active.default_tool or "").strip() == default_tool
            ):
                return True
            try:
                dialog_runtime.activate_channel(
                    webspace_id=ws,
                    channel_id=cid,
                    owner=owner,
                    default_skill=default_skill,
                    default_tool=default_tool,
                    conversation_id=str(channel.get("conversation_id") or f"conv.{cid}.{ws}").strip(),
                    active_agent_id=str(meta.get("active_agent_id") or channel.get("active_agent_id") or "").strip() or None,
                    active_agent_label=str(
                        meta.get("active_agent_label") or channel.get("active_agent_label") or channel.get("label") or ""
                    ).strip()
                    or None,
                    active_agent_owner=str(
                        meta.get("active_agent_owner")
                        or channel.get("active_agent_owner")
                        or channel.get("owner")
                        or ""
                    ).strip()
                    or None,
                    active_agent_kind=str(
                        meta.get("active_agent_kind") or channel.get("active_agent_kind") or "skill_agent"
                    ).strip()
                    or None,
                    active_agent_gender=str(
                        meta.get("active_agent_gender")
                        or channel.get("active_agent_gender")
                        or meta.get("voice_gender")
                        or channel.get("gender")
                        or ""
                    ).strip()
                    or None,
                    active_agent_voice=str(
                        meta.get("active_agent_voice")
                        or channel.get("active_agent_voice")
                        or meta.get("voice")
                        or channel.get("voice")
                        or ""
                    ).strip()
                    or None,
                    active_agent_icon=str(
                        meta.get("active_agent_icon")
                        or channel.get("active_agent_icon")
                        or meta.get("agent_icon")
                        or channel.get("icon")
                        or ""
                    ).strip()
                    or None,
                    active_agent_avatar_ref=str(
                        meta.get("active_agent_avatar_ref")
                        or channel.get("active_agent_avatar_ref")
                        or meta.get("agent_avatar_ref")
                        or channel.get("agent_avatar_ref")
                        or ""
                    ).strip()
                    or None,
                    route_id=str(channel.get("route_id") or meta.get("route_id") or "voice_chat").strip() or "voice_chat",
                    bus=self.bus,
                    source="router.voice.requested_channel",
                )
                _schedule_dialog_state_write(ws, event="requested_channel")
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
            default_skill = str(meta.get("default_skill") or "").strip()
            default_tool = str(meta.get("default_tool") or "").strip()
            if "." in default_tool:
                tool_skill, _, tool_name = default_tool.partition(".")
                default_skill = default_skill or tool_skill
                default_tool = tool_name
            if not default_skill or not default_tool:
                try:
                    active = dialog_runtime.get_active_channel(webspace_id)
                except Exception:
                    active = None
                if active is not None and str(active.channel_id or "").strip().lower() == str(channel_id or "").strip().lower():
                    default_skill = default_skill or str(active.default_skill or "").strip()
                    default_tool = default_tool or str(active.default_tool or "").strip()
            if not default_skill or not default_tool:
                try:
                    channel = conversation_store.get_dialog_channel(webspace_id, str(channel_id or "").strip().lower())
                except Exception:
                    channel = None
                if isinstance(channel, dict):
                    default_skill = default_skill or str(channel.get("default_skill") or "").strip()
                    default_tool = default_tool or str(channel.get("default_tool") or "").strip()
                    if "." in default_tool:
                        tool_skill, _, tool_name = default_tool.partition(".")
                        default_skill = default_skill or tool_skill
                        default_tool = tool_name
            runtime_ref = f"{default_skill}.{default_tool}".strip(".")
            if str(channel_id or "").strip().lower() == "builder":
                text = (
                    "Builder channel selected, but builder_skill.chat is not available in runtime yet. "
                    "Install/activate builder_skill, then repeat the request."
                )
            elif runtime_ref:
                text = (
                    f"{label} channel selected, but {runtime_ref} is not available in runtime yet. "
                    f"Install/activate {default_skill}, then repeat the request."
                )
            else:
                text = f"{label} channel selected, but its runtime tool is not available yet."
            response = {
                "id": _make_id("m"),
                "from": "hub",
                "text": text,
                "ts": time.time(),
                "active_agent_id": str(meta.get("active_agent_id") or "").strip() or None,
                "active_agent_label": label or None,
                "_meta": dict(meta),
            }
            await _append_voice_chat_message(webspace_id, response, target_node_id)
            if str(meta.get("io_type") or "").strip().lower() == "telegram":
                self.bus.publish(
                    Event(
                        type="io.out.chat.append",
                        source="router.dialog",
                        ts=time.time(),
                        payload={
                            **response,
                            "_meta": {**dict(meta), "skip_voice_chat": True},
                        },
                    )
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
            turn_route_id = _dialog_ingress_route_id(meta, event_kind)
            meta["route_id"] = turn_route_id
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
            transport_integrity_error = _builder_transport_integrity_error(
                text,
                meta=meta,
                dialog_channel_id=requested_dialog_channel_id,
            )
            if transport_integrity_error:
                rejection = {
                    "id": _make_id("m"),
                    "from": "hub",
                    "text": (
                        "Builder rejected a transport-corrupted message before persistence. "
                        "Resend the original text as UTF-8."
                    ),
                    "ts": time.time(),
                    "_meta": {
                        **meta,
                        "route_id": turn_route_id,
                        "transport_integrity": "rejected",
                        "transport_integrity_reason": transport_integrity_error,
                    },
                }
                try:
                    await _append_voice_chat_message(ws, rejection, target_node_id)
                except Exception:
                    pass
                try:
                    self.bus.publish(
                        Event(
                            type="io.out.chat.append",
                            source="router.voice",
                            ts=time.time(),
                            payload={
                                **rejection,
                                "_meta": {
                                    **dict(rejection["_meta"]),
                                    "skip_voice_chat": True,
                                },
                            },
                        )
                    )
                except Exception:
                    pass
                _log_voice_phase(
                    "transport_integrity_rejected",
                    extra=f"reason={transport_integrity_error}",
                )
                return
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
                    _schedule_dialog_state_write(ws, event="general_channel_requested")
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
                            "_meta": {**meta, "route_id": turn_route_id, "skip_voice_chat": True},
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
                                    "_meta": {**dict(meta), "route_id": turn_route_id},
                                },
                            )
                        )
                    except Exception:
                        pass
                meta["dialog_channel_id"] = GENERAL_DIALOG_CHANNEL_ID
                _apply_general_agent_metadata(meta)
                if not addressed_general_text:
                    try:
                        _schedule_dialog_state_write(ws, event="general_agent_addressed")
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
                        _schedule_dialog_state_write(ws, event="general_agent_addressed")
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
                        "route_id": turn_route_id,
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
                            route_id=turn_route_id,
                            bus=self.bus,
                            source="router.voice.addressed_agent",
                        )
                        _schedule_dialog_state_write(ws, event="agent_addressed")
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
                        route_id=turn_route_id,
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
                    route_id=turn_route_id,
                    meta={**meta, "route_id": turn_route_id, "dialog_policy_reason": "active_dialog_followup"},
                )
            except Exception:
                dialog_action = None
            _log_voice_phase("followup_action_resolved", extra=f"has_action={isinstance(dialog_action, dict)}")
            if isinstance(dialog_action, dict) and await _handle_dialog_action(
                dialog_action=dialog_action,
                webspace_id=ws,
                meta={**meta, "route_id": turn_route_id, "dialog_policy_reason": "active_dialog_followup"},
                route_id=turn_route_id,
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
                            "_meta": {**meta, "route_id": turn_route_id},
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
            # A member may already own the interactive dialog RPC and publish
            # this event only as evidence for the asynchronous LLM teacher.
            # Do not start a second dialog fallback or append a duplicate reply.
            if meta.get("nlu_teacher_only") is True:
                return
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

        return [
            ("ui.notify", self._on_event),
            ("ui.say", _on_say),
            ("voice.chat.open", _on_voice_open),
            ("voice.chat.user", _on_voice_user),
            ("dialog.user_message", _on_voice_user),
            ("dialog.channel.select", _on_dialog_channel_select),
            ("dialog.channel.activated", _on_dialog_channel_event),
            ("dialog.channel.deactivated", _on_dialog_channel_event),
            ("conversation.interaction.responded", _on_conversation_interaction_responded),
            ("tg.delivery.receipt", self._on_telegram_delivery_receipt),
            ("io.out.chat.append", _on_io_out_chat_append),
            ("io.out.say", _on_io_out_say),
            ("io.out.media.route", _on_io_out_media_route),
            ("io.out.stream.publish", _on_io_out_stream_publish),
            ("webio.stream.snapshot.requested", _on_voice_chat_stream_snapshot),
            ("webio.stream.subscription.changed", _on_voice_chat_stream_snapshot),
            ("conversation.history.more", _on_conversation_history_more),
            ("browser.session.changed", _on_browser_session_changed),
            ("subnet.member.snapshot.changed", _on_member_media_inventory_changed),
            ("subnet.member.link.up", _on_member_media_inventory_changed),
            ("subnet.member.link.down", _on_member_media_inventory_changed),
            ("capacity.changed", _on_member_media_inventory_changed),
            ("nlp.intent.not_obtained", _on_nlp_intent_not_obtained),
            ("nlp.teacher.candidate.proposed", _on_nlp_teacher_candidate_proposed),
        ]

    def _start_rules_watch(self) -> None:
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

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            conversation_store.ensure_schema()
            _seed_conversation_registry()
        except Exception:
            logging.getLogger("adaos.router.dialog").debug("conversation store bootstrap failed", exc_info=True)
        if not self._subscribed:
            for event_type, handler in self._compose_handlers():
                self.bus.subscribe(event_type, handler)
            self._subscribed = True
        self._start_rules_watch()

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
