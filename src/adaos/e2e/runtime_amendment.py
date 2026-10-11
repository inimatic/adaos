"""Explicit transport/runner-only amendments; original preregistrations retained.

No context/router/schema/compiler/client/probe change is permitted here. The
harness ALSO verifies all assembled planned messages/options remain identical.
"""
from pathlib import Path
import json

TRANSPORT_SOURCES = frozenset({
 'src/adaos/sdk/llm/llm_client.py', 'src/adaos/e2e/request_budget.py',
 'src/adaos/e2e/prototype_context_ablation.py', 'e2e/stand/run-prototype-context-composition.py',
 'e2e/stand/repair-prototype-pipeline-cohort.py', 'e2e/stand/qualify-prototype-context-ablation.py'})

def validated_sources(repo, plan, run):
    from adaos.e2e.prototype_context_ablation import source_fingerprints
    actual=source_fingerprints(repo); expected=plan['runtime_source_fingerprints']
    if actual == expected:return actual
    path=Path(run).parent/'transport-amendment.json'
    if not path.exists():raise ValueError('Frozen implementation drift without explicit transport amendment')
    amendment=json.loads(path.read_text(encoding='utf-8'))
    changed={key for key in actual.keys()|expected.keys() if actual.get(key)!=expected.get(key)}
    if not changed <= TRANSPORT_SOURCES or amendment['before_sources'] != expected or amendment['after_sources'] != actual:
        raise ValueError('Transport amendment does not cover exact source drift')
    return actual
