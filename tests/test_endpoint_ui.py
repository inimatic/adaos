from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

from adaos.services.endpoint_ui import ABI, endpoint_profile, project_endpoint_ui, resolve_endpoint_ui_action

FIXTURE = Path(__file__).parent / "fixtures/endpoint_ui/media_center_controls.webui.json"


def source():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def widget(document):
    return document["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]


def context():
    result = json.loads(FIXTURE.with_name("media_center_controls.context.json").read_text(encoding="utf-8"))
    return {**result, "expires_at": 1120, "now": 1000}


def prepare(document=None, **changes):
    arguments = context()
    arguments.update(changes)
    return project_endpoint_ui(document or source(), **arguments)


def event(prepared, button="next"):
    scope = prepared["projection"]["scope"]
    return {"schema": "adaos.ui.endpoint_action.v1", "event_id": "event:1",
            **{key: scope[key] for key in ("endpoint_id", "surface_id", "generation", "state_revision")},
            "projection_digest": prepared["receipt"]["projection_digest"],
            "action_ref": "action:" + hashlib.sha256(json.dumps(["media-mobile-transport", button], separators=(",", ":")).encode()).hexdigest()}


def resolve(prepared, input_event=None, **changes):
    args = {"authenticated_endpoint_id": "fixture:ipad", "current_state_revision": "state:7", "current_generation": 3, "now": 1010}
    args.update(changes)
    return resolve_endpoint_ui_action(prepared, input_event or event(prepared), **args)


def finding(result, code):
    assert result["ok"] is False
    assert result["findings"][0]["code"] == "endpoint_ui." + code
    assert "projection" not in result and "action" not in result


def test_actual_media_center_controls_lower_without_a_second_ui_definition():
    document = source()
    original = copy.deepcopy(document)
    result = prepare(document)
    assert result["ok"], result
    assert document == original
    projected = result["projection"]["widgets"][0]
    assert projected["id"] == widget(document)["id"]
    assert [b["id"] for b in projected["inputs"]["buttons"]] == ["previous", "toggle", "next", "stop"]
    assert [b["label"] for b in projected["inputs"]["buttons"]] == [b["label"] for b in widget(document)["inputs"]["buttons"]]
    assert "media_control_skill" not in json.dumps(result["projection"])
    assert "$state" not in json.dumps(result["projection"])
    assert result["receipt"]["hardware_qualified"] is False
    assert result["authority"] == "preparation_only"


@pytest.mark.parametrize("button", ["previous", "toggle", "next", "stop"])
def test_input_resolves_the_same_original_action_with_frozen_arguments(button):
    prepared = prepare()
    result = resolve(prepared, event(prepared, button))
    assert result["ok"]
    original = next(a for a in widget(source())["actions"] if a["on"] == "click:" + button)
    expected = copy.deepcopy(original)
    expected["params"].update(profile_id="family", target_id="target:kitchen")
    assert result["action"] == expected
    assert result["authority"] == "requires_admitted_action_dispatch"
    result["action"]["params"]["target_id"] = "changed"
    assert resolve(prepared, event(prepared, button))["action"] == expected


def test_exact_profile_digest_and_common_schema_are_attributable():
    profile = endpoint_profile()
    raw = json.dumps(profile["definition"], sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    assert profile["identity"]["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    for file in ("ui.endpoint_profile.v1.schema.json", "ui.endpoint_implementation.v1.schema.json", "ui.endpoint_projection.v1.schema.json", "ui.endpoint_action.v1.schema.json"):
        Draft202012Validator.check_schema(json.loads((ABI / file).read_text()))
    for document, schema in [(profile["definition"], "ui.endpoint_profile.v1.schema.json"), (context()["implementation"], "ui.endpoint_implementation.v1.schema.json"), (prepare()["projection"], "ui.endpoint_projection.v1.schema.json"), (event(prepare()), "ui.endpoint_action.v1.schema.json")]:
        Draft202012Validator(json.loads((ABI / schema).read_text())).validate(document)
    assert prepare()["receipt"]["source_contract_digest"].startswith("sha256:")
    assert prepare() == prepare()


@pytest.mark.parametrize("field,value", [("version", "1.0.0"), ("digest", "sha256:" + "0" * 64), ("profile_id", "another.profile")])
def test_incompatible_implementation_never_receives_a_projection(field, value):
    args = context()
    args["implementation"]["profiles"][0][field] = value
    finding(project_endpoint_ui(source(), **args), "profile_incompatible")


@pytest.mark.parametrize("limit,value", [("max_buttons_per_widget", 3), ("max_payload_bytes", 100), ("max_label_chars", 3)])
def test_receiver_limits_narrow_the_server_budget(limit, value):
    args = context()
    args["implementation"]["limits"] = {limit: value}
    finding(project_endpoint_ui(source(), **args), "limit_exceeded")


@pytest.mark.parametrize("where,key,value,code", [
    ("widget", "type", "ui.form", "component_unsupported"),
    ("widget", "dataSource", {"kind": "skill", "target": "private.read"}, "unsupported_field"),
    ("widget", "visibleIf", "$state.a && $state.b", "unsupported_visibility"),
    ("inputs", "selectedStateKey", "selection", "unsupported_field"),
    ("button", "onClick", "code()", "unsupported_field"),
    ("button", "label", "", "label_required"),
    ("button", "disabled", "false", "invalid_shape"),
    ("action", "type", "navigateModal", "action_unsupported"),
    ("action", "script", "code()", "unsupported_field"),
])
def test_required_unsupported_semantics_fail_instead_of_silent_loss(where, key, value, code):
    document = source()
    w = widget(document)
    target = {"widget": w, "inputs": w["inputs"], "button": w["inputs"]["buttons"][0], "action": w["actions"][0]}[where]
    target[key] = value
    result = prepare(document)
    assert result["ok"] is False
    # The ordinary WebUI schema may reject invalid input before profile lowering.
    assert result["findings"][0]["code"] in ("endpoint_ui." + code, "endpoint_ui.source_invalid")


@pytest.mark.parametrize("replacement,code", [("$event.target_id", "unsupported_binding"), ("prefix $state.profileId", "unsupported_binding"), ("$state.missing", "binding_missing"), ({"kind": "expression", "op": "if"}, "unsupported_binding")])
def test_dynamic_bindings_are_an_explicit_small_subset(replacement, code):
    document = source()
    widget(document)["actions"][0]["params"]["target_id"] = replacement
    finding(prepare(document), code)


def test_localized_labels_and_disabled_controls_keep_truthful_actions():
    document = source()
    w = widget(document)
    w["inputs"]["buttons"][0]["disabled"] = True
    prepared = prepare(document, translations={"runtime.media_center.ui.next": "Далее", "runtime.media_center.ui.transport": "Управление"})
    assert prepared["ok"]
    projected = prepared["projection"]["widgets"][0]
    assert projected["title"] == "Управление"
    assert projected["inputs"]["buttons"][2]["label"] == "Далее"
    assert projected["inputs"]["buttons"][0]["disabled"] is True
    finding(resolve(prepared, event(prepared, "previous")), "action_unavailable")
    assert resolve(prepared)["ok"]


def test_hidden_widget_has_no_projection_controls_or_dispatchable_actions():
    state = context()["state"]
    state["surfaceProfile"] = "desktop"
    result = prepare(state=state)
    assert result["ok"]
    assert result["projection"]["widgets"] == [] and result["node_bindings"] == {}
    finding(resolve(result), "action_unavailable")


@pytest.mark.parametrize("change,code", [
    ({"endpoint_id": "another:endpoint"}, "endpoint_mismatch"),
    ({"surface_id": "another:surface"}, "surface_mismatch"),
    ({"projection_digest": "sha256:" + "0" * 64}, "surface_mismatch"),
    ({"state_revision": "state:8"}, "stale_event"),
    ({"generation": 4}, "stale_event"),
    ({"action_ref": "undeclared"}, "action_unavailable"),
    ({"params": {"target_id": "attacker"}}, "event_invalid"),
    ({"generation": True}, "event_invalid"),
])
def test_endpoint_input_cannot_choose_another_action_target_or_generation(change, code):
    prepared = prepare()
    e = event(prepared)
    e.update(change)
    finding(resolve(prepared, e), code)


@pytest.mark.parametrize("changes,code", [
    ({"authenticated_endpoint_id": "another:endpoint"}, "endpoint_mismatch"),
    ({"current_state_revision": "state:8"}, "stale_event"),
    ({"current_generation": 4}, "stale_event"),
    ({"current_generation": True}, "stale_event"),
    ({"now": 1120}, "expired"),
])
def test_authoritative_caller_state_is_rechecked(changes, code):
    finding(resolve(prepare(), **changes), code)


def test_duplicate_input_has_one_stable_dispatch_key_but_resolution_executes_nothing():
    prepared = prepare()
    first = resolve(prepared)
    repeated = resolve(prepared)
    assert first == repeated
    assert first["event_key"].startswith("endpoint-ui:")
    e = event(prepared)
    e["event_id"] = "event:2"
    assert resolve(prepared, e)["event_key"] != first["event_key"]
    # The normal admitted dispatcher must persist/deduplicate this key;
    # this pure helper does not claim exactly-once effects.
    assert first["authority"] == "requires_admitted_action_dispatch"


@pytest.mark.parametrize("part", ["projection", "node_bindings"])
def test_mutating_node_preparation_invalidates_its_receipt(part):
    prepared = prepare()
    if part == "projection":
        prepared[part]["widgets"][0]["title"] = "changed"
    else:
        prepared[part][event(prepared)["action_ref"]]["action"]["params"]["target_id"] = "changed"
    finding(resolve(prepared, event(prepare())), "preparation_invalid")


@pytest.mark.parametrize("widget_ids", [[], ["missing"], ["media-mobile-transport"] * 2, [{}]])
def test_invalid_selection_is_a_structured_gap(widget_ids):
    assert prepare(widget_ids=widget_ids)["ok"] is False


@pytest.mark.parametrize("expires_at", [1000, 999, 1301, True, float("inf")])
def test_deadlines_are_bounded(expires_at):
    finding(prepare(expires_at=expires_at), "deadline_invalid")


def test_malformed_source_and_non_json_state_fail_safely():
    document = source()
    document["ui"] = "not an object"
    finding(prepare(document), "source_invalid")
    finding(prepare(state={"n": float("nan")}), "invalid_json")
    finding(prepare(state={"secret": "x" * 20000}), "limit_exceeded")


def test_profile_result_is_not_shared_mutable_state():
    profile = endpoint_profile()
    profile["definition"]["limits"]["max_payload_bytes"] = 1
    assert prepare()["ok"]


def test_action_refs_cannot_collide_between_widget_and_button_identities():
    document = source()
    first = widget(document)
    first["id"] = "a:b"
    first["inputs"]["buttons"] = [{"id": "c", "label": "First"}]
    first["actions"] = [{"on": "click:c", "type": "callSkill", "target": "fixture.first", "params": {}}]
    second = copy.deepcopy(first)
    second["id"] = "a"
    second["inputs"]["buttons"][0]["id"] = "b:c"
    second["actions"][0].update(on="click:b:c", target="fixture.second")
    document["ui"]["application"]["desktop"]["pageSchema"]["widgets"].append(second)
    prepared = prepare(document, widget_ids=["a:b", "a"])
    assert prepared["ok"], prepared
    assert len(prepared["node_bindings"]) == 2
    assert {b["action"]["target"] for b in prepared["node_bindings"].values()} == {"fixture.first", "fixture.second"}


def test_i18n_descriptor_uses_the_common_key_fallback_meaning():
    document = source()
    widget(document)["title_i18n"] = {"key": "fixture.title", "fallback": "Fallback title"}
    result = prepare(document)
    assert result["ok"], result
    assert result["projection"]["widgets"][0]["title"] == "Fallback title"
    result = prepare(document, translations={"fixture.title": "Translated title"})
    assert result["projection"]["widgets"][0]["title"] == "Translated title"


@pytest.mark.parametrize("translations", [["not a dictionary"], {"x": {"nested": "not admitted"}}])
def test_translation_inputs_have_a_closed_shape(translations):
    finding(prepare(translations=translations), "invalid_shape")


def test_browser_json_number_range_is_preserved():
    finding(prepare(state={**context()["state"], "outside_browser_range": 2**53}), "invalid_json")


def test_unpaired_unicode_is_a_refusal_instead_of_a_wire_encoding_exception():
    finding(prepare(state={**context()["state"], "invalid_text": "\ud800"}), "invalid_json")


@pytest.mark.parametrize("literal", ['1e3', '"escaped\\ntext"'])
def test_visibility_does_not_reinterpret_browser_literals_as_json(literal):
    document = source()
    widget(document)["visibleIf"] = "$state.surfaceProfile == " + literal
    finding(prepare(document), "unsupported_visibility")


def test_source_and_profile_are_independent_from_the_endpoint_platform():
    arguments = context()
    ipad = project_endpoint_ui(source(), **arguments)
    arguments["implementation"].update(endpoint_id="fixture:android", renderer_build="fixture:java.0.1.0")
    android = project_endpoint_ui(source(), **arguments)
    assert ipad["ok"] and android["ok"]
    assert ipad["projection"]["widgets"] == android["projection"]["widgets"]
    assert ipad["projection"]["profile"] == android["projection"]["profile"]
    assert ipad["node_bindings"] == android["node_bindings"]
    assert ipad["projection"]["scope"]["endpoint_id"] != android["projection"]["scope"]["endpoint_id"]


def test_sdk_exposes_the_same_pure_preparation_and_resolution():
    from adaos.sdk.developer import ui
    prepared = ui.project_endpoint(source(), **context())
    assert prepared == prepare()
    assert ui.endpoint_profile() == endpoint_profile()
    assert ui.resolve_endpoint_action(prepared, event(prepared),
        authenticated_endpoint_id="fixture:ipad", current_state_revision="state:7",
        current_generation=3, now=1010)["action"] == resolve(prepared)["action"]


def test_offline_tool_prepares_the_same_source_without_effects(tmp_path):
    args = context()
    args.pop("expires_at")
    args.pop("now")
    p = tmp_path / "context.json"
    p.write_text(json.dumps(args))
    output, wire = tmp_path / "node.json", tmp_path / "wire.json"
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1] / "tools/project_endpoint_ui.py"), "prepare", "--source", str(FIXTURE), "--context", str(p), "--output", str(output), "--projection-output", str(wire)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]
    assert json.loads(output.read_text())["ok"]
    assert "media_control_skill" not in wire.read_text()


def test_offline_refusal_does_not_leave_an_old_successful_receiver_file(tmp_path):
    args = context()
    args.pop("expires_at")
    args.pop("now")
    args["implementation"]["profiles"][0]["digest"] = "sha256:" + "0" * 64
    p = tmp_path / "context.json"
    p.write_text(json.dumps(args))
    output, wire = tmp_path / "node.json", tmp_path / "wire.json"
    wire.write_text('{"schema":"stale-success"}')
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).parents[1] / "tools/project_endpoint_ui.py"), "prepare", "--source", str(FIXTURE), "--context", str(p), "--output", str(output), "--projection-output", str(wire)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert json.loads(wire.read_text())["ok"] is False
