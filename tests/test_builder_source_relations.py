"""Source-owned relational admission, not general natural-language completeness."""

import copy
import json

import pytest

from adaos.services.builder.source_relations import (
    GRAMMAR_REF,
    contract_relation_findings,
    transition_inventory,
    transition_relation_findings,
)
from adaos.services.builder.task_interpretation import (
    TaskInterpretationRejected,
    admit_interpretation,
    interpretation_messages,
)
from adaos.services.builder.task_spec import compile_task_spec, seal
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_interpretation import prepared


def english():
    text = (
        "Item R1: title Atlas, owner Maya, status open.\n"
        "Provide a Resolve command that changes status from open to accepted for the selected item only.\n"
        "Preserve its title and owner, and preserve every field of the other item."
    )
    intent = capture_intent(text, project_ref="project:relations", locale="en")
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal = {
        "schema": "adaos.builder.task_interpretation.v1",
        "source_digest": intent["digest"],
        "clause_roles": [
            {"clause_id": "clause:001", "roles": ["fact"]},
            {"clause_id": "clause:002", "roles": ["product"]},
            {"clause_id": "clause:003", "roles": ["verification"]},
        ],
        "requirements": [
            {
                "id": "resolve",
                "clause_ids": ["clause:002"],
                "statement": task["clauses"][1]["text"].strip(),
                "kind": "behavior",
                "operation": "transition",
                "verification_family": "transition",
                "supersedes": [],
            },
            {
                "id": "preserve",
                "clause_ids": ["clause:003"],
                "statement": task["clauses"][2]["text"],
                "kind": "verification",
                "operation": None,
                "verification_family": "preservation",
                "supersedes": [],
            },
        ],
        "facts": [
            {
                "clause_ids": ["clause:001"],
                "subject": "R1",
                "field": field,
                "source_field": field,
                "value": value,
                "record_evidence": task["clauses"][0]["text"].strip(),
                "field_evidence": field + " " + value,
            }
            for field, value in [
                ("title", "Atlas"),
                ("owner", "Maya"),
                ("status", "open"),
            ]
        ],
        "transitions": [
            {
                "clause_ids": ["clause:002"],
                "requirement_id": "resolve",
                "command_label": "Resolve",
                "field": "status",
                "from_value": "open",
                "to_value": "accepted",
                "preserve_other_fields": True,
            }
        ],
        "constraints": [],
        "questions": [],
        "design_freedom": [],
    }
    return intent, task, proposal


def mutate(proposal, mutation):
    row = proposal["transitions"][0]
    if mutation == "direction_mismatch":
        row["from_value"], row["to_value"] = row["to_value"], row["from_value"]
    elif mutation == "command_mismatch":
        row["command_label"] = row["from_value"]
    elif mutation == "preservation_weakened":
        row["preserve_other_fields"] = False


@pytest.mark.parametrize("fixture", [prepared, english], ids=["ru", "en"])
@pytest.mark.parametrize(
    "mutation", ["direction_mismatch", "command_mismatch", "preservation_weakened"]
)
def test_literals_without_their_source_relations_cannot_be_admitted(fixture, mutation):
    intent, task, proposal = fixture()
    admit_interpretation(task, proposal, intent)  # The original meaning still admits.
    mutate(proposal, mutation)
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    findings = [
        row
        for row in caught.value.findings
        if row["code"] == "task.source_transition_" + mutation
    ]
    assert len(findings) == 1
    assert findings[0]["gate"] == "G3" and findings[0]["owner"] == "task_interpretation"
    assert findings[0]["source_grammar_ref"] == GRAMMAR_REF
    assert findings[0]["source_relation_ref"].startswith("source-relation:")


