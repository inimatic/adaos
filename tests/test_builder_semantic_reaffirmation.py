import copy

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.semantic_reaffirmation import candidate_findings, requirement_closure
from adaos.services.builder.interpretation_boundary import reference_context
from adaos.services.builder.task_interpretation import admit_interpretation, interpretation_contract, TaskInterpretationRejected
from adaos.services.builder.task_spec import seal
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from test_builder_semantic_prototype import _multi_resource_fixture
from test_builder_semantic_ui_constraints import proposal


def basis(*, provider_canonical=True, with_peer=False):
    intent, task, value = proposal('Show a list.\nShow a table.' if with_peer else 'Show a list.')
    value['requirements'][0]['statement'] = 'Show a list.'
    if with_peer:
        value['clause_roles'].append({'clause_id': 'clause:002', 'roles': ['product']})
        value['requirements'].append({**value['requirements'][0], 'id': 'peer',
            'clause_ids': ['clause:002'], 'statement': 'Show a table.'})
    _, brief = admit_interpretation(task, value, intent)
    _, source = _multi_resource_fixture()
    document = compile_semantic_prototype(source)['semantic_document']
    ref = brief['residual_requirements'][0]['id']
    document.update(brief_ref=brief['brief_id'], brief_digest=brief['digest'],
        requirement_bindings=[{'requirement_ref': ref, 'semantic_refs': ['view:work-list']}])
    if with_peer:
        document['requirement_bindings'].append({'requirement_ref': brief['residual_requirements'][1]['id'],
            'semantic_refs': ['view:people-list']})
    # All old definitions stay canonical; only their admitted proof authority is
    # replaced in this deliberately constructed, model-free test fixture.
    if provider_canonical:
        from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
        from test_builder_semantic_prototype import _multi_resource_candidate
        # Use the actual candidate compiler to establish the same fully closed
        # baseline that native generation publishes. A hand-built graph with
        # missing defaults/owner bindings is not an unchanged provider baseline.
        document = compile_semantic_prototype_candidate(_multi_resource_candidate(document),
            brief=brief)['semantic_document']
    return brief, document, ref


def reaffirm(text='Preserve the existing list for resource "work_items" unchanged.'):
    previous_brief, document, ref = basis()
    intent, task, value = proposal(text)
    task = seal({**task, 'reference_context': reference_context(document, previous_brief)})
    value['requirements'][0].update(kind='behavior', operation=None, verification_family='preservation', reaffirms=[ref])
    return intent, task, value, previous_brief, document, ref


def test_reaffirmation_has_exact_owner_effect_closure_and_no_new_operation_or_proof():
    intent, task, value, previous_brief, previous, ref = reaffirm()
    _, brief = admit_interpretation(task, value, intent, previous_brief=previous_brief)
    assert len(brief['residual_requirements']) == len(previous_brief['residual_requirements']) == 1
    assert brief['operations'] == []
    relation = brief['task_contract']['reaffirmations'][0]
    assert relation['prior_requirements'][0]['requirement_ref'] == ref
    assert relation['prior_requirements'][0]['closure_digest'] == requirement_closure(previous, ref)['closure_digest']
    candidate = copy.deepcopy(previous)
    candidate.update(brief_ref=brief['brief_id'], brief_digest=brief['digest'])
    assert candidate_findings(candidate, brief, previous) == []
    assert candidate_findings(candidate, brief, None, require_baseline=False) == []
    assert candidate_findings(candidate, brief, None)[0]['code'] == 'task.reaffirmation_changed'


def test_batch_closures_validate_once_and_keep_single_root_semantics(monkeypatch):
    from adaos.services.builder import semantic_graph_model as model
    _,document,_=basis()
    graph=model.semantic_document_to_graph(document)
    roots=[[node['ref']] for node in graph['nodes'][:8]]
    expected=[model.semantic_graph_dependency_closure(graph,group) for group in roots]
    original=model.validate_semantic_graph;calls=[]
    def validate(value):calls.append(1);return original(value)
    monkeypatch.setattr(model,'validate_semantic_graph',validate)
    assert model.semantic_graph_dependency_closures(graph,roots)==expected
    assert len(calls)==1
    with pytest.raises(ValueError):model.semantic_graph_dependency_closures(graph,[['view:foreign']])


