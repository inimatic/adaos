"""Safety and contract checks for the human-authored Builder design specimen."""

import importlib.util
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
    assert page["initialState"]["fileTree"] != page["initialState"]["baselineFileTree"]
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
