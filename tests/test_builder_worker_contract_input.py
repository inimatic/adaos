import json
from pathlib import Path
import pytest
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, CodexRunResult
from adaos.services.ui_component_contracts import project_component_contract


def test_worker_generation_and_repair_submit_final_admitted_developer_facts_and_reject_rebase(tmp_path):
    calls = []
    def executor(**kwargs):
        calls.append(kwargs)
        return CodexRunResult(returncode=0)
    worker = LocalSkillFactoryWorker(state_dir=tmp_path/'state', repo_root=tmp_path/'repo',
        dev_skills_root=tmp_path/'skills', dev_scenarios_root=tmp_path/'scenarios', executor=executor)
    snapshot = project_component_contract()
    assignment = {'realize_request': {'artifacts': {'component_contract_input': {'snapshot': snapshot, 'required_ids': ['ui.table']}}}}
    for prompt in ['Initial generation', 'Repair a reported defect']:
        worker._execute_codex(task_id='task.contract', assignment=assignment, workspace=tmp_path,
            prompt=prompt, output_dir=tmp_path/'attempt/output', root_mcp={'url': 'http://root.invalid/mcp', 'enabled_tools': ['get_descriptor_item']})
    assert len(calls) == 2
    assert all(json.loads(call['developer_instructions'])['platform_contracts']['callable_tools'] == ['get_descriptor_item'] for call in calls)
    receipts = list((tmp_path/'attempt/input/model-attempts').glob('*.json'))
    assert len(receipts) == 2 and all(json.loads(path.read_text())['contract_input_admission']['status'] == 'admitted' for path in receipts)
    assignment['realize_request']['artifacts']['component_contract_input']['required_ids'] = ['ui.form']
    with pytest.raises(ValueError, match='resume_snapshot_conflict'):
        worker._execute_codex(task_id='task.contract', assignment=assignment, workspace=tmp_path,
            prompt='Changed pins', output_dir=tmp_path/'attempt/output')
    assert len(calls) == 2
