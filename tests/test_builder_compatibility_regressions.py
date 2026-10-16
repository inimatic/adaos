import copy
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from adaos.e2e.prototype_context_ablation import native_builder
from adaos.sdk.developer.ui import expression_contract, expression_findings
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


@pytest.fixture
def builder():
    root = Path(__file__).resolve().parents[1]
    return native_builder(Path(os.environ.get("ADAOS_TEST_BUILDER_SOURCE", root / ".adaos/workspace/skills/builder_skill")) / "handlers/main.py")


def test_abi_expression_vocabulary_matches_the_actual_client_executor():
    path = Path(__file__).resolve().parents[1] / "src/adaos/integrations/adaos-client/src/app/runtime/state-expression.ts"
    evaluator = path.read_text(encoding="utf-8").split("function evaluateDeclarativeExpression(", 1)[1].split("export function resolveDeclarativeValue(", 1)[0]
    actual = set(re.findall(r"if \(op === '([^']+)'\)", evaluator))
    assert actual == set(expression_contract()["definition"]["properties"]["op"]["enum"])


def test_conditional_contract_is_shared_with_native_generation_and_repair(builder):
    good = {"kind": "expression", "op": "if", "args": [True, "natural", "unchanged"]}
    assert expression_findings(good) == []
    assert builder._invalid_declarative_expression(good) == ""
    bad = {**good, "op": "ternary"}
    before = copy.deepcopy(bad)
    findings = expression_findings({"skin": bad}, path="pageSchema")
    assert findings[0]["path"] == "pageSchema.skin"
    assert findings[0]["contract_digest"] == expression_contract()["digest"]
    diagnostic = builder._invalid_declarative_expression({"skin": bad}, "pageSchema")
    assert "op='if'" in diagnostic and "expression_contract.v1" in diagnostic
    assert bad == before


def test_nested_control_id_is_selected_only_in_the_addressed_array(builder):
    document = {"widgets": [{"id": "start", "title": "unrelated"}, {"id": "params", "inputs": {
        "buttons": [{"id": "other"}, {"id": "start", "label": "Retouch"}]}}]}
    path = "/widgets/@params/inputs/buttons/@start/label"
    result = builder._apply_json_patch_operation(copy.deepcopy(document), {"op": "replace", "path": path, "value": "Start retouch"})
    assert result["widgets"][0] == document["widgets"][0]
    assert result["widgets"][1]["inputs"]["buttons"][1]["label"] == "Start retouch"
    result["widgets"][1]["inputs"]["buttons"].reverse()
    assert builder._json_pointer_get(result, path) == "Start retouch"


def test_key_only_columns_have_precise_non_mutating_diagnostic(builder):
    columns = [{"key": "photo"}, {"key": "version"}]
    with pytest.raises(KeyError, match="key-only members=.*photo.*version"):
        builder._json_pointer_list_index(columns, "@photo")
    assert columns == [{"key": "photo"}, {"key": "version"}]
    with pytest.raises(ValueError, match="ambiguous"):
        builder._json_pointer_list_index([{"id": "run"}, {"id": "run"}], "@run")


@pytest.mark.parametrize("name", ["scenario.json", "webui.json"])
def test_large_formatting_diff_is_admitted_but_changed_values_still_require_admission(tmp_path, name):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / name
    value = {"schema": "adaos.webui.v1", "records": [{"id": f"row-{i}", "enabled": True} for i in range(200)]}
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    for args in (["init"], ["config", "user.name", "Fixture"], ["config", "user.email", "fixture@example.invalid"],
                 ["add", name], ["commit", "-m", "baseline"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    assignment = {"realize_request": {"artifacts": {"execution_budget": {"max_wall_seconds": 300}}}}
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    LocalSkillFactoryWorker._validate_manifest_rewrite_bounds(assignment, [name], workspace=root)
    value["records"][0]["enabled"] = 1  # Python True == 1 must not admit a JSON type change.
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(ValueError, match="large declarative manifest rewrite"):
        LocalSkillFactoryWorker._validate_manifest_rewrite_bounds(assignment, [name], workspace=root)
