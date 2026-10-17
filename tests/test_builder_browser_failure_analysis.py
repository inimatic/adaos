import copy
import pytest

from adaos.services.builder.browser_failure_analysis import analyze_browser_receipt


def sample():
    return {'task_id': 'task.one', 'attempt': 1, 'ok': True,
        'source': {'digest': 'source', 'unchanged_during_observation': True},
        'verifier': {'before': {'runner': 'old'}, 'unchanged_during_observation': True},
        'report': {'samples': [{'layout': 'wide', 'state_snapshots': [{'snapshot': {'records': []}}],
            'environment': {'browser': 'observed'}, 'runtime_identity': {'core': 'one'},
            'foreign_api_origins': [], 'authoritative_data_settled': True}]},
        'application_journey': {'ok': True, 'repairable': False}}


@pytest.mark.parametrize('fault,owner', [('authority', 'environment'), ('verifier', 'verifier'),
    ('source', 'environment'), ('candidate', 'application'), ('timeout', 'insufficient_evidence'),
    ('unknown_authority', 'insufficient_evidence'), (None, 'insufficient_evidence')])
def test_owned_trace_calibration_does_not_turn_infrastructure_into_app_repair(fault, owner):
    receipt = sample()
    receipt['ok'] = fault is None
    if fault == 'authority': receipt['report']['samples'][0]['foreign_api_origins'] = ['http://localhost:8777']
    if fault == 'verifier': receipt['verifier']['unchanged_during_observation'] = False
    if fault == 'source': receipt['source']['unchanged_during_observation'] = False
    if fault == 'candidate': receipt['application_journey'] = {'ok': False, 'repairable': True}
    if fault == 'timeout': receipt['application_journey'] = {'ok': False, 'repairable': False, 'timed_out': True}
    if fault == 'unknown_authority': receipt['report']['samples'][0].pop('foreign_api_origins')
    result = analyze_browser_receipt(receipt, evidence_ref='artifact:receipt')
    assert result['classification'] == owner
    assert result['source_rewrite_authorized'] is False
    if fault is None:
        assert not result['violations']
    if fault == 'authority':
        assert result['critical_violation']['constraint_id'] == 'browser.runtime_authority.sample_0'


def test_another_layout_passing_is_not_recovery_of_the_failed_layout():
    receipt = sample(); receipt['ok'] = False
    other = copy.deepcopy(receipt['report']['samples'][0]); other['layout'] = 'compact'
    receipt['report']['samples'][0]['foreign_api_origins'] = ['http://localhost:8777']
    receipt['report']['samples'].append(other)
    result = analyze_browser_receipt(receipt, evidence_ref='artifact:receipt')
    assert result['classification'] == 'environment'
    assert result['critical_violation']['recovered'] is False


@pytest.mark.parametrize('missing', ['verifier', 'state', 'environment', 'task','outcome','empty_snapshot','missing_verifier_input'])
def test_legacy_unknown_dependencies_require_actual_enrichment_not_todays_verifier(missing):
    receipt = sample()
    if missing == 'verifier': receipt.pop('verifier')
    if missing == 'state': receipt['report']['samples'][0].pop('state_snapshots')
    if missing == 'environment': receipt['report']['samples'][0].pop('runtime_identity')
    if missing == 'task': receipt.pop('task_id')
    if missing == 'outcome': receipt.pop('ok')
    if missing == 'empty_snapshot': receipt['report']['samples'][0]['state_snapshots'][0]['snapshot']=None
    if missing == 'missing_verifier_input': receipt['verifier']['before']['runner']=None
    with pytest.raises(ValueError, match='Retained'):
        analyze_browser_receipt(receipt, evidence_ref='artifact:receipt')
