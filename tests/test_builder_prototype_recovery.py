import copy
import hashlib
import json

import pytest

from adaos.services.builder.prototype_recovery import replay_base_ref,prepare_initial_replay,_sources
from adaos.services.builder.llm_input_attribution import retain_llm_evidence
from adaos.services.builder.task_interpretation import admit_interpretation
from test_builder_generation_wire import envelope
from test_builder_task_interpretation import prepared


def setup(tmp_path):
    root=tmp_path/'qualification';root.mkdir()
    journal=root/'llm_jobs';journal.mkdir()
    evidence=tmp_path/'evidence'
    (root/'webui.json').write_text('{"initial":true}',encoding='utf-8')
    plan,messages,_=envelope('personal')
    intent,task,proposal=prepared()
    task,brief=admit_interpretation(task,proposal,intent)
    project=intent['scope']['project_ref']
    source={'schema':'adaos.builder.llm_job_input.v1','job_id':'ai-owned','request_id':'request',
        'scenario_id':project.split(':',1)[-1],'messages':messages,'source_inputs':_sources(root)}
    terminal={'schema':'adaos.builder.llm_job_result.v1','status':'failed','job_id':'ai-owned',
        'request_id':'request','scenario_id':source['scenario_id']}
    def retain(value):
        receipt=retain_llm_evidence(value,state_dir=evidence)
        raw=json.dumps({**value,'evidence_receipt':receipt}).encode('utf-8')
        (journal/'ai-owned.request.json').write_bytes(raw)
        terminal['input_artifact']={'path':'llm_jobs/ai-owned.request.json',
            'sha256':hashlib.sha256(raw).hexdigest(),'evidence_receipt':receipt}
        (journal/'ai-owned.json').write_text(json.dumps(terminal),encoding='utf-8')
    retain(source)
    (journal/'wire-owned.json').write_text(json.dumps({'job_id':'ai-owned',
        'context_admission':{'generation_plan_ref':plan['generation_plan_id']}}),encoding='utf-8')
    record={'schema':'adaos.builder.generation_admission.v1','task_spec':task,'brief':brief,'generation_plan':plan}
    plan_path=journal/(plan['generation_plan_id'].replace(':','-')+'.json')
    plan_path.write_text(json.dumps(record),encoding='utf-8')
    options={'project_ref':project,'artifact_root':root,'journal_dir':journal,'job_id':'ai-owned',
        'request_text':intent['statement'],'expected_base_ref':replay_base_ref(project_ref=project,artifact_root=root),
        'evidence_state_dir':evidence}
    return options,source,brief,terminal,record,plan_path,retain


def test_initial_replay_recovers_exact_admitted_brief_and_acknowledged_input_without_any_model(tmp_path):
    options,_,brief,_,_,_,_=setup(tmp_path)
    before={path.name:path.read_bytes() for path in options['artifact_root'].glob('*.json')}
    result=prepare_initial_replay(**options)
    assert result['status']=='admitted' and result['provider_calls']==0
    assert result['brief']==brief and result['base_ref']==options['expected_base_ref']
    assert result['original_input_receipt_ref'].startswith('artifact://context/sha256/')
    assert prepare_initial_replay(**options)==result
    assert before=={path.name:path.read_bytes() for path in options['artifact_root'].glob('*.json')}
    assert replay_base_ref(project_ref=options['project_ref'],artifact_root=options['artifact_root'],revision='001')=='001'
    assert prepare_initial_replay(**{**options,'expected_base_ref':'001'}) is None


@pytest.mark.parametrize('fault',[
    'changed_current_base','changed_original_base','foreign_project','foreign_job','changed_request',
    'changed_raw_input','changed_acknowledgement','changed_admission','changed_source_meaning','changed_input_path',
])
def test_initial_replay_refuses_stale_or_unqualified_recovery_before_paid_work(tmp_path,fault):
    options,source,_,terminal,record,plan_path,retain=setup(tmp_path)
    root,journal=options['artifact_root'],options['journal_dir']
    if fault=='changed_current_base':(root/'webui.json').write_text('{}',encoding='utf-8')
    elif fault=='changed_original_base':
        source=copy.deepcopy(source)
        source['source_inputs']['webui.json']={'sha256':hashlib.sha256(b'{}').hexdigest(),'content':'{}'}
        retain(source)
    elif fault=='foreign_project':options['project_ref']='scenario:foreign'
    elif fault=='foreign_job':options['job_id']='../../foreign'
    elif fault=='changed_request':options['request_text']='Different meaning'
    elif fault=='changed_raw_input':(journal/'ai-owned.request.json').write_text('{}',encoding='utf-8')
    elif fault=='changed_acknowledgement':
        terminal['input_artifact']['evidence_receipt']['digest']='sha256:'+'0'*64
        (journal/'ai-owned.json').write_text(json.dumps(terminal),encoding='utf-8')
    elif fault=='changed_admission':
        record['brief']['problem']={'state':'unknown','value':None}
        record['brief']['title']={'state':'known','value':'Forged'}
        plan_path.write_text(json.dumps(record),encoding='utf-8')
    elif fault=='changed_source_meaning':
        body=json.loads(source['messages'][-1]['content'])
        body['builder_request']['instruction']='Different meaning'
        source['messages'][-1]['content']=json.dumps(body)
        retain(source)
    elif fault=='changed_input_path':
        terminal['input_artifact']['path']='llm_jobs/other.request.json'
        (journal/'ai-owned.json').write_text(json.dumps(terminal),encoding='utf-8')
    with pytest.raises(ValueError):prepare_initial_replay(**options)
