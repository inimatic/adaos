"""Accounting helpers for the context-composition experiment (not invoices)."""

import copy
import hashlib
import json

RATES = {
    "gpt-5": {"input": 1.25, "cached_input": .125, "cache_write": 1.25, "output": 10},
    "gpt-6-astra": {"input": 10, "cached_input": 1, "cache_write": 12.5, "output": 50},
    "gpt-5.5": {"input": 5, "cached_input": .5, "cache_write": None, "output": 30},
    "gpt-5.6-sol": {"input": 4, "cached_input": .4, "cache_write": 5, "output": 20},
}


def usage_cost_components(value, rate):
    """Reprice observed tokens, not counterfactual model behavior or invoices."""
    cached, written = value["cached_input_tokens"], value["cache_write_tokens"]
    if written and rate["cache_write"] is None:
        raise ValueError("No published cache-write tariff for this model")
    return {
        "uncached_input_usd": (value["input_tokens"] - cached - written) * rate["input"] / 1e6,
        "cached_input_usd": cached * rate["cached_input"] / 1e6,
        "cache_write_usd": written * (rate["cache_write"] or 0) / 1e6,
        "reasoning_usd": value["reasoning_tokens"] * rate["output"] / 1e6,
        "output_excluding_reasoning_usd": value["output_excluding_reasoning_tokens"] * rate["output"] / 1e6,
    }


def execution_seconds(timing):
    """Unknown duration is not zero, including historical terminal receipts."""
    value = timing.get("execution_ms")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Execution duration must be a nonnegative number")
    return value / 1000


def sum_execution_seconds(timings):
    values = [execution_seconds(timing) for timing in timings]
    return None if any(value is None for value in values) else sum(values)


def qualification_outcome(qualified_row, *, structural_ok):
    """A failed transport is unevaluated functionality, not a model failure.

    Classify only explicit connection errors before any functional probes.
    Assertion failures and unknown errors remain failures; do not turn an
    application defect into an infrastructure exemption.
    """
    infrastructure_error = bool(qualified_row and not qualified_row.get('probes')
        and qualified_row.get('error') in {'ConnectionError', 'ConnectTimeout'})
    browser = qualified_row.get('ok') if qualified_row and not infrastructure_error else None
    functional = browser if structural_ok else False
    return {'browser_pass': browser, 'functional_e2e_pass': functional,
        'qualification_pending': not qualified_row or infrastructure_error,
        'qualification_infrastructure_error': infrastructure_error}


def paired_fixture_message_parity(left, right, *, left_scenario, right_scenario):
    """Permit only the native owned fixture ID, never mask task differences.

    Planned inputs can be byte-identical while actual native submissions have
    different isolated scenario IDs. Report both, without rewriting receipts.
    """
    if not all(isinstance(value, str) and value.startswith('test_') for value in (left_scenario, right_scenario)):
        raise ValueError('Only explicitly owned test fixture namespaces may be normalized')

    def normalized(messages, scenario):
        result = copy.deepcopy(messages)
        changes = 0
        for message in result:
            try:
                body = json.loads(message['content'])
            except (ValueError, TypeError):
                continue
            request = body.get('builder_request') if isinstance(body, dict) else None
            if isinstance(request, dict) and request.get('scenario_id') == scenario:
                request['scenario_id'] = 'test_PAIRED_FIXTURE'
                changes += 1
            message['content'] = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return result, changes

    first, first_changes = normalized(left, left_scenario)
    second, second_changes = normalized(right, right_scenario)
    encoded = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'raw_messages_identical': left == right,
        'identical_except_owned_scenario_id': first == second and first_changes == second_changes == 1,
        'normalized_fields_per_side': [first_changes, second_changes],
        'normalized_sha256': [hashlib.sha256(encoded(value)).hexdigest() for value in (first, second)],
        'normalization_scope': 'Only builder_request.scenario_id; all guidance, Brief, schema, examples and task values remain compared.'}


