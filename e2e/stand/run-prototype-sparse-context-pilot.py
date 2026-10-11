"""Additional compact task calls, isolated from the original 32-attempt series."""
import argparse
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.e2e.composition_metrics import retrieval_behavior, usage_record
from adaos.e2e.dollar_budget import DollarBudget
from adaos.e2e.prototype_context_ablation import native_builder, read, source_fingerprints, verify_root_retry_budget_support
from adaos.e2e.prototype_mcp_eval import mcp_trace
from adaos.e2e.request_budget import RequestBudget
from adaos.sdk.builder import prototype
from adaos.sdk.llm import llm_client
from adaos.services.agent_context import get_ctx
from adaos.services.builder.prototype_mcp import generation_options, library_snapshot, redact_credentials

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--task', choices=('book-catalog-ru', 'contact-form-ru'), required=True)
parser.add_argument('--retry-delivery', action='store_true', help='Explicit new attempt for retained zero-output MCP discovery failures only')
parser.add_argument('--routing-fix', action='store_true', help='Prospective dependency intervention, separate new task-aware answers')
args = parser.parse_args()
load_dotenv()
logging.disable(logging.WARNING)
assert os.getenv('ENV_TYPE') == 'dev'
os.environ.update(ADAOS_ROOT_LLM_BASE_URL='https://ru.api.inimatic.com', ADAOS_ROOT_LLM_PREFER_GLOBAL='0',
    ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED='1', ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY='full')
init_ctx(Settings.from_sources())
worker = 'https://api.inimatic.com'
llm_client._root_llm_base_urls = lambda *_args, **_kwargs: [worker]
run = Path('e2e/artifacts/builder/prototype-sparse-context-pilot-20261011-01')
plan = read(run / 'preregistration.json')
sources = plan['source_fingerprints']
if args.routing_fix:
    amendment = read(run / 'routing-amendment-01.json')
    assert sources == amendment['before_sources'] and not args.retry_delivery and args.task == 'book-catalog-ru'
    sources = amendment['after_sources']
assert plan['planned_only'] and sources == source_fingerprints(Path.cwd())
budget = RequestBudget(run / 'request-budget.sqlite', maximum=8, limits={'primary': 8, 'repair': 0, 'judge': 0})
dollars = DollarBudget(run / 'dollar-budget.sqlite', limit_usd=plan['additional_usd_limit'])
builder = native_builder(Path(get_ctx().paths.dev_skills_dir()) / 'builder_skill/handlers/main.py')
baseline = read(Path('src/adaos/scenario_templates/scenario_default/webui.json'))
_write_json(run / 'delivery-preflight.json', verify_root_retry_budget_support(llm_client))
order = [('gpt-5', 'full-mcp'), ('gpt-6-astra', 'task-aware'), ('gpt-5', 'task-aware'), ('gpt-6-astra', 'full-mcp')]
if args.task == 'contact-form-ru':
    order = list(reversed(order))
if args.routing_fix:
    order = [item for item in order if item[1] == 'task-aware']
