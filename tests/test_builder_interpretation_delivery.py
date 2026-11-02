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
    BOUND_TEXT_NAME,
    BOUND_TYPED_NAME,
    CLAUSE_TYPED_NAME,
    BOUND_CLAUSE_TYPED_NAME,
    QUOTE_TYPED_NAME,
    BOUND_QUOTE_TYPED_NAME,
)
from adaos.services.builder.task_interpretation import (
    TaskInterpretationRejected,
    interpretation_contract,
)


def record(proposal, name):
    value = (
        proposal
        if name in {TYPED_NAME, BOUND_TYPED_NAME, CLAUSE_TYPED_NAME, BOUND_CLAUSE_TYPED_NAME,
                    QUOTE_TYPED_NAME, BOUND_QUOTE_TYPED_NAME}
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
    assert original["wire_name"] == QUOTE_TYPED_NAME
    assert original["wire_schema"] == interpretation_contract(provider=True, task=task, source_references=True, source_quotes=True)
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "json_text_envelope")
    projected = request_packet(task)
    data = json.loads(projected["messages"][-1]["content"])
    assert projected["messages"][-1]["role"] == "user"
    assert data["interpretation_contract"] == interpretation_contract(provider=True, task=task)
    typed_data = json.loads(original['messages'][-1]['content'])
    assert 'COMPLETE source clauses' in data['interpretation_request']['role_coverage_contract']['atomic_evidence']
    assert 'shortest complete exact source substring' in typed_data['interpretation_request']['role_coverage_contract']['atomic_evidence']
    comparable = copy.deepcopy(data)
    comparable.pop('interpretation_contract')
    for projection in (typed_data, comparable):
        projection['interpretation_request']['role_coverage_contract'].pop('atomic_evidence')
    assert typed_data == comparable
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


def bound_record(proposal):
    frozen = record(
        {
            key: value
            for key, value in proposal.items()
            if key not in {"schema", "source_digest"}
        },
        BOUND_TEXT_NAME,
    )
    frozen["task_spec"] = prepared()[1]
    return frozen


def test_bound_typed_profile_preserves_full_provider_grammar_without_service_constants(
    monkeypatch,
):
    from jsonschema import ValidationError

    _, task, proposal = prepared()
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_typed")
    packet = request_packet(task)
    assert packet["wire_name"] == BOUND_QUOTE_TYPED_NAME
    assert set(packet["wire_schema"]["properties"]) == set(
        interpretation_contract(provider=True, task=task)["properties"]
    ) - {"schema", "source_digest"}
    assert (
        "omitting ROOT properties schema and source_digest"
        in packet["messages"][0]["content"]
    )
    for row in proposal["requirements"]:
        row["statement"] = next(
            clause["text"].strip()
            for clause in task["clauses"]
            if clause["id"] == row["clause_ids"][0]
        )
    payload = {
        key: value
        for key, value in proposal.items()
        if key not in {"schema", "source_digest"}
    }
    for row in payload['requirements']:
        row['reaffirms'] = []
        row['source_clause'] = row['clause_ids'][0]
        row['statement_text'] = row.pop('statement')
    validator = Draft202012Validator(packet["wire_schema"])
    validator.validate(payload)
    wrong = copy.deepcopy(payload)
    wrong["clause_roles"][0]["roles"].append("ui")
    with pytest.raises(ValidationError):
        validator.validate(wrong)
    frozen = record(payload, BOUND_QUOTE_TYPED_NAME)
    frozen["task_spec"] = task
    decoded = decode_proposal(frozen)
    assert decoded['facts'] == proposal['facts']
    assert all(row['statement'] == next(c['text'].strip() for c in task['clauses'] if c['id']==row['clause_ids'][0])
               for row in decoded['requirements'])


