"""Durable, opt-in LLM proposals over an immutable Core semantic admission."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .semantic_admission import SemanticAdmissionService, PROPOSAL_SCHEMA, proposal_contract, compiler_digest
from .semantic_graph import _read, digest
from .run_observability import execution_metrics_from_llm
from .workflow import BuilderWorkflowError

RULES = """You propose a bounded edit to an existing AdaOS declarative prototype.
Use only the Core-admitted stable refs, operation kinds and writable properties.
Localized labels are independent en/ru slots: change only the requested locale.
field_refs is the order of existing table columns, not a resource schema change.
Read-only dependency descriptors explain current labels; they grant no write authority.
Do not change ids, records, relationships, commands, guards or other properties.
If the request is ambiguous, return clarification and no operations. If it requires
an unadmitted operation, return unsupported and no operations. Do not guess a target.
Return only the structured result. Core derives all preconditions and validates
the complete merged prototype; your output cannot grant or widen its own scope."""


def provider_contract(admission: Mapping[str, Any]) -> dict[str, Any]:
    contract = proposal_contract()
    contract['properties']['schema'] = {'type': 'string', 'enum': [PROPOSAL_SCHEMA]}
    contract['properties']['message'].pop('maxLength', None)
    contract['properties']['ops']['maxItems'] = admission['max_ops']
    variants = contract['properties']['ops']['items']['anyOf']
    for variant in variants:
        props = variant['properties']
        kind = props['kind'].pop('const')
        props['kind'].update(type='string', enum=[kind])
        refs = [mask['ref'] for mask in admission['write_masks'] if
                ('field_refs' in mask['properties'] if kind == 'view.fields.reorder'
                 else any(prop.startswith(('title.', 'label.')) for prop in mask['properties']))]
        props['ref'] = {'type': 'string', 'enum': refs}
        if kind == 'label.set':
            props['text'] = {'type': 'string'}
            props['locale']['enum'] = sorted({prop.partition('.')[2] for mask in admission['write_masks']
                for prop in mask['properties'] if prop.startswith(('title.', 'label.'))})
        else:
            props['field_refs'].pop('uniqueItems', None)
            props['field_refs']['items'] = {'type': 'string'}
    contract['properties']['ops']['items']['anyOf'] = [variant for variant in variants if variant['properties']['ref']['enum']]
    return contract


def document_shape(value):
    """Strict shape of the frozen source; Core still checks every protected value."""
    if isinstance(value, Mapping):
        return {'type': 'object', 'properties': {key: document_shape(item) for key, item in value.items()},
                'required': list(value), 'additionalProperties': False}
    if isinstance(value, list):
        variants = []
        for item in value:
            shape = document_shape(item)
            if shape not in variants:
                variants.append(shape)
        return {'type': 'array', 'items': variants[0] if len(variants) == 1 else {'anyOf': variants} if variants else {'type': 'string'},
                'minItems': len(value), 'maxItems': len(value)}
    if value is None: return {'type': 'null'}
    if isinstance(value, bool): return {'type': 'boolean'}
    if isinstance(value, int): return {'type': 'integer'}
    if isinstance(value, float): return {'type': 'number'}
    return {'type': 'string'}


def _response_metrics(job: Mapping[str, Any], effort: str) -> dict[str, Any]:
    candidates = [job]
    for _ in range(4):
        added = [value for candidate in candidates for key in ('result', 'response', 'data', 'job', '_protocol')
                 if isinstance(value := candidate.get(key), Mapping) and all(value is not old for old in candidates)]
        if not added:
            break
        candidates.extend(added)
    # Root wrappers may hold timing while the nested response owns usage/cost.
    # Merge disjoint measurements; a thin wrapper must not hide billed tokens.
    selected = {}
    telemetry = {}
    for row in reversed(candidates):
        for key in ('model', 'usage', 'cost'):
            if row.get(key):
                selected[key] = row[key]
        for key, value in (row.get('telemetry') or {}).items():
            if isinstance(value, Mapping) and isinstance(telemetry.get(key), Mapping):
                telemetry[key] = {**telemetry[key], **value}
            elif value is not None:
                telemetry[key] = value
    usage = dict(telemetry.get('usage') or selected.get('usage') or {})
    for name, nested, key in [('cached_input_tokens', 'input_tokens_details', 'cached_tokens'),
                              ('reasoning_tokens', 'output_tokens_details', 'reasoning_tokens')]:
        value = (usage.get(nested) or {}).get(key)
        if name not in usage and value is not None:
            usage[name] = value
    selected['usage'] = usage
    if telemetry.get('usage'):
        telemetry['usage'] = usage
    selected['telemetry'] = telemetry
    metrics = execution_metrics_from_llm(selected, reasoning_effort=effort)
    metrics.setdefault('llm_calls', 1)
    metrics.setdefault('repair_attempts', 0)
    job_id = job.get('job_id') or (job.get('job') or {}).get('id')
    if job_id:
        metrics.setdefault('source_refs', [f'llm_job:{job_id}'])
    return metrics


class SemanticProposer:
    def __init__(self, admission: SemanticAdmissionService, *, submit=None, wait=None):
        from adaos.sdk.llm import llm_client
        self.admission = admission
        self.submit = submit or llm_client.submit_response_job
        self.wait = wait or llm_client.wait_response_job

    def _observe_run(self, record, retained, activity):
        metrics = retained.get('execution_metrics') or {'source': 'builder.llm_result',
            'model': retained['request']['model'],
            'llm_calls': 1, 'repair_attempts': 0, 'source_refs': ['llm_job:' + retained['job_id']]}
        effort = (retained['request'].get('reasoning') or {}).get('effort')
        if effort:
            metrics.setdefault('reasoning_effort', effort)
        return self.admission.graph.workflow.record_semantic_model_observation(
            record['project_ref'].removeprefix('scenario:'), change_id=record['change_id'],
            run_id=record['admission_id'], activity='prototype_semantic_model_' + activity,
            execution_metrics=metrics, source_message_id=record['source_request']['source_message_id'])

    def propose(self, *, project_ref: str, admission_id: str, model: str, reasoning_effort: str = 'high',
                max_tokens: int = 32768, timeout_s: float = 300, output_mode: str = 'bounded_ops') -> dict[str, Any]:
        from .model_controls import reasoning_for_model
        if not model or isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1024 <= max_tokens <= 65536:
            raise BuilderWorkflowError('Explicit model and bounded output capacity are required')
        if output_mode not in ('bounded_ops', 'full_document'):
            raise BuilderWorkflowError('unsupported semantic proposal output mode')
        reasoning = reasoning_for_model(model, reasoning_effort)
        root, path = self.admission._location(project_ref, admission_id)
        record = _read(path)
        immutable = {key: value for key, value in record.items() if key not in
                     ('admission_digest', 'prepared_request', 'proposal', 'prepared_digest')}
        if digest(immutable) != record['admission_digest'] or compiler_digest() != record['compiler_digest']:
            raise BuilderWorkflowError('Proposal admission integrity/compiler dependency mismatch')
        gate = self.admission.graph.workflow.prototype_edit_context('scenario', project_ref.partition(':')[2])
        if record['change_id'] != gate['change']['change_id'] or not gate['capabilities']['can_edit_prototype']:
            raise BuilderWorkflowError('Proposal requires its active editable Change')
        packet = self.admission._packet(record)
        model_path = path.with_suffix('.model.json')
        user_input = {'request': record['source_request']['instruction'], 'context': packet['context'],
            'admitted_scope': {'write_masks': packet['write_masks'], 'max_ops': packet['max_ops'],
                               'operation_kinds': packet['operation_kinds']}}
        contract = provider_contract(packet)
        instructions = RULES
        if output_mode == 'full_document':
            source = _read(self.admission.graph._file(root, 'semantic.webui.json'))
            user_input['semantic_document'] = source
            contract = {'type': 'object', 'additionalProperties': False, 'required': ['status', 'message', 'semantic_document'],
                'properties': {'status': {'type': 'string', 'enum': ['proposed', 'clarification', 'unsupported']},
                    'message': {'type': 'string'}, 'semantic_document': {'anyOf': [document_shape(source), {'type': 'null'}]}}}
            instructions += '\nFor this full-document mode, return the complete semantic_document with only admitted values changed. Preserve all other values and array order exactly. Nonproposals use null.'
        messages = [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': json.dumps(
            user_input, ensure_ascii=False, separators=(',', ':'))}]
        text = {'format': {'type': 'json_schema', 'name': 'builder_semantic_op_proposal', 'strict': True,
                           'schema': contract}}
        envelope = {'messages': messages, 'model': model, 'reasoning': reasoning, 'max_tokens': max_tokens,
                    'text': text, 'tools': [], 'tool_choice': 'none', 'profile_scope': 'development'}
        if len(json.dumps(envelope, ensure_ascii=False).encode('utf-8')) > 256 * 1024:
            raise BuilderWorkflowError('The admitted proposal exceeds its bounded context capacity')
        request_digest = digest(envelope)
        with mutation_lock(model_path.parent / '.model.lock'):
            retained = _read(model_path) if model_path.is_file() else {}
            if retained:
                frozen = retained['request']
                frozen_mode = 'full_document' if 'semantic_document' in frozen['text']['format']['schema']['properties'] else 'bounded_ops'
                if (digest(frozen) != retained['request_digest'] or frozen_mode != output_mode
                        or any(frozen.get(key) != envelope.get(key) for key in
                               ('model', 'reasoning', 'max_tokens', 'tools', 'tool_choice', 'profile_scope'))):
                    raise BuilderWorkflowError('A retained proposal cannot change its model/profile/context on retry')
                # Software/prompt updates must not replace a previously sealed
                # Root request. Resume its exact envelope, not a new paid input.
                envelope = copy.deepcopy(frozen)
                request_digest = retained['request_digest']
            if 'result' in retained:
                return copy.deepcopy(retained['result'])
            # Recheck the immutable base before any paid submission, not after it.
            if not retained.get('job_id'):
                _root, gate, document, runtime, locales = self.admission._base(project_ref)
                if (gate['generation'] != record['expected_generation']
                        or self.admission.graph.workflow.current_prototype_revision('scenario', gate['object_id']) != record['source_revision']
                        or digest(document) != record['base_hash']
                        or digest(runtime) != record['runtime_hash']
                        or {locale: digest(value) for locale, value in locales.items()} != record['locale_hashes']):
                    raise BuilderWorkflowError('The proposal base changed before model submission')
            if not retained:
                retained = {'schema': 'adaos.builder.semantic_model_request.v1', 'admission_id': admission_id,
                    'request_id': f'builder-semantic-proposal:{admission_id}', 'request_digest': request_digest,
                    'request': envelope, 'status': 'prepared'}
                atomic_write_json(model_path, retained)
            if not retained.get('job_id'):
                submitted = self.submit(**envelope, request_id=retained['request_id'], stream=False,
                                        provider_max_attempts=1, timeout=30)
                job_id = submitted.get('job_id') or (submitted.get('job') or {}).get('id')
                if not job_id:
                    raise BuilderWorkflowError('Root returned no durable proposal job identity')
                retained.update(job_id=job_id, base_url=(submitted.get('_client') or {}).get('base_url'), status='submitted')
                atomic_write_json(model_path, retained)
            self._observe_run(record, retained, 'started')
            job = self.wait(retained['job_id'], base_url=retained.get('base_url'), timeout_s=timeout_s, poll_interval_s=0.5)
            metrics = _response_metrics(job, reasoning_effort)
            metrics.setdefault('model', model)
            retained.update(status='observed', job=job, execution_metrics=metrics)
            atomic_write_json(model_path, retained)
            output = job.get('output_text')
            if not output or str(job.get('status') or '') not in ('succeeded', 'completed'):
                self._observe_run(record, retained, 'failed')
                raise BuilderWorkflowError('Proposal job is incomplete, refused or failed; no source was changed')
            try:
                proposal = json.loads(output)
                prepared = (self.admission.prepare_document if output_mode == 'full_document' else self.admission.prepare)(
                    project_ref=project_ref, admission_id=admission_id,
                    **({'candidate': proposal} if output_mode == 'full_document' else {'proposal': proposal}))
            except Exception as exc:
                self._observe_run(record, retained, 'failed')
                raise BuilderWorkflowError('The model returned no valid semantic proposal') from exc
            self._observe_run(record, retained, 'ready' if prepared['status'] == 'planned' else prepared['status'])
            result = {**prepared, 'proposal': proposal, 'execution_metrics': metrics, 'root_job_id': retained['job_id'],
                'admission_id': admission_id, 'model_calls': metrics['llm_calls'], 'requested_model': model,
                'reasoning_effort': reasoning_effort, 'output_mode': output_mode,
                'input_bytes': len(json.dumps(envelope['messages'], ensure_ascii=False).encode('utf-8'))}
            atomic_write_json(model_path, {**retained, 'status': 'completed', 'result': result})
            return result
