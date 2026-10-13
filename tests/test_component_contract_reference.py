import copy
from pathlib import Path

import pytest

from adaos.e2e.component_contract_conformance import CollectionEditorReference, load_slice
from adaos.sdk.developer.prototypes import derive_resource_spec
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.resources.prototype import PrototypeResourceConflict, PrototypeResourceService

ROOT = Path(__file__).resolve().parents[1]


def runtime(tmp_path, *, remove_guard=False):
    document, spec = load_slice(ROOT)
    compiled = compile_semantic_prototype(document, project_ref="project:contract-conformance")
    provider = PrototypeResourceService(tmp_path)
    for resource in compiled["prototype_resources"]:
        derived = derive_resource_spec(compiled["webui"], resource["records"], resource_type=resource["resource_type"])
        if remove_guard:
            derived["resource_definition"]["metadata"]["prototype_policy"].pop("commands", None)
        provider.materialize({"schema": "adaos.builder.prototype_resource.v1", "project_ref": "project:contract-conformance",
            "change_id": "change-contract", "revision": "001", "webui_digest": compiled["semantic_digest"], **derived})
    reference = CollectionEditorReference(copy.deepcopy(document["resources"][0]["records"]), spec["scope"])
    return provider, compiled["prototype_resources"][0]["resource_type"], reference, compiled


def test_query_filters_and_reset_preserve_scope_against_independent_model(tmp_path):
    provider, identity, reference, _ = runtime(tmp_path)
    reference.search = "does not exist"
    reference.user_filters["state"] = "done"
    for reset in (False, True):
        if reset: reference.reset_query()
        rows = provider.query(identity, filters={"team_id": "team-1", **reference.user_filters},
            search=reference.search, sort=[], limit=None)
        assert [row["id"] for row in rows] == reference.visible_ids()
    assert reference.visible_ids() == ["record-1"]


def test_guard_rejects_fixed_effect_before_write_independently(tmp_path):
    provider, identity, reference, _ = runtime(tmp_path)
    reference.open_editor("record-1")
    assert reference.commit("complete") == "guard_error"
    before = provider.query(identity, filters={}, search="", sort=[], limit=None)
    with pytest.raises(PrototypeResourceConflict, match="guard rejected"):
        provider.operate(identity, "update", record_id="record-1", payload={"state": "done", "review_note": ""}, command_id="complete")
    assert provider.query(identity, filters={}, search="", sort=[], limit=None) == before
    reference.draft["review_note"] = "Approved"
    assert reference.commit("complete") == "committed"
    result = provider.operate(identity, "update", record_id="record-1", payload={"state": "done", "review_note": "Approved"}, command_id="complete")
    assert {key: value for key, value in result["record"].items() if key != "revision"} == reference.records[0]


def test_independent_oracle_detects_missing_guard_mutant(tmp_path):
    provider, identity, reference, _ = runtime(tmp_path, remove_guard=True)
    reference.open_editor("record-1")
    assert reference.commit("complete") == "guard_error"
    provider.operate(identity, "update", record_id="record-1", payload={"state": "done", "review_note": ""})
    assert provider.query(identity, filters={"id": "record-1"}, search="", sort=[], limit=None)[0]["state"] != reference.records[0]["state"]


def test_unscoped_or_widened_command_is_not_admitted(tmp_path):
    provider, identity, _, _ = runtime(tmp_path)
    with pytest.raises(PrototypeResourceConflict, match="command identity"):
        provider.operate(identity, "update", record_id="record-1", payload={"state": "done"})
    with pytest.raises(PrototypeResourceConflict, match="declared effect"):
        provider.operate(identity, "update", record_id="record-1", payload={"state": "done", "name": "Hidden edit", "review_note": "Ready"}, command_id="complete")


def test_required_field_rejection_does_not_write_or_change_generation(tmp_path):
    provider, identity, reference, _ = runtime(tmp_path)
    reference.open_editor("record-1")
    reference.draft["name"] = ""
    assert reference.commit("save") == "validation_error"
    before = provider.query(identity, filters={}, search="", sort=[], limit=None)
    with pytest.raises(ValueError):
        provider.operate(identity, "update", record_id="record-1", payload={"name": ""}, command_id="save")
    assert provider.query(identity, filters={}, search="", sort=[], limit=None) == before