@pytest.mark.parametrize("field", ["schema", "source_digest"])
def test_bound_typed_decoder_does_not_overwrite_even_matching_model_owned_root_fields(
    field,
):
    proposal = prepared()[2]
    payload = {
        key: value
        for key, value in proposal.items()
        if key not in {"schema", "source_digest"}
    }
    payload[field] = proposal[field]
    frozen = record(payload, BOUND_TYPED_NAME)
    frozen["task_spec"] = prepared()[1]
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal(frozen)
    assert caught.value.findings[0]["code"] == "task.interpretation_response_invalid"


def test_bound_typed_context_replay_and_same_turn_profile_change_are_separate(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_typed")

    class BoundTyped(Provider):
        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            payload = json.loads(result["output_text"])
            payload.pop("schema")
            payload.pop("source_digest")
            result["output_text"] = json.dumps(payload)
            return result

    provider = BoundTyped(prepared()[2])
    accepted = invocation(tmp_path, provider)
    assert invocation(tmp_path, provider) == accepted
    assert (
        len(provider.submissions) == 1
        and provider.submissions[0][1]["text"]["format"]["name"] == BOUND_QUOTE_TYPED_NAME
    )
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_json_text")
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert caught.value.findings[0]["code"] == "task.interpretation_ingress_changed"
    assert len(provider.submissions) == 1


def test_source_bound_packet_removes_only_core_owned_constant_fields(monkeypatch):
    _, task, _ = prepared()
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_json_text")
    packet = request_packet(task)
    assert packet["wire_name"] == BOUND_TEXT_NAME
    source_contract = interpretation_contract(provider=True, task=task)
    expected = copy.deepcopy(source_contract)
    for key in ("schema", "source_digest"):
        expected["properties"].pop(key)
        expected["required"].remove(key)
    projected = json.loads(packet["messages"][-1]["content"])
    assert projected["interpretation_contract"] == expected
    assert packet["messages"][-1]["role"] == "user"
    assert "MUST OMIT schema and source_digest" in packet["messages"][0]["content"]
    assert "source_digest" in source_contract["properties"]
    Draft202012Validator.check_schema(expected)


def test_source_binding_uses_frozen_task_and_keeps_original_result_bytes(monkeypatch):
    proposal = prepared()[2]
    frozen = bound_record(proposal)
    before = copy.deepcopy(frozen)
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "typed")
    assert decode_proposal(frozen) == proposal
    assert frozen == before


@pytest.mark.parametrize(
    "fault",
    [
        "not_constant",
        "conflicting_constant",
        "extra_assertion",
        "wrong_type",
        "not_required",
    ],
)
def test_bound_projection_refuses_drift_in_the_owned_constant_contract(
    monkeypatch, fault
):
    from adaos.services.builder import interpretation_delivery

    _, task, _ = prepared()
    contract = interpretation_contract(provider=True, task=task)
    field = contract["properties"]["source_digest"]
    if fault == "not_constant":
        field.pop("const", None)
        field.pop("enum", None)
    elif fault == "conflicting_constant":
        field["enum"] = ["another-source"]
    elif fault == "extra_assertion":
        field["minLength"] = 1000
    elif fault == "wrong_type":
        field["type"] = "number"
    else:
        contract["required"].remove("source_digest")
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_json_text")
    monkeypatch.setattr(
        interpretation_delivery, "interpretation_contract", lambda **kwargs: contract
    )
    with pytest.raises(TaskInterpretationRejected) as caught:
        request_packet(task)
    assert (
        caught.value.findings[0]["code"]
        == "task.interpretation_binding_projection_invalid"
    )
    assert caught.value.findings[0]["gate"] == "G5"


