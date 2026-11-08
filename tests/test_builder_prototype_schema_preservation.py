import copy

from adaos.sdk.builder.prototype import semantic_revision_context, compile_semantic_candidate
from adaos.services.builder.prototype_additive_evolution import schema_findings
from adaos.services.builder.semantic_revision import SemanticRevisionError
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate
import pytest


def test_presentation_changes_do_not_require_a_record_schema_migration():
    _, before = _multi_resource_fixture()
    after = copy.deepcopy(before)
    after['resources'][0]['fields'][0]['label']['en'] = 'New caption'
    after['resources'][0]['fields'][0]['required'] = not after['resources'][0]['fields'][0]['required']
    after['views'][0]['presentation'] = 'cards'
    assert schema_findings(before, after) == []


@pytest.mark.parametrize('fault', ['type', 'enum', 'field', 'identity', 'resource'])
def test_publication_schema_conflicts_are_candidate_findings_before_commit(fault):
    _, before = _multi_resource_fixture()
    after = copy.deepcopy(before)
    resource = after['resources'][0]
    field = resource['fields'][0]
    if fault == 'type': field['value_type'] = 'number'
    elif fault == 'enum':
        field.update(value_type='choice', options=[{'value':'narrowed', 'label': {'en':'Narrowed'}}])
    elif fault == 'field': resource['fields'].pop(0)
    elif fault == 'identity': resource['identity_field_refs'] = [field['id']]
    else: after['resources'].pop(0)
    findings = schema_findings(before, after)
    assert findings and all(row['owner'] == 'candidate' and row['gate'] == 'G8' for row in findings)


def test_sdk_repairs_schema_conflicts_from_the_exact_committed_baseline():
    brief, before = _multi_resource_fixture()
    source = _multi_resource_candidate(before)
    source['resources'][1]['fields'][1].update(value_type='choice', options=[
        {'value': value, 'label': {'en': value, 'ru': value}} for value in ['+1', '+2']])
    baseline = semantic_revision_context(before, brief,
        source_ref='prototype-publication:'+'a'*24, revision='001')
    with pytest.raises(SemanticRevisionError) as error:
        compile_semantic_candidate(source, brief=brief, previous=baseline)
    assert any(row['code'] == 'semantic.prototype_data_schema_conflict' for row in error.value.findings)
