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


def test_build_capture_command_accepts_vad_activation_overrides() -> None:
    endpoint = {
        "code": "ABC123",
        "endpoint_manifest": {
            "endpoint_id": "endpoint-1",
            "services": {"audio_input_endpoint": {"enabled": True}},
        },
    }

    command = endpoint_audio.build_capture_command(
        endpoint,
        code="ABC123",
        mode="vad",
        activation={"min_rms": "1200", "silence_ms": 1100, "pre_roll_ms": 500, "min_segment_ms": 900},
    )

    activation = command["payload"]["input_policy"]["activation"]
    assert activation["min_rms"] == 1200
    assert activation["silence_ms"] == 1100
    assert activation["pre_roll_ms"] == 500
    assert activation["min_segment_ms"] == 900


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


def test_verify_audio_input_content_parses_latest_segment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR", str(tmp_path))
    event = {
        "type": "endpoint.audio.segment",
        "endpoint_id": "endpoint-1",
        "session_id": "audio-session",
        "command_id": "cmd:1",
        "action": "voice_activity.ended",
        "audio": {
            "mime": "audio/wav",
            "data_b64": base64.b64encode(_wav_bytes(7)).decode("ascii"),
            "bytes": len(_wav_bytes(7)),
        },
        "vad": {"duration_ms": 1000},
    }
    segment = endpoint_audio.save_audio_segment(event)
    state = {"last_segment": segment}

    check = endpoint_audio.verify_audio_input_content(
        state,
        {"endpoint_manifest": {"services": {"audio_input_endpoint": {"enabled": True}}}},
    )

    assert check["ok"] is True
    assert check["state"] == "ready"
    assert check["sample_rate"] == 16000
    assert check["channels"] == 1
    assert check["policy"]["microphone_allowed"] is True


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
    assert state["vad"]["state"] == "listening"


def test_diagnostics_snapshot_reports_vad_stt_and_retention(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR", str(tmp_path))
    state: dict[str, object] = {
        "vad": {"state": "recording", "noise_floor_rms": 120, "threshold_rms": 650},
        "stt": {"ok": True, "state": "recognized", "text": "test"},
        "events": [{"id": "evt-1"}],
    }

    snapshot = endpoint_audio.diagnostics_snapshot(state, {"endpoint_manifest": {"trust_level": "limited"}})

    assert snapshot["schema_version"] == "endpoint-audio-diagnostics.v1"
    assert snapshot["vad"]["state"] == "recording"
    assert snapshot["stt"]["text"] == "test"
    assert snapshot["retention"]["debug_clip_limit"] == 10
    assert snapshot["policy"]["trust_level"] == "limited"


def test_readiness_report_is_compact_and_ready_for_allowed_microphone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ENDPOINT_AUDIO_STATE_DIR", str(tmp_path))
    event = {
        "type": "endpoint.audio.segment",
        "endpoint_id": "endpoint-1",
        "session_id": "audio-session",
        "command_id": "cmd:readiness",
        "action": "voice_activity.ended",
        "audio": {
            "mime": "audio/wav",
            "data_b64": base64.b64encode(_wav_bytes(9)).decode("ascii"),
            "bytes": len(_wav_bytes(9)),
        },
        "vad": {"duration_ms": 900},
    }
    segment = endpoint_audio.save_audio_segment(event)
    state = {
        "vad": {"state": "sent", "updated_at": "2026-06-23T10:00:00+00:00"},
        "last_segment": segment,
        "retention": endpoint_audio.retention_report(),
    }

    report = endpoint_audio.readiness_report(
        state,
        {"endpoint_manifest": {"services": {"audio_input_endpoint": {"enabled": True}}}},
    )

    assert report["schema_version"] == "endpoint-audio-readiness.v1"
    assert report["ok"] is True
    assert report["state"] == "ready"
    assert report["policy"]["microphone_allowed"] is True
    assert report["retention"]["stored_debug_clips"] == 1
    assert "clips" not in report["retention"]
    assert "path" not in report["last_segment"]


def test_readiness_report_blocks_when_microphone_policy_is_missing() -> None:
    report = endpoint_audio.readiness_report(
        {"vad": {"state": "idle"}},
        {"endpoint_manifest": {"trust_level": "limited"}},
    )

    assert report["ok"] is False
    assert report["state"] == "policy_blocked"
    assert report["policy"]["microphone_allowed"] is False
