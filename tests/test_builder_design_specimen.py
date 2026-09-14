"""Safety and contract checks for the human-authored Builder design specimen."""

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SPEC = importlib.util.spec_from_file_location(
    "builder_design", Path(__file__).resolve().parents[1] / "scripts/build_builder_workbench_prototype.py"
)
design = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(design)


def test_specimen_uses_native_schema_without_live_execution():
    doc = design.build_document()
    design.audit_safety(doc)
    Draft202012Validator(design.read(design.ROOT / "src/adaos/abi/webui.v1.schema.json")).validate(doc)
    page = doc["ui"]["application"]["desktop"]["pageSchema"]
    assert page["meta"]["builder"]["human_acceptance"] == "pending"
    assert page["meta"]["builder"]["live_commands"] is False
    assert len(page["initialState"]["samples"]) == 17
    assert set(design.LOCALES["ru"]) == set(design.LOCALES["en"])


@pytest.mark.parametrize("action", [
    {"on": "click", "type": "callSkill", "target": "builder.run"},
    {"on": "click", "type": "futureUnknownExecutor"},
    {"on": "click", "type": "openModal", "target": "legacy-incompatible"},
    {"kind": "api", "url": "/api/builder"},
    {"on": "click", "type": "openUrl", "params": {"url": "https://example.test"}},
    {"sendCommand": "voice.chat.user"},
    {"label": "Execute", "command": "voice.chat.user"},
    {"label": "Approve", "token": "unreviewed-approval"},
])
def test_rejects_live_or_incompatible_contracts(action):
    with pytest.raises(ValueError):
        design.audit_safety(action)


def test_evidence_matches_each_specimen_state():
    samples = design.specimens()
    assert samples["failed"]["checks"][-1]["result"] == "Не пройдена"
    assert samples["verifying"]["checks"][-1]["result"] == "Выполняется"
    assert samples["offline"]["checks"][-1]["result"] == "Актуальность неизвестна"
    assert samples["implementation_review"]["checks"][-1]["result"] == "Пройдена"
    for sample in samples.values():
        assert sample["process"][-1]["preview"] == sample["summary"]


def test_feedback_reuses_dev_tickets_with_real_design_identity():
    action = design.feedback("click:feedback")
    assert action["type"] == "openDevTickets"
    scope = action["params"]["target_scope"]
    assert scope == {"type": "scenario", "id": "builder", "source": "dev", "project_id": "builder", "scenario_id": "builder", "revision": design.REVISION}
    assert action["params"]["specimen_preview_revision"] == "$state.previewRevision"


def test_acceptance_never_starts_implementation_or_publication():
    doc = design.build_document()
    acceptance = doc["ui"]["application"]["modals"]["design-accept"]["schema"]["widgets"][-1]
    actions = acceptance["actions"]
    assert actions[0]["params"]["current"] == "$state.samples.accepted"
    assert actions[-1]["type"] == "closeModal"
    assert all(action["type"] in {"updateState", "closeModal"} for action in actions)


def test_editors_use_selected_record_hydration_and_versioned_locale_assets():
    application = design.build_document()["ui"]["application"]
    for name in ("design-edit-asset", "design-settings"):
        editor = application["modals"][name]["schema"]["widgets"][0]
        assert editor["dataSource"]["kind"] == "static"
        assert editor["inputs"]["selectedStateKey"]
        assert not any(str(field.get("defaultValue", "")).startswith("$state") for field in editor["inputs"]["fields"])
    assert all(f"design-{design.REVISION}-" in resource["path"] for resource in application["resources"].values())


