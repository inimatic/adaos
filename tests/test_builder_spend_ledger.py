from concurrent.futures import ThreadPoolExecutor
import pytest

from adaos.services.builder.spend_ledger import BuilderSpendLedger


@pytest.fixture
def ledger(tmp_path):
    result = BuilderSpendLedger(state_dir=tmp_path)
    result.configure('project', limit_usd=10, default_reserve_usd=2, authorization_ref='user:experiment-10')
    return result


def reserve(ledger, request='request', **kwargs):
    return ledger.reserve('project', request_id=request, run_id='run', input_digest='sha256:exact-input', **kwargs)


def test_concurrent_admission_never_oversubscribes_project_limit(ledger):
    def attempt(index):
        try:
            return reserve(ledger, f'request-{index}')
        except ValueError as exc:
            assert str(exc) == 'builder_usd_reservation_limit'
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(attempt, range(12)))
    assert sum(row is not None for row in rows) == 5
    assert ledger.snapshot('project')['available_usd'] == 0


def test_same_request_retries_are_idempotent_but_input_drift_is_rejected(ledger):
    assert reserve(ledger) == reserve(ledger)
    assert len(ledger.snapshot('project')['attempts']) == 1
    with pytest.raises(ValueError, match='identity conflict'):
        ledger.reserve('project', request_id='request', run_id='run', input_digest='different')


def test_unknown_cancel_and_terminal_estimate_keep_the_reserve_across_restart(ledger):
    reserve(ledger)
    ledger.observe('request', receipt_ref='cancel', status='cancelled')
    ledger.observe('request', receipt_ref='estimate', usd=.13, basis='estimate', complete=True, status='cancelled')
    restarted = BuilderSpendLedger(state_dir=ledger.path.parent.parent)
    result = restarted.snapshot('project')
    assert result['committed_usd'] == 2 and result['available_usd'] == 8
    assert result['attempts'][0]['observed_usd'] == .13 and not result['usage_complete']


def test_complete_metered_receipt_releases_only_its_own_reserve(ledger):
    reserve(ledger, 'primary'); reserve(ledger, 'repair')
    receipt = dict(receipt_ref='bill', usd=.3, basis='metered', complete=True, status='succeeded')
    assert ledger.observe('primary', **receipt) == ledger.observe('primary', **receipt)
    result = ledger.snapshot('project')
    assert result['committed_usd'] == 2.3 and not result['usage_complete']
    with pytest.raises(ValueError, match='identity conflict'):
        ledger.observe('repair', **receipt)


def test_late_invoice_supersedes_estimate_without_counting_the_charge_twice(ledger):
    reserve(ledger)
    ledger.observe('request', receipt_ref='estimate', usd=.2, basis='estimate', complete=True)
    ledger.observe('request', receipt_ref='invoice', usd=.25, basis='invoiced', complete=True)
    result = ledger.snapshot('project')
    assert result['committed_usd'] == .25 and result['usage_complete']
    with pytest.raises(ValueError, match='Conflicting cost facts'):
        ledger.observe('request', receipt_ref='drift', usd=.26, basis='invoiced', complete=True)


def test_known_overrun_is_visible_and_blocks_new_attempt(ledger):
    reserve(ledger)
    ledger.observe('request', receipt_ref='bill', usd=11, basis='metered', complete=True)
    result = ledger.snapshot('project')
    assert result['status'] == 'exceeded' and result['available_usd'] == -1
    with pytest.raises(ValueError, match='reservation_limit'):
        reserve(ledger, 'repair')


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), 'bad'])
def test_invalid_money_cannot_enter_admission(ledger, value):
    with pytest.raises(ValueError):
        reserve(ledger, reserve_usd=value)
    assert ledger.snapshot('project')['attempts'] == []


def test_provider_job_ownership_and_receipts_are_joined_once(ledger):
    reserve(ledger)
    ledger.link_job('request', job_id='job', provider_base='https://root.example/')
    job = {'job_id': 'job', 'status': 'succeeded', '_protocol': {'cost': {'usd': .4, 'basis': 'metered', 'complete': True}}}
    ledger.observe_job(job, provider_base='https://root.example')
    ledger.observe_job(job, provider_base='https://root.example')
    assert ledger.snapshot('project')['committed_usd'] == .4
    assert ledger.observe_job(job, provider_base='https://different.example') is None


def test_unconfigured_scope_never_looks_like_zero_usage(ledger):
    assert ledger.reserve('not-authorized', request_id='r', run_id='run', input_digest='input') is None
    assert ledger.snapshot('not-authorized')['status'] == 'not_configured'


def test_late_partial_copy_does_not_revoke_complete_metering(ledger):
    reserve(ledger)
    ledger.observe('request', receipt_ref='complete', usd=.25, basis='metered', complete=True)
    ledger.observe('request', receipt_ref='delayed-partial', usd=.25, basis='metered', complete=False)
    result = ledger.snapshot('project')
    assert result['committed_usd'] == .25 and result['usage_complete']


def test_sdk_blocks_before_transport_and_preserves_an_ambiguous_attempt(ledger, monkeypatch):
    from adaos.sdk.llm import llm_client
    import adaos.services.builder.spend_ledger as owner
    monkeypatch.setattr(owner, 'BuilderSpendLedger', lambda: ledger)
    calls = []
    def submit(*args, **kwargs):
        calls.append(kwargs['request_id'])
        raise TimeoutError('response lost after acceptance')
    monkeypatch.setattr(llm_client, '_submit_root_job', submit)
    for index in range(5):
        with pytest.raises(TimeoutError):
            llm_client.submit_response_job([{'role': 'user', 'content': 'task'}], request_id=f'r{index}',
                spend_scope='project', spend_run_id='run')
    from adaos.sdk.llm.errors import SubmissionNotAdmitted
    with pytest.raises(SubmissionNotAdmitted, match='reservation_limit') as denied:
        llm_client.submit_response_job([{'role': 'user', 'content': 'task'}], request_id='r6',
            spend_scope='project', spend_run_id='run')
    assert len(calls) == 5 and ledger.snapshot('project')['committed_usd'] == 10
    assert denied.value.admission['provider_transport_started'] is False
    assert denied.value.admission['request_id'] == 'r6'


def test_sdk_joins_success_and_repeated_poll_receipts_without_double_counting(ledger, monkeypatch):
    from adaos.sdk.llm import llm_client
    import adaos.services.builder.spend_ledger as owner
    monkeypatch.setattr(owner, 'BuilderSpendLedger', lambda: ledger)
    monkeypatch.setattr(llm_client, '_observe_job_spend', lambda job, base: ledger.observe_job(job, provider_base=base))
    job = {'job_id': 'job', 'status': 'succeeded', '_client': {'base_url': 'https://root.example'},
        '_protocol': {'cost': {'usd': .4, 'basis': 'metered', 'complete': True}}}
    monkeypatch.setattr(llm_client, '_submit_root_job', lambda *args, **kwargs: dict(job))
    result = llm_client.submit_response_job([{'role': 'user', 'content': 'task'}], request_id='request',
        spend_scope='project', spend_run_id='run')
    assert result['_client']['spend']['committed_usd'] == .4
    ledger.observe_job(job, provider_base='https://root.example')
    assert ledger.snapshot('project')['committed_usd'] == .4