@pytest.mark.parametrize('mutation', ['owner', 'query_scope', 'precondition', 'effect', 'binding', 'base'])
def test_reuse_rejects_changed_semantic_owner_scope_guard_effect_and_base(mutation):
    intent, task, value, old_brief, previous, ref = reaffirm()
    # Bind the protected requirement to both the collection and its command;
    # dependency closure must include all editor/resource effects, not labels.
    previous['requirement_bindings'][0]['semantic_refs'].append('command:' + previous['commands'][0]['id'])
    task = seal({**task, 'reference_context': reference_context(previous, old_brief)})
    _, brief = admit_interpretation(task, value, intent, previous_brief=old_brief)
    candidate = copy.deepcopy(previous)
    collection = next(v for v in candidate['views'] if v['id'] == 'work-list')
    if mutation == 'owner': collection['id'] = 'new-owner'
    elif mutation == 'query_scope': collection['scope_filters'] = [{'field_ref': 'status', 'value': 'changed'}]
    elif mutation == 'precondition': candidate['resources'][0]['read_only_when'] = {'field_ref': 'status', 'operator': 'not_equals', 'value': 'closed'}
    elif mutation == 'effect': candidate['commands'][0]['fixed_values']['status'] = 'changed'
    elif mutation == 'binding': candidate['requirement_bindings'][0]['semantic_refs'] = ['view:people-list']
    else: previous = {**previous, 'title': {'en': 'Different base'}}
    assert candidate_findings(candidate, brief, previous)


@pytest.mark.parametrize('mutation', ['actor', 'foreign_ref', 'superseded', 'new_operation', 'amendment'])
def test_reaffirmation_cannot_grant_changed_authority_or_amendment(mutation):
    intent, task, value, old_brief, previous, ref = reaffirm()
    if mutation == 'actor': task = seal({**task, 'authority': {**task['authority'], 'external_effects': 'allowed'}})
    elif mutation == 'foreign_ref': value['requirements'][0]['reaffirms'] = ['req:foreign']
    elif mutation == 'superseded':
        old_brief = copy.deepcopy(old_brief)
        old_brief['task_contract']['requirements'][0]['status'] = 'superseded'
        old_brief = seal(old_brief, id_key='brief_id', prefix='brief:')
    elif mutation == 'new_operation': value['requirements'][0]['operation'] = 'search'
    else: value['requirements'][0]['supersedes'] = [ref]
    with pytest.raises(TaskInterpretationRejected):
        admit_interpretation(task, value, intent, previous_brief=old_brief)


@pytest.mark.parametrize('source', ['Do not preserve the table.', 'If approved, preserve the table.',
                                   'Preserve the table but change its search scope.'])
def test_nonpositive_or_mixed_source_is_not_an_unchanged_directive(source):
    intent, task, value, old_brief, _, _ = reaffirm(source)
    with pytest.raises(TaskInterpretationRejected):
        admit_interpretation(task, value, intent, previous_brief=old_brief)


def test_only_preservation_branch_advertises_exact_reaffirmable_refs():
    _, task, value, old_brief, _, ref = reaffirm()
    contract = interpretation_contract(provider=True, task=task, previous_brief=old_brief)
    requirement = copy.deepcopy(value['requirements'][0])
    validator = Draft202012Validator({'$ref': '#/$defs/requirement', '$defs': contract['$defs']})
    validator.validate(requirement)
    requirement.update(operation='search', verification_family='query')
    assert list(validator.iter_errors(requirement))


@pytest.mark.parametrize('text', [
    'Preserve the two tabs, both directories and open-only Accept.',
    'This is intentionally different; retain that filtering.',
    'If approved, preserve the existing list for resource "work_items" unchanged.',
    'Record R1: note "Preserve the existing list for resource work_items unchanged".',
    'Preserve the existing list for resource "work_items" unchanged but change its search.',
])
def test_unavailable_source_reaffirmation_is_not_offered_by_fresh_provider_schema(text):
    from adaos.services.builder.semantic_reaffirmation import source_reaffirmation_options
    from adaos.services.builder.task_interpretation import interpretation_messages
    import json
    _, task, value, old_brief, _, _ = reaffirm(text)
    assert source_reaffirmation_options(task, old_brief) == []
    schema = interpretation_contract(provider=True, task=task, previous_brief=old_brief)
    validator = Draft202012Validator({'$ref':'#/$defs/requirement','$defs':schema['$defs']})
    assert list(validator.iter_errors(value['requirements'][0]))
    value['requirements'][0]['reaffirms'] = []
    validator.validate(value['requirements'][0])
    delivered = json.loads(interpretation_messages(task,previous_brief=old_brief)[1]['content'])['interpretation_request']
    assert delivered['reaffirmation_source_options'] == []


def test_shortened_positive_quote_cannot_hide_a_trailing_source_amendment():
    text = 'Preserve the existing list for resource "work_items" unchanged but change its search.'
    intent, task, value, old_brief, _, _ = reaffirm(text)
    value['requirements'][0]['statement'] = 'Preserve the existing list for resource "work_items" unchanged'
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task,value,intent,previous_brief=old_brief)
    assert any(f['code']=='task.reaffirmation_reference_unproved' for f in caught.value.findings)