@pytest.mark.parametrize(
    "fault,code",
    [
        ("value", "task.fact_not_source"),
        ("kind", "task.interpretation_shape_invalid"),
        ("clause", "task.source_reference_invalid"),
        ("question", "task.clarification_required"),
    ],
)
def test_bound_source_header_cannot_certify_wrong_semantic_payload(fault, code):
    from adaos.services.builder.task_interpretation import admit_interpretation

    intent, task, proposal = prepared()
    if fault == "value":
        proposal["facts"][0]["value"] = 0
    elif fault == "kind":
        proposal["requirements"][0]["kind"] = "invented"
    elif fault == "clause":
        proposal["facts"][0]["clause_ids"] = ["clause:999"]
    else:
        proposal["questions"] = [
            {"clause_ids": ["clause:001"], "question": "Material decision?"}
        ]
    decoded = decode_proposal(bound_record(proposal))
    assert decoded["source_digest"] == intent["digest"]
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, decoded, intent)
    assert any(row["code"] == code for row in caught.value.findings)


@pytest.mark.parametrize("field", ["schema", "source_digest"])
@pytest.mark.parametrize("value", ["foreign-value", "same-as-binding"])
def test_bound_payload_cannot_supply_even_matching_authority_owned_fields(field, value):
    proposal = prepared()[2]
    payload = {
        key: item
        for key, item in proposal.items()
        if key not in {"schema", "source_digest"}
    }
    payload[field] = proposal[field] if value == "same-as-binding" else value
    frozen = record(payload, BOUND_TEXT_NAME)
    frozen["task_spec"] = prepared()[1]
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal(frozen)
    assert caught.value.findings[0]["code"] == "task.interpretation_response_invalid"


@pytest.mark.parametrize("fault", ["missing", "tampered", "nonhash"])
def test_missing_or_corrupt_frozen_binding_is_context_failure_not_model_repair(fault):
    from adaos.services.builder.task_spec import seal

    frozen = bound_record(prepared()[2])
    if fault == "missing":
        frozen.pop("task_spec")
    elif fault == "tampered":
        frozen["task_spec"]["source"]["digest"] = "PRIVATE"
    else:
        frozen["task_spec"]["source"]["digest"] = "PRIVATE"
        frozen["task_spec"] = seal(frozen["task_spec"])
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal(frozen)
    assert (
        caught.value.findings[0]["code"] == "task.interpretation_source_binding_invalid"
    )
    assert caught.value.findings[0]["gate"] == "G5"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("layer", ["outer", "inner"])
def test_bound_transport_rejects_duplicate_json_keys(layer):
    frozen = bound_record(prepared()[2])
    if layer == "outer":
        frozen["result"]["output_text"] = '{"proposal_json":"{}","proposal_json":"{}"}'
    else:
        frozen["result"]["output_text"] = json.dumps(
            {"proposal_json": '{"facts":[],"facts":[]}'}
        )
    with pytest.raises(TaskInterpretationRejected) as caught:
        decode_proposal(frozen)
    assert caught.value.findings[0]["code"] == "task.interpretation_response_invalid"


def test_bound_profile_preserves_canonical_literal_admission_and_recovery(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_json_text")

    class Bound(Provider):
        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            payload = json.loads(result["output_text"])
            payload.pop("schema")
            payload.pop("source_digest")
            result["output_text"] = json.dumps(
                {"proposal_json": json.dumps(payload, ensure_ascii=False)}
            )
            return result

    provider = Bound(prepared()[2])
    accepted = invocation(tmp_path / "accepted", provider)
    assert accepted["task_spec"]["interpretation_status"] == "admitted"
    assert provider.submissions[0][1]["text"]["format"]["name"] == BOUND_TEXT_NAME
    assert invocation(tmp_path / "accepted", provider) == accepted
    assert len(provider.submissions) == 1
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "typed")
    with pytest.raises(TaskInterpretationRejected) as changed_packet:
        invocation(tmp_path / "accepted", provider)
    assert (
        changed_packet.value.findings[0]["code"]
        == "task.interpretation_ingress_changed"
    )
    assert len(provider.submissions) == 1
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "bound_json_text")
    changed = prepared()[2]
    changed["facts"][0]["value"] = 0
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path / "rejected", Bound(changed))
    assert any(row["code"] == "task.fact_not_source" for row in caught.value.findings)
    assert not list((tmp_path / "rejected").glob("generation-plan-*.json"))


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
