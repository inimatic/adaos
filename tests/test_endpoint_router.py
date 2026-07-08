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


def test_endpoint_router_sends_redevice_command_with_router_evidence(monkeypatch) -> None:
    from adaos.sdk import redevice as sdk_redevice

    sent: dict[str, object] = {}

    class FakeBridge:
        def __init__(self, timeout=0):
            self.timeout = timeout

        def send_command(self, code, payload):
            sent["code"] = code
            sent["payload"] = payload
            return {"ok": True, "command": {"state": "queued", "command_id": payload["command_id"]}}

    monkeypatch.setattr(sdk_redevice, "ReDeviceBridge", FakeBridge)
    monkeypatch.setattr(sdk_redevice, "endpoint_root_base", lambda endpoint: "")
    monkeypatch.setattr(
        sdk_redevice,
        "select_transport",
        lambda endpoint, **kwargs: {"schema_version": "transport-selection.v1", "selected_transport": "redevice_poll"},
    )

    result = endpoint_router.send_redevice_command(
        {"endpoint_id": "endpoint-1", "code": "ABCD1234"},
        "ABCD1234",
        {"type": "display.show_text", "text": "hello"},
        requested_by={"skill_id": "test_skill"},
    )

    assert result["ok"] is True
    assert result["endpoint_command"]["target"]["service"] == "display_endpoint"
    assert result["endpoint_router"]["route"] == "redevice_compat_command_queue"
    assert sent["code"] == "ABCD1234"
    assert sent["payload"]["endpoint_command"]["requested_by"] == {"skill_id": "test_skill"}


def test_build_media_session_defaults_to_endpoint_pull() -> None:
    session = endpoint_router.build_media_session(
        endpoint={"endpoint_id": "endpoint-1", "code": "ABCD1234"},
        code="ABCD1234",
        owner={"skill_id": "slideshow_skill"},
        intent="display.slideshow",
        item_count=10,
    )

    assert session["schema_version"] == "endpoint-media-session.v1"
    assert session["primary_transport"] == "endpoint_media_pull"
    assert session["inline_fallback"] is False
    assert session["target"]["device_ref"] == "redevice:endpoint-1"
