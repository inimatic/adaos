import copy
import json

import pytest

from adaos.sdk.builder.prototype import semantic_generation_guidance
from adaos.services.builder.prototype_pipeline import compile_messages
from adaos.services.builder.prototype_task_context import ORIENTATION_KERNEL, select_task_fragments, task_evidence


def messages(instruction="Search records and edit a selected customer's linked history on mobile", **facts):
    return [{"role": "system", "content": "Policy unchanged"},
        {"role": "user", "content": json.dumps({"stable_builder_context": {
            "semantic_invariants": semantic_generation_guidance(), "output_contract": {"schema": "candidate"},
            "other_authority": {"exact": True}}}, indent=2)},
        {"role": "user", "content": json.dumps({"builder_request": {"instruction": instruction,
            "prototype_brief": {"required_references": ["operation:1"], "facts": ["exact amount 12.5"]}, **facts}}, indent=2)}]


@pytest.mark.parametrize("strategy", ["core-indexed", "task-aware"])
def test_projection_preserves_exact_dynamic_messages_contract_and_authority(strategy):
    original = messages()
    before = copy.deepcopy(original)
    delivered, receipt = compile_messages(original, strategy=strategy, retrieval_available=True)
    assert original == before
    assert delivered[0] == original[0] and delivered[2] == original[2]
    stable = json.loads(delivered[1]["content"])["stable_builder_context"]
    assert stable["other_authority"] == {"exact": True}
    assert stable["output_contract"] == {"schema": "candidate"}
    exact = semantic_generation_guidance()
    assert all(stable["semantic_invariants"][key] == exact[key] for key in ORIENTATION_KERNEL)
    inline = set(receipt["task_selections"][0]["inline_fragment_ids"])
    assert not inline & {part["id"] for part in stable["prototype_retrieval"]["dimensions"]}
    assert all(part["read_when"] for part in stable["prototype_retrieval"]["dimensions"])
    assert stable["platform_accents"]["library_digest"] == stable["prototype_retrieval"]["library_digest"]
    for key, value in stable["semantic_invariants"].items():
        if key == "ux_recommendations":
            assert all(value[child] == exact[key][child] for child in value)
        else:
            assert value == exact[key]


def test_router_is_task_specific_bilingual_and_not_driven_by_guide_prose():
    selected, _ = select_task_fragments(task_evidence(messages("Поиск и фильтры, связанные записи, редактирование, пустое состояние на телефоне")))
    assert {"query_filters", "selection_links", "field_validation", "state_rules", "ux.layout"} <= set(selected)
    assert "media" not in selected
    plain = messages("Display records")
    plain[0]["content"] = "Search filter upload media " * 100
    selected, _ = select_task_fragments(task_evidence(plain))
    assert "media" not in selected and "query_filters" not in selected
    revised, _ = select_task_fragments(task_evidence(messages("Display records", current_semantic={"resources": []})))
    assert "revision_scope" in revised


@pytest.mark.parametrize("instruction", [
    "Search books and filter by genre, with a sample having no results",
    "Каталог книг: поиск по названию, фильтр по жанру, пример отсутствия результатов",
    "Display a searchable catalog",
])
def test_query_rules_close_over_state_proof_constraints_without_state_keywords(instruction):
    selected, reasons = select_task_fragments(task_evidence(messages(instruction)))
    assert {"query_filters", "state_rules", "state_proofs"} <= set(selected)
    assert any(reason["signal"] == "required_by:query_filters" for reason in reasons)


def test_query_dependency_does_not_expand_unrelated_read_only_context():
    selected, _ = select_task_fragments(task_evidence(messages("Display a static catalog")))
    assert not {"query_filters", "state_rules", "state_proofs"} & set(selected)


def test_oracle_is_explicit_exact_and_cannot_invent_fragments():
    source = messages()
    projected, receipt = compile_messages(source, strategy="oracle", retrieval_available=True,
        oracle_fragment_ids=["query_filters", "ux.query_toolbar"])
    invariant = json.loads(projected[1]["content"])["stable_builder_context"]["semantic_invariants"]
    assert set(invariant) == ORIENTATION_KERNEL | {"query_filters", "ux_recommendations"}
    assert receipt["task_selections"][0]["selected_fragment_ids"] == ["query_filters", "ux.query_toolbar"]
    for identities in (None, [], ["unknown"], "query_filters", [False]):
        with pytest.raises(ValueError):
            compile_messages(source, strategy="oracle", retrieval_available=True, oracle_fragment_ids=identities)


