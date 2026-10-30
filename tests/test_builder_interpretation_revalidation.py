import copy
import json

import pytest

from adaos.services.builder.task_interpretation import TaskInterpretationRejected
from adaos.services.builder.task_pipeline import prepare_generation_request
from adaos.services.builder.interpretation_revalidation import (
    recover_rejected_first_interpretation,
)
from adaos.services.builder_intent import capture_intent
from test_builder_task_interpretation import prepared
from test_builder_task_pipeline import Provider, invocation


def rejected_by_old_core(directory, monkeypatch):
    from adaos.services.builder import task_interpretation

    original = task_interpretation._constraint_literal
    monkeypatch.setattr(
        task_interpretation,
        "_constraint_literal",
        lambda text, kind, value: False
        if kind == "viewport"
        else original(text, kind, value),
    )
    provider = Provider(copy.deepcopy(prepared()[2]))
    with pytest.raises(TaskInterpretationRejected):
        invocation(directory, provider)
    path = next(directory.glob("builder-interpretation-*.json"))
    before = path.read_bytes()
    monkeypatch.setattr(task_interpretation, "_constraint_literal", original)
    return provider, path, before


def test_fixed_core_revalidates_the_original_result_without_relabelling_or_respending(
    tmp_path, monkeypatch
):
    provider, path, before = rejected_by_old_core(tmp_path, monkeypatch)
    accepted = invocation(tmp_path, provider)
    assert accepted["generation_plan"]["route"] == "single_stage"
    receipt = accepted["interpretation_receipt"]
    assert (
        receipt["provider_calls"] == 0
        and receipt["historical_result_corrected"] is False
    )
    assert receipt["revalidated_interpretation_job_id"] == "same-job"
    assert path.read_bytes() == before
    assert json.loads(before)["admission"]["status"] == "rejected"
    reviews = list(tmp_path.glob("interpretation-validation-*.json"))
    assert (
        len(reviews) == 1
        and len(provider.submissions) == len(provider.observations) == 1
    )
    assert invocation(tmp_path, provider) == accepted
    assert len(list(tmp_path.glob("interpretation-validation-*.json"))) == 1
    assert path.read_bytes() == before and len(provider.submissions) == 1


def test_validated_meaning_keeps_the_unmaterialized_project_adopted_when_general_switch_is_off(
    tmp_path, monkeypatch
):
    provider, path, before = rejected_by_old_core(tmp_path, monkeypatch)
    invocation(tmp_path, provider)
    old = prepared()[0]
    intent = capture_intent(
        old["statement"],
        locale=old["locale"],
        project_ref=old["scope"]["project_ref"],
        source={"kind": "chat", "ref": "new-trusted-turn"},
    )
    request = {
        "output_mode": "semantic_v2",
        "prototype_brief": {},
        "base_request": {},
        "dynamic_request": {
            "instruction": intent["statement"],
            "scenario_id": "qualification",
        },
    }
    result = prepare_generation_request(
        request,
        metadata={
            "builder_task_pipeline": "0",
            "builder_ai_resource_ref": "ai-resource:personal-chatgpt",
            "prototype_intent": intent,
        },
        model="exact-selected-model",
        reasoning={"effort": "low"},
        provider=provider,
        journal_dir=tmp_path,
    )
    assert result["generation_plan"]["route"] == "single_stage"
    assert result["interpretation_receipt"]["provider_calls"] == 0
    assert len(provider.submissions) == 1 and path.read_bytes() == before


def test_genuinely_wrong_proposal_remains_rejected_and_is_not_repaired_by_revalidation(
    tmp_path,
):
    proposal = prepared()[2]
    proposal["facts"][0]["value"] = 0
    provider = Provider(proposal)
    for _ in range(2):
        with pytest.raises(TaskInterpretationRejected):
            invocation(tmp_path, provider)
    assert len(provider.submissions) == 1 and not list(
        tmp_path.glob("interpretation-validation-*.json")
    )


def test_tampered_original_context_cannot_authorize_zero_call_revalidation(
    tmp_path, monkeypatch
):
    provider, path, _ = rejected_by_old_core(tmp_path, monkeypatch)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["messages"][0]["content"] += " Altered context."
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert (
        caught.value.findings[0]["code"] == "task.revalidation_input_identity_invalid"
    )
    assert len(provider.submissions) == 1
    assert not list(tmp_path.glob("interpretation-validation-*.json"))


def test_unverified_current_seed_cannot_buy_generation_from_incomplete_persisted_meaning(
    tmp_path, monkeypatch
):
    from adaos.services.builder import task_pipeline
    from adaos.services.builder.task_interpretation import admit_interpretation
    from adaos.services.builder.task_spec import compile_task_spec, seal

    intent, task, proposal = prepared()
    _, brief = admit_interpretation(task, proposal, intent)
    brief["task_contract"]["facts"] = brief["task_contract"]["facts"][:1]
    brief = seal(brief, id_key="brief_id", prefix="brief:")
    accepted = compile_task_spec(intent, brief)
    monkeypatch.setattr(task_pipeline, "_retained_task", lambda *args: accepted)
    provider = Provider(proposal)
    request = {
        "output_mode": "semantic_v2",
        "prototype_brief": {},
        "base_request": {},
        "dynamic_request": {
            "instruction": intent["statement"],
            "scenario_id": "qualification",
        },
    }
    with pytest.raises(TaskInterpretationRejected) as caught:
        prepare_generation_request(
            request,
            metadata={
                "builder_task_pipeline": "1",
                "prototype_intent": intent,
                "prototype_brief": brief,
                "builder_ai_resource_ref": "ai-resource:personal-chatgpt",
            },
            model="exact-selected-model",
            reasoning={"effort": "low"},
            provider=provider,
            journal_dir=tmp_path,
        )
    assert any(
        row["code"] == "task.source_record_attribute_unextracted"
        for row in caught.value.findings
    )
    assert provider.submissions == []


@pytest.mark.parametrize(
    "scope",
    [
        "accepted_history",
        "materialized_baseline",
        "different_project",
        "changed_authority",
    ],
)
def test_revalidation_does_not_widen_its_first_task_source_or_authority_scope(
    tmp_path, monkeypatch, scope
):
    provider, path, before = rejected_by_old_core(tmp_path, monkeypatch)
    intent = prepared()[0]
    previous = None
    baseline = None
    if scope == "accepted_history":
        previous = {"existing": "history"}
    elif scope == "materialized_baseline":
        baseline = {"existing": "document"}
    else:
        from adaos.services.builder.task_spec import seal

        if scope == "different_project":
            intent["scope"]["project_ref"] = "project:foreign"
        else:
            intent["authority"]["runtime_scope"] = "automation"
        intent = seal(intent, id_key="intent_id", prefix="intent:")

    def forbidden(*args):
        raise AssertionError("Foreign/broader meaning cannot reach replay")

    assert (
        recover_rejected_first_interpretation(
            intent,
            previous_brief=previous,
            journal_dir=tmp_path,
            current_seed={},
            current_semantic=baseline,
            exact_replay=forbidden,
        )
        is None
    )
    assert path.read_bytes() == before and len(provider.submissions) == 1
    assert not list(tmp_path.glob("interpretation-validation-*.json"))
