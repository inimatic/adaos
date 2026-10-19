"""A failed transport without output is not a malformed successful completion."""
import pytest
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, CodexRunResult


@pytest.mark.parametrize('returncode,final', [(1, ''), (0, ''), (1, 'not-json')])
def test_structured_executor_preserves_failure_and_still_validates_completions(tmp_path, returncode, final):
    class Executor:
        returns_structured_outcome = True
        def __call__(self, **kwargs):
            return CodexRunResult(returncode=returncode, final_message=final, stderr='remote_failure_identity')
    worker = LocalSkillFactoryWorker(state_dir=tmp_path / 'state', repo_root=tmp_path,
        dev_skills_root=tmp_path / 'skills', dev_scenarios_root=tmp_path / 'scenarios', executor=Executor())
    result = worker._execute_codex(task_id='task.structured.failure', workspace=tmp_path,
        prompt='Synthetic diagnostic test only.', output_dir=tmp_path / 'output')
    assert 'remote_failure_identity' in result.stderr
    if returncode != 0 and not final:
        assert 'Invalid Automation outcome' not in result.stderr
        assert result.returncode == returncode
    else:
        assert 'Invalid Automation outcome' in result.stderr
        assert result.returncode != 0
