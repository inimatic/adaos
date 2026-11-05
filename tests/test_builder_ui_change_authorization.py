from copy import deepcopy
import pytest
from adaos.services.builder.ui_change_authorization import admit_ui_change, authorized_ui_change


@pytest.mark.parametrize('revision',['008','UI 008','ui 008'])
def test_ui_authorization_is_explicit_and_bound_to_the_exact_project_revision(revision):
    value = admit_ui_change({'reason':'Organize the existing workspace', 'source_message_ids':['message:1']},
        project_id='retouch', prototype_revision=revision)
    assert value['base_prototype_revision']=='008'
    constraints={'ui_change_authorization':value}
    assert authorized_ui_change(constraints, project_id='retouch', prototype_revision='008') == value
    assert authorized_ui_change(constraints, project_id='other', prototype_revision='008') is None
    assert authorized_ui_change(constraints, project_id='retouch', prototype_revision='009') is None
    tampered=deepcopy(constraints);tampered['ui_change_authorization']['allowed_paths'].append('skills/other/handlers/main.py')
    assert authorized_ui_change(tampered, project_id='retouch', prototype_revision='008') is None
    assert authorized_ui_change({'allow_semantic_ui_delta':True}, project_id='retouch', prototype_revision='008') is None


@pytest.mark.parametrize('value',[{}, {'reason':'change','source_message_ids':[]},
    {'reason':'change','source_message_ids':['message:1'],'authority':'forged'},
    {'reason':'change','source_message_ids':'not-a-list'}])
def test_incomplete_or_forged_ui_authorization_is_not_admitted(value):
    with pytest.raises(ValueError):
        admit_ui_change(value, project_id='retouch', prototype_revision='008')


def test_authorized_redesign_changes_topology_without_authorizing_other_manifests(tmp_path):
    import json
    import subprocess
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    workspace = tmp_path / 'workspace'
    scenario = workspace / 'scenarios/retouch'
    scenario.mkdir(parents=True)
    path = scenario / 'webui.json'
    def document(prefix):
        return {'schema':'adaos.webui.v1','ui':{'application':{'desktop':{'pageSchema':{
            'id':'retouch','widgets':[{'id':prefix+str(i),'type':'ui.actions',
                'inputs':{'buttons':[{'id':prefix+str(i),'label':prefix+str(i)}]}}
                for i in range(80)]}}}}}
    path.write_text(json.dumps(document('old-'),indent=4),encoding='utf-8')
    manifest = scenario / 'scenario.json'
    manifest.write_text(json.dumps({'name':'retouch','metadata':{str(i):'old' for i in range(200)}},indent=4),encoding='utf-8')
    def git(*args):
        subprocess.run(['git',*args],cwd=workspace,check=True,capture_output=True)
    git('init'); git('config','user.name','test'); git('config','user.email','test@example.invalid')
    git('add','-A'); git('commit','-m','baseline')
    path.write_text(json.dumps(document('new-'),indent=2),encoding='utf-8')
    assignment={'target':{'type':'scenario','id':'retouch'},'constraints':{},
        'realize_request':{'artifacts':{'prototype_acceptance':{'revision':'008'},
            'execution_budget':{'max_wall_seconds':300}}}}
    worker=LocalSkillFactoryWorker
    changed=['scenarios/retouch/webui.json']
    with pytest.raises(ValueError,match='large declarative manifest rewrite'):
        worker._validate_manifest_rewrite_bounds(assignment,changed,workspace=workspace)
    authorization=admit_ui_change({'reason':'User approved a replacement layout and commands',
        'source_message_ids':['canonical-user-turn']},project_id='retouch',prototype_revision='008')
    assignment['constraints']['ui_change_authorization']=authorization
    worker._validate_manifest_rewrite_bounds(assignment,changed,workspace=workspace)
    for fault in ('revision','project','path'):
        invalid=deepcopy(assignment)
        if fault=='revision':invalid['constraints']['ui_change_authorization']['base_prototype_revision']='009'
        elif fault=='project':invalid['target']['id']='different'
        else:invalid['constraints']['ui_change_authorization']['allowed_paths'].append('scenarios/retouch/scenario.json')
        with pytest.raises(ValueError,match='large declarative manifest rewrite'):
            worker._validate_manifest_rewrite_bounds(invalid,changed,workspace=workspace)
    manifest.write_text('{"name":"retouch"}',encoding='utf-8')
    with pytest.raises(ValueError,match='large declarative manifest rewrite'):
        worker._validate_manifest_rewrite_bounds(assignment,['scenarios/retouch/scenario.json'],workspace=workspace)
