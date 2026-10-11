"""Incremental primary/final compiler replay on owned, completed cohorts.

Earlier immutable cohort receipts may coexist with later cohorts. No model
submission, application mutation, or silent replacement of an effective answer.
"""
import argparse
import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv

from adaos.e2e.builder import _write_json
from adaos.e2e.prototype_context_ablation import acceptance, native_builder, read, source_fingerprints
from adaos.services.builder_context import canonical, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--cohort', required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to(Path.cwd() / 'e2e/artifacts/builder'):
        parser.error('Owned E2E evidence required')
    registration = read(run / 'preregistration.json')
    assert args.cohort in {c['id'] for c in registration['cohorts']}
    actual_sources = source_fingerprints(Path.cwd())
    if actual_sources != registration['runtime_source_fingerprints']:
        amendment = read(run / 'client-disclosure-amendment-01.json')
        assert amendment['before_sources'] == registration['runtime_source_fingerprints']
        assert amendment['after_sources'] == actual_sources
        changed = {key for key in actual_sources if actual_sources[key] != amendment['before_sources'][key]}
        assert changed == {'src/adaos/integrations/adaos-client/src/app/renderer/desktop/desktop.component.ts'}
    directory = run / args.cohort
    results, plan, brief = read(directory / 'results.json'), read(directory / 'plan.json'), read(directory / 'source-brief.json')
    assert results['complete'] and len(results['samples']) == 5
    load_dotenv(); logging.disable(logging.WARNING)
    from adaos.apps.cli.app import Settings, init_ctx
    from adaos.services.agent_context import get_ctx
    from adaos.sdk.developer import projects
    from adaos.sdk.llm import llm_client
    init_ctx(Settings.from_sources())
    builder = native_builder(Path(get_ctx().paths.dev_skills_dir()) / 'builder_skill/handlers/main.py')
    labels = ('current-primary', 'current-final')
    with patch.object(llm_client, 'submit_response_job', side_effect=AssertionError('Offline replay cannot submit a model')):
        for label in labels:
            rows = []
            for row in results['samples']:
                sample = f"{row['order']:02d}-{row['arm']}"
                source = directory / sample
                session = read(source / 'session.json')
                text = read(source / 'call-01-terminal.json').get('output_text') or ''
                repair = source / 'planned-repair-result.json'
                repaired = label == 'current-final' and repair.exists()
                if repaired:
                    final = read(repair)
                    text = final.get('raw_response') or final.get('last_response') or ''
                    root = projects.resolve_root('scenario', row['scenario_id']).resolve()
                    for artifact in reversed(final.get('candidate_artifacts') or []):
                        if artifact.get('stage') == 'semantic-repair-merged':
                            artifact_path = (root / artifact['path']).resolve()
                            assert artifact_path.is_relative_to(root)
                            merged = read(artifact_path)['structured_candidate']
                            if merged:
                                text = canonical(merged)
                                break
                record = {'cohort': args.cohort, 'sample': sample, 'includes_repair': repaired,
                    'effective_candidate_sha256': digest(text), 'new_llm_calls': 0}
                try:
                    candidate = builder._parse_llm_webui_transform_output(output_text=text,
                        previous_preview=session['initial_webui'].get('preview_state') or {},
                        before_webui=session['initial_webui'], prototype_brief=brief,
                        project_ref=session['project_ref'], output_mode='semantic_v2')
                    record.update(generation_ok=candidate['ok'], structural_ok=acceptance(candidate, brief, task=plan['task'])['ok'])
                except Exception as error:
                    candidate = {'ok': False, 'error': 'offline_replay_failed', 'detail': str(error), 'findings': getattr(error, 'findings', [])}
                    record.update(generation_ok=False, structural_ok=False, error=str(error))
                target = run / ('compiler-replay-' + label) / args.cohort / sample
                target.mkdir(parents=True, exist_ok=True)
                output = target / 'generation-result.json'
                if output.exists():
                    assert (target / 'effective-candidate.txt').read_text(encoding='utf-8') == text, 'Cannot overwrite an earlier answer'
                    earlier = read(output)
                    assert earlier.get('ok') == candidate.get('ok') and earlier.get('semantic_document') == candidate.get('semantic_document')
                else:
                    _write_json(output, candidate)
                    (target / 'effective-candidate.txt').write_text(text, encoding='utf-8')
                rows.append(record)
            _write_json(run / ('compiler-replay-' + label) / args.cohort / 'cohort-report.json',
                {'samples': rows, 'complete': True, 'adapter_sha256': 'sha256:' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
            aggregate = []
            for cohort in registration['cohorts']:
                path = run / ('compiler-replay-' + label) / cohort['id'] / 'cohort-report.json'
                if path.exists():
                    retained = read(path)
                    assert retained['complete']
                    aggregate.extend(retained['samples'])
            _write_json(run / ('compiler-replay-' + label) / 'report.json',
                {'samples': aggregate, 'complete': len(aggregate) == 20, 'new_llm_calls': 0,
                    'scope': 'Current frozen compiler, incremental replay; no product correction between primary generations.'})
    print(json.dumps({'cohort': args.cohort, 'primary_compile_pass': sum(r['generation_ok'] for r in
        read(run / 'compiler-replay-current-primary' / args.cohort / 'cohort-report.json')['samples']),
        'final_compile_pass': sum(r['generation_ok'] for r in rows)}), flush=True)


if __name__ == '__main__':
    main()
