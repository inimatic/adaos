"""Truthful accounting of retained evolution outcomes; never rewrites trials."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import statistics

from .prototype_evolution_trial import confirmed_turn


def summarize(bundle):
    bundle=Path(bundle)
    rows=[];seen=set();totals=Counter();efforts={}
    for path in bundle.glob('turns/*/result.json'):
        turn=json.loads(path.read_text(encoding='utf-8'))
        attempts=json.loads((path.parent/'provider-attempts.json').read_text(encoding='utf-8'))['attempts']
        tokens=Counter();calls=0;stages=Counter()
        for attempt in attempts:
            if attempt['id'] in seen:raise ValueError('Duplicate provider attempt across outcome cells')
            seen.add(attempt['id'])
            wire=attempt['telemetry'].get('wire',{})
            if wire.get('reasoning_effort')!=turn['effort']:raise ValueError('Actual wire effort disagrees with the declared arm')
            count=attempt['telemetry'].get('provider_calls',0);calls+=count
            stages['interpretation' if attempt['request_id'].startswith('builder-interpretation-') else 'generation']+=count
            usage=attempt['usage']
            tokens['input']+=usage.get('input_tokens',0)
            tokens['output']+=usage.get('output_tokens',0)
            tokens['cached_input']+=usage.get('input_tokens_details',{}).get('cached_tokens',0)
            tokens['reasoning_output_subset']+=usage.get('output_tokens_details',{}).get('reasoning_tokens',0)
        if calls!=turn['provider_calls']:raise ValueError('Outcome call count disagrees with exact attempt receipts')
        tokens['uncached_input']=tokens['input']-tokens['cached_input'];tokens['total']=tokens['input']+tokens['output']
        qualified=confirmed_turn(path)
        row={'turn':path.parent.name,'level':turn['level'],'effort':turn['effort'],
            'phase':turn['phase'],'qualified':bool(qualified),'original_functional_success':turn['functional_success'],
            'oracle_correction':bool(qualified and not turn['functional_success']),
            'calls':calls,'stages':dict(stages),'tokens':dict(tokens),
            'base_revision':turn['base_revision'],'actual_revision':turn['actual_revision'],
            'submission_to_terminal_ms':turn['generation_ms'],'whole_observation_ms':turn['whole_outcome_ms']}
        oracle=path.parent/'oracle-input.json'
        if oracle.exists():
            document=json.loads(oracle.read_text(encoding='utf-8'))['accepted']['semantic_document']
            row['complexity']={'resources':len(document['resources']),
                'fields':sum(len(r['fields']) for r in document['resources']),
                'fixture_records':sum(len(r['records']) for r in document['resources']),
                'views':len(document['views']),'commands':len(document['commands']),
                'relationships':len(document['relationships']),
                'query_controls':sum(len(v.get('query_controls',[])) for v in document['views']),
                'selection_projections':sum(v.get('selection_filter') is not None for v in document['views'])}
        rows.append(row);totals.update(tokens);totals['calls']+=calls
        arm=efforts.setdefault(turn['effort'],{'turns':0,'qualified':0,'calls':0,'qualified_latency_ms':[]})
        arm['turns']+=1;arm['qualified']+=bool(qualified);arm['calls']+=calls
        if qualified:arm['qualified_latency_ms'].append(turn['generation_ms'])
    for arm in efforts.values():
        times=arm.pop('qualified_latency_ms')
        arm['qualified_latency_median_ms']=statistics.median(times) if times else None
    return {'schema':'adaos.evidence.prototype_evolution_summary.v1','turns':sorted(rows,key=lambda row:(row['level'],row['phase'],row['turn'])),
        'totals':dict(totals),'by_effort':efforts,
        'scope':'Adaptive native end-to-end logical tasks, not a randomized/exact-wire causal effort comparison. Failures require separate stage attribution.',
        'usage_policy':'Cached input and reasoning output are subsets. Subscription usage is not converted to API-price billing.',
        'timing_policy':'UI submission to owned terminal includes Core/inference/publication, not pure model time. Whole observation includes browser QA but excludes preceding setup.'}
