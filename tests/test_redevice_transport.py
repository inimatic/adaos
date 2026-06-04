from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaos.sdk.redevice import select_transport


def test_redevice_audio_capture_transport_uses_audio_in_direction() -> None:
    endpoint = {
        "code": "ABC123",
        "endpoint_manifest": {"endpoint_id": "endpoint-1"},
        "transport_profile": {
            "schema_version": "transport-profile.v1",
            "preferred_order": ["redevice_poll", "segment_upload"],
            "routes": {
                "redevice_poll": {
                    "available": True,
                    "state": "ready",
                    "directions": ["control", "events"],
                    "legacy_safe": True,
                },
                "segment_upload": {
                    "available": True,
                    "state": "ready",
                    "directions": ["audio_in"],
                },
            },
        },
    }

    selected = select_transport(endpoint, intent="audio.capture.ptt")

    assert selected["selected_transport"] == "segment_upload"
    assert selected["content"]["direction"] == "audio_in"
    assert selected["control"]["transport"] == "redevice_poll"


def test_redevice_audio_capture_falls_back_to_control_when_media_unavailable() -> None:
    endpoint = {
        "code": "ABC123",
        "transport_profile": {
            "preferred_order": ["redevice_poll"],
            "routes": {
                "redevice_poll": {
                    "available": True,
                    "state": "ready",
                    "directions": ["control", "events"],
                    "legacy_safe": True,
                }
            },
        },
    }

    selected = select_transport(endpoint, intent="audio.capture.ptt")

    assert selected["selected_transport"] == "redevice_poll"
    assert selected["content"]["direction"] == "audio_in"
    assert selected["content"]["transport"] == "unavailable"