for model, arm in order:
    assert sources == source_fingerprints(Path.cwd()), 'Platform source drift'
    directory = run / args.task / (model + '-' + arm + '-v2')
    if args.routing_fix:
        directory = run / args.task / (model + '-' + arm + '-v4')
    if args.retry_delivery:
        if not (directory / 'result.json').exists() or read(directory / 'result.json').get('failure_stage') != 'mcp_discovery':
            continue
        rejected = read(directory / 'terminal.json')
        if rejected.get('output_text'):
            raise ValueError('A model output must never be retried as transport discovery')
        directory = run / args.task / (model + '-' + arm + '-v3')
    directory.mkdir(exist_ok=True)
    if (directory / 'result.json').exists():
        continue
    packet = read(run / args.task / ('planned-' + arm + ('-routing-fix' if args.routing_fix else '') + '.json'))
    options = copy.deepcopy(packet['options'])
    # Issuance authenticates the read-only protocol, but is NOT a paid LLM call.
    options.update(generation_options())
    options.update(model=model, max_tool_calls=plan['max_tool_calls'], provider_timeout_s=900,
        provider_max_attempts=1, profile_scope='development', timeout=60, stream=True,
        request_id='sparse-pilot:' + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:24])
    key = str(directory.resolve())
    if (directory / 'terminal.json').exists():
        terminal = read(directory / 'terminal.json')
    elif (directory / 'submission.json').exists():
        submission = read(directory / 'submission.json')
        terminal = submission if submission.get('status') == 'succeeded' else llm_client.wait_response_job(
            submission['job_id'], base_url=worker, timeout_s=1200, request_timeout=15)
        _write_json(directory / 'terminal.json', redact_credentials(terminal))
    else:
        if (directory / 'budget.json').exists():
            raise RuntimeError('Ambiguous submission; recover request id before any new call')
        dollars.reserve(key, reserve_usd=2.5 if model == 'gpt-6-astra' else .5)
        slot = budget.reserve('primary', key)
        _write_json(directory / 'budget.json', {'ordinal': slot})
        _write_json(directory / 'request.json', redact_credentials({'messages': packet['messages'], 'options': options}))
        submission = llm_client.submit_response_job(packet['messages'], **options)
        _write_json(directory / 'submission.json', redact_credentials(submission))
        budget.observe(slot, job_id=submission.get('job_id'), status=submission.get('status', 'submitted'))
        print(json.dumps({'stage': 'submitted', 'task': args.task, 'model': model, 'arm': arm, 'job': submission.get('job_id')}), flush=True)
        terminal = submission if submission.get('status') == 'succeeded' else llm_client.wait_response_job(
            submission['job_id'], base_url=worker, timeout_s=1200, request_timeout=15)
        _write_json(directory / 'terminal.json', redact_credentials(terminal))
    budget.observe(read(directory / 'budget.json')['ordinal'], job_id=terminal.get('job_id'), status=terminal.get('status', 'terminal'))
    telemetry = terminal.get('telemetry') or {}
    assert telemetry.get('provider_max_attempts') == 1 and telemetry.get('provider_attempts') == 1
    actual_model = (terminal.get('response') or {}).get('model')
    if terminal.get('status') == 'succeeded' and not str(actual_model).startswith(model):
        raise RuntimeError('Unexpected provider model substitution')
    usage = usage_record(terminal, model)
    if usage is None:
        error = terminal.get('error') or {}
        message = str((error.get('error') or {}).get('message') or error.get('message') or '')
        discovery = 'Error retrieving tool list from MCP server' in message and not terminal.get('output_text')
        _write_json(directory / 'result.json', {'task': args.task, 'model': model, 'arm': arm,
            'native_compile_pass': None, 'failure_stage': 'mcp_discovery' if discovery else 'unknown_provider_failure',
            'error_message': message[:1000], 'job_id': terminal.get('job_id'), 'usage': None,
            'cost_reservation_retained': True, 'scored_as_model_quality': False})
        _write_json(run / 'dollar-budget.json', dollars.snapshot())
        if not discovery:
            raise RuntimeError('Unknown paid usage: reservation retained, stop additional calls')
        print(json.dumps({'stage': 'mcp_discovery_failure', 'job_id': terminal.get('job_id'),
            'automatic_resubmission': False, 'cost_reservation_retained': True}), flush=True)
        continue
    dollars.observe(key, usd=usage['usd_standard_estimate_without_unreported_cache_writes'])
    _write_json(run / 'dollar-budget.json', dollars.snapshot())
    raw = terminal.get('output_text') or ''
    try:
        candidate = builder._parse_llm_webui_transform_output(output_text=raw,
            previous_preview=baseline.get('preview_state') or {}, before_webui=baseline,
            prototype_brief=read(run / args.task / 'brief.json'), project_ref='project:test_sparse_context',
            output_mode='semantic_v2') if terminal.get('status') == 'succeeded' else {'ok': False, 'error': 'provider_failure'}
    except Exception as error:
        candidate = {'ok': False, 'error': type(error).__name__, 'detail': str(error), 'findings': getattr(error, 'findings', [])}
    _write_json(directory / 'candidate.json', candidate)
    trace = mcp_trace(terminal)
    inline = packet['receipt'].get('task_selections') or []
    row = {'task': args.task, 'model': model, 'arm': arm, 'native_compile_pass': candidate.get('ok') is True,
        'usage': usage, 'mcp': trace, 'timing': telemetry.get('timing'), 'job_id': terminal.get('job_id'),
        'provider_model': (terminal.get('response') or {}).get('model'),
        'retrieval': retrieval_behavior(trace, inline[0]['inline_fragment_ids'] if inline else [f['id'] for f in library_snapshot()['fragments']]),
        'initial_context': {k: packet['receipt'].get(k) for k in ('input_chars', 'output_chars', 'estimated_tokens')}}
    _write_json(directory / 'result.json', row)
    print(json.dumps({k: row[k] for k in ('task', 'model', 'arm', 'native_compile_pass', 'job_id')}), flush=True)
    if dollars.snapshot()['remaining_estimated_usd'] < 0:
        raise RuntimeError('Cost estimate exceeded additional limit: stop, do not issue another call')
_write_json(run / 'request-budget.json', budget.snapshot())
