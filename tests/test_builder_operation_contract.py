import copy
import json

from adaos.services.builder.operation_contract import operation_contract, query_kind_for_operation
from adaos.services.builder.task_interpretation import interpretation_contract, interpretation_messages
from adaos.services.builder.semantic_input import authoring_contract
from adaos.services.builder.semantic_prototype import semantic_prototype_generation_guidance
from test_builder_task_interpretation import prepared


def test_interpretation_and_generation_receive_the_identical_operation_vocabulary():
    _, task, _ = prepared()
    message = json.loads(interpretation_messages(task)[-1]['content'])
    shared = operation_contract()
    assert message['interpretation_request']['product_operation_contract'] == shared
    assert authoring_contract()['product_operations'] == shared
    canonical = interpretation_contract()['$defs']['requirement']
    # Nullable provider object projection is not a second operation registry.
    canonical = next(row for row in canonical['anyOf'] if row.get('type') == 'object') if 'anyOf' in canonical else canonical
    enum = canonical['properties']['operation']['enum']
    assert set(enum) == set(shared['operations'])
    assert shared['query_example']['product_operations'] == ['search']
    assert query_kind_for_operation('search') == 'search'
    assert query_kind_for_operation('filter') == 'filter'
    assert query_kind_for_operation('list') is None
    # Repeated composition cannot mutate a later turn's platform authority.
    altered = copy.deepcopy(shared)
    altered['operations']['search'] = 'invalid'
    assert altered != operation_contract() == shared


def test_layout_witness_names_real_legacy_regions_not_view_roles():
    from adaos.services.builder.semantic_prototype import _layout_reference_findings
    guidance = semantic_prototype_generation_guidance()['layout_regions']['legacy_string']
    witness = copy.deepcopy(guidance['witness'])
    witness['layout'] = {'pattern': witness['layout']}
    assert _layout_reference_findings(witness) == []
    witness['views'][1]['region_role'] = 'detail'
    assert _layout_reference_findings(witness)[0]['code'] == 'semantic.layout_region_unknown'
