import json

import pytest

from adaos.sdk.developer import ui
from adaos.services.ui_widget_inputs import static_widget_data_findings
from adaos.services.scenario.validation import _scenario_webui_contract_issues


def document(record, task_key=None):
    return {'ui':{'application':{'desktop':{'pageSchema':{'widgets':[
        {'id':'wait','type':'feedback.taskProgress','inputs':{'taskKey':task_key} if task_key else {},
         'dataSource':{'kind':'static','value':record}}]}}}}}


@pytest.mark.parametrize('record', [
    {'id':'request','status':'running','title':'Wait','steps':[]},
    {'id':'request','status':'running','progress':{'completed':3,'total':2}},
    {'id':'request','status':'running','stages':[{'id':'a','label':'A','status':'running'}]*2},
])
def test_literal_record_is_rejected_by_authoring_and_scenario_preflight(record, tmp_path):
    doc = document(record)
    result = ui.validate_contract(doc)
    assert not result['ok']
    assert any(f['code']=='ui.component.data_invalid' for f in result['findings'])
    assert 'adaos.ui.task_progress.v1' in result['detail']
    manifest = tmp_path/'scenario.yaml'
    manifest.write_text('id: sample\nversion: 1.0.0\n',encoding='utf-8')
    (tmp_path/'webui.json').write_text(json.dumps(doc),encoding='utf-8')
    issues = _scenario_webui_contract_issues(manifest, (), ())
    assert any(i.level=='error' and i.code=='ui.component.data_invalid' for i in issues)


@pytest.mark.parametrize('value,key', [
    ({'id':'request','status':'running'},None),
    ([{'id':'request','status':'running'}],None),
    ({'items':[{'id':'request','status':'running'}]},None),
    ({'result':{'id':'request','status':'running'}},'result'),
    ('$state.task',None),
    ({'id':'request','status':'$state.status'},None),
])
def test_valid_literal_wrappers_and_runtime_bindings_remain_supported(value,key):
    assert ui.validate_contract(document(value,key))['ok']


def test_group_error_points_to_the_actual_child_record():
    doc={'widgets':[{'id':'group','type':'layout.group','children':[
        document({'id':'request','status':'invalid'})['ui']['application']['desktop']['pageSchema']['widgets'][0]]}]}
    result=static_widget_data_findings(doc)
    assert result[0]['path']=='$.widgets[0].children[0].dataSource.value.status'
