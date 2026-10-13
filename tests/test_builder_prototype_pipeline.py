import copy
import json

import pytest

from adaos.sdk.builder.prototype import semantic_generation_guidance
from adaos.services.builder.prototype_pipeline import KERNEL, compile_messages
from adaos.services.builder_context import digest


def messages(candidate=None, scope=None, contract="adaos.builder.state_repair.v3"):
    result = [{"role": "system", "content": "unchanged policy"}, {"role": "user", "content": json.dumps({
        "stable_builder_context": {"output_contract": {"schema": contract},
                                   "semantic_invariants": semantic_generation_guidance()}})},
        {"role": "user", "content": json.dumps({"builder_request": {"instruction": "Сохрани требования",
            "prototype_brief": {"requirements": ["exact"], "required_refs": ["req:1"]}}})}]
    if candidate:
        result.append({"role": "user", "content": json.dumps({"semantic_repair": {
            "candidate": candidate, "repair_scope": scope, "validation_findings": [{"code": "exact"}],
            "instruction": "original", "prototype_brief": {"requirements": ["exact"]}}})})
    return result


def fixture():
    return {"schema": "adaos.builder.semantic_prototype_candidate.v2", "title": {"ru": "Test"},
        "layout": "master_detail", "resources": [
            {"id": "a", "fields": [{"id": "fk", "value_type": "short_text"}], "records": [{"id": "a1", "values": ["b1"]}]},
            {"id": "b", "fields": [], "records": [{"id": "b1", "values": []}]},
            {"id": "c", "fields": [], "records": []}],
        "relationships": [{"id": "rel", "from_resource_ref": "a", "to_resource_ref": "b"}],
        "views": [{"id": "va", "resource_ref": "a"}, {"id": "vc", "resource_ref": "c"}],
        "representative_states": [{"id": "sa", "view_ref": "va", "proof": {"kind": "query_empty"}}, {"id": "sc", "view_ref": "vc"}],
        "commands": [{"id": "immutable"}], "bindings": [{"id": "immutable"}]}


def test_full_is_byte_preserving_and_does_not_alias():
    original = messages()
    result, receipt = compile_messages(original, strategy="full", retrieval_available=True)
    assert result == original
    assert result is not original
    assert receipt["input_sha256"] == receipt["output_sha256"]


def test_kernel_is_exact_and_task_authority_is_unchanged():
    original = messages()
    before = copy.deepcopy(original)
    result, receipt = compile_messages(original, strategy="hybrid", retrieval_available=True)
    stable = json.loads(result[1]["content"])["stable_builder_context"]
    assert set(stable["semantic_invariants"]) == KERNEL
    assert all(value == semantic_generation_guidance()[key] for key, value in stable["semantic_invariants"].items())
    assert json.loads(result[2]["content"]) == json.loads(original[2]["content"])
    assert original == before
    assert receipt["output_chars"] < receipt["input_chars"]
    assert not set(item["id"] for item in stable["prototype_retrieval"]["dimensions"]) & KERNEL


@pytest.mark.parametrize("available,unknown", [(False, False), (True, True)])
def test_unknown_guidance_or_unavailable_tools_falls_back(available, unknown):
    original = messages()
    if unknown:
        body = json.loads(original[1]["content"])
        body["stable_builder_context"]["semantic_invariants"]["unknown"] = "must keep"
        original[1]["content"] = json.dumps(body)
    result, receipt = compile_messages(original, strategy="hybrid-focused", retrieval_available=available)
    assert result[1] == original[1]
    assert receipt["fallbacks"]


def test_focused_state_repair_keeps_complete_connected_data_and_exact_scope():
    candidate = fixture()
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"],
             "state_contexts": [{"resource_id": "a", "fields": ["exact"]}], "task": "exact task"}
    original = messages(candidate, scope)
    result, receipt = compile_messages(original, strategy="hybrid-focused", retrieval_available=True)
    repair = json.loads(result[-1]["content"])["semantic_repair"]
    assert "candidate" not in repair
    context = repair["candidate_context"]
    assert context["resources"] == candidate["resources"][:2]
    assert context["relationships"] == candidate["relationships"]
    assert context["views"] == candidate["views"][:1]
    assert context["representative_states"] == candidate["representative_states"][:1]
    assert repair["repair_scope"] == scope
    assert repair["prototype_brief"] == {"requirements": ["exact"]}
    assert receipt["focused_repairs"]
    assert json.loads(original[-1]["content"])["semantic_repair"]["candidate"] == candidate


