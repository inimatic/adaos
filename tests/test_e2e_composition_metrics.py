import pytest

from adaos.e2e.composition_metrics import execution_seconds, qualification_outcome, retrieval_behavior, sum_execution_seconds, usage_record


def envelope(*, cached=0, written=0):
    return {"response": {"usage": {"input_tokens": 1000, "output_tokens": 200,
        "total_tokens": 1200, "input_tokens_details": {"cached_tokens": cached,
        "cache_write_tokens": written}, "output_tokens_details": {"reasoning_tokens": 150}}}}


def test_cached_and_reasoning_tokens_are_subsets_not_additional_usage():
    result = usage_record(envelope(cached=400), "gpt-5")
    assert result["total_tokens"] == 1200
    assert result["output_excluding_reasoning_tokens"] == 50
    assert result["usd_standard_estimate_without_unreported_cache_writes"] == .0028


def test_cache_write_rate_replaces_regular_input_rate():
    result = usage_record(envelope(cached=200, written=600), "gpt-6-astra")
    assert result["usd_standard_estimate_without_unreported_cache_writes"] == .0197
    assert result["usd_uncached_same_tokens_counterfactual"] == .02


def test_unknown_usage_is_unknown():
    assert usage_record({"response": {}}, "gpt-5") is None


@pytest.mark.parametrize('error', ['ConnectionError', 'ConnectTimeout'])
def test_unavailable_hub_does_not_count_as_a_model_functionality_failure(error):
    outcome = qualification_outcome({'ok': False, 'error': error}, structural_ok=True)
    assert outcome['functional_e2e_pass'] is None
    assert outcome['browser_pass'] is None
    assert outcome['qualification_pending'] is True
    assert outcome['qualification_infrastructure_error'] is True


def test_pending_qualification_preserves_an_unknown_browser_verdict():
    assert qualification_outcome({}, structural_ok=True)['functional_e2e_pass'] is None
    assert qualification_outcome({}, structural_ok=False)['functional_e2e_pass'] is False


@pytest.mark.parametrize('result', [{'ok': False, 'error': 'AssertionError'},
    {'ok': False, 'error': 'ConnectionError', 'probes': {'interactions': 1}}, {'ok': False, 'error': 'UnknownError'}])
def test_infrastructure_exemption_does_not_hide_application_or_unknown_errors(result):
    outcome = qualification_outcome(result, structural_ok=True)
    assert outcome['functional_e2e_pass'] is False
    assert outcome['qualification_infrastructure_error'] is False


def test_successful_browser_qualification_still_requires_structural_acceptance():
    result = {'ok': True, 'probes': {'interactions': 0}}
    assert qualification_outcome(result, structural_ok=True)['functional_e2e_pass'] is True
    assert qualification_outcome(result, structural_ok=False)['functional_e2e_pass'] is False


def test_actual_input_parity_permits_only_an_owned_native_scenario_identity():
    import copy
    import json
    from adaos.e2e.composition_metrics import paired_fixture_message_parity
    left = [{'role': 'system', 'content': 'Keep all rules'}, {'role': 'user', 'content': json.dumps(
        {'builder_request': {'scenario_id': 'test_left', 'instruction': 'Compare suppliers'}})}]
    right = copy.deepcopy(left)
    right[1]['content'] = right[1]['content'].replace('test_left', 'test_right')
    outcome = paired_fixture_message_parity(left, right, left_scenario='test_left', right_scenario='test_right')
    assert outcome['raw_messages_identical'] is False
    assert outcome['identical_except_owned_scenario_id'] is True
    assert outcome['normalized_fields_per_side'] == [1, 1]
    assert outcome['normalized_sha256'][0] == outcome['normalized_sha256'][1]
    assert 'test_left' in left[1]['content']
    for altered in (right[1]['content'].replace('Compare suppliers', 'Ignore suppliers'),
                    right[1]['content'].replace('test_right', 'test_wrong')):
        changed = [right[0], {**right[1], 'content': altered}]
        assert paired_fixture_message_parity(left, changed, left_scenario='test_left',
            right_scenario='test_right')['identical_except_owned_scenario_id'] is False
    with pytest.raises(ValueError, match='owned test'):
        paired_fixture_message_parity(left, right, left_scenario='real_application', right_scenario='test_right')


