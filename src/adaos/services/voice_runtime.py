from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


LISTENING_MODES = frozenset({"off", "push_to_talk", "continuous", "activation"})
LONG_FORM_START_INTENT = "voice.long_form.start"
LONG_FORM_NOTE_START_INTENT = "voice.long_form.note.start"
LONG_FORM_DIALOG_START_INTENT = "voice.long_form.dialog.start"
LONG_FORM_START_INTENTS = frozenset(
    {
        LONG_FORM_START_INTENT,
        LONG_FORM_NOTE_START_INTENT,
        LONG_FORM_DIALOG_START_INTENT,
    }
)
LONG_FORM_STOP_INTENT = "voice.long_form.stop"
_POLICY_LOCK = threading.RLock()
_LONG_FORM_LOCK = threading.RLock()
_ACTIVATION_ARBITER_LOCK = threading.RLock()
_ACTIVATION_ARBITER: "VoiceActivationArbiter | None" = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_listening_mode(value: Any, *, default: str = "activation") -> str:
    token = _text(value).lower().replace("-", "_")
    aliases = {
        "disabled": "off",
        "manual": "push_to_talk",
        "ptt": "push_to_talk",
        "wake": "activation",
        "wake_word": "activation",
    }
    normalized = aliases.get(token, token)
    return normalized if normalized in LISTENING_MODES else default


def voice_policy_path() -> Path:
    override = _text(os.getenv("ADAOS_VOICE_POLICY_PATH"))
    if override:
        return Path(override)
    try:
        from adaos.services.agent_context import get_ctx

        root = Path(get_ctx().paths.state_dir())
    except Exception:
        root = Path(".adaos") / "state"
    return root / "voice" / "listening-policy.json"


def long_form_sessions_path() -> Path:
    override = _text(os.getenv("ADAOS_VOICE_LONG_FORM_PATH"))
    if override:
        return Path(override)
    return voice_policy_path().with_name("long-form-sessions.json")


def long_form_scope_key(
    webspace_id: str,
    *,
    speaker_id: str = "",
    device_id: str = "",
) -> str:
    # Until verified diarization is available, unknown speakers deliberately
    # share one anonymous session per webspace and capture device.
    return "\0".join(
        (
            _text(webspace_id) or "default",
            _text(device_id) or "local",
            _text(speaker_id) or "anonymous",
        )
    )


