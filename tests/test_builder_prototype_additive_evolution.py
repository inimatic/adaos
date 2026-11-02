import copy

import pytest

from adaos.services.builder.prototype_additive_evolution import derive
from adaos.services.builder.prototype_publications import PrototypePublicationRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder_context import digest


def fixture():
    def resource(name,records,fields):
        return {'id':name,'identity_field_refs':['id'],'records':records,
                'fields':[{'id':field,'label':{'en':field}} for field in fields]}
    def spec(name,records,properties):
        return {'data_definition':{'schema':'adaos.builder.prototype_data.v1','source_id':name,
            'mode':'local_crud','record_schema':{'type':'object','properties':{'id':{'type':'string'},
                'revision':{'type':'integer'},**properties},'required':['id','revision'],'additionalProperties':False},
            'seed':copy.deepcopy(records),'activities':[{'activity_id':'list','operation':'list','input_schema':{},
                'output_schema':{},'side_effect_class':'read_only','implementation_status':'prototype_only'}]}}
    records=[{'id':'w1','revision':1,'title':'Pump','state':'open'},
             {'id':'w2','revision':1,'title':'Valve','state':'closed'}]
    previous={'document':{'resources':[resource('work',records,['title','state'])],'relationships':[]}}
    old={'work':spec('work',records,{'title':{'type':'string'},'state':{'type':'string'}})}
    next_records=[{**row,'team_id':'T-A'} for row in records]
    teams=[{'id':'T-A','revision':1,'name':'Mechanical'}]
    new={'work':spec('work',next_records,{'title':{'type':'string'},'state':{'type':'string'},'team_id':{'type':['string','null']}}),
         'teams':spec('teams',teams,{'name':{'type':'string'}})}
    document={'resources':[resource('work',next_records,['title','state','team_id']),resource('teams',teams,['name'])],
        'relationships':[{'id':'work_team','from_resource_ref':'work','from_field_ref':'team_id',
            'to_resource_ref':'teams','to_field_ref':'id','cardinality':'many_to_one','label_field_refs':['name']}],
        'requirement_bindings':[]}
    facts=[{'subject':record['id'],'source_field':field,'field':field,'value':record[field],
            'assertion_ref':f'assertion:{record["id"]}-{field}'}
            for name,rows,fields in [('work',next_records,['team_id']),('teams',teams,['name'])]
            for record in rows for field in fields]
    value={'document':document,'brief':{'task_contract':{'facts':facts}},'seed':{'source_digest':digest(document)}}
    live=copy.deepcopy(records);live[0].update(state='accepted',revision=2)
    fork=seal({'resources':[{'resource_ref':'work','records':live,'records_digest':digest(live)}]},
              id_key='fork_id',prefix='runtime-fork:')
    return previous,value,fork,old,new


def test_additions_preserve_live_values_without_turning_them_into_source_or_reseeding():
    args=fixture();before=copy.deepcopy(args)
    plan=derive(*args)
    records={row['resource_ref']:row['records'] for row in plan['resources']}
    assert records['work'][0]=={'id':'w1','revision':2,'title':'Pump','state':'accepted','team_id':'T-A'}
    assert records['teams']==[{'id':'T-A','revision':1,'name':'Mechanical'}]
    assert args==before


def test_deleted_fixture_stays_deleted_and_a_live_created_record_gets_only_nullable_defaults():
    previous,value,fork,old,new=fixture()
    fork['resources'][0]['records']=[{'id':'live','revision':4,'title':'Operator','state':'accepted'}]
    fork['resources'][0]['records_digest']=digest(fork['resources'][0]['records'])
    plan=derive(previous,value,fork,old,new)
    rows=next(row['records'] for row in plan['resources'] if row['resource_ref']=='work')
    assert rows==[{'id':'live','revision':4,'title':'Operator','state':'accepted','team_id':None}]


@pytest.mark.parametrize('fault', ['remove_resource','identity','old_schema','old_required','old_fixture',
    'unknown_fact','wrong_fact','missing_target','new_field_collision','required_for_live','relationship_reinterpretation'])
def test_unsupported_or_unadmitted_migrations_fail_before_resource_io(fault):
    previous,value,fork,old,new=fixture()
    if fault=='remove_resource':del new['work']
    if fault=='identity':value['document']['resources'][0]['identity_field_refs']=['title']
    if fault=='old_schema':new['work']['data_definition']['record_schema']['properties']['state']={'type':'number'}
    if fault=='old_required':new['work']['data_definition']['record_schema']['required'].append('title')
    if fault=='old_fixture':new['work']['data_definition']['seed'][0]['state']='accepted'
    if fault=='unknown_fact':value['brief']['task_contract']['facts']=[]
    if fault=='wrong_fact':value['brief']['task_contract']['facts'][0]['value']='T-B'
    if fault=='missing_target':
        new['teams']['data_definition']['seed']=[]
        value['document']['resources'][1]['records']=[]
        value['brief']['task_contract']['facts']=value['brief']['task_contract']['facts'][:2]
    if fault=='new_field_collision':fork['resources'][0]['records'][0]['team_id']='operator-owned'
    if fault=='required_for_live':
        fork['resources'][0]['records'].append({'id':'live','revision':1,'title':'New','state':'open'})
        new['work']['data_definition']['record_schema']['properties']['team_id']={'type':'string'}
    if fault=='relationship_reinterpretation':
        previous['document']['relationships']=copy.deepcopy(value['document']['relationships'])
        value['document']['relationships'][0]['label_field_refs']=[]
    fork['resources'][0]['records_digest']=digest(fork['resources'][0]['records'])
    with pytest.raises(PrototypePublicationRejected):derive(previous,value,fork,old,new)


def test_missing_task_admission_does_not_expand_the_legacy_ui_only_boundary():
    previous,value,fork,old,new=fixture();value['brief']=None
    with pytest.raises(PrototypePublicationRejected,match='admitted task contract'):derive(previous,value,fork,old,new)


def test_publication_fork_pins_additive_records_without_replacing_source_seeds(monkeypatch):
    from types import SimpleNamespace
    from adaos.services.builder import prototype_runtime_forks as forks
    previous,value,fork,old,new=fixture()
    previous['seed']={};value['seed']['project_ref']='project:test'
    fork=seal({**fork,'base_publication_ref':'prototype-publication:base'},id_key='fork_id',prefix='runtime-fork:')
    for name,spec in new.items():spec['resource_definition']={'resource_type':'test.'+name,'metadata':{}}
    monkeypatch.setattr(forks,'bindings',lambda item:old if item is previous else new)
    store=SimpleNamespace(load=lambda ref:previous,_runtime_project_ref=lambda seed:'project:test')
    before=copy.deepcopy(value['document'])
    value['runtime_fork']=fork
    forks.apply(store,value,fork)
    assert value['document']==before
    assert forks.initial_records(value,new['work'])[0]['state']=='accepted'
    assert new['work']['data_definition']['seed'][0]['state']=='open'
    assert new['work']['resource_definition']['metadata']['prototype_runtime_evolution_ref']==value['runtime_evolution']['evolution_id']
    before=copy.deepcopy(value)
    forks.apply(store,value,fork)
    assert value==before
