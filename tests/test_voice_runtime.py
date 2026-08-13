from __future__ import annotations

import threading

from adaos.services import voice_runtime


def test_voice_policy_round_trip_and_public_projection(tmp_path, monkeypatch) -> None:
    path = tmp_path / "voice-policy.json"
    monkeypatch.setenv("ADAOS_VOICE_POLICY_PATH", str(path))

    policy = voice_runtime.set_voice_policy(listening_mode="wake_word", source="test")

    assert policy["listening_mode"] == "activation"
    assert voice_runtime.read_voice_policy()["source"] == "test"
    projection = voice_runtime.listening_service_projection()
    assert projection["contract"] == "node.voice.listening.v1"
    assert projection["settings"]["tool"] == "node.voice.configure"


def test_stt_provider_policy_starts_with_system_and_is_switchable(tmp_path, monkeypatch) -> None:
    path = tmp_path / "voice-policy.json"
    monkeypatch.setenv("ADAOS_VOICE_POLICY_PATH", str(path))

    initial = voice_runtime.read_voice_policy()
    assert initial["stt"]["provider_mode"] == "system"
    assert initial["stt"]["active_provider"] == "system"

    updated = voice_runtime.set_voice_policy(
        listening_mode="activation",
        source="test",
        updates={"stt": {"provider_mode": "auto", "language": "en-US"}},
    )
    assert updated["stt"]["provider_mode"] == "auto"
    assert voice_runtime.listening_service_projection(updated)["stt"]["language"] == "en-US"


def test_long_form_is_started_by_nlu_and_finished_by_nlu() -> None:
    started = voice_runtime.advance_long_form_session(
        None,
        text="запиши длинную заметку",
        intent_name="voice.long_form.note.start",
        active_agent_id="agent:ada",
    )
    assert started["dispatch"] is False
    assert started["session"]["purpose"] == "note"
    assert started["client_directives"][0]["type"] == "voice.dictation.start"

    appended = voice_runtime.advance_long_form_session(
        started["session"],
        text="Это первая часть заметки",
        intent_name="nlu_fallback",
        active_agent_id="agent:ada",
    )
    completed = voice_runtime.advance_long_form_session(
        appended["session"],
        text="конец записи",
        intent_name="voice.long_form.stop",
        active_agent_id="agent:ada",
    )

    assert completed["completed_text"] == "Это первая часть заметки"
    assert completed["dispatch"] is False
    assert completed["client_directives"][0]["type"] == "voice.dictation.stop"


def test_addressed_command_finishes_dictation_and_is_dispatched() -> None:
    recording = {
        **voice_runtime.new_long_form_state(),
        "state": "recording",
        "segments": ["длинная мысль"],
    }

    result = voice_runtime.advance_long_form_session(
        recording,
        text="какая погода",
        intent_name="weather.current",
        active_agent_id="agent:ada",
        addressed_agent_id="agent:ada",
    )

    assert result["action"] == "completed_and_dispatch"
    assert result["completed_text"] == "длинная мысль"
    assert result["text"] == "какая погода"


def test_long_form_persistence_is_scoped_and_drops_nlu_stop_probe(tmp_path, monkeypatch) -> None:
    path = tmp_path / "long-form.json"
    monkeypatch.setenv("ADAOS_VOICE_LONG_FORM_PATH", str(path))
    scope = voice_runtime.long_form_scope_key(
        "desktop",
        device_id="microphone-1",
    )

    started = voice_runtime.advance_persisted_long_form_session(
        scope,
        text="запиши длинную заметку",
        intent_name="voice.long_form.note.start",
        active_agent_id="agent:ada",
    )
    voice_runtime.advance_persisted_long_form_session(
        scope,
        text="полезный текст",
        intent_name="",
    )
    voice_runtime.advance_persisted_long_form_session(
        scope,
        text="конец записи",
        intent_name="",
    )
    completed = voice_runtime.advance_persisted_long_form_session(
        scope,
        text="конец записи",
        intent_name="voice.long_form.stop",
        drop_trailing_text=True,
    )

    assert started["session"]["state"] == "recording"
    assert completed["completed_text"] == "полезный текст"
    assert voice_runtime.read_long_form_session(scope)["state"] == "completed"


def test_room_arbitration_selects_highest_confidence_then_snr() -> None:
    candidates = [
        {"device_id": "phone", "observed_at_ms": 1_000, "activation_confidence": 0.81, "snr_db": 18},
        {"device_id": "pc", "observed_at_ms": 1_010, "activation_confidence": 0.91, "snr_db": 9},
        {"device_id": "tablet", "observed_at_ms": 1_005, "activation_confidence": 0.91, "snr_db": 14},
    ]

    result = voice_runtime.choose_activation_candidate(candidates, now_ms=1_020)

    assert result["winner"]["device_id"] == "tablet"
    assert set(result["suppressed"]) == {"phone", "pc"}


def test_audio_processing_report_never_claims_unavailable_aec() -> None:
    report = voice_runtime.audio_processing_report(
        capture_id="capture-1",
        device_id="stationary",
        aec_available=False,
        aec_enabled=True,
        echo_reference_id="tts-1",
    )

    assert report["aec"] == {"available": False, "enabled": False, "state": "unavailable"}
    assert report["echo_reference"]["present"] is True


def test_voice_activation_arbiter_collects_one_winner_and_suppresses_other_endpoint() -> None:
    arbiter = voice_runtime.VoiceActivationArbiter(window_ms=60, lease_ms=500)
    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def claim(device_id: str, confidence: float, snr_db: float) -> None:
        barrier.wait()
        results[device_id] = arbiter.claim(
            {
                "room_id": "room-1",
                "phrase_fingerprint": "phrase:shared",
                "capture_id": f"capture:{device_id}",
                "device_id": device_id,
                "activation_confidence": confidence,
                "snr_db": snr_db,
            }
        )

    phone = threading.Thread(target=claim, args=("phone", 0.9, 12.0))
    pc = threading.Thread(target=claim, args=("pc", 0.9, 18.0))
    phone.start()
    pc.start()
    phone.join(timeout=2)
    pc.join(timeout=2)

    assert results["pc"]["admitted"] is True
    assert results["phone"]["admitted"] is False
    assert results["phone"]["winner_device_id"] == "pc"
    assert results["pc"]["candidate_count"] == 2
    snapshot = arbiter.snapshot()
    assert snapshot["claims_total"] == 2
    assert snapshot["leases_total"] == 1
    assert snapshot["suppressed_total"] == 1


def test_voice_activation_arbiter_correlates_transcript_clients_with_distinct_capture_ids() -> None:
    arbiter = voice_runtime.VoiceActivationArbiter(window_ms=60, lease_ms=500)
    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def claim(device_id: str, confidence: float) -> None:
        barrier.wait()
        results[device_id] = arbiter.claim(
            {
                "room_id": "room-1",
                "text": "Ада проверь статус",
                "capture_id": f"capture:{device_id}",
                "device_id": device_id,
                "activation_confidence": confidence,
            }
        )

    phone = threading.Thread(target=claim, args=("phone", 0.8))
    pc = threading.Thread(target=claim, args=("pc", 0.9))
    phone.start()
    pc.start()
    phone.join(timeout=2)
    pc.join(timeout=2)

    assert results["pc"]["admitted"] is True
    assert results["phone"]["admitted"] is False
    assert results["pc"]["candidate_count"] == 2
