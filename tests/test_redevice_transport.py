from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaos.sdk.redevice import choose_endpoint, current_endpoint_records, select_transport, with_local_content_route


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


def test_redevice_display_transport_prefers_command_local_content_route() -> None:
    endpoint = {
        "code": "ABC123",
        "endpoint_manifest": {"endpoint_id": "endpoint-1"},
    }

    selected = select_transport(with_local_content_route(endpoint), intent="display.slideshow", content_bytes=20_000)

    assert selected["selected_transport"] == "local_http"
    assert selected["content"]["direction"] == "content_in"
    assert selected["content"]["transport"] == "local_http"
    assert selected["requires_root_relay"] is False
    assert selected["degraded"] is False


def test_choose_endpoint_heals_stale_pair_code_for_same_endpoint() -> None:
    endpoints = [
        {
            "state": "consumed",
            "code": "OLD",
            "endpoint_id": "redevice-1",
            "last_seen_at": 1,
        },
        {
            "state": "consumed",
            "code": "NEW",
            "endpoint_id": "redevice-1",
            "last_seen_at": 1_000_000_000_000,
        },
    ]

    selected = choose_endpoint(endpoints, "OLD")

    assert selected is not None
    assert selected["code"] == "NEW"


def test_current_endpoint_records_keeps_current_identity_and_history() -> None:
    endpoints = [
        {
            "state": "revoked",
            "code": "OLD",
            "endpoint_id": "redevice-1",
            "last_seen_at": 10,
        },
        {
            "state": "consumed",
            "code": "NEW",
            "endpoint_id": "redevice-1",
            "last_seen_at": 1_000_000_000_000,
        },
    ]

    records = current_endpoint_records(endpoints)

    assert len(records) == 1
    assert records[0]["code"] == "NEW"
    assert records[0]["admission_history"][0]["code"] == "OLD"
    assert records[0]["admission_history"][0]["state"] == "revoked"