@pytest.mark.parametrize("contract,base", [("adaos.builder.binding_repair.v1", "valid"),
    ("adaos.builder.state_repair.v99", "valid"), ("adaos.builder.state_repair.v3", "stale")])
def test_unsupported_or_stale_repair_retains_full_candidate(contract, base):
    candidate = fixture()
    scope = {"base_sha256": digest(candidate)[7:] if base == "valid" else "0" * 64,
             "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"]}
    result, receipt = compile_messages(messages(candidate, scope, contract), strategy="hybrid-focused", retrieval_available=True)
    assert json.loads(result[-1]["content"])["semantic_repair"]["candidate"] == candidate
    assert not receipt["focused_repairs"]


def test_invalid_strategy_is_not_silently_accepted():
    with pytest.raises(ValueError):
        compile_messages(messages(), strategy="typo")


def test_selection_ancestor_and_initial_record_remain_in_repair_context():
    candidate = fixture()
    candidate["views"][0]["selection_filter"] = {"source_view_ref": "vb"}
    candidate["views"].append({"id": "vb", "resource_ref": "b", "initial_selection_id": "b1"})
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"]}
    result, _ = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    context = json.loads(result[-1]["content"])["semantic_repair"]["candidate_context"]
    assert context["views"] == [candidate["views"][0], candidate["views"][2]]


def test_command_dependent_proof_keeps_full_candidate():
    candidate = fixture()
    candidate["representative_states"][0]["proof"]["kind"] = "invalid_form"
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"]}
    result, receipt = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    assert json.loads(result[-1]["content"])["semantic_repair"]["candidate"] == candidate
    assert not receipt["focused_repairs"]


def test_fixture_expansion_closes_additional_selection_ancestors():
    candidate = fixture()
    candidate["views"].append({"id": "vb", "resource_ref": "b", "selection_filter": {"source_view_ref": "vc"}})
    candidate["representative_states"].append({"id": "sb", "view_ref": "vb", "proof": {"kind": "query_empty"}})
    candidate["representative_states"][1]["proof"] = {"kind": "query_empty"}
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"],
             "fixture_scope": [{"resource_ref": "a"}]}
    result, receipt = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    context = json.loads(result[-1]["content"])["semantic_repair"]["candidate_context"]
    assert receipt["focused_repairs"]
    assert context["views"] == candidate["views"]
    assert context["resources"] == candidate["resources"]
    assert context["representative_states"] == candidate["representative_states"]


def test_fixture_expansion_with_command_dependent_constraint_falls_back():
    candidate = fixture()
    candidate["views"].append({"id": "vb", "resource_ref": "b"})
    candidate["representative_states"].append({"id": "sb", "view_ref": "vb", "proof": {"kind": "invalid_form"}})
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"],
             "fixture_scope": [{"resource_ref": "a"}]}
    result, receipt = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    assert "candidate" in json.loads(result[-1]["content"])["semantic_repair"]
    assert not receipt["focused_repairs"]


def test_malformed_candidate_does_not_break_delivery():
    candidate = fixture()
    candidate["views"][0].pop("id")
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"]}
    result, receipt = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    assert "candidate" in json.loads(result[-1]["content"])["semantic_repair"]
    assert not receipt["focused_repairs"]


def test_layout_guidance_uses_exact_contract_namespaces():
    from adaos.sdk.builder.prototype import semantic_candidate_contract
    guidance = semantic_generation_guidance()["layout_regions"]
    assert guidance["legacy_string"]["allowed_view_region_roles"] == ["actions", "primary", "supporting"]
    assert guidance["explicit_object"]["allowed_region_roles"] == semantic_candidate_contract(version="v2")["$defs"]["layoutRegion"]["properties"]["role"]["enum"]
    assert "secondary" not in guidance["explicit_object"]["allowed_region_roles"]


def test_raw_source_side_selection_graph_retains_full_repair_evidence():
    candidate = fixture()
    candidate["views"][0]["selection_links"] = [{"target_view_ref": "vc"}]
    scope = {"base_sha256": digest(candidate)[7:], "allowed_view_ids": ["va"], "allowed_state_ids": ["sa"]}
    result, receipt = compile_messages(messages(candidate, scope), strategy="hybrid-focused", retrieval_available=True)
    assert not receipt["focused_repairs"]
    assert json.loads(result[-1]["content"])["semantic_repair"]["candidate"] == candidate


