import copy

import pytest

from adaos.sdk.builder import prototype
from adaos.sdk.developer import projects, prompt_context
from adaos.services.builder.generation_profiles import profile_metadata
from adaos.services.builder.interpretation_delivery import request_packet


@pytest.mark.parametrize('name', [None, 'default'])
def test_existing_projects_keep_the_default_route(name):
    assert profile_metadata(name) == {}


@pytest.mark.parametrize('name',['s2-inline-semantic-input-v1','s1-facts-inline-semantic-input-v1',
    's1-facts-inline-bounded-v1','s2-inline-bounded-v1','s2-inline-semantic-json-bounded-v1'])
def test_semantic_input_profiles_are_project_scoped_without_changing_the_account(name):
    metadata=profile_metadata(name)
    assert metadata['builder_semantic_input_profile']=='semantic-input-v1'
    assert metadata.get('builder_task_interpretation_mode')==('deterministic_facts' if name.startswith('s1-') else None)
    assert not any('model' in key or 'resource' in key or 'binding' in key for key in metadata)


@pytest.mark.parametrize('name,maximum',[('s1-facts-inline-first-pass-v1',1),('s2-inline-no-repair-v1',2),
    ('s1-facts-inline-bounded-v1',2),('s2-inline-bounded-v1',3),('s2-inline-semantic-json-bounded-v1',3)])
def test_first_pass_profiles_bound_the_whole_turn_without_changing_global_settings(name,maximum,monkeypatch):
    import os
    monkeypatch.setenv('ADAOS_AI_BUILDER_PROVIDER_CALL_LIMIT','4')
    metadata=profile_metadata(name)
    assert metadata['builder_provider_call_limit']==maximum
    assert metadata['builder_semantic_input_profile']=='semantic-input-v1'
    assert os.environ['ADAOS_AI_BUILDER_PROVIDER_CALL_LIMIT']=='4'


@pytest.mark.parametrize('name', ['s2-inline-typed-v1', 's2-inline-json-envelope-v1'])
def test_explicit_profile_does_not_select_a_model_account_or_global_default(name, monkeypatch):
    monkeypatch.setenv('ADAOS_BUILDER_TASK_PIPELINE_ENABLED', '0')
    metadata = profile_metadata(name)
    assert metadata['builder_task_pipeline'] == '1'
    assert metadata['builder_generation_run_protocol'] == 'v1'
    assert metadata['builder_context_retrieval'] == 'inline'
    assert not any('model' in key or 'resource' in key or 'binding' in key for key in metadata)
    metadata['builder_task_pipeline'] = '0'
    assert profile_metadata(name)['builder_task_pipeline'] == '1'
    import os
    assert os.environ['ADAOS_BUILDER_TASK_PIPELINE_ENABLED'] == '0'


@pytest.mark.parametrize('name', ['', 's2', 'auto', {}, False])
def test_unknown_profiles_refuse_before_preferences_write(name, monkeypatch):
    monkeypatch.setattr('adaos.sdk.access.require', lambda capability: None)
    monkeypatch.setattr(prompt_context, '_update', lambda *args, **kwargs: pytest.fail('Unknown profile was persisted'))
    with pytest.raises(ValueError, match='Unknown Prototype generation profile'):
        prototype.configure_generation_profile('project', 'owned', profile=name)


@pytest.mark.parametrize('effort', ['medium', 'high'])
def test_explicit_effort_qualification_changes_only_the_effort_control(effort):
    baseline = profile_metadata('s2-inline-no-repair-v1')
    variant = profile_metadata(f's2-inline-no-repair-{effort}-v1')
    assert variant.pop('builder_llm_reasoning_effort') == effort
    variant['builder_generation_profile'] = baseline['builder_generation_profile']
    assert variant == baseline


def test_profile_configuration_still_requires_caller_authority(monkeypatch):
    from adaos.services.policy.caller import CallerAccessDenied

    monkeypatch.setattr(prompt_context, '_update', lambda *args, **kwargs: pytest.fail('Unauthenticated profile write'))
    with pytest.raises(CallerAccessDenied):
        prototype.configure_generation_profile('project', 'owned', profile='s2-inline-typed-v1')


def test_profile_preferences_update_preserves_runs_and_selected_connection(monkeypatch):
    saved = []
    state = {'llm_profile': {'resource_ref': 'ai-resource:personal-chatgpt', 'model': 'selected'},
             'workflow': {'change': {'runs': [{'run_id': 'paid', 'status': 'running'}]}}}
    before = copy.deepcopy(state)
    monkeypatch.setattr(prompt_context, '_read', lambda *args: copy.deepcopy(state))
    monkeypatch.setattr(prompt_context, '_write', lambda kind, identity, value: saved.append(value))
    monkeypatch.setattr('adaos.sdk.access.require', lambda capability: None)
    result = prototype.configure_generation_profile('project', 'owned', profile='s2-inline-typed-v1')
    assert result['profile'] == 's2-inline-typed-v1'
    assert saved[0]['llm_profile'] == before['llm_profile']
    assert saved[0]['workflow'] == before['workflow']
    assert saved[0]['prototype_generation_profile'] == 's2-inline-typed-v1'


def test_adopted_profile_cannot_downgrade_under_the_shared_preferences_lock(monkeypatch):
    state = {'prototype_generation_profile': 's2-inline-typed-v1', 'workflow_authority': {'project_ref': 'owned'}}
    monkeypatch.setattr(prompt_context, '_read', lambda *args: copy.deepcopy(state))
    monkeypatch.setattr(prompt_context, '_write', lambda *args: pytest.fail('Adopted authoring downgraded'))
    with pytest.raises(projects.DeveloperProjectError, match='cannot downgrade'):
        prompt_context.set_preferences('project', 'owned', prototype_generation_profile='default')


def test_explicit_interpretation_packet_does_not_mutate_environment(monkeypatch):
    from test_builder_task_interpretation import prepared

    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE', 'typed')
    _, task, _ = prepared()
    explicit = request_packet(task, output_mode='json_text_envelope')
    assert explicit['mode'] == 'json_text_envelope'
    assert request_packet(task)['mode'] == 'typed'


def test_project_packet_override_reaches_the_actual_task_pipeline(tmp_path, monkeypatch):
    import json

    from test_builder_task_interpretation import prepared
    from test_builder_task_pipeline import Provider

    from adaos.services.builder.task_pipeline import prepare_generation_request

    monkeypatch.setenv('ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE', 'typed')
    intent, _, proposal = prepared()
    provider = Provider({'proposal_json': json.dumps(proposal)})
    request = {'output_mode': 'semantic_v2', 'base_request': {},
               'dynamic_request': {'instruction': intent['statement'], 'scenario_id': 'qualification'}}
    result = prepare_generation_request(request, metadata={
        'builder_task_pipeline': '1', 'builder_ai_resource_ref': 'ai-resource:personal-chatgpt',
        'prototype_intent': intent, 'builder_interpretation_output_mode': 'json_text_envelope'},
        model='selected', reasoning=None, provider=provider, journal_dir=tmp_path)
    assert result['generation_plan']['route'] == 'two_stage'
    schema = provider.submissions[0][1]['text']['format']['schema']
    assert set(schema['properties']) == {'proposal_json'}
    retained = json.loads(next(tmp_path.glob('builder-interpretation-*.json')).read_text(encoding='utf-8'))
    assert retained['interpretation_output_mode'] == 'json_text_envelope'