def _read_long_form_sessions_unlocked() -> dict[str, Any]:
    try:
        payload = json.loads(long_form_sessions_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        payload = {}
    sessions = _mapping(payload.get("sessions")) if isinstance(payload, Mapping) else {}
    return {
        "schema_version": "voice-long-form-sessions.v1",
        "sessions": sessions,
    }


def read_long_form_session(scope_key: str) -> dict[str, Any]:
    with _LONG_FORM_LOCK:
        sessions = _read_long_form_sessions_unlocked()["sessions"]
        value = _mapping(sessions.get(_text(scope_key)))
    return {**new_long_form_state(), **value}


def advance_persisted_long_form_session(
    scope_key: str,
    *,
    text: str,
    intent_name: str,
    active_agent_id: str = "",
    addressed_agent_id: str = "",
    drop_trailing_text: bool = False,
) -> dict[str, Any]:
    key = _text(scope_key)
    if not key:
        raise ValueError("voice_long_form_scope_required")
    with _LONG_FORM_LOCK:
        payload = _read_long_form_sessions_unlocked()
        sessions = payload["sessions"]
        state = {**new_long_form_state(), **_mapping(sessions.get(key))}
        if drop_trailing_text:
            token = _text(text)
            segments = [_text(item) for item in state.get("segments") or [] if _text(item)]
            if segments and segments[-1] == token:
                segments.pop()
                state["segments"] = segments
                state["text"] = " ".join(segments)
        result = advance_long_form_session(
            state,
            text=text,
            intent_name=intent_name,
            active_agent_id=active_agent_id,
            addressed_agent_id=addressed_agent_id,
        )
        sessions[key] = dict(result["session"])
        path = long_form_sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
        return result


def default_voice_policy() -> dict[str, Any]:
    return {
        "schema_version": "voice-listening-policy.v1",
        "listening_mode": "activation",
        "activation": {
            "detector": "transcript_address",
            "require_address": True,
            "aliases_source": "dialog_agent_registry",
        },
        "audio_processing": {
            "aec": "required_when_output_active",
            "noise_suppression": True,
            "automatic_gain_control": True,
            "echo_reference": True,
        },
        "speaker_separation": {
            "mode": "metadata_only",
            "enrollment_required": False,
        },
        "room_arbitration": {
            "enabled": True,
            "window_ms": 280,
        },
        "updated_at": _now(),
    }


def read_voice_policy() -> dict[str, Any]:
    path = voice_policy_path()
    with _POLICY_LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {}
    policy = default_voice_policy()
    if isinstance(payload, Mapping):
        for key in ("activation", "audio_processing", "speaker_separation", "room_arbitration"):
            policy[key] = {**_mapping(policy.get(key)), **_mapping(payload.get(key))}
        policy.update(
            {
                key: value
                for key, value in payload.items()
                if key not in {"activation", "audio_processing", "speaker_separation", "room_arbitration"}
            }
        )
    policy["listening_mode"] = normalize_listening_mode(policy.get("listening_mode"))
    return policy


def set_voice_policy(
    *,
    listening_mode: Any,
    source: str = "device_registry",
    updates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = voice_policy_path()
    with _POLICY_LOCK:
        policy = read_voice_policy()
        mode = normalize_listening_mode(listening_mode, default="")
        if not mode:
            raise ValueError("voice_listening_mode_invalid")
        policy["listening_mode"] = mode
        for key in ("activation", "audio_processing", "speaker_separation", "room_arbitration"):
            incoming = _mapping(_mapping(updates).get(key))
            if incoming:
                policy[key] = {**_mapping(policy.get(key)), **incoming}
        policy["source"] = _text(source) or "device_registry"
        policy["updated_at"] = _now()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    return policy


def listening_service_projection(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current = dict(policy or read_voice_policy())
    return {
        "contract": "node.voice.listening.v1",
        "state": "disabled" if current.get("listening_mode") == "off" else "ready",
        "listening_mode": normalize_listening_mode(current.get("listening_mode")),
        "modes": sorted(LISTENING_MODES),
        "activation": _mapping(current.get("activation")),
        "audio_processing": _mapping(current.get("audio_processing")),
        "speaker_separation": _mapping(current.get("speaker_separation")),
        "room_arbitration": _mapping(current.get("room_arbitration")),
        "settings": {
            "tool": "node.voice.configure",
            "field": "listening_mode",
        },
        "updated_at": current.get("updated_at"),
    }


def new_long_form_state() -> dict[str, Any]:
    return {
        "schema_version": "voice-long-form-session.v1",
        "state": "idle",
        "session_id": "",
        "segments": [],
        "text": "",
        "updated_at": _now(),
    }


def advance_long_form_session(
    state: Mapping[str, Any] | None,
    *,
    text: str,
    intent_name: str,
    active_agent_id: str = "",
    addressed_agent_id: str = "",
) -> dict[str, Any]:
    current = {**new_long_form_state(), **_mapping(state)}
    token = _text(text)
    intent = _text(intent_name)
    active = current.get("state") == "recording"
    now = _now()

    if not active and intent in LONG_FORM_START_INTENTS:
        purpose = (
            "note"
            if intent == LONG_FORM_NOTE_START_INTENT
            else "dialog"
            if intent == LONG_FORM_DIALOG_START_INTENT
            else "generic"
        )
        seed = f"{active_agent_id}:{time.time_ns()}"
        current.update(
            {
                "state": "recording",
                "session_id": "dictation:" + hashlib.sha256(seed.encode()).hexdigest()[:16],
                "owner_agent_id": _text(active_agent_id),
                "purpose": purpose,
                "segments": [],
                "text": "",
                "started_at": now,
                "updated_at": now,
            }
        )
        return {
            "action": "started",
            "dispatch": False,
            "session": current,
            "client_directives": [
                {
                    "type": "voice.dictation.start",
                    "source": "nlu",
                    "intent": intent,
                    "purpose": purpose,
                }
            ],
        }

    if not active:
        return {"action": "pass", "dispatch": True, "text": token, "session": current, "client_directives": []}

    alternative_address = bool(_text(addressed_agent_id)) and intent != LONG_FORM_STOP_INTENT
    if intent == LONG_FORM_STOP_INTENT or alternative_address:
        completed = " ".join(_text(item) for item in current.get("segments") or [] if _text(item)).strip()
        current.update(
            {
                "state": "completed",
                "text": completed,
                "ended_at": now,
                "ended_by": "alternative_address" if alternative_address else "nlu",
                "updated_at": now,
            }
        )
        return {
            "action": "completed_and_dispatch" if alternative_address else "completed",
            "dispatch": alternative_address,
            "text": token if alternative_address else "",
            "completed_text": completed,
            "session": current,
            "client_directives": [
                {
                    "type": "voice.dictation.stop",
                    "source": "activation" if alternative_address else "nlu",
                    "intent": intent or None,
                }
            ],
        }

    segments = [_text(item) for item in current.get("segments") or [] if _text(item)]
    if token:
        segments.append(token)
    current.update({"segments": segments[-512:], "text": " ".join(segments), "updated_at": now})
    return {
        "action": "appended",
        "dispatch": False,
        "session": current,
        "client_directives": [{"type": "voice.dictation.append", "source": "stt", "segment_count": len(segments)}],
    }


def choose_activation_candidate(
    candidates: Sequence[Mapping[str, Any]],
    *,
    now_ms: float | None = None,
    window_ms: int = 280,
) -> dict[str, Any]:
    """Select one room endpoint without pretending to perform speaker diarization.

    Candidates for the same acoustic fingerprint are ranked by wake confidence,
    estimated SNR and arrival time. The Hub can issue the returned lease id and
    suppress the other devices for the same event.
    """

    now_value = float(now_ms if now_ms is not None else time.time() * 1000.0)
    admitted: list[dict[str, Any]] = []
    for raw in candidates:
        item = _mapping(raw)
        observed = float(item.get("observed_at_ms") or now_value)
        if abs(now_value - observed) > max(50, int(window_ms)):
            continue
        if not _text(item.get("device_id")):
            continue
        admitted.append(item)
    if not admitted:
        return {"ok": False, "state": "no_candidate", "winner": None, "suppressed": []}
    ranked = sorted(
        admitted,
        key=lambda item: (
            -float(item.get("activation_confidence") or 0.0),
            -float(item.get("snr_db") or -100.0),
            float(item.get("observed_at_ms") or now_value),
            _text(item.get("device_id")),
        ),
    )
    winner = ranked[0]
    fingerprint = _text(winner.get("audio_fingerprint")) or _text(winner.get("capture_id"))
    seed = f"{fingerprint}:{winner.get('device_id')}:{winner.get('observed_at_ms')}"
    return {
        "ok": True,
        "state": "leased",
        "lease_id": "voice-lease:" + hashlib.sha256(seed.encode()).hexdigest()[:16],
        "winner": winner,
        "suppressed": [_text(item.get("device_id")) for item in ranked[1:]],
        "window_ms": max(50, int(window_ms)),
    }


class VoiceActivationArbiter:
    """Collect concurrent room activation claims and issue one short lease.

    The collector uses Hub arrival time for its window so endpoints with skewed
    clocks cannot accidentally escape arbitration.  ``audio_fingerprint`` is
    supplied by native capture when available; transcript-only clients use a
    normalized phrase fingerprint and expose that limitation in diagnostics.
    """

    def __init__(self, *, window_ms: int = 280, lease_ms: int = 2_500) -> None:
        self.window_ms = max(50, int(window_ms))
        self.lease_ms = max(self.window_ms, int(lease_ms))
        self._condition = threading.Condition(threading.RLock())
        self._groups: dict[str, dict[str, Any]] = {}
        self._claims_total = 0
        self._leases_total = 0
        self._suppressed_total = 0
        self._last_result: dict[str, Any] | None = None

    @staticmethod
    def _group_key(candidate: Mapping[str, Any]) -> str:
        room_id = _text(candidate.get("room_id")) or "local"
        fingerprint = (
            _text(candidate.get("audio_fingerprint"))
            or _text(candidate.get("phrase_fingerprint"))
            or _text(candidate.get("capture_id"))
        )
        if not fingerprint:
            raise ValueError("voice_activation_fingerprint_required")
        return f"{room_id}\0{fingerprint}"

    @staticmethod
    def phrase_fingerprint(text: Any) -> str:
        normalized = " ".join(_text(text).casefold().split())
        if not normalized:
            raise ValueError("voice_activation_phrase_required")
        return "phrase:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def claim(self, candidate: Mapping[str, Any], *, window_ms: int | None = None) -> dict[str, Any]:
        item = _mapping(candidate)
        device_id = _text(item.get("device_id"))
        if not device_id:
            raise ValueError("voice_activation_device_required")
        if not (
            _text(item.get("audio_fingerprint"))
            or _text(item.get("phrase_fingerprint"))
            or _text(item.get("capture_id"))
        ):
            item["phrase_fingerprint"] = self.phrase_fingerprint(item.get("text"))
        group_key = self._group_key(item)
        bounded_window = max(50, min(1_000, int(window_ms or self.window_ms)))
        received_wall_ms = time.time() * 1000.0
        received_mono = time.monotonic()
        item["endpoint_observed_at_ms"] = item.get("observed_at_ms")
        item["observed_at_ms"] = received_wall_ms
        item["hub_received_at_ms"] = received_wall_ms
        item.setdefault("capture_id", f"capture:{uuid.uuid4().hex[:16]}")

        with self._condition:
            self._expire_unlocked(received_mono)
            group = self._groups.get(group_key)
            if group is not None and group.get("result") is not None:
                return self._response_unlocked(group, device_id)
            if group is None:
                group = {
                    "key": group_key,
                    "created_mono": received_mono,
                    "deadline_mono": received_mono + bounded_window / 1000.0,
                    "expires_mono": received_mono + self.lease_ms / 1000.0,
                    "window_ms": bounded_window,
                    "candidates": {},
                    "result": None,
                    "winner_delivered": False,
                }
                self._groups[group_key] = group
            candidates = group["candidates"]
            previous = candidates.get(device_id)
            if previous is None or (
                float(item.get("activation_confidence") or 0.0),
                float(item.get("snr_db") or -100.0),
            ) > (
                float(previous.get("activation_confidence") or 0.0),
                float(previous.get("snr_db") or -100.0),
            ):
                candidates[device_id] = item
            self._claims_total += 1
            self._condition.notify_all()

            while group.get("result") is None:
                remaining = float(group["deadline_mono"]) - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=remaining)
                    continue
                collected = list(group["candidates"].values())
                result = choose_activation_candidate(
                    collected,
                    # The collection deadline has already enforced the Hub
                    # window. Rank against the latest arrival rather than the
                    # scheduler wake-up time, which may be a few ms late.
                    now_ms=max(float(value.get("observed_at_ms") or 0.0) for value in collected),
                    window_ms=int(group["window_ms"]),
                )
                result["schema_version"] = "voice-activation-lease.v1"
                result["group_key_hash"] = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:16]
                result["candidate_count"] = len(group["candidates"])
                result["lease_ms"] = self.lease_ms
                group["result"] = result
                self._leases_total += 1
                self._suppressed_total += len(result.get("suppressed") or [])
                self._last_result = dict(result)
                self._condition.notify_all()
            return self._response_unlocked(group, device_id)

    def _response_unlocked(self, group: Mapping[str, Any], device_id: str) -> dict[str, Any]:
        result = dict(group.get("result") or {})
        winner = _mapping(result.get("winner"))
        is_winner = _text(winner.get("device_id")) == device_id
        delivered = bool(group.get("winner_delivered"))
        admitted = is_winner and not delivered
        if admitted:
            group["winner_delivered"] = True
        return {
            **result,
            "admitted": admitted,
            "state": "winner" if admitted else "suppressed" if not is_winner else "lease_already_delivered",
            "winner_device_id": _text(winner.get("device_id")) or None,
            "request_device_id": device_id,
        }

    def _expire_unlocked(self, now_mono: float) -> None:
        expired = [key for key, group in self._groups.items() if float(group["expires_mono"]) <= now_mono]
        for key in expired:
            self._groups.pop(key, None)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            self._expire_unlocked(time.monotonic())
            return {
                "schema_version": "voice-activation-arbiter.v1",
                "state": "ready",
                "window_ms": self.window_ms,
                "lease_ms": self.lease_ms,
                "active_groups": len(self._groups),
                "claims_total": self._claims_total,
                "leases_total": self._leases_total,
                "suppressed_total": self._suppressed_total,
                "last_result": dict(self._last_result or {}),
            }


def get_voice_activation_arbiter() -> VoiceActivationArbiter:
    global _ACTIVATION_ARBITER
    with _ACTIVATION_ARBITER_LOCK:
        if _ACTIVATION_ARBITER is None:
            _ACTIVATION_ARBITER = VoiceActivationArbiter()
        return _ACTIVATION_ARBITER


def claim_voice_activation(candidate: Mapping[str, Any], *, window_ms: int | None = None) -> dict[str, Any]:
    return get_voice_activation_arbiter().claim(candidate, window_ms=window_ms)


def audio_processing_report(
    *,
    capture_id: str,
    device_id: str,
    aec_available: bool,
    aec_enabled: bool,
    echo_reference_id: str = "",
    noise_suppression: bool | None = None,
    automatic_gain_control: bool | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "voice-audio-processing.v1",
        "capture_id": _text(capture_id),
        "device_id": _text(device_id),
        "aec": {
            "available": bool(aec_available),
            "enabled": bool(aec_enabled and aec_available),
            "state": "active" if aec_enabled and aec_available else "unavailable" if not aec_available else "disabled",
        },
        "echo_reference": {
            "id": _text(echo_reference_id) or None,
            "present": bool(_text(echo_reference_id)),
        },
        "noise_suppression": noise_suppression,
        "automatic_gain_control": automatic_gain_control,
        "updated_at": _now(),
    }


__all__ = [
    "LISTENING_MODES",
    "LONG_FORM_START_INTENT",
    "LONG_FORM_NOTE_START_INTENT",
    "LONG_FORM_DIALOG_START_INTENT",
    "LONG_FORM_START_INTENTS",
    "LONG_FORM_STOP_INTENT",
    "advance_long_form_session",
    "advance_persisted_long_form_session",
    "audio_processing_report",
    "claim_voice_activation",
    "choose_activation_candidate",
    "default_voice_policy",
    "get_voice_activation_arbiter",
    "listening_service_projection",
    "long_form_scope_key",
    "long_form_sessions_path",
    "new_long_form_state",
    "normalize_listening_mode",
    "read_voice_policy",
    "read_long_form_session",
    "set_voice_policy",
    "voice_policy_path",
    "VoiceActivationArbiter",
]
