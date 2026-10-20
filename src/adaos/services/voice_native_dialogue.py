"""Native endpoints share the core dictation buffer, never dispatching its segments.

Only the events gateway may call this with its authenticated endpoint identity.
Speech text remains on the subnet; endpoint leases exclude a second microphone.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from adaos.services import voice_runtime as runtime
from adaos.services.artifact_pipeline.storage import atomic_write_json, read_atomic_json, mutation_lock

_LOCK = threading.RLock()


def _path() -> Path:
    return runtime.long_form_sessions_path().with_name("native-dialogues.json")


def _save(states: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, states)


def dialogue_command(webspace: str, endpoint: str, payload: dict, *, now: float | None = None) -> dict:
    from adaos.services.router.dialog_registry import published_agent_records

    if not endpoint:
        raise ValueError("voice_endpoint_required")
    action = str(payload.get("action") or "")
    operation = str(payload.get("operation_id") or "")[:160]
    if not operation:
        raise ValueError("voice_operation_required")
    now = time.time() if now is None else now
    key = hashlib.sha256(webspace.encode()).hexdigest()
    with _LOCK, mutation_lock(_path().with_suffix(".lock")):
        try:
            states = read_atomic_json(_path())
        except FileNotFoundError:
            states = {}
        current = dict(states.get(key) or {})
        owner = current.get("endpoint")
        if owner and owner != endpoint and now - current.get("heartbeat", 0) < 45:
            raise ValueError("voice_dialogue_owned_by_another_endpoint")
        if owner and owner != endpoint and action != "start":
            raise ValueError("voice_dialogue_start_required")
        if action != "start" and not current.get("active"):
            raise ValueError("voice_dialogue_start_required")
        if operation in current.get("operations", []):
            return {"mode": current.get("mode", "dialogue"), "duplicate": True}
        target = str(payload.get("target_id") or current.get("target_id") or "")
        records = {str(r.get("id")): r for r in published_agent_records()}
        if target not in records:
            raise ValueError("voice_assistant_unavailable")
        label = str(records[target].get("label") or "Assistant")
        scope = runtime.long_form_scope_key(webspace, device_id="native-dialogue:" + endpoint)
        text = str(payload.get("text") or "").strip()
        if len(text) > 50_000:
            raise ValueError("voice_draft_limit")
        result = {"mode": current.get("mode", "dialogue"), "assistant": label}
        if action == "start":
            if current.get("mode") == "collecting":
                if owner != endpoint or target != current.get("target_id"):
                    raise ValueError("voice_finish_or_cancel_draft_first")
                result["draft"] = runtime.read_long_form_session(scope).get("text", "")
            else:
                current["mode"] = "dialogue"
            current.update(active=True, endpoint=endpoint, target_id=target)
        elif action == "listen":
            if current.get("mode") != "collecting":
                runtime.advance_persisted_long_form_session(scope, text="", intent_name=runtime.LONG_FORM_DIALOG_START_INTENT, active_agent_id=target)
            current["mode"] = "collecting"
        elif action == "append":
            if current.get("mode") != "collecting":
                raise ValueError("voice_not_collecting")
            draft = runtime.read_long_form_session(scope)
            if len(draft.get("text", "")) + len(text) + 1 > 50_000:
                raise ValueError("voice_draft_limit")
            updated = runtime.advance_persisted_long_form_session(scope, text=text, intent_name="")
            result["draft"] = updated["session"]["text"]
        elif action in {"commit", "cancel", "end"}:
            if current.get("mode") == "collecting":
                finished = runtime.advance_persisted_long_form_session(scope, text="", intent_name=runtime.LONG_FORM_STOP_INTENT)
                if action == "commit":
                    text = finished.get("completed_text", "")
            elif action == "commit":
                raise ValueError("voice_not_collecting")
            current["mode"] = "dialogue"
            if action == "end":
                current["active"] = False
                current["endpoint"] = ""
            if action != "commit":
                text = ""
        elif action == "turn":
            if current.get("mode") == "collecting":
                raise ValueError("voice_commit_draft_first")
        elif action in {"heartbeat", "interrupt", "pause"}:
            text = ""
            if action == "pause":
                current["active"] = False
        else:
            raise ValueError("voice_dialogue_action_invalid")
        current["heartbeat"] = now
        current["operations"] = [*current.get("operations", []), operation][-128:]
        result["mode"] = current["mode"] if current.get("active") else ("waiting" if action == "end" else "paused")
        if text and action in {"turn", "commit"}:
            result["dispatch_text"] = label + ", " + text
            result["target_id"] = target
            result["message_id"] = "native-voice:" + hashlib.sha256((endpoint + operation).encode()).hexdigest()[:32]
            result["user_text"] = text
        states[key] = current
        # Webspaces are registered locally, but prevent an unbounded stale cache.
        if len(states) > 256:
            stale = sorted(states, key=lambda k: states[k].get("heartbeat", 0))[:len(states)-256]
            for old in stale:
                if old != key:
                    states.pop(old, None)
        _save(states)
        return result


_STREAMS: dict[tuple[str, str], dict] = {}


def transcribe_stream(endpoint: str, payload: dict) -> dict:
    """Keep one Vosk recognizer across transport chunks; finish only on VAD end."""
    import base64
    import vosk
    from adaos.apps.api import stt_api

    stream_id = str(payload.get("stream_id") or "")[:160]
    audio = str(payload.get("audio_b64") or "")
    if not stream_id or len(audio) > 1024 * 1024:
        raise ValueError("voice_stream_payload_invalid")
    raw = base64.b64decode(audio, validate=True)
    pcm = stt_api._read_wav_mono16k(raw)
    if len(pcm) > 16 * 32000:
        raise ValueError("voice_stream_chunk_too_large")
    language = stt_api._resolve_lang(str(payload.get("lang") or ""))
    with _LOCK:
        now = time.monotonic()
        for key in list(_STREAMS):
            if now - _STREAMS[key]["at"] > 90:
                _STREAMS.pop(key)
        key = (endpoint, stream_id)
        state = _STREAMS.get(key)
        if state is None:
            if len(_STREAMS) >= 16:
                raise ValueError("voice_stream_capacity")
            model = stt_api._load_model(vosk, stt_api._ensure_model(language))
            rec = vosk.KaldiRecognizer(model, 16000)
            rec.SetWords(False)
            state = {"rec": rec, "parts": [], "at": now, "seq": 0, "language": language}
            _STREAMS[key] = state
        sequence = int(payload.get("sequence") or 0)
        if sequence != state["seq"] or language != state["language"]:
            _STREAMS.pop(key, None)
            raise ValueError("voice_stream_sequence_mismatch")
        state["seq"] += 1
        state["at"] = now
        if sum(len(part) for part in state["parts"]) > 50_000:
            _STREAMS.pop(key, None)
            raise ValueError("voice_draft_limit")
        # Small frames preserve Vosk's finalized segments within a long batch.
        for offset in range(0, len(pcm), 8000):
            if state["rec"].AcceptWaveform(pcm[offset:offset+8000]):
                state["parts"].append(json.loads(state["rec"].Result()).get("text", ""))
        if payload.get("more"):
            # Vosk's acoustic endpoint can finish a turn even when the cheap
            # client VAD stays open because AGC/background noise raises RMS.
            completed = " ".join(p for p in state["parts"] if p).strip()
            state["parts"].clear()
            result = {"text": completed, "pending": not bool(completed)}
            if payload.get("include_partial"):
                # Provisional words are for named interruption detection only.
                # They never enter a draft or the conversation as a user turn.
                result["partial"] = str(json.loads(state["rec"].PartialResult()).get("partial") or "")[:8000]
            return result
        state["parts"].append(json.loads(state["rec"].FinalResult()).get("text", ""))
        _STREAMS.pop(key, None)
        return {"text": " ".join(p for p in state["parts"] if p).strip(), "pending": False}
