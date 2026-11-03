import copy
import json

import pytest

from adaos.services.builder.source_literals import literal_inventory, literal_coverage
from adaos.services.builder.task_interpretation import (
    _attribute_literal,
    admit_interpretation,
    interpretation_messages,
    TaskInterpretationRejected,
)
from adaos.services.builder.task_spec import compile_task_spec, verify_identity
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_interpretation import prepared


def task_for(text):
    intent = capture_intent(
        text, project_ref="project:literal-qualification", locale="en"
    )
    return compile_task_spec(intent, compile_prototype_brief(intent))


@pytest.mark.parametrize('header,subject', [
    ('В разделе «Копии» покажи копию RU-backup для сервера RU', 'RU-backup'),
    ('Show record L1 for account A1', 'L1'),
    ('Display sample item X-7 for group G2', 'X-7'),
])
def test_related_entity_qualifier_does_not_steal_record_attribute_ownership(header, subject):
    text = header + ': created_at 2026-10-29, root_version v0.1.0.'
    task = task_for(text)
    inventory = literal_inventory(task)
    assert [row['subject'] for row in inventory['atoms']] == [subject, subject]
    facts = [{'subject': subject, 'source_field': field, 'value': value,
              'clause_ids': ['clause:001']} for field, value in
             [('created_at', '2026-10-29'), ('root_version', 'v0.1.0')]]
    proposal = {'facts': facts, 'clause_roles': [{'clause_id': 'clause:001', 'roles': ['fact']}]}
    findings, receipt = literal_coverage(task, proposal, literal_matches=_attribute_literal)
    assert not findings and receipt['covered_atoms'] == 2
    for row in inventory['atoms']:
        assert text[row['start']:row['end']] == row['evidence']
    for row in proposal['facts']:
        row['subject'] = 'A1' if subject == 'L1' else 'RU'
    findings, _ = literal_coverage(task, proposal, literal_matches=_attribute_literal)
    assert len(findings) == 2


@pytest.mark.parametrize('text', [
    'Show a report for server RU: value 7.',
    'Show record B1 for account A1 for group G2: value 7.',
])
def test_ambiguous_qualified_header_is_not_reassigned_or_silently_omitted(text):
    from adaos.services.builder.source_literals import source_preflight_findings

    task = task_for(text)
    inventory = literal_inventory(task)
    assert not inventory['atoms']
    assert inventory['unsupported_segments'][0]['status'] == 'record_subject_unresolved'
    proposal = {'facts': [], 'clause_roles': [{'clause_id': 'clause:001', 'roles': ['fact']}]}
    findings, receipt = literal_coverage(task, proposal, literal_matches=_attribute_literal)
    assert findings[0]['code'] == 'task.source_record_subject_unresolved'
    assert not receipt['recognized_atoms_accounted_for']
    assert source_preflight_findings(task) == findings


@pytest.mark.parametrize('text', [
    'Implementation guidance for this revision: Publication, release acceptance and runtime switching are external operations.',
    'Guidance for authors: Keep the interface compact and retain existing operational behavior.',
])
def test_authoring_heading_without_scalar_attributes_does_not_manufacture_a_record(text):
    from adaos.services.builder.source_literals import source_preflight_findings

    task = task_for(text)
    inventory = literal_inventory(task)
    assert inventory['atoms'] == []
    assert source_preflight_findings(task) == []


