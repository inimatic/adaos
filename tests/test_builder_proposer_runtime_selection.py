from adaos.sdk.builder import prototype
from adaos.adapters.builder.legacy_dev_skill import WorkspaceSkillPrototypeExecution


def test_bounded_request_uses_installed_workspace_and_never_legacy_dev(monkeypatch):
    class Manager:
        def run_tool(self, name, tool, payload, **kwargs):
            assert name == 'builder_skill' and tool == 'chat'
            assert payload['_meta']['builder_semantic_edit_mode'] == 'bounded_ops'
            return {'ok': True, 'status': 'semantic_proposal_ready'}
        def run_dev_tool(self, *args, **kwargs):
            raise AssertionError('A different DEV slot is not the installed Builder')
    port = WorkspaceSkillPrototypeExecution()
    port._skill_manager = Manager()
    monkeypatch.setattr(prototype, '_WORKSPACE_PORT', port)
    result = prototype.submit_request('Change a heading', webspace_id='host',
        metadata={'builder_semantic_edit_mode': 'bounded_ops',
                  'builder_selected_target': {'object_type': 'scenario', 'object_id': 'selected'}})
    assert result['sdk']['execution_adapter'] == 'workspace_skill.v1'


def test_scenario_workflow_uses_the_canonical_builder_conversation():
    from adaos.services.scenario.workflow_runtime import _builder_conversation_id
    from adaos.services.conversation_links import builder_conversation_id
    assert _builder_conversation_id('desktop') == _builder_conversation_id('desktop-dev') == builder_conversation_id()


def test_core_fences_the_request_before_resolving_or_billing_another_project():
    import pytest
    from adaos.services.builder.semantic_request_scope import request_target
    from adaos.services.builder.semantic_admission import SemanticAdmissionService
    from adaos.services.builder.workflow import BuilderWorkflowError
    service = SemanticAdmissionService(None)
    with request_target('scenario:selected'), pytest.raises(BuilderWorkflowError, match='caller-selected'):
        service._location('scenario:other')
