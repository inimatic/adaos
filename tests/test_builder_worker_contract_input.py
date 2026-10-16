import json
from pathlib import Path
import pytest
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, CodexRunResult
from adaos.services.ui_component_contracts import project_component_contract
from adaos.services.mcp_tool_scope import effective_mcp_tool_scope
from adaos.services.skill_factory_worker import SubprocessCodexExecutor


@pytest.mark.parametrize('profile', [
    {'enabled': False, 'url': 'http://root.invalid/mcp', 'enabled_tools': ['get_descriptor_item']},
    {'url': 'http://root.invalid/mcp', 'enabled_tools': ['get_descriptor_item'], 'disabled_tools': ['get_descriptor_item']},
    {'transport': 'stdio', 'url': 'http://root.invalid/mcp', 'enabled_tools': ['get_descriptor_item']},
])
def test_final_worker_facts_use_the_same_effective_scope_as_executor(tmp_path, profile):
    calls = []
    worker = LocalSkillFactoryWorker(state_dir=tmp_path/'state', repo_root=tmp_path/'repo',
        dev_skills_root=tmp_path/'skills', dev_scenarios_root=tmp_path/'scenarios',
        executor=lambda **kw: calls.append(kw) or CodexRunResult(returncode=0))
    assignment = {'realize_request': {'artifacts': {'component_contract_input': {
        'snapshot': project_component_contract(), 'required_ids': ['ui.table']}}}}
    worker._execute_codex(task_id='task.scope', assignment=assignment, workspace=tmp_path,
        prompt='Use platform facts', output_dir=tmp_path/'attempt/output', root_mcp=profile)
    assert json.loads(calls[0]['developer_instructions'])['platform_contracts']['callable_tools'] == []
    scope = effective_mcp_tool_scope(profile)
    args = SubprocessCodexExecutor._root_mcp_config_args(profile)
    assert not args if not scope.enabled else any('enabled_tools=[]' in arg for arg in args)


def test_effective_scope_distinguishes_unbounded_from_explicit_deny_all():
    assert effective_mcp_tool_scope({'url': 'https://root.invalid/mcp'}).tools is None
    assert effective_mcp_tool_scope({'url': 'https://root.invalid/mcp', 'enabled_tools': []}).tools == ()
    assert effective_mcp_tool_scope({'url': 'https://root.invalid/mcp', 'enabled_tools': ['read', 'write', 'read'],
        'disabled_tools': ['write']}).tools == ('read',)


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
    from adaos.services.context_control import ContextControlService
    contexts = ContextControlService(tmp_path/'state')
    saved = [contexts.get_artifact(json.loads(path.read_text())['evidence_receipt']['ref']) for path in sorted(receipts)]
    assert saved[1]['parent_evidence_ref'] == json.loads(sorted(receipts)[0].read_text())['evidence_receipt']['ref']
    assert saved[0]['prompt'] == calls[0]['prompt'] and saved[1]['prompt'] == calls[1]['prompt']
    assert saved[1]['assignment'] == assignment
    assignment['realize_request']['artifacts']['component_contract_input']['required_ids'] = ['ui.form']
    with pytest.raises(ValueError, match='resume_snapshot_conflict'):
        worker._execute_codex(task_id='task.contract', assignment=assignment, workspace=tmp_path,
            prompt='Changed pins', output_dir=tmp_path/'attempt/output')
    assert len(calls) == 2


def test_paid_output_and_validation_are_acknowledged_before_next_attempt_overwrites_them(tmp_path):
    from adaos.services.context_control import ContextControlService
    import base64
    output = tmp_path / 'attempt/output'
    output.mkdir(parents=True)
    raw = 'Естественная кожа 👩🏽‍💻'.encode('utf-8')
    (output/'last_message.md').write_bytes(raw)
    (output/'test_report.json').write_text('{"ok":false,"errors":["prior failure"]}', encoding='utf-8')
    state_dir = tmp_path/'state'
    LocalSkillFactoryWorker._archive_model_output('paid-task', output, 1, state_dir=state_dir)
    receipt = json.loads((output/'model-attempts/001/receipt.json').read_text())
    saved = ContextControlService(state_dir).get_artifact(receipt['evidence_receipt']['ref'])
    assert base64.b64decode(saved['content_base64']['last_message.md']) == raw
    assert 'prior failure' in base64.b64decode(saved['content_base64']['test_report.json']).decode()


def test_unacknowledged_input_never_reaches_executor(tmp_path, monkeypatch):
    from adaos.services.builder import llm_input_attribution
    calls = []
    def fail(*args, **kwargs):
        raise OSError('injected disk full')
    monkeypatch.setattr(llm_input_attribution, 'retain_llm_evidence', fail)
    worker = LocalSkillFactoryWorker(state_dir=tmp_path/'state', repo_root=tmp_path/'repo',
        dev_skills_root=tmp_path/'skills', dev_scenarios_root=tmp_path/'scenarios',
        executor=lambda **kw: calls.append(kw) or CodexRunResult(returncode=0))
    with pytest.raises(OSError, match='disk full'):
        worker._execute_codex(task_id='no-paid-call', workspace=tmp_path, prompt='Task', output_dir=tmp_path/'attempt/output')
    assert calls == []
