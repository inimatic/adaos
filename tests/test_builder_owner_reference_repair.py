import copy

import pytest
from jsonschema import ValidationError

from adaos.sdk.builder import prototype
from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate, SemanticPrototypeValidationError
from adaos.services.builder.semantic_owner_references import owner_reference_findings
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate


def test_unrelated_findings_do_not_walk_a_malformed_owner_graph():
    candidate = {"resources": [{"records": [["row", "keep"]]}]}
    original = copy.deepcopy(candidate)
    assert prototype.prepare_reference_repair(candidate, [{"code": "missing_binding"}]) is None
    assert candidate == original


@pytest.mark.parametrize('collection,field',[
    ('views','resource_ref'),('commands','view_ref'),('representative_states','view_ref'),
    ('relationships','from_resource_ref'),('relationships','to_resource_ref'),
])
def test_missing_owner_is_typed_before_canonicalization_and_patch_preserves_every_other_decision(collection,field):
    brief,document=_multi_resource_fixture()
    valid=_multi_resource_candidate(document)
    candidate=copy.deepcopy(valid)
    candidate[collection][0][field]='undeclared-owner'
    original=copy.deepcopy(candidate)
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate,brief=brief)
    findings=caught.value.findings
    assert findings[0]['code']==('semantic.relationship_reference_missing' if collection=='relationships'
        else 'semantic.owner_reference_missing')
    assert findings[0]['gate']=='G7' and findings[0]['owner']=='candidate'
    assert findings[0]['path']==f'$.{collection}[0].{field}'
    plan=prototype.prepare_reference_repair(candidate,findings)
    assert plan and len(plan['allowed_owner_edges'])==1
    patch={'schema':'adaos.builder.owner_reference_repair.v1','base_sha256':plan['base_sha256'],
        'corrections':[{'collection':collection,'identity':candidate[collection][0]['id'],
            'field':field,'value':valid[collection][0][field]}]}
    repaired=prototype.apply_reference_repair(candidate,patch,findings)
    assert candidate==original and repaired==valid
    compile_semantic_prototype_candidate(repaired,brief=brief)
    for mutation in (
        {**patch,'base_sha256':'stale'},
        {**patch,'commands':[]},
        {**patch,'corrections':[{**patch['corrections'][0],'value':'invented'}]},
        {**patch,'corrections':[{**patch['corrections'][0],'field':'id'}]},
        {**patch,'corrections':patch['corrections']*2},
    ):
        with pytest.raises(ValidationError):prototype.apply_reference_repair(candidate,mutation,findings)


def test_owner_diagnostics_aggregate_and_scope_never_trusts_allowed_ids_in_findings():
    _,document=_multi_resource_fixture()
    candidate=_multi_resource_candidate(document)
    candidate['views'][0]['resource_ref']='missing-resource'
    candidate['representative_states'][0]['view_ref']='missing-view'
    findings=owner_reference_findings(candidate)
    assert len(findings)==2
    for row in findings:row['allowed_refs']=['attacker-chosen']
    plan=prototype.prepare_reference_repair(candidate,findings)
    assert plan and len(plan['allowed_owner_edges'])==2
    assert all('attacker-chosen' not in row['allowed_refs'] for row in plan['allowed_owner_edges'])
    assert prototype.prepare_reference_repair(candidate,[*findings,{'code':'semantic.state_proof_invalid'}]) is None
    assert prototype.prepare_reference_repair(candidate,[{**findings[0],'path':'$.commands[0].view_ref'}]) is None


def test_owner_repair_declines_ambiguous_identity_or_absent_target():
    _,document=_multi_resource_fixture()
    candidate=_multi_resource_candidate(document)
    candidate['representative_states'][0]['view_ref']='missing'
    findings=owner_reference_findings(candidate)
    candidate['representative_states'].append(copy.deepcopy(candidate['representative_states'][0]))
    assert prototype.prepare_reference_repair(candidate,findings) is None
    candidate['views']=[]
    assert prototype.prepare_reference_repair(candidate,owner_reference_findings(candidate)) is None


def test_unknown_state_owner_retains_independent_missing_collection_in_same_diagnostic():
    brief,document=_multi_resource_fixture()
    candidate=_multi_resource_candidate(document)
    state=candidate['representative_states'][0]
    collection=next(view for view in candidate['views'] if view['id']==state['view_ref'])
    resource=collection['resource_ref']
    candidate['views']=[view for view in candidate['views'] if view['id']!=collection['id']]
    # Retain a known owned view: the absence of collection is independent of
    # the broken state reference, not a guess about the destination resource.
    if not any(view['resource_ref']==resource for view in candidate['views']):
        candidate['views'].append({**copy.deepcopy(collection),'id':'existing-details','role':'details',
            'query_controls':[],'filter':None,'empty_state':None,'selection':None})
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(candidate,brief=brief)
    assert {'semantic.owner_reference_missing','semantic.resource_collection_missing'} <= {
        row['code'] for row in caught.value.findings}
    assert prototype.prepare_reference_repair(candidate,caught.value.findings) is None
