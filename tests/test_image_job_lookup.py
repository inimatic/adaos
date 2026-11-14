"""Recover the exact Root image request without creating provider work."""
from types import SimpleNamespace

import pytest

from adaos.sdk.llm import llm_client as llm


@pytest.fixture
def lookup_transport(monkeypatch):
    calls = []
    answers = {}
    monkeypatch.setattr(llm, '_current_ctx', lambda:None)
    monkeypatch.setattr(llm, '_root_http_client', lambda ctx:(SimpleNamespace(base_url='https://a.invalid'), None))
    monkeypatch.setattr(llm, '_identity_headers', lambda *_:{'X-Node':'same-owned-node'})
    monkeypatch.setattr(llm, '_root_llm_base_urls', lambda *a, **kw:['https://a.invalid','https://b.invalid'])
    monkeypatch.setattr(llm, '_observe_job_spend', lambda *_:None)
    class Http:
        def __init__(self, base):self.base = base
        def request(self, method, path, **kwargs):
            calls.append((self.base, method, path, kwargs))
            answer = answers[self.base]
            if isinstance(answer, Exception):raise answer
            return answer
    monkeypatch.setattr(llm, '_root_http_for_base', lambda _p,_b,base,_i:Http(base))
    return calls, answers


def test_lookup_checks_other_root_without_any_submit(lookup_transport):
    calls, answers = lookup_transport
    answers['https://a.invalid'] = llm.RootHttpError('not found', status_code=404)
    answers['https://b.invalid'] = {'job_id':'retained-job','request_id':'image-owned','status':'queued'}
    result = llm.lookup_image_job(request_id='image-owned', model='gpt-image-1', prompt='Preserve')
    assert result['job_id'] == 'retained-job'
    assert result['_client']['base_url'] == 'https://b.invalid'
    assert [c[1:3] for c in calls] == [('POST','/v1/llm/jobs/lookup')]*2
    assert all(c[3]['json']['request_id'] == 'image-owned' for c in calls)
    assert all(c[3]['headers'] == {'X-Node':'same-owned-node'} for c in calls)


def test_lookup_keeps_exact_edit_payload_and_ignores_local_budget_fields(monkeypatch, lookup_transport):
    calls, answers = lookup_transport
    image = {'prompt':'Preserve','images':[{'image_url':'owned'}],'size':'2160x2112',
        'quality':'max','output_format':'png','background':'transparent'}
    monkeypatch.setattr(llm, '_validated_image_edit', lambda **kwargs:image)
    answers['https://a.invalid'] = {'job_id':'retained-job','request_id':'image-owned','status':'queued'}
    llm.lookup_image_job(request_id='image-owned', model='gpt-image-2.5-sunburst', **image)
    assert calls[0][3]['json'] == {'api':'images.edit','model':'gpt-image-2.5-sunburst',
        'request_id':'image-owned','image':image}


@pytest.mark.parametrize('answer,expected', [
    (llm.RootHttpError('not found',status_code=404),None),
    (llm.RootHttpError('denied',status_code=403),llm.RootHttpError),
    (llm.RootHttpError('input conflict',status_code=409),llm.RootHttpError),
    ({'status':'queued'},ValueError),
    ({'job_id':'other-job','request_id':'other-request'},ValueError),
])
def test_lookup_does_not_turn_missing_or_invalid_proof_into_submit(lookup_transport, answer, expected):
    calls, answers = lookup_transport
    answers.update({'https://a.invalid':answer,'https://b.invalid':answer})
    if expected:
        with pytest.raises(expected):
            llm.lookup_image_job(request_id='image-owned', model='gpt-image-1', prompt='Preserve')
    else:
        assert llm.lookup_image_job(request_id='image-owned', model='gpt-image-1', prompt='Preserve') is None
    assert all(call[2] == '/v1/llm/jobs/lookup' for call in calls)