def test_external_command_does_not_require_an_invented_scalar_transition():
    text = "Provide Submit for review through the external approval service."
    intent = capture_intent(text, project_ref="project:external-review", locale="en")
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal = {"schema": "adaos.builder.task_interpretation.v1", "source_digest": intent["digest"],
        "clause_roles": [{"clause_id": "clause:001", "roles": ["product"]}],
        "requirements": [{"id": "external-review", "clause_ids": ["clause:001"], "statement": text,
            "kind": "behavior", "operation": "other", "verification_family": "runtime", "supersedes": []}],
        "facts": [], "transitions": [], "constraints": [], "questions": [], "design_freedom": []}
    admitted, _ = admit_interpretation(task, proposal, intent)
    assert admitted["acceptance"]["transitions"] == []
    assert admitted["requirements"][0]["operation"] == "other"


@pytest.mark.parametrize("kind", ["policy", "verification", "ui"])
def test_role_retagging_cannot_hide_an_explicit_named_transition(kind):
    intent, task, proposal = english()
    proposal["transitions"] = []
    proposal["clause_roles"][1]["roles"] = [
        "policy" if kind == "policy" else "verification"
    ]
    proposal["requirements"][0].update(
        kind=kind,
        operation=None,
        verification_family="policy" if kind == "policy" else "layout",
    )
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    assert any(
        row["code"] == "task.source_transition_relation_unextracted"
        for row in caught.value.findings
    )


def test_relational_evidence_has_exact_spans_and_is_not_an_execution_instruction():
    intent, task, _ = english()
    inventory = transition_inventory(intent["statement"], intent["digest"])
    (atom,) = inventory["atoms"]
    assert (
        intent["statement"][atom["start"] : atom["end"]]
        == "status from open to accepted"
    )
    assert (
        intent["statement"][atom["command"]["start"] : atom["command"]["end"]]
        == "Resolve command"
    )
    assert atom["preserve_other_fields"] and atom["command"]["declared_head"]
    assert inventory["general_source_completeness_proven"] is False
    evidence = json.loads(interpretation_messages(task)[-1]["content"])[
        "interpretation_request"
    ]
    assert evidence["source_relation_inventory"] == inventory


def transition(**overrides):
    return {
        "clause_ids": ["clause:001"],
        "requirement_id": "change",
        "command_label": "Resolve",
        "field": "status",
        "from_value": "open",
        "to_value": "accepted",
        "preserve_other_fields": False,
        **overrides,
    }


@pytest.mark.parametrize(
    "text",
    [
        'Show the text "Resolve command changes status from open to accepted".',
        "Do not provide a Resolve command that changes status from open to accepted.",
        "Never create a Resolve command that changes status from open to accepted.",
    ],
)
def test_quoted_data_and_negative_head_are_not_positive_effect_witnesses(text):
    findings, _ = transition_relation_findings(text, "source", [transition()])
    assert [row["code"] for row in findings] == [
        "task.source_transition_scope_unresolved"
    ]
    assert not transition_relation_findings(text, "source", [])[0]


@pytest.mark.parametrize("label", ["Resolve", "Закрыть", "Mark done", "Archive-7"])
def test_quoted_command_names_are_domain_independent_and_not_state_values(label):
    text = f'Provide a "{label}" command that changes status from "open" to "accepted", only this field.'
    good = transition(command_label=label, preserve_other_fields=True)
    assert not transition_relation_findings(text, "source", [good])[0]
    bad = {**good, "command_label": "open"}
    assert any(
        row["code"] == "task.source_transition_command_mismatch"
        for row in transition_relation_findings(text, "source", [bad])[0]
    )


@pytest.mark.parametrize(
    "raw,before,after",
    [
        ("12.5 to 14.5", 12.5, 14.5),
        ("12,5 to 14,5", 12.5, 14.5),
        ("false to true", False, True),
        ("null to open", None, "open"),
        (
            '"an \\"open\\" value" to "accepted value"',
            'an "open" value',
            "accepted value",
        ),
    ],
)
def test_direction_values_use_shared_exact_scalar_rules(raw, before, after):
    text = f"Provide a Resolve command that changes status from {raw}."
    assert not transition_relation_findings(
        text, "source", [transition(from_value=before, to_value=after)]
    )[0]
    findings, _ = transition_relation_findings(
        text, "source", [transition(from_value=after, to_value=before)]
    )
    assert findings[0]["code"] == "task.source_transition_direction_mismatch"


