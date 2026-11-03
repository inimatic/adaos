"""Exact, content-bound recovery before the first accepted Prototype revision."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from adaos.services.builder_context import digest
from .generation_plan import GenerationInputRejected
from .task_spec import verify_identity

INITIAL_PREFIX = 'prototype-initial-base:'
SOURCE_FILES = ('webui.json','semantic.webui.json','prototype.brief.json')


def _sources(artifact_root):
    root = Path(artifact_root).resolve()
    values = {}
    for name in SOURCE_FILES:
        path = root / name
        if path.is_file():
            if path.resolve().parent != root:
                raise ValueError('Prototype recovery source escapes its artifact root')
            raw = path.read_bytes()
            values[name] = {'sha256':hashlib.sha256(raw).hexdigest(),'content':raw.decode('utf-8')}
    return values


def _identity(project_ref, sources):
    files = {}
    for name in SOURCE_FILES:
        item = sources.get(name)
        if item is None:
            files[name] = None
            continue
        actual = hashlib.sha256(item['content'].encode('utf-8')).hexdigest()
        if actual != item['sha256']:
            raise ValueError('Retained Prototype source bytes conflict with their digest')
        files[name] = actual
    return INITIAL_PREFIX + digest({'project_ref':project_ref,'files':files})


def replay_base_ref(*, project_ref, artifact_root, revision=None):
    return str(revision).strip() if revision else _identity(project_ref,_sources(artifact_root))


def prepare_initial_replay(*, project_ref, artifact_root, journal_dir, job_id,
                          request_text, expected_base_ref, evidence_state_dir=None, workflow=None):
    if not str(expected_base_ref).startswith(INITIAL_PREFIX):
        return None  # Existing revision replay retains its established path.
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',job_id):
        raise GenerationInputRejected('context.replay_job_invalid','Recovery needs an exact retained job identity')
    root, journal = Path(artifact_root).resolve(),Path(journal_dir).resolve()
    if journal.parent != root:
        from .generation_journals import GenerationJournals
        from .authoring_scope import verify_frozen_authoring_scope
        if workflow is None:
            raise GenerationInputRejected('context.replay_scope_conflict','Recovery needs its Core journal owner')
        owner = GenerationJournals(workflow).owner_for_directory(journal)
        scope = verify_frozen_authoring_scope(workflow, owner['source_scope'])
        if (scope['project_ref'] != project_ref
                or workflow.project_root(scope['object_type'], scope['object_id']).resolve() != root):
            raise GenerationInputRejected('context.replay_scope_conflict','Core journal belongs to another authoring source')
    current_sources = _sources(root)
    if _identity(project_ref,current_sources) != expected_base_ref:
        raise GenerationInputRejected('context.replay_base_changed','Initial Prototype source changed after replay selection')
    terminal = json.loads((journal/(job_id+'.json')).read_text(encoding='utf-8'))
    if (terminal.get('job_id') != job_id or terminal.get('status') not in {'failed','interrupted'}
            or terminal.get('scenario_id') != project_ref.split(':',1)[-1]):
        raise GenerationInputRejected('context.replay_job_conflict','Only the selected project failed job may be recovered')
    input_ref = terminal['input_artifact']
    if Path(input_ref['path']).name != job_id+'.request.json':
        raise GenerationInputRejected('context.replay_input_conflict','Recovery must use its exact original job request artifact')
    raw = (journal/Path(input_ref['path']).name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != input_ref['sha256']:
        raise GenerationInputRejected('context.replay_input_changed','Original request bytes no longer match their retained receipt')
    source = json.loads(raw)
    from .llm_input_attribution import read_llm_evidence
    retained = read_llm_evidence(input_ref['evidence_receipt'],state_dir=evidence_state_dir)
    clean = lambda value:{key:item for key,item in value.items() if key!='evidence_receipt' and item is not None}
    if (clean(source) != clean(retained) or source.get('job_id') != job_id
            or source.get('request_id') != terminal.get('request_id')
            or source.get('scenario_id') != terminal['scenario_id']):
        raise GenerationInputRejected('context.replay_input_conflict','Original request identity or acknowledged evidence differs')
    if _identity(project_ref,source['source_inputs']) != expected_base_ref:
        raise GenerationInputRejected('context.replay_original_base_changed','Current initial source differs from the failed attempt baseline')
    wires = [json.loads(path.read_text(encoding='utf-8')) for path in journal.glob('wire-*.json')]
    wires = [row for row in wires if row.get('job_id')==job_id]
    if len(wires)!=1:
        raise GenerationInputRejected('context.replay_plan_ambiguous','Recovery needs one exact original wire admission')
    ref = wires[0]['context_admission']['generation_plan_ref']
    if not re.fullmatch(r'generation-plan:[a-f0-9]{24}',ref):
        raise GenerationInputRejected('context.replay_plan_invalid','Original generation plan identity is invalid')
    record = json.loads((journal/(ref.replace(':','-')+'.json')).read_text(encoding='utf-8'))
    plan,task,brief = record['generation_plan'],record['task_spec'],record['brief']
    verify_identity(plan,id_key='generation_plan_id',prefix='generation-plan:')
    verify_identity(task,id_key='task_spec_id',prefix='task:')
    verify_identity(brief,id_key='brief_id',prefix='brief:')
    requests = []
    for message in source['messages']:
        try:body = json.loads(message.get('content',''))
        except (ValueError,TypeError):continue
        if isinstance(body,dict) and isinstance(body.get('builder_request'),dict):requests.append(body['builder_request'])
    if len(requests)!=1:
        raise GenerationInputRejected('context.replay_carrier_ambiguous','Original request must have one admitted task carrier')
    carrier = requests[0]
    model_brief = carrier['prototype_brief']
    if (plan['generation_plan_id']!=ref or plan['baseline_digest'] is not None
            or plan['task_spec_ref']!=task['task_spec_id'] or task['interpretation_status']!='admitted'
            or plan['brief_digest']!=brief['digest'] or task['brief_digest']!=brief['digest']
            or task['scope']['project_ref']!=project_ref
            or task['source']['statement'].strip()!=request_text.strip()
            or carrier.get('instruction','').strip()!=request_text.strip()
            or carrier.get('generation_plan_ref')!=ref
            or model_brief.get('brief_ref')!=brief['brief_id'] or model_brief.get('brief_digest')!=brief['digest']
            or plan.get('model_context_digest') and digest(model_brief)!=plan['model_context_digest']):
        raise GenerationInputRejected('context.replay_task_conflict','Recovery cannot change the admitted task, Brief, project or original empty semantic base')
    return {'schema':'adaos.builder.initial_replay_context.v1','status':'admitted',
        'project_ref':project_ref,'job_id':job_id,'base_ref':expected_base_ref,
        'generation_plan_ref':ref,'brief':copy.deepcopy(brief),
        'original_input_receipt_ref':input_ref['evidence_receipt']['ref'],'provider_calls':0}
