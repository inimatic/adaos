import copy
import pytest
from adaos.services.builder.issue_planning import decompose_issues, verify_decomposition


def source(count=21):
    return {'issue_id':'skin', 'title':'Preserve all requirements', 'lane':'automation',
            'source_message_ids':['canonical:one'], 'kind':'product_requirement',
            'applicable_phases':['automation'],
            'acceptance_criteria':[f'Criterion {i}' for i in range(count)]}


def test_partition_retains_canonical_parent_and_every_occurrence_without_rephrasing():
    original=source(43); original['acceptance_criteria'][22]=original['acceptance_criteria'][0]
    saved=copy.deepcopy(original)
    rows=decompose_issues([original])
    assert original==saved
    assert rows[0]['source_acceptance_criteria']==saved['acceptance_criteria']
    assert rows[0]['structural_status']=='split'
    assert [len(row['acceptance_criteria']) for row in rows[1:]]==[20,20,3]
    assert [text for row in rows[1:] for text in row['acceptance_criteria']]==saved['acceptance_criteria']
    assert len(set(rows[0]['source_criterion_ids']))==43
    assert all(row['source_message_ids']==['canonical:one'] for row in rows)
    assert rows==decompose_issues([saved])
    verify_decomposition(rows)


@pytest.mark.parametrize('mutation', ['drop','text','id','parent','link'])
def test_mutated_coverage_cannot_be_accepted(mutation):
    rows=decompose_issues([source()])
    if mutation=='drop': rows.pop()
    if mutation=='text': rows[1]['acceptance_criteria'][0]='A different obligation'
    if mutation=='id': rows[1]['criterion_ids'][0]='forged'
    if mutation=='parent': rows.pop(0)
    if mutation=='link': rows[1]['derived_from_issue_ids']=[]
    with pytest.raises(ValueError): verify_decomposition(rows)


def test_plan_capacity_fails_without_trimming_or_partial_admission():
    original=source(981); saved=copy.deepcopy(original)
    with pytest.raises(ValueError,match='at most 50'): decompose_issues([original])
    assert original==saved


def test_no_mechanical_cutting_of_overlong_semantic_criterion():
    original=source(); original['acceptance_criteria'][0]='x'*501
    with pytest.raises(ValueError,match='500 characters'): decompose_issues([original])


def test_explicit_ids_cannot_collide_with_generated_ids():
    other=source(1); other['issue_id']='skin~part001'
    with pytest.raises(ValueError,match='collision'): decompose_issues([source(),other])


def test_bounded_legacy_plan_is_unchanged():
    original=source(3)
    assert decompose_issues([original])==[original]


def test_source_whitespace_retained_while_executable_abi_is_canonical():
    original=source();original['acceptance_criteria'][0]='  Keep\n every\tword  '
    rows=decompose_issues([original])
    rows[1]['acceptance_criteria'][0]='Keep every word'
    verify_decomposition(rows)
    assert rows[0]['source_acceptance_criteria'][0]=='  Keep\n every\tword  '


def test_default_identity_is_normalized_before_coverage_validation():
    original=source(1);original.pop('issue_id')
    assert decompose_issues([original])[0]['issue_id']=='I001'


def test_graph_retains_source_criteria_without_inventing_behavioral_evidence():
    from adaos.services.builder.traceability import project_change
    issues=decompose_issues([source()])
    result=project_change(project_ref='scenario:example',generation=1,change={'issues':issues})
    graph=result['graph']
    canonical=[row for row in graph['nodes'] if row['kind']=='source_criterion']
    assert len(canonical)==21
    assert {row['ref'] for row in canonical}==set(issues[0]['source_criterion_ids'])
    assert not [row for row in graph['nodes'] if row['kind']=='execution']
    assert not [row for row in result['gaps'] if row['kind']=='unresolved_source_criterion']
