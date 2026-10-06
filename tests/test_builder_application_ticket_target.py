import json

import pytest

from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.development_tickets import _automation_target_from_ticket, _development_source_scope, _project_identity_from_ticket


def workspace(tmp_path):
    return BuilderWorkspaceService(state_dir=tmp_path/'state', workspace_root=tmp_path/'workspace',
        dev_skills_root=tmp_path/'dev'/'skills', dev_scenarios_root=tmp_path/'dev'/'scenarios')


def manifest(root, *, project_id='drive', presentation='scenario:file_browser'):
    path=root/'projects'/'drive'/'project.yaml'
    path.parent.mkdir(parents=True,exist_ok=True)
    data={'schema':'adaos.project.v1','kind':'project','id':project_id,'version':'1.0.0','profiles':[],
        'catalog':{'title':'Drive','description':'Files','categories':[],'tags':[]},
        'lifecycle':{'uninstall':{'components':'remove_if_unreferenced','runtime_data':'retain','source_artifacts':'retain'}},
        'components':{'owned':[{'ref':'scenario:file_browser','role':'primary'}, {'ref':'skill:file_engine','role':'implementation'}],
                      'dependencies':[]},
        'entrypoints':[{'id':'main','presentation':presentation,'default':True,'bindings':{}}]}
    path.write_text(json.dumps(data),encoding='utf-8')
    return path


def test_application_ticket_resolves_owned_entrypoint_and_preserves_project(tmp_path, monkeypatch):
    service=workspace(tmp_path)
    manifest(tmp_path/'workspace')
    monkeypatch.setattr(BuilderWorkspaceService,'from_context',classmethod(lambda cls: service))
    ticket={'target_scope':{'type':'application','id':'drive'},'owner_area':'workspace'}
    target=_automation_target_from_ticket(ticket)
    assert target=={'object_type':'scenario','object_id':'file_browser','project_id':'drive','project_ref':'project:drive'}
    scope=_development_source_scope(ticket,target)
    assert scope['type']=='scenario' and scope['id']=='file_browser' and scope['project_id']=='drive'
    assert _project_identity_from_ticket(ticket)=={'project_id':'drive','project_ref':'project:drive'}
    assert service.resolve_project_repair_target('drive',preferred_ref='skill:file_engine')['object_id']=='file_engine'
    with pytest.raises(ValueError,match='owned component'):
        service.resolve_project_repair_target('drive',preferred_ref='scenario:foreign')


def test_project_target_never_guesses_or_falls_back_from_invalid_dev_authority(tmp_path):
    service=workspace(tmp_path)
    with pytest.raises(ValueError,match='authoritative project manifest'):
        service.resolve_project_repair_target('drive')
    manifest(tmp_path/'workspace')
    dev_path=manifest(tmp_path/'dev',project_id='wrong')
    with pytest.raises(ValueError,match='identity'):
        service.resolve_project_repair_target('drive')
    dev_path.write_text('{broken',encoding='utf-8')
    with pytest.raises(Exception):
        service.resolve_project_repair_target('drive')


def test_core_ownership_is_rejected_before_project_resolution(monkeypatch):
    monkeypatch.setattr(BuilderWorkspaceService,'from_context',classmethod(lambda cls: pytest.fail('must not resolve Core through Builder')))
    with pytest.raises(ValueError,match='owned by core'):
        _automation_target_from_ticket({'target_scope':{'type':'application','id':'drive'},'owner_area':'core'})
