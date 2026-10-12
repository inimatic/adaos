"""Bounded, source-attributed execution measurements and Builder projections."""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads((Path(__file__).resolve().parents[2] / 'abi/builder.run.v1.schema.json').read_text(encoding='utf-8'))
    return Draft202012Validator(schema['$defs']['execution_metrics'])


def validate_execution_metrics(value):
    if value is None:
        return None
    _validator().validate(value)
    if any(isinstance(item, float) and not math.isfinite(item) for item in value.values()):
        raise ValidationError('execution measurements must be finite')
    if value.get('cost_basis') == 'zero_model' and (value.get('source') != 'core.semantic_graph'
            or value.get('llm_calls') != 0 or value.get('cost_usd') != 0):
        raise ValidationError('zero_model requires explicit zero-call Core evidence')
    return copy.deepcopy(dict(value))


def execution_metrics_from_llm(result: Mapping, *, reasoning_effort=None):
    mapping = lambda value: value if isinstance(value, Mapping) else {}
    telemetry = mapping(result.get('telemetry'))
    usage = mapping(telemetry.get('usage')) or mapping(result.get('usage'))
    provider = mapping(telemetry.get('provider'))
    metrics = {'source': 'builder.llm_result'}
    model = result.get('model') or provider.get('model')
    if model:
        metrics['model'] = str(model)
    if provider.get('model') and provider['model'] != model:
        metrics['resolved_model'] = str(provider['model'])
    if reasoning_effort:
        metrics['reasoning_effort'] = str(reasoning_effort)
    for name in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens'):
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            metrics[name] = value
    repair = mapping(telemetry.get('repair'))
    jobs = list(dict.fromkeys(str(item) for item in (telemetry.get('root_job_id'), repair.get('root_job_id')) if item))
    if jobs:
        metrics['llm_calls'] = len(jobs)
        metrics['repair_attempts'] = int(bool(repair.get('root_job_id')) and repair.get('root_job_id') != telemetry.get('root_job_id'))
        metrics['source_refs'] = [f'llm_job:{item}' for item in jobs]
    timing = mapping(telemetry.get('timing'))
    for target, value in [('root_execution_ms', timing.get('execution_ms')), ('root_queue_ms', timing.get('queue_ms')),
                          ('client_wait_ms', telemetry.get('wait_elapsed_ms'))]:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            metrics[target] = value
    def measured_cost(cost):
        cost = mapping(cost)
        # Cost must have an explicit basis. Missing metering is never $0.
        basis = cost.get('basis') or ('estimate' if 'estimated_usd' in cost else None)
        usd = cost.get('estimated_usd') if basis == 'estimate' else cost.get('usd')
        if basis in {'estimate', 'metered', 'invoiced'} and isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= 0:
            return usd, basis
        return None
    cost = measured_cost(telemetry.get('cost') or result.get('cost'))
    if repair and repair.get('root_job_id') != telemetry.get('root_job_id'):
        repair_cost = measured_cost(repair.get('cost'))
        # A known primary charge is not the total cost of a partially metered repair.
        cost = (cost[0] + repair_cost[0], cost[1]) if cost and repair_cost and cost[1] == repair_cost[1] else None
    if cost:
        metrics.update(cost_usd=cost[0], cost_basis=cost[1])
    return validate_execution_metrics(metrics)


def project_run(value: Mapping, *, request=None, source_message_ids=()):
    row = copy.deepcopy(dict(value))
    activity = str(row.get('activity') or 'workflow')
    identity = str(row.get('run_id') or row.get('id') or '')
    revision = next((str(ref).removeprefix('ui_revision:') for ref in row.get('output_refs') or []
                     if str(ref).startswith('ui_revision:')), None)
    metrics = row.get('execution_metrics') or {}
    no_llm = metrics.get('llm_calls') == 0
    row.update(id=identity, title=' · '.join(filter(None, [activity, f'UI {revision}' if revision else None, identity[-32:]])),
               stage='prototype' if activity.startswith('prototype_') else activity,
               revision=revision, request=request, source_message_ids=list(source_message_ids),
               model_label='Core (no LLM)' if no_llm else (' → '.join(filter(None, [metrics.get('model'), metrics.get('resolved_model')])) or '—'),
               effort_label=metrics.get('reasoning_effort') or '—',
               cost_label=(f"${metrics['cost_usd']:.8g} ({metrics['cost_basis']})" if 'cost_usd' in metrics else '—'),
               root_time_label=(f"{metrics['root_execution_ms']:g} ms" if 'root_execution_ms' in metrics else '—'),
               measurement_label=('Recorded' if {'input_tokens', 'output_tokens', 'llm_calls', 'cost_usd'} <= metrics.keys()
                                  else 'Partial') if metrics else 'Not recorded')
    for name in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'llm_calls', 'repair_attempts', 'root_queue_ms', 'client_wait_ms'):
        row[name + '_label'] = str(metrics[name]) if name in metrics else '—'
    return row
