import hashlib
import json
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation_sources import capture_automation_sources, materialized_automation_matches_acceptance
from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.skill_factory_sources import source_tree_digest


@pytest.mark.parametrize('defect', ['none', 'other_design', 'failed_task', 'corrupt_snapshot'])
def test_implemented_base_requires_completed_task_and_exact_design_lineage(tmp_path, defect):
    state=tmp_path/'state';runs=tmp_path/'runs'
    root=state/'builder/workflow_snapshots/scenario/demo/automation';root.mkdir(parents=True)
    source=b'{"implemented":true}'
    (root/'webui.json').write_bytes(source)
    (root/'snapshot.json').write_text(json.dumps({'schema':'adaos.builder.automation_snapshot.v2',
        'object_type':'scenario','object_id':'demo','task_id':'task.demo',
        'file_digests':{'webui.json':hashlib.sha256(source).hexdigest()}}),encoding='utf8')
    acceptance={'decision':'accepted','revision':'002','webui_digest':'sha256:design-two'}
    assignment=runs/'task.demo/input/assignment.json';assignment.parent.mkdir(parents=True)
    assignment.write_text(json.dumps({'target':{'type':'scenario','id':'demo'},'realize_request':{'artifacts':{
        'prototype_acceptance':{**acceptance,**({'webui_digest':'sha256:other-design'} if defect=='other_design' else {})}}}}),encoding='utf8')
    if defect=='corrupt_snapshot':(root/'webui.json').write_bytes(b'changed')
    factory=SimpleNamespace(read_task=lambda _: {'status':'failed' if defect=='failed_task' else 'completed'})
    assert materialized_automation_matches_acceptance(state_dir=state,runs_root=runs,factory=factory,
        object_type='scenario',object_id='demo',acceptance=acceptance) is (defect=='none')


def test_followup_preserves_implemented_dev_and_edits_without_reseeding_the_prototype(tmp_path):
    source=tmp_path/'dev';source.mkdir()
    (source/'webui.json').write_text('{"implemented":"with current user edits"}',encoding='utf8')
    prototype={'schema':'adaos.webui.v1','ui':{}}
    acceptance={'decision':'accepted','revision':'002','webui_digest':prototype_webui_digest(prototype)}
    workflow=SimpleNamespace(_committed_prototype_publication=lambda *_: {
        'seed':{'revision':'002'},'compiled':{'webui':prototype},'document':{}})
    args=dict(workflow=workflow,object_type='scenario',object_id='demo',acceptance=acceptance,
        state_dir=tmp_path/'state',artifacts=[('scenario','demo',source)],attachments=[],created_at='2026-11-03T00:00:00Z')
    preserved=capture_automation_sources(**args,preserve_materialized_source=True)
    assert preserved['artifacts'][0]['digest']==source_tree_digest(source,excluded_dirs=frozenset({'artifacts'}))
    assert 'activation_source_digest' not in preserved['artifacts'][0]
    seeded=capture_automation_sources(**args)
    assert seeded['artifacts'][0]['digest']!=preserved['artifacts'][0]['digest']
    assert seeded['artifacts'][0]['activation_source_digest']==preserved['artifacts'][0]['digest']
    with pytest.raises(ValueError,match='accepted revision'):
        capture_automation_sources(**{**args,'acceptance':{**acceptance,'webui_digest':'sha256:wrong'}},preserve_materialized_source=True)
