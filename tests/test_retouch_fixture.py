"""Provider doubles must honor legacy and new strict schemas without inventing visibility."""
from copy import deepcopy

import jsonschema
import pytest

from adaos.e2e.retouch_fixture import RetouchFixture, synthetic_qa_checks
from adaos.e2e.retouch_layered_fixture import synthetic_native_qa


def test_terminal_quota_attempt_stays_failed_after_provider_recovers(tmp_path):
    import json
    import sqlite3
    from types import SimpleNamespace
    from adaos.e2e.retouch_layered_fixture import LayeredRetouchFixture
    path=tmp_path/'provider.sqlite3'
    def connect():
        db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
    with connect() as db:
        db.execute('CREATE TABLE fixture_jobs(request_id TEXT PRIMARY KEY,kind,input_digest,request,response,polls DEFAULT 0,submits DEFAULT 1)')
        db.execute('CREATE TABLE fixture_calls(operation,identity,at)')
    provider=object.__new__(LayeredRetouchFixture)
    provider.store=SimpleNamespace(connect=connect)
    provider.module=SimpleNamespace(dumps=json.dumps)
    controls={'quota_phase':'qa'}
    provider.control=lambda key:controls.get(key)
    schema={'type':'object','properties':{'issues':{'type':'array'},'complete':{'type':'boolean'},
        'preference':{'type':'string'}},'required':['issues','complete','preference'],'additionalProperties':False}
    request={'request_id':'initial-attempt','purpose':'natural_retouch.native_qa','prompt':'test',
             'schema':schema,'data':{'stage':'segment-0'}}
    assert provider.submit('qa',request)['status']=='queued'
    controls['quota_phase']=None
    failed=provider.poll('qa','initial-attempt')
    assert failed['status']=='failed' and failed['error']['code']=='credit_balance_exhausted'
    assert failed['admission_refusals'] is None and failed['last_admission_refusal'] is None
    with pytest.raises(AssertionError,match='resubmitted'):
        provider.submit('qa',request)
    provider.submit('qa',{**request,'request_id':'explicit-second-attempt'})
    assert provider.poll('qa','explicit-second-attempt')['status']=='completed'
    assert provider.poll('qa','initial-attempt')['status']=='failed'


@pytest.mark.parametrize('stage,preference', [('segment-0', 'candidate'), ('candidate-0-0', 'uncertain')])
@pytest.mark.parametrize('evidence_fields', [False, True])
@pytest.mark.parametrize('reassessment', [False, True])
def test_native_double_obeys_typed_qa_and_keeps_result_reviewable(stage, preference, evidence_fields, reassessment):
    schema = {'type': 'object', 'additionalProperties': False,
        'required': ['issues', 'preference', 'complete'], 'properties': {
            'issues': {'type': 'array'}, 'preference': {'enum': ['candidate', 'parent', 'uncertain']},
            'complete': {'type': 'boolean'}}}
    if evidence_fields:
        for field in ('resolved_issue_ids', 'requested_evidence'):
            schema['required'].append(field)
            schema['properties'][field] = {'type': 'array'}
    if reassessment:
        schema['required'].append('reassessed_view_ids')
        schema['properties']['reassessed_view_ids'] = {'type': 'array', 'items': {'type': 'string'}}
    result = synthetic_native_qa({'schema': schema, 'data': {'stage': stage}})
    assert result == {'issues': [], 'preference': preference, 'complete': True,
                      **({'resolved_issue_ids': [], 'requested_evidence': []} if evidence_fields else {}),
                      **({'reassessed_view_ids': []} if reassessment else {})}


def test_native_double_refuses_unknown_contract_or_missing_stage():
    with pytest.raises(ValueError, match='stage_required'):
        synthetic_native_qa({'schema': {'type': 'object'}})
    with pytest.raises(jsonschema.ValidationError):
        synthetic_native_qa({'schema': {'type': 'object', 'required': ['new_evidence_field']},
                             'data': {'stage': 'segment-0'}})


@pytest.mark.parametrize("presence_required", [False, True])
def test_synthetic_qa_obeys_recorded_schema(presence_required):
    check = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
             "properties": {"verdict": {"enum": ["pass", "fail", "uncertain"]}, "reason": {"type": "string"}}}
    if presence_required:
        check["required"].append("subject_presence")
        check["properties"]["subject_presence"] = {"enum": ["visible", "absent", "unknown"]}
    schema = {"properties": {"checks": {"properties": {"pores_moles": deepcopy(check), "identity": deepcopy(check)}}}}
    before = deepcopy(schema)
    result = synthetic_qa_checks(["pores_moles", "identity"], schema)
    for item in result.values():
        jsonschema.validate(item, check)
        assert "SYNTHETIC" in item["reason"]
        assert item.get("subject_presence") == ("unknown" if presence_required else None)
    assert schema == before


def test_double_rejects_missing_content_purpose_before_any_effect():
    fixture = RetouchFixture.__new__(RetouchFixture)
    with pytest.raises(TypeError, match="purpose"):
        fixture.submit("mask_advice", {"request_id": "missing-purpose", "prompt": "example",
                                        "schema": {"type": "object"}})


def test_fixture_attachment_download_preserves_bytes_and_read_authorization():
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from adaos.e2e.retouch_fixture import fixture_app
    raw=b'\x89PNG\r\n\x1a\nfixture';calls=[]
    async def read(tool,args):calls.append((tool,args));return {'ok':True}
    owner=SimpleNamespace(token='fixture-only',source_digest='sha256:'+'a'*64,
        call=read,read_blob=lambda ref,logical:raw)
    client=TestClient(fixture_app(owner))
    url='/api/tools/natural_retouch_skill/read_attachment/attachments/exports/'+'b'*64+'/result.png'
    assert client.get(url).status_code==403
    for query,disposition in [('', 'inline;'),('?download=1','attachment;')]:
        response=client.get(url+query,headers={'X-Retouch-Fixture-Token':'fixture-only'})
        assert response.content==raw and response.headers['content-disposition'].startswith(disposition)
    assert len(calls)==2 and all(tool=='read_attachment' for tool,args in calls)