def test_changed_authority_does_not_advertise_prior_proof_reuse():
    _, task, value, old_brief, _, _ = reaffirm()
    task = seal({**task,'authority':{**task['authority'],'external_effects':'allowed'}})
    schema = interpretation_contract(provider=True,task=task,previous_brief=old_brief)
    validator = Draft202012Validator({'$ref':'#/$defs/requirement','$defs':schema['$defs']})
    assert list(validator.iter_errors(value['requirements'][0]))
    value['requirements'][0]['reaffirms'] = []
    validator.validate(value['requirements'][0])


def test_source_reuse_needs_the_exact_prior_authority_not_context_cards_alone():
    from adaos.services.builder.semantic_reaffirmation import source_reaffirmation_options
    _, task, value, old_brief, _, ref = reaffirm()
    assert source_reaffirmation_options(task) == []
    options = source_reaffirmation_options(task,old_brief)
    assert len(options)==1 and ref in options[0]['candidate_requirement_refs']
    schema = interpretation_contract(provider=True,task=task)
    assert list(Draft202012Validator({'$ref':'#/$defs/requirement','$defs':schema['$defs']}).iter_errors(value['requirements'][0]))


def test_reaffirmation_survives_the_complete_candidate_compiler_without_auto_binding():
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate
    from test_builder_semantic_prototype import _multi_resource_candidate
    intent, task, value, old_brief, previous, ref = reaffirm()
    _, brief = admit_interpretation(task, value, intent, previous_brief=old_brief)
    candidate = _multi_resource_candidate(previous)
    result = compile_semantic_prototype_candidate(candidate, brief=brief, previous=previous)
    bindings = result['semantic_document']['requirement_bindings']
    assert [row['requirement_ref'] for row in bindings] == [ref]
    assert result['semantic_document']['brief_ref'] == brief['brief_id']


def test_noncanonical_manual_baseline_cannot_claim_an_unchanged_provider_roundtrip():
    from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate, SemanticPrototypeValidationError
    from test_builder_semantic_prototype import _multi_resource_candidate
    old, previous, ref = basis(provider_canonical=False)
    intent, task, value = proposal('Preserve the existing list for resource "work_items" unchanged.')
    task = seal({**task, 'reference_context': reference_context(previous, old)})
    value['requirements'][0].update(kind='behavior', operation=None, verification_family='preservation', reaffirms=[ref])
    _, brief = admit_interpretation(task, value, intent, previous_brief=old)
    with pytest.raises(SemanticPrototypeValidationError) as caught:
        compile_semantic_prototype_candidate(_multi_resource_candidate(previous), brief=brief, previous=previous)
    assert any(row['code'] == 'task.reaffirmation_changed' for row in caught.value.findings)


def test_an_active_unrelated_requirement_cannot_waive_the_actual_source_target():
    old, previous, _ = basis(with_peer=True)
    intent, task, value = proposal('Preserve the existing list for resource "work_items" unchanged.')
    task = seal({**task, 'reference_context': reference_context(previous, old)})
    value['requirements'][0].update(kind='behavior', operation=None, verification_family='preservation',
        reaffirms=[old['residual_requirements'][1]['id']])
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, value, intent, previous_brief=old)
    assert caught.value.findings[0]['code'] == 'task.reaffirmation_reference_unproved'


@pytest.mark.parametrize('source', ['Preserve the list and Accept semantics unchanged.',
    'Preserve the literal "all existing features".', 'Preserve the stored state unchanged.'])
def test_partial_noun_recognition_cannot_waive_unparsed_preservation(source):
    from adaos.services.builder.semantic_reaffirmation import preservation_targets
    old, previous, _ = basis()
    assert preservation_targets(source, reference_context(previous, old)) == set()


@pytest.mark.parametrize('prefix', ['If approved, ', 'Do not ', 'For example, ', 'The note says: "'])
def test_a_short_quote_cannot_strip_source_authority_frame(prefix):
    quote = 'Preserve the existing list for resource "work_items" unchanged.'
    source = prefix + quote + ('"' if prefix.endswith('"') else '')
    intent, task, value, old, _, _ = reaffirm(source)
    value['requirements'][0]['statement'] = quote
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, value, intent, previous_brief=old)
    assert any(row['code'] == 'task.reaffirmation_source_authority_unproved' for row in caught.value.findings)


def test_a_markdown_bullet_is_formatting_not_a_source_authority_change():
    quote = 'Preserve the existing list for resource "work_items" unchanged.'
    intent, task, value, old, _, _ = reaffirm('- ' + quote)
    value['requirements'][0]['statement'] = quote
    _, brief = admit_interpretation(task, value, intent, previous_brief=old)
    assert brief['task_contract']['reaffirmations'][0]['source_targets'] == ['view:work-list']
