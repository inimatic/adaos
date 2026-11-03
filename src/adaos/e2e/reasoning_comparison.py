"""Model-free eligibility gate for a reasoning-only wire comparison.

Never submits or authorizes inference. In particular, resealing a GenerationPlan
and changing its model-visible ID is NOT silently normalized away as bookkeeping.
"""
from __future__ import annotations

import copy
import hashlib
import json


PROVENANCE = {'observed_provider_body', 'reconstructed_offline_projection'}
COHORTS = {'historical_input', 'current_baseline'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON keys cannot define a matched input')
        result[key] = value
    return result


def _load(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 1024 * 1024:
        raise ValueError('A bounded exact serialized provider body is required')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not isinstance(value, dict) or not isinstance(value.get('reasoning'), dict):
        raise ValueError('Both bodies need an explicit reasoning configuration')
    if not isinstance(value['reasoning'].get('effort'), str):
        raise ValueError('Both bodies need an explicit reasoning effort')
    return value


def _wire(value):
    # The production Personal serializer uses these exact JSON settings. Unknown
    # historical formatting is not silently relabelled exact-byte preservation.
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def compare_reasoning_inputs(before, after, *, cohort, provenance):
    if cohort not in COHORTS or provenance not in PROVENANCE:
        raise ValueError('Declare the separate cohort and actual body provenance')
    left, right = _load(before), _load(after)
    replacement = copy.deepcopy(left)
    replacement['reasoning']['effort'] = right['reasoning']['effort']
    different = left['reasoning']['effort'] != right['reasoning']['effort']
    structural = replacement == right
    serializer_known = _wire(left) == before and _wire(right) == after
    exact = serializer_known and _wire(replacement) == after
    eligible = different and structural and exact
    return {'schema': 'adaos.evidence.reasoning_input_comparison.v1',
        'cohort': cohort, 'body_provenance': provenance,
        'before_sha256': hashlib.sha256(before).hexdigest(),
        'after_sha256': hashlib.sha256(after).hexdigest(),
        'effort_before': left['reasoning']['effort'], 'effort_after': right['reasoning']['effort'],
        'distinct_treatment': different, 'only_effort_structurally_changed': structural,
        'production_serializer_format': serializer_known, 'only_effort_wire_bytes_changed': exact,
        'reasoning_only_pair_eligible': eligible,
        'historical_wire_observed': cohort == 'historical_input' and provenance == 'observed_provider_body',
        'model_calls': 0, 'inference_submission_authorized': False,
        'account_identity_and_supported_effort_require_separate_runtime_admission': True,
        'failure_policy': 'Keep a confounded or reconstructed pair labelled as such; never erase plan IDs, schema changes, repairs or failed attempts.'}