def test_preservation_cannot_be_borrowed_from_quoted_or_negated_data():
    for suffix in ['Display "preserve other fields".', "Do not preserve other fields."]:
        text = (
            "Provide a Resolve command that changes status from open to accepted. "
            + suffix
        )
        assert not transition_inventory(text, "source")["atoms"][0][
            "preserve_other_fields"
        ]


def test_preservation_named_for_one_command_is_not_shared_across_multiple_commands():
    text = (
        "Item R1: title Atlas, status open.\n"
        "Provide a Resolve command that changes status from open to accepted.\n"
        "Provide a Reopen command that changes status from accepted to open.\n"
        "Preserve its title."
    )
    assert all(
        not row["preserve_other_fields"]
        for row in transition_inventory(text, "source")["atoms"]
    )


def test_unknown_history_is_not_reinterpreted_against_current_clause_ids():
    intent, _, proposal = english()
    old = {
        **proposal["transitions"][0],
        "source_digest": "different-old-source",
        "assertion_ref": "assertion:old",
    }
    findings, coverage = transition_relation_findings(
        intent["statement"], intent["digest"], [old], check_coverage=False
    )
    assert not findings and coverage["unverified_historical_transition_refs"] == [
        "assertion:old"
    ]
    assert coverage["general_source_completeness_proven"] is False


def test_exact_source_replay_can_check_preserved_old_assertion_source_alias():
    intent, _, proposal = english()
    contract = {
        "source": intent,
        "transitions": [{**proposal["transitions"][0], "source_digest": "old"}],
        "derivation": {"kind": "exact_source_replay", "prior_source_digest": "old"},
    }
    assert not contract_relation_findings(contract)[0]
    contract["transitions"][0]["preserve_other_fields"] = False
    assert (
        contract_relation_findings(contract)[0][0]["code"]
        == "task.source_transition_preservation_weakened"
    )


@pytest.mark.parametrize(
    "mutation", ["direction_mismatch", "command_mismatch", "preservation_weakened"]
)
def test_persisted_contract_cannot_bypass_the_guard_at_generation_or_candidate_admission(
    mutation,
):
    from adaos.services.builder.prototype_context import compile_prototype_model_context
    from adaos.services.builder.task_acceptance import task_candidate_findings
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis

    intent, task, proposal = english()
    _, brief = admit_interpretation(task, proposal, intent)
    bad = copy.deepcopy(brief)
    mutate(bad["task_contract"], mutation)
    bad = seal(bad, id_key="brief_id", prefix="brief:")
    with pytest.raises(TaskInterpretationRejected):
        compile_prototype_model_context(bad, compact=True)
    findings = task_candidate_findings({}, bad)
    relational = [
        row for row in findings if row["code"].startswith("task.source_transition_")
    ]
    diagnosis = compile_repair_diagnosis(
        candidate_digest="candidate",
        findings=relational,
        parent={"generation_plan_id": "plan", "task_spec_ref": "task"},
        compiler_digest="compiler",
    )
    assert diagnosis["failure_classes"] == ["source_semantics"]
    assert diagnosis["next_actions"] == ["resolve_source_semantics"]
    assert diagnosis["paid_candidate_repair_allowed"] is False


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_relation_diagnostics_do_not_invent_a_negative_source_or_missing_values(locale):
    from adaos.sdk.builder.prototype import task_admission_failure

    intent, task, proposal = english()
    mutate(proposal, "direction_mismatch")
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    result = task_admission_failure(caught.value, locale=locale)
    assert result["next_action"] == "resolve_source_semantics"
    assert result["paid_candidate_generation"] is False
    assert (
        "не отсутствие" if locale == "ru" else "not missing supplied values"
    ) in result["detail"]
    assert "negative" not in result["detail"] and "отрицательн" not in result["detail"]
