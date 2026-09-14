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
    assert len(page["initialState"]["samples"]) == 12
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
    for name in ("design-edit-asset", "design-settings", "design-specimens"):
        editor = application["modals"][name]["schema"]["widgets"][0]
        assert editor["dataSource"]["kind"] == "static"
        assert editor["inputs"]["selectedStateKey"]
        assert not any(str(field.get("defaultValue", "")).startswith("$state") for field in editor["inputs"]["fields"])
    assert all(f"design-{design.REVISION}-" in resource["path"] for resource in application["resources"].values())
