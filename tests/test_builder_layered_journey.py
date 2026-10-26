import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3

import pytest
from PIL import Image
import yaml

from adaos.services.builder.application_journeys import REQUEST_CASES, qualify_request_report
from adaos.services.builder.journey_profiles import profile_definition, retouch_profile_for_sources
from adaos.e2e.retouch_verification import verify_layered_downloads


def test_layered_profile_requires_exact_owned_tool_contract(tmp_path):
    source=tmp_path/'natural_retouch_skill';source.mkdir()
    assert retouch_profile_for_sources([source])=='natural-retouch.v5'
    names=('workspace_query','workspace_command','workspace_run','workspace_draft')
    tools=[{'name':name,'entry':'handlers.workspace:'+name} for name in names]
    (source/'skill.yaml').write_text(yaml.safe_dump({'tools':tools}),encoding='utf-8')
    assert retouch_profile_for_sources([source])=='natural-retouch.v6'
    tools[-1]['entry']='candidate.provided:runner'
    (source/'skill.yaml').write_text(yaml.safe_dump({'tools':tools}),encoding='utf-8')
    assert retouch_profile_for_sources([source])=='natural-retouch.v5'
    definition=profile_definition(repo_root=Path(__file__).parents[1],profile_id='natural-retouch.v6')
    assert {'raw-alpha-advisory-delivery','reassembly-noop-protection','final-export-adopt'}<=set(definition['obligations'])


@pytest.mark.parametrize('fault',[None,'missing_case','duplicate_case','wrong_source','green_lie','wrong_balance','duplicate_charge','reset_accepted'])
def test_request_grader_requires_observed_ledger_facts(fault):
    report={'source_digest':'source','external_model_calls':0,'basis':'image-request-count',
      'environment':{k:'recorded' for k in ('python','platform','pillow','numpy')},
      'budget':{'configured':True,'limit':2,'prior_used':1,'reserved':1,'remaining':0},
      'claims':[{'request_id':'image','phase':'image:retouch:0'},{'request_id':'text','phase':'qa:0'}],
      'duplicate_fresh':False,'admission_error':'request_budget_exhausted','reset_error':'historical_usage_is_immutable',
      'cases':[{'id':key,'passed':True} for key in REQUEST_CASES],'passed':True}
    if fault=='missing_case':report['cases'].pop()
    if fault=='duplicate_case':report['cases'][-1]=report['cases'][0]
    if fault=='wrong_source':report['source_digest']='other'
    if fault=='green_lie':report['cases'][0]['passed']=False
    if fault=='wrong_balance':report['budget']['remaining']=1
    if fault=='duplicate_charge':report['claims'].append(copy.deepcopy(report['claims'][0]))
    if fault=='reset_accepted':report['reset_error']=None
    assert qualify_request_report(report,source_digest='source')['ok'] is (fault is None)


def png(image):
    stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()


@pytest.mark.parametrize('fault',[None,'outside_change','no_final','missing_jpeg','opaque_raw','wrong_digest','wrong_geometry'])
def test_independent_layered_download_grader(tmp_path,fault):
    owned=tmp_path/'owned';data=owned/'data';data.mkdir(parents=True);output=tmp_path/'output';output.mkdir()
    original=Image.new('RGB',(16,12),'black');result=original.copy();result.putpixel((8,6),(160,100,90))
    if fault=='outside_change':result.putpixel((0,0),(1,0,0))
    alpha=Image.new('L',original.size);alpha.putpixel((8,6),255)
    raw=original.convert('RGBA');raw.putalpha(255 if fault=='opaque_raw' else alpha)
    raw_source=png(original);raw_result=png(result);digest=hashlib.sha256(raw_result).hexdigest()
    source_digest=hashlib.sha256(raw_source).hexdigest()
    artifact='assembled';variant={'assembled':artifact,'raw':'raw','alpha':'a1','repair_support':'zero','protection':'zero','occluders':'zero'}
    with sqlite3.connect(data/'retouch.sqlite3') as db:
        db.execute('CREATE TABLE layered_artifacts(id TEXT,photo_id TEXT,series_id TEXT,role TEXT,digest TEXT,body BLOB,metadata TEXT)')
        db.execute('CREATE TABLE layered_records(id TEXT,kind TEXT,body TEXT)')
        db.execute('CREATE TABLE assets(digest TEXT,base BLOB)')
        db.execute('INSERT INTO assets VALUES(?,?)',(source_digest,raw_source))
        for identity,kind,body in [('photo','photo',{'sha256':source_digest,'final_artifact_id':None if fault=='no_final' else artifact}),
           ('adopted','photo',{'parent_artifact_id':artifact,'title':'photo (1).png'}),
           ('series','series',{'status':'completed','a0':'a0','variants':[variant]})]:
            db.execute('INSERT INTO layered_records VALUES(?,?,?)',(identity,kind,json.dumps(body)))
        for identity,body in [('a0',png(alpha)),('a1',png(alpha)),('zero',png(Image.new('L',original.size))),('raw',png(raw)),(artifact,raw_result)]:
            db.execute('INSERT INTO layered_artifacts VALUES(?,?,?,?,?,?,?)',(identity,'photo','series','assembled' if identity==artifact else identity,
                'wrong' if identity==artifact and fault=='wrong_digest' else hashlib.sha256(body).hexdigest(),body,json.dumps({'exportable':True})))
    (output/'result.png').write_bytes(png(Image.new('RGB',(8,8))) if fault=='wrong_geometry' else raw_result)
    result.save(output/'result.jpg',format='JPEG',quality=95)
    report={'downloads':[{'format':fmt,'path':name,'artifact_id':artifact} for fmt,name in [('PNG','result.png'),('JPEG','result.jpg')]]}
    if fault=='missing_jpeg':report['downloads'].pop()
    if fault:
        with pytest.raises(ValueError):verify_layered_downloads(owned_root=owned,output=output,report=report)
    else:
        receipt=verify_layered_downloads(owned_root=owned,output=output,report=report)
        assert receipt['passed'] and receipt['changed_pixels']==1 and not receipt['provider_quality_claim']
