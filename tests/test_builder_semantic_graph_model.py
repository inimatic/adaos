from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from adaos.sdk.builder import semantic_ui as semantic_sdk
from adaos.services.builder.semantic_graph import reduce_graph
from adaos.services.builder.semantic_graph_model import (
    SemanticGraphError,
    canonicalize_semantic_graph,
    semantic_document_to_graph,
    semantic_graph_dependency_closure,
    semantic_graph_diff,
    semantic_graph_to_document,
    validate_semantic_graph,
)
from adaos.services.builder.semantic_prototype import (
    _compile_semantic_prototype_v2,
    compile_semantic_prototype,
    compile_semantic_prototype_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = [
    ROOT / "e2e/builder/development/semantic-increments/fixture.json",
    ROOT / "e2e/builder/development/contract-conformance/inventory-fixture.json",
]


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_semantic_v2_round_trips_through_one_canonical_graph(path: Path) -> None:
    source = read(path)
    graph = semantic_document_to_graph(source)

    assert semantic_graph_to_document(graph) == source
    receipt = validate_semantic_graph(graph)
    assert receipt["roundtrip"] is True
    assert receipt["source_digest"] == graph["source_digest"]
    assert receipt["node_count"] == len(graph["nodes"])
    assert graph == semantic_document_to_graph(source)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_graph_compiler_route_is_exactly_equal_to_retained_direct_lowering(path: Path) -> None:
    source = read(path)
    direct = _compile_semantic_prototype_v2(source, project_ref="project:graph-parity")
    harmonized = compile_semantic_prototype(source, project_ref="project:graph-parity")
    receipt = harmonized.pop("semantic_graph_receipt")

    assert harmonized == direct
    assert receipt["compiler_route"] == "semantic_graph.v1"
    assert receipt["source_digest"] == direct["semantic_digest"]


def test_public_sdk_exposes_the_same_pure_graph_contract() -> None:
    source = read(FIXTURES[0])
    graph = semantic_sdk.project_graph(source)

    assert semantic_sdk.validate_graph(graph)["roundtrip"] is True
    assert semantic_sdk.graph_dependencies(graph, ["command:complete"]) == (
        semantic_graph_dependency_closure(graph, ["command:complete"])
    )
    assert semantic_sdk.graph_diff(graph, graph)["changes"] == []


def test_graph_compilation_retains_brief_authority_for_interpreted_automation_requirements():
    from adaos.services.builder.workflow import BuilderWorkflowError
    from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate, _automation_requirement
    brief, source = _multi_resource_fixture()
    old_ref = brief['principal_jobs'][0]['id']
    ref = 'req:external-command'
    brief['principal_jobs'][0]['id'] = ref
    candidate = _multi_resource_candidate(source)
    binding = next(row for row in candidate['requirement_bindings'] if row['requirement_ref'] == old_ref)
    binding.update(requirement_ref=ref, semantic_refs=[{'kind': 'view', 'id': candidate['views'][0]['id']}])
    candidate['automation_requirements'] = [_automation_requirement(ref)]
    compiled = compile_semantic_prototype_candidate(candidate, brief=brief)
    document = compiled['semantic_document']
    graph = semantic_document_to_graph(document, brief=brief)
    assert semantic_graph_to_document(graph, brief=brief) == document
    assert compile_semantic_prototype(document, brief=brief)['semantic_document'] == document
    # A graph is not a replacement for the source requirement authority.
    with pytest.raises(BuilderWorkflowError, match='only defer jobs or residual'):
        validate_semantic_graph(graph)


def test_dependency_closure_uses_graph_edges_and_implicit_identity_fields() -> None:
    graph = semantic_document_to_graph(read(FIXTURES[0]))

    closure = semantic_graph_dependency_closure(graph, ["command:complete"])

    assert "command:complete" in closure
    assert "view:record-editor" in closure
    assert "resource:records" in closure
    assert "field:records.id" in closure
    assert "field:records.state" in closure


def test_declared_identity_field_has_one_owner_dependency() -> None:
    source = read(FIXTURES[0])
    resource = source["resources"][0]
    identity = copy.deepcopy(resource["fields"][0])
    identity.update(
        id="id",
        label={"key": "field.records.id", "en": "ID", "ru": "ID"},
        value_type="short_text",
        editable=False,
        required=False,
    )
    resource["fields"].insert(0, identity)

    graph = semantic_document_to_graph(source)
    dependencies = [
        edge
        for edge in graph["edges"]
        if edge["source"] == "field:records.id"
        and edge["relation"] == "field.resource"
    ]

    assert len(dependencies) == 1
    assert semantic_graph_to_document(graph) == source


def test_requirement_binding_resolves_one_unqualified_field_ref() -> None:
    source = read(FIXTURES[0])
    source["requirement_bindings"].append(
        {"requirement_ref": "information:name", "semantic_refs": ["field:name"]}
    )

    graph = semantic_document_to_graph(source)
    requirement = next(
        item for item in graph["nodes"] if item["kind"] == "requirement_binding"
    )
    targets = {
        edge["target"]
        for edge in graph["edges"]
        if edge["source"] == requirement["ref"] and edge["relation"] == "requirement.binds"
    }

    assert "field:records.name" in targets
    assert semantic_graph_to_document(graph) == source


def test_graph_reducer_reports_only_the_admitted_stable_ref() -> None:
    source = read(FIXTURES[0])
    view = next(item for item in source["views"] if item["id"] == "records-list")
    operation = {
        "seq": 1,
        "kind": "label.set",
        "ref": "view:records-list",
        "locale": "en",
        "expected": view["title"]["en"],
        "text": "Current records",
    }

    result = reduce_graph(source, [operation], write_refs=["view:records-list"])

    assert result["semantic_document"]["views"][0]["title"]["en"] == "Current records"
    assert result["graph_diff"]["write_refs"] == ["view:records-list"]
    assert result["semantic_graph_receipt"]["graph_digest"] == result["semantic_graph"]["graph_digest"]
    assert reduce_graph(
        result["semantic_document"], result["inverse_ops"], write_refs=["view:records-list"]
    )["semantic_document"] == source


def test_graph_detects_digest_tampering_and_can_canonicalize_an_admitted_draft() -> None:
    source = read(FIXTURES[0])
    graph = semantic_document_to_graph(source)
    draft = copy.deepcopy(graph)
    node = next(item for item in draft["nodes"] if item["ref"] == "view:records-list")
    node["payload"]["title"]["en"] = "Changed"

    with pytest.raises(SemanticGraphError, match="digest mismatch"):
        validate_semantic_graph(draft)

    changed = canonicalize_semantic_graph(draft)
    diff = semantic_graph_diff(graph, changed)
    assert diff["write_refs"] == ["view:records-list"]
    assert semantic_graph_to_document(changed)["views"][0]["title"]["en"] == "Changed"


def test_graph_preserves_optional_surfaces_regions_and_distinct_automation_obligations() -> None:
    source = read(FIXTURES[0])
    source["layout"] = {
        "pattern": "workbench",
        "regions": [
            {"id": "primary", "role": "main", "priority": 100, "scroll": "region", "wide": "pane",
             "compact": "stack", "min_px": 480, "preferred_px": 800, "max_px": 1600, "grow": 1},
            {"id": "supporting", "role": "inspector", "priority": 60, "scroll": "region", "wide": "pane",
             "compact": "sheet", "min_px": 320, "preferred_px": 420, "max_px": 800, "grow": 0},
        ],
    }
    source["capability_surfaces"] = [
        {"id": "preview", "capability_ref": "visual.sitePreview", "region_role": "primary",
         "title": {"key": "preview.title", "en": "Preview", "ru": "Предпросмотр"},
         "site_id": "graph-proof", "route": "/", "min_height_px": 480,
         "remember_viewport": False}
    ]
    common = {
        "requirement_ref": "job:shared",
        "disclosure": {"en": "Automation remains separate.", "ru": "Автоматизация остаётся отдельной."},
        "acceptance": "The declared authority boundary remains observable.",
    }
    source["automation_requirements"] = [
        {**common, "reason": "business_rule"},
        {**common, "reason": "external_integration"},
    ]
    source["requirement_bindings"].append(
        {"requirement_ref": "job:shared", "semantic_refs": ["view:records-list"]}
    )

    graph = semantic_document_to_graph(source)

    assert semantic_graph_to_document(graph) == source
    automation_nodes = [
        item for item in graph["nodes"] if item["kind"] == "automation_requirement"
    ]
    assert len(automation_nodes) == 2
    assert all(
        any(
            edge["source"] == item["ref"]
            and edge["relation"] == "automation.requirement"
            for edge in graph["edges"]
        )
        for item in automation_nodes
    )
    assert any(item["ref"] == "capability:visual.sitePreview" for item in graph["nodes"])
    assert semantic_graph_dependency_closure(graph, ["surface:preview"]) == [
        "capability:visual.sitePreview", "region:primary", "surface:preview"
    ]
