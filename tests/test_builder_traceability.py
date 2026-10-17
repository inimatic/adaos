from copy import deepcopy
import json
import pytest

from adaos.services.builder.traceability import project_change, evidence_impact
from adaos.services.traceability import validate_graph
from test_builder_semantic_graph import graph_project, operation  # noqa: F401


def projection(document, change=None, generation=1):
    return project_change(project_ref="scenario:retouch", generation=generation,
        semantic_document=document, change=change or {"issues": []},
        source_map={"command:run": ["ui.application.desktop.pageSchema.widgets.@run"]})


def test_many_to_many_message_requirement_semantic_and_expected_postcondition_links():
    document = {"views": [{"id": "params", "title": {"en": "Parameters"}}], "commands": [{"id": "run", "guard": True}]}
    change = {"issues": [{"issue_id": "I1", "source_message_ids": ["M1"], "semantic_refs": ["view:params", "command:run"]},
                         {"issue_id": "I2", "source_message_ids": ["M1"], "semantic_refs": ["command:run"]}],
              "acceptance_constraints": [{"constraint_id": "V1", "target_ref": "widget:run", "kind": "visible"}]}
    result = projection(document, change)
    graph = validate_graph(result["graph"])
    assert len([row for row in graph["nodes"] if row["kind"] == "source_message"]) == 1
    assert len([row for row in graph["edges"] if row["relation"] == "implemented_by"]) == 3
    assert len([row for row in graph["edges"] if row["relation"] == "verified_by"]) == 1
    assert not any(row["kind"] == "execution" for row in graph["nodes"])
    assert "no inferred" in result["coverage_claim"]


def test_command_change_invalidates_its_receipt_but_label_change_preserves_independent_evidence():
    document = {"commands": [{"id": "run", "guard": True}], "views": [{"id": "params", "title": "Original"}]}
    old = projection(document)["graph"]
    dependencies = {row["ref"]: row["digest"] for row in old["nodes"] if row["ref"] == "command:run"}
    receipt = {"receipt_ref": "browser:prior-run", "dependencies": dependencies}
    changed = deepcopy(document)
    changed["views"][0]["title"] = "Parameters"
    compatible = evidence_impact(old, projection(changed, generation=2)["graph"], [receipt])
    assert compatible["receipts"][0]["status"] == "compatible"
    changed["commands"][0]["guard"] = False
    stale = evidence_impact(old, projection(changed, generation=3)["graph"], [receipt])
    assert stale["receipts"][0]["status"] == "stale"
    assert stale["receipts"][0]["dependency_causes"] == ["command:run"]
    assert receipt["dependencies"] == dependencies


def test_missing_mapping_and_unknown_execution_pins_do_not_become_coverage():
    result = projection({}, {"issues": [{"issue_id": "missing", "semantic_refs": ["command:missing"]}]})
    assert {gap["kind"] for gap in result["gaps"]} == {"semantic_mapping_missing", "unresolved_semantic_ref"}
    impact = evidence_impact(result["graph"], result["graph"], [{"receipt_ref": "unknown", "dependencies": {}}])
    assert impact["receipts"][0]["status"] == "unqualified"


def test_removed_requirement_invalidates_its_evidence_and_preserves_an_independent_requirement():
    document = {"commands": [{"id": "run"}]}
    change = {"issues": [{"issue_id": identity, "semantic_refs": ["command:run"]} for identity in ("keep", "remove")]}
    before = projection(document, change)["graph"]
    refs = {row["ref"]: row["digest"] for row in before["nodes"] if row["kind"] == "requirement"}
    after = projection(document, {"issues": change["issues"][:1]}, generation=2)["graph"]
    receipts = [{"receipt_ref": key, "dependencies": {key: refs[key]}} for key in refs]
    result = {row["receipt_ref"]: row for row in evidence_impact(before, after, receipts)["receipts"]}
    assert result["keep"]["status"] == "compatible"
    assert result["remove"]["status"] == "stale"
    assert result["remove"]["dependency_causes"] == ["remove"]
    assert all(row["original_execution_unchanged"] for row in result.values())


def test_ambiguous_bare_dependency_ref_cannot_silently_select_another_kind():
    graph = projection({"commands": [{"id": "run"}]},
        {"issues": [{"issue_id": "command:run", "semantic_refs": ["command:run"]}]})["graph"]
    requirement = next(row for row in graph["nodes"] if row["kind"] == "requirement")
    result = evidence_impact(graph, graph, [{"receipt_ref": "ambiguous", "dependencies": {"command:run": requirement["digest"]}}])
    assert result["receipts"][0]["status"] == "unqualified"