def test_files_inputs_and_process_are_separate_scoped_views():
    application = design.build_document()["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    widgets = {w["id"]: w for w in page["widgets"]}
    assert widgets["design-file-tree"]["type"] == "visual.taigaTree"
    assert widgets["design-file-tree"]["inputs"]["selectionMode"] == "leaf"
    component = page["initialState"]["fileComponents"]["scenario"]
    assert component["tree"] != component["baselineTree"]
    assert page["initialState"]["includeReference"] is False
    viewer = application["modals"]["design-file-viewer"]["schema"]["widgets"]
    assert all(w["type"] != "ui.form" for w in viewer)
    assert viewer[1]["actions"][0]["params"]["requestRevision"] == "$state.previewRevision"
    menu = next(b for b in widgets["design-workbench-header"]["inputs"]["buttons"] if b["id"] == "specimens")
    assert menu["displaySelectedLabel"] is False
    assert menu["optionMetaPaths"] == ["actor"]


def test_clarification_drafts_do_not_resume_execution():
    widgets = design.build_document()["ui"]["application"]["modals"]["design-answer"]["schema"]["widgets"]
    answer = widgets[1]
    assert answer["inputs"]["autoCommit"] is True
    assert len([f for f in answer["inputs"]["fields"] if f.get("required")]) == 2
    assert answer["actions"][0]["params"]["current"] == "$state.samples.verifying"
    assert widgets[2]["actions"] == [{"on": "click:save-draft", "type": "closeModal"}]


def test_delivery_steps_are_explicit_local_simulations():
    modals = design.build_document()["ui"]["application"]["modals"]
    for command, target in [("prepare-beta", "beta_ready"), ("install-beta", "beta_active"), ("accept-beta", "stable_ready"), ("release-stable", "stable_local"), ("publish-stable", "stable_published")]:
        actions = modals["design-" + command]["schema"]["widgets"][-1]["actions"]
        assert actions[0]["params"]["current"] == "$state.samples." + target
        assert actions[1]["type"] == "closeModal"


def test_component_selection_retains_ownership_and_revision_scope():
    application = design.build_document()["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    components = page["initialState"]["fileComponents"]
    assert {c["component_kind"] for c in components.values()} == {"project", "skill", "scenario"}
    assert components["dependency"]["editable"] is False
    assert components["scenario"]["tree"] != components["scenario"]["baselineTree"]
    picker = next(w for w in page["widgets"] if w["id"] == "design-component-picker")
    assert picker["actions"][0]["params"]["selectedFile"] == {}
    viewer = application["modals"]["design-file-viewer"]["schema"]["widgets"][-1]
    assert "$state.selectedComponent.editable" in viewer["inputs"]["buttons"][0]["enabledIf"]
    assert viewer["actions"][0]["params"]["requestComponent"] == "$state.selectedComponent.label"


def test_header_has_application_title_and_no_ambiguous_head_label():
    page = design.build_document()["ui"]["application"]["desktop"]["pageSchema"]
    header = next(w for w in page["widgets"] if w["id"] == "design-workbench-header")
    status = header["inputs"]["statusDataSource"]["value"]
    assert status["title"] == "$state.applicationTitle"
    assert status["target_label"].startswith("Редакция $state.previewRevision")
    assert "текущий" not in status["target_label"]
    assert header["inputs"]["buttons"][0]["label"] == "$state.applicationTitle"


def test_readme_informal_chat_and_platform_feedback_do_not_implicitly_execute():
    application = design.build_document()["ui"]["application"]
    page = application["desktop"]["pageSchema"]
    chats = [w for w in page["widgets"] if w["type"] == "ui.chat"]
    assert len(chats) == 4
    assert all(w["dataSource"]["kind"] == "static" and not w["inputs"].get("sendCommand") for w in chats)
    ids = page["initialState"]["conversationModes"]
    assert ids["task"]["thread"] != ids["informal"]["thread"]
    editor = application["modals"]["design-readme-editor"]["schema"]["widgets"][0]
    assert editor["inputs"]["selectedStateKey"] == "readmeRecordId"
    assert editor["actions"][0]["params"]["readmeText"] == "$event.values.content"
    viewer = application["modals"]["design-file-viewer"]["schema"]["widgets"][0]
    assert viewer["dataSource"]["value"]["content"]["then"]["else"] == "$state.readmeText"
    for w in page["widgets"]:
        if w["id"].startswith("design-composer-") and w["id"].endswith("-task"):
            assert {o["value"] for o in w["inputs"]["fields"][0]["options"]} == {"correction", "requirement", "discussion"}
            assert w["actions"][0]["params"]["pendingIntent"] == "$event.values.intent"
    consent = application["modals"]["design-platform-consent"]["schema"]["widgets"][-1]
    assert set(consent["actions"][0]["params"]) == {"platformRequestStatus"}
    proposal = application["modals"]["design-promote-idea"]["schema"]["widgets"][-1]
    assert proposal["actions"][0]["params"]["requestedFile"] == ""
    assert proposal["actions"][0]["params"]["requestComponent"] == ""


def test_trace_preserves_verbatim_versioned_inputs_and_attempt_history():
    trace = design.trace_specimen()
    records = trace["records"]
    first = records["run-p16"]["packet"]
    retry = records["run-p17"]["packet"]
    assert [r["id"] for r in first["requirements"]] == ["req-edit-v1", "req-selection"]
    assert [r["id"] for r in retry["requirements"]] == ["req-edit-v2", "req-selection"]
    assert [m["id"] for m in first["messages"]] == ["msg-01"]
    assert [m["id"] for m in retry["messages"]] == ["msg-01", "msg-02", "msg-03", "msg-04"]
    for record in records.values():
        if record["entity"] == "run":
            raw = json.dumps(record["packet"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            assert record["digest"] == "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
            for message in record["packet"]["messages"]:
                assert message["body"] == records[message["id"]]["body"]
        if record["entity"] == "message":
            assert record["digest"] == "sha256:" + hashlib.sha256(record["body"].encode("utf-8")).hexdigest()
    assert ("run-p17", "repairs", "run-p16") in trace["edges"]
    assert ("evidence-16", "evaluates", "result-p16") in trace["edges"]
    assert ("evidence-17", "evaluates", "result-003") in trace["edges"]


def test_trace_links_are_bidirectional_and_nonrequirements_do_not_gain_tasks():
    trace = design.trace_specimen()
    records = trace["records"]
    for left, _, right in trace["edges"]:
        assert right in {r["id"] for r in records[left]["related"]}
        assert left in {r["id"] for r in records[right]["related"]}
    assert not records["msg-05"]["related"]
    assert not records["msg-06"]["related"]
    assert not records["suggest-confirm"]["related"]
    assert {r["id"] for r in records["msg-04"]["related"]} == {"run-p17"}
    included = [r for r in trace["scope_rows"] if r["admission"] == "В приемке 003"]
    assert {r["id"] for r in included} == {"req-edit-v2", "req-selection"}
    assert all(r["result"] == "003 · E17" for r in included)
    assert ("msg-03", "defers", "req-readme") in trace["edges"]


def test_trace_widgets_reuse_native_chat_actions_without_changing_preview():
    app = design.build_document()["ui"]["application"]
    page = app["desktop"]["pageSchema"]
    widgets = {w["id"]: w for w in page["widgets"]}
    chat = widgets["design-conversation-full-task"]
    message = next(m for m in chat["dataSource"]["value"]["messages"] if m["id"] == "msg-02")
    assert message["text"] == page["initialState"]["traceRecords"]["msg-02"]["body"]
    action = message["actions"][0]["action"]
    assert action["params"]["statePatch"]["traceRecord"] == "$state.traceRecords.msg-02"
    assert action["type"] == "openModal"
    links = app["modals"]["design-trace"]["schema"]["widgets"][-1]
    assert set(links["actions"][0]["params"]) == {"traceRecord", "traceContextVisible"}
    assert "'003'" in widgets["design-scope-requirements"]["visibleIf"]
    assert any(w["id"] == "design-review-coverage" for w in app["modals"]["design-accept"]["schema"]["widgets"])
