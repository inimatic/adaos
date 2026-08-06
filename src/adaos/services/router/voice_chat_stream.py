from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any


try:
    VOICE_CHAT_VISIBLE_TAIL = max(8, min(int(str(os.getenv("ADAOS_VOICE_CHAT_VISIBLE_TAIL") or "24").strip()), 100))
except Exception:
    VOICE_CHAT_VISIBLE_TAIL = 24
VOICE_CHAT_HISTORY_LIMIT = 200
VOICE_CHAT_STREAM_TEXT_MAX_CHARS = 1200
VOICE_CHAT_STREAM_ACTION_JSON_MAX_CHARS = 4096
VOICE_CHAT_STREAM_ACTIONS_MAX = 6

try:
    VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES = max(
        4096,
        min(int(str(os.getenv("ADAOS_VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES") or "16384").strip()), 65536),
    )
except Exception:
    VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES = 16384

def _truncate_voice_chat_stream_text(value: Any, *, max_chars: int = VOICE_CHAT_STREAM_TEXT_MAX_CHARS) -> str:
    text = str(value or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _compact_voice_chat_stream_action(action: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("id", "label", "title", "icon", "fill", "command", "token"):
        value = action.get(key)
        if value is not None and value != "":
            compact[key] = value
    if action.get("disabled") is True:
        compact["disabled"] = True
    for key in ("params", "action"):
        value = action.get(key)
        if value is None:
            continue
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            continue
        if len(encoded) <= VOICE_CHAT_STREAM_ACTION_JSON_MAX_CHARS:
            compact[key] = value
    return compact


def _compact_voice_chat_stream_message(item: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "id",
        "from",
        "ts",
        "seq",
        "conversation_id",
        "dialog_channel_id",
        "thread_id",
        "conversation_topic_id",
        "topic_id",
        "turn_trace_id",
        "active_agent_id",
        "active_agent_label",
        "active_agent_gender",
        "active_agent_voice",
        "active_agent_icon",
        "active_agent_avatar_ref",
        "agent_avatar_ref",
        "recipient_label",
        "origin_label",
        "progress_group_id",
        "progress_phase",
        "progress_status",
        "progress_label",
        "progress_seq",
    ):
        value = item.get(key)
        if value is not None and value != "":
            compact[key] = value
    compact["text"] = _truncate_voice_chat_stream_text(item.get("text"))
    meta = item.get("_meta")
    if isinstance(meta, Mapping):
        compact_meta: dict[str, Any] = {}
        for key in (
            "route_id",
            "dialog_channel_id",
            "dialog_event_kind",
            "canonical_event_kind",
            "input_event_kind",
            "origin",
            "origin_kind",
            "actor_kind",
            "source",
        ):
            value = meta.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    compact_meta[key] = value[:160]
            elif isinstance(value, (bool, int, float)):
                compact_meta[key] = value
        if compact_meta:
            compact["_meta"] = compact_meta
    actions = item.get("actions")
    if isinstance(actions, list):
        compact_actions = [
            _compact_voice_chat_stream_action(action)
            for action in actions[:VOICE_CHAT_STREAM_ACTIONS_MAX]
            if isinstance(action, Mapping)
        ]
        compact_actions = [action for action in compact_actions if action]
        if compact_actions:
            compact["actions"] = compact_actions
    return compact


def _compact_voice_chat_stream_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _compact_voice_chat_stream_message(item)
        for item in messages
        if isinstance(item, Mapping)
    ]


def _voice_chat_stream_json_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8", errors="replace"
        )
    )


def _bound_voice_chat_stream_messages(
    messages: list[dict[str, Any]],
    *,
    max_bytes: int = VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES,
) -> tuple[list[dict[str, Any]], int]:
    """Keep the newest contiguous chat tail within the WebIO payload budget."""
    budget = max(1024, int(max_bytes or VOICE_CHAT_STREAM_SNAPSHOT_MAX_BYTES))
    selected_reversed: list[dict[str, Any]] = []
    used_bytes = 2  # JSON list brackets.
    for source in reversed(messages):
        candidate = dict(source)
        candidate_bytes = _voice_chat_stream_json_bytes(candidate)
        while candidate_bytes > budget - 2 and isinstance(candidate.get("actions"), list) and candidate["actions"]:
            candidate["actions"] = candidate["actions"][:-1]
            if not candidate["actions"]:
                candidate.pop("actions", None)
            candidate_bytes = _voice_chat_stream_json_bytes(candidate)
        if candidate_bytes > budget - 2:
            candidate["text"] = _truncate_voice_chat_stream_text(candidate.get("text"), max_chars=256)
            candidate_bytes = _voice_chat_stream_json_bytes(candidate)
        separator_bytes = 1 if selected_reversed else 0
        if selected_reversed and used_bytes + separator_bytes + candidate_bytes > budget:
            break
        selected_reversed.append(candidate)
        used_bytes += separator_bytes + candidate_bytes
        if used_bytes >= budget:
            break
    selected = list(reversed(selected_reversed))
    return selected, max(0, len(messages) - len(selected))

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
