from __future__ import annotations

from adaos.services import endpoint_router


def test_endpoint_command_envelope_infers_display_service() -> None:
    envelope = endpoint_router.build_endpoint_command(
        {"type": "display.slideshow", "items": []},
        endpoint={
            "identity": {"endpoint_id": "endpoint-1", "pair_code": "ABCD1234"},
            "policy": {"trust_level": "limited"},
        },
        device_ref="redevice:endpoint-1",
        requested_by={"node_id": "hub-1", "skill_id": "slideshow_skill"},
        transport={"selected_transport": "local_http"},
    )

    assert envelope["schema_version"] == "endpoint-command.v1"
    assert envelope["type"] == "display.slideshow"
    assert envelope["target"] == {
        "kind": "redevice",
        "device_ref": "redevice:endpoint-1",
        "endpoint_id": "endpoint-1",
        "code": "ABCD1234",
        "service": "display_endpoint",
    }
    assert envelope["requested_by"] == {"node_id": "hub-1", "skill_id": "slideshow_skill"}
    assert envelope["evidence"]["policy_present"] is True
    assert envelope["evidence"]["transport"]["selected_transport"] == "local_http"


def test_legacy_payload_preserves_command_id_and_embeds_envelope_summary() -> None:
    envelope = endpoint_router.build_endpoint_command(
        {"type": "audio.capture.vad", "lang": "ru"},
        endpoint={"endpoint_id": "endpoint-1", "code": "ABCD1234"},
    )

    payload = endpoint_router.legacy_payload_from_envelope(envelope)

    assert payload["command_id"] == envelope["command_id"]
    assert payload["endpoint_command"]["schema_version"] == "endpoint-command.v1"
    assert payload["endpoint_command"]["target"]["service"] == "audio_input_endpoint"
