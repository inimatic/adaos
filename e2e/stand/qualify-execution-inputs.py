"""Qualify shared input guards against a retained, approved application record.

Reads SQLite in read-only mode; the changed-input probes never reach a model or
application mutation. This does not certify that the application uses the SDK.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

from adaos.sdk.llm.execution import bind_inputs, require_current_approval, result_currentness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--record', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((Path.cwd() / 'e2e/artifacts').resolve()):
        parser.error('Qualification output belongs in e2e/artifacts')
    if output.exists():
        parser.error('Use a fresh immutable qualification output')
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT revision,body FROM records WHERE kind='runs' AND id=?", (args.record,)).fetchone()
    if not row:
        raise ValueError('Run is missing')
    revision, body = row
    run = json.loads(body)
    if (run.get('approved') is not True or run.get('status') != 'completed'
            or run.get('approved_plan_revision') != run.get('plan_revision')):
        raise ValueError('An explicitly approved, completed run with an exact plan revision is required')
    owner = 'application-run:' + args.record
    original = bind_inputs(owner_ref=owner, inputs=run['snapshot'])
    plan = 'sha256:' + hashlib.sha256(json.dumps(run['plan'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cases = []
    require_current_approval(approved_inputs=original, current_inputs=original,
                            approved_plan_digest=plan, current_plan_digest=plan)
    cases.append({'id': 'unchanged-approved-inputs', 'passed': True})
    for axis in ('mask', 'settings', 'operations', 'processing', 'photo_sha256'):
        if axis not in run['snapshot']:
            raise ValueError('Required declared input axis missing: ' + axis)
        changed = copy.deepcopy(run['snapshot'])
        # Explicit mutation probe, never persisted and never submitted.
        changed[axis] = {'mutation_probe': True, 'previous': changed[axis]}
        current = bind_inputs(owner_ref=owner, inputs=changed)
        rejected = False
        try:
            require_current_approval(approved_inputs=original, current_inputs=current,
                                    approved_plan_digest=plan, current_plan_digest=plan)
        except ValueError:
            rejected = True
        freshness = result_currentness(result_inputs=original, current_inputs=current)
        cases.append({'id': 'changed-' + axis, 'passed': rejected and not freshness['current']
                      and freshness['historical_result_retained']})
    with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        after = db.execute("SELECT revision,body FROM records WHERE kind='runs' AND id=?", (args.record,)).fetchone()
    unchanged = tuple(after) == row
    receipt = {'schema': 'adaos.eval.execution_input_guards.v1',
        'scope': 'SDK controls on retained real run inputs; application adoption and online approval excluded',
        'record_id': args.record, 'record_revision': revision,
        'record_body_digest': 'sha256:' + hashlib.sha256(body.encode()).hexdigest(),
        'input_binding_digest': original['digest'], 'cases': cases,
        'source_unchanged': unchanged, 'new_provider_submissions': 0,
        'job_read_replay_qualification': 'not_observed; durable provider submit counters unavailable in this record',
        'passed': unchanged and all(case['passed'] for case in cases)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': receipt['passed'], 'cases': len(cases), 'provider_submissions': 0}))
    if not receipt['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
