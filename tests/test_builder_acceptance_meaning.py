import copy

import pytest

from adaos.services.builder.prototype_acceptance import build_prototype_acceptance, admit_prototype_acceptance
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder_context import digest
from test_builder_semantic_ui_constraints import proposal
from test_prototype_acceptance import _webui, _visual_checks


def meaning():
    text = 'Show a read-only board. Do not enable dragging, moving cards, editing or a new write command.'
    intent, task, value = proposal(text)
    _, brief = admit_interpretation(task, value, intent)
    return intent, brief, {'publication_ref': 'prototype-publication:' + 'a' * 24,
        'intent_ref': intent['intent_id'], 'intent_digest': intent['digest'],
        'brief_ref': brief['brief_id'], 'brief_digest': brief['digest'], 'semantic_digest': digest({'exact': 'source'})}


def acceptance(**overrides):
    intent, brief, evidence = meaning()
    webui = _webui()
    board = webui['ui']['application']['desktop']['pageSchema']['widgets'][1]
    board['inputs']['dragDrop'] = False
    board['actions'] = []
    return build_prototype_acceptance(**{**dict(acceptance_id='acceptance:meaning',
        project_ref='project:kanban', change_id='change:test', revision='001', webui=webui,
        request=intent['statement'], intent=intent, prototype_brief=brief, meaning_evidence=evidence,
        reviewer={'id': 'agent:test', 'kind': 'agent'},
        behavior_checks=[{'id': 'render.ready', 'status': 'passed', 'evidence_refs': ['probe:render']}],
        visual_checks=_visual_checks()), **overrides})


def test_acceptance_uses_admitted_readonly_meaning_not_a_new_board_recipe():
    result = acceptance()
    assert result['deterministic_evaluation']['ok']
    assert result['admitted_meaning'] == meaning()[2]
    assert not result['deterministic_evaluation']['qualification']['requirements'].get('drag_drop')


@pytest.mark.parametrize('field', ['intent_ref', 'intent_digest', 'brief_ref', 'brief_digest'])
def test_forged_meaning_pair_rejected(field):
    evidence = meaning()[2]
    evidence[field] = 'wrong'
    with pytest.raises(BuilderWorkflowError, match='meaning identity mismatch'):
        acceptance(meaning_evidence=evidence)


@pytest.mark.parametrize('field', ['publication_ref', 'semantic_digest', 'brief_ref', 'intent_digest'])
def test_automation_gate_rejects_changed_or_missing_meaning(field):
    result = acceptance()
    expected = copy.deepcopy(result['admitted_meaning'])
    expected[field] = 'changed'
    with pytest.raises(BuilderWorkflowError, match='admitted_meaning'):
        admit_prototype_acceptance(result, expected_project_ref='project:kanban',
            expected_change_id='change:test', expected_revision='001',
            expected_webui_digest=result['webui_digest'], expected_admitted_meaning=expected)


def test_partial_or_different_source_cannot_enable_semantic_evaluator():
    with pytest.raises(BuilderWorkflowError, match='complete retained meaning'):
        acceptance(meaning_evidence=None)
    with pytest.raises(BuilderWorkflowError, match='meaning identity mismatch'):
        acceptance(request='different source')


def test_workflow_resolves_only_committed_meaning_and_rejects_stale_snapshot(monkeypatch):
    intent, brief, evidence = meaning()
    service = object.__new__(BuilderWorkflowService)
    document = {'brief_ref': brief['brief_id'], 'brief_digest': brief['digest']}
    publication = {'brief': brief, 'document': document, 'seed': {'revision': '011', 'change_id': 'change:test'},
        'webui_digest': digest({'webui': 'exact'}), 'publication_ref': evidence['publication_ref']}
    monkeypatch.setattr(BuilderWorkflowService, '_committed_prototype_publication', lambda *args: copy.deepcopy(publication))
    validated = []
    monkeypatch.setattr('adaos.services.builder.semantic_prototype.validate_semantic_prototype',
                        lambda doc, **kwargs: validated.append((doc, kwargs)))
    kwargs = dict(revision='011', webui_digest=publication['webui_digest'], change_id='change:test')
    result = service._prototype_acceptance_meaning('project', 'kanban', **kwargs)
    assert result['intent'] == intent and result['prototype_brief'] == brief
    assert validated == [(document, {'brief': brief})]
    with pytest.raises(BuilderWorkflowError, match='meaning mismatch'):
        service._prototype_acceptance_meaning('project', 'kanban', **{**kwargs, 'revision': '012'})
    with pytest.raises(BuilderWorkflowError, match='meaning mismatch'):
        service._prototype_acceptance_meaning('project', 'kanban', **{**kwargs, 'change_id': 'change:other'})
    publication['document']['brief_digest'] = 'wrong'
    with pytest.raises(BuilderWorkflowError, match='meaning mismatch'):
        service._prototype_acceptance_meaning('project', 'kanban', **kwargs)


def test_automation_followup_still_compares_meaning_with_the_immutable_publication(monkeypatch):
    from adaos.domain.artifact_release import canonical_payload_digest
    result = acceptance()
    expected = copy.deepcopy(result['admitted_meaning'])
    service = object.__new__(BuilderWorkflowService)
    workflow = {'active_phase': 'automation', 'prototype': {'head_revision': '001',
        'stable': True, 'acceptance_required': True, 'acceptance': result},
        'automation': {'status': 'running'}, 'change': {'change_id': 'change:test',
            'change_set_id': 'change:test', 'request': 'Continue the accepted implementation.'}}
    monkeypatch.setattr(BuilderWorkflowService, 'describe', lambda *args: workflow)
    observed = []
    def retained(self, *args, **kwargs):
        observed.append(kwargs)
        return {'meaning_evidence': expected}
    monkeypatch.setattr(BuilderWorkflowService, '_prototype_acceptance_meaning', retained)
    assert service.require_current_prototype_acceptance('project', 'kanban') == result
    assert observed[0]['change_id'] == 'change:test'
    result['admitted_meaning']['semantic_digest'] = 'sha256:' + 'b' * 64
    result['digest'] = canonical_payload_digest({key: value for key, value in result.items() if key != 'digest'})
    with pytest.raises(BuilderWorkflowError, match='admitted_meaning'):
        service.require_current_prototype_acceptance('project', 'kanban')
