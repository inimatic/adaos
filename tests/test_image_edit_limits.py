import base64
import io
import pytest
from PIL import Image
from adaos.sdk.llm.images import edit_limits, validate_edit
from adaos.sdk.llm import llm_client


def test_inspected_limits_and_actual_edit_admission_use_the_same_contract(monkeypatch):
    model='gpt-image-2.5-sunburst-2026-09-08'
    limits=edit_limits(model)
    assert limits['source']=='core_sdk_admission' and limits['requires_remote_availability_check']
    assert limits['max_pixels']==8294400 and limits['max_edge']==3840 and 'max' in limits['qualities']
    stream=io.BytesIO();Image.new('RGBA',(16,16),(100,80,60,255)).save(stream,format='PNG')
    reference={'image_url':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}
    sent=[]
    monkeypatch.setattr(llm_client,'_submit_budgeted_root_job',lambda payload,**_:sent.append(payload) or payload)
    args=dict(request_id='limit-test',model=model,prompt='Retouch',images=[reference],quality='max')
    llm_client.submit_image_edit_job(**args,size='3200x2560')
    assert len(sent)==1
    preflight=validate_edit(**{k:v for k,v in args.items() if k!='request_id'},size='3200x2560')
    assert preflight['ok'] and not preflight['provider_contacted'] and len(sent)==1
    for size in ('3216x2592','3856x1024','3201x2560','3840x1024'):
        with pytest.raises(ValueError): llm_client.submit_image_edit_job(**args,size=size)
        with pytest.raises(ValueError): validate_edit(**{k:v for k,v in args.items() if k!='request_id'},size=size)
    assert len(sent)==1
    assert edit_limits('gpt-image-1')['custom_sizes'] is False
