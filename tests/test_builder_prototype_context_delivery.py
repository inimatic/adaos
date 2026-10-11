import copy
import json

import pytest

from adaos.services.builder.prototype_context_delivery import compile_prototype_messages
from adaos.sdk.builder.prototype import semantic_generation_guidance, semantic_provider_contract


def messages(instruction="Inventory with filters and purchase requests", **extra):
    return [
        {"role": "system", "content": "Exact owner and authority policy"},
        {"role": "user", "content": json.dumps({"stable_builder_context": {
            "semantic_invariants": semantic_generation_guidance()}}, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"builder_request": {
            "instruction": instruction, "prototype_brief": {"required_references": [{"id": "job:1"}]}, **extra}})},
    ]


def invariants(value):
    return json.loads(value[1]["content"])["stable_builder_context"]["semantic_invariants"]


def test_full_is_byte_preserving_and_does_not_mutate_inputs():
    original = messages()
    before = copy.deepcopy(original)
    result, receipt = compile_prototype_messages(original)
    assert result == before == original
    assert receipt["strategy"] == "full"
    assert not receipt["omitted"]


def test_adaptive_only_omits_declared_advisory_entries():
    original = messages()
    result, receipt = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert result[0] == original[0] and result[2] == original[2]
    before, after = invariants(original), invariants(result)
    for key, value in before.items():
        if key not in {"ux_recommendations", "media", "revision_scope"}:
            assert after[key] == value
    assert receipt["must_keep_complete"] and receipt["omitted"]
    assert receipt["estimated_tokens"] < receipt["full_estimated_tokens"]
    assert invariants(original) == before


@pytest.mark.parametrize("instruction", ["View video attachments", "Покажи изображения и вложения", "Upload a file"])
def test_requested_media_invariants_are_mandatory(instruction):
    original = messages(instruction)
    result, _ = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert invariants(result)["media"] == invariants(original)["media"]


def test_baseline_revision_scope_is_mandatory_even_in_a_repair():
    original = messages(current_semantic={"resources": []})
    original[-1]["content"] = json.dumps({"original_request": json.loads(original[-1]["content"]), "validation_error": {"code": "example"}})
    result, _ = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert result[-1] == original[-1]
    assert invariants(result)["revision_scope"] == invariants(original)["revision_scope"]


def test_new_platform_guidance_is_never_silently_pruned():
    original = messages()
    body = json.loads(original[1]["content"])
    body["stable_builder_context"]["semantic_invariants"].update(new_authority_rule="Exact future invariant")
    body["stable_builder_context"]["semantic_invariants"]["ux_recommendations"]["new_recommendation"] = "Unknown hint remains required"
    original[1]["content"] = json.dumps(body)
    result, _ = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert invariants(result)["new_authority_rule"] == "Exact future invariant"
    assert invariants(result)["ux_recommendations"]["new_recommendation"] == "Unknown hint remains required"


def test_capability_catalog_prose_cannot_route_its_own_hints():
    original = messages()
    original.insert(2, {"role": "user", "content": json.dumps({"semantic_capability_context": {
        "summary": "video image attachments file upload", "policy": "media is available"}})})
    result, receipt = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert "media" not in invariants(result)
    assert any(item["key"] == "media" for item in receipt["omitted"])


@pytest.mark.parametrize("interface", [{"region_content_budgets": {"main": {"max_table_columns": 4}}}, {"capability_refs": ["layout.workbench"]}])
def test_explicit_interface_contract_falls_back_to_complete_guidance(interface):
    original = messages()
    body = json.loads(original[-1]["content"])
    body["builder_request"]["prototype_brief"]["interface_context"] = interface
    original[-1]["content"] = json.dumps(body)
    result, receipt = compile_prototype_messages(original, strategy="adaptive", hint_budget_tokens=0)
    assert result == original
    assert receipt["reason"] == "accepted_interface_contract"


@pytest.mark.parametrize("original", [[{"role": "user", "content": "Not semantic"}], [{"role": "user", "content": [{"type": "input_image", "image_url": "opaque"}]}]])
def test_unsupported_input_falls_back_intact(original):
    result, receipt = compile_prototype_messages(original, strategy="adaptive")
    assert result == original and receipt["status"] == "full_fallback"


def test_provider_schema_is_in_the_budget_and_never_mutated():
    schema = semantic_provider_contract(version="v2", locales=("ru",))
    options = {"text": {"format": {"schema": schema}}}
    before = copy.deepcopy(options)
    _, without = compile_prototype_messages(messages(), strategy="adaptive")
    _, with_schema = compile_prototype_messages(messages(), strategy="adaptive", generation_options=options)
    assert options == before
    assert with_schema["estimated_tokens"] > without["estimated_tokens"]


def test_must_keep_overflow_blocks_instead_of_truncating():
    from adaos.services.builder_context import ContextBudgetExceeded
    with pytest.raises(ContextBudgetExceeded):
        compile_prototype_messages(messages(), strategy="adaptive", capacity_tokens=1000, reserve_tokens=100)


def test_repair_payload_growth_stays_in_budget():
    from adaos.services.builder_context import ContextBudgetExceeded
    original = messages()
    original[-1]["content"] = json.dumps({"previous_response": "x" * 10000, "original_request": json.loads(original[-1]["content"])})
    with pytest.raises(ContextBudgetExceeded):
        compile_prototype_messages(original, strategy="adaptive", capacity_tokens=5000, reserve_tokens=100)


@pytest.mark.parametrize("kwargs", [{"strategy": "core"}, {"hint_budget_tokens": -1}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        compile_prototype_messages(messages(), **kwargs)
