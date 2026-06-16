from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx


MAX_DEBUG_CLIPS = 10


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _target_lang(lang: str | None) -> str:
    token = _text(lang).lower()
    if token.startswith("en"):
        return "en-us"
    return "ru-ru"


def state_dir() -> Path:
    override = _text(os.getenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR"))
    if override:
        root = Path(override)
    else:
        try:
            root = Path(get_ctx().paths.state_dir()) / "endpoint_audio"
        except Exception:
            root = Path(".adaos") / "state" / "endpoint_audio"
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_dir(lang: str | None) -> Path:
    target = _target_lang(lang)
    try:
        return Path(get_ctx().paths.base_dir()) / "models" / "vosk" / target
    except Exception:
        return Path(".adaos") / "models" / "vosk" / target


def event_id(event: Mapping[str, Any]) -> str:
    raw = json.dumps(
        {
            "type": event.get("type"),
            "action": event.get("action"),
            "endpoint_id": event.get("endpoint_id"),
            "session_id": event.get("session_id"),
            "command_id": event.get("command_id"),
            "record_button": event.get("record_button"),
            "vad": event.get("vad"),
            "audio_bytes": _mapping(event.get("audio")).get("bytes"),
            "transcript": _mapping(event.get("transcript")).get("text"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]


def stt_status(lang: str | None = None) -> dict[str, Any]:
    target = _target_lang(lang)
    path = model_dir(target)
    try:
        import vosk  # type: ignore  # noqa: F401
    except Exception as exc:
        return {"available": False, "state": "vosk_unavailable", "target": target, "detail": str(exc)}
    if not path.exists() or not any(path.iterdir()):
        return {"available": False, "state": "model_missing", "target": target, "model_dir": str(path)}
    return {"available": True, "state": "ready", "target": target, "model_dir": str(path)}


def transcribe_wav(path: str | Path, *, lang: str | None = None) -> dict[str, Any]:
    status = stt_status(lang)
    if not status.get("available"):
        return {"ok": False, **status}
    try:
        import vosk  # type: ignore

        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2:
                return {"ok": False, "state": "unsupported_wav", "detail": f"sampwidth={wf.getsampwidth()}"}
            frames = wf.readframes(wf.getnframes())
            rate = wf.getframerate()
        model = vosk.Model(str(status["model_dir"]))
        rec = vosk.KaldiRecognizer(model, rate)
        rec.SetWords(False)
        rec.AcceptWaveform(frames)
        result = json.loads(rec.FinalResult() or "{}")
        text = _text(result.get("text"))
        return {"ok": True, **status, "text": text, "raw": result}
    except Exception as exc:
        return {"ok": False, **status, "state": "transcribe_failed", "detail": str(exc)}


def dispatch_transcript(
    text: str,
    *,
    event: Mapping[str, Any],
    webspace_id: str | None = None,
    source: str = "endpoint_audio_service",
) -> dict[str, Any]:
    token = _text(text)
    if not token:
        return {"ok": False, "state": "empty_transcript"}
    request_id = f"endpoint_audio:{event_id(event)}"
    try:
        from adaos.services.eventbus import emit as bus_emit
        from adaos.services.yjs.webspace import default_webspace_id

        payload = {
            "text": token,
            "utterance": token,
            "webspace_id": webspace_id or default_webspace_id(),
            "request_id": request_id,
            "_meta": {
                "route_id": "voice_chat",
                "source": source,
                "endpoint_id": event.get("endpoint_id"),
                "session_id": event.get("session_id"),
                "surface_id": event.get("surface_id"),
                "audio_event_id": event_id(event),
            },
        }
        bus_emit(get_ctx().bus, "nlp.intent.detect.rasa", payload, source=source)
        return {"ok": True, "state": "dispatched", "request_id": request_id}
    except Exception as exc:
        return {"ok": False, "state": "dispatch_failed", "detail": str(exc), "request_id": request_id}


def _debug_audio_dir() -> Path:
    path = state_dir() / "debug_audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_debug_audio(limit: int = MAX_DEBUG_CLIPS) -> None:
    root = _debug_audio_dir()
    clips = sorted(root.glob("*.wav"), key=lambda item: item.stat().st_mtime if item.exists() else 0.0)
    for clip in clips[:-max(1, int(limit or 1))]:
        try:
            clip.unlink()
        except Exception:
            pass
        sidecar = clip.with_suffix(".json")
        try:
            if sidecar.exists():
                sidecar.unlink()
        except Exception:
            pass


def save_audio_segment(event: Mapping[str, Any], *, retain_debug_clips: int = MAX_DEBUG_CLIPS) -> dict[str, Any]:
    audio = _mapping(event.get("audio"))
    data = _text(audio.get("data_b64"))
    if not data:
        return {"ok": False, "state": "missing_audio"}
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception as exc:
        return {"ok": False, "state": "invalid_audio_base64", "detail": str(exc)}
    token = event_id(event)
    path = _debug_audio_dir() / f"{token}.wav"
    path.write_bytes(raw)
    meta = {
        "event_id": token,
        "event_type": _text(event.get("type")),
        "endpoint_id": _text(event.get("endpoint_id")),
        "session_id": _text(event.get("session_id")),
        "command_id": _text(event.get("command_id")),
        "mime": _text(audio.get("mime")) or "audio/wav",
        "bytes": len(raw),
        "duration_ms": _mapping(event.get("record_button")).get("duration_ms") or _mapping(event.get("vad")).get("duration_ms"),
        "updated_at": _now(),
    }
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_debug_audio(retain_debug_clips)
    return {"ok": True, "path": str(path), **meta}


def policy_report(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _mapping(endpoint.get("endpoint_manifest"))
    policy = _mapping(endpoint.get("endpoint_policy"))
    services = _mapping(manifest.get("services"))
    audio = _mapping(services.get("audio_input_endpoint"))
    capabilities = _mapping(manifest.get("capabilities"))
    mic = _mapping(capabilities.get("audio.input"))
    enabled = bool(audio.get("enabled") or mic.get("available"))
    trust = _text(policy.get("trust_level") or manifest.get("trust_level")) or "limited"
    return {
        "microphone_allowed": enabled,
        "trust_level": trust,
        "service_enabled": bool(audio.get("enabled")) or enabled,
        "mic_available": bool(mic.get("available") or enabled),
        "capture": "voice_activity",
        "local_stt": False,
        "local_tts": False,
        "retention": {"debug_clip_limit": MAX_DEBUG_CLIPS},
    }


def compact_endpoint(endpoint: Mapping[str, Any], *, selected_code: str = "") -> dict[str, Any]:
    from adaos.sdk.redevice import compact_endpoint as sdk_compact_endpoint
    from adaos.sdk.redevice import select_transport

    compact = sdk_compact_endpoint(endpoint, selected_codes={selected_code} if selected_code else set())
    policy = policy_report(endpoint)
    return {
        "id": compact.get("id"),
        "code": compact.get("code"),
        "title": compact.get("title"),
        "subtitle": f"{compact.get('online_state')} | mic={policy['microphone_allowed']} | trust={policy['trust_level']}",
        "online_state": compact.get("online_state"),
        "online": compact.get("online"),
        "selected": compact.get("selected"),
        "selected_label": compact.get("selected_label"),
        "last_seen": compact.get("last_seen"),
        "endpoint_id": compact.get("endpoint_id"),
        "active_app": compact.get("active_app"),
        "active_surface": compact.get("active_surface"),
        "policy": policy,
        "transport": select_transport(endpoint, intent="audio.capture.vad", allow_root_relay=True),
    }


def process_endpoint_event(
    state: dict[str, Any],
    endpoint: Mapping[str, Any],
    *,
    webspace_id: str | None = None,
    source: str = "endpoint_audio_service",
    dispatch: bool = True,
) -> dict[str, Any] | None:
    event = _mapping(endpoint.get("last_event"))
    if not event:
        return None
    event_type = _text(event.get("type"))
    if event_type not in {
        "endpoint.audio.segment",
        "endpoint.audio.transcript",
        "endpoint.audio.record_button",
        "endpoint.audio.voice_activity",
    }:
        return None
    token = event_id(event)
    if _text(state.get("last_event_id")) == token:
        return None
    state["last_event_id"] = token
    compact = {
        "id": token,
        "schema_version": "speech-event.v1",
        "type": event_type,
        "action": _text(event.get("action")),
        "endpoint_id": _text(event.get("endpoint_id")),
        "session_id": _text(event.get("session_id")),
        "command_id": _text(event.get("command_id")),
        "duration_ms": _mapping(event.get("record_button")).get("duration_ms") or _mapping(event.get("vad")).get("duration_ms"),
        "audio_bytes": _mapping(event.get("audio")).get("bytes"),
        "vad": _mapping(event.get("vad")),
        "updated_at": _now(),
    }
    result: dict[str, Any] = {"ok": True, "event": compact}
    if event_type == "endpoint.audio.segment":
        segment = save_audio_segment(event)
        result["segment"] = segment
        state["last_segment"] = segment
        if segment.get("ok"):
            stt = transcribe_wav(Path(str(segment["path"])), lang=_text(event.get("lang")) or "ru")
            result["stt"] = stt
            state["stt"] = stt
            if dispatch and stt.get("ok") and _text(stt.get("text")):
                result["dispatch"] = dispatch_transcript(_text(stt.get("text")), webspace_id=webspace_id, event=event, source=source)
    elif event_type == "endpoint.audio.transcript":
        transcript = _mapping(event.get("transcript"))
        text = _text(transcript.get("text"))
        stt = {
            "ok": bool(text),
            "state": "recognized" if text else "empty_transcript",
            "source": _text(transcript.get("source")) or "endpoint_local",
            "text": text,
            "raw": transcript,
        }
        result["stt"] = stt
        state["stt"] = stt
        if dispatch and text:
            result["dispatch"] = dispatch_transcript(text, webspace_id=webspace_id, event=event, source=source)
    events = list(state.get("events") or [])
    events.append({**compact, "result": {key: value for key, value in result.items() if key != "event"}})
    state["events"] = events[-24:]
    return result


def build_capture_command(
    endpoint: Mapping[str, Any],
    *,
    code: str,
    mode: str = "vad",
    lang: str | None = "ru",
    max_duration_ms: int = 8000,
    owner_node_id: str = "member",
    owner_skill_id: str = "redevice_voice",
) -> dict[str, Any]:
    from adaos.sdk.redevice import select_transport

    capture_mode = _text(mode).lower()
    vad = capture_mode in {"vad", "voice_activity", "voice_activation", "sound_activation"}
    command_id = "cmd:voice:" + hashlib.sha256(f"{code}:{capture_mode}:{time.time()}".encode("utf-8")).hexdigest()[:16]
    session_id = "audio:" + command_id.split(":")[-1]
    command_type = "audio.capture.vad" if vad else "audio.capture.ptt"
    transport = select_transport(endpoint, intent=command_type, allow_root_relay=True)
    duration = max(1000, min(12000, int(max_duration_ms or 8000)))
    policy = policy_report(endpoint)
    return {
        "command_id": command_id,
        "type": command_type,
        "owner": {"node_id": owner_node_id, "skill_id": owner_skill_id},
        "payload": {
            "schema_version": "audio-session.v1",
            "surface_id": f"surface:voice:{command_id.split(':')[-1]}",
            "surface_ref": "voice.vad" if vad else "voice.ptt",
            "session_id": session_id,
            "mode": "command",
            "title": "Voice Endpoint",
            "body": (
                "Voice activation is armed. Speak near this device."
                if vad
                else "Hold the button while speaking. Audio is captured only while held."
            ),
            "lang": _target_lang(lang),
            "max_duration_ms": duration,
            "active_app": {"app_id": owner_skill_id, "skill_id": owner_skill_id, "label": "ReDevice Voice"},
            "input_policy": {
                "microphone_required": True,
                "capture": "voice_activity" if vad else "only_while_button_held",
                "activation": {
                    "strategy": "vad" if vad else "ptt",
                    "min_segment_ms": 700,
                    "silence_ms": 900,
                    "pre_roll_ms": 700,
                    "min_rms": 650,
                },
                "local_stt": False,
                "local_tts": False,
                "retention": {"debug_clip_limit": MAX_DEBUG_CLIPS},
            },
            "endpoint_policy_check": policy,
            "transport": transport,
        },
    }


__all__ = [
    "MAX_DEBUG_CLIPS",
    "build_capture_command",
    "compact_endpoint",
    "dispatch_transcript",
    "event_id",
    "policy_report",
    "process_endpoint_event",
    "save_audio_segment",
    "state_dir",
    "stt_status",
    "transcribe_wav",
]