def test_quoted_qualifier_does_not_change_a_simple_subject_or_its_polarity():
    task = task_for('Do not show "for account A1" record B1: value 7.')
    inventory = literal_inventory(task)
    assert inventory['atoms'][0]['subject'] == 'B1'
    assert inventory['atoms'][0]['source_head_operator']['kind'] == 'negative_imperative_head'


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Loan L1: title Atlas, borrower Maya, status out. Loan L2: title Handbook, borrower Leo, status returned.",
            [
                ("L1", "title", "title Atlas"),
                ("L1", "borrower", "borrower Maya"),
                ("L1", "status", "status out"),
                ("L2", "title", "title Handbook"),
                ("L2", "borrower", "borrower Leo"),
                ("L2", "status", "status returned"),
            ],
        ),
        (
            "Покажи сервер RU: CPU 92, disk 78.",
            [("RU", "CPU", "CPU 92"), ("RU", "disk", "disk 78")],
        ),
        (
            'Book B1: title "Atlas, Vol. 2", borrower «Майя».',
            [
                ("B1", "title", 'title "Atlas, Vol. 2"'),
                ("B1", "borrower", "borrower «Майя»"),
            ],
        ),
        (
            'Sensor S.1: reading 92.5 %, pressure 92,5, site "https://example.net/a.b?x=1,y=2".',
            [
                ("S.1", "reading", "reading 92.5 %"),
                ("S.1", "pressure", "pressure 92,5"),
                ("S.1", "site", 'site "https://example.net/a.b?x=1,y=2"'),
            ],
        ),
        (
            '📗 Loan L1: title "Ada\\"OS", status out.',
            [("L1", "title", 'title "Ada\\"OS"'), ("L1", "status", "status out")],
        ),
    ],
)
def test_supported_lexical_grammar_preserves_values_and_exact_unicode_offsets(
    text, expected
):
    task = task_for(text)
    before = copy.deepcopy(task)
    inventory = literal_inventory(task)
    assert inventory["offset_unit"] == "unicode_codepoint"
    assert [
        (row["subject"], row["source_field"], row["evidence"])
        for row in inventory["atoms"]
    ] == expected
    for atom in inventory["atoms"]:
        assert text[atom["start"] : atom["end"]] == atom["evidence"]
    verify_identity(inventory, id_key="inventory_ref", prefix="source-literals:")
    assert task == before and literal_inventory(task) == inventory


@pytest.mark.parametrize(
    "text",
    [
        "Book B1: title AdaOS handbook, borrower Maya.",
        'Book B1: title "unfinished, borrower Maya.',
        'Book B1: meta {"x":1}, borrower Maya.',
        'Book B1: title "Atlas" extra "text", borrower Maya.',
    ],
)
def test_unsupported_tuple_cannot_silently_drop_one_of_its_attributes(text):
    inventory = literal_inventory(task_for(text))
    assert inventory["atoms"] == []
    assert inventory["unsupported_segments"]


def test_whole_source_clause_coverage_cannot_hide_an_omitted_explicit_attribute():
    intent, task, proposal = prepared()
    original = copy.deepcopy(proposal)
    proposal["facts"] = proposal["facts"][:1]
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    row = next(
        row
        for row in caught.value.findings
        if row["code"] == "task.source_record_attribute_unextracted"
    )
    assert row["gate"] == "G3" and row["owner"] == "task_interpretation"
    assert row["source_clause_ids"] == ["clause:001"]
    assert proposal["clause_roles"] == original["clause_roles"]


def test_literal_inventory_is_supplied_as_user_evidence_without_changing_the_source():
    _, task, _ = prepared()
    messages = interpretation_messages(task)
    evidence = json.loads(messages[-1]["content"])["interpretation_request"]
    assert messages[-1]["role"] == "user"
    assert evidence["source_literal_inventory"] == literal_inventory(task)
    assert evidence["source_clauses"] == task["clauses"]
    assert "lexical_evidence_not_added_product_requirements" in json.dumps(evidence)


def test_clause_roles_and_requirement_kinds_are_separate_schema_derived_namespaces():
    from adaos.services.builder.task_interpretation import interpretation_contract

    _, task, _ = prepared()
    instructions = interpretation_messages(task)[0]["content"]
    schema = interpretation_contract()["$defs"]
    roles = schema["clauseRole"]["properties"]["roles"]["items"]["enum"]
    assert "ui" not in roles and "product" in roles
    assert "TWO DISTINCT TAXONOMIES" in instructions
    assert "NOT role=ui" in instructions
    assert json.dumps(roles, ensure_ascii=False, separators=(",", ":")) in instructions


def test_coverage_receipt_does_not_misrepresent_lexical_recall_as_semantic_completeness():
    _, task, proposal = prepared()
    findings, receipt = literal_coverage(
        task, proposal, literal_matches=_attribute_literal
    )
    assert not findings and receipt["checked_atoms"] == receipt["covered_atoms"] == 2
    assert receipt["complete_for_checked_atoms"] is True
    assert receipt["general_source_completeness_proven"] is False
    # Role classification is another admission obligation. Never turn a negated
    # example into a positive fixture, but do not silently admit unresolved
    # lexical evidence either.
    proposal["clause_roles"][0]["roles"] = ["policy"]
    findings, partial = literal_coverage(
        task, proposal, literal_matches=_attribute_literal
    )
    assert len(findings) == 2
    assert all(row["code"] == "task.source_record_role_unresolved" for row in findings)
    assert partial["complete_for_checked_atoms"] is False
    assert len(partial["unclassified_atom_refs"]) == 2
    assert partial["general_source_completeness_proven"] is False


