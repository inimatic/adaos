import json
import pytest
from adaos.sdk import ai
from adaos.services.content_generation import ContentGenerationService
from test_content_generation import setup_service,request,SCHEMA


def test_root_web_search_is_explicit_and_part_of_identity(tmp_path):
    service,calls=setup_service(tmp_path)
    request(service,web_search=True)
    assert calls[0][1]['tools']==[{'type':'web_search'}]
    assert calls[0][1]['max_tool_calls']==3
    with pytest.raises(ValueError,match='different content'):request(service,web_search=False)


def test_personal_content_pins_account_and_never_falls_back(tmp_path,monkeypatch):
    service,root_calls=setup_service(tmp_path)
    calls=[]
    def submit(**options):
        calls.append(options)
        return {'job_id':'ai-1','status':'queued','funding_source':'personal_subscription'}
    monkeypatch.setattr(ai,'submit',submit)
    monkeypatch.setattr(ai,'job',lambda identity:{'job_id':identity,'status':'completed','funding_source':'personal_subscription',
        'output_text':json.dumps({'status':'completed','data':{'text':'answer'},'message':''})})
    assert request(service,model='personal-model',personal_binding='binding',web_search=True)['status']=='queued'
    request(service,model='personal-model',personal_binding='binding',web_search=True)
    assert len(calls)==1 and not root_calls
    assert calls[0]['expected_binding']=='binding' and calls[0]['web_search'] is True
    assert 'max_output_tokens' not in calls[0]
    result=service.get('draft-1')
    assert result['data']=={'text':'answer'} and result['funding_source']=='personal_subscription'
    assert not root_calls


def test_personal_failure_does_not_submit_to_api(tmp_path,monkeypatch):
    service,calls=setup_service(tmp_path)
    def fail(**kwargs):raise PermissionError('connection_changed')
    monkeypatch.setattr(ai,'submit',fail)
    with pytest.raises(PermissionError):request(service,personal_binding='frozen',model='personal-model')
    assert calls==[]