@pytest.mark.parametrize("model,expected", [("gpt-5.5", .0092), ("gpt-5.6-sol", .00656)])
def test_new_models_use_published_short_context_rates(model, expected):
    assert usage_record(envelope(cached=400), model)["usd_standard_estimate_without_unreported_cache_writes"] == expected


def test_sol_cache_write_pricing_and_reasoning_decomposition():
    from adaos.e2e.composition_metrics import RATES, usage_cost_components
    result = usage_record(envelope(cached=200, written=600), "gpt-5.6-sol")
    assert result["usd_standard_estimate_without_unreported_cache_writes"] == .00788
    parts = usage_cost_components(result, RATES["gpt-5.6-sol"])
    assert parts["reasoning_usd"] == .003
    assert round(sum(parts.values()), 6) == .00788


def test_unpublished_cache_write_tariff_is_not_invented():
    with pytest.raises(ValueError, match="published cache-write"):
        usage_record(envelope(written=100), "gpt-5.5")


def test_new_short_context_band_requires_evidence():
    raw = envelope()
    raw["response"]["usage"].update(input_tokens=272001, total_tokens=272201)
    with pytest.raises(ValueError, match="Short context"):
        usage_record(raw, "gpt-5.6-sol")


def test_unknown_timing_does_not_turn_into_a_zero_duration():
    assert execution_seconds({}) is None
    assert sum_execution_seconds([{"execution_ms": 2000}, {}]) is None
    assert sum_execution_seconds([]) == 0
    assert sum_execution_seconds([{"execution_ms": 2000}, {"execution_ms": 0}]) == 2


@pytest.mark.parametrize("value", [-1, True, "100"])
def test_invalid_execution_duration_is_rejected(value):
    with pytest.raises(ValueError, match="duration"):
        execution_seconds({"execution_ms": value})


def test_inconsistent_cache_subsets_are_rejected():
    with pytest.raises(ValueError, match="subsets"):
        usage_record(envelope(cached=500, written=600), "gpt-6-astra")


def test_retrieval_distinguishes_search_reads_inline_and_overlapping_batches():
    value = retrieval_behavior({'calls': [
        {'name': 'builder_search_prototype_guidance'},
        {'name': 'builder_read_prototype_guidance', 'fragment_ids': ['known', 'missing']},
        {'name': 'builder_read_prototype_guidance', 'fragment_id': 'missing'},
    ]}, ['known'])
    assert value['search_calls'] == 1 and value['read_calls'] == 2
    assert value['fragment_occurrences'] == 3
    assert value['already_inline_fragment_occurrences'] == 1
    assert value['repeated_fragment_occurrences'] == 1
    assert value['unique_noninline_fragments'] == ['missing']


def test_posthoc_recheck_does_not_cherry_pick_an_earlier_pass():
    from adaos.e2e.composition_metrics import reconcile_probe_rechecks
    batches = [{'complete': True, 'checks': [{'probe': 'review', 'exit_code': code, 'valid_diagnostic': True}]}
               for code in (0, 1)]
    assert reconcile_probe_rechecks({'review': 1, 'other': 0}, batches) == {'review': 1, 'other': 0}


@pytest.mark.parametrize('complete,valid,second_code', [(False, True, 0), (True, False, 0), (True, True, 1)])
def test_posthoc_recheck_requires_complete_valid_all_passing_repetitions(complete, valid, second_code):
    from adaos.e2e.composition_metrics import reconcile_probe_rechecks
    checks = [{'probe': 'review', 'exit_code': 0, 'valid_diagnostic': True},
              {'probe': 'review', 'exit_code': second_code, 'valid_diagnostic': valid}]
    assert reconcile_probe_rechecks({'review': 1}, [{'complete': complete, 'checks': checks}])['review'] == 1


def test_posthoc_recheck_is_separate_and_preserves_unchecked_probes():
    from adaos.e2e.composition_metrics import reconcile_probe_rechecks
    original = {'review': 1, 'interaction': 0}
    result = reconcile_probe_rechecks(original, [{'complete': True, 'checks': [
        {'probe': 'review', 'exit_code': 0, 'valid_diagnostic': True}]}])
    assert result == {'review': 0, 'interaction': 0}
    assert original['review'] == 1
