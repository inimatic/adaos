"""Persisted voice-listening policy shared with the Android foreground service."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_MODES = {"off", "push_to_talk", "continuous", "activation"}
_STT_MODES = {"system", "vosk", "auto"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mode(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    token = {"ptt": "push_to_talk", "wake": "activation", "wake_word": "activation"}.get(token, token)
    if token not in _MODES:
        raise ValueError("voice_listening_mode_invalid")
    return token


def _stt_mode(value: Any) -> str:
    token = str(value or "system").strip().lower().replace("-", "_")
    token = {"native": "system", "on_device": "system", "automatic": "auto"}.get(token, token)
    if token not in _STT_MODES:
        raise ValueError("voice_stt_provider_mode_invalid")
    return token


class AndroidVoicePolicyStore:
    def __init__(self, data_root: str | Path) -> None:
        self.path = Path(data_root) / "voice-listening-policy.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.default(), ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    def default(self) -> dict[str, Any]:
        return {
            "schema_version": "voice-listening-policy.v1",
            "listening_mode": "activation",
            "native_detector_enabled": True,
            "activation": {
                "detector": "transcript_address",
                "native_detector": "android_on_device_speech",
                "prefer_on_device": True,
                "language": "ru-RU",
                "barge_in_enabled": True,
                "require_address": True,
                "aliases_source": "dialog_agent_registry",
            },
            "stt": {
                "provider_mode": "system",
                "active_provider": "system",
                "migration_mode": "observe_then_verified_vosk",
                "language": "ru-RU",
                "selected_model_id": "",
                "fallback_provider": "system",
            },
            "audio_processing": {
                "aec": "android_acoustic_echo_canceler",
                "noise_suppression": True,
                "automatic_gain_control": True,
                "echo_reference": True,
            },
            "speaker_separation": {
                "mode": "metadata_only",
                "enrollment_required": False,
            },
            "room_arbitration": {"enabled": True, "window_ms": 280},
            "updated_at": _now(),
        }

    def read(self) -> dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                raw = {}
        policy = self.default()
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in {"activation", "stt", "audio_processing", "speaker_separation", "room_arbitration"} and isinstance(value, dict):
                    policy[key] = {**dict(policy.get(key) or {}), **value}
                else:
                    policy[key] = value
        try:
            policy["listening_mode"] = _mode(policy.get("listening_mode"))
        except ValueError:
            policy["listening_mode"] = "activation"
        policy["native_detector_enabled"] = policy.get("native_detector_enabled") is True
        try:
            policy["stt"]["provider_mode"] = _stt_mode(policy["stt"].get("provider_mode"))
        except ValueError:
            policy["stt"]["provider_mode"] = "system"
        if policy["stt"]["provider_mode"] == "system":
            policy["stt"]["active_provider"] = "system"
        return policy

    def configure(self, payload: dict[str, Any], *, source: str) -> dict[str, Any]:
        with self._lock:
            policy = self.read()
            policy["listening_mode"] = _mode(payload.get("listening_mode") or payload.get("mode"))
            if "native_detector_enabled" in payload:
                policy["native_detector_enabled"] = payload.get("native_detector_enabled") is True
            for key in ("activation", "stt", "audio_processing", "speaker_separation", "room_arbitration"):
                value = payload.get(key)
                if isinstance(value, dict):
                    policy[key] = {**dict(policy.get(key) or {}), **value}
            policy["stt"]["provider_mode"] = _stt_mode(policy["stt"].get("provider_mode"))
            if policy["stt"]["provider_mode"] == "system":
                policy["stt"]["active_provider"] = "system"
            policy["source"] = str(source or "device_registry")
            policy["updated_at"] = _now()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
            return policy

    def service(self) -> dict[str, Any]:
        policy = self.read()
        return {
            "contract": "node.voice.listening.v1",
            "state": "disabled" if policy["listening_mode"] == "off" else "ready",
            "listening_mode": policy["listening_mode"],
            "modes": sorted(_MODES),
            "native_detector_enabled": policy["native_detector_enabled"],
            "activation": dict(policy.get("activation") or {}),
            "stt": dict(policy.get("stt") or {}),
            "audio_processing": dict(policy.get("audio_processing") or {}),
            "speaker_separation": dict(policy.get("speaker_separation") or {}),
            "room_arbitration": dict(policy.get("room_arbitration") or {}),
            "settings": {"tool": "node.voice.configure", "field": "listening_mode"},
            "updated_at": policy.get("updated_at"),
        }


__all__ = ["AndroidVoicePolicyStore"]
