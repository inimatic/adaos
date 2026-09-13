"""Independent declarative DEV-owner tool checks against a pinned TEST Automation."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _expectation_findings, _load_document, _resolve_value, _write_json
from adaos.e2e.builder_lifecycle import _target
from adaos.e2e.stand import redact_value
from adaos.sdk.developer import compositions
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.workspaces import index as workspace_index
from adaos.services.workspaces.relations import WebspaceRelationshipRegistry


def validate_scope(checkpoint, pin, project, snapshot, skill):
    context = checkpoint.get('context', {})
    targets = context.get('owned_artifacts', [])
    scenario = pin.get('scenario_id', '')
    if (not checkpoint.get('cleanup', {}).get('test') or not context.get('retain_test_projects')
            or len(targets) != 1 or targets[0].get('primary_ref') != f'scenario:{scenario}'
            or targets[0].get('project_id') != project.get('id')
            or '[TEST]' not in str(project.get('catalog', {}).get('title', ''))):
        raise ValueError('The checkpoint must own the explicitly marked TEST application')
    if (pin.get('stage') != 'automation' or not str(pin.get('revision', '')).startswith('task.')
            or snapshot.get('object_type') != 'scenario' or snapshot.get('object_id') != scenario
            or snapshot.get('task_id') != pin['revision']):
        raise ValueError('The exact current Automation snapshot is required')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', skill) or not any(
            item.get('ref') == f'skill:{skill}' for item in project.get('components', {}).get('owned', [])):
        raise ValueError('Tool skill must belong to this TEST application')


def validate_plan(plan):
    if plan.get('schema') != 'adaos.e2e.application_tools.v1' or not plan.get('steps'):
        raise ValueError('A nonempty declarative application tool plan is required')
    seen = set()
    for step in plan['steps']:
        key = step.get('id')
        if not key or key in seen or not re.fullmatch(r'[A-Za-z0-9_]+', str(step.get('tool', ''))):
            raise ValueError('Steps need unique IDs and local tool names')
        if step.get('caller', 'owner') not in {'owner', 'anonymous'} or not step.get('expect'):
            raise ValueError('DEV checks require explicit expectations and owner/anonymous ingress')
        if 'concurrent_arguments' in step:
            variants = step['concurrent_arguments']
            if ('arguments' in step or not isinstance(variants, list) or not 2 <= len(variants) <= 4
                    or not all(isinstance(item, dict) for item in variants)):
                raise ValueError('A race requires two to four argument objects and no sequential arguments')
        seen.add(key)


def observe_response(response):
    return {'http_status': response.status_code, 'body': response.json()}


def race_summary(results):
    def successful(item):
        body = item['body']
        result = body.get('result', body)
        return (200 <= item['http_status'] < 300 and body.get('ok') is not False
                and (not isinstance(result, dict) or result.get('ok') is not False))
    return {'results': results, 'http_statuses': sorted(item['http_status'] for item in results),
            'success_count': sum(successful(item) for item in results)}


def concurrent_calls(hub, headers, bodies):
    # Separate sessions avoid sharing mutable connection/cookie state between racers.
    from threading import Barrier
    barrier = Barrier(len(bodies), timeout=10)

    def request(body):
        with requests.Session() as client:
            barrier.wait()
            return observe_response(client.post(hub + '/api/tools/call', headers=headers, json=body, timeout=60))

    with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
        return race_summary(list(executor.map(request, bodies)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('pin', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--skill', required=True)
    args = parser.parse_args()
    load_dotenv()
    root = (Path.cwd() / 'e2e/artifacts/builder').resolve()
    output = args.output.resolve()
    if os.getenv('ENV_TYPE') != 'dev' or not output.is_relative_to(root) or output == root or output.exists():
        parser.error('Requires DEV and new evidence inside e2e/artifacts/builder')
    checkpoint, pin, plan = map(_load_document, (args.checkpoint, args.pin, args.plan))
    validate_plan(plan)
    init_ctx(Settings.from_sources())
    context = copy.deepcopy(checkpoint['context'])
    scenario = pin['scenario_id']
    _target({'object_type': 'scenario', 'object_id': scenario}, context)
    project = compositions.get(context['owned_artifacts'][0]['project_id'])
    snapshot_root = BuilderWorkflowService.from_context().automation_snapshot_root('scenario', scenario)
    snapshot = _load_document(snapshot_root / 'snapshot.json')
    validate_scope(checkpoint, pin, project, snapshot, args.skill)
    webspace = pin['preview_webspace_id']
    WebspaceRelationshipRegistry.from_context().require_preview_target(webspace)
    row = workspace_index.get_workspace(webspace)
    if not row or row.effective_kind != 'dev':
        parser.error('The explicit paired DEV preview must exist')
    marker = 'E2E-HTTP-' + uuid4().hex[:10]
    context.update(outputs={}, case_instance_id=marker, webspace_id=webspace)
    hub = 'http://127.0.0.1:8778'
    headers = {'owner': {'X-AdaOS-Token': resolve_control_token(base_url=hub)}, 'anonymous': {}}
    report = {'scope': 'Independent DEV-owner HTTP behavior, not delegated reader/writer acceptance',
              'scenario': scenario, 'task': pin['revision'], 'skill': args.skill, 'webspace': webspace,
              'marker': marker, 'plan': plan, 'steps': [], 'ok': False,
              'records': 'Only new marked test data; retained for browser inspection'}
    with requests.Session() as client:
        try:
            for step in plan['steps']:
                step_id = step['id']
                bodies = []
                for raw in step.get('concurrent_arguments', [step.get('arguments', {})]):
                    arguments = _resolve_value(raw, context)
                    if arguments.get('webspace_id', webspace) != webspace:
                        raise ValueError('An operation cannot override the pinned webspace')
                    arguments['webspace_id'] = webspace
                    bodies.append({'tool': f'{args.skill}:{step["tool"]}', 'arguments': arguments,
                                   'dev': True, 'context': {'webspace_id': webspace, 'current_scenario_id': scenario}})
                report['active_step'] = step_id
                _write_json(output, redact_value(report))
                started = time.perf_counter()
                credential = headers[step.get('caller', 'owner')]
                if 'concurrent_arguments' in step:
                    observed = concurrent_calls(hub, credential, bodies)
                else:
                    observed = observe_response(client.post(hub + '/api/tools/call', headers=credential,
                                                            json=bodies[0], timeout=60))
                findings = _expectation_findings(observed, _resolve_value(step['expect'], context))
                report['steps'].append({'id': step_id, 'input': bodies if len(bodies) > 1 else bodies[0], 'output': observed,
                                       'duration_ms': round((time.perf_counter() - started) * 1000, 2),
                                       'status': 'failed' if findings else 'passed', 'findings': findings})
                report['active_step'] = None
                _write_json(output, redact_value(report))
                print(f'{step_id}: {"failed" if findings else "passed"}', flush=True)
                if findings:
                    return 1
                context['outputs'][step_id] = observed
            report['ok'] = True
        except Exception as exc:
            report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            return 1
        finally:
            _write_json(output, redact_value(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
