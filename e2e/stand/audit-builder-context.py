"""Describe retained model inputs independently of generated UI or grader scores."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    rows = []
    for path in sorted((args.run / 'evidence' / 'model-io').rglob('*.request.json')):
        request = json.loads(path.read_text(encoding='utf-8'))
        if request.get('schema') != 'adaos.builder.llm_job_input.v1':
            continue
        stable, dynamic, repair = {}, {}, {}
        for message in request.get('messages', []):
            try:
                content = json.loads(message['content'])
            except (TypeError, ValueError):
                continue
            if isinstance(content, dict):
                stable.update(content.get('stable_builder_context') or {})
                dynamic.update(content.get('builder_request') or {})
                repair.update(content.get('semantic_repair') or {})
        options = request.get('generation', {}).get('options', {})
        rows.append({
            'request': str(path.relative_to(args.run)), 'job_id': request.get('job_id'),
            'model': request.get('generation', {}).get('model'),
            'effort': (options.get('reasoning') or {}).get('effort'), 'max_tokens': options.get('max_tokens'),
            'stable_keys': sorted(stable),
            'stable_digest': hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
            'dynamic_keys': sorted(dynamic),
            'repair_keys': sorted(repair),
            'context_kind': 'repair' if repair else 'primary',
            'instruction_chars': len(dynamic.get('instruction') or ''),
            'message_chars': [len(message['content']) for message in request.get('messages', [])],
            'output_locales': dynamic.get('output_locales'),
            'unexpected_dynamic_keys': sorted(set(dynamic) - {'instruction', 'output_locales', 'prototype_brief', 'scenario_id', 'title'}),
            'unexpected_repair_keys': sorted(set(repair) - {'task', 'instruction', 'prototype_brief', 'output_locales', 'validation_findings', 'candidate', 'repair_scope', 'invariant_checklist'}),
        })
    if not rows:
        parser.error('No retained model requests found')
    output = args.run / 'context-audit.json'
    if output.exists():
        number = 1
        while (args.run / f'context-audit-{number:02}.json').exists():
            number += 1
        output = args.run / f'context-audit-{number:02}.json'
    with output.open('x', encoding='utf-8') as target:
        json.dump({'scope': 'Input inventory, not proof of semantic absence of leakage; inspect stable content and code too',
                   'requests': rows}, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps({'requests': len(rows), 'stable_variants': len({row['stable_digest'] for row in rows}),
                      'unexpected_dynamic_keys': [row for row in rows if row['unexpected_dynamic_keys']],
                      'unexpected_repair_keys': [row for row in rows if row['unexpected_repair_keys']],
                      'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