def retrieval_behavior(trace, inline_fragments):
    """Describe retrieval, never infer confusion or causality from call counts."""
    inline, seen = set(inline_fragments), set()
    searches = reads = occurrences = inline_reads = repeats = 0
    indexed = set()
    for call in trace.get("calls", []):
        if call.get("name") == "builder_search_prototype_guidance":
            searches += 1
            continue
        if call.get("name") != "builder_read_prototype_guidance":
            continue
        reads += 1
        ids = call.get("fragment_ids") or [call.get("fragment_id")]
        for identity in filter(None, ids):
            occurrences += 1
            inline_reads += identity in inline
            repeats += identity in seen
            if identity not in inline:
                indexed.add(identity)
            seen.add(identity)
    return {"search_calls": searches, "read_calls": reads, "fragment_occurrences": occurrences,
        "already_inline_fragment_occurrences": inline_reads, "repeated_fragment_occurrences": repeats,
        "unique_read_fragments": sorted(seen), "unique_noninline_fragments": sorted(indexed),
        "scope": "Primary response only. Re-reading inline rules may resolve a perceived conflict; NOT evidence of confusion or waste by itself."}


def reconcile_probe_rechecks(primary_probes, batches):
    """Separate post-hoc verdict; latest complete batch wins, not any past pass.

    The caller validates owned diagnostic artifacts and source amendments.
    Missing artifacts cannot turn an original failure into a success. A batch
    containing repetitions requires every repetition to pass.
    """
    result = dict(primary_probes)
    for batch in batches:
        if not batch.get('complete'):
            continue
        for probe in {check['probe'] for check in batch['checks']}:
            if probe not in primary_probes:
                raise ValueError('Recheck is outside original probe scope')
            checks = [check for check in batch['checks'] if check['probe'] == probe]
            if any(check.get('valid_diagnostic') is not True for check in checks):
                result[probe] = primary_probes[probe]
            else:
                result[probe] = 0 if all(check['exit_code'] == 0 for check in checks) else 1
    return result


def usage_record(envelope, model):
    raw = (envelope.get("response") or {}).get("usage")
    if not isinstance(raw, dict):
        return None
    value = {key: raw.get(key, 0) for key in ("input_tokens", "output_tokens", "total_tokens")}
    details = raw.get("input_tokens_details") or {}
    value["cached_input_tokens"] = details.get("cached_tokens", 0)
    value["cache_write_tokens"] = details.get("cache_write_tokens", 0)
    value["reasoning_tokens"] = (raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
    value["output_excluding_reasoning_tokens"] = value["output_tokens"] - value["reasoning_tokens"]
    if value["total_tokens"] != value["input_tokens"] + value["output_tokens"]:
        raise ValueError("Provider token totals are inconsistent")
    cached, written = value["cached_input_tokens"], value["cache_write_tokens"]
    if not 0 <= cached + written <= value["input_tokens"] or cached < 0 or written < 0:
        raise ValueError("Cache tokens must be disjoint input subsets")
    if not 0 <= value["reasoning_tokens"] <= value["output_tokens"]:
        raise ValueError("Reasoning tokens must be an output subset")
    rate = RATES[model]
    if model in {"gpt-5.5", "gpt-5.6-sol"} and value["input_tokens"] > 272000:
        # Aggregate usage after MCP cannot identify the largest individual
        # prompt. Never silently apply a short tariff to an unproven band.
        raise ValueError("Short context cannot be certified from aggregate input usage")
    # Cache writes replace normal input billing; they are NOT an additive fee.
    value["usd_standard_estimate_without_unreported_cache_writes"] = round(sum(usage_cost_components(value, rate).values()), 6)
    # Counterfactual only: identical observed tokens, no cache reads/writes.
    value["usd_uncached_same_tokens_counterfactual"] = round((
        value["input_tokens"] * rate["input"] + value["output_tokens"] * rate["output"]
    ) / 1e6, 6)
    value["provider_input_details"] = details
    return value