@pytest.mark.parametrize("strategy", ["core-indexed", "task-aware"])
@pytest.mark.parametrize("problem", ["retrieval_unavailable", "unknown_authority", "task_missing"])
def test_uncertainty_returns_byte_preserving_full_not_a_silent_partial_projection(strategy, problem):
    source = messages()
    if problem == "unknown_authority":
        body = json.loads(source[1]["content"])
        body["stable_builder_context"]["semantic_invariants"]["unknown"] = "Must keep"
        source[1]["content"] = json.dumps(body, indent=2)
    if problem == "task_missing":
        source.pop()
    result, receipt = compile_messages(source, strategy=strategy, retrieval_available=problem != "retrieval_unavailable")
    assert result == source and receipt["input_sha256"] == receipt["output_sha256"]
    assert receipt["fallbacks"]


def test_repair_routes_from_original_task_without_treating_candidate_as_new_authority():
    source = messages("Display records")
    task = json.loads(source.pop()["content"])
    source.append({"role": "user", "content": json.dumps({"original_request": task,
        "semantic_repair": {"validation_findings": [{"code": "state_query_empty_unreachable"}],
            "candidate": {"title": "upload image filter"}}}, indent=2)})
    selected, _ = select_task_fragments(task_evidence(source))
    assert "state_rules" in selected and "media" not in selected
    result, _ = compile_messages(source, strategy="task-aware", retrieval_available=True)
    assert result[-1] == source[-1]


def test_known_index_direct_read_is_advertised_without_an_artificial_tool_quota():
    result, _ = compile_messages(messages(), strategy="core-indexed", retrieval_available=True)
    policy = json.loads(result[1]["content"])["stable_builder_context"]["prototype_retrieval"]["policy"]
    assert "1–8" in policy and "Do not search" in policy
    assert "Do not call tools merely to satisfy a quota" in policy


def test_brief_known_value_markers_do_not_create_a_fake_state_feature():
    evidence = {"requests": [{"instruction": "Display records", "prototype_brief": {
        "representative_states": {"state": "unknown", "value": None},
        "facts": {"state": "known", "value": "Plain records"}}}]}
    selected, _ = select_task_fragments(evidence)
    assert "state_rules" not in selected
    evidence["requests"][0]["prototype_brief"]["operations"] = [{"kind": "create", "statement": "New record"}]
    selected, _ = select_task_fragments(evidence)
    assert "field_validation" in selected


@pytest.mark.parametrize("instruction", [
    "Display a static catalog",
    "Search and filter linked customer history; edit fields, attachments, mobile tabs, no results",
    "Поиск, фильтры, связанные записи, редактирование, пустые состояния и вкладки на телефоне",
])
def test_compact_context_has_bounded_exact_fragments_and_complete_decision_index(instruction):
    from adaos.services.builder.prototype_task_context import COMPACT_INLINE_CHAR_BUDGET, COMPACT_KERNEL
    from adaos.services.builder.prototype_mcp import library_snapshot
    from adaos.services.builder_context import canonical
    original = messages(instruction)
    delivered, receipt = compile_messages(original, strategy="task-aware-compact", retrieval_available=True)
    assert delivered[0] == original[0] and delivered[2] == original[2]
    stable = json.loads(delivered[1]["content"])["stable_builder_context"]
    exact, projected = semantic_generation_guidance(), stable["semantic_invariants"]
    assert len(canonical(projected)) <= COMPACT_INLINE_CHAR_BUDGET
    assert all(projected[key] == exact[key] for key in COMPACT_KERNEL)
    # Do not drop bounds under the mistaken assumption strict provider schemas
    # still carry assertions stripped by semantic_prototype_provider_contract.
    assert projected["authoring_constraints"] == exact["authoring_constraints"]
    for key, value in projected.items():
        if key == "ux_recommendations":
            assert all(value[child] == exact[key][child] for child in value)
        else:
            assert value == exact[key]
    selection = receipt["task_selections"][0]
    inline = set(selection["inline_fragment_ids"])
    indexed = [identity for group in stable["prototype_retrieval"]["dimensions"] for identity in group["ids"]]
    assert not inline & set(indexed)
    assert len(indexed) == len(set(indexed))
    assert inline | set(indexed) == {part["id"] for part in library_snapshot()["fragments"]}
    assert all(group["read_when"] and group["keywords"] for group in stable["prototype_retrieval"]["dimensions"])
    if "query_filters" in inline:
        assert {"state_rules", "state_proofs"} <= inline
    assert selection["compact_delivery_chars"] < len(canonical(exact)) * 0.66
    assert stable["output_contract"] == {"schema": "candidate"}


@pytest.mark.parametrize("problem", ["retrieval_unavailable", "unknown_authority", "task_missing"])
def test_compact_context_falls_back_without_losing_task_or_authority(problem):
    source = messages()
    if problem == "unknown_authority":
        body = json.loads(source[1]["content"])
        body["stable_builder_context"]["semantic_invariants"]["new_rule"] = "must retain"
        source[1]["content"] = json.dumps(body)
    elif problem == "task_missing":
        source.pop()
    result, receipt = compile_messages(source, strategy="task-aware-compact", retrieval_available=problem != "retrieval_unavailable")
    assert result == source and receipt["fallbacks"]
