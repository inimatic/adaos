from __future__ import annotations

import base64
import io
import wave

from adaos.services import endpoint_audio


def _wav_bytes(payload_byte: int) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(bytes([payload_byte, 0]) * 160)
    return stream.getvalue()


def test_build_capture_command_defaults_to_vad_without_local_stt() -> None:
    endpoint = {
        "code": "ABC123",
        "endpoint_manifest": {
            "endpoint_id": "endpoint-1",
            "services": {"audio_input_endpoint": {"enabled": True}},
        },
    }

    command = endpoint_audio.build_capture_command(endpoint, code="ABC123", mode="vad", lang="ru")

    assert command["type"] == "audio.capture.vad"
    assert command["owner"]["skill_id"] == "redevice_voice"
    payload = command["payload"]
    assert payload["surface_ref"] == "voice.vad"
    assert payload["input_policy"]["capture"] == "voice_activity"
    assert payload["input_policy"]["local_stt"] is False
    assert payload["transport"]["intent"] == "audio.capture.vad"


def test_audio_segment_retention_keeps_latest_ten(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR", str(tmp_path))

    for idx in range(12):
        event = {
            "type": "endpoint.audio.segment",
            "endpoint_id": "endpoint-1",
            "session_id": "audio-session",
            "command_id": f"cmd:{idx}",
            "action": "voice_activity.ended",
            "audio": {
                "mime": "audio/wav",
                "data_b64": base64.b64encode(_wav_bytes(idx)).decode("ascii"),
                "bytes": len(_wav_bytes(idx)),
            },
            "vad": {"duration_ms": 1000 + idx},
        }
        result = endpoint_audio.save_audio_segment(event)
        assert result["ok"] is True

    clips = sorted((tmp_path / "debug_audio").glob("*.wav"))
    sidecars = sorted((tmp_path / "debug_audio").glob("*.json"))
    assert len(clips) == 10
    assert len(sidecars) == 10


def test_process_voice_activity_event_records_without_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR", str(tmp_path))
    state: dict[str, object] = {}
    endpoint = {
        "last_event": {
            "type": "endpoint.audio.voice_activity",
            "action": "voice_activity.armed",
            "endpoint_id": "endpoint-1",
            "session_id": "audio-session",
            "vad": {"reason": "armed"},
        }
    }

    result = endpoint_audio.process_endpoint_event(state, endpoint, dispatch=False)

    assert result is not None
    assert result["ok"] is True
    assert state["events"][0]["type"] == "endpoint.audio.voice_activity"
    assert state["last_event_id"]