@pytest.mark.parametrize("role", ["product", "policy", "verification", "exclusion", "design", "formatting"])
def test_retagging_a_record_clause_cannot_authorize_omitting_its_values(role):
    intent, task, proposal = prepared()
    proposal["clause_roles"][0]["roles"] = [role]
    proposal["facts"] = []
    before = copy.deepcopy(proposal)
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    unresolved = [row for row in caught.value.findings if row["code"] == "task.source_record_role_unresolved"]
    assert len(unresolved) == 2
    assert all(row["gate"] == "G3" and row["owner"] == "task_interpretation" for row in unresolved)
    assert proposal == before


def test_negated_tuple_is_not_invented_as_a_positive_fact_or_silently_discarded():
    task = task_for("Do not create Loan L1: title Atlas, status returned.")
    proposal = {"clause_roles": [{"clause_id": "clause:001", "roles": ["exclusion"]}], "facts": []}
    findings, receipt = literal_coverage(task, proposal, literal_matches=_attribute_literal)
    assert len(findings) == 2
    assert all(row["code"] == "task.source_record_polarity_unresolved" for row in findings)
    assert receipt["checked_atoms"] == receipt["covered_atoms"] == 0
    assert receipt["recognized_atoms_accounted_for"] is False
    assert len(receipt['polarity_unresolved_atom_refs'])==2
    assert receipt["general_source_completeness_proven"] is False
    assert proposal["facts"] == []


@pytest.mark.parametrize(
    "phrase,label,value",
    [
        ('title "Atlas, Vol. 2"', "title", "Atlas, Vol. 2"),
        ('title "Ada\\"OS"', "title", 'Ada"OS'),
        ("title “Atlas”", "title", "Atlas"),
        ("reading 92,5", "reading", 92.5),
        ("reading 92.5 %", "reading", 92.5),
    ],
)
def test_grounding_matches_the_protected_scalar_evidence(phrase, label, value):
    assert _attribute_literal(phrase, label, value)


def test_malformed_quoted_scalar_is_not_silently_normalized():
    assert not _attribute_literal(
        'title "Atlas" extra "text"', "title", 'Atlas" extra "text'
    )


@pytest.mark.parametrize("fault", ["missing_clause", "offset", "text"])
def test_resealed_task_cannot_supply_a_lossy_clause_projection(fault):
    from adaos.services.builder.task_spec import seal

    _, task, _ = prepared()
    if fault == "missing_clause":
        task["clauses"].pop()
    elif fault == "offset":
        task["clauses"][0]["end"] -= 1
    else:
        task["clauses"][0]["text"] = "different"
    task = seal(task)
    with pytest.raises(TaskInterpretationRejected) as caught:
        literal_inventory(task)
    assert (
        caught.value.findings[0]["code"]
        == "task.source_literal_source_projection_invalid"
    )
    assert caught.value.findings[0]["gate"] == "G5"


@pytest.mark.parametrize(
    "limit", ["MAX_SOURCE_CODEPOINTS", "MAX_INVENTORY_ATOMS", "MAX_INVENTORY_BYTES"]
)
def test_capacity_loss_is_explicit_and_never_truncates_the_inventory(
    monkeypatch, limit
):
    from adaos.services.builder import source_literals

    _, task, _ = prepared()
    monkeypatch.setattr(source_literals, limit, 1)
    with pytest.raises(TaskInterpretationRejected) as caught:
        literal_inventory(task)
    assert caught.value.findings[0]["code"] == "task.source_literal_capacity"
    assert caught.value.findings[0]["gate"] == "G5"


@pytest.mark.parametrize("slot", ["statement", "provenance"])
def test_matching_source_digest_cannot_launder_a_different_task_source(slot):
    from adaos.services.builder.task_spec import seal

    intent, task, proposal = prepared()
    if slot == "statement":
        task["source"]["statement"] += " Changed source."
    else:
        task["source"]["provenance"] = {"kind": "chat", "ref": "another-user-turn"}
    task = seal(task)
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, proposal, intent)
    assert (
        caught.value.findings[0]["code"] == "task.interpretation_task_source_conflict"
    )
    assert caught.value.findings[0]["gate"] == "G0"
