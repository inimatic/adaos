"""Pure execution guards for applications using asynchronous model jobs.

These guards validate owner-supplied evidence. They do not grant approval,
submit a job, release money, or replace the application's durable transaction.
Keep original/input/plan/mask versions in ``inputs``; domain policy stays local.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return 'sha256:' + hashlib.sha256(raw.encode('utf-8')).hexdigest()


def bind_inputs(*, owner_ref: str, inputs: Mapping) -> dict:
    """Capture all declared inputs, without retaining a mutable caller alias."""
    if not isinstance(owner_ref, str) or not owner_ref.strip() or not isinstance(inputs, Mapping) or not inputs:
        raise ValueError('A scoped owner and nonempty versioned inputs are required')
    # JSON round-trip rejects non-JSON values and freezes nested caller values.
    snapshot = json.loads(json.dumps(dict(inputs), ensure_ascii=False, allow_nan=False))
    body = {'schema': 'adaos.llm.execution_inputs.v1', 'owner_ref': owner_ref, 'inputs': snapshot}
    return {**body, 'digest': _digest(body)}


def _checked(binding):
    if not isinstance(binding, Mapping):
        raise ValueError('An immutable input binding is required')
    actual = bind_inputs(owner_ref=binding.get('owner_ref'), inputs=binding.get('inputs'))
    if dict(binding) != actual:
        raise ValueError('Input binding is corrupt or has an unsupported schema')
    return actual


def require_current_approval(*, approved_inputs, current_inputs,
                             approved_plan_digest: str, current_plan_digest: str) -> None:
    """Check a persisted approval against exact inputs and plan before submit.

    The application must separately establish who approved and retain that
    authorization. A matching digest alone is not an authorization receipt.
    """
    approved, current = _checked(approved_inputs), _checked(current_inputs)
    if (not isinstance(approved_plan_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', approved_plan_digest)
            or approved_plan_digest != current_plan_digest
            or approved != current):
        raise ValueError('Approval is stale for these inputs or this plan')


def result_currentness(*, result_inputs, current_inputs) -> dict:
    """Historical results remain valid artifacts when current inputs change."""
    result, current = _checked(result_inputs), _checked(current_inputs)
    if result['owner_ref'] != current['owner_ref']:
        raise ValueError('Cannot compare results from a foreign execution owner')
    return {'schema': 'adaos.llm.result_currentness.v1', 'current': result == current,
            'result_input_digest': result['digest'], 'current_input_digest': current['digest'],
            'historical_result_retained': True}


def _jobs(snapshot):
    if (not isinstance(snapshot, Mapping) or not snapshot.get('owner_ref')
            or not isinstance(snapshot.get('jobs'), Sequence) or isinstance(snapshot['jobs'], (str, bytes))):
        raise ValueError('A complete owner-scoped job snapshot is required')
    jobs = {}
    for row in snapshot['jobs']:
        if (not isinstance(row, Mapping) or not row.get('request_id') or not row.get('job_id')
                or not row.get('input_digest') or type(row.get('submits')) is not int or row['submits'] < 1
                or row['request_id'] in jobs):
            raise ValueError('Each job needs unique request identity, job identity, input digest and observed submit count')
        jobs[row['request_id']] = {key: row[key] for key in ('job_id', 'input_digest', 'submits')}
    return jobs


def qualify_job_transition(*, before, after, operation: str, request_id: str | None = None) -> dict:
    """Qualify read or replay using complete durable owner observations.

    Poll/status fields may advance. Job identity, exact input and submission
    counts must stay fixed. The scope is this observed transition only; callers
    must capture the full owner ledger rather than a convenient subset.
    """
    if operation not in {'read', 'replay'}:
        raise ValueError('Only declared read and replay transitions are supported')
    previous, current = _jobs(before), _jobs(after)
    if before['owner_ref'] != after['owner_ref']:
        raise ValueError('Job snapshots belong to different owners')
    if operation == 'replay' and (not request_id or request_id not in previous):
        raise ValueError('Replay requires the original durable request identity')
    violations = []
    if previous.keys() != current.keys():
        violations.append('job_set_changed')
    for key in previous.keys() & current.keys():
        for field in ('job_id', 'input_digest', 'submits'):
            if previous[key][field] != current[key][field]:
                violations.append(f'{field}_changed:{key}')
    return {'schema': 'adaos.llm.execution_transition.v1', 'operation': operation,
            'owner_ref': before['owner_ref'], 'request_id': request_id,
            'passed': not violations, 'violations': sorted(violations),
            'before_digest': _digest(before), 'after_digest': _digest(after),
            'scope': 'complete owner-supplied observations; no submit or authorization'}


__all__ = ['bind_inputs', 'require_current_approval', 'result_currentness', 'qualify_job_transition']
