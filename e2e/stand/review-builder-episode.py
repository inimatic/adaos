"""Read a retained Builder trial without hiding external interventions or cost gaps."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--task-root', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--interventions', type=Path, required=True)
    parser.add_argument('--recovery-spec', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((Path.cwd() / 'e2e/artifacts').resolve()):
        parser.error('Use fresh output under e2e/artifacts')
    spec, receipt, session = read(args.spec), read(args.receipt), read(args.session)
    assignment = read(args.task_root / 'input/assignment.json')
    task_id = assignment['task_id']
    if receipt.get('task_id') != task_id or session.get('current_task_id') != task_id:
        raise ValueError('Episode evidence belongs to different tasks')
    interventions = read(args.interventions)
    rows = interventions.get('interventions')
    if not isinstance(rows, list) or any(not row.get('actor') or not row.get('action') or not row.get('evidence_refs')
            or type(row.get('required_internal')) is not bool for row in rows):
        raise ValueError('Every intervention needs actor, action, evidence and an explicit internal-intervention classification')
    changes = []
    if args.recovery_spec:
        recovery = read(args.recovery_spec)
        if recovery['task_id'] != task_id:
            raise ValueError('Recovery spec belongs to a different task')
        for filename, expected in recovery['source_files'].items():
            if 'sha256:' + hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
                changes.append(filename)
    events = [json.loads(line) for line in (args.task_root / 'output/codex-live.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    turns = [event for event in events if event.get('type') == 'turn.completed']
    usage = {}
    for key in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
        values = [(event.get('usage') or {}).get(key) for event in turns]
        usage[key] = sum(values) if values and all(type(value) is int and value >= 0 for value in values) else None
    if usage['input_tokens'] is not None and usage['cached_input_tokens'] is not None:
        if usage['cached_input_tokens'] > usage['input_tokens']:
            raise ValueError('Cached input exceeds its containing token total')
        usage['fresh_input_tokens'] = usage['input_tokens'] - usage['cached_input_tokens']
    complete_accounting = interventions.get('accounting_complete') is True
    required_internal = [row for row in rows if row['required_internal']]
    result_qualified = (receipt.get('ok') is True and session.get('status') == 'completed' and not changes)
    start = datetime.fromisoformat(assignment['created_at'].replace('Z', '+00:00'))
    end = datetime.fromisoformat((session.get('updated_at') or receipt['created_at']).replace('Z', '+00:00'))
    result = {'schema': 'adaos.eval.builder_episode.v1', 'task_id': task_id, 'trial': spec.get('trial'),
        'started_at': assignment['created_at'], 'observed_until': end.isoformat(),
        'wall_seconds_to_observed_state': max(0, (end-start).total_seconds()),
        'user_result_qualified': result_qualified, 'source_changed_after_freeze': changes,
        'intervention_accounting_complete': complete_accounting, 'interventions': rows,
        'required_internal_interventions': len(required_internal) if complete_accounting else None,
        'intervention_accounting_basis': 'explicit operator evidence record; not a universal automatic intervention detector',
        'unassisted_success': bool(result_qualified and not required_internal) if complete_accounting else None,
        'codex_completed_turns': len(turns), 'codex_usage': usage,
        'codex_provider_calls': None, 'codex_actual_usd': None,
        'cost_note': 'CLI usage is observed. Internal provider-call count and invoice cost are unknown; cached/reasoning subsets are not added again.',
        'retouch_provider_jobs': 'Read the separately pinned project ledger; this report does not infer a charge from a successful UI check.',
        'evidence': {'spec': str(args.spec), 'assignment': str(args.task_root / 'input/assignment.json'),
                     'browser_receipt': str(args.receipt), 'session': str(args.session),
                     'intervention_record': str(args.interventions)},
        'claim_scope': 'One retained supervised episode; no population autonomy rate or SOTA ranking'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('task_id', 'user_result_qualified', 'unassisted_success', 'codex_completed_turns')}))


if __name__ == '__main__':
    main()
