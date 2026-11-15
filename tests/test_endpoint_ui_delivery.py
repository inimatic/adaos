"""Delivery export keeps node effects private and preserves the event echo token."""
import copy
import json
from pathlib import Path
import subprocess
import sys

from adaos.sdk.developer import ui
from adaos.services.endpoint_ui import endpoint_ui_delivery, project_endpoint_ui, resolve_endpoint_ui_action

FIXTURES = Path(__file__).parent / "fixtures/endpoint_ui"
NOW = 2000000000


def prepared():
    return project_endpoint_ui(json.loads((FIXTURES / "media_center_controls.webui.json").read_text()),
        **json.loads((FIXTURES / "media_center_controls.context.json").read_text()), now=NOW, expires_at=NOW + 120)


def test_export_is_an_independent_receiver_only_snapshot():
    source = prepared()
    result = ui.endpoint_delivery(source, now=NOW)
    assert result["ok"]
    wire = result["delivery"]
    assert set(wire) == {"schema", "projection", "projection_digest"}
    assert wire["projection_digest"] == source["receipt"]["projection_digest"]
    assert "voice_command" not in json.dumps(wire) and "node_bindings" not in wire
    wire["projection"]["widgets"].clear()
    assert source["projection"]["widgets"]


def test_export_refuses_changed_receipts_bindings_and_expiry():
    original = prepared()
    for key in ("projection", "node_bindings"):
        source = copy.deepcopy(original)
        source[key].clear()
        assert not endpoint_ui_delivery(source, now=NOW)["ok"]
    for now in (NOW + 120, NOW + 121, NOW - 400, float("nan"), True):
        assert not endpoint_ui_delivery(original, now=now)["ok"]
    for source in ({}, {"ok": False}, None, [], {"ok": True, "projection": None}):
        assert not endpoint_ui_delivery(source, now=NOW)["ok"]


def test_delivery_digest_resolves_the_original_next_action():
    source = prepared()
    wire = endpoint_ui_delivery(source, now=NOW)["delivery"]
    event = {"schema": "adaos.ui.endpoint_action.v1", "event_id": "tap:123",
             **{key: value for key, value in wire["projection"]["scope"].items() if key != "expires_at"},
             "projection_digest": wire["projection_digest"],
             "action_ref": wire["projection"]["widgets"][0]["inputs"]["buttons"][2]["action_ref"]}
    result = resolve_endpoint_ui_action(source, event, authenticated_endpoint_id="fixture:ipad",
        current_state_revision="state:7", current_generation=3, now=NOW + 1)
    assert result["ok"]
    assert result["action"]["params"] == {"action": "next", "profile_id": "family", "target_id": "target:kitchen"}


def test_cli_exports_delivery_and_replaces_it_on_refusal(tmp_path):
    output, delivery = tmp_path / "node.json", tmp_path / "delivery.json"
    command = [sys.executable, "-B", "tools/project_endpoint_ui.py", "prepare", "--source",
        str(FIXTURES / "media_center_controls.webui.json"), "--context",
        str(FIXTURES / "media_center_controls.context.json"), "--output", str(output), "--delivery-output", str(delivery)]
    good = subprocess.run(command, capture_output=True, timeout=30)
    assert good.returncode == 0, good.stderr
    assert set(json.loads(delivery.read_text())) == {"schema", "projection", "projection_digest"}
    assert delivery.read_text().count("\n") == 1  # Compact wire encoding; indentation is not part of the profile budget.
    bad = subprocess.run(command + ["--ttl", "999"], capture_output=True, timeout=30)
    assert bad.returncode == 1
    assert json.loads(delivery.read_text())["ok"] is False
    assert "projection" not in json.loads(delivery.read_text())
    collision = command[:-1] + [str(output)]
    refused = subprocess.run(collision, capture_output=True, timeout=30)
    assert refused.returncode == 2
    assert b"must be distinct" in refused.stderr