@pytest.mark.parametrize("contract,scope", [
    ("adaos.builder.collection_repair.v1", {"collection_contexts": [{"resource_ref": "a"}], "existing_view_ids": ["va", "vc"]}),
    ("adaos.builder.record_repair.v1", {"record_contexts": [{"resource_ref": "a", "record_id": "a1", "field_refs": ["fk"]}]}),
    ("adaos.builder.reference_repair.v1", {"allowed_relationship_fields": [{"relationship_ref": "rel", "field": "to_field_ref", "values": ["id"]}]}),
])
def test_other_bounded_repairs_keep_typed_relationship_closure(contract, scope):
    candidate = fixture()
    scope = {"base_sha256": digest(candidate)[7:], **scope}
    original = messages(candidate, scope, contract)
    result, receipt = compile_messages(original, strategy="hybrid-focused", retrieval_available=True)
    repair = json.loads(result[-1]["content"])["semantic_repair"]
    assert receipt["focused_repairs"]
    assert repair["candidate_context"]["resources"] == candidate["resources"][:2]
    assert repair["candidate_context"]["relationships"] == candidate["relationships"]
    assert repair["repair_scope"] == scope


@pytest.mark.parametrize("source", ["workspace", "installed"])
def test_native_installed_message_hook_is_opt_in_and_legacy_safe(monkeypatch, source):
    import ast
    import os
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Mapping, Any
    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
    repo = Path(__file__).parents[1]
    skills_root = repo / ".adaos/workspace/skills"
    if source == "workspace":
        path = skills_root / "builder_skill/handlers/main.py"
    else:
        if not (skills_root / ".runtime/builder_skill/current_runtime.json").exists():
            pytest.skip("Installed Builder runtime fixture absent")
        runtime = SkillRuntimeEnvironment(skills_root=skills_root, skill_name="builder_skill")
        version = runtime.resolve_active_version()
        if not version:
            pytest.skip("Installed Builder runtime inactive")
        slot = runtime.build_slot_paths(version, runtime.read_active_slot(version))
        path = slot.src_dir / "skills/builder_skill/handlers/main.py"
    if not path.exists():
        pytest.skip("Installed Builder integration fixture absent")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_builder_llm_messages")
    namespace = {"Mapping": Mapping, "Any": Any, "os": os, "_compact_json": json.dumps,
                 "sdk_builder_prototype": SimpleNamespace()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    request = {"system_prompt": "policy", "stable_user_prompt": "stable"}
    monkeypatch.delenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", raising=False)
    original, purposes = namespace["_builder_llm_messages"](request, "task")
    assert [item["content"] for item in original] == ["policy", "stable", "task"]
    monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "full")
    assert namespace["_builder_llm_messages"](request, "task") == (original, purposes)
    monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY", "hybrid-focused")
    with pytest.raises(RuntimeError, match="current Builder SDK"):
        namespace["_builder_llm_messages"](request, "task")
    calls = []
    namespace["sdk_builder_prototype"] = SimpleNamespace(compile_generation_messages=lambda values: (calls.append(values) or values, {}))
    delivered, delivered_purposes = namespace["_builder_llm_messages"](request, "task")
    assert delivered == original
    assert delivered_purposes == purposes
    assert len(calls) == 1
    request["capability_user_prompt"] = "capability context"
    request["dynamic_request"] = {"active_turn": {"instruction": "keep exact"}}
    delivered, delivered_purposes = namespace["_builder_llm_messages"](request, "task")
    assert [item["content"] for item in delivered[:-1]] == ["policy", "stable", "capability context", "task"]
    assert json.loads(delivered[-1]["content"]) == {"active_turn": {"instruction": "keep exact"}}
    assert delivered_purposes == ["system_policy", "stable_context", "capability_context", "user_delta", "active_turn_recap"]
    assert len(calls) == 2
    # Exercise the real SDK compiler as well as the call-boundary stub. No
    # provider/inference is invoked by this local context projection.
    from adaos.sdk.builder import prototype as facade
    from adaos.services.builder import prototype_mcp
    namespace["sdk_builder_prototype"] = facade
    monkeypatch.setattr(prototype_mcp, "enabled", lambda: True)
    structured = messages()
    projected, projected_purposes = namespace["_builder_llm_messages"]({
        "system_prompt": structured[0]["content"], "stable_user_prompt": structured[1]["content"]}, structured[2]["content"])
    assert set(json.loads(projected[1]["content"])["stable_builder_context"]["semantic_invariants"]) == KERNEL
    assert projected[0] == structured[0] and projected[-1] == structured[-1]
    assert projected_purposes == purposes
