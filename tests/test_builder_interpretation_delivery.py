import copy
import json

import pytest
from jsonschema import Draft202012Validator

from test_builder_task_interpretation import prepared
from test_builder_task_pipeline import Provider, invocation
from adaos.services.builder.interpretation_delivery import (
    request_packet,
    decode_proposal,
    TYPED_NAME,
    TEXT_NAME,
)
from adaos.services.builder.task_interpretation import (
    TaskInterpretationRejected,
    interpretation_contract,
)


def record(proposal, name):
    value = (
        proposal
        if name == TYPED_NAME
        else {"proposal_json": json.dumps(proposal, ensure_ascii=False)}
    )
    return {
        "options": {"text": {"format": {"name": name}}},
        "result": {"output_text": json.dumps(value, ensure_ascii=False)},
    }


def test_default_packet_is_typed_and_text_profile_has_complete_source_contract_in_user_evidence(
    monkeypatch,
):
    _, task, _ = prepared()
    monkeypatch.delenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", raising=False)
    original = request_packet(task)
    assert original["wire_name"] == TYPED_NAME
    assert original["wire_schema"] == interpretation_contract(provider=True, task=task)
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "json_text_envelope")
    projected = request_packet(task)
    data = json.loads(projected["messages"][-1]["content"])
    assert projected["messages"][-1]["role"] == "user"
    assert data["interpretation_contract"] == original["wire_schema"]
    assert all(
        value == data[key]
        for key, value in json.loads(original["messages"][-1]["content"]).items()
    )
    assert "Return only the typed proposal" not in projected["messages"][0]["content"]
    assert "exactly proposal_json" in projected["messages"][0]["content"]
    Draft202012Validator.check_schema(projected["wire_schema"])
    assert projected["wire_name"] == TEXT_NAME


@pytest.mark.parametrize("name", [TYPED_NAME, TEXT_NAME])
def test_decoder_uses_retained_wire_identity_not_current_mode(monkeypatch, name):
    proposal = prepared()[2]
    frozen = record(proposal, name)
    before = copy.deepcopy(frozen)
    monkeypatch.setenv(
        "ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "unknown-current-value"
    )
    assert decode_proposal(frozen) == proposal and frozen == before


@pytest.mark.parametrize(
    "value",
    [
        {"proposal_json": {}, "extra": 1},
        {"proposal_json": "PRIVATE"},
        {"proposal_json": "{}", "extra": "PRIVATE"},
        "PRIVATE",
        None,
    ],
)
def test_text_envelope_rejects_missing_ambiguous_or_incomplete_inner_data(value):
    frozen = record({}, TEXT_NAME)
    frozen["result"]["output_text"] = json.dumps(value)
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal(frozen)
    assert caught.value.findings[0]["code"] == "task.interpretation_response_invalid"
    assert "PRIVATE" not in str(caught.value)


def test_text_profile_still_runs_full_canonical_and_literal_acceptance_and_exact_job_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "json_text_envelope")

    class Envelope(Provider):
        def wait(self, *args, **kwargs):
            value = super().wait(*args, **kwargs)
            value["output_text"] = json.dumps(
                {"proposal_json": value["output_text"]}, ensure_ascii=False
            )
            return value

    provider = Envelope(prepared()[2])
    accepted = invocation(tmp_path / "accepted", provider)
    assert accepted["task_spec"]["interpretation_status"] == "admitted"
    assert invocation(tmp_path / "accepted", provider) == accepted
    assert (
        len(provider.submissions) == 1
        and provider.submissions[0][1]["text"]["format"]["strict"] is True
    )
    changed = prepared()[2]
    changed["facts"][0]["value"] = 0
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path / "rejected", Envelope(changed))
    assert any(row["code"] == "task.fact_not_source" for row in caught.value.findings)
    assert not list((tmp_path / "rejected").glob("generation-plan-*.json"))


def test_unknown_profile_stops_before_submission(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "typo")
    provider = Provider(prepared()[2])
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert caught.value.findings[0]["gate"] == "G5" and provider.submissions == []


def test_missing_retained_transport_is_not_guessed_from_current_environment():
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal({"result": {"output_text": "{}"}})
    assert caught.value.findings[0]["code"] == "task.interpretation_transport_unknown"
    assert caught.value.findings[0]["gate"] == "G5"


@pytest.mark.parametrize(
    "fault,code",
    [
        ("source", "task.interpretation_source_mismatch"),
        ("kind", "task.interpretation_shape_invalid"),
        ("clause", "task.source_reference_invalid"),
        ("question", "task.clarification_required"),
    ],
)
def test_envelope_does_not_replace_canonical_semantic_and_source_gates(
    tmp_path, monkeypatch, fault, code
):
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "json_text_envelope")
    value = prepared()[2]
    if fault == "source":
        value["source_digest"] = "sha256:" + "0" * 64
    elif fault == "kind":
        value["requirements"][0]["kind"] = "invented"
    elif fault == "clause":
        value["requirements"][0]["clause_ids"] = ["clause:foreign"]
    else:
        value["questions"] = [
            {"clause_ids": ["clause:001"], "question": "Material question"}
        ]

    class Envelope(Provider):
        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            result["output_text"] = json.dumps({"proposal_json": result["output_text"]})
            return result

    provider = Envelope(value)
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert any(row["code"] == code for row in caught.value.findings)
    assert len(provider.submissions) == 1 and not list(
        tmp_path.glob("generation-plan-*.json")
    )
