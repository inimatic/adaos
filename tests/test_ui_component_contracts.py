import copy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from adaos.e2e.component_contract_conformance import load_slice
from adaos.services.ui_component_contracts import (
    component_registration_types, contract_impact, load_component_contract,
    project_component_contract, validate_component_contract, component_contract_scope, component_contract_findings,
)
from adaos.services.builder.semantic_prototype import compile_semantic_prototype

ROOT = Path(__file__).resolve().parents[1]


def test_generated_client_registration_types_have_one_source():
    expected = component_registration_types()
    assert (ROOT / "src/adaos/integrations/adaos-client/src/app/runtime/component-contract.generated.ts").read_text(encoding="utf-8") == expected
    assert "readonly ContractQueryControl[]" in expected and "Record<ContractWidgetType" in expected


def test_mappings_retain_required_composition_and_alternatives():
    projection = project_component_contract()
    mappings = {row["id"]: row for row in projection["mappings"]}
    assert mappings["collection.query"]["required"] == ["ui.table", "ui.queryToolbar"]
    assert mappings["editor.commands"]["source_roles"] == ["editor", "command"]
    assert mappings["editor.surface"]["alternatives"] == ["inline", "modal", "side_sheet"]
    assert all("tool_id" not in row for row in projection["mappings"])


@pytest.mark.parametrize("fault", ["required", "relation", "dependency", "schema"])
def test_malformed_or_incomplete_declarations_are_rejected(fault):
    value = load_component_contract()
    if fault == "required": value["mappings"][0]["required"] = []
    elif fault == "relation": value["mappings"][0]["alternatives"] = ["ui.form"]
    elif fault == "dependency": value["components"][0]["dependencies"] = ["missing"]
    else: value["components"][0]["unknown"] = True
    with pytest.raises((ValueError, ValidationError)):
        validate_component_contract(value)


def test_real_existing_relational_slice_compiles_with_scope_and_query():
    document, _ = load_slice(ROOT)
    result = compile_semantic_prototype(document, project_ref="project:contract-test")
    assert result["validation"]["ok"]
    page = result["webui"]["ui"]["application"]["desktop"]["pageSchema"]
    table = next(widget for widget in page["widgets"] if widget["id"] == "records-list")
    assert table["dataSource"]["query"]["filters"]["team_id"] == "team-1"
    assert table["dataSource"]["query"]["filters"]["state"].startswith("$state.")
    assert len(result["source_map"]["field:name"]) >= 2
    assert result["component_contract"] == project_component_contract()["provenance"]


def test_change_has_dependency_impact_without_rebasing_active_runs():
    previous = load_component_contract()
    current = copy.deepcopy(previous)
    current["version"] = "1.0.1"
    current["components"][0]["effects"].append("selection.changed")
    impact = contract_impact(previous, current)
    assert impact["affected_units"] == ["ui.queryToolbar", "ui.table"]
    assert impact["active_runs"] == "retain_pinned_snapshot"
    assert impact["requires_new_admission"]


def test_scoped_contract_evolution_rebuilds_real_compiler_catalog_and_descriptor():
    from adaos.services.ui_capabilities import ui_capability_catalog
    from adaos.services.root_mcp.descriptor_search import get_descriptor_item
    from adaos.services.builder.semantic_admission import compiler_digest
    document, _ = load_slice(ROOT)
    previous = load_component_contract()
    baseline = compile_semantic_prototype(document, project_ref="project:contract-evolution")
    fingerprint = compiler_digest()
    current = copy.deepcopy(previous)
    current["version"] = "2.0.0"
    current["input_shapes"]["ContractTableColumn"]["properties"]["label"]["maxLength"] = 24
    with component_contract_scope(current) as snapshot:
        assert compiler_digest() != fingerprint
        assert "component.projection_snapshot_incompatible" in {finding["code"] for finding in component_contract_findings(baseline["webui"])}
        rebuilt = compile_semantic_prototype(document, project_ref="project:contract-evolution")
        assert rebuilt["component_contract"]["version"] == "2.0.0"
        assert not component_contract_findings(rebuilt["webui"])
        assert rebuilt["semantic_document"] == baseline["semantic_document"]
        assert rebuilt["prototype_resources"] == baseline["prototype_resources"]
        catalog = ui_capability_catalog()
        assert catalog["component_contracts"] == [snapshot["provenance"]]
        actual = get_descriptor_item(**snapshot["retrieval_units"][0]["locator"])
        assert actual["item"]["component_contract"] == snapshot["retrieval_units"][0]
        assert component_registration_types(current) != component_registration_types(previous)
    assert load_component_contract() == previous and compiler_digest() == fingerprint
