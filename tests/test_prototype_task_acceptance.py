import copy

import pytest

from adaos.e2e.prototype_task_acceptance import mutation_contract, task_mutation_contract


def fixture():
    operations = [{"id": "op:create", "kind": "create"}, {"id": "op:approve", "kind": "transition"}]
    document = {"views": [{"id": "editor", "role": "editor", "resource_ref": "arbitrary-object"},
                          {"id": "line", "role": "editor", "resource_ref": "arbitrary-child"}],
                "commands": [{"id": "create", "kind": "create", "view_ref": "editor"},
                             {"id": "approve", "kind": "transition", "view_ref": "editor"}],
                "requirement_bindings": [{"requirement_ref": "op:create", "semantic_refs": ["command:create"]},
                                         {"requirement_ref": "op:approve", "semantic_refs": ["command:approve"]}]}
    return document, operations


def test_same_resource_contract_accepts_arbitrary_names():
    document, operations = fixture()
    assert mutation_contract(document, operations, same_resource_groups=[["op:create", "op:approve"]])["ok"]


def test_child_line_create_does_not_satisfy_parent_request_create():
    document, operations = fixture()
    document["commands"][0]["view_ref"] = "line"
    result = task_mutation_contract(document, {"operations": operations}, task="inventory-procurement-ru")
    assert not result["ok"]
    assert result["findings"][0]["code"] == "task.operation_resource_mismatch"


def test_different_resources_are_legal_without_explicit_task_constraint():
    document, operations = fixture()
    document["commands"][0]["view_ref"] = "line"
    assert mutation_contract(document, operations)["ok"]


@pytest.mark.parametrize("change", ["wrong_kind", "unbound", "missing_editor", "collection"])
def test_unrelated_commands_do_not_satisfy_requirement(change):
    document, operations = fixture()
    if change == "wrong_kind":
        document["commands"][0]["kind"] = "update"
    elif change == "unbound":
        document["requirement_bindings"][0]["semantic_refs"] = ["command:approve"]
    elif change == "missing_editor":
        document["commands"][0]["view_ref"] = "absent"
    else:
        document["views"][0]["role"] = "collection"
    assert not mutation_contract(document, operations)["ok"]


@pytest.mark.parametrize("group", [["unknown", "op:create"], ["op:create"], ["op:create", "op:create"]])
def test_invalid_group_is_not_silently_ignored(group):
    document, operations = fixture()
    with pytest.raises(ValueError):
        mutation_contract(document, operations, same_resource_groups=[group])


def test_renaming_does_not_change_result():
    document, operations = fixture()
    renamed = copy.deepcopy(document)
    for view in renamed["views"]:
        view["resource_ref"] = "supplier" if view["resource_ref"] == "arbitrary-object" else "request"
    assert mutation_contract(document, operations)["ok"] == mutation_contract(renamed, operations)["ok"]