def test_issue_split_and_merge_keep_many_to_many_derivation_without_inventing_verification():
    change = {"issues": [{"issue_id": "parent"},
        {"issue_id": "left", "derived_from_issue_ids": ["parent"]},
        {"issue_id": "right", "derived_from_issue_ids": ["parent"]},
        {"issue_id": "merged", "derived_from_issue_ids": ["left", "right"]}]}
    graph = projection({}, change)["graph"]
    assert len([row for row in graph["edges"] if row["relation"] == "superseded_by"]) == 4
    assert not any(row["relation"] == "verified_by" for row in graph["edges"])


def behavioral_graph(**changes):
    context = {axis: {'identity': axis + '-v1'} for axis in ('implementation','runtime','state','environment')}
    context.update(changes)
    return project_change(project_ref='scenario:retouch', generation=1,
        change={'acceptance_constraints': [{'constraint_id': 'goal-plan-result', 'target_ref': 'command:start', 'kind': 'journey'}]},
        semantic_document={'commands': [{'id': 'start'}]}, behavioral_context=context)['graph']


def test_behavioral_acceptance_requires_full_closure_and_keeps_the_execution_immutable():
    from adaos.services.builder.traceability import make_behavioral_receipt, attach_behavioral_execution
    graph = behavioral_graph()
    receipt = make_behavioral_receipt(graph, verification_refs=['goal-plan-result'], execution_ref='browser:run-1',
        observations={'goal-plan-result': {'passed': True, 'evidence_ref': 'artifact:actual-browser-result'}})
    before = deepcopy(receipt)
    assert evidence_impact(graph, graph, [receipt])['receipts'][0]['reuse_admissible']
    linked = attach_behavioral_execution(graph, receipt)
    assert {'executed_by','accepted_by'} <= {row['relation'] for row in linked['edges']}
    assert receipt == before
    for axis in ('implementation','runtime','state','environment'):
        changed = behavioral_graph(**{axis: {'identity': axis + '-v2'}})
        impact = evidence_impact(graph, changed, [receipt])['receipts'][0]
        assert impact['status'] == 'stale' and not impact['reuse_admissible']
        assert impact['dependency_causes'] == ['behavior:' + axis]
    assert receipt == before


def test_behavioral_receipts_cannot_omit_dependencies_or_promote_authoring_status():
    from adaos.services.builder.traceability import make_behavioral_receipt
    graph = behavioral_graph()
    with pytest.raises(ValueError, match='closure is incomplete'):
        make_behavioral_receipt(behavioral_graph(state=None), verification_refs=['goal-plan-result'],
            execution_ref='browser:run', observations={'goal-plan-result': {'passed': True, 'evidence_ref': 'artifact:actual'}})
    with pytest.raises(ValueError, match='preregistered'):
        make_behavioral_receipt(graph, verification_refs=['goal-plan-result'], execution_ref='authoring:completed', observations={})
    legacy = {'receipt_ref': 'old', 'dependencies': {'behavior:implementation': next(
        row['digest'] for row in graph['nodes'] if row['ref'] == 'behavior:implementation')}}
    impact = evidence_impact(graph, graph, [legacy])['receipts'][0]
    assert impact['status'] == 'compatible' and not impact['reuse_admissible']


def test_actual_workflow_projection_tracks_two_committed_increments_and_preserves_records(graph_project):
    service, workflow, root, original = graph_project
    records = deepcopy(original["resources"])
    first = service.apply(operation(service, original))
    current = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
    request = operation(service, current, transaction_id="label-2", source_revision=first["revision"])
    request["write_refs"] = ["view:people-list"]
    request["ops"][0].update(ref="view:people-list",
        expected=next(view for view in current["views"] if view["id"] == "people-list")["title"]["en"])
    request["ops"][0]["text"] = "Natural retouch"
    service.apply(request)
    result = workflow.traceability("scenario", root.name)
    final = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
    assert final["resources"] == records
    assert next(view for view in final["views"] if view["id"] == "work-list")["title"]["en"] == "Work queue"
    assert next(view for view in final["views"] if view["id"] == "people-list")["title"]["en"] == "Natural retouch"
    assert len([row for row in result["graph"]["nodes"] if row["kind"] == "execution"]) >= 2
    assert result == workflow.traceability("scenario", root.name)
